"""Phase 7A, §9 — bounded no-eligible-candidate recovery.

The live-observed failure mode this exists for: a decision cycle finds zero
eligible opportunities, reports `no_eligible_candidate`, and — because the
next hourly cycle re-reads the exact same stale opportunity pool — repeats
forever with no path back to a filled slot. This module gives that cycle
exactly ONE bounded, safe thing to try before giving up: pick an
under-explored exploration cell and trigger fresh, narrowly-scoped
discovery aimed at it.

Hard bounds, by construction, not by convention:
  - `attempt_recovery_discovery` makes at most one discovery call per
    invocation — it never loops internally.
  - A cooldown (`min_hours_between_attempts`) stops it firing again on
    every subsequent hourly tick while supply is still catching up
    upstream (probe -> collection -> interpretation is not synchronous).
  - It NEVER re-runs eligibility with safety checks relaxed — the only
    thing "recovery" changes is which NEW opportunities might exist to
    assess next cycle, via the ordinary `exploration_cell=True` carve-out
    (itself narrow — see eligibility_service.py) applied only to
    opportunities this module itself originated.
  - A failed/empty discovery attempt is recorded and the caller is told to
    remain safely unfilled with an explicit reason — never silently retried
    within the same call.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from app.intelligence.exploration.cell import ExplorationCell
from app.intelligence.exploration.portfolio import (
    AllocationPolicy,
    choose_next_cell,
)
from app.intelligence.exploration.repository import (
    compute_trial_counts,
    list_recent_cells,
    record_cell_selection,
)
from app.intelligence.exploration.taxonomy import AUDIENCE_VALUE_SEEDS, DOMAIN_SEEDS, FORMAT_SEEDS

DEFAULT_MIN_HOURS_BETWEEN_ATTEMPTS: int = 6
DEFAULT_STALE_ASSESSMENT_STREAK: int = 3


class DiscoveryFn(Protocol):
    def __call__(
        self, conn: sqlite3.Connection, *, channel_id: int, cell: ExplorationCell
    ) -> DiscoveryOutcome: ...


@dataclass(frozen=True)
class DiscoveryOutcome:
    """What a discovery attempt produced. `probes_created`/`opportunities_created`
    are both reported because probe creation (real MI machinery) and
    opportunity materialization (collection + interpretation) are separate,
    not-necessarily-synchronous stages — see module docstring."""

    attempted: bool
    probes_created: int = 0
    opportunities_created: int = 0
    error: str | None = None


@dataclass(frozen=True)
class RecoveryResult:
    recovery_attempted: bool
    cell: ExplorationCell | None
    outcome: DiscoveryOutcome | None
    reason: str


def _default_candidate_pool() -> list[ExplorationCell]:
    """Every seed domain x every seed format x the first audience-value
    seed — a deliberately small, deterministic pool (not the full cross
    product, which would be domains x formats x values and dominated by
    combinatorics for no real benefit here). Real callers with richer
    signal should build their own candidate pool from actual observed
    opportunity availability; this is the safe, always-available fallback.

    Phase 7C, §1 — restricted to production-capable formats only
    (`format_contract.known_format_specs()`). This is the ONE candidate
    pool that actually feeds real, production-triggering discovery (via
    `attempt_recovery_discovery` -> `select_recovery_cell` ->
    `choose_next_cell`), so a format with no production contract
    (e.g. "myth_bust") must never be selectable here — selecting one
    would trigger discovery/production work for a format script
    generation cannot actually execute (see stage_executors.py's
    resolve_format_spec fallback and its exploration_format correction).
    Every seed format remains valid to select for PURE exploration/
    simulation purposes elsewhere (simulation.py's own candidate pool,
    used for §16-style reporting on the full taxonomy, is intentionally
    NOT filtered — format seeds are never deleted, only excluded from
    the one pool that actually materializes production work).
    """
    from app.intelligence.exploration.format_contract import known_format_specs

    value = AUDIENCE_VALUE_SEEDS[0]
    executable_formats = set(known_format_specs())
    return [
        ExplorationCell(domain=d, format=f, audience_value=value)
        for d in DOMAIN_SEEDS
        for f in FORMAT_SEEDS
        if f in executable_formats
    ]


def _hours_since(timestamp: str | None, *, now: datetime) -> float:
    if timestamp is None:
        return float("inf")
    try:
        ts = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
    except ValueError:
        return float("inf")
    return (now - ts).total_seconds() / 3600.0


def should_attempt_recovery(
    conn: sqlite3.Connection,
    channel_id: int,
    *,
    consecutive_no_eligible_streak: int,
    last_recovery_attempt_at: str | None,
    min_hours_between_attempts: int = DEFAULT_MIN_HOURS_BETWEEN_ATTEMPTS,
    stale_streak_floor: int = DEFAULT_STALE_ASSESSMENT_STREAK,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """Pure decision: is supply stale/exhausted enough, and are we past the
    cooldown, to justify one more discovery attempt?

    `consecutive_no_eligible_streak` and `last_recovery_attempt_at` are
    supplied by the caller (decision_cycle.py already tracks
    last_decision_outcome; the cooldown timestamp comes from this module's
    own ledger via `last_recovery_attempt`) rather than queried here, so
    this stays a pure, cheaply-testable function.
    """
    now = now or datetime.now(UTC)
    if consecutive_no_eligible_streak < stale_streak_floor:
        return False, (
            f"only {consecutive_no_eligible_streak} consecutive no_eligible_candidate "
            f"result(s); waiting for {stale_streak_floor} before treating supply as stale"
        )
    hours_since_last = _hours_since(last_recovery_attempt_at, now=now)
    if hours_since_last < min_hours_between_attempts:
        return False, (
            f"last recovery attempt was {hours_since_last:.1f}h ago; cooldown is "
            f"{min_hours_between_attempts}h"
        )
    return True, (
        f"{consecutive_no_eligible_streak} consecutive no_eligible_candidate results and "
        f"{hours_since_last:.1f}h since the last recovery attempt — supply looks stale"
    )


def last_recovery_attempt(conn: sqlite3.Connection, channel_id: int) -> str | None:
    row = conn.execute(
        """SELECT selected_at FROM exploration_cell_selections
            WHERE channel_id = ? AND source = 'recovery_discovery'
            ORDER BY selected_at DESC, id DESC LIMIT 1""",
        (channel_id,),
    ).fetchone()
    return row["selected_at"] if row is not None else None


def select_recovery_cell(
    conn: sqlite3.Connection,
    channel_id: int,
    *,
    candidates: list[ExplorationCell] | None = None,
    policy: AllocationPolicy | None = None,
) -> ExplorationCell:
    """Always picks in exploration mode — recovery exists specifically to
    surface under-tested supply, never to revisit a known-good cell."""
    candidates = candidates if candidates is not None else _default_candidate_pool()
    recent = list_recent_cells(
        conn, channel_id, limit=policy.diversity.distinct_domain_window if policy else 8
    )
    trial_counts = compute_trial_counts(conn, channel_id)
    decision = choose_next_cell(
        candidates,
        recent=recent,
        trial_counts=trial_counts,
        policy=policy,
        exploit_ready=False,
    )
    return decision.cell


def _default_discovery_fn(
    conn: sqlite3.Connection, *, channel_id: int, cell: ExplorationCell
) -> DiscoveryOutcome:
    """Real-but-bounded default: one additional cold-start-style seed-
    expansion call biased toward the chosen cell's domain as an EXTRA
    secondary niche (the channel's own configured primary_niche is never
    overridden). Produces fresh MARKET_REGION probes only — turning those
    into dispatched, interpreted Opportunities is the existing, separate MI
    pipeline's job (collector.py / interpreter.py), not synchronous with
    this call. Requires a real AI provider and an active channel profile;
    degrades to `attempted=False` with a clear error otherwise rather than
    silently doing nothing.
    """
    from app.core.config import get_config
    from app.intelligence.market.cold_start import ExplorationProfile, plan_cold_start
    from app.intelligence.repository import get_active_profile_version

    profile_row = get_active_profile_version(conn, channel_id)
    if profile_row is None or not (profile_row.primary_niche or "").strip():
        return DiscoveryOutcome(
            attempted=False, error="no active channel profile with a primary_niche"
        )

    cfg = get_config()
    if not cfg.anthropic_api_key:
        return DiscoveryOutcome(attempted=False, error="no anthropic_api_key configured")

    from app.ai.claude import ClaudeProvider

    provider = ClaudeProvider(api_key=cfg.anthropic_api_key)
    # Phase 7C.3, §2: the cell's domain is conveyed ONLY via target_domain
    # below, never folded into secondary_niches — v3/v4's prompt framing
    # tells the model to diversify AWAY from everything listed as a
    # secondary niche, which is the opposite of what a recovery call
    # wants for the domain it exists to replenish (see cold_start.py's
    # target_domain_section). The channel's own configured secondary
    # niches are passed through completely unmodified.
    profile = ExplorationProfile(
        primary_niche=profile_row.primary_niche,
        secondary_niches=list(profile_row.secondary_niches or []),
        excluded_topics=profile_row.excluded_topics or [],
        audience_description=profile_row.audience_description or "",
        duplicate_similarity_threshold=profile_row.duplicate_similarity_threshold,
    )
    try:
        result = plan_cold_start(
            conn,
            profile,
            provider=provider,
            channel_id=channel_id,
            max_probes=2,
            max_depth=1,
            search_budget=4,
            # Phase 7C.2, §5 — this call exists specifically to replenish
            # `cell`; domain alignment must dominate which of the LLM's
            # candidates win the limited dispatch budget.
            target_domain=cell.domain,
        )
    except Exception as exc:  # noqa: BLE001 — recovery must never crash the decision cycle
        return DiscoveryOutcome(attempted=True, error=str(exc))

    return DiscoveryOutcome(attempted=True, probes_created=result.candidate_count)


def attempt_recovery_discovery(
    conn: sqlite3.Connection,
    channel_id: int,
    *,
    workspace_id: str | None,
    consecutive_no_eligible_streak: int,
    candidates: list[ExplorationCell] | None = None,
    policy: AllocationPolicy | None = None,
    discover_fn: DiscoveryFn | None = None,
    now: datetime | None = None,
) -> RecoveryResult:
    """The single, bounded entry point decision_cycle.py calls on
    `no_eligible_candidate`. See module docstring for the exact bounds this
    enforces. `discover_fn` is injectable for tests; production callers
    should leave it as the real default.
    """
    now = now or datetime.now(UTC)
    last_attempt = last_recovery_attempt(conn, channel_id)
    should, reason = should_attempt_recovery(
        conn,
        channel_id,
        consecutive_no_eligible_streak=consecutive_no_eligible_streak,
        last_recovery_attempt_at=last_attempt,
        now=now,
    )
    if not should:
        return RecoveryResult(recovery_attempted=False, cell=None, outcome=None, reason=reason)

    cell = select_recovery_cell(conn, channel_id, candidates=candidates, policy=policy)
    fn = discover_fn or _default_discovery_fn
    outcome = fn(conn, channel_id=channel_id, cell=cell)

    record_cell_selection(
        conn,
        channel_id=channel_id,
        workspace_id=workspace_id,
        cell=cell,
        selected_at=now.strftime("%Y-%m-%dT%H:%M:%S"),
        source="recovery_discovery",
    )

    if not outcome.attempted:
        return RecoveryResult(
            recovery_attempted=True,
            cell=cell,
            outcome=outcome,
            reason=f"discovery could not run: {outcome.error}",
        )
    if outcome.error:
        return RecoveryResult(
            recovery_attempted=True,
            cell=cell,
            outcome=outcome,
            reason=f"discovery attempted but failed: {outcome.error}",
        )
    return RecoveryResult(
        recovery_attempted=True,
        cell=cell,
        outcome=outcome,
        reason=(
            f"triggered fresh discovery for {cell.label()}: "
            f"{outcome.probes_created} probe(s) created; new opportunities, if any, "
            f"will surface once collection/interpretation completes on their own cadence"
        ),
    )
