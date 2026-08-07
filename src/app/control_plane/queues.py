"""Review and exception queue queries."""

from __future__ import annotations

from typing import Any

from app.control_plane.models import OperationExecution


def pending_review_events(conn: Any, workspace_id: str) -> dict[str, Any]:
    """Return items requiring review: dead-lettered events, failed ops, degraded health."""
    from app.control_plane.health import list_degraded_entities
    from app.control_plane.monitoring import find_dead_lettered_events, find_stuck_operations

    dead = find_dead_lettered_events(conn)
    stuck = find_stuck_operations(conn)
    degraded = list_degraded_entities(conn)

    return {
        "workspace_id": workspace_id,
        "dead_lettered_event_count": len(dead),
        "stuck_operation_count": len(stuck),
        "degraded_entity_count": len(degraded),
    }


def list_failed_operations_by_workspace(
    conn: Any, workspace_id: str, limit: int = 50
) -> list[OperationExecution]:
    rows = conn.execute(
        "SELECT * FROM cp_operation_executions "
        "WHERE workspace_id = ? AND status = 'failed' "
        "ORDER BY updated_at DESC LIMIT ?",
        (workspace_id, limit),
    ).fetchall()
    from app.control_plane.repository import _row_to_operation_execution
    return [_row_to_operation_execution(r) for r in rows]
