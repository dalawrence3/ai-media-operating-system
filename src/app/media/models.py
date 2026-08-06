"""Phase 8 rendering engine domain models.

Dataclasses are mutable pre-persistence drafts or in-memory value objects.
Pydantic BaseModels are frozen DB row projections.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


# ── In-memory value objects ───────────────────────────────────────────────────


@dataclass
class ResolvedAsset:
    """A scene manifest asset with a resolved local file path."""

    asset_id: int
    scene_id: int
    asset_index: int
    category: str
    priority: str
    local_path: str | None       # None if not yet downloaded
    local_sha256: str | None     # populated after download
    source_url: str | None
    license_status: str
    commercial_safe: bool


@dataclass
class RenderSceneDraft:
    """Per-scene render data assembled before persistence."""

    scene_index: int
    scene_id: int                 # FK scene_manifest_scenes
    segment_id: int
    narration_asset_id: int | None
    audio_path: str | None        # resolved from ApprovedNarrationRun
    audio_sha256: str | None
    start_ms: int
    end_ms: int
    duration_ms: int
    shot_type: str
    camera_movement: str
    visual_objective: str
    caption_cue_ids: list[int]
    primary_asset: ResolvedAsset | None  # best resolved visual asset


@dataclass
class RenderManifestDraft:
    """Complete render manifest assembled before persistence."""

    scene_manifest_id: int
    narration_run_id: int
    caption_run_id: int
    topic_id: int
    plan_id: int
    script_id: int
    experiment_id: str | None
    input_hash: str
    render_schema_version: str
    compositor_version: str
    total_scene_count: int
    total_duration_ms: int
    width: int
    height: int
    fps: int
    caption_burn_in: bool
    scenes: list[RenderSceneDraft] = field(default_factory=list)

    @property
    def resolution_label(self) -> str:
        return f"{self.width}x{self.height}"


@dataclass
class RenderJobResult:
    """Result returned by a RenderBackend after a successful render."""

    output_path: str
    output_sha256: str
    duration_s: float
    file_size_bytes: int
    render_time_s: float
    ffmpeg_cmd: list[str]


# ── Pydantic DB row projections (frozen) ──────────────────────────────────────


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


from pydantic import BaseModel, ConfigDict  # noqa: E402


class RenderManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    scene_manifest_id: int
    narration_run_id: int
    caption_run_id: int
    topic_id: int
    plan_id: int
    script_id: int
    experiment_id: str | None
    input_hash: str
    render_schema_version: str
    compositor_version: str
    total_scene_count: int
    total_duration_ms: int
    width: int
    height: int
    fps: int
    caption_burn_in: bool
    status: str
    approved_at: datetime | None
    rejected_at: datetime | None
    superseded_at: datetime | None
    superseded_by_id: int | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> RenderManifest:
        d = _row_to_dict(row)
        d["caption_burn_in"] = bool(d["caption_burn_in"])
        return cls(**d)


class RenderJob(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    render_manifest_id: int
    backend: str
    backend_version: str
    output_path: str | None
    output_sha256: str | None
    duration_s: float | None
    file_size_bytes: int | None
    render_time_s: float | None
    width: int
    height: int
    fps: int
    video_codec: str
    audio_codec: str
    crf: int
    audio_bitrate: str
    caption_burn_in: bool
    ffmpeg_cmd: list[str] | None
    status: str
    error_message: str | None
    validated: bool
    validation_metadata: dict | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> RenderJob:
        d = _row_to_dict(row)
        d["caption_burn_in"] = bool(d["caption_burn_in"])
        d["validated"] = bool(d["validated"])
        d["ffmpeg_cmd"] = json.loads(d.pop("ffmpeg_cmd_json") or "null")
        d["validation_metadata"] = json.loads(d.pop("validation_metadata_json") or "null")
        return cls(**d)


class RenderReviewEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    render_manifest_id: int
    render_job_id: int | None
    topic_id: int
    plan_id: int
    script_id: int
    scene_manifest_id: int
    experiment_id: str | None
    render_schema_version: str
    event_type: str
    reason_code: str | None
    severity: int | None
    expected_correction: str | None
    notes: str | None
    actor: str | None
    created_at: datetime

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> RenderReviewEvent:
        return cls(**_row_to_dict(row))


class RenderThumbnail(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    render_job_id: int
    file_path: str
    timestamp_ms: int
    scene_index: int | None
    selected: bool
    created_at: datetime

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> RenderThumbnail:
        d = _row_to_dict(row)
        d["selected"] = bool(d["selected"])
        return cls(**d)


# ── Handoff object for Phase 9 (publishing) ───────────────────────────────────


@dataclass(frozen=True)
class ApprovedRender:
    """Frozen handoff from Phase 8 to Phase 9 (publishing engine)."""

    render_manifest_id: int
    render_job_id: int
    scene_manifest_id: int
    narration_run_id: int
    caption_run_id: int
    topic_id: int
    plan_id: int
    script_id: int
    experiment_id: str | None
    output_path: str
    output_sha256: str
    duration_s: float
    width: int
    height: int
    fps: int
    video_codec: str
    audio_codec: str
    approved_at: datetime
