"""Control Plane service API boundary — high-level read operations."""

from __future__ import annotations

from typing import Any

from app.control_plane import repository as repo
from app.control_plane.models import (
    Channel,
    ControlEvent,
    Experiment,
    PlatformAccount,
    StrategyProfile,
    Workspace,
)
from app.control_plane.monitoring import find_dead_lettered_events, find_stuck_operations
from app.control_plane.reviews import ReviewQueueItem, build_review_queue


def list_workspaces(conn: Any, status: str | None = None) -> list[Workspace]:
    return repo.list_workspaces(conn, status=status)


def get_workspace(conn: Any, workspace_id: str) -> Workspace:
    return repo.get_workspace(conn, workspace_id)


def list_channels(conn: Any, workspace_id: str) -> list[Channel]:
    return repo.list_channels_by_workspace(conn, workspace_id)


def list_platform_accounts(conn: Any, channel_id: str) -> list[PlatformAccount]:
    return repo.list_platform_accounts_by_channel(conn, channel_id)


def get_effective_policy(
    conn: Any,
    workspace_id: str,
    channel_id: str | None = None,
    platform_account_id: str | None = None,
) -> str:
    from app.control_plane.policies import resolve_effective_level

    return resolve_effective_level(conn, workspace_id, channel_id, platform_account_id)


def list_active_experiments(conn: Any, workspace_id: str) -> list[Experiment]:
    return repo.list_experiments_by_workspace(conn, workspace_id, status="active")


def get_channel_strategy(conn: Any, channel_id: str) -> StrategyProfile | None:
    return repo.get_active_strategy_for_channel(conn, channel_id)


def get_review_queue(conn: Any, workspace_id: str) -> list[ReviewQueueItem]:
    return build_review_queue(conn, workspace_id)


def get_event_history(
    conn: Any,
    workspace_id: str,
    event_type: str | None = None,
    limit: int = 50,
) -> list[ControlEvent]:
    return repo.list_events_by_workspace(conn, workspace_id, event_type=event_type, limit=limit)


def get_cost_summary(conn: Any, workspace_id: str) -> dict[str, Any]:
    records = repo.list_cost_records_by_workspace(conn, workspace_id, limit=500)
    total_usd = sum(r.usd_equivalent for r in records)
    by_provider: dict[str, float] = {}
    for r in records:
        by_provider[r.provider_key] = by_provider.get(r.provider_key, 0.0) + r.usd_equivalent

    return {
        "workspace_id": workspace_id,
        "total_usd": round(total_usd, 6),
        "by_provider": {k: round(v, 6) for k, v in by_provider.items()},
        "record_count": len(records),
    }


def workspace_control_center_status(conn: Any, workspace_id: str) -> dict[str, Any]:
    """Unified dashboard projection for a workspace."""
    channels = repo.list_channels_by_workspace(conn, workspace_id)

    in_progress_ops = conn.execute(
        "SELECT id, operation_type, status, channel_id, platform_account_id, created_at "
        "FROM cp_operation_executions "
        "WHERE workspace_id = ? AND status IN ('pending', 'running') "
        "ORDER BY created_at DESC LIMIT 20",
        (workspace_id,),
    ).fetchall()

    failed_ops = conn.execute(
        "SELECT id, operation_type, error_message, channel_id, platform_account_id, updated_at "
        "FROM cp_operation_executions "
        "WHERE workspace_id = ? AND status = 'failed' "
        "ORDER BY updated_at DESC LIMIT 20",
        (workspace_id,),
    ).fetchall()

    unhealthy = []
    for ch in channels:
        accounts = repo.list_platform_accounts_by_channel(conn, ch.id)
        for acc in accounts:
            if acc.status not in ("connected",):
                unhealthy.append({"account_id": acc.id, "status": acc.status, "channel_id": ch.id})

    paused_channels = [
        {"channel_id": ch.id, "name": ch.name} for ch in channels if ch.status == "paused"
    ]

    active_experiments = repo.list_experiments_by_workspace(conn, workspace_id, status="active")
    active_workflows = conn.execute(
        "SELECT id, name, trigger_event_type FROM cp_workflows "
        "WHERE workspace_id = ? AND status = 'active'",
        (workspace_id,),
    ).fetchall()

    review_items = build_review_queue(conn, workspace_id)
    dead_lettered = find_dead_lettered_events(conn)
    stuck = [op for op in find_stuck_operations(conn) if op.workspace_id == workspace_id]

    return {
        "workspace_id": workspace_id,
        "channel_count": len(channels),
        "in_progress_operations": [dict(r) for r in in_progress_ops],
        "failed_operations": [dict(r) for r in failed_ops],
        "paused_channels": paused_channels,
        "unhealthy_accounts": unhealthy,
        "review_queue_size": len(review_items),
        "dead_lettered_events": len(dead_lettered),
        "stuck_operations": len(stuck),
        "active_experiments": [{"id": e.id, "name": e.name} for e in active_experiments],
        "active_workflows": [{"id": r["id"], "name": r["name"]} for r in active_workflows],
    }


def workspace_audit_timeline(
    conn: Any, workspace_id: str, limit: int = 100
) -> list[dict[str, Any]]:
    """Chronological audit timeline: events + operations for a workspace."""
    events = repo.list_events_by_workspace(conn, workspace_id, limit=limit)
    ops = conn.execute(
        "SELECT id, operation_type, status, actor, created_at, engine "
        "FROM cp_operation_executions WHERE workspace_id = ? ORDER BY created_at DESC LIMIT ?",
        (workspace_id, limit),
    ).fetchall()

    timeline = []
    for ev in events:
        timeline.append(
            {
                "kind": "event",
                "id": ev.id,
                "event_type": ev.event_type,
                "actor": ev.actor,
                "source_engine": ev.source_engine,
                "channel_id": ev.channel_id,
                "platform_account_id": ev.platform_account_id,
                "experiment_id": ev.experiment_id,
                "correlation_id": ev.correlation_id,
                "causation_id": ev.causation_id,
                "ts": ev.created_at.isoformat(),
            }
        )
    for op in ops:
        timeline.append(
            {
                "kind": "operation",
                "id": op["id"],
                "operation_type": op["operation_type"],
                "status": op["status"],
                "actor": op["actor"],
                "source_engine": op["engine"],
                "ts": op["created_at"],
            }
        )

    timeline.sort(key=lambda x: x["ts"], reverse=True)
    return timeline[:limit]
