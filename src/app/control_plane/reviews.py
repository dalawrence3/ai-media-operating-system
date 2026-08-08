"""Centralized review queue view for the Control Plane."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ReviewQueueItem:
    item_type: str
    entity_id: str
    summary: str
    severity: str


def build_review_queue(conn: Any, workspace_id: str) -> list[ReviewQueueItem]:
    """Aggregate all open review items for a workspace."""
    from app.control_plane.health import list_degraded_entities
    from app.control_plane.monitoring import (
        find_dead_lettered_events,
        find_stuck_operations,
    )
    from app.control_plane.queues import list_failed_operations_by_workspace

    items: list[ReviewQueueItem] = []

    for ep in find_dead_lettered_events(conn):
        items.append(
            ReviewQueueItem(
                item_type="dead_lettered_event",
                entity_id=ep.event_id,
                summary=f"Event {ep.event_id} dead-lettered in handler {ep.handler_key}",
                severity="high",
            )
        )

    for op in find_stuck_operations(conn):
        if op.workspace_id != workspace_id:
            continue
        items.append(
            ReviewQueueItem(
                item_type="stuck_operation",
                entity_id=op.id,
                summary=f"Operation {op.operation_type} stuck in {op.status}",
                severity="medium",
            )
        )

    for op in list_failed_operations_by_workspace(conn, workspace_id):
        items.append(
            ReviewQueueItem(
                item_type="failed_operation",
                entity_id=op.id,
                summary=f"Operation {op.operation_type} failed: {op.error_message or 'unknown'}",
                severity="medium",
            )
        )

    for hr in list_degraded_entities(conn):
        items.append(
            ReviewQueueItem(
                item_type="health_issue",
                entity_id=hr.entity_id,
                summary=f"{hr.entity_type} {hr.entity_id} is {hr.status}",
                severity="high" if hr.status in ("unavailable", "failed") else "medium",
            )
        )

    return items
