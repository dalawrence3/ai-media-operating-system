"""Semantic visual engine — domain models.

Dataclasses are mutable pre-persistence drafts / in-memory value objects,
matching the convention used by app.scenes and app.media.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.visuals.constants import (
    FIT_COVER,
    LICENSE_UNKNOWN,
    MEDIA_GRAPHIC,
    MOTION_NONE,
    QA_PASS,
)

# ── Planning ─────────────────────────────────────────────────────────────────


@dataclass
class VisualBeat:
    """One semantic visual unit.

    A beat is strictly narrower than a scene: a scene owns the narration audio
    segment, a beat owns a stretch of the *visual* track inside it.  Beats tile
    their parent scene exactly, so audio lineage is never touched by visual
    re-planning.
    """

    beat_index: int  # global, ascending across the whole video
    scene_index: int  # parent scene
    scene_id: int | None
    segment_id: int
    start_ms: int  # absolute, video timeline
    end_ms: int
    duration_ms: int
    narration_text: str
    keywords: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    visual_intent: str = "entity"
    media_type_preferences: list[str] = field(default_factory=list)
    search_queries: list[str] = field(default_factory=list)
    avoid_terms: list[str] = field(default_factory=list)
    claim_ids: list[int] = field(default_factory=list)
    evidence_ids: list[int] = field(default_factory=list)
    preferred_motion: str = MOTION_NONE
    importance: float = 0.5
    confidence: float = 0.5

    @property
    def primary_query(self) -> str:
        return self.search_queries[0] if self.search_queries else ""


@dataclass
class VisualCandidate:
    """A retrievable or generatable visual, before selection.

    ``asset_key`` is the stable cross-video identity used by asset memory and
    by every repetition safeguard.  It must be provider-scoped, never
    query-scoped — query-scoped identity is what let a prior video's footage
    reappear under a new topic.
    """

    provider: str
    provider_asset_id: str
    media_type: str
    query: str
    source_url: str | None = None
    download_url: str | None = None
    local_path: str | None = None
    width: int | None = None
    height: int | None = None
    duration_s: float | None = None
    license_status: str = LICENSE_UNKNOWN
    license_name: str | None = None
    attribution_required: bool = False
    attribution_text: str | None = None
    commercial_safe: bool = False
    creator: str | None = None
    tags: list[str] = field(default_factory=list)
    title: str = ""
    provider_rank: int = 0  # 0-based position in the provider's own ordering
    cost_units: int = 0
    tier: int = 1

    @property
    def asset_key(self) -> str:
        return f"{self.provider}:{self.provider_asset_id}"


@dataclass
class ScoredCandidate:
    """A candidate with its deterministic relevance/quality score."""

    candidate: VisualCandidate
    score: float
    factors: dict[str, float] = field(default_factory=dict)
    rejected_reason: str | None = None

    @property
    def accepted(self) -> bool:
        return self.rejected_reason is None


@dataclass
class BeatResolution:
    """The visual decided upon for one beat."""

    beat: VisualBeat
    media_type: str = MEDIA_GRAPHIC
    local_path: str | None = None
    asset_key: str | None = None
    provider: str = "programmatic"
    source_url: str | None = None
    license_status: str = LICENSE_UNKNOWN
    license_name: str | None = None
    attribution_required: bool = False
    attribution_text: str | None = None
    commercial_safe: bool = True
    motion: str = MOTION_NONE
    fit_mode: str = FIT_COVER
    score: float = 0.0
    score_factors: dict[str, float] = field(default_factory=dict)
    fallback_reason: str | None = None
    candidates_considered: int = 0
    is_placeholder: bool = False
    # Provider-supplied descriptive terms, retained so the QA gate can judge
    # whether the finished video is visually varied or merely differently-keyed.
    descriptors: list[str] = field(default_factory=list)
    # True when this resolution came from the Phase 18E remediation pass rather
    # than the ordinary first pass. Reported, never used for scoring: a
    # remediated visual is an ordinary visual once it is on screen.
    remediated: bool = False

    @property
    def resolved(self) -> bool:
        return self.local_path is not None


@dataclass
class VisualPlan:
    """The complete visual decision set for one video."""

    scene_manifest_id: int
    topic_id: int
    experiment_id: str | None
    channel_key: str | None
    engine_version: str
    planner_version: str
    beats: list[VisualBeat] = field(default_factory=list)
    resolutions: list[BeatResolution] = field(default_factory=list)
    provider_calls: dict[str, int] = field(default_factory=dict)

    @property
    def total_duration_ms(self) -> int:
        return sum(b.duration_ms for b in self.beats)

    def resolutions_for_scene(self, scene_index: int) -> list[BeatResolution]:
        return [r for r in self.resolutions if r.beat.scene_index == scene_index]


# ── QA ───────────────────────────────────────────────────────────────────────


@dataclass
class QAFinding:
    code: str
    severity: str  # "info" | "warning" | "blocking"
    message: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class VisualQAReport:
    status: str = QA_PASS
    qa_version: str = ""
    beat_count: int = 0
    resolved_count: int = 0
    placeholder_count: int = 0
    distinct_asset_count: int = 0
    beats_per_minute: float = 0.0
    dominant_asset_key: str | None = None
    dominant_asset_ms: int = 0
    dominant_asset_share: float = 0.0
    low_confidence_count: int = 0
    media_type_distribution: dict[str, int] = field(default_factory=dict)
    provider_distribution: dict[str, int] = field(default_factory=dict)
    findings: list[QAFinding] = field(default_factory=list)

    @property
    def blocking(self) -> list[QAFinding]:
        return [f for f in self.findings if f.severity == "blocking"]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "qa_version": self.qa_version,
            "beat_count": self.beat_count,
            "resolved_count": self.resolved_count,
            "placeholder_count": self.placeholder_count,
            "distinct_asset_count": self.distinct_asset_count,
            "beats_per_minute": round(self.beats_per_minute, 3),
            "dominant_asset_key": self.dominant_asset_key,
            "dominant_asset_ms": self.dominant_asset_ms,
            "dominant_asset_share": round(self.dominant_asset_share, 4),
            "low_confidence_count": self.low_confidence_count,
            "media_type_distribution": dict(self.media_type_distribution),
            "provider_distribution": dict(self.provider_distribution),
            "findings": [
                {
                    "code": f.code,
                    "severity": f.severity,
                    "message": f.message,
                    "detail": f.detail,
                }
                for f in self.findings
            ],
        }


# ── Persisted row projections ────────────────────────────────────────────────


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


class VisualBeatRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    scene_manifest_id: int
    scene_id: int | None
    beat_index: int
    scene_index: int
    segment_id: int
    start_ms: int
    end_ms: int
    duration_ms: int
    narration_text: str
    keywords_json: str
    entities_json: str
    visual_intent: str
    media_type_preferences_json: str
    search_queries_json: str
    avoid_terms_json: str
    claim_ids_json: str
    preferred_motion: str
    importance: float
    confidence: float
    resolved_media_type: str | None
    resolved_provider: str | None
    resolved_asset_key: str | None
    resolved_local_path: str | None
    resolved_score: float | None
    resolved_motion: str | None
    license_status: str | None
    attribution_text: str | None
    fallback_reason: str | None
    engine_version: str
    planner_version: str
    created_at: datetime

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> VisualBeatRecord:
        return cls(**_row_to_dict(row))


class VisualAssetUsageRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    asset_key: str
    provider: str
    provider_asset_id: str
    media_type: str
    channel_key: str | None
    workspace_id: str | None
    topic_id: int | None
    experiment_id: str | None
    scene_manifest_id: int | None
    render_manifest_id: int | None
    publication_id: int | None
    beat_index: int | None
    scene_index: int | None
    duration_ms: int
    used_at: datetime

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> VisualAssetUsageRecord:
        return cls(**_row_to_dict(row))
