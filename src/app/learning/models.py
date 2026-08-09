"""Phase 11 — Learning & Optimization Engine domain models.

Dataclasses = mutable pre-persistence drafts.
Pydantic BaseModels = frozen DB row projections.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from pydantic import BaseModel

from app.learning.constants import EVIDENCE_OBSERVATIONAL, STRENGTH_EXPLORATORY

# ── Evidence item (component of a recommendation's evidence_json) ─────────────


@dataclass
class EvidenceItem:
    """A single piece of evidence supporting a recommendation.

    Every recommendation must include at least one evidence item.
    Each item names the metric, the observed value, and why it matters.
    """

    metric_name: str  # canonical metric name from Phase 10
    observed_value: float  # the measured value
    comparison_value: float | None  # baseline or threshold being compared to
    period_type: str  # lifetime / daily / weekly / monthly
    period_key: str  # e.g. '2026-08', 'lifetime', '2026-W31'
    snapshot_ids: list[int]  # contributing analytics_snapshots IDs
    interpretation: str  # plain-language meaning of this evidence item

    def to_dict(self) -> dict:
        return {
            "metric_name": self.metric_name,
            "observed_value": self.observed_value,
            "comparison_value": self.comparison_value,
            "period_type": self.period_type,
            "period_key": self.period_key,
            "snapshot_ids": self.snapshot_ids,
            "interpretation": self.interpretation,
        }

    @classmethod
    def from_dict(cls, d: dict) -> EvidenceItem:
        return cls(
            metric_name=d["metric_name"],
            observed_value=d["observed_value"],
            comparison_value=d.get("comparison_value"),
            period_type=d["period_type"],
            period_key=d["period_key"],
            snapshot_ids=d.get("snapshot_ids", []),
            interpretation=d["interpretation"],
        )


# ── Generator result tracking ─────────────────────────────────────────────────


@dataclass
class GeneratorResult:
    """Outcome of running one recommendation generator."""

    generator_name: str
    status: str  # "succeeded" | "failed"
    recommendation_count: int
    error_message: str | None = None


@dataclass
class AllGeneratorResults:
    """Combined result from running all generators in a single pass."""

    drafts: list[RecommendationDraft]
    generator_results: list[GeneratorResult]


# ── Pre-persistence drafts (mutable) ─────────────────────────────────────────


@dataclass
class RecommendationDraft:
    """Everything needed to persist a single optimization recommendation."""

    learning_run_id: int
    topic_id: int
    publication_id: int | None

    domain: str
    subsystem: str
    measure: str

    title: str
    explanation: str  # WHY: what evidence produced this
    expected_improvement: str  # what we expect to improve

    evidence: list[EvidenceItem] = field(default_factory=list)
    confidence: str = "low"
    confidence_score: float = 0.0
    evidence_classification: str = EVIDENCE_OBSERVATIONAL
    recommendation_strength: str = STRENGTH_EXPLORATORY

    affected_subsystem: str = ""
    subsystem_entity_type: str = ""
    subsystem_entity_id: int | None = None

    experiment_id: str | None = None

    engine_version: str = ""
    schema_version: str = ""
    input_hash: str = ""


@dataclass
class ReviewEventDraft:
    """A human review event for an optimization recommendation."""

    recommendation_id: int
    topic_id: int
    event_type: str  # accepted / rejected / noted
    reviewer: str = ""
    notes: str = ""
    expected_outcome: str = ""
    input_hash: str = ""


# ── DB row projections (frozen Pydantic) ──────────────────────────────────────


class LearningRun(BaseModel):
    """Frozen projection of a learning_runs row."""

    model_config = {"frozen": True}

    id: int
    topic_id: int
    publication_id: int | None

    publication_count: int
    recommendation_count: int
    status: str

    engine_version: str
    schema_version: str
    input_hash: str

    error: str | None

    created_at: str
    completed_at: str | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> LearningRun:
        return cls(**dict(row))


class OptimizationRecommendation(BaseModel):
    """Frozen projection of an optimization_recommendations row."""

    model_config = {"frozen": True}

    id: int
    learning_run_id: int
    topic_id: int
    publication_id: int | None

    domain: str
    subsystem: str
    measure: str

    title: str
    explanation: str
    expected_improvement: str

    evidence_json: str
    evidence_classification: str
    recommendation_strength: str
    confidence: str
    confidence_score: float

    affected_subsystem: str
    subsystem_entity_type: str
    subsystem_entity_id: int | None

    experiment_id: str | None

    engine_version: str
    schema_version: str
    input_hash: str

    status: str
    superseded_at: str | None
    superseded_by_id: int | None

    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> OptimizationRecommendation:
        return cls(**dict(row))

    @property
    def evidence(self) -> list[EvidenceItem]:
        try:
            items = json.loads(self.evidence_json)
            return [EvidenceItem.from_dict(i) for i in items]
        except (json.JSONDecodeError, TypeError, KeyError):
            return []


class RecommendationReviewEvent(BaseModel):
    """Frozen projection of a recommendation_review_events row (append-only)."""

    model_config = {"frozen": True}

    id: int
    recommendation_id: int
    topic_id: int
    event_type: str
    reviewer: str
    notes: str
    expected_outcome: str
    input_hash: str
    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> RecommendationReviewEvent:
        return cls(**dict(row))


class LearningRunGeneratorResult(BaseModel):
    """Frozen projection of a learning_run_generator_results row."""

    model_config = {"frozen": True}

    id: int
    learning_run_id: int
    generator_name: str
    status: str
    recommendation_count: int
    error_message: str | None
    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> LearningRunGeneratorResult:
        return cls(**dict(row))


# ── Phase 12 handoff ──────────────────────────────────────────────────────────


class ReviewedRecommendationItem(BaseModel):
    """A single recommendation with its human-review history, for Phase 12.

    Fields are flat (not nested under OptimizationRecommendation) so Phase 12
    can consume the handoff without querying raw learning tables.
    """

    model_config = {"frozen": True}

    id: int
    domain: str
    subsystem: str
    measure: str
    title: str
    explanation: str
    expected_improvement: str
    recommendation_strength: str  # exploratory | actionable
    evidence_classification: str  # observational | controlled_experiment | …
    confidence: str
    confidence_score: float
    affected_subsystem: str
    subsystem_entity_type: str
    subsystem_entity_id: int | None
    experiment_id: str | None
    status: str  # pending | accepted | rejected | superseded
    input_hash: str
    created_at: str
    review_events: list[RecommendationReviewEvent]

    @classmethod
    def from_recommendation(
        cls,
        rec: OptimizationRecommendation,
        review_events: list[RecommendationReviewEvent],
    ) -> ReviewedRecommendationItem:
        return cls(
            id=rec.id,
            domain=rec.domain,
            subsystem=rec.subsystem,
            measure=rec.measure,
            title=rec.title,
            explanation=rec.explanation,
            expected_improvement=rec.expected_improvement,
            recommendation_strength=rec.recommendation_strength,
            evidence_classification=rec.evidence_classification,
            confidence=rec.confidence,
            confidence_score=rec.confidence_score,
            affected_subsystem=rec.affected_subsystem,
            subsystem_entity_type=rec.subsystem_entity_type,
            subsystem_entity_id=rec.subsystem_entity_id,
            experiment_id=rec.experiment_id,
            status=rec.status,
            input_hash=rec.input_hash,
            created_at=rec.created_at,
            review_events=review_events,
        )


class ReviewedOptimizationHandoff(BaseModel):
    """Frozen Phase 12 input: reviewed learning run results.

    Phase 12 MUST use this handoff as its sole input.  It must not query
    raw learning tables or infer which recommendations are approved.

    Scope:
    - This handoff does NOT apply recommendations automatically.
    - It only surfaces human-reviewed decisions for Phase 12 planning.
    - Upstream engine tables (scripts, narration, scenes, etc.) are untouched.

    Evidence classification note:
    - All Phase 11 recommendations are 'observational' — derived from Platform
      Analytics without experimental treatment/control semantics.
    - 'controlled_experiment' requires validated A/B experiment attribution,
      which Phase 11 does not implement.
    - The field is extensible; Phase 12+ may introduce richer classifications.

    Human-feedback integration scope:
    - Phase 11 consumes only AnalyticsHandoff (Phase 10 output).
    - Upstream human review signals (scripts, narration, scenes, rendering,
      publishing) are not yet consumed by Phase 11.
    - Future phases may enrich evidence_classification using those signals.
    """

    model_config = {"frozen": True}

    learning_run_id: int
    topic_id: int
    publication_id: int | None
    run_status: str  # completed | partial | failed

    accepted: list[ReviewedRecommendationItem]
    rejected: list[ReviewedRecommendationItem]
    pending: list[ReviewedRecommendationItem]

    generator_results: list[LearningRunGeneratorResult]

    engine_version: str
    schema_version: str
    algorithm_version: str
    handoff_created_at: str
