"""Phase 18C — channel-scoped authorization for unattended public publishing.

This module answers exactly one question, and answers it conservatively:

    May this channel, right now, perform an autonomous public publishing
    side effect without a human approving this specific video?

The answer requires FOUR independent things to be true (section 4's defence
in depth). None of them implies any other, and none of them may be inferred
from decision automation, production automation, an OAuth connection, a
configured cadence, or the mere existence of a READY render:

  1. ACE_PUBLISHING_LIVE_ENABLED   — global emergency kill switch (env)
  2. ACE_RELEASE_PUBLIC_ENABLED    — global emergency kill switch (env)
  3. channel authorization         — persisted, per-channel, revocable (this module)
  4. runtime safety checks         — rate limit, account health (this module)

The two env gates keep their pre-existing meaning; this phase does not
redefine or weaken them. They remain global because a process-level
environment variable is the correct shape for an emergency stop that must
halt every channel at once without a database write.

Authorization state is deliberately a table of its own rather than a column
on autonomy_policies: granting a channel the right to publish publicly with
no per-video review is categorically unlike setting a cadence, and it must
be impossible to flip as a side effect of an unrelated policy edit.

Every grant and revocation emits a cp_events audit record. Revocation takes
effect immediately — `evaluate_publishing_authorization` is re-consulted
before every external side effect, including between upload and release.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

# Account statuses that must block an autonomous publish. `credential_expiring`
# is deliberately absent: the token refresh path handles it, and blocking on it
# would stall publishing for a credential that still works.
BLOCKING_ACCOUNT_STATUSES = frozenset(
    {"disconnected", "credential_invalid", "quota_limited", "paused"}
)

DEFAULT_MAX_PUBLICATIONS_PER_24H = 1
DEFAULT_MISSED_SLOT_GRACE_MINUTES = 120


class BlockReason(StrEnum):
    """Why an autonomous publish may not proceed. Ordered by check sequence."""

    global_publishing_gate_off = "global_publishing_gate_off"
    global_release_gate_off = "global_release_gate_off"
    channel_not_authorized = "channel_not_authorized"
    rate_limit_reached = "rate_limit_reached"
    account_unhealthy = "account_unhealthy"
    no_account = "no_account"
    # The credential can upload but cannot change a video's privacy status.
    # Deliberately distinct from account_unhealthy: the account is fine, it is
    # the granted scope set that is insufficient, and the remedy is a specific
    # OAuth re-consent rather than a reconnect.
    release_scope_missing = "release_scope_missing"


@dataclass
class ChannelPublishingAuthorization:
    """Persisted per-channel authorization for unattended public publishing."""

    channel_id: str
    workspace_id: str
    authorized: bool
    authorized_at: str | None
    authorized_by: str | None
    revoked_at: str | None
    revoked_by: str | None
    policy_version: int
    max_publications_per_24h: int
    missed_slot_grace_minutes: int
    created_at: str | None = None
    updated_at: str | None = None


@dataclass
class AuthorizationDecision:
    """The result of evaluating all four authorization layers.

    `allowed` is true only when every layer passes. `blocked_by` lists every
    failing layer rather than short-circuiting at the first, so an operator
    sees the complete picture instead of fixing one blocker at a time.
    """

    allowed: bool
    blocked_by: list[BlockReason] = field(default_factory=list)
    detail: str = ""
    global_publishing_enabled: bool = False
    global_release_enabled: bool = False
    channel_authorized: bool = False
    publications_last_24h: int = 0
    max_publications_per_24h: int = DEFAULT_MAX_PUBLICATIONS_PER_24H
    account_id: str | None = None
    account_status: str | None = None
    release_scope_granted: bool = False

    @property
    def primary_reason(self) -> BlockReason | None:
        return self.blocked_by[0] if self.blocked_by else None


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _row_to_authorization(row: sqlite3.Row) -> ChannelPublishingAuthorization:
    return ChannelPublishingAuthorization(
        channel_id=row["channel_id"],
        workspace_id=row["workspace_id"],
        authorized=bool(row["authorized"]),
        authorized_at=row["authorized_at"],
        authorized_by=row["authorized_by"],
        revoked_at=row["revoked_at"],
        revoked_by=row["revoked_by"],
        policy_version=row["policy_version"],
        max_publications_per_24h=row["max_publications_per_24h"],
        missed_slot_grace_minutes=row["missed_slot_grace_minutes"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def get_channel_publishing_authorization(
    conn: sqlite3.Connection, channel_id: str
) -> ChannelPublishingAuthorization | None:
    """Return the channel's authorization row, or None if never configured.

    A missing row means NOT authorized. Absence is never permission.
    """
    row = conn.execute(
        "SELECT * FROM channel_publishing_authorizations WHERE channel_id = ?",
        (channel_id,),
    ).fetchone()
    return _row_to_authorization(row) if row else None


def _emit_authorization_event(
    conn: sqlite3.Connection,
    *,
    event_type: str,
    workspace_id: str,
    channel_id: str,
    actor: str,
    payload: dict[str, Any],
) -> None:
    """Write a cp_events audit record. Best-effort: never fails the caller.

    An authorization change that succeeded but whose audit write failed is
    strictly better than one rolled back for want of a log line — the state
    change itself is the safety-critical part, and the failure is logged.
    """
    import logging

    from app.control_plane.models import ControlEventDraft
    from app.control_plane.repository import create_event

    try:
        create_event(
            conn,
            ControlEventDraft(
                id=str(uuid.uuid4()),
                event_type=event_type,
                workspace_id=workspace_id,
                actor=actor,
                channel_id=channel_id,
                source_engine="publishing.authorization",
                source_entity_id=channel_id,
                payload=payload,
            ),
        )
    except Exception as exc:  # pragma: no cover - audit is best-effort
        logging.getLogger(__name__).warning(
            "publishing authorization audit event write failed (non-fatal): %s", exc
        )


def grant_channel_publishing_authorization(
    conn: sqlite3.Connection,
    *,
    channel_id: str,
    workspace_id: str,
    actor: str,
    max_publications_per_24h: int | None = None,
    missed_slot_grace_minutes: int | None = None,
) -> ChannelPublishingAuthorization:
    """Authorize a channel for unattended public publishing.

    This is a deliberate operator action, not a form toggle. It records who
    granted it and when, bumps policy_version, clears any prior revocation,
    and emits an audit event.
    """
    now = _now_iso()
    existing = get_channel_publishing_authorization(conn, channel_id)

    limit = (
        max_publications_per_24h
        if max_publications_per_24h is not None
        else (existing.max_publications_per_24h if existing else DEFAULT_MAX_PUBLICATIONS_PER_24H)
    )
    grace = (
        missed_slot_grace_minutes
        if missed_slot_grace_minutes is not None
        else (existing.missed_slot_grace_minutes if existing else DEFAULT_MISSED_SLOT_GRACE_MINUTES)
    )
    if limit <= 0:
        raise ValueError("max_publications_per_24h must be greater than zero.")
    if grace < 0:
        raise ValueError("missed_slot_grace_minutes must not be negative.")

    if existing is None:
        conn.execute(
            """INSERT INTO channel_publishing_authorizations
               (channel_id, workspace_id, authorized, authorized_at, authorized_by,
                revoked_at, revoked_by, policy_version, max_publications_per_24h,
                missed_slot_grace_minutes, created_at, updated_at)
               VALUES (?, ?, 1, ?, ?, NULL, NULL, 1, ?, ?, ?, ?)""",
            (channel_id, workspace_id, now, actor, limit, grace, now, now),
        )
        new_version = 1
    else:
        new_version = existing.policy_version + 1
        conn.execute(
            """UPDATE channel_publishing_authorizations
               SET authorized = 1, authorized_at = ?, authorized_by = ?,
                   revoked_at = NULL, revoked_by = NULL, policy_version = ?,
                   max_publications_per_24h = ?, missed_slot_grace_minutes = ?,
                   updated_at = ?
               WHERE channel_id = ?""",
            (now, actor, new_version, limit, grace, now, channel_id),
        )

    _emit_authorization_event(
        conn,
        event_type="channel.publishing_authorization_granted",
        workspace_id=workspace_id,
        channel_id=channel_id,
        actor=actor,
        payload={
            "channel_id": channel_id,
            "policy_version": new_version,
            "max_publications_per_24h": limit,
            "missed_slot_grace_minutes": grace,
        },
    )
    conn.commit()

    result = get_channel_publishing_authorization(conn, channel_id)
    assert result is not None
    return result


def revoke_channel_publishing_authorization(
    conn: sqlite3.Connection,
    *,
    channel_id: str,
    workspace_id: str,
    actor: str,
    reason: str | None = None,
) -> ChannelPublishingAuthorization:
    """Revoke a channel's publishing authorization. Takes effect immediately.

    Revocation deliberately does NOT touch queued or ready production work
    (section 3): turning publishing off must not destroy artifacts the
    channel already spent resources producing.
    """
    now = _now_iso()
    existing = get_channel_publishing_authorization(conn, channel_id)

    if existing is None:
        # Persist an explicit un-authorized row so the revocation is auditable
        # even for a channel that was never granted authorization.
        conn.execute(
            """INSERT INTO channel_publishing_authorizations
               (channel_id, workspace_id, authorized, authorized_at, authorized_by,
                revoked_at, revoked_by, policy_version, max_publications_per_24h,
                missed_slot_grace_minutes, created_at, updated_at)
               VALUES (?, ?, 0, NULL, NULL, ?, ?, 1, ?, ?, ?, ?)""",
            (
                channel_id,
                workspace_id,
                now,
                actor,
                DEFAULT_MAX_PUBLICATIONS_PER_24H,
                DEFAULT_MISSED_SLOT_GRACE_MINUTES,
                now,
                now,
            ),
        )
        new_version = 1
    else:
        new_version = existing.policy_version + 1
        conn.execute(
            """UPDATE channel_publishing_authorizations
               SET authorized = 0, authorized_at = NULL, authorized_by = NULL,
                   revoked_at = ?, revoked_by = ?, policy_version = ?, updated_at = ?
               WHERE channel_id = ?""",
            (now, actor, new_version, now, channel_id),
        )

    _emit_authorization_event(
        conn,
        event_type="channel.publishing_authorization_revoked",
        workspace_id=workspace_id,
        channel_id=channel_id,
        actor=actor,
        payload={"channel_id": channel_id, "policy_version": new_version, "reason": reason},
    )
    conn.commit()

    result = get_channel_publishing_authorization(conn, channel_id)
    assert result is not None
    return result


def update_publishing_limits(
    conn: sqlite3.Connection,
    *,
    channel_id: str,
    workspace_id: str,
    actor: str,
    max_publications_per_24h: int | None = None,
    missed_slot_grace_minutes: int | None = None,
) -> ChannelPublishingAuthorization:
    """Adjust safety limits WITHOUT changing the authorization boolean.

    Deliberately separated from grant/revoke so that tuning a rate limit can
    never be the thing that authorizes a channel.
    """
    existing = get_channel_publishing_authorization(conn, channel_id)
    now = _now_iso()

    if existing is None:
        limit = max_publications_per_24h or DEFAULT_MAX_PUBLICATIONS_PER_24H
        grace = (
            missed_slot_grace_minutes
            if missed_slot_grace_minutes is not None
            else DEFAULT_MISSED_SLOT_GRACE_MINUTES
        )
        if limit <= 0:
            raise ValueError("max_publications_per_24h must be greater than zero.")
        if grace < 0:
            raise ValueError("missed_slot_grace_minutes must not be negative.")
        conn.execute(
            """INSERT INTO channel_publishing_authorizations
               (channel_id, workspace_id, authorized, authorized_at, authorized_by,
                revoked_at, revoked_by, policy_version, max_publications_per_24h,
                missed_slot_grace_minutes, created_at, updated_at)
               VALUES (?, ?, 0, NULL, NULL, NULL, NULL, 1, ?, ?, ?, ?)""",
            (channel_id, workspace_id, limit, grace, now, now),
        )
    else:
        limit = (
            max_publications_per_24h
            if max_publications_per_24h is not None
            else existing.max_publications_per_24h
        )
        grace = (
            missed_slot_grace_minutes
            if missed_slot_grace_minutes is not None
            else existing.missed_slot_grace_minutes
        )
        if limit <= 0:
            raise ValueError("max_publications_per_24h must be greater than zero.")
        if grace < 0:
            raise ValueError("missed_slot_grace_minutes must not be negative.")
        conn.execute(
            """UPDATE channel_publishing_authorizations
               SET max_publications_per_24h = ?, missed_slot_grace_minutes = ?, updated_at = ?
               WHERE channel_id = ?""",
            (limit, grace, now, channel_id),
        )

    _emit_authorization_event(
        conn,
        event_type="channel.publishing_limits_updated",
        workspace_id=workspace_id,
        channel_id=channel_id,
        actor=actor,
        payload={
            "channel_id": channel_id,
            "max_publications_per_24h": limit,
            "missed_slot_grace_minutes": grace,
        },
    )
    conn.commit()

    result = get_channel_publishing_authorization(conn, channel_id)
    assert result is not None
    return result


def count_publications_last_24h(
    conn: sqlite3.Connection,
    channel_id: str,
    *,
    exclude_publication_id: int | None = None,
) -> int:
    """Count this channel's publications created in the trailing 24 hours.

    Counts by created_at rather than published_at so that an upload which
    failed partway through still consumes rate-limit budget — the protection
    exists to bound external side effects, not successful outcomes.

    `exclude_publication_id` omits one publication from the count, and exists
    for a specific and necessary reason: the publishing cycle re-evaluates
    authorization between upload and release, and by that point the upload
    has already created the Publication row for the video being released.
    Counting it would make the cycle rate-limit itself out of finishing its
    own work — with the conservative default of 1/24h, every publication
    would upload and then refuse to go public. A video already uploaded under
    this slot's authorization is not a second publication.
    """
    cutoff = (datetime.now(UTC) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")
    if exclude_publication_id is not None:
        row = conn.execute(
            """SELECT COUNT(*) AS n FROM publications
               WHERE channel_id = ? AND deleted_at IS NULL AND created_at >= ?
                 AND id != ?""",
            (channel_id, cutoff, exclude_publication_id),
        ).fetchone()
    else:
        row = conn.execute(
            """SELECT COUNT(*) AS n FROM publications
               WHERE channel_id = ? AND deleted_at IS NULL AND created_at >= ?""",
            (channel_id, cutoff),
        ).fetchone()
    return int(row["n"]) if row else 0


def get_publishing_account(
    conn: sqlite3.Connection, channel_id: str
) -> tuple[str | None, str | None]:
    """Return (account_id, status) for the channel's YouTube account.

    Returns (None, None) when the channel has no platform account at all.
    """
    row = conn.execute(
        """SELECT id, status FROM cp_platform_accounts
           WHERE channel_id = ? AND platform_key = 'youtube'
           ORDER BY created_at ASC LIMIT 1""",
        (channel_id,),
    ).fetchone()
    if row is None:
        return None, None
    return row["id"], row["status"]


def _has_release_scope(conn: sqlite3.Connection, *, account_id: str, channel_id: str) -> bool:
    """Whether the account's stored credential carries youtube.force-ssl.

    Wraps the canonical oauth helper so an environment without a token store
    (tests, a fresh install) degrades to False rather than raising — absence of
    evidence is never treated as permission here.
    """
    try:
        from app.oauth.flow import has_release_scope

        row = conn.execute(
            "SELECT workspace_id FROM cp_channels WHERE id = ?", (channel_id,)
        ).fetchone()
        if row is None:
            return False
        return bool(
            has_release_scope(
                conn,
                account_id=account_id,
                workspace_id=row["workspace_id"],
                channel_id=channel_id,
            )
        )
    except Exception:
        return False


def evaluate_publishing_authorization(
    conn: sqlite3.Connection,
    *,
    channel_id: str,
    config: Any = None,
    exclude_publication_id: int | None = None,
) -> AuthorizationDecision:
    """Evaluate all four authorization layers for one channel.

    This is THE gate. Every autonomous external publishing side effect must
    call it immediately beforehand — including the release step, after the
    upload has already happened, so that a revocation landing mid-cycle stops
    the video from going public (section 18).

    Collects every failing layer rather than short-circuiting, so an operator
    can see all blockers at once.
    """
    if config is None:
        from app.core.config import get_config

        config = get_config()

    decision = AuthorizationDecision(allowed=False)
    decision.global_publishing_enabled = bool(getattr(config, "publishing_live_enabled", False))
    decision.global_release_enabled = bool(getattr(config, "release_public_enabled", False))

    if not decision.global_publishing_enabled:
        decision.blocked_by.append(BlockReason.global_publishing_gate_off)
    if not decision.global_release_enabled:
        decision.blocked_by.append(BlockReason.global_release_gate_off)

    auth = get_channel_publishing_authorization(conn, channel_id)
    decision.channel_authorized = bool(auth and auth.authorized)
    decision.max_publications_per_24h = (
        auth.max_publications_per_24h if auth else DEFAULT_MAX_PUBLICATIONS_PER_24H
    )
    if not decision.channel_authorized:
        decision.blocked_by.append(BlockReason.channel_not_authorized)

    decision.publications_last_24h = count_publications_last_24h(
        conn, channel_id, exclude_publication_id=exclude_publication_id
    )
    if decision.publications_last_24h >= decision.max_publications_per_24h:
        decision.blocked_by.append(BlockReason.rate_limit_reached)

    account_id, account_status = get_publishing_account(conn, channel_id)
    decision.account_id = account_id
    decision.account_status = account_status
    if account_id is None:
        decision.blocked_by.append(BlockReason.no_account)
    elif account_status in BLOCKING_ACCOUNT_STATUSES:
        decision.blocked_by.append(BlockReason.account_unhealthy)

    # Scope check. An upload-capable credential is NOT release-capable:
    # youtube.upload authorizes videos.insert, while setting privacyStatus is a
    # videos.update call requiring youtube.force-ssl. Without this the cycle
    # would upload a video to the real channel and then be unable to publish it,
    # leaving an orphan private video behind. Read-only, no network call.
    if account_id is not None:
        decision.release_scope_granted = _has_release_scope(
            conn, account_id=account_id, channel_id=channel_id
        )
        if not decision.release_scope_granted:
            decision.blocked_by.append(BlockReason.release_scope_missing)

    decision.allowed = not decision.blocked_by
    decision.detail = (
        "All publishing authorization layers passed."
        if decision.allowed
        else "Blocked by: " + ", ".join(r.value for r in decision.blocked_by)
    )
    return decision
