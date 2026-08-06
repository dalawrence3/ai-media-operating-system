"""Phase 7 scene-planning domain models.

Dataclasses are mutable pre-persistence drafts.
Pydantic BaseModels are frozen DB row projections.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import BaseModel, ConfigDict

# ── Pre-persistence draft types ───────────────────────────────────────────────


@dataclass
class PlannedAssetDraft:
    """An asset recommendation before database insertion."""

    scene_index: int  # parent scene index (resolved to scene_id after insert)
    asset_index: int  # order within the scene
    category: str  # AssetCategory constant
    priority: str  # AssetPriority constant
    description: str  # human-readable description of what to find
    search_query: str | None
    provider: str | None
    source_url: str | None
    license_status: str  # LicenseStatus constant
    license_name: str | None
    attribution_required: bool
    attribution_text: str | None
    commercial_safe: bool
    verification_status: str
    usage_rights: dict  # free-form dict persisted as JSON
    ai_generation_requested: bool
    ai_generation_prompt: str | None
    ai_generation_model: str | None
    claim_ids: list[int] = field(default_factory=list)
    evidence_ids: list[int] = field(default_factory=list)


@dataclass
class PlannedSceneDraft:
    """A single planned scene before database insertion."""

    scene_index: int
    segment_id: int
    narration_asset_id: int | None
    caption_cue_ids: list[int]
    claim_ids: list[int]
    evidence_ids: list[int]
    script_section_index: int | None
    narration_text: str
    start_ms: int
    end_ms: int
    duration_ms: int
    shot_type: str
    camera_movement: str
    transition_in: str
    transition_out: str
    visual_objective: str
    visual_rationale: str
    confidence: float
    assets: list[PlannedAssetDraft] = field(default_factory=list)


@dataclass
class SceneManifestDraft:
    """Complete manifest data assembled before persistence."""

    caption_run_id: int
    narration_run_id: int
    plan_id: int
    script_id: int
    topic_id: int
    experiment_id: str | None
    input_hash: str
    manifest_schema_version: str
    planner_version: str
    scenes: list[PlannedSceneDraft] = field(default_factory=list)

    @property
    def total_scene_count(self) -> int:
        return len(self.scenes)

    @property
    def total_asset_count(self) -> int:
        return sum(len(s.assets) for s in self.scenes)

    @property
    def total_duration_ms(self) -> int:
        return sum(s.duration_ms for s in self.scenes)


# ── Pydantic DB models (frozen, row-mapped) ───────────────────────────────────


def _row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


class SceneManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    caption_run_id: int
    narration_run_id: int
    plan_id: int
    script_id: int
    topic_id: int
    experiment_id: str | None
    input_hash: str
    manifest_schema_version: str
    planner_version: str
    status: str
    total_scene_count: int
    total_asset_count: int
    total_duration_ms: int
    approved_at: datetime | None
    rejected_at: datetime | None
    superseded_at: datetime | None
    superseded_by_manifest_id: int | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> SceneManifest:
        return cls(**_row_to_dict(row))


class SceneManifestScene(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    manifest_id: int
    scene_index: int
    segment_id: int
    narration_asset_id: int | None
    caption_cue_ids: list[int]
    claim_ids: list[int]
    evidence_ids: list[int]
    script_section_index: int | None
    narration_text: str
    start_ms: int
    end_ms: int
    duration_ms: int
    shot_type: str
    camera_movement: str
    transition_in: str
    transition_out: str
    visual_objective: str
    visual_rationale: str
    confidence: float
    created_at: datetime

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> SceneManifestScene:
        d = _row_to_dict(row)
        d["caption_cue_ids"] = json.loads(d.pop("caption_cue_ids_json", "[]") or "[]")
        d["claim_ids"] = json.loads(d.pop("claim_ids_json", "[]") or "[]")
        d["evidence_ids"] = json.loads(d.pop("evidence_ids_json", "[]") or "[]")
        return cls(**d)


class SceneManifestAsset(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    scene_id: int
    manifest_id: int
    asset_index: int
    category: str
    priority: str
    description: str
    search_query: str | None
    provider: str | None
    source_url: str | None
    license_status: str
    license_name: str | None
    attribution_required: bool
    attribution_text: str | None
    commercial_safe: bool
    verification_status: str
    usage_rights: dict
    ai_generation_requested: bool
    ai_generation_prompt: str | None
    ai_generation_model: str | None
    claim_ids: list[int]
    evidence_ids: list[int]
    created_at: datetime

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> SceneManifestAsset:
        d = _row_to_dict(row)
        d["usage_rights"] = json.loads(d.pop("usage_rights_json", "{}") or "{}")
        d["attribution_required"] = bool(d["attribution_required"])
        d["commercial_safe"] = bool(d["commercial_safe"])
        d["ai_generation_requested"] = bool(d["ai_generation_requested"])
        d["claim_ids"] = json.loads(d.pop("claim_ids_json", "[]") or "[]")
        d["evidence_ids"] = json.loads(d.pop("evidence_ids_json", "[]") or "[]")
        return cls(**d)


class SceneManifestReviewEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    manifest_id: int
    scene_id: int | None
    topic_id: int
    plan_id: int
    script_id: int
    caption_run_id: int
    narration_run_id: int
    experiment_id: str | None
    manifest_schema_version: str
    event_type: str
    reason_code: str | None
    severity: int | None
    expected_correction: str | None
    notes: str | None
    actor: str | None
    created_at: datetime

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> SceneManifestReviewEvent:
        return cls(**_row_to_dict(row))


# ── Hydrated handoff object (for future rendering) ────────────────────────────


@dataclass(frozen=True)
class ApprovedSceneScene:
    """One fully resolved scene in the approved manifest."""

    scene_id: int
    manifest_id: int
    scene_index: int
    segment_id: int
    narration_asset_id: int | None
    caption_cue_ids: list[int]
    claim_ids: list[int]
    evidence_ids: list[int]
    script_section_index: int | None
    narration_text: str
    start_ms: int
    end_ms: int
    duration_ms: int
    shot_type: str
    camera_movement: str
    transition_in: str
    transition_out: str
    visual_objective: str
    visual_rationale: str
    confidence: float
    assets: list[SceneManifestAsset]


@dataclass(frozen=True)
class ApprovedSceneManifest:
    """Frozen handoff for future rendering engines."""

    manifest_id: int
    caption_run_id: int
    narration_run_id: int
    plan_id: int
    script_id: int
    topic_id: int
    experiment_id: str | None
    input_hash: str
    manifest_schema_version: str
    planner_version: str
    approved_at: datetime
    scenes: list[ApprovedSceneScene]

    @property
    def total_scene_count(self) -> int:
        return len(self.scenes)

    @property
    def total_duration_ms(self) -> int:
        return sum(s.duration_ms for s in self.scenes)
