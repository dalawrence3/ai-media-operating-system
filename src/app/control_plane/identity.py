"""Workspace and channel identity management."""

from __future__ import annotations

import uuid
from typing import Any

from app.control_plane import repository as repo
from app.control_plane.constants import (
    CHANNEL_STATUS_ACTIVE,
    WORKSPACE_STATUS_ACTIVE,
)
from app.control_plane.models import Channel, ChannelDraft, Workspace, WorkspaceDraft


def create_workspace(
    conn: Any,
    *,
    name: str,
    slug: str,
    actor: str,
    metadata: dict[str, Any] | None = None,
) -> Workspace:
    import json

    draft = WorkspaceDraft(
        id=str(uuid.uuid4()),
        name=name,
        slug=slug,
        actor=actor,
        status=WORKSPACE_STATUS_ACTIVE,
        metadata_json=json.dumps(metadata) if metadata else None,
    )
    return repo.create_workspace(conn, draft)


def create_channel(
    conn: Any,
    *,
    workspace_id: str,
    name: str,
    slug: str,
    actor: str,
    description: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Channel:
    import json

    draft = ChannelDraft(
        id=str(uuid.uuid4()),
        workspace_id=workspace_id,
        name=name,
        slug=slug,
        actor=actor,
        status=CHANNEL_STATUS_ACTIVE,
        description=description,
        metadata_json=json.dumps(metadata) if metadata else None,
    )
    return repo.create_channel(conn, draft)


def suspend_workspace(conn: Any, workspace_id: str, actor: str) -> Workspace:
    return repo.update_workspace_status(conn, workspace_id, "suspended", actor)


def archive_workspace(conn: Any, workspace_id: str, actor: str) -> Workspace:
    return repo.update_workspace_status(conn, workspace_id, "archived", actor)


def pause_channel(conn: Any, channel_id: str, actor: str) -> Channel:
    return repo.update_channel_status(conn, channel_id, "paused", actor)


def archive_channel(conn: Any, channel_id: str, actor: str) -> Channel:
    return repo.update_channel_status(conn, channel_id, "archived", actor)
