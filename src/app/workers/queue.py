"""RQ queue configuration with JSON-safe serialization.

Security invariants:
- Pickle serialization is DISABLED. All payloads use rq.serializers.JSONSerializer.
- Queue payloads contain only safe primitive identifiers:
    operation_id, pipeline_id, workspace_id, channel_id, platform_account_id,
    command_key, correlation_id, idempotency_key.
- No Python objects, repositories, provider clients, credentials, or
  ApplicationService instances are ever serialized into Redis.
"""

from __future__ import annotations

import os

import redis as redis_lib
from rq import Queue
from rq.serializers import JSONSerializer

_DEFAULT_QUEUE_NAME = "default"
_HIGH_PRIORITY_QUEUE_NAME = "high"
_LOW_PRIORITY_QUEUE_NAME = "low"

# All queues use the JSON serializer — pickle is never used.
_SERIALIZER = JSONSerializer


def get_redis_connection(redis_url: str | None = None) -> redis_lib.Redis:
    """Return a Redis connection from ACE_REDIS_URL or the provided URL."""
    url = redis_url or os.environ.get("ACE_REDIS_URL", "redis://localhost:6379/0")
    return redis_lib.from_url(url)


def get_queue(
    name: str = _DEFAULT_QUEUE_NAME,
    redis_url: str | None = None,
    connection: redis_lib.Redis | None = None,
) -> Queue:
    """Return an RQ Queue with JSON serialization enforced.

    Pass connection to reuse an existing Redis connection (recommended in tests).
    """
    conn = connection or get_redis_connection(redis_url)
    return Queue(name=name, connection=conn, serializer=_SERIALIZER)


def get_all_queues(
    redis_url: str | None = None,
    connection: redis_lib.Redis | None = None,
) -> dict[str, Queue]:
    """Return all application queues keyed by name."""
    conn = connection or get_redis_connection(redis_url)
    return {
        name: Queue(name=name, connection=conn, serializer=_SERIALIZER)
        for name in [_HIGH_PRIORITY_QUEUE_NAME, _DEFAULT_QUEUE_NAME, _LOW_PRIORITY_QUEUE_NAME]
    }
