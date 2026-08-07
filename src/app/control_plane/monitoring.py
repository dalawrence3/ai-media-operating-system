"""Monitoring helpers: stuck jobs, health aggregation, dead-lettered events."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.control_plane import repository as repo
from app.control_plane.models import EventProcessing, OperationExecution


def find_stuck_operations(
    conn: Any, older_than_minutes: int = 30
) -> list[OperationExecution]:
    cutoff = (datetime.now(UTC) - timedelta(minutes=older_than_minutes)).isoformat()
    rows = conn.execute(
        "SELECT * FROM cp_operation_executions "
        "WHERE status IN ('pending','running') AND created_at < ? "
        "ORDER BY created_at ASC",
        (cutoff,),
    ).fetchall()
    from app.control_plane.repository import _row_to_operation_execution
    return [_row_to_operation_execution(r) for r in rows]


def find_dead_lettered_events(conn: Any, limit: int = 50) -> list[EventProcessing]:
    rows = conn.execute(
        "SELECT * FROM cp_event_processing WHERE status = 'dead_lettered' "
        "ORDER BY completed_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    from app.control_plane.repository import _row_to_event_processing
    return [_row_to_event_processing(r) for r in rows]


def workspace_health_summary(conn: Any, workspace_id: str) -> dict[str, Any]:
    channels = repo.list_channels_by_workspace(conn, workspace_id)
    account_statuses: dict[str, int] = {}
    for ch in channels:
        accounts = repo.list_platform_accounts_by_channel(conn, ch.id)
        for acc in accounts:
            account_statuses[acc.status] = account_statuses.get(acc.status, 0) + 1

    return {
        "workspace_id": workspace_id,
        "channel_count": len(channels),
        "account_status_counts": account_statuses,
    }
