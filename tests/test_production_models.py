"""Tests for Phase 6 M6.1 production plan Pydantic and dataclass models."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from app.production.models import (
    ApprovedProductionPlan,
    ProductionPlan,
    ProductionPlanDraft,
    ProductionPlanReviewEvent,
    ProductionSegment,
    ProductionSegmentCitation,
    ProductionSegmentDraft,
    ProductionSegmentWithCitations,
)

# ---------------------------------------------------------------------------
# Draft dataclasses
# ---------------------------------------------------------------------------


def test_production_segment_draft_fields() -> None:
    seg = ProductionSegmentDraft(
        segment_index=0,
        section_index=0,
        section_type="hook",
        narration_text="Hello world.",
        estimated_duration_s=1,
        estimated_word_count=2,
        citation_claim_ids=[1, 2],
    )
    assert seg.segment_index == 0
    assert seg.section_type == "hook"
    assert seg.narration_text == "Hello world."
    assert seg.citation_claim_ids == [1, 2]


def test_production_segment_draft_default_citations() -> None:
    seg = ProductionSegmentDraft(
        segment_index=1,
        section_index=1,
        section_type="body",
        narration_text="Body text.",
        estimated_duration_s=2,
        estimated_word_count=2,
    )
    assert seg.citation_claim_ids == []


def test_production_plan_draft_fields() -> None:
    segs = [
        ProductionSegmentDraft(0, 0, "hook", "Hook.", 2, 1, []),
        ProductionSegmentDraft(1, 1, "body", "Body.", 4, 1, [3]),
    ]
    draft = ProductionPlanDraft(
        topic_id=1,
        script_id=2,
        script_version=1,
        input_hash="abc" * 20 + "ab",
        script_body_hash="def" * 20 + "de",
        plan_schema_version="ProductionPlan-v1",
        renderer_version="production-renderer-v1",
        duration_algorithm_version="duration-150wpm-v1",
        title="My Script",
        format="short",
        total_estimated_duration_s=6,
        total_word_count=2,
        warnings=[],
        requires_evidence_review=False,
        evidence_hash="ev" * 32,
        generation_run_id=5,
        experiment_id=None,
        segments=segs,
    )
    assert draft.topic_id == 1
    assert draft.script_id == 2
    assert draft.title == "My Script"
    assert len(draft.segments) == 2
    assert draft.experiment_id is None


# ---------------------------------------------------------------------------
# ProductionPlan.from_row
# ---------------------------------------------------------------------------


def _make_plan_row(**overrides) -> MagicMock:
    row = MagicMock()
    defaults = {
        "id": 1,
        "topic_id": 1,
        "script_id": 2,
        "script_version": 1,
        "input_hash": "a" * 64,
        "script_body_hash": "b" * 64,
        "plan_schema_version": "ProductionPlan-v1",
        "renderer_version": "production-renderer-v1",
        "duration_algorithm_version": "duration-150wpm-v1",
        "title": "Test Script",
        "format": "short",
        "total_estimated_duration_s": 30,
        "total_word_count": 75,
        "warnings_json": '["warn one"]',
        "requires_evidence_review": 0,
        "evidence_hash": "c" * 64,
        "generation_run_id": 3,
        "experiment_id": None,
        "status": "draft",
        "approved_at": None,
        "superseded_at": None,
        "rejected_at": None,
        "created_at": "2024-01-01T00:00:00",
        "updated_at": "2024-01-01T00:00:00",
    }
    defaults.update(overrides)
    row.__getitem__ = lambda self, key: defaults[key]
    return row


def test_production_plan_from_row_basic() -> None:
    plan = ProductionPlan.from_row(_make_plan_row())
    assert plan.id == 1
    assert plan.script_id == 2
    assert plan.status == "draft"
    assert plan.warnings == ["warn one"]
    assert plan.requires_evidence_review is False
    assert plan.experiment_id is None


def test_production_plan_from_row_empty_warnings() -> None:
    plan = ProductionPlan.from_row(_make_plan_row(warnings_json="[]"))
    assert plan.warnings == []


def test_production_plan_from_row_null_warnings() -> None:
    plan = ProductionPlan.from_row(_make_plan_row(warnings_json=None))
    assert plan.warnings == []


def test_production_plan_from_row_approved() -> None:
    plan = ProductionPlan.from_row(
        _make_plan_row(status="approved", approved_at="2024-06-01T12:00:00")
    )
    assert plan.status == "approved"
    assert plan.approved_at == "2024-06-01T12:00:00"


def test_production_plan_from_row_rejected() -> None:
    plan = ProductionPlan.from_row(
        _make_plan_row(status="rejected", rejected_at="2024-07-01T00:00:00")
    )
    assert plan.status == "rejected"
    assert plan.rejected_at == "2024-07-01T00:00:00"


def test_production_plan_is_frozen() -> None:
    plan = ProductionPlan.from_row(_make_plan_row())
    with pytest.raises(ValidationError):
        plan.status = "approved"  # type: ignore[misc]


def test_production_plan_with_experiment_id() -> None:
    plan = ProductionPlan.from_row(_make_plan_row(experiment_id="hook-v1-arm-a"))
    assert plan.experiment_id == "hook-v1-arm-a"


# ---------------------------------------------------------------------------
# ProductionSegment.from_row
# ---------------------------------------------------------------------------


def _make_segment_row(**overrides) -> MagicMock:
    row = MagicMock()
    defaults = {
        "id": 10,
        "plan_id": 1,
        "segment_index": 0,
        "section_index": 0,
        "section_type": "hook",
        "narration_text": "This is the hook.",
        "estimated_duration_s": 4,
        "estimated_word_count": 4,
        "created_at": "2024-01-01T00:00:00",
    }
    defaults.update(overrides)
    row.__getitem__ = lambda self, key: defaults[key]
    return row


def test_production_segment_from_row() -> None:
    seg = ProductionSegment.from_row(_make_segment_row())
    assert seg.id == 10
    assert seg.section_type == "hook"
    assert seg.narration_text == "This is the hook."
    assert seg.estimated_word_count == 4


def test_production_segment_is_frozen() -> None:
    seg = ProductionSegment.from_row(_make_segment_row())
    with pytest.raises(ValidationError):
        seg.narration_text = "Changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ProductionSegmentCitation.from_row
# ---------------------------------------------------------------------------


def _make_citation_row(**overrides) -> MagicMock:
    row = MagicMock()
    defaults = {
        "id": 100,
        "segment_id": 10,
        "claim_id": 42,
        "citation_order": 0,
        "created_at": "2024-01-01T00:00:00",
    }
    defaults.update(overrides)
    row.__getitem__ = lambda self, key: defaults[key]
    return row


def test_production_segment_citation_from_row() -> None:
    cit = ProductionSegmentCitation.from_row(_make_citation_row())
    assert cit.id == 100
    assert cit.claim_id == 42
    assert cit.citation_order == 0


def test_production_segment_citation_is_frozen() -> None:
    cit = ProductionSegmentCitation.from_row(_make_citation_row())
    with pytest.raises(ValidationError):
        cit.claim_id = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ProductionPlanReviewEvent.from_row
# ---------------------------------------------------------------------------


def _make_review_event_row(**overrides) -> MagicMock:
    row = MagicMock()
    defaults = {
        "id": 1,
        "plan_id": 1,
        "topic_id": 1,
        "script_id": 2,
        "evidence_hash": "e" * 64,
        "model": "claude-sonnet-5",
        "prompt_hash": "p" * 64,
        "experiment_id": None,
        "decision": "approved",
        "reason_code": None,
        "notes": None,
        "actor": "dom",
        "created_at": "2024-01-01T00:00:00",
    }
    defaults.update(overrides)
    row.__getitem__ = lambda self, key: defaults[key]
    return row


def test_review_event_from_row_approved() -> None:
    ev = ProductionPlanReviewEvent.from_row(_make_review_event_row())
    assert ev.decision == "approved"
    assert ev.reason_code is None
    assert ev.actor == "dom"
    assert ev.model == "claude-sonnet-5"


def test_review_event_from_row_rejected() -> None:
    ev = ProductionPlanReviewEvent.from_row(
        _make_review_event_row(
            decision="rejected",
            reason_code="pacing",
            notes="Too fast.",
        )
    )
    assert ev.decision == "rejected"
    assert ev.reason_code == "pacing"
    assert ev.notes == "Too fast."


def test_review_event_from_row_null_model() -> None:
    ev = ProductionPlanReviewEvent.from_row(_make_review_event_row(model=None, prompt_hash=None))
    assert ev.model is None
    assert ev.prompt_hash is None


def test_review_event_is_frozen() -> None:
    ev = ProductionPlanReviewEvent.from_row(_make_review_event_row())
    with pytest.raises(ValidationError):
        ev.decision = "rejected"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ProductionSegmentWithCitations
# ---------------------------------------------------------------------------


def _make_segment_with_citations() -> ProductionSegmentWithCitations:
    citations = [
        ProductionSegmentCitation(
            id=1, segment_id=10, claim_id=42, citation_order=0, created_at="2024-01-01T00:00:00"
        ),
        ProductionSegmentCitation(
            id=2, segment_id=10, claim_id=99, citation_order=1, created_at="2024-01-01T00:00:00"
        ),
    ]
    return ProductionSegmentWithCitations(
        segment_id=10,
        plan_id=1,
        segment_index=0,
        section_index=0,
        section_type="hook",
        narration_text="The hook text.",
        estimated_duration_s=3,
        estimated_word_count=3,
        citations=citations,
    )


def test_segment_with_citations_citation_claim_ids() -> None:
    seg = _make_segment_with_citations()
    assert seg.citation_claim_ids == [42, 99]


def test_segment_with_citations_is_frozen() -> None:
    seg = _make_segment_with_citations()
    with pytest.raises(ValidationError):
        seg.narration_text = "Changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ApprovedProductionPlan
# ---------------------------------------------------------------------------


def test_approved_production_plan_fields() -> None:
    seg = _make_segment_with_citations()
    plan = ApprovedProductionPlan(
        plan_id=1,
        topic_id=1,
        script_id=2,
        script_version=1,
        input_hash="a" * 64,
        script_body_hash="b" * 64,
        plan_schema_version="ProductionPlan-v1",
        renderer_version="production-renderer-v1",
        duration_algorithm_version="duration-150wpm-v1",
        title="My Script",
        format="short",
        total_estimated_duration_s=30,
        total_word_count=75,
        warnings=[],
        requires_evidence_review=False,
        evidence_hash="c" * 64,
        generation_run_id=5,
        experiment_id=None,
        approved_at="2024-06-01T12:00:00",
        segments=[seg],
    )
    assert plan.plan_id == 1
    assert len(plan.segments) == 1
    assert plan.segments[0].citation_claim_ids == [42, 99]


def test_approved_production_plan_is_frozen() -> None:
    seg = _make_segment_with_citations()
    plan = ApprovedProductionPlan(
        plan_id=1,
        topic_id=1,
        script_id=2,
        script_version=1,
        input_hash="a" * 64,
        script_body_hash="b" * 64,
        plan_schema_version="ProductionPlan-v1",
        renderer_version="production-renderer-v1",
        duration_algorithm_version="duration-150wpm-v1",
        title="My Script",
        format="short",
        total_estimated_duration_s=30,
        total_word_count=75,
        warnings=[],
        requires_evidence_review=False,
        evidence_hash="c" * 64,
        generation_run_id=None,
        experiment_id=None,
        approved_at="2024-06-01T12:00:00",
        segments=[seg],
    )
    with pytest.raises(ValidationError):
        plan.plan_id = 999  # type: ignore[misc]
