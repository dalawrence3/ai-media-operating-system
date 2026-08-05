"""Pydantic and dataclass models for Phase 6 M6.1 production plan generation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

# ---------------------------------------------------------------------------
# Internal draft types (pure Python; no DB IDs; output of build_production_plan)
# ---------------------------------------------------------------------------


@dataclass
class ProductionSegmentDraft:
    """One segment's data before persistence."""

    segment_index: int
    section_index: int
    section_type: str
    narration_text: str
    estimated_duration_s: int
    estimated_word_count: int
    citation_claim_ids: list[int] = field(default_factory=list)


@dataclass
class ProductionPlanDraft:
    """Complete plan data before persistence."""

    topic_id: int
    script_id: int
    script_version: int
    input_hash: str
    script_body_hash: str
    plan_schema_version: str
    renderer_version: str
    duration_algorithm_version: str
    title: str
    format: str
    total_estimated_duration_s: int
    total_word_count: int
    warnings: list[str]
    requires_evidence_review: bool
    evidence_hash: str
    generation_run_id: int | None
    experiment_id: str | None
    segments: list[ProductionSegmentDraft] = field(default_factory=list)


# ---------------------------------------------------------------------------
# DB models (constructed via from_row(); frozen for safety)
# ---------------------------------------------------------------------------


class ProductionPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    topic_id: int
    script_id: int
    script_version: int
    input_hash: str
    script_body_hash: str
    plan_schema_version: str
    renderer_version: str
    duration_algorithm_version: str
    title: str
    format: str
    total_estimated_duration_s: int
    total_word_count: int
    warnings: list[str]
    requires_evidence_review: bool
    evidence_hash: str
    generation_run_id: int | None
    experiment_id: str | None
    status: Literal["draft", "approved", "rejected"]
    approved_at: str | None
    superseded_at: str | None
    rejected_at: str | None
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: Any) -> ProductionPlan:
        warnings_raw = row["warnings_json"]
        return cls(
            id=row["id"],
            topic_id=row["topic_id"],
            script_id=row["script_id"],
            script_version=row["script_version"],
            input_hash=row["input_hash"],
            script_body_hash=row["script_body_hash"],
            plan_schema_version=row["plan_schema_version"],
            renderer_version=row["renderer_version"],
            duration_algorithm_version=row["duration_algorithm_version"],
            title=row["title"],
            format=row["format"],
            total_estimated_duration_s=row["total_estimated_duration_s"],
            total_word_count=row["total_word_count"],
            warnings=json.loads(warnings_raw) if warnings_raw else [],
            requires_evidence_review=bool(row["requires_evidence_review"]),
            evidence_hash=row["evidence_hash"],
            generation_run_id=row["generation_run_id"],
            experiment_id=row["experiment_id"],
            status=row["status"],
            approved_at=row["approved_at"],
            superseded_at=row["superseded_at"],
            rejected_at=row["rejected_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class ProductionSegment(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    plan_id: int
    segment_index: int
    section_index: int
    section_type: str
    narration_text: str
    estimated_duration_s: int
    estimated_word_count: int
    created_at: str

    @classmethod
    def from_row(cls, row: Any) -> ProductionSegment:
        return cls(
            id=row["id"],
            plan_id=row["plan_id"],
            segment_index=row["segment_index"],
            section_index=row["section_index"],
            section_type=row["section_type"],
            narration_text=row["narration_text"],
            estimated_duration_s=row["estimated_duration_s"],
            estimated_word_count=row["estimated_word_count"],
            created_at=row["created_at"],
        )


class ProductionSegmentCitation(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    segment_id: int
    claim_id: int
    citation_order: int
    created_at: str

    @classmethod
    def from_row(cls, row: Any) -> ProductionSegmentCitation:
        return cls(
            id=row["id"],
            segment_id=row["segment_id"],
            claim_id=row["claim_id"],
            citation_order=row["citation_order"],
            created_at=row["created_at"],
        )


class ProductionPlanReviewEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    plan_id: int
    topic_id: int
    script_id: int
    evidence_hash: str
    model: str | None
    prompt_hash: str | None
    experiment_id: str | None
    decision: Literal["approved", "rejected"]
    reason_code: str | None
    notes: str | None
    actor: str | None
    created_at: str

    @classmethod
    def from_row(cls, row: Any) -> ProductionPlanReviewEvent:
        return cls(
            id=row["id"],
            plan_id=row["plan_id"],
            topic_id=row["topic_id"],
            script_id=row["script_id"],
            evidence_hash=row["evidence_hash"],
            model=row["model"],
            prompt_hash=row["prompt_hash"],
            experiment_id=row["experiment_id"],
            decision=row["decision"],
            reason_code=row["reason_code"],
            notes=row["notes"],
            actor=row["actor"],
            created_at=row["created_at"],
        )


# ---------------------------------------------------------------------------
# M6.2 handoff models (fully hydrated; consumed by narration generation)
# ---------------------------------------------------------------------------


class ProductionSegmentWithCitations(BaseModel):
    model_config = ConfigDict(frozen=True)

    segment_id: int
    plan_id: int
    segment_index: int
    section_index: int
    section_type: str
    narration_text: str
    estimated_duration_s: int
    estimated_word_count: int
    citations: list[ProductionSegmentCitation]

    @property
    def citation_claim_ids(self) -> list[int]:
        return [c.claim_id for c in self.citations]


class ApprovedProductionPlan(BaseModel):
    """Frozen handoff object for M6.2 narration generation."""

    model_config = ConfigDict(frozen=True)

    plan_id: int
    topic_id: int
    script_id: int
    script_version: int
    input_hash: str
    script_body_hash: str
    plan_schema_version: str
    renderer_version: str
    duration_algorithm_version: str
    title: str
    format: str
    total_estimated_duration_s: int
    total_word_count: int
    warnings: list[str]
    requires_evidence_review: bool
    evidence_hash: str
    generation_run_id: int | None
    experiment_id: str | None
    approved_at: str
    segments: list[ProductionSegmentWithCitations]
