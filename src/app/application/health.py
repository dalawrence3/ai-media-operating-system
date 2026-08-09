"""Aggregate health projection for a workspace.

Does not create a second source of truth — reads from:
- cp_health_records (Phase 12 health records)
- cp_event_processing (dead-letter detection)
- cp_operation_executions (stuck operations)
- app_pipeline_executions (active pipelines)
- cp_budget_policies / cp_cost_records (budget status)
"""

from __future__ import annotations

from typing import Any

from app.application.contracts import HealthView
from app.control_plane.costs import check_budget
from app.control_plane.errors import BudgetExceededError
from app.control_plane.monitoring import find_dead_lettered_events, find_stuck_operations


def get_workspace_health(conn: Any, workspace_id: str) -> HealthView:
    """Compute and return aggregate workspace health."""
    dead_letters = find_dead_lettered_events(conn)
    # Filter to workspace scope.
    ws_dead = [
        e for e in dead_letters if _event_belongs_to_workspace(conn, e.event_id, workspace_id)
    ]

    stuck_ops = find_stuck_operations(conn)

    active_pipelines = conn.execute(
        "SELECT COUNT(*) FROM app_pipeline_executions "
        "WHERE workspace_id=? AND status IN ('running', 'waiting_for_review')",
        (workspace_id,),
    ).fetchone()[0]

    # Budget check — warn/block status without recording a spend.
    budget_status = "ok"
    try:
        result = check_budget(conn, workspace_id)
        if result.get("warnings"):
            budget_status = "warn"
    except BudgetExceededError:
        budget_status = "block"
    except Exception:
        budget_status = "unknown"

    # CP health records — scoped via workspace/channel/account entity IDs.
    entity_ids = _workspace_entity_ids(conn, workspace_id)
    degraded_entities: list[dict[str, Any]] = []
    if entity_ids:
        placeholders = ",".join("?" * len(entity_ids))
        health_rows = conn.execute(
            f"SELECT entity_type, entity_id, status FROM cp_health_records "
            f"WHERE entity_id IN ({placeholders}) ORDER BY recorded_at DESC LIMIT 50",
            entity_ids,
        ).fetchall()
        degraded_entities = [
            {"entity_type": r["entity_type"], "entity_id": r["entity_id"]}
            for r in health_rows
            if r["status"] in ("degraded", "unhealthy")
        ]

    ws_stuck = [op for op in stuck_ops if op.workspace_id == workspace_id]
    event_bus_ok = len(ws_dead) == 0
    stuck_count = len(ws_stuck)
    dead_count = len(ws_dead)

    overall_status: str
    if budget_status == "block" or degraded_entities or dead_count > 0:
        overall_status = "degraded"
    elif stuck_count > 0 or budget_status == "warn":
        overall_status = "warn"
    else:
        overall_status = "ok"

    return HealthView(
        workspace_id=workspace_id,
        overall_status=overall_status,
        event_bus_ok=event_bus_ok,
        dead_letter_count=dead_count,
        stuck_operation_count=stuck_count,
        active_pipeline_count=active_pipelines,
        budget_status=budget_status,
        details={
            "degraded_entities": degraded_entities,
            "dead_letter_event_ids": [e.event_id for e in ws_dead],
            "stuck_operation_ids": [op.id for op in ws_stuck],
        },
    )


def _workspace_entity_ids(conn: Any, workspace_id: str) -> list[str]:
    """Collect IDs for the workspace itself plus its channels and accounts."""
    ids: list[str] = [workspace_id]
    channels = conn.execute(
        "SELECT id FROM cp_channels WHERE workspace_id=?", (workspace_id,)
    ).fetchall()
    ids.extend(r["id"] for r in channels)
    # Platform accounts are linked via channels, not directly to workspace.
    if len(ids) > 1:
        channel_ids = ids[1:]  # skip workspace_id at index 0
        placeholders = ",".join("?" * len(channel_ids))
        accounts = conn.execute(
            f"SELECT id FROM cp_platform_accounts WHERE channel_id IN ({placeholders})",
            channel_ids,
        ).fetchall()
        ids.extend(r["id"] for r in accounts)
    return ids


def _event_belongs_to_workspace(conn: Any, event_id: str, workspace_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM cp_events WHERE id=? AND workspace_id=?",
        (event_id, workspace_id),
    ).fetchone()
    return row is not None
