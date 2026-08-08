"""Phase 6 M6.3A caption domain models.

Dataclasses are mutable pre-persistence drafts.
Pydantic BaseModels are frozen DB row projections.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import BaseModel

# ── In-memory draft types (pre-persistence) ───────────────────────────────────


@dataclass
class CaptionCueDraft:
    """A single in-memory caption cue before database insertion."""

    segment_id: int
    narration_asset_id: int
    narration_text_hash: str
    audio_sha256: str
    cue_index: int  # global index within the caption run
    segment_cue_index: int  # index within this segment's cue list
    lines: list[str]  # display lines (at most CAPTION_MAX_LINES_PER_CUE)
    start_ms: int  # segment-relative start, integer milliseconds
    end_ms: int  # segment-relative end, integer milliseconds
    timing_source: str  # 'estimated' in M6.3A
    warnings: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        """Display text: lines joined with newline."""
        return "\n".join(self.lines)

    @property
    def char_count(self) -> int:
        return sum(len(line) for line in self.lines)

    @property
    def line_count(self) -> int:
        return len(self.lines)

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


@dataclass
class CaptionRunDraft:
    """Complete caption run data assembled before persistence."""

    narration_run_id: int
    plan_id: int
    script_id: int
    topic_id: int
    experiment_id: str | None
    input_hash: str
    caption_schema_version: str
    segmentation_version: str
    timing_algorithm_version: str
    style_version: str
    exporter_version: str
    language: str
    cues: list[CaptionCueDraft] = field(default_factory=list)

    @property
    def total_cue_count(self) -> int:
        return len(self.cues)


# ── Pydantic DB models (frozen, row-mapped) ───────────────────────────────────


def _row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


class CaptionRun(BaseModel):
    model_config = {"frozen": True}

    id: int
    narration_run_id: int
    plan_id: int
    script_id: int
    topic_id: int
    experiment_id: str | None
    input_hash: str
    caption_schema_version: str
    segmentation_version: str
    timing_algorithm_version: str
    style_version: str
    exporter_version: str
    language: str
    status: str
    total_cue_count: int | None
    total_duration_ms: int | None
    failure_reason: str | None
    srt_path: str | None
    vtt_path: str | None
    json_path: str | None
    srt_sha256: str | None
    vtt_sha256: str | None
    json_sha256: str | None
    approved_at: datetime | None
    rejected_at: datetime | None
    superseded_at: datetime | None
    superseded_by_run_id: int | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> CaptionRun:
        return cls(**_row_to_dict(row))


class CaptionCue(BaseModel):
    model_config = {"frozen": True}

    id: int
    run_id: int
    segment_id: int
    narration_asset_id: int
    narration_text_hash: str
    audio_sha256: str
    cue_index: int
    segment_cue_index: int
    text: str
    start_ms: int
    end_ms: int
    line_count: int
    char_count: int
    timing_source: str
    warnings: list[str]
    superseded_at: datetime | None
    created_at: datetime

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> CaptionCue:
        d = _row_to_dict(row)
        warnings_raw = d.get("warnings_json") or "[]"
        d["warnings"] = json.loads(warnings_raw)
        del d["warnings_json"]
        return cls(**d)

    @property
    def lines(self) -> list[str]:
        """Reconstruct display lines from stored text (inverse of '\\n'.join(lines))."""
        return self.text.split("\n")

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


class CaptionReviewEvent(BaseModel):
    model_config = {"frozen": True}

    id: int
    run_id: int
    cue_id: int | None
    segment_id: int | None
    narration_asset_id: int | None
    narration_run_id: int
    plan_id: int
    script_id: int
    topic_id: int
    voice_profile_id: int
    provider: str
    model: str
    voice_id: str
    experiment_id: str | None
    caption_schema_version: str
    segmentation_version: str
    timing_algorithm_version: str
    style_version: str
    exporter_version: str
    event_type: str
    reason_code: str | None
    severity: int | None
    expected_correction: str | None
    notes: str | None
    actor: str | None
    created_at: datetime

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> CaptionReviewEvent:
        return cls(**_row_to_dict(row))
