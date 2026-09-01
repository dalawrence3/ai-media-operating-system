"""Phase 18D — the publication → experiment → observation → outcome bridge.

Phase 14A gave the experiment ledger a complete lifecycle
(draft → planned → in_production → published → observing → mature →
analyzed → completed) and Phase 18B/18C drove it as far as `in_production`.
Nothing advanced it any further: `attach_publication` and every transition
past `in_production` were reachable only from tests and the CLI. A video
could go public, collect real analytics, and feed learning while its
experiment sat in `in_production` forever.

This module owns the missing half. It is the single canonical owner of
every post-production experiment transition.

Design rules this module holds itself to:

Lineage, not arguments
    Callers pass a publication id. The experiment is derived through
    publication → publishing_plan → experiment_id (falling back to the
    deeper production_plan path). No caller may name an experiment
    directly, so a bug in one call site cannot attach analytics to an
    unrelated experiment.

Idempotent and forward-only
    Every entry point re-reads current state and walks the ledger forward
    to the requested stage, skipping stages already passed. Calling it
    twice, or calling it after a restart, changes nothing the second time.
    It never walks backwards and never touches a cancelled or completed
    experiment.

Separate concepts, separate states
    PUBLICATION is an event → `published`.
    OBSERVATION is a period → `observing`.
    OUTCOME MATURITY is evidence quality → `mature` (and `analyzed` once an
    outcome row has been persisted). Collapsing these would make restart
    ambiguous: "published" alone cannot tell you whether observation was
    ever registered.

Never fatal
    A publishing cycle that has already made a video public must not fail
    because a ledger row would not update. Every entry point returns a
    result object and logs; the reconciler is the safety net.

Honest maturity
    `mature` is only ever reached through the existing Phase 14G outcome
    evaluator's own thresholds. This module has no opinion about whether
    evidence is sufficient and never fabricates one.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, field

from app.core.logging import get_logger
from app.intelligence.experiments.models import ExperimentStatus

logger = get_logger(__name__)

# The ordered spine of the post-production lifecycle. Walking this list is
# how every entry point advances an experiment to a target stage without
# each call site having to know the intermediate steps.
_FORWARD_ORDER: list[ExperimentStatus] = [
    ExperimentStatus.draft,
    ExperimentStatus.planned,
    ExperimentStatus.in_production,
    ExperimentStatus.published,
    ExperimentStatus.observing,
    ExperimentStatus.mature,
    ExperimentStatus.analyzed,
    ExperimentStatus.completed,
]

_ORDER_INDEX: dict[ExperimentStatus, int] = {s: i for i, s in enumerate(_FORWARD_ORDER)}

# Statuses from which no automatic advancement is ever attempted.
_TERMINAL = frozenset({ExperimentStatus.cancelled, ExperimentStatus.completed})


@dataclass
class LifecycleAdvanceResult:
    """What one advance attempt actually did."""

    publication_id: int
    experiment_id: str | None = None
    from_status: str | None = None
    to_status: str | None = None
    transitions: list[str] = field(default_factory=list)
    publication_attached: bool = False
    changed: bool = False
    skipped_reason: str | None = None
    error: str | None = None


# ── Lineage derivation ───────────────────────────────────────────────────────


def derive_experiment_for_publication(conn: sqlite3.Connection, publication_id: int) -> str | None:
    """Resolve the experiment that this publication realises, or None.

    Canonical path is publication → publishing_plan.experiment_id. When the
    publishing plan carries no experiment (older plans predate the column
    being populated) the deeper publication → publishing_plan →
    production_plan.experiment_id path is used. Both are pure lineage
    lookups — there is no heuristic fallback, because guessing here would
    mean attributing one experiment's analytics to another.
    """
    row = conn.execute(
        """
        SELECT pp.experiment_id AS plan_experiment_id,
               prod.experiment_id AS production_experiment_id
        FROM publications pub
        JOIN publishing_plans pp ON pp.id = pub.publishing_plan_id
        LEFT JOIN production_plans prod ON prod.id = pp.production_plan_id
        WHERE pub.id = ?
        """,
        (publication_id,),
    ).fetchone()
    if row is None:
        return None
    return row["plan_experiment_id"] or row["production_experiment_id"]


def _publication_is_public(conn: sqlite3.Connection, publication_id: int) -> bool:
    """True only when the provider actually has this video public.

    Both columns matter: `visibility` is what the provider reports and
    `status` is the local terminal state the observer keys off. Advancing
    the ledger on either one alone would let a private upload look like a
    publication event.
    """
    row = conn.execute(
        "SELECT visibility, status FROM publications WHERE id = ? AND deleted_at IS NULL",
        (publication_id,),
    ).fetchone()
    if row is None:
        return False
    return row["visibility"] == "public" and row["status"] == "published"


def _has_active_observation(conn: sqlite3.Connection, publication_id: int) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM analytics_observation_state
        WHERE publication_id = ? AND observation_status = 'active'
        LIMIT 1
        """,
        (publication_id,),
    ).fetchone()
    return row is not None


# ── Audit ────────────────────────────────────────────────────────────────────


def _emit(
    conn: sqlite3.Connection,
    *,
    event_type: str,
    experiment_id: str,
    publication_id: int,
    actor: str,
    payload: dict | None = None,
) -> None:
    """Emit a cp_event for a lifecycle transition. Never raises."""
    try:
        from app.control_plane.models import ControlEventDraft
        from app.control_plane.repository import create_event

        pub = conn.execute(
            "SELECT workspace_id, channel_id FROM publications WHERE id = ?",
            (publication_id,),
        ).fetchone()
        create_event(
            conn,
            ControlEventDraft(
                id=str(uuid.uuid4()),
                event_type=event_type,
                workspace_id=(pub["workspace_id"] if pub else "") or "",
                actor=actor,
                channel_id=pub["channel_id"] if pub else None,
                source_entity_id=experiment_id,
                experiment_id=experiment_id,
                payload={"publication_id": publication_id, **(payload or {})},
            ),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 — audit must never break the loop
        logger.debug("experiment lifecycle: event %r emit failed (non-fatal): %s", event_type, exc)


# ── Core advance ─────────────────────────────────────────────────────────────


def advance_experiment_to(
    conn: sqlite3.Connection,
    experiment_id: str,
    target: ExperimentStatus,
    *,
    actor: str,
    reason: str,
) -> list[str]:
    """Walk an experiment forward to `target`, returning the transitions made.

    Returns [] when the experiment is already at or past the target, or is
    cancelled/completed. Intermediate stages are traversed in order because
    the ledger's own ALLOWED_TRANSITIONS only permits single steps — this is
    what lets a caller say "get this to observing" without knowing whether
    production ever recorded `planned`.
    """
    from app.intelligence.experiments.repository import (
        get_experiment,
        transition_experiment_state,
    )

    exp = get_experiment(conn, experiment_id)
    current = exp.status
    if current in _TERMINAL:
        return []

    current_idx = _ORDER_INDEX.get(current)
    target_idx = _ORDER_INDEX.get(target)
    if current_idx is None or target_idx is None or current_idx >= target_idx:
        return []

    made: list[str] = []
    for step in _FORWARD_ORDER[current_idx + 1 : target_idx + 1]:
        transition_experiment_state(conn, experiment_id, step, actor=actor, reason=reason)
        made.append(step.value)
    return made


# ── Entry point: a publication became public ─────────────────────────────────


def advance_experiment_for_publication(
    conn: sqlite3.Connection,
    publication_id: int,
    *,
    actor: str = "system:autonomy-publishing",
    reason: str = "publication confirmed public",
    target: ExperimentStatus | None = None,
) -> LifecycleAdvanceResult:
    """Advance the experiment behind a now-public publication.

    Called from the publishing cycle the moment a video is confirmed PUBLIC,
    and again by the reconciler for anything the live path missed. Advances
    to `published`, then on to `observing` when an active observation
    schedule already exists (registration usually happens moments later, in
    which case the reconciler or the next observation tick promotes it).

    `target` overrides the stage to reach; it exists for the reconciler and
    the outcome bridge, not for ordinary callers.
    """
    result = LifecycleAdvanceResult(publication_id=publication_id)

    try:
        experiment_id = derive_experiment_for_publication(conn, publication_id)
        if experiment_id is None:
            result.skipped_reason = (
                f"Publication {publication_id} has no experiment in its lineage."
            )
            return result
        result.experiment_id = experiment_id

        if not _publication_is_public(conn, publication_id):
            result.skipped_reason = (
                f"Publication {publication_id} is not public+published; "
                "the ledger must not record a publication event that has not happened."
            )
            return result

        from app.intelligence.experiments.repository import (
            attach_publication,
            get_experiment,
        )

        exp = get_experiment(conn, experiment_id)
        result.from_status = exp.status.value

        if exp.status in _TERMINAL:
            result.skipped_reason = (
                f"Experiment {experiment_id} is {exp.status.value}; not advancing."
            )
            return result

        # Attach first: `mature` evaluation and every downstream consumer
        # reads experiments.publication_id, and a transition without the link
        # would leave an experiment marked published with nothing to observe.
        if exp.publication_id != publication_id:
            attach_publication(conn, experiment_id, publication_id)
            result.publication_attached = True

        if target is None:
            target = (
                ExperimentStatus.observing
                if _has_active_observation(conn, publication_id)
                else ExperimentStatus.published
            )

        result.transitions = advance_experiment_to(
            conn, experiment_id, target, actor=actor, reason=reason
        )
        conn.commit()

        refreshed = get_experiment(conn, experiment_id)
        result.to_status = refreshed.status.value
        result.changed = bool(result.transitions) or result.publication_attached

        for step in result.transitions:
            _emit(
                conn,
                event_type=f"experiment.{step}",
                experiment_id=experiment_id,
                publication_id=publication_id,
                actor=actor,
                payload={"from_status": result.from_status, "reason": reason},
            )

        if result.changed:
            logger.info(
                "experiment lifecycle: %s %s → %s for publication %d",
                experiment_id,
                result.from_status,
                result.to_status,
                publication_id,
            )
    except Exception as exc:  # noqa: BLE001 — never fail a cycle over bookkeeping
        result.error = str(exc)
        logger.warning(
            "experiment lifecycle: could not advance experiment for publication %d "
            "(non-fatal, reconciliation will retry): %s",
            publication_id,
            exc,
        )
    return result


# ── Entry point: observation is under way ────────────────────────────────────


def mark_experiment_observing(
    conn: sqlite3.Connection,
    publication_id: int,
    *,
    actor: str = "system:auto_observer",
) -> LifecycleAdvanceResult:
    """Promote a published experiment to `observing`.

    Called by the analytics observer at the top of every tick. Observation
    being a period rather than an event, this is the state the experiment
    legitimately rests in for as long as evidence is still accumulating —
    including when that evidence stays honestly insufficient.
    """
    return advance_experiment_for_publication(
        conn,
        publication_id,
        actor=actor,
        reason="analytics observation active",
        target=ExperimentStatus.observing,
    )


# ── Reconciliation ───────────────────────────────────────────────────────────


def reconcile_experiment_lifecycle(
    conn: sqlite3.Connection,
    *,
    actor: str = "system:reconciliation",
) -> list[LifecycleAdvanceResult]:
    """Repair every experiment whose publication is public but whose ledger is behind.

    This is the restart-safety net for the whole bridge: any interruption
    between "provider confirmed public" and "ledger updated" — a crash, a
    failed commit, a publish that happened before this code existed — is
    healed by the next reconciliation pass rather than needing an operator.

    Only forward repairs are made, so running it repeatedly is a no-op once
    the ledger has caught up.
    """
    rows = conn.execute(
        """
        SELECT pub.id AS publication_id
        FROM publications pub
        JOIN publishing_plans pp ON pp.id = pub.publishing_plan_id
        LEFT JOIN production_plans prod ON prod.id = pp.production_plan_id
        WHERE pub.deleted_at IS NULL
          AND pub.visibility = 'public'
          AND pub.status = 'published'
          AND COALESCE(pp.experiment_id, prod.experiment_id) IS NOT NULL
        ORDER BY pub.id
        """
    ).fetchall()

    results: list[LifecycleAdvanceResult] = []
    for row in rows:
        res = advance_experiment_for_publication(
            conn,
            row["publication_id"],
            actor=actor,
            reason="reconciliation: publication is public",
        )
        if res.changed:
            results.append(res)
    return results


__all__ = [
    "LifecycleAdvanceResult",
    "advance_experiment_for_publication",
    "advance_experiment_to",
    "derive_experiment_for_publication",
    "mark_experiment_observing",
    "reconcile_experiment_lifecycle",
]
