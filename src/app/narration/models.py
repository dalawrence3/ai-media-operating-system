"""Phase 6 M6.2: Narration domain models."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import BaseModel

# ── Creation dataclasses (mutable, used before DB insertion) ──────────────────


@dataclass
class VoiceProfileCreate:
    channel_id: int | None
    provider: str
    model: str
    voice_id: str
    name: str
    language: str
    speaking_rate: float
    style: str | None
    stability: float | None
    similarity_boost: float | None
    settings_json: str
    version: int = 1
    is_default: bool = False


@dataclass
class NarrationRunDraft:
    plan_id: int
    plan_input_hash: str
    voice_profile_id: int
    voice_profile_version: int
    language: str
    speaking_rate: float
    style: str | None
    stability: float | None
    similarity_boost: float | None
    settings_json: str
    output_format: str
    sample_rate_hz: int
    experiment_id: str | None = None
    notes: str | None = None


@dataclass
class NarrationSegmentAssetDraft:
    run_id: int
    segment_id: int
    narration_text_hash: str
    provider: str
    model: str
    voice_id: str
    voice_profile_id: int
    voice_profile_version: int
    language: str
    speaking_rate: float
    style: str | None
    stability: float | None
    similarity_boost: float | None
    settings_json_hash: str
    output_format: str
    sample_rate_hz: int
    input_hash: str


@dataclass
class AudioMetadata:
    duration_seconds: float
    sample_rate_hz: int
    num_channels: int
    num_frames: int
    sample_width: int


@dataclass
class NarrationRunResult:
    run_id: int
    assets: list[NarrationSegmentAsset] = field(default_factory=list)
    skipped_segment_ids: list[int] = field(default_factory=list)


# ── Pydantic DB models (frozen, row-mapped) ───────────────────────────────────


def _row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


class VoiceProfile(BaseModel):
    model_config = {"frozen": True}

    id: int
    channel_id: int | None
    provider: str
    model: str
    voice_id: str
    name: str
    language: str
    speaking_rate: float
    style: str | None
    stability: float | None
    similarity_boost: float | None
    settings_json: str
    version: int
    is_default: bool
    superseded_by_id: int | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> VoiceProfile:
        return cls(**_row_to_dict(row))


class NarrationRun(BaseModel):
    model_config = {"frozen": True}

    id: int
    plan_id: int
    plan_input_hash: str
    voice_profile_id: int
    voice_profile_version: int
    language: str
    speaking_rate: float
    style: str | None
    stability: float | None
    similarity_boost: float | None
    settings_json: str
    output_format: str
    sample_rate_hz: int
    input_hash: str
    status: str
    experiment_id: str | None
    notes: str | None
    error_message: str | None
    completed_at: datetime | None
    approved_at: datetime | None
    rejected_at: datetime | None
    superseded_at: datetime | None
    superseded_by_run_id: int | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> NarrationRun:
        return cls(**_row_to_dict(row))


class NarrationSegmentAsset(BaseModel):
    model_config = {"frozen": True}

    id: int
    run_id: int
    segment_id: int
    narration_text_hash: str
    provider: str
    model: str
    voice_id: str
    voice_profile_id: int
    voice_profile_version: int
    language: str
    speaking_rate: float
    style: str | None
    stability: float | None
    similarity_boost: float | None
    settings_json_hash: str
    output_format: str
    sample_rate_hz: int
    input_hash: str
    status: str
    audio_path: str | None
    audio_sha256: str | None
    duration_seconds: float | None
    characters_billed: int | None
    cost_usd: float | None
    superseded_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> NarrationSegmentAsset:
        return cls(**_row_to_dict(row))


class TtsCall(BaseModel):
    model_config = {"frozen": True}

    id: int
    run_id: int
    segment_id: int | None
    provider: str
    model: str
    voice_id: str
    input_characters: int
    characters_billed: int
    output_format: str
    sample_rate_hz: int
    duration_seconds: float | None
    cost_usd: float
    success: bool
    error_message: str | None
    latency_ms: int | None
    request_id: str | None
    provider_metadata_json: str | None
    narration_schema_version: str
    narration_algorithm_version: str
    called_at: datetime

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> TtsCall:
        return cls(**_row_to_dict(row))


class NarrationReviewEvent(BaseModel):
    model_config = {"frozen": True}

    id: int
    run_id: int
    plan_id: int
    script_id: int
    topic_id: int
    voice_profile_id: int
    provider: str
    model: str
    voice_id: str
    experiment_id: str | None
    segment_id: int | None
    asset_id: int | None
    replacement_asset_id: int | None
    event_type: str
    reason_code: str | None
    severity: int | None
    expected_correction: str | None
    notes: str | None
    actor: str | None
    created_at: datetime

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> NarrationReviewEvent:
        return cls(**_row_to_dict(row))


# ── Caption handoff models (frozen, caption pipeline reads these) ─────────────


@dataclass(frozen=True)
class ApprovedNarrationSegment:
    """One synthesized segment ready to be captioned."""

    asset_id: int
    segment_id: int
    narration_text: str
    narration_text_hash: str
    audio_sha256: str
    audio_path: str
    duration_ms: int
    provider: str
    model: str
    voice_id: str


@dataclass(frozen=True)
class ApprovedNarrationRun:
    """Full narration run handoff — everything the caption pipeline needs."""

    run_id: int
    plan_id: int
    script_id: int
    topic_id: int
    experiment_id: str | None
    input_hash: str
    voice_profile_id: int
    provider: str
    model: str
    voice_id: str
    language: str
    segments: tuple[ApprovedNarrationSegment, ...]
