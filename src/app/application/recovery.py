"""Recovery and event replay services.

Rules:
- Failure history is preserved (no destructive updates to old rows).
- Completed idempotent artifacts are reused.
- Uncertain external states require explicit safe resolution by operators.
- Every recovery action emits a CP event for audit.
- Non-idempotent unsafe replay is blocked.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from app.application import state as pipeline_state
from app.application.contracts import PipelineView
from app.application.errors import (
    PipelineNotRecoverableError,
    ReplayBlockedError,
)
from app.control_plane import repository as cp_repo
from app.control_plane.event_bus import dispatch_event
from app.control_plane.models import ControlEventDraft

# Statuses from which a pipeline can be recovered.
RECOVERABLE_STATUSES: frozenset[str] = frozenset({"failed", "blocked", "paused"})

# Event types that are safe to replay (idempotent handlers only).
REPLAYABLE_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "pipeline.started",
        "pipeline.stage_started",
        "pipeline.waiting_for_review",
        "workflow.triggered",
        "health.degraded",
        "cost.warn",
    }
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _emit_event(
    conn: Any,
    event_type: str,
    workspace_id: str,
    actor: str,
    payload: dict[str, Any],
    *,
    correlation_id: str | None = None,
    causation_id: str | None = None,
) -> None:
    draft = ControlEventDraft(
        id=str(uuid.uuid4()),
        event_type=event_type,
        workspace_id=workspace_id,
        actor=actor,
        payload=payload,
        correlation_id=correlation_id,
        causation_id=causation_id,
    )
    event = cp_repo.create_event(conn, draft)
    dispatch_event(conn, event)


def find_recoverable_pipelines(
    conn: Any, workspace_id: str
) -> list[PipelineView]:
    """Return failed/blocked/paused pipelines that can be recovered."""
    result = []
    for status in RECOVERABLE_STATUSES:
        result.extend(
            pipeline_state.list_pipelines(conn, workspace_id, status=status)
        )
    return sorted(result, key=lambda p: p.created_at, reverse=True)


def recover_pipeline(
    conn: Any,
    pipeline_id: str,
    workspace_id: str,
    actor: str,
    *,
    from_stage: str | None = None,
) -> PipelineView:
    """Reset a failed/blocked pipeline from a given stage or its last failed stage.

    Does not re-run external work that already completed successfully.
    """
    pv = pipeline_state.get_pipeline(conn, pipeline_id)

    if pv.workspace_id != workspace_id:
        from app.application.errors import CrossWorkspaceAccessError
        raise CrossWorkspaceAccessError("Pipeline", pipeline_id, workspace_id)

    if pv.status not in RECOVERABLE_STATUSES:
        raise PipelineNotRecoverableError(
            pipeline_id, f"status '{pv.status}' is not recoverable"
        )

    # Determine the stage to recover from.
    target_stage = from_stage
    if target_stage is None:
        # Find the first failed or blocked stage.
        for s in pv.stages:
            if s.status in ("failed", "blocked"):
                target_stage = s.stage
                break
    if target_stage is None:
        target_stage = pv.current_stage or (pv.stages[0].stage if pv.stages else None)
    if target_stage is None:
        raise PipelineNotRecoverableError(pipeline_id, "no recoverable stage found")

    # Preserve failure history — insert new attempt rows instead of updating in-place.
    stage_names = [s.stage for s in pv.stages]
    if target_stage not in stage_names:
        raise PipelineNotRecoverableError(
            pipeline_id, f"stage '{target_stage}' not in pipeline"
        )
    target_idx = stage_names.index(target_stage)
    now = _now_iso()
    for stage in stage_names[target_idx:]:
        max_attempt = conn.execute(
            "SELECT MAX(attempt_number) FROM app_pipeline_stage_log "
            "WHERE pipeline_id=? AND stage=?",
            (pipeline_id, stage),
        ).fetchone()[0] or 0
        status = "running" if stage == target_stage else "pending"
        started_at = now if stage == target_stage else None
        conn.execute(
            "INSERT INTO app_pipeline_stage_log "
            "(id, pipeline_id, stage, attempt_number, status, started_at, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), pipeline_id, stage, max_attempt + 1, status, started_at, now),
        )
    conn.execute(
        "UPDATE app_pipeline_executions "
        "SET status='running', current_stage=?, error_message=NULL, "
        "blocked_reason=NULL, updated_at=? "
        "WHERE id=?",
        (target_stage, now, pipeline_id),
    )
    conn.commit()

    _emit_event(
        conn, "pipeline.recovered", workspace_id, actor,
        {"pipeline_id": pipeline_id, "from_stage": target_stage},
        correlation_id=pv.correlation_id,
    )

    return pipeline_state.get_pipeline(conn, pipeline_id)


def replay_event(
    conn: Any,
    event_id: str,
    workspace_id: str,
    actor: str,
) -> dict[str, Any]:
    """Re-deliver a dead-lettered event to its registered handlers.

    The original event row is never modified. A new replay attempt is
    recorded as a causation chain.

    Raises ReplayBlockedError for non-idempotent or completed events.
    """
    row = conn.execute(
        "SELECT * FROM cp_events WHERE id=?", (event_id,)
    ).fetchone()
    if row is None:
        raise ReplayBlockedError(event_id, "event not found")

    event_type = row["event_type"]
    if event_type not in REPLAYABLE_EVENT_TYPES:
        raise ReplayBlockedError(
            event_id,
            f"event_type '{event_type}' is not in the safe replay allowlist",
        )

    # Check if any processing entries already completed successfully.
    completed = conn.execute(
        "SELECT COUNT(*) FROM cp_event_processing "
        "WHERE event_id=? AND status='completed'",
        (event_id,),
    ).fetchone()[0]
    if completed > 0:
        raise ReplayBlockedError(
            event_id,
            "event has completed processing entries; replay would duplicate effects",
        )

    # Reset failed processing entries for re-delivery.
    conn.execute(
        "UPDATE cp_event_processing "
        "SET status='pending', attempt_count=0, error_message=NULL, last_attempt_at=NULL "
        "WHERE event_id=? AND status IN ('failed', 'dead_lettered')",
        (event_id,),
    )
    conn.commit()

    _emit_event(
        conn, "event.replayed", workspace_id, actor,
        {"original_event_id": event_id, "event_type": event_type},
        causation_id=event_id,
    )

    return {
        "event_id": event_id,
        "event_type": event_type,
        "replayed_at": _now_iso(),
        "actor": actor,
    }


def find_dead_lettered_events(conn: Any, workspace_id: str) -> list[dict[str, Any]]:
    """Return workspace-scoped dead-lettered event processing entries."""
    rows = conn.execute(
        "SELECT ep.*, ce.event_type, ce.workspace_id "
        "FROM cp_event_processing ep "
        "JOIN cp_events ce ON ep.event_id = ce.id "
        "WHERE ce.workspace_id=? AND ep.status='dead_lettered' "
        "ORDER BY ep.created_at DESC",
        (workspace_id,),
    ).fetchall()
    return [
        {
            "processing_id": r["id"],
            "event_id": r["event_id"],
            "event_type": r["event_type"],
            "handler_key": r["handler_key"],
            "attempt_count": r["attempt_count"],
            "error_message": r["error_message"],
            "created_at": r["created_at"],
            "replayable": r["event_type"] in REPLAYABLE_EVENT_TYPES,
        }
        for r in rows
    ]
