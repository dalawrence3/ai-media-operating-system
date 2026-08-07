"""Domain event creation and routing helpers."""

from __future__ import annotations

import uuid
from typing import Any

from app.control_plane import repository as repo
from app.control_plane.constants import ALL_EVENT_TYPES
from app.control_plane.errors import InvalidEventTypeError
from app.control_plane.models import ControlEvent, ControlEventDraft


def emit_event(
    conn: Any,
    *,
    event_type: str,
    workspace_id: str,
    actor: str,
    payload: dict[str, Any] | None = None,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    channel_id: str | None = None,
    platform_account_id: str | None = None,
    source_engine: str | None = None,
    source_entity_id: str | None = None,
    experiment_id: str | None = None,
) -> ControlEvent:
    if event_type not in ALL_EVENT_TYPES:
        raise InvalidEventTypeError(event_type)
    draft = ControlEventDraft(
        id=str(uuid.uuid4()),
        event_type=event_type,
        workspace_id=workspace_id,
        actor=actor,
        payload=payload or {},
        correlation_id=correlation_id,
        causation_id=causation_id,
        channel_id=channel_id,
        platform_account_id=platform_account_id,
        source_engine=source_engine,
        source_entity_id=source_entity_id,
        experiment_id=experiment_id,
    )
    return repo.create_event(conn, draft)
