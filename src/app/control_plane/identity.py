"""Workspace and channel identity management."""

from __future__ import annotations

import uuid
from typing import Any

from app.application.errors import UnsupportedPlatformError
from app.control_plane import repository as repo
from app.control_plane.constants import (
    ACCOUNT_STATUS_DISCONNECTED,
    CHANNEL_STATUS_ACTIVE,
    WORKSPACE_STATUS_ACTIVE,
)
from app.control_plane.models import (
    Channel,
    ChannelDraft,
    PlatformAccount,
    PlatformAccountDraft,
    Workspace,
    WorkspaceDraft,
)

# Canonical platform registry — single source of truth for supported platform IDs.
# Adding a platform: extend both dicts; no other changes required.
_PLATFORM_DISPLAY: dict[str, str] = {
    "youtube": "YouTube",
    "instagram": "Instagram",
    "tiktok": "TikTok",
}

#: Public constant — importable by routes, tests, and frontend documentation generators.
SUPPORTED_PLATFORM_IDS: frozenset[str] = frozenset(_PLATFORM_DISPLAY)


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


def create_platform_account(
    conn: Any,
    *,
    channel_id: str,
    platform_id: str,
    external_account_id: str,
    display_name: str,
    actor: str,
) -> PlatformAccount:
    if platform_id not in SUPPORTED_PLATFORM_IDS:
        raise UnsupportedPlatformError(platform_id, SUPPORTED_PLATFORM_IDS)
    # Ensure the platform row exists (idempotent) so the FK constraint passes.
    repo.ensure_platform(
        conn,
        platform_id,
        platform_id,
        _PLATFORM_DISPLAY[platform_id],
    )
    draft = PlatformAccountDraft(
        id=str(uuid.uuid4()),
        channel_id=channel_id,
        platform_id=platform_id,
        platform_key=platform_id,
        external_account_id=external_account_id,
        display_name=display_name,
        actor=actor,
        status=ACCOUNT_STATUS_DISCONNECTED,
    )
    return repo.create_platform_account(conn, draft)


def suspend_workspace(conn: Any, workspace_id: str, actor: str) -> Workspace:
    return repo.update_workspace_status(conn, workspace_id, "suspended", actor)


def archive_workspace(conn: Any, workspace_id: str, actor: str) -> Workspace:
    return repo.update_workspace_status(conn, workspace_id, "archived", actor)


def pause_channel(conn: Any, channel_id: str, actor: str) -> Channel:
    return repo.update_channel_status(conn, channel_id, "paused", actor)


def archive_channel(conn: Any, channel_id: str, actor: str) -> Channel:
    return repo.update_channel_status(conn, channel_id, "archived", actor)
