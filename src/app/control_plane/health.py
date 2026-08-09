"""Health record management."""

from __future__ import annotations

import uuid
from typing import Any

from app.control_plane import repository as repo
from app.control_plane.models import HealthRecord, HealthRecordDraft


def record_health(
    conn: Any,
    *,
    entity_type: str,
    entity_id: str,
    status: str,
    recorded_by: str,
    detail: str | None = None,
) -> HealthRecord:
    draft = HealthRecordDraft(
        id=str(uuid.uuid4()),
        entity_type=entity_type,
        entity_id=entity_id,
        status=status,
        recorded_by=recorded_by,
        detail=detail,
    )
    return repo.create_health_record(conn, draft)


def get_health(conn: Any, entity_type: str, entity_id: str) -> HealthRecord | None:
    return repo.get_latest_health_record(conn, entity_type, entity_id)


def list_degraded_entities(conn: Any) -> list[HealthRecord]:
    non_healthy = []
    for status in ("degraded", "unavailable", "credential_expired", "quota_limited", "failed"):
        non_healthy.extend(repo.list_health_records(conn, status=status, limit=200))
    non_healthy.sort(key=lambda r: r.recorded_at, reverse=True)
    return non_healthy
