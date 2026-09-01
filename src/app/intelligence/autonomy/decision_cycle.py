"""Phase 18A — the autonomous decision cycle.

Observe current state -> refresh/consume learning -> ensure Market
Intelligence freshness -> resolve semantic-fit eligibility as needed ->
load active Channel Strategy -> run strategy-aware planner -> select the
next experiment -> reserve a future publishing slot -> place the
selection into the internal production queue -> STOP before production.

Hard stop: this module never calls a script/narration/visual/render
generator, never touches YouTube upload, never creates a `publications`
row, never changes visibility. It also never creates an `experiments` row
— Phase 14E's own design keeps that step "downstream, human-gated"; this
cycle's unit of "selected work" is an experiment_strategy_briefs row,
referenced by a publishing_slots.brief_id.

Channel-scoped, idempotent, restart-safe: see module-level design notes
inline at each step.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

from app.intelligence.autonomy.models import AutonomyPolicy, DecisionCycleResult, DecisionOutcome
from app.intelligence.autonomy.repository import (
    compute_next_publishable_slot,
    fill_slot,
    get_autonomy_policy,
    list_active_slots,
    record_decision_outcome,
    reserve_slot,
)
from app.publishing.authorization import (
    DEFAULT_MAX_PUBLICATIONS_PER_24H,
    get_channel_publishing_authorization,
)

_OPPORTUNITY_CONSIDER_LIMIT = 50


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _now_iso() -> str:
    return _now_utc().strftime("%Y-%m-%dT%H:%M:%S")


def run_decision_cycle(
    conn: sqlite3.Connection,
    *,
    cp_channel_id: str,
    workspace_id: str,
    actor: str = "system:autonomy",
    ai_provider: Any = None,
    youtube_api_key: str = "",
    anthropic_api_key: str = "",
) -> DecisionCycleResult:
    """Run one channel-scoped autonomous decision cycle.

    `ai_provider` may be supplied directly (tests); otherwise, if
    `anthropic_api_key` is non-empty, a real ClaudeProvider is constructed
    for the semantic-fit resolution step only (the one step that can
    legitimately spend an LLM call). Every other step is deterministic or
    already-cached.
    """
    from app.intelligence.channel_bridge import get_intelligence_channel_id

    started_at = _now_iso()
    result = DecisionCycleResult(
        channel_id=cp_channel_id,
        workspace_id=workspace_id,
        started_at=started_at,
    )

    # ── 1. Observe: policy + intelligence-domain identity ──────────────────
    policy = get_autonomy_policy(conn, cp_channel_id)
    if policy is None or not policy.decision_automation_enabled:
        result.outcome = DecisionOutcome.DISABLED
        result.reason = "Decision automation is not enabled for this channel."
        result.completed_at = _now_iso()
        return result

    intel_channel_id = get_intelligence_channel_id(conn, cp_channel_id)
    if intel_channel_id is None:
        result.outcome = DecisionOutcome.FAILED
        result.reason = "Channel has no intelligence-domain identity bridge."
        result.errors.append(result.reason)
        result.completed_at = _now_iso()
        return result

    # ── 2. Cheap queue check — before any real work (section 17) ───────────
    active_slots = list_active_slots(conn, cp_channel_id)
    filled_slots = [s for s in active_slots if s.state == "filled"]
    in_flight_slots = [s for s in active_slots if s.state == "reserved" and s.brief_id is None]

    if len(filled_slots) >= policy.queue_target and not in_flight_slots:
        result.outcome = DecisionOutcome.QUEUE_ALREADY_SATISFIED
        result.reason = f"{len(filled_slots)}/{policy.queue_target} slots already filled."
        result.completed_at = _now_iso()
        record_decision_outcome(conn, cp_channel_id, result.outcome.value, at=result.completed_at)
        return result

    # ── 3. Idempotency / concurrency lock — hour-bucketed per channel ──────
    # Deliberately NOT keyed to a specific slot: the slot to work on isn't
    # known until step 4 (a crashed prior attempt may have left one
    # in-flight, which step 4 resumes rather than re-reserving). Bucketing
    # by hour bounds duplicate concurrent attempts to a single hour while
    # still allowing a fresh, independent attempt on the next tick if this
    # one fails or the process restarts — restart-safety without needing
    # lease/heartbeat expiry logic for what is, by design, an hourly check.
    from app.control_plane.jobs import complete_operation, fail_operation
    from app.control_plane.jobs import start_operation as _start_operation
    from app.control_plane.repository import get_operation_by_idempotency_key

    idempotency_key = f"autonomy_decision:{cp_channel_id}:{_now_utc():%Y%m%dT%H}"
    result.idempotency_key = idempotency_key

    existing_op = get_operation_by_idempotency_key(conn, idempotency_key)
    if existing_op is not None:
        # Another tick (concurrent, or an earlier tick this same hour) already
        # attempted a cycle for this channel. Nothing new to do — the slot/
        # queue state already reflects whatever that attempt accomplished.
        result.already_running = True
        result.operation_id = existing_op.id
        result.outcome = DecisionOutcome.QUEUE_ALREADY_SATISFIED
        result.reason = (
            f"A decision-cycle attempt for this channel already ran this hour "
            f"(operation {existing_op.id}, status={existing_op.status}); skipping."
        )
        result.completed_at = _now_iso()
        return result

    operation = _start_operation(
        conn,
        operation_type="autonomy_decision_cycle",
        workspace_id=workspace_id,
        actor=actor,
        channel_id=cp_channel_id,
        idempotency_key=idempotency_key,
        input_data={"queue_target": policy.queue_target, "cadence_type": policy.cadence_type.value},
    )
    result.operation_id = operation.id

    try:
        _run_locked_cycle(
            conn,
            cp_channel_id=cp_channel_id,
            workspace_id=workspace_id,
            intel_channel_id=intel_channel_id,
            policy=policy,
            in_flight_slots=in_flight_slots,
            ai_provider=ai_provider,
            youtube_api_key=youtube_api_key,
            anthropic_api_key=anthropic_api_key,
            result=result,
        )
        complete_operation(conn, operation.id, output_data={"outcome": result.outcome.value})
    except Exception as exc:  # noqa: BLE001 — must never propagate out of a scheduler tick
        result.outcome = DecisionOutcome.FAILED
        result.reason = f"Unhandled exception in decision cycle: {exc}"
        result.errors.append(str(exc))
        fail_operation(conn, operation.id, str(exc))

    result.completed_at = _now_iso()
    record_decision_outcome(conn, cp_channel_id, result.outcome.value, at=result.completed_at)
    return result


def _run_locked_cycle(
    conn: sqlite3.Connection,
    *,
    cp_channel_id: str,
    workspace_id: str,
    intel_channel_id: int,
    policy: AutonomyPolicy,
    in_flight_slots: list,
    ai_provider: Any,
    youtube_api_key: str,
    anthropic_api_key: str,
    result: DecisionCycleResult,
) -> None:
    """The actual decision work, run under the operation-execution lock
    acquired by the caller. Mutates `result` in place."""

    # ── 4. Resolve target slot: resume in-flight, or reserve a new one ─────
    #
    # The slot chosen must be one the channel could actually publish in.
    # Cadence and the publication rate ceiling remain independent concepts —
    # the cadence decides when slots exist, the ceiling decides which of them
    # are usable — but reserving a slot the ceiling will certainly refuse is
    # not free: production would generate a script, narration, visuals and a
    # render for it, and publishing would then decline to upload and retire
    # the slot as missed. That is real spend on a video that was never
    # publishable, so the ceiling is consulted here rather than discovered
    # hours later at publish time.
    if in_flight_slots:
        target_slot = in_flight_slots[0]
    else:
        auth_row = get_channel_publishing_authorization(conn, cp_channel_id)
        ceiling = (
            auth_row.max_publications_per_24h if auth_row else DEFAULT_MAX_PUBLICATIONS_PER_24H
        )
        selection = compute_next_publishable_slot(
            conn,
            policy,
            channel_id=cp_channel_id,
            after_utc=_now_utc(),
            max_publications_per_24h=ceiling,
        )
        result.rate_limited_slot_shift = selection.cadence_candidates_skipped
        if selection.cadence_candidates_skipped:
            result.errors.append(
                f"Skipped {selection.cadence_candidates_skipped} cadence slot(s) that fell "
                f"inside the {ceiling}/24h publication ceiling window "
                f"(clear at {selection.earliest_rate_permitted_utc:%Y-%m-%dT%H:%M}Z)."
            )
        target_slot = reserve_slot(
            conn,
            channel_id=cp_channel_id,
            workspace_id=workspace_id,
            slot_key=selection.slot_key,
            scheduled_for_local=selection.scheduled_for_local,
            timezone=policy.timezone,  # type: ignore[arg-type]
            scheduled_for_utc=selection.scheduled_for_utc,
        )
    result.slot_id = target_slot.id

    # ── 5. Cross-publication learning — cheap, idempotent, no external calls ──
    try:
        from app.learning.cross_publication import run_cross_publication_learning

        cp_result = run_cross_publication_learning(
            conn,
            channel_id=cp_channel_id,
            workspace_id=workspace_id,
        )
        result.cross_pub_learning_ran = True
        result.cross_pub_learning_publication_count = getattr(cp_result, "publication_count", 0)
    except Exception as exc:  # noqa: BLE001 — learning is never allowed to block the cycle
        result.errors.append(f"cross_publication_learning: {exc}")

    # ── 6. Market Intelligence freshness ────────────────────────────────────
    _ensure_market_freshness(
        conn,
        cp_channel_id=cp_channel_id,
        workspace_id=workspace_id,
        intel_channel_id=intel_channel_id,
        policy=policy,
        youtube_api_key=youtube_api_key,
        result=result,
    )

    # ── 7. Semantic-fit resolution — the one step that can spend LLM calls ──
    _resolve_semantic_fit(
        conn,
        intel_channel_id=intel_channel_id,
        policy=policy,
        ai_provider=ai_provider,
        anthropic_api_key=anthropic_api_key,
        result=result,
    )

    # ── 8. Strategy-aware planner ────────────────────────────────────────────
    from app.intelligence.experiments.eligibility import EligibilityPolicy
    from app.intelligence.experiments.eligibility_service import assess_experiment_eligibility
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    opp_rows = conn.execute(
        """SELECT id FROM opportunities
           WHERE channel_id = ?
             AND current_lifecycle_state NOT IN ('rejected', 'archived', 'produced')
           ORDER BY id DESC LIMIT ?""",
        (intel_channel_id, _OPPORTUNITY_CONSIDER_LIMIT),
    ).fetchall()
    eligibility_policy = EligibilityPolicy.v1()
    assessments = [
        assess_experiment_eligibility(
            conn, r["id"], intel_channel_id, ai_provider=None, policy=eligibility_policy
        )
        for r in opp_rows
    ]
    result.eligible_count = sum(
        1 for a in assessments if a.classification.value in ("general_eligible", "exploration_only")
    )

    plan = build_portfolio_plan(conn, intel_channel_id, assessments, dry_run=False)
    result.planning_run_id = plan.run_id

    selected = next((d for d in plan.decisions if d.selected), None)
    if selected is None:
        if result.eligible_count == 0:
            result.outcome = DecisionOutcome.NO_ELIGIBLE_CANDIDATE
            result.reason = "No opportunity currently resolves to an eligible classification."
        else:
            result.outcome = DecisionOutcome.NO_ELIGIBLE_CANDIDATE
            result.reason = "Planner found eligible candidates but selected none this cycle."
        if result.errors:
            result.outcome = DecisionOutcome.DEGRADED_BUT_PROCEEDED
        return

    result.opportunity_id = selected.opportunity_id

    decision_row = conn.execute(
        "SELECT id FROM experiment_selection_decisions "
        "WHERE planning_run_id = ? AND opportunity_id = ? "
        "AND selected = 1 ORDER BY id DESC LIMIT 1",
        (plan.run_id, selected.opportunity_id),
    ).fetchone()
    if decision_row is None:
        result.outcome = DecisionOutcome.FAILED
        result.reason = (
            "Planner selected a candidate but its selection_decision row could not be found."
        )
        result.errors.append(result.reason)
        return
    result.selection_decision_id = decision_row["id"]

    # ── 9. Strategy brief — idempotent, re-checks eligibility itself ───────
    from app.intelligence.experiments.brief_service import BriefCreationError, create_strategy_brief
    from app.intelligence.experiments.strategy_policy import load_policy_for_channel

    try:
        planning_policy = load_policy_for_channel(conn, intel_channel_id)
        brief = create_strategy_brief(conn, result.selection_decision_id, policy=planning_policy)
        conn.commit()
        result.brief_id = brief.id
    except BriefCreationError as exc:
        result.outcome = DecisionOutcome.NO_ELIGIBLE_CANDIDATE
        result.reason = f"Selected candidate failed brief-time eligibility recheck: {exc}"
        return

    # ── 10. Fill the reserved slot ──────────────────────────────────────────
    filled = fill_slot(
        conn,
        target_slot.id,
        brief_id=result.brief_id,
        selection_decision_id=result.selection_decision_id,
        opportunity_id=result.opportunity_id,
    )
    result.slot_id = filled.id
    result.outcome = (
        DecisionOutcome.DEGRADED_BUT_PROCEEDED if result.errors else DecisionOutcome.SELECTED
    )
    result.reason = (
        f"Selected opportunity {result.opportunity_id} and reserved slot {filled.id} "
        f"for {filled.scheduled_for_local}."
    )


def _ensure_market_freshness(
    conn: sqlite3.Connection,
    *,
    cp_channel_id: str,
    workspace_id: str,
    intel_channel_id: int,
    policy: AutonomyPolicy,
    youtube_api_key: str,
    result: DecisionCycleResult,
) -> None:
    """Reuse the Phase 17F recurring-refresh state when fresh; otherwise
    invoke run_market_refresh_cycle() directly. A refresh failure is
    recorded but never treated as fatal — the cycle proceeds with whatever
    intelligence already exists (per section 9)."""
    row = conn.execute(
        "SELECT last_run_at FROM app_schedule_definitions "
        "WHERE channel_id = ? AND operation_type = 'market_refresh' AND is_active = 1 "
        "ORDER BY created_at DESC LIMIT 1",
        (cp_channel_id,),
    ).fetchone()

    age_hours: float | None = None
    if row is not None and row["last_run_at"]:
        try:
            last_run = datetime.fromisoformat(row["last_run_at"].replace("Z", "+00:00"))
            if last_run.tzinfo is None:
                last_run = last_run.replace(tzinfo=UTC)
            age_hours = (_now_utc() - last_run).total_seconds() / 3600.0
        except ValueError:
            age_hours = None

    if age_hours is not None and age_hours < policy.market_refresh_max_age_hours:
        result.market_refresh_status = "reused"
        return

    try:
        from app.intelligence.market.refresh_service import run_market_refresh_cycle

        refresh_result = run_market_refresh_cycle(
            conn,
            channel_id=intel_channel_id,
            workspace_id=workspace_id,
            api_key=youtube_api_key,
        )
        result.market_refresh_status = "executed" if refresh_result.ok else "failed"
        if not refresh_result.ok:
            result.market_refresh_error = "; ".join(refresh_result.errors)
    except Exception as exc:  # noqa: BLE001 — a refresh failure must not kill the cycle
        result.market_refresh_status = "failed"
        result.market_refresh_error = str(exc)
        result.errors.append(f"market_refresh: {exc}")


def _resolve_semantic_fit(
    conn: sqlite3.Connection,
    *,
    intel_channel_id: int,
    policy: AutonomyPolicy,
    ai_provider: Any,
    anthropic_api_key: str,
    result: DecisionCycleResult,
) -> None:
    from app.intelligence.experiments.eligibility_service import (
        resolve_unresolved_opportunities_for_channel,
    )

    provider = ai_provider
    if provider is None and anthropic_api_key:
        from app.ai.claude import ClaudeProvider

        provider = ClaudeProvider(api_key=anthropic_api_key)

    try:
        resolution = resolve_unresolved_opportunities_for_channel(
            conn,
            channel_id=intel_channel_id,
            ai_provider=provider,
            max_evaluations=policy.semantic_fit_max_evaluations_per_run,
        )
        result.semantic_fit_considered = resolution.considered
        result.semantic_fit_evaluated = resolution.evaluated
        result.semantic_fit_cache_hits = resolution.cache_hits
        result.semantic_fit_eligible = resolution.eligible
    except Exception as exc:  # noqa: BLE001 — semantic-fit failure degrades, never blocks
        result.errors.append(f"semantic_fit_resolution: {exc}")
