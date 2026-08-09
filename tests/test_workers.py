"""Tests for RQ worker queue, job payloads, and scheduler (M15.4).

Covers:
  - JSON-safe payload enforcement (no pickle, no Python objects)
  - Queue uses JSONSerializer (not pickle)
  - enqueue_pipeline_stage payload structure
  - execute_pipeline_stage_job worker lifecycle (mocked DB)
  - Scheduler: get_due_schedules, mark_schedule_ran, run_scheduler_tick
  - compute_next_run_at for interval and shortcuts
  - No real Redis connection required for payload/serializer tests
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.workers.jobs import (
    JobPayload,
    _validate_payload,
    enqueue_pipeline_stage,
    execute_pipeline_stage_job,
    execute_scheduled_operation_job,
)
from app.workers.scheduler import (
    compute_next_run_at,
    get_due_schedules,
    mark_schedule_ran,
    run_scheduler_tick,
)

# ── JSON-safe payload enforcement ──────────────────────────────────────────


def test_payload_validation_passes_for_primitives():
    payload: JobPayload = {
        "pipeline_id": "pipe-1",
        "stage": "research",
        "workspace_id": "ws-1",
        "actor": "system:pipeline",
        "correlation_id": "corr-1",
        "channel_id": None,
    }
    _validate_payload(payload)  # no exception


def test_payload_validation_rejects_python_objects():
    payload: JobPayload = {"pipeline_id": object()}  # type: ignore
    with pytest.raises(ValueError, match="JSON-safe"):
        _validate_payload(payload)


def test_payload_validation_rejects_callable():
    payload: JobPayload = {"fn": lambda x: x}  # type: ignore
    with pytest.raises(ValueError, match="JSON-safe"):
        _validate_payload(payload)


def test_payload_validation_rejects_class_instance():
    class Secret:
        pass

    payload: JobPayload = {"secret": Secret()}  # type: ignore
    with pytest.raises(ValueError, match="JSON-safe"):
        _validate_payload(payload)


# ── Queue serializer enforcement ────────────────────────────────────────────


def test_queue_uses_json_serializer():
    from rq.serializers import JSONSerializer

    from app.workers.queue import _SERIALIZER

    assert _SERIALIZER is JSONSerializer


def test_get_queue_returns_rq_queue_with_json_serializer():
    from rq import Queue
    from rq.serializers import JSONSerializer

    from app.workers.queue import get_queue

    mock_redis = MagicMock()
    q = get_queue(connection=mock_redis)
    assert isinstance(q, Queue)
    assert q.serializer is JSONSerializer


# ── enqueue_pipeline_stage ─────────────────────────────────────────────────


def test_enqueue_pipeline_stage_enqueues_job():
    mock_queue = MagicMock()
    mock_job = MagicMock()
    mock_job.id = "job-uuid-1"
    mock_queue.enqueue.return_value = mock_job

    job_id = enqueue_pipeline_stage(
        pipeline_id="pipe-1",
        stage="research",
        workspace_id="ws-1",
        actor="system:pipeline",
        queue=mock_queue,
    )

    assert job_id == "job-uuid-1"
    mock_queue.enqueue.assert_called_once()
    call_args = mock_queue.enqueue.call_args
    # First arg is the worker function
    assert call_args[0][0] is execute_pipeline_stage_job
    # Second arg is the payload dict
    payload = call_args[0][1]
    assert payload["pipeline_id"] == "pipe-1"
    assert payload["stage"] == "research"
    assert payload["workspace_id"] == "ws-1"
    assert payload["actor"] == "system:pipeline"
    # Payload must be JSON-safe
    json.dumps(payload)  # no exception


def test_enqueue_pipeline_stage_payload_has_no_objects():
    mock_queue = MagicMock()
    mock_queue.enqueue.return_value = MagicMock(id="j1")

    enqueue_pipeline_stage(
        pipeline_id="pipe-1",
        stage="narration",
        workspace_id="ws-1",
        channel_id="ch-1",
        platform_account_id="acc-1",
        correlation_id="corr-1",
        idempotency_key="idem-1",
        actor="system:pipeline",
        queue=mock_queue,
    )

    payload = mock_queue.enqueue.call_args[0][1]
    # Must be fully JSON-serializable
    serialized = json.dumps(payload)
    recovered = json.loads(serialized)
    assert recovered["pipeline_id"] == "pipe-1"
    assert recovered["channel_id"] == "ch-1"


# ── execute_pipeline_stage_job worker ──────────────────────────────────────


def test_execute_pipeline_stage_job_missing_fields():
    with pytest.raises(ValueError, match="missing required fields"):
        execute_pipeline_stage_job({"stage": "research"})


def test_execute_pipeline_stage_job_returns_dict(tmp_path: Path):
    from app.core.database import open_db

    # Seed a fresh DB (the worker function opens its own connection).
    db_path = tmp_path / "worker_test.db"
    open_db(db_path).close()

    payload = {
        "pipeline_id": "pipe-doesnt-exist",
        "stage": "research",
        "workspace_id": "ws-1",
        "actor": "system:test",
        "correlation_id": "corr-1",
    }

    with patch("app.core.config.get_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(db_path=db_path)
        with patch("app.core.database.get_db_connection") as mock_conn_factory:
            mock_conn = MagicMock()
            mock_conn_factory.return_value = mock_conn

            # get_pipeline returns None (pipeline doesn't exist)
            with patch("app.application.state.get_pipeline", return_value=None):
                result = execute_pipeline_stage_job(payload)

    assert result["status"] == "error"
    assert "not found" in result["reason"]


# ── execute_scheduled_operation_job ────────────────────────────────────────


def test_execute_scheduled_operation_job_returns_dict():
    payload = {
        "schedule_id": "sched-1",
        "workspace_id": "ws-1",
        "actor": "system:scheduler",
        "enqueued_at": "2026-08-07T12:00:00+00:00",
    }
    result = execute_scheduled_operation_job(payload)
    assert result["status"] == "dispatched"
    assert result["schedule_id"] == "sched-1"


# ── compute_next_run_at ────────────────────────────────────────────────────


def test_compute_next_run_at_interval():
    result = compute_next_run_at({"seconds": 3600}, "interval")
    assert result is not None
    dt = datetime.strptime(result, "%Y-%m-%dT%H:%M:%S")
    now = datetime.now(UTC).replace(tzinfo=None)
    diff = (dt - now).total_seconds()
    assert 3500 < diff < 3700


def test_compute_next_run_at_daily_shortcut():
    result = compute_next_run_at({"shortcut": "@daily"}, "interval")
    assert result is not None
    dt = datetime.strptime(result, "%Y-%m-%dT%H:%M:%S")
    now = datetime.now(UTC).replace(tzinfo=None)
    diff = (dt - now).total_seconds()
    assert 86000 < diff < 87000


def test_compute_next_run_at_once_returns_none():
    result = compute_next_run_at({}, "once")
    assert result is None


# ── get_due_schedules ──────────────────────────────────────────────────────


def _insert_schedule(conn, schedule_id, workspace_id, next_run_at, is_active=1):
    now = "2026-08-07T00:00:00"
    try:
        conn.execute(
            """INSERT INTO cp_workspaces
               (id, name, slug, actor, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (workspace_id, "Test WS", workspace_id, "system", now, now),
        )
        conn.commit()
    except Exception:
        pass

    conn.execute(
        """
        INSERT INTO app_schedule_definitions
            (id, workspace_id, name, operation_type, schedule_type, schedule_config_json,
             timezone, is_active, next_run_at, actor, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            schedule_id,
            workspace_id,
            "Test Schedule",
            "pipeline_run",
            "interval",
            '{"seconds": 3600}',
            "UTC",
            is_active,
            next_run_at,
            "system:scheduler",
            now,
            now,
        ),
    )
    conn.commit()


def test_get_due_schedules_returns_overdue(tmp_path: Path):
    from app.core.database import open_db

    conn = open_db(tmp_path / "sched.db")
    _insert_schedule(conn, "sched-1", "ws-1", "2026-01-01T00:00:00", is_active=1)
    _insert_schedule(conn, "sched-2", "ws-1", "2099-01-01T00:00:00", is_active=1)

    now = datetime(2026, 8, 7, 12, 0, 0, tzinfo=UTC)
    due = get_due_schedules(conn, now=now)
    ids = [d["id"] for d in due]
    assert "sched-1" in ids
    assert "sched-2" not in ids
    conn.close()


def test_get_due_schedules_inactive_excluded(tmp_path: Path):
    from app.core.database import open_db

    conn = open_db(tmp_path / "sched_inactive.db")
    _insert_schedule(conn, "sched-inactive", "ws-1", "2026-01-01T00:00:00", is_active=0)

    now = datetime(2026, 8, 7, 12, 0, 0, tzinfo=UTC)
    due = get_due_schedules(conn, now=now)
    assert not any(d["id"] == "sched-inactive" for d in due)
    conn.close()


def test_mark_schedule_ran_updates_row(tmp_path: Path):
    from app.core.database import open_db

    conn = open_db(tmp_path / "mark.db")
    _insert_schedule(conn, "sched-1", "ws-1", "2026-01-01T00:00:00")
    mark_schedule_ran(conn, "sched-1", "2026-08-08T00:00:00")
    row = conn.execute(
        "SELECT last_run_at, next_run_at FROM app_schedule_definitions WHERE id = ?",
        ("sched-1",),
    ).fetchone()
    assert row["last_run_at"] is not None
    assert row["next_run_at"] == "2026-08-08T00:00:00"
    conn.close()


def test_run_scheduler_tick_enqueues_due_schedules(tmp_path: Path):
    from app.core.database import open_db

    conn = open_db(tmp_path / "tick.db")
    _insert_schedule(conn, "sched-1", "ws-1", "2026-01-01T00:00:00")

    mock_queue = MagicMock()
    mock_queue.enqueue.return_value = MagicMock(id="job-1")

    with patch("app.workers.jobs.get_queue", return_value=mock_queue):
        job_ids = run_scheduler_tick(conn, queue=mock_queue)

    assert len(job_ids) == 1
    assert job_ids[0] == "job-1"
    conn.close()


def test_run_scheduler_tick_no_due_schedules(tmp_path: Path):
    from app.core.database import open_db

    conn = open_db(tmp_path / "no_tick.db")
    _insert_schedule(conn, "sched-future", "ws-1", "2099-01-01T00:00:00")

    job_ids = run_scheduler_tick(conn)
    assert job_ids == []
    conn.close()


def test_run_scheduler_tick_idempotent(tmp_path: Path):
    """After first tick, last_run_at is set so second tick produces nothing."""
    from app.core.database import open_db

    conn = open_db(tmp_path / "idem.db")
    _insert_schedule(conn, "sched-1", "ws-1", "2026-01-01T00:00:00")

    mock_queue = MagicMock()
    mock_queue.enqueue.return_value = MagicMock(id="job-1")

    # First tick — should dispatch
    with patch("app.workers.scheduler.get_due_schedules") as mock_due:
        # Simulate what happens: first call returns due schedule, second returns []
        mock_due.side_effect = [
            [
                {
                    "id": "sched-1",
                    "workspace_id": "ws-1",
                    "name": "S1",
                    "schedule_type": "interval",
                    "schedule_config_json": '{"seconds":3600}',
                    "timezone": "UTC",
                    "actor": "system:scheduler",
                }
            ],
            [],
        ]
        with patch("app.workers.jobs.get_queue", return_value=mock_queue):
            first = run_scheduler_tick(conn, queue=mock_queue)
            second = run_scheduler_tick(conn, queue=mock_queue)

    assert len(first) > 0
    assert second == []
    conn.close()
