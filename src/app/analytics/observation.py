"""Analytics observation scheduling — register, recover, and manage cadence.

One app_schedule_definitions row per publication tracks WHEN to observe.
One analytics_observation_state row per publication tracks WHAT was found.

Age-aware cadence:
    0–6 h   → hourly    (3 600 s)
    6–24 h  → 3-hourly  (10 800 s)
    1–3 d   → 6-hourly  (21 600 s)
    3–7 d   → 12-hourly (43 200 s)
    7–30 d  → daily     (86 400 s)
    30 d+   → 3-daily   (259 200 s)

Registration is idempotent: calling register_publication_for_observation() on
an already-registered publication is a no-op.

Recovery: reconcile_unobserved_publications() discovers public publications
that have no active observation schedule and registers them.  It is safe to
call repeatedly — each call only adopts orphans.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

OPERATION_TYPE = "analytics_observation"

# Maximum consecutive failures before pausing observation.
MAX_CONSECUTIVE_FAILURES = 5

# ── Cadence ──────────────────────────────────────────────────────────────────


def compute_observation_interval_seconds(published_at_iso: str | None) -> int:
    """Return the polling interval in seconds based on publication age."""
    if published_at_iso is None:
        return 21600  # 6 h default when age is unknown

    try:
        published_at = datetime.fromisoformat(published_at_iso.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return 21600

    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=UTC)

    age = datetime.now(UTC) - published_at
    hours = age.total_seconds() / 3600

    if hours < 6:
        return 3600
    if hours < 24:
        return 10800
    if hours < 72:
        return 21600
    if hours < 168:
        return 43200
    if hours < 720:
        return 86400
    return 259200


# ── Observation state ────────────────────────────────────────────────────────


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def get_observation_state(conn: Any, publication_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM analytics_observation_state WHERE publication_id = ?",
        (publication_id,),
    ).fetchone()
    return dict(row) if row else None


def upsert_observation_state(
    conn: Any,
    *,
    publication_id: int,
    workspace_id: str,
    channel_id: str | None = None,
    platform_account_id: str | None = None,
    schedule_id: str | None = None,
    observation_status: str = "active",
    last_attempted_at: str | None = None,
    last_success_at: str | None = None,
    latest_snapshot_id: int | None = None,
    retention_acquired: bool = False,
    consecutive_no_data: int = 0,
    failure_count: int = 0,
    commit: bool = True,
) -> None:
    now = _now()
    existing = get_observation_state(conn, publication_id)
    if existing is None:
        conn.execute(
            """
            INSERT INTO analytics_observation_state (
                publication_id, workspace_id, channel_id, platform_account_id,
                schedule_id, observation_status,
                last_attempted_at, last_success_at, latest_snapshot_id,
                retention_acquired, consecutive_no_data, failure_count,
                created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                publication_id,
                workspace_id,
                channel_id,
                platform_account_id,
                schedule_id,
                observation_status,
                last_attempted_at,
                last_success_at,
                latest_snapshot_id,
                1 if retention_acquired else 0,
                consecutive_no_data,
                failure_count,
                now,
                now,
            ),
        )
    else:
        # Merge: only update non-None supplied values over existing
        fields: list[str] = ["observation_status = ?", "updated_at = ?"]
        params: list = [observation_status, now]

        if last_attempted_at is not None:
            fields.append("last_attempted_at = ?")
            params.append(last_attempted_at)
        if last_success_at is not None:
            fields.append("last_success_at = ?")
            params.append(last_success_at)
        if latest_snapshot_id is not None:
            fields.append("latest_snapshot_id = ?")
            params.append(latest_snapshot_id)
        if schedule_id is not None:
            fields.append("schedule_id = ?")
            params.append(schedule_id)
        if platform_account_id is not None:
            fields.append("platform_account_id = ?")
            params.append(platform_account_id)

        fields.append("retention_acquired = ?")
        params.append(1 if retention_acquired else int(existing.get("retention_acquired") or 0))
        fields.append("consecutive_no_data = ?")
        params.append(consecutive_no_data)
        fields.append("failure_count = ?")
        params.append(failure_count)

        params.append(publication_id)
        conn.execute(
            f"UPDATE analytics_observation_state SET {', '.join(fields)} WHERE publication_id = ?",
            params,
        )
    if commit:
        conn.commit()


# ── Schedule registration ────────────────────────────────────────────────────


def _active_observation_schedule_id(conn: Any, publication_id: int) -> str | None:
    """Return the schedule_id of an active observation schedule for this publication, or None."""
    rows = conn.execute(
        """
        SELECT id, schedule_config_json FROM app_schedule_definitions
        WHERE operation_type = ? AND is_active = 1
        """,
        (OPERATION_TYPE,),
    ).fetchall()
    for row in rows:
        try:
            cfg = json.loads(row["schedule_config_json"] or "{}")
            if int(cfg.get("publication_id", -1)) == publication_id:
                return row["id"]
        except (ValueError, TypeError):
            continue
    return None


def register_publication_for_observation(
    conn: Any,
    *,
    publication_id: int,
    workspace_id: str,
    channel_id: str | None = None,
    platform_account_id: str | None = None,
    published_at: str | None = None,
    commit: bool = True,
) -> str:
    """Idempotently register a publication for automatic analytics observation.

    Returns the schedule_id (existing or newly created).
    """
    # Idempotency: return existing if already active.
    existing_id = _active_observation_schedule_id(conn, publication_id)
    if existing_id is not None:
        logger.debug(
            "observation: publication %d already has active schedule %s",
            publication_id,
            existing_id,
        )
        return existing_id

    interval_seconds = compute_observation_interval_seconds(published_at)
    now = datetime.now(UTC)
    next_run_iso = now.strftime("%Y-%m-%dT%H:%M:%S")  # due immediately
    now_iso = now.isoformat()
    schedule_id = str(uuid.uuid4())

    config = {
        "publication_id": publication_id,
        "platform_account_id": platform_account_id,
        "interval_seconds": interval_seconds,
    }

    conn.execute(
        """
        INSERT INTO app_schedule_definitions (
            id, workspace_id, channel_id, name, operation_type,
            schedule_type, schedule_config_json, timezone,
            is_active, next_run_at, actor, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            schedule_id,
            workspace_id,
            channel_id,
            f"analytics_observation:pub_{publication_id}",
            OPERATION_TYPE,
            "interval",
            json.dumps(config),
            "UTC",
            1,
            next_run_iso,
            "system:auto_observer",
            now_iso,
            now_iso,
        ),
    )

    upsert_observation_state(
        conn,
        publication_id=publication_id,
        workspace_id=workspace_id,
        channel_id=channel_id,
        platform_account_id=platform_account_id,
        schedule_id=schedule_id,
        observation_status="active",
        commit=False,
    )

    if commit:
        conn.commit()

    logger.info(
        "observation: registered publication %d for observation (schedule=%s, interval=%ds)",
        publication_id,
        schedule_id,
        interval_seconds,
    )
    return schedule_id


def advance_schedule_next_run(
    conn: Any,
    schedule_id: str,
    *,
    interval_seconds: int,
    commit: bool = True,
) -> None:
    """Advance next_run_at for an observation schedule after a completed tick."""
    now = datetime.now(UTC)
    next_run = now + timedelta(seconds=interval_seconds)
    conn.execute(
        "UPDATE app_schedule_definitions "
        "SET last_run_at = ?, next_run_at = ?, updated_at = ? "
        "WHERE id = ?",
        (
            now.strftime("%Y-%m-%dT%H:%M:%S"),
            next_run.strftime("%Y-%m-%dT%H:%M:%S"),
            now.isoformat(),
            schedule_id,
        ),
    )
    if commit:
        conn.commit()


def pause_observation_schedule(conn: Any, publication_id: int, *, commit: bool = True) -> None:
    """Pause the observation schedule for a publication after too many failures."""
    existing = get_observation_state(conn, publication_id)
    if existing is None:
        return
    schedule_id = existing.get("schedule_id")
    if schedule_id:
        conn.execute(
            "UPDATE app_schedule_definitions SET is_active = 0, updated_at = ? WHERE id = ?",
            (_now(), schedule_id),
        )
    conn.execute(
        "UPDATE analytics_observation_state "
        "SET observation_status = 'paused', updated_at = ? "
        "WHERE publication_id = ?",
        (_now(), publication_id),
    )
    if commit:
        conn.commit()
    logger.warning(
        "observation: paused observation for publication %d after repeated failures", publication_id
    )


# ── Recovery ─────────────────────────────────────────────────────────────────


def reconcile_unobserved_publications(
    conn: Any,
    *,
    provider: str = "youtube",
) -> list[int]:
    """Discover public publications with no active observation schedule and register them.

    Returns the list of publication_ids that were adopted.
    Safe to call repeatedly — already-registered publications are skipped.
    """
    rows = conn.execute(
        """
        SELECT p.id, p.workspace_id, p.channel_id, p.platform_account_id,
               p.published_at, pp.experiment_id
        FROM publications p
        JOIN publishing_plans pp ON pp.id = p.publishing_plan_id
        WHERE p.provider = ?
          AND p.visibility = 'public'
          AND p.status = 'published'
        """,
        (provider,),
    ).fetchall()

    adopted: list[int] = []
    for row in rows:
        pub_id = row["id"]
        existing_id = _active_observation_schedule_id(conn, pub_id)
        if existing_id is not None:
            continue  # already registered

        register_publication_for_observation(
            conn,
            publication_id=pub_id,
            workspace_id=row["workspace_id"] or "",
            channel_id=row["channel_id"],
            platform_account_id=row["platform_account_id"],
            published_at=row["published_at"],
        )
        adopted.append(pub_id)
        logger.info("observation: adopted orphan publication %d during reconciliation", pub_id)

    return adopted


# ── Due schedules ────────────────────────────────────────────────────────────


def get_due_observation_schedules(conn: Any) -> list[dict]:
    """Return due analytics_observation schedule rows."""
    now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    rows = conn.execute(
        """
        SELECT id, workspace_id, channel_id, schedule_config_json
        FROM app_schedule_definitions
        WHERE operation_type = ?
          AND is_active = 1
          AND (next_run_at IS NULL OR next_run_at <= ?)
        ORDER BY next_run_at ASC
        """,
        (OPERATION_TYPE, now_str),
    ).fetchall()
    return [dict(r) for r in rows]
