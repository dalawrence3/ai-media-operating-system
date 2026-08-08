"""Unified exception queue — aggregates exceptions from all subsystems.

Covers: workflow failures, pipeline failures, credential issues, budget issues,
quota issues, provider outages, licensing blocks, stuck operations, dead-letter events.

No AI remediation in Phase 13.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.application.contracts import ExceptionView
from app.control_plane.monitoring import find_dead_lettered_events, find_stuck_operations


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _dt_iso(dt: Any) -> str:
    if dt is None:
        return _now_iso()
    if isinstance(dt, datetime):
        return dt.isoformat()
    return str(dt)


def get_exception_queue(conn: Any, workspace_id: str) -> list[ExceptionView]:
    """Aggregate all active exceptions for a workspace."""
    results: list[ExceptionView] = []

    # Dead-lettered events.
    for ep in find_dead_lettered_events(conn):
        ev_row = conn.execute(
            "SELECT workspace_id FROM cp_events WHERE id=?", (ep.event_id,)
        ).fetchone()
        if ev_row is None or ev_row["workspace_id"] != workspace_id:
            continue
        results.append(
            ExceptionView(
                exception_type="dead_lettered_event",
                entity_id=ep.event_id,
                workspace_id=workspace_id,
                description=(
                    f"Event processing dead-lettered in handler '{ep.handler_key}' "
                    f"after {ep.attempt_count} attempt(s)"
                ),
                severity="high",
                occurred_at=_dt_iso(ep.created_at),
                metadata={
                    "handler_key": ep.handler_key,
                    "attempt_count": ep.attempt_count,
                    "error_message": ep.error_message,
                },
            )
        )

    # Stuck operations.
    for op in find_stuck_operations(conn):
        if op.workspace_id != workspace_id:
            continue
        results.append(
            ExceptionView(
                exception_type="stuck_operation",
                entity_id=op.id,
                workspace_id=workspace_id,
                description=(
                    f"Operation '{op.operation_type}' has been in status '{op.status}' "
                    f"beyond the expected duration"
                ),
                severity="medium",
                occurred_at=_dt_iso(op.created_at),
                metadata={
                    "operation_type": op.operation_type,
                    "status": op.status,
                    "channel_id": op.channel_id,
                },
            )
        )

    # Failed operations.
    failed_ops = conn.execute(
        "SELECT * FROM cp_operation_executions "
        "WHERE workspace_id=? AND status='failed' "
        "ORDER BY updated_at DESC LIMIT 50",
        (workspace_id,),
    ).fetchall()
    for op in failed_ops:
        results.append(
            ExceptionView(
                exception_type="failed_operation",
                entity_id=op["id"],
                workspace_id=workspace_id,
                description=(
                    f"Operation '{op['operation_type']}' failed: "
                    f"{op['error_message'] or 'unknown error'}"
                ),
                severity="medium",
                occurred_at=op["updated_at"] or _now_iso(),
                metadata={
                    "operation_type": op["operation_type"],
                    "error_message": op["error_message"],
                    "channel_id": op["channel_id"],
                },
            )
        )

    # Failed pipeline stages.
    pipeline_rows = conn.execute(
        "SELECT ape.id AS pipeline_id, ape.channel_id, ape.created_at, "
        "asl.stage, asl.error_message, asl.completed_at "
        "FROM app_pipeline_executions ape "
        "JOIN app_pipeline_stage_log asl ON asl.pipeline_id = ape.id "
        "WHERE ape.workspace_id=? AND asl.status='failed' "
        "ORDER BY asl.completed_at DESC LIMIT 50",
        (workspace_id,),
    ).fetchall()
    for row in pipeline_rows:
        results.append(
            ExceptionView(
                exception_type="pipeline_stage_failure",
                entity_id=row["pipeline_id"],
                workspace_id=workspace_id,
                description=(
                    f"Pipeline stage '{row['stage']}' failed: "
                    f"{row['error_message'] or 'unknown error'}"
                ),
                severity="medium",
                occurred_at=row["completed_at"] or row["created_at"] or _now_iso(),
                metadata={
                    "stage": row["stage"],
                    "channel_id": row["channel_id"],
                    "error": row["error_message"],
                },
            )
        )

    # Degraded health records.
    health_rows = conn.execute(
        "SELECT * FROM cp_health_records "
        "WHERE status IN ('degraded', 'unavailable', 'credential_expired', "
        "'quota_limited', 'failed') "
        "ORDER BY recorded_at DESC LIMIT 100",
    ).fetchall()
    # Scope health records to workspace via channel/account lookup.
    ws_entity_ids = _workspace_entity_ids(conn, workspace_id)
    for hr in health_rows:
        if hr["entity_id"] not in ws_entity_ids and hr["entity_id"] != workspace_id:
            continue
        results.append(
            ExceptionView(
                exception_type="health_degraded",
                entity_id=hr["entity_id"],
                workspace_id=workspace_id,
                description=(f"{hr['entity_type']} '{hr['entity_id']}' health: {hr['status']}"),
                severity="high" if hr["status"] in ("unavailable", "failed") else "medium",
                occurred_at=hr["recorded_at"] or _now_iso(),
                metadata={
                    "entity_type": hr["entity_type"],
                    "status": hr["status"],
                    "detail": hr["detail"],
                },
            )
        )

    return results


def _workspace_entity_ids(conn: Any, workspace_id: str) -> set[str]:
    """Return the set of entity IDs (channels + accounts) belonging to a workspace."""
    ids: set[str] = {workspace_id}
    channels = conn.execute(
        "SELECT id FROM cp_channels WHERE workspace_id=?", (workspace_id,)
    ).fetchall()
    for ch in channels:
        ids.add(ch["id"])
        accounts = conn.execute(
            "SELECT id FROM cp_platform_accounts WHERE channel_id=?", (ch["id"],)
        ).fetchall()
        for acc in accounts:
            ids.add(acc["id"])
    return ids
