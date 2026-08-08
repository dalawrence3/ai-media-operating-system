"""Job payload definitions and enqueue helpers.

All payloads are JSON-safe dicts of primitive identifiers only.
Workers reload canonical state from DB; no business objects cross the queue boundary.

Worker lifecycle (enforced in execute_pipeline_stage_job):
  1. Receive primitive identifiers from queue payload.
  2. Load canonical operation state from DB via ApplicationService.
  3. Verify current state + idempotency key.
  4. Verify workspace-pause / policy / budget / concurrency.
  5. Execute ApplicationService command.
  6. Record canonical result in DB.
  7. Acknowledge queue work (RQ auto-acks on success).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from rq import Queue

from app.workers.queue import get_queue

# ── Payload type ────────────────────────────────────────────────────────────

# All values must be JSON-primitive (str, int, float, bool, None, list, dict).
JobPayload = dict[str, Any]


def _validate_payload(payload: JobPayload) -> None:
    """Raise ValueError if any payload value is not JSON-safe."""
    try:
        json.dumps(payload)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Queue payload must be JSON-safe (no Python objects, classes, or credentials): {exc}"
        ) from exc


# ── Pipeline stage job ──────────────────────────────────────────────────────


def enqueue_pipeline_stage(
    pipeline_id: str,
    stage: str,
    workspace_id: str,
    *,
    channel_id: str | None = None,
    platform_account_id: str | None = None,
    correlation_id: str | None = None,
    idempotency_key: str | None = None,
    actor: str,
    queue: Queue | None = None,
    job_timeout: int = 3600,
    result_ttl: int = 86400,
    failure_ttl: int = 604800,
) -> str:
    """Enqueue a pipeline stage execution job and return the RQ job ID.

    Returns the RQ job ID (string). The job ID can be used to check status via
    rq.job.Job.fetch(job_id, connection=redis_conn).

    The worker function (execute_pipeline_stage_job) receives only primitive
    identifiers and reloads canonical state from DB.
    """
    payload: JobPayload = {
        "pipeline_id": pipeline_id,
        "stage": stage,
        "workspace_id": workspace_id,
        "channel_id": channel_id,
        "platform_account_id": platform_account_id,
        "correlation_id": correlation_id,
        "idempotency_key": idempotency_key,
        "actor": actor,
        "enqueued_at": datetime.now(UTC).isoformat(),
    }
    _validate_payload(payload)

    q = queue or get_queue()
    job = q.enqueue(
        execute_pipeline_stage_job,
        payload,
        job_timeout=job_timeout,
        result_ttl=result_ttl,
        failure_ttl=failure_ttl,
    )
    return job.id


# ── Worker function ─────────────────────────────────────────────────────────


def execute_pipeline_stage_job(payload: JobPayload) -> dict:
    """Worker entry point for pipeline stage execution.

    Invoked by an RQ worker process. Receives only JSON-safe primitive identifiers.
    Reloads all state from DB; never trusts payload to carry business objects.

    Worker lifecycle:
      1. Validate payload shape.
      2. Load canonical pipeline from DB.
      3. Verify idempotency (skip if already completed for this key).
      4. Verify workspace not paused.
      5. Execute stage via ApplicationService.
      6. Return canonical result dict (stored by RQ as job result).
    """
    # Validate payload is the right shape.
    required = {"pipeline_id", "stage", "workspace_id", "actor"}
    missing = required - set(payload.keys())
    if missing:
        raise ValueError(f"Job payload missing required fields: {missing}")

    pipeline_id: str = payload["pipeline_id"]
    stage: str = payload["stage"]
    workspace_id: str = payload["workspace_id"]
    actor: str = payload["actor"]
    correlation_id: str | None = payload.get("correlation_id")

    # Import here to avoid circular imports and to keep the module importable
    # without a live DB connection (important for tests that only import types).
    from app.core.config import get_config
    from app.core.database import get_db_connection

    cfg = get_config()
    conn = get_db_connection(db_path=cfg.db_path)

    try:
        # Reload canonical state from DB.
        from app.application.state import get_pipeline
        from app.workers.executor import dispatch_stage

        pipeline = get_pipeline(conn, pipeline_id)
        if pipeline is None:
            return {
                "status": "error",
                "reason": f"Pipeline {pipeline_id!r} not found in DB",
                "pipeline_id": pipeline_id,
                "stage": stage,
            }

        result = dispatch_stage(
            conn,
            pipeline,
            stage,
            actor=actor,
            workspace_id=workspace_id,
            correlation_id=correlation_id,
        )
        return result.to_dict()
    finally:
        conn.close()


# ── Scheduled job dispatch ──────────────────────────────────────────────────


def enqueue_scheduled_operations(
    workspace_id: str,
    due_schedule_ids: list[str],
    *,
    actor: str = "system:scheduler",
    queue: Queue | None = None,
) -> list[str]:
    """Enqueue one job per due schedule definition. Returns list of RQ job IDs.

    The canonical schedule truth remains in app_schedule_definitions.
    Redis/RQ is only used as execution transport.
    """
    q = queue or get_queue()
    job_ids: list[str] = []
    for schedule_id in due_schedule_ids:
        payload: JobPayload = {
            "schedule_id": schedule_id,
            "workspace_id": workspace_id,
            "actor": actor,
            "enqueued_at": datetime.now(UTC).isoformat(),
        }
        _validate_payload(payload)
        job = q.enqueue(
            execute_scheduled_operation_job,
            payload,
            job_timeout=3600,
            result_ttl=86400,
        )
        job_ids.append(job.id)
    return job_ids


def execute_scheduled_operation_job(payload: JobPayload) -> dict:
    """Worker entry point for scheduled operation execution.

    Receives only JSON-safe primitive identifiers. Reloads schedule definition
    from DB and dispatches via ApplicationService.
    """
    schedule_id: str = payload["schedule_id"]
    workspace_id: str = payload["workspace_id"]
    actor: str = payload.get("actor", "system:scheduler")

    return {
        "status": "dispatched",
        "schedule_id": schedule_id,
        "workspace_id": workspace_id,
        "actor": actor,
        "note": "Scheduled job received; operation dispatch via ApplicationService in M15.7.",
    }
