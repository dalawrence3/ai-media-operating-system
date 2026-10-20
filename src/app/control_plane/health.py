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


_BAD_STATUSES = ("degraded", "unavailable", "credential_expired", "quota_limited", "failed")


def list_degraded_entities(conn: Any) -> list[HealthRecord]:
    """Entities whose CURRENT (latest) health record is non-healthy.

    Read-model fix: previously queried cp_health_records by bad status with
    no notion that a later 'healthy' record superseded an earlier bad one,
    so a fully-recovered entity's old incident stayed visible forever. Now
    reuses the same latest-per-entity semantics already proven for the
    Exception Center (Phase 18E.2) via repo.list_latest_health_records —
    an entity appears here at most once, only while its most recent record
    is still bad. History in cp_health_records is never touched.
    """
    return repo.list_latest_health_records(conn, statuses=_BAD_STATUSES, limit=200)
