"""Phase 18C — surviving the uncertain upload.

There is exactly one failure mode in this system that can create a duplicate
YouTube video, and it is not a retry bug:

    provider.upload() succeeds → the process dies (or the DB write fails)
    before the returned provider_video_id is persisted → the next run sees
    no local evidence of an upload → it uploads again.

YouTube's videos.insert accepts no client-supplied idempotency key, so the
provider cannot deduplicate for us. The strongest practical defence is
therefore a write-ahead intent record: we commit a row saying "we are about
to upload for slot N" BEFORE the provider call, and resolve that row after.
A crash then always leaves evidence behind.

State machine for an attempt:

    intent_recorded ──upload returns──▶ succeeded  (video id persisted)
          │
          └──crash / exception with unknown outcome──▶ uncertain
                                                          │
                            reconcile against provider ───┤
                                                          ▼
                                            reconciled (video found → adopted)
                                            failed     (no video → safe to retry)

An `uncertain` attempt BLOCKS further upload attempts for that slot until it
is reconciled. Refusing to publish is always recoverable; a duplicate public
video on a real channel is not.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class UploadAttempt:
    id: int
    attempt_key: str
    slot_id: int
    publishing_plan_id: int
    channel_id: str
    workspace_id: str
    state: str
    provider: str
    provider_video_id: str | None
    error_message: str | None
    created_at: str
    resolved_at: str | None


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _row(r: sqlite3.Row) -> UploadAttempt:
    return UploadAttempt(
        id=r["id"],
        attempt_key=r["attempt_key"],
        slot_id=r["slot_id"],
        publishing_plan_id=r["publishing_plan_id"],
        channel_id=r["channel_id"],
        workspace_id=r["workspace_id"],
        state=r["state"],
        provider=r["provider"],
        provider_video_id=r["provider_video_id"],
        error_message=r["error_message"],
        created_at=r["created_at"],
        resolved_at=r["resolved_at"],
    )


def record_upload_intent(
    conn: sqlite3.Connection,
    *,
    attempt_key: str,
    slot_id: int,
    publishing_plan_id: int,
    channel_id: str,
    workspace_id: str,
    provider: str,
) -> UploadAttempt:
    """Commit the intent to upload BEFORE calling the provider.

    Committed immediately and deliberately outside any enclosing transaction:
    the whole point is that this row survives a crash of the very next
    statement. Re-recording an existing attempt_key returns the existing row
    unchanged, so a resumed cycle reuses its own prior intent.
    """
    existing = conn.execute(
        "SELECT * FROM publishing_upload_attempts WHERE attempt_key = ?", (attempt_key,)
    ).fetchone()
    if existing is not None:
        return _row(existing)

    conn.execute(
        """INSERT INTO publishing_upload_attempts
           (attempt_key, slot_id, publishing_plan_id, channel_id, workspace_id,
            state, provider, created_at)
           VALUES (?, ?, ?, ?, ?, 'intent_recorded', ?, ?)""",
        (attempt_key, slot_id, publishing_plan_id, channel_id, workspace_id, provider, _now_iso()),
    )
    conn.commit()

    row = conn.execute(
        "SELECT * FROM publishing_upload_attempts WHERE attempt_key = ?", (attempt_key,)
    ).fetchone()
    return _row(row)


def mark_attempt_succeeded(
    conn: sqlite3.Connection, attempt_key: str, *, provider_video_id: str
) -> None:
    """Persist the provider video ID the instant the upload returns.

    Committed on its own before any further work, so the id is durable even
    if everything downstream fails.
    """
    conn.execute(
        """UPDATE publishing_upload_attempts
           SET state = 'succeeded', provider_video_id = ?, resolved_at = ?
           WHERE attempt_key = ?""",
        (provider_video_id, _now_iso(), attempt_key),
    )
    conn.commit()


def mark_attempt_uncertain(conn: sqlite3.Connection, attempt_key: str, *, error: str) -> None:
    """Mark an attempt whose provider outcome is genuinely unknown.

    Used when the provider call raised in a way that does not prove the
    upload did not happen (timeouts, connection resets, unexpected errors
    mid-transfer). Blocks retries until reconciled.
    """
    conn.execute(
        """UPDATE publishing_upload_attempts
           SET state = 'uncertain', error_message = ?, resolved_at = NULL
           WHERE attempt_key = ?""",
        (error[:2000], attempt_key),
    )
    conn.commit()


def mark_attempt_failed(conn: sqlite3.Connection, attempt_key: str, *, error: str) -> None:
    """Mark an attempt that provably never reached the provider.

    Only for failures raised before the upload call, or errors that prove no
    video was created. Safe to retry after this.
    """
    conn.execute(
        """UPDATE publishing_upload_attempts
           SET state = 'failed', error_message = ?, resolved_at = ?
           WHERE attempt_key = ?""",
        (error[:2000], _now_iso(), attempt_key),
    )
    conn.commit()


def mark_attempt_reconciled(
    conn: sqlite3.Connection, attempt_key: str, *, provider_video_id: str | None, note: str
) -> None:
    """Resolve an uncertain attempt after checking the provider."""
    conn.execute(
        """UPDATE publishing_upload_attempts
           SET state = 'reconciled', provider_video_id = ?, error_message = ?, resolved_at = ?
           WHERE attempt_key = ?""",
        (provider_video_id, note[:2000], _now_iso(), attempt_key),
    )
    conn.commit()


def get_attempt(conn: sqlite3.Connection, attempt_key: str) -> UploadAttempt | None:
    row = conn.execute(
        "SELECT * FROM publishing_upload_attempts WHERE attempt_key = ?", (attempt_key,)
    ).fetchone()
    return _row(row) if row else None


def find_unresolved_attempt_for_slot(
    conn: sqlite3.Connection, slot_id: int
) -> UploadAttempt | None:
    """Return an attempt for this slot that blocks a fresh upload.

    `intent_recorded` counts as blocking: a row still in that state means a
    previous run announced an upload and never resolved it — which is exactly
    the crash signature we are defending against.
    """
    row = conn.execute(
        """SELECT * FROM publishing_upload_attempts
           WHERE slot_id = ? AND state IN ('intent_recorded', 'uncertain')
           ORDER BY id DESC LIMIT 1""",
        (slot_id,),
    ).fetchone()
    return _row(row) if row else None


def find_succeeded_attempt_for_slot(conn: sqlite3.Connection, slot_id: int) -> UploadAttempt | None:
    """Return a completed upload for this slot, if one exists.

    A resumed cycle uses this to skip straight to release rather than
    re-uploading a video that already exists on the provider.
    """
    row = conn.execute(
        """SELECT * FROM publishing_upload_attempts
           WHERE slot_id = ? AND state IN ('succeeded', 'reconciled')
             AND provider_video_id IS NOT NULL
           ORDER BY id DESC LIMIT 1""",
        (slot_id,),
    ).fetchone()
    return _row(row) if row else None


def reconcile_uncertain_attempt(
    conn: sqlite3.Connection,
    attempt: UploadAttempt,
    *,
    yt_client: Any,
    expected_title: str,
) -> UploadAttempt:
    """Determine whether an uncertain attempt actually created a video.

    Strategy, in order of reliability:

    1. If a Publication row already carries a provider_video_id for this
       plan, the upload plainly succeeded — adopt it. (Local evidence beats
       a network call.)
    2. Otherwise ask the provider for the account's recent uploads and look
       for a title match. YouTube offers no idempotency key, so title
       matching against a bounded recent window is the strongest signal
       available.
    3. If the provider cannot be queried, the attempt STAYS uncertain. We
       never downgrade uncertainty to "safe to retry" on the strength of a
       failed lookup — that is precisely how duplicates get created.
    """
    # 1. Local evidence first.
    pub = conn.execute(
        """SELECT id, provider_video_id FROM publications
           WHERE publishing_plan_id = ? AND provider_video_id IS NOT NULL
             AND deleted_at IS NULL
           ORDER BY id DESC LIMIT 1""",
        (attempt.publishing_plan_id,),
    ).fetchone()
    if pub is not None:
        mark_attempt_reconciled(
            conn,
            attempt.attempt_key,
            provider_video_id=pub["provider_video_id"],
            note=f"Adopted existing publication {pub['id']} for the same plan.",
        )
        logger.info(
            "upload reconciliation: attempt %s adopted existing publication %d",
            attempt.attempt_key,
            pub["id"],
        )
        result = get_attempt(conn, attempt.attempt_key)
        assert result is not None
        return result

    # 2. Ask the provider what it actually has.
    lister = getattr(yt_client, "list_my_recent_videos", None)
    if lister is None:
        logger.warning(
            "upload reconciliation: provider client exposes no recent-uploads lookup; "
            "attempt %s stays uncertain (refusing to risk a duplicate upload)",
            attempt.attempt_key,
        )
        return attempt

    try:
        recent = lister(max_results=10)
    except Exception as exc:
        logger.warning(
            "upload reconciliation: recent-uploads lookup failed for attempt %s: %s — "
            "staying uncertain rather than risking a duplicate",
            attempt.attempt_key,
            exc,
        )
        return attempt

    for video in recent:
        if (video.get("title") or "").strip() == expected_title.strip():
            vid = video.get("video_id")
            mark_attempt_reconciled(
                conn,
                attempt.attempt_key,
                provider_video_id=vid,
                note=f"Matched an existing provider upload by title: {expected_title!r}.",
            )
            logger.info(
                "upload reconciliation: attempt %s matched existing provider video %s by title",
                attempt.attempt_key,
                vid,
            )
            result = get_attempt(conn, attempt.attempt_key)
            assert result is not None
            return result

    # 3. Provider answered and has no such video — the upload provably did not
    #    land. Safe to retry.
    mark_attempt_failed(
        conn,
        attempt.attempt_key,
        error=(
            "Provider's recent uploads contain no video matching this attempt; "
            "concluded the upload never completed. Safe to retry."
        ),
    )
    logger.info(
        "upload reconciliation: attempt %s confirmed as never-uploaded; retry permitted",
        attempt.attempt_key,
    )
    result = get_attempt(conn, attempt.attempt_key)
    assert result is not None
    return result
