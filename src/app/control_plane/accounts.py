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


# ── Canonical health recovery (Phase 18E.2) ──────────────────────────────────
#
# Extracted from app.analytics.auto_observer, which had the only working
# "credential_invalid heals itself" path in the system. That path only ran
# after a successful analytics observation; a live credential re-verification
# (app.oauth.flow.verify_youtube_connection) had no equivalent, so it could
# prove a credential was fine again and never actually say so anywhere the
# dashboard reads. This is the single place recovery is decided so both
# callers reach the exact same two-pass outcome.

# Statuses set by transient failures that a later successful check can repair.
RECOVERABLE_ACCOUNT_STATUSES = frozenset(
    {"credential_invalid", "credential_expiring", "quota_limited"}
)

# Health statuses a fresh 'healthy' record should supersede.
NON_HEALTHY_HEALTH_STATUSES = frozenset(
    {"degraded", "unavailable", "credential_expired", "quota_limited", "failed"}
)

# Operator-set intentional states that must never be auto-restored by any
# passive recovery path — only an explicit operator action reverses these.
OPERATOR_INTENT_STATUSES = frozenset({"disconnected", "paused"})


def restore_account_health(
    conn: Any,
    *,
    account_id: str,
    workspace_id: str,
    recorded_by: str,
    detail: str,
    event_payload: dict[str, Any] | None = None,
) -> bool:
    """Restore a platform account's status and health after proof it works again.

    "Proof" means the caller actually exercised the credential successfully —
    this function does not itself verify anything, it only records that
    verification happened. Two independent passes, exactly as the recovery
    that already worked for the observer path:

    Pass 1 — account status: if currently in a recoverable degraded state,
    restore it to 'connected' and emit EVENT_ACCOUNT_RESUMED. Intentional
    operator states (disconnected, paused) are never touched by this — those
    require an explicit operator action, never a passive success.

    Pass 2 — health record: if the latest health record for this account is
    non-healthy, write a canonical 'healthy' one — regardless of what pass 1
    did, so a stale degraded record left over from a state OAuth already fixed
    (e.g. reconnection updated account.status directly) still gets closed out.

    Returns True if either pass changed anything, for callers that want to log
    "recovery happened" versus "nothing needed recovering".

    Idempotent: calling this repeatedly once healthy is a no-op both passes.
    """
    from app.control_plane.constants import EVENT_ACCOUNT_RESUMED
    from app.control_plane.events import emit_event
    from app.control_plane.health import get_health, record_health

    acct = repo.get_platform_account(conn, account_id)
    changed = False

    if acct.status in RECOVERABLE_ACCOUNT_STATUSES:
        resume_account(conn, account_id, recorded_by)
        conn.commit()
        emit_event(
            conn,
            event_type=EVENT_ACCOUNT_RESUMED,
            workspace_id=workspace_id,
            actor=recorded_by,
            platform_account_id=account_id,
            payload={"account_id": account_id, "reason": detail, **(event_payload or {})},
        )
        conn.commit()
        changed = True

    if acct.status not in OPERATOR_INTENT_STATUSES:
        latest = get_health(conn, "platform_account", account_id)
        if latest is None or latest.status in NON_HEALTHY_HEALTH_STATUSES:
            record_health(
                conn,
                entity_type="platform_account",
                entity_id=account_id,
                status="healthy",
                recorded_by=recorded_by,
                detail=detail,
            )
            conn.commit()
            changed = True

    return changed


def mark_credential_invalid(conn: Any, account_id: str, actor: str) -> PlatformAccount:
    return repo.update_platform_account_status(conn, account_id, "credential_invalid", actor)


def mark_credential_expiring(conn: Any, account_id: str, actor: str) -> PlatformAccount:
    return repo.update_platform_account_status(conn, account_id, "credential_expiring", actor)


def mark_quota_limited(conn: Any, account_id: str, actor: str) -> PlatformAccount:
    return repo.update_platform_account_status(conn, account_id, "quota_limited", actor)
