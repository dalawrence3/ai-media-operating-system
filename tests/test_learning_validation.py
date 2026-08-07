"""Tests for Phase 11 validation module."""

from __future__ import annotations

import pytest

from app.learning.errors import (
    InvalidConfidenceLevelError,
    InvalidConfidenceScoreError,
    InvalidEvidenceClassificationError,
    InvalidRecommendationDomainError,
    InvalidRecommendationStatusError,
    InvalidRecommendationSubsystemError,
    InvalidReviewEventTypeError,
    NotesRequiredError,
    RecommendationNotReviewableError,
    ReviewerRequiredError,
)
from app.learning.validation import (
    validate_confidence_level,
    validate_confidence_score,
    validate_domain,
    validate_evidence_classification,
    validate_recommendation_status,
    validate_review_eligible,
    validate_review_event,
    validate_review_event_type,
    validate_subsystem,
)


class TestValidateEvidenceClassification:
    def test_all_valid_classifications_pass(self):
        for c in (
            "observational",
            "controlled_experiment",
            "human_preference",
            "operational_failure",
            "mixed",
        ):
            validate_evidence_classification(c)  # should not raise

    def test_invalid_classification_raises(self):
        with pytest.raises(InvalidEvidenceClassificationError):
            validate_evidence_classification("unknown")

    def test_empty_string_raises(self):
        with pytest.raises(InvalidEvidenceClassificationError):
            validate_evidence_classification("")

    def test_causal_label_is_rejected(self):
        with pytest.raises(InvalidEvidenceClassificationError):
            validate_evidence_classification("causal")


class TestValidateDomain:
    def test_valid_domains_pass(self):
        for d in ("topics", "research", "scripts", "narration", "publishing"):
            validate_domain(d)  # should not raise

    def test_invalid_domain_raises(self):
        with pytest.raises(InvalidRecommendationDomainError):
            validate_domain("invalid_domain")

    def test_empty_string_raises(self):
        with pytest.raises(InvalidRecommendationDomainError):
            validate_domain("")


class TestValidateSubsystem:
    def test_valid_subsystem_passes(self):
        validate_subsystem("hook_effectiveness")
        validate_subsystem("title_effectiveness")

    def test_invalid_subsystem_raises(self):
        with pytest.raises(InvalidRecommendationSubsystemError):
            validate_subsystem("magic_feature")


class TestValidateConfidenceLevel:
    def test_valid_levels_pass(self):
        for level in ("low", "medium", "high"):
            validate_confidence_level(level)

    def test_invalid_level_raises(self):
        with pytest.raises(InvalidConfidenceLevelError):
            validate_confidence_level("very_high")


class TestValidateConfidenceScore:
    def test_zero_is_valid(self):
        validate_confidence_score(0.0)

    def test_one_is_valid(self):
        validate_confidence_score(1.0)

    def test_midpoint_is_valid(self):
        validate_confidence_score(0.5)

    def test_negative_raises(self):
        with pytest.raises(InvalidConfidenceScoreError):
            validate_confidence_score(-0.001)

    def test_over_one_raises(self):
        with pytest.raises(InvalidConfidenceScoreError):
            validate_confidence_score(1.001)


class TestValidateRecommendationStatus:
    def test_valid_statuses_pass(self):
        for s in ("pending", "accepted", "rejected", "superseded"):
            validate_recommendation_status(s)

    def test_invalid_status_raises(self):
        with pytest.raises(InvalidRecommendationStatusError):
            validate_recommendation_status("unknown")


class TestValidateReviewEventType:
    def test_valid_types_pass(self):
        for t in ("accepted", "rejected", "noted"):
            validate_review_event_type(t)

    def test_invalid_type_raises(self):
        with pytest.raises(InvalidReviewEventTypeError):
            validate_review_event_type("dismissed")


class TestValidateReviewEligible:
    def _make_rec(self, status: str):
        from unittest.mock import MagicMock
        rec = MagicMock()
        rec.id = 1
        rec.status = status
        return rec

    def test_pending_is_eligible(self):
        validate_review_eligible(self._make_rec("pending"))  # should not raise

    def test_accepted_is_eligible(self):
        validate_review_eligible(self._make_rec("accepted"))

    def test_rejected_is_eligible(self):
        validate_review_eligible(self._make_rec("rejected"))

    def test_superseded_raises(self):
        with pytest.raises(RecommendationNotReviewableError):
            validate_review_eligible(self._make_rec("superseded"))


class TestValidateReviewEvent:
    def test_noted_without_reviewer_is_ok(self):
        validate_review_event("noted", "", "")  # should not raise

    def test_accepted_requires_reviewer(self):
        with pytest.raises(ReviewerRequiredError):
            validate_review_event("accepted", "", "")

    def test_accepted_with_reviewer_passes(self):
        validate_review_event("accepted", "operator", "")

    def test_rejected_requires_reviewer_and_notes(self):
        with pytest.raises(ReviewerRequiredError):
            validate_review_event("rejected", "", "some notes")

    def test_rejected_requires_notes(self):
        with pytest.raises(NotesRequiredError):
            validate_review_event("rejected", "operator", "")

    def test_rejected_with_reviewer_and_notes_passes(self):
        validate_review_event("rejected", "operator", "not applicable")

    def test_invalid_event_type_raises(self):
        with pytest.raises(InvalidReviewEventTypeError):
            validate_review_event("unknown", "operator", "notes")
