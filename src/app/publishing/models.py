"""Phase 9 publishing domain models.

Dataclasses = mutable pre-persistence drafts.
Pydantic BaseModels = frozen DB row projections.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from pydantic import BaseModel

# ── Pre-persistence drafts (mutable) ─────────────────────────────────────────


@dataclass
class PublishingMetadataDraft:
    """All metadata fields for a publishing plan before persistence."""

    title: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    language: str = "en"
    category: str | None = None
    visibility: str = "private"
    made_for_kids: bool = False
    playlist_id: str | None = None
    thumbnail_path: str | None = None
    captions_path: str | None = None
    copyright_notice: str | None = None
    licensing_notes: str | None = None
    publication_notes: str | None = None


@dataclass
class PublishingScheduleDraft:
    """Scheduling intent for a publishing plan."""

    schedule_type: str = "immediate"
    scheduled_at: str | None = None  # ISO 8601 UTC string
    timezone: str | None = None  # IANA tz name, e.g. "America/New_York"


# ── DB row projections (frozen Pydantic) ──────────────────────────────────────


class PublishingPlan(BaseModel):
    """Frozen projection of a publishing_plans row."""

    model_config = {"frozen": True}

    id: int
    render_manifest_id: int
    render_job_id: int | None
    topic_id: int
    production_plan_id: int
    script_id: int
    scene_manifest_id: int
    narration_run_id: int
    caption_run_id: int
    experiment_id: str | None
    input_hash: str
    publishing_engine_version: str
    metadata_version: str
    provider: str
    provider_version: str
    title: str
    description: str
    tags_json: str
    language: str
    category: str | None
    visibility: str
    made_for_kids: int
    playlist_id: str | None
    thumbnail_path: str | None
    captions_path: str | None
    copyright_notice: str | None
    licensing_notes: str | None
    publication_notes: str | None
    schedule_type: str
    scheduled_at: str | None
    timezone: str | None
    status: str
    approved_at: str | None
    rejected_at: str | None
    rejection_reason: str | None
    superseded_at: str | None
    superseded_by_id: int | None
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> PublishingPlan:
        return cls(**dict(row))

    @property
    def tags(self) -> list[str]:
        try:
            return json.loads(self.tags_json)
        except (json.JSONDecodeError, TypeError):
            return []

    @property
    def is_superseded(self) -> bool:
        return self.superseded_at is not None


class PublishingJob(BaseModel):
    """Frozen projection of a publishing_jobs row."""

    model_config = {"frozen": True}

    id: int
    publishing_plan_id: int
    attempt_number: int
    provider: str
    provider_version: str
    status: str
    error_message: str | None
    retry_count: int
    retry_after: str | None
    started_at: str | None
    completed_at: str | None
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> PublishingJob:
        return cls(**dict(row))

    @property
    def is_terminal(self) -> bool:
        return self.status in {"completed", "failed", "cancelled"}

    @property
    def is_active(self) -> bool:
        return self.status in {"queued", "running", "retry_scheduled"}


class Publication(BaseModel):
    """Frozen projection of a publications row."""

    model_config = {"frozen": True}

    id: int
    publishing_plan_id: int
    publishing_job_id: int
    provider: str
    provider_version: str
    provider_video_id: str
    provider_url: str | None
    provider_status_json: str
    status: str
    error_message: str | None
    visibility: str
    scheduled_at: str | None
    published_at: str | None
    deleted_at: str | None
    publishing_engine_version: str
    input_hash: str
    output_sha256: str
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Publication:
        return cls(**dict(row))

    @property
    def provider_status(self) -> dict:
        try:
            return json.loads(self.provider_status_json)
        except (json.JSONDecodeError, TypeError):
            return {}

    @property
    def is_live(self) -> bool:
        return self.status == "published" and self.deleted_at is None


class PublishingReviewEvent(BaseModel):
    """Frozen projection of a publishing_review_events row (append-only)."""

    model_config = {"frozen": True}

    id: int
    publishing_plan_id: int
    publishing_job_id: int | None
    publication_id: int | None
    topic_id: int
    production_plan_id: int
    script_id: int
    render_manifest_id: int
    provider: str
    experiment_id: str | None
    event_type: str
    reason_code: str | None
    severity: int | None
    notes: str | None
    actor: str | None
    expected_correction: str | None
    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> PublishingReviewEvent:
        return cls(**dict(row))
