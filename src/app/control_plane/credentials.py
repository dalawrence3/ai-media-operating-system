"""Credential profile management (safe metadata only — no secrets)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from app.control_plane import repository as repo
from app.control_plane.models import CredentialProfile, CredentialProfileDraft


def create_credential_profile(
    conn: Any,
    *,
    workspace_id: str,
    display_name: str,
    credential_type: str,
    external_ref: str,
    actor: str,
    expires_at: datetime | None = None,
) -> CredentialProfile:
    draft = CredentialProfileDraft(
        id=str(uuid.uuid4()),
        workspace_id=workspace_id,
        display_name=display_name,
        credential_type=credential_type,
        external_ref=external_ref,
        actor=actor,
        status="active",
        expires_at=expires_at,
    )
    return repo.create_credential_profile(conn, draft)


def revoke_credential(conn: Any, profile_id: str, actor: str) -> CredentialProfile:
    return repo.update_credential_status(conn, profile_id, "revoked", actor)


def mark_credential_expiring(
    conn: Any, profile_id: str, actor: str
) -> CredentialProfile:
    return repo.update_credential_status(conn, profile_id, "expiring", actor)


def mark_credential_expired(
    conn: Any, profile_id: str, actor: str
) -> CredentialProfile:
    return repo.update_credential_status(conn, profile_id, "expired", actor)


def reactivate_credential(conn: Any, profile_id: str, actor: str) -> CredentialProfile:
    return repo.update_credential_status(conn, profile_id, "active", actor)
