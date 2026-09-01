"""Scheduler — deterministic eligibility and schedule definitions.

No daemon. No cron installation. No distributed scheduler.
Computes due-operations at query time based on next_run_at timestamps.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
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
    commit: bool = True,
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
    if commit:
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


def pause_schedule(
    conn: Any, schedule_id: str, workspace_id: str, *, commit: bool = True
) -> ScheduleView:
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
    if commit:
        conn.commit()
    return get_schedule(conn, schedule_id)


def resume_schedule(
    conn: Any, schedule_id: str, workspace_id: str, *, commit: bool = True
) -> ScheduleView:
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
    if commit:
        conn.commit()
    return get_schedule(conn, schedule_id)


def delete_schedule(conn: Any, schedule_id: str, workspace_id: str, *, commit: bool = True) -> None:
    row = conn.execute(
        "SELECT id FROM app_schedule_definitions WHERE id=? AND workspace_id=?",
        (schedule_id, workspace_id),
    ).fetchone()
    if row is None:
        raise ScheduleNotFoundError(schedule_id)
    conn.execute("DELETE FROM app_schedule_definitions WHERE id=?", (schedule_id,))
    if commit:
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


@dataclass(frozen=True)
class ScheduleReconciliation:
    """One schedule's before/after next_run_at during a reconciliation pass."""

    schedule_id: str
    operation_type: str
    channel_id: str | None
    interval_seconds: int | None
    previous_next_run_at: str | None
    canonical_next_run_at: str | None
    repaired: bool
    reason: str


def _canonical_interval_seconds(conn: Any, row: Any, config: dict[str, Any]) -> int | None:
    """The interval this schedule is genuinely supposed to run at.

    For most operation types that is `interval_seconds` from the stored
    config. `analytics_observation` is the exception: its cadence is
    age-aware and is recomputed by the observer at the end of every tick
    (hourly for a fresh video, three-daily for an old one), so the stored
    `interval_seconds` is only the value it was registered with and is
    routinely, correctly, out of date. Reconciling those against the stored
    number would drag a mature publication back to an hourly poll.
    """
    if row["schedule_type"] != "interval":
        return None

    if row["operation_type"] == "analytics_observation":
        from app.analytics.observation import compute_observation_interval_seconds

        pub_id = config.get("publication_id")
        if pub_id is None:
            return None
        pub = conn.execute(
            "SELECT published_at FROM publications WHERE id = ?", (pub_id,)
        ).fetchone()
        return compute_observation_interval_seconds(pub["published_at"] if pub else None)

    raw = config.get("interval_seconds")
    return int(raw) if raw is not None else None


def reconcile_schedule_next_runs(
    conn: Any,
    *,
    workspace_id: str | None = None,
    channel_id: str | None = None,
    now: datetime | None = None,
    apply: bool = True,
) -> list[ScheduleReconciliation]:
    """Bring active schedules back onto their configured cadence.

    A schedule's `next_run_at` is persisted, so a period during which it was
    computed wrongly leaves rows that stay wrong long after the computation
    is fixed. That happened here: the worker read `schedule_config["seconds"]`
    while every row writes `interval_seconds`, so every interval schedule
    silently persisted a 24-hour next run. Fixing the computation does not
    fix the rows — an hourly decision cycle would still have waited out its
    stale daily timestamp before its first correct tick.

    This repairs those rows from the canonical definition rather than from a
    chosen timestamp: `next_run = last_run_at + canonical_interval`, using
    the same `_compute_next_run` the scheduler itself uses. A schedule that
    has never run anchors to `now`.

    Deliberately one-directional. A row is only rewritten when the persisted
    value is **later** than canonical — i.e. genuinely stale-too-far-out.
    Reconciliation can therefore never pull a schedule earlier than its own
    cadence permits, which is what keeps it from becoming a way to make
    things fire sooner. Rows already at or before canonical are left exactly
    as they are, which is also what makes the pass idempotent: after one
    run every row satisfies `stored <= canonical`, so a second run repairs
    nothing.

    An overdue result (canonical in the past) is preserved as-is rather than
    clamped to `now`: the row then honestly reads "this was due at X", and
    the scheduler picks it up on its next poll. Catch-up is bounded to a
    single tick because the dispatcher advances `next_run_at` before running.

    `apply=False` computes the same report without writing — used by tests
    and by operators who want to see what a pass would do.
    """
    if now is None:
        now = datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    sql = "SELECT * FROM app_schedule_definitions WHERE is_active = 1"
    params: list[Any] = []
    if workspace_id is not None:
        sql += " AND workspace_id = ?"
        params.append(workspace_id)
    if channel_id is not None:
        sql += " AND channel_id = ?"
        params.append(channel_id)
    sql += " ORDER BY operation_type"

    results: list[ScheduleReconciliation] = []
    for row in conn.execute(sql, params).fetchall():
        raw_config = row["schedule_config_json"]
        config = json.loads(raw_config) if isinstance(raw_config, str) else (raw_config or {})
        interval = _canonical_interval_seconds(conn, row, config)

        if interval is None:
            results.append(
                ScheduleReconciliation(
                    schedule_id=row["id"],
                    operation_type=row["operation_type"],
                    channel_id=row["channel_id"],
                    interval_seconds=None,
                    previous_next_run_at=row["next_run_at"],
                    canonical_next_run_at=row["next_run_at"],
                    repaired=False,
                    reason="not an interval schedule with a derivable cadence",
                )
            )
            continue

        anchor = _parse_iso(row["last_run_at"]) if row["last_run_at"] else now
        canonical = _compute_next_run(
            "interval", {"interval_seconds": interval}, row["timezone"], anchor
        )
        assert canonical is not None  # interval schedules always produce one

        stored_raw = row["next_run_at"]
        stored = _parse_iso(stored_raw) if stored_raw else None

        if stored is not None and stored <= canonical:
            results.append(
                ScheduleReconciliation(
                    schedule_id=row["id"],
                    operation_type=row["operation_type"],
                    channel_id=row["channel_id"],
                    interval_seconds=interval,
                    previous_next_run_at=stored_raw,
                    canonical_next_run_at=stored_raw,
                    repaired=False,
                    reason="already at or ahead of its configured cadence",
                )
            )
            continue

        canonical_str = canonical.strftime("%Y-%m-%dT%H:%M:%S")
        if apply:
            conn.execute(
                "UPDATE app_schedule_definitions SET next_run_at = ?, updated_at = ? WHERE id = ?",
                (canonical_str, now.strftime("%Y-%m-%dT%H:%M:%S"), row["id"]),
            )
        results.append(
            ScheduleReconciliation(
                schedule_id=row["id"],
                operation_type=row["operation_type"],
                channel_id=row["channel_id"],
                interval_seconds=interval,
                previous_next_run_at=stored_raw,
                canonical_next_run_at=canonical_str,
                repaired=True,
                reason=(
                    f"persisted next run was later than {interval}s after the last run"
                    if stored_raw
                    else "no next run was persisted"
                ),
            )
        )

    if apply and any(r.repaired for r in results):
        conn.commit()
    return results


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
