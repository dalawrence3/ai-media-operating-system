"""Scheduler — deterministic eligibility and schedule definitions.

No daemon. No cron installation. No distributed scheduler.
Computes due-operations at query time based on next_run_at timestamps.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.application.commands import SCHEDULE_TYPES
from app.application.contracts import ScheduleView
from app.application.errors import (
    InvalidScheduleTypeError,
    ScheduleNotFoundError,
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_iso(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _compute_next_run(
    schedule_type: str,
    schedule_config: dict[str, Any],
    tz_name: str,
    from_dt: datetime,
) -> datetime | None:
    """Compute the next run datetime given schedule parameters.

    Returns None for one-shot schedules that have already run.
    """
    if schedule_type == "once":
        run_at_str = schedule_config.get("run_at")
        if not run_at_str:
            return None
        dt = _parse_iso(run_at_str)
        return dt if dt > from_dt else None

    if schedule_type == "interval":
        seconds = int(schedule_config.get("interval_seconds", 3600))
        return from_dt + timedelta(seconds=seconds)

    if schedule_type == "after_event":
        # Eligibility determined by event occurrence, not time.
        return None

    if schedule_type == "cron":
        # Minimal cron: support only @daily, @hourly, @weekly as named shortcuts.
        expr = schedule_config.get("expression", "@daily")
        if expr == "@hourly":
            return from_dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        if expr == "@weekly":
            days_ahead = 7 - from_dt.weekday()
            return (from_dt + timedelta(days=days_ahead)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        # Default: @daily
        return (from_dt + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    return None


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def create_schedule(
    conn: Any,
    *,
    workspace_id: str,
    name: str,
    operation_type: str,
    schedule_type: str,
    schedule_config: dict[str, Any],
    actor: str,
    channel_id: str | None = None,
    timezone_name: str = "UTC",
) -> ScheduleView:
    if schedule_type not in SCHEDULE_TYPES:
        raise InvalidScheduleTypeError(schedule_type)

    sched_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    next_run = _compute_next_run(schedule_type, schedule_config, timezone_name, now)
    next_run_iso = next_run.isoformat() if next_run else None
    now_iso = now.isoformat()

    conn.execute(
        "INSERT INTO app_schedule_definitions "
        "(id, workspace_id, channel_id, name, operation_type, schedule_type, "
        "schedule_config_json, timezone, is_active, next_run_at, actor, "
        "created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            sched_id,
            workspace_id,
            channel_id,
            name,
            operation_type,
            schedule_type,
            json.dumps(schedule_config),
            timezone_name,
            1,
            next_run_iso,
            actor,
            now_iso,
            now_iso,
        ),
    )
    conn.commit()
    return get_schedule(conn, sched_id)


def get_schedule(conn: Any, schedule_id: str) -> ScheduleView:
    row = conn.execute(
        "SELECT * FROM app_schedule_definitions WHERE id=?", (schedule_id,)
    ).fetchone()
    if row is None:
        raise ScheduleNotFoundError(schedule_id)
    return _row_to_view(row)


def list_schedules(
    conn: Any,
    workspace_id: str,
    *,
    is_active: bool | None = None,
) -> list[ScheduleView]:
    sql = "SELECT * FROM app_schedule_definitions WHERE workspace_id=?"
    params: list[Any] = [workspace_id]
    if is_active is not None:
        sql += " AND is_active=?"
        params.append(1 if is_active else 0)
    sql += " ORDER BY created_at DESC"
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_view(r) for r in rows]


def pause_schedule(conn: Any, schedule_id: str, workspace_id: str) -> ScheduleView:
    row = conn.execute(
        "SELECT * FROM app_schedule_definitions WHERE id=? AND workspace_id=?",
        (schedule_id, workspace_id),
    ).fetchone()
    if row is None:
        raise ScheduleNotFoundError(schedule_id)
    conn.execute(
        "UPDATE app_schedule_definitions SET is_active=0, updated_at=? WHERE id=?",
        (_now_iso(), schedule_id),
    )
    conn.commit()
    return get_schedule(conn, schedule_id)


def resume_schedule(conn: Any, schedule_id: str, workspace_id: str) -> ScheduleView:
    row = conn.execute(
        "SELECT * FROM app_schedule_definitions WHERE id=? AND workspace_id=?",
        (schedule_id, workspace_id),
    ).fetchone()
    if row is None:
        raise ScheduleNotFoundError(schedule_id)
    now = datetime.now(UTC)
    config = json.loads(row["schedule_config_json"])
    next_run = _compute_next_run(row["schedule_type"], config, row["timezone"], now)
    conn.execute(
        "UPDATE app_schedule_definitions SET is_active=1, next_run_at=?, updated_at=? WHERE id=?",
        (next_run.isoformat() if next_run else None, now.isoformat(), schedule_id),
    )
    conn.commit()
    return get_schedule(conn, schedule_id)


def delete_schedule(conn: Any, schedule_id: str, workspace_id: str) -> None:
    row = conn.execute(
        "SELECT id FROM app_schedule_definitions WHERE id=? AND workspace_id=?",
        (schedule_id, workspace_id),
    ).fetchone()
    if row is None:
        raise ScheduleNotFoundError(schedule_id)
    conn.execute("DELETE FROM app_schedule_definitions WHERE id=?", (schedule_id,))
    conn.commit()


def record_run(conn: Any, schedule_id: str) -> ScheduleView:
    """Record that a schedule was triggered; advance next_run_at."""
    row = conn.execute(
        "SELECT * FROM app_schedule_definitions WHERE id=?", (schedule_id,)
    ).fetchone()
    if row is None:
        raise ScheduleNotFoundError(schedule_id)
    now = datetime.now(UTC)
    config = json.loads(row["schedule_config_json"])
    next_run = _compute_next_run(row["schedule_type"], config, row["timezone"], now)
    conn.execute(
        "UPDATE app_schedule_definitions SET last_run_at=?, next_run_at=?, updated_at=? WHERE id=?",
        (now.isoformat(), next_run.isoformat() if next_run else None, now.isoformat(), schedule_id),
    )
    conn.commit()
    return get_schedule(conn, schedule_id)


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------


def eligible_schedules(
    conn: Any,
    workspace_id: str,
    *,
    now: datetime | None = None,
) -> list[ScheduleView]:
    """Return active schedules whose next_run_at is in the past."""
    if now is None:
        now = datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    rows = conn.execute(
        "SELECT * FROM app_schedule_definitions "
        "WHERE workspace_id=? AND is_active=1 AND next_run_at IS NOT NULL "
        "ORDER BY next_run_at ASC",
        (workspace_id,),
    ).fetchall()
    result = []
    for row in rows:
        next_run = _parse_iso(row["next_run_at"])
        if next_run <= now:
            result.append(_row_to_view(row))
    return result


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------


def _row_to_view(row: Any) -> ScheduleView:
    raw_config = row["schedule_config_json"]
    config = json.loads(raw_config) if isinstance(raw_config, str) else raw_config
    return ScheduleView(
        id=row["id"],
        workspace_id=row["workspace_id"],
        channel_id=row["channel_id"],
        name=row["name"],
        operation_type=row["operation_type"],
        schedule_type=row["schedule_type"],
        schedule_config=config,
        timezone=row["timezone"],
        is_active=bool(row["is_active"]),
        last_run_at=row["last_run_at"],
        next_run_at=row["next_run_at"],
        actor=row["actor"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
