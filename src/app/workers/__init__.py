"""Background worker infrastructure (M15.4).

Architecture:
  - Redis is transport only; canonical job state lives in PostgreSQL/SQLite.
  - All queue payloads are JSON-safe primitives (IDs, strings).
  - Workers reload canonical operation state from DB before executing.
  - Idempotency, policy, budget, and concurrency checks happen inside the worker.
"""

from app.workers.jobs import JobPayload, enqueue_pipeline_stage
from app.workers.queue import get_queue, get_redis_connection

__all__ = [
    "get_queue",
    "get_redis_connection",
    "enqueue_pipeline_stage",
    "JobPayload",
]
