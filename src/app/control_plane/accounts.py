"""Platform account lifecycle management."""

from __future__ import annotations

import uuid
from typing import Any

from app.control_plane import repository as repo
from app.control_plane.models import PlatformAccount, PlatformAccountDraft


def connect_account(
    conn: Any,
    *,
    channel_id: str,
    platform_key: str,
    external_account_id: str,
    display_name: str,
    actor: str,
    credential_profile_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> PlatformAccount:
    import json

    platform = repo.get_platform_by_key(conn, platform_key)
    draft = PlatformAccountDraft(
        id=str(uuid.uuid4()),
        channel_id=channel_id,
        platform_id=platform.id,
        platform_key=platform_key,
        external_account_id=external_account_id,
        display_name=display_name,
        actor=actor,
        status="connected",
        credential_profile_id=credential_profile_id,
        metadata_json=json.dumps(metadata) if metadata else None,
    )
    return repo.create_platform_account(conn, draft)


def disconnect_account(conn: Any, account_id: str, actor: str) -> PlatformAccount:
    return repo.update_platform_account_status(conn, account_id, "disconnected", actor)


def pause_account(conn: Any, account_id: str, actor: str) -> PlatformAccount:
    return repo.update_platform_account_status(conn, account_id, "paused", actor)


def resume_account(conn: Any, account_id: str, actor: str) -> PlatformAccount:
    return repo.update_platform_account_status(conn, account_id, "connected", actor)


def mark_credential_invalid(conn: Any, account_id: str, actor: str) -> PlatformAccount:
    return repo.update_platform_account_status(
        conn, account_id, "credential_invalid", actor
    )


def mark_credential_expiring(conn: Any, account_id: str, actor: str) -> PlatformAccount:
    return repo.update_platform_account_status(
        conn, account_id, "credential_expiring", actor
    )


def mark_quota_limited(conn: Any, account_id: str, actor: str) -> PlatformAccount:
    return repo.update_platform_account_status(conn, account_id, "quota_limited", actor)
