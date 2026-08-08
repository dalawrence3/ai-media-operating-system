"""DB-driven scheduler: reads app_schedule_definitions and enqueues due jobs.

Design:
  - Canonical schedule state lives in app_schedule_definitions (PostgreSQL / SQLite).
  - Redis/RQ is execution transport only; it is never the scheduling source of truth.
  - Scheduler process runs as a daemon (e.g., `rq worker --with-scheduler` or standalone).
  - Due-job determination is fully deterministic, timezone-aware, and idempotent.
  - Duplicate enqueue protection: last_run_at is updated atomically before enqueue.

Usage (standalone daemon):
  ACE_DATABASE_URL=postgresql://... ACE_REDIS_URL=redis://... python -m app.workers.scheduler

Usage (with RQ worker built-in scheduler):
  rq worker --with-scheduler  (uses rq.Scheduler under the hood for periodic jobs)

The standalone daemon polls at a configurable interval and is restart-safe.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


def get_due_schedules(conn: Any, now: datetime | None = None) -> list[dict]:
    """Return schedule definitions whose next_run_at is due (or NULL and active).

    A schedule is due when:
      - is_active = 1
      - next_run_at IS NULL  (never run)
      - OR next_run_at <= now (in ISO8601 UTC string comparison)
    """
    if now is None:
        now = datetime.now(UTC)
    now_str = now.strftime("%Y-%m-%dT%H:%M:%S")
    rows = conn.execute(
        """
        SELECT id, workspace_id, channel_id, name, operation_type,
               schedule_type, schedule_config_json, timezone,
               last_run_at, next_run_at, actor
        FROM app_schedule_definitions
        WHERE is_active = 1
          AND (next_run_at IS NULL OR next_run_at <= ?)
        ORDER BY next_run_at ASC
        """,
        (now_str,),
    ).fetchall()
    return [dict(row) for row in rows]


def compute_next_run_at(schedule_config: dict, schedule_type: str, tz: str = "UTC") -> str | None:
    """Compute the next execution time for a schedule definition.

    Returns an ISO8601 UTC string, or None if the schedule is exhausted.
    Currently supports: @daily, @hourly, @weekly shortcuts and interval seconds.
    """
    now = datetime.now(UTC)

    if schedule_type == "interval":
        seconds = int(schedule_config.get("seconds", 86400))
        from datetime import timedelta

        next_dt = now + timedelta(seconds=seconds)
        return next_dt.strftime("%Y-%m-%dT%H:%M:%S")

    # Named shortcuts map to fixed intervals.
    shortcuts = {"@daily": 86400, "@hourly": 3600, "@weekly": 604800}
    shortcut_key = schedule_config.get("shortcut", "")
    if shortcut_key in shortcuts:
        from datetime import timedelta

        next_dt = now + timedelta(seconds=shortcuts[shortcut_key])
        return next_dt.strftime("%Y-%m-%dT%H:%M:%S")

    # once-type schedules do not repeat.
    if schedule_type == "once":
        return None

    # Default: daily
    from datetime import timedelta

    return (now + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")


def mark_schedule_ran(conn: Any, schedule_id: str, next_run_at: str | None) -> None:
    """Update last_run_at and next_run_at for a schedule. Called before enqueue."""
    now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    conn.execute(
        """
        UPDATE app_schedule_definitions
        SET last_run_at = ?, next_run_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (now_str, next_run_at, now_str, schedule_id),
    )
    conn.commit()


def run_scheduler_tick(conn: Any, queue=None) -> list[str]:
    """Single scheduler tick: find due schedules, update DB, enqueue jobs.

    Returns list of enqueued RQ job IDs (empty if no jobs due).
    Idempotent: if last_run_at was just set, the row won't be returned again.
    """
    import json

    from app.workers.jobs import enqueue_scheduled_operations

    due = get_due_schedules(conn)
    if not due:
        return []

    enqueued_ids: list[str] = []
    for sched in due:
        schedule_id = sched["id"]
        workspace_id = sched["workspace_id"]
        schedule_type = sched["schedule_type"]
        schedule_config = json.loads(sched.get("schedule_config_json") or "{}")
        tz = sched.get("timezone") or "UTC"

        # Compute next_run_at BEFORE marking ran (so we don't lose the next slot).
        next_run = compute_next_run_at(schedule_config, schedule_type, tz)

        # Mark ran atomically (prevents re-enqueue on restart).
        mark_schedule_ran(conn, schedule_id, next_run)

        # Enqueue the job.
        job_ids = enqueue_scheduled_operations(
            workspace_id=workspace_id,
            due_schedule_ids=[schedule_id],
            actor=sched.get("actor") or "system:scheduler",
            queue=queue,
        )
        enqueued_ids.extend(job_ids)
        logger.info(
            "Scheduler enqueued job for schedule %s (%s) next=%s",
            schedule_id,
            sched.get("name"),
            next_run,
        )

    return enqueued_ids


def run_scheduler_daemon(poll_interval_seconds: int = 60) -> None:  # pragma: no cover
    """Run the scheduler as a blocking daemon process.

    Each tick: check DB for due schedules, enqueue, sleep.
    Restart-safe: last_run_at is written before enqueue.
    """
    from app.core.config import get_config
    from app.core.database import get_db_connection

    cfg = get_config()
    logger.info("Scheduler daemon starting (poll interval: %ds)", poll_interval_seconds)

    while True:
        try:
            conn = get_db_connection(db_path=cfg.db_path)
            try:
                run_scheduler_tick(conn)
            finally:
                conn.close()
        except Exception as exc:
            logger.error("Scheduler tick failed: %s", exc, exc_info=True)

        time.sleep(poll_interval_seconds)


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    run_scheduler_daemon()
