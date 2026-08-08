"""Phase 11 — input validation for the Learning & Optimization Engine."""

from __future__ import annotations

from app.learning.constants import (
    CONFIDENCE_LEVELS,
    EVIDENCE_CLASSIFICATIONS,
    RECOMMENDATION_DOMAINS,
    RECOMMENDATION_STATUSES,
    RECOMMENDATION_STRENGTHS,
    RECOMMENDATION_SUBSYSTEMS,
    REVIEW_ELIGIBLE_STATUSES,
    REVIEW_EVENT_TYPES,
    STATUS_SUPERSEDED,
)
from app.learning.errors import (
    InvalidConfidenceLevelError,
    InvalidConfidenceScoreError,
    InvalidEvidenceClassificationError,
    InvalidRecommendationDomainError,
    InvalidRecommendationStatusError,
    InvalidRecommendationStrengthError,
    InvalidRecommendationSubsystemError,
    InvalidReviewEventTypeError,
    NotesRequiredError,
    RecommendationNotReviewableError,
    ReviewerRequiredError,
)
from app.learning.models import OptimizationRecommendation


def validate_evidence_classification(classification: str) -> None:
    if classification not in EVIDENCE_CLASSIFICATIONS:
        raise InvalidEvidenceClassificationError(
            f"Invalid evidence_classification {classification!r}. "
            f"Valid: {sorted(EVIDENCE_CLASSIFICATIONS)}"
        )


def validate_recommendation_strength(strength: str) -> None:
    if strength not in RECOMMENDATION_STRENGTHS:
        raise InvalidRecommendationStrengthError(
            f"Invalid recommendation_strength {strength!r}. "
            f"Valid: {sorted(RECOMMENDATION_STRENGTHS)}"
        )


def validate_domain(domain: str) -> None:
    if domain not in RECOMMENDATION_DOMAINS:
        raise InvalidRecommendationDomainError(
            f"Invalid domain {domain!r}. Valid: {sorted(RECOMMENDATION_DOMAINS)}"
        )


def validate_subsystem(subsystem: str) -> None:
    if subsystem not in RECOMMENDATION_SUBSYSTEMS:
        raise InvalidRecommendationSubsystemError(
            f"Invalid subsystem {subsystem!r}. Valid: {sorted(RECOMMENDATION_SUBSYSTEMS)}"
        )


def validate_confidence_level(confidence: str) -> None:
    if confidence not in CONFIDENCE_LEVELS:
        raise InvalidConfidenceLevelError(
            f"Invalid confidence {confidence!r}. Valid: {sorted(CONFIDENCE_LEVELS)}"
        )


def validate_confidence_score(score: float) -> None:
    if not (0.0 <= score <= 1.0):
        raise InvalidConfidenceScoreError(
            f"confidence_score must be in [0.0, 1.0]; got {score}"
        )


def validate_recommendation_status(status: str) -> None:
    if status not in RECOMMENDATION_STATUSES:
        raise InvalidRecommendationStatusError(
            f"Invalid status {status!r}. Valid: {sorted(RECOMMENDATION_STATUSES)}"
        )


def validate_review_event_type(event_type: str) -> None:
    if event_type not in REVIEW_EVENT_TYPES:
        raise InvalidReviewEventTypeError(
            f"Invalid event_type {event_type!r}. Valid: {sorted(REVIEW_EVENT_TYPES)}"
        )


def validate_review_eligible(
    recommendation: OptimizationRecommendation,
) -> None:
    if recommendation.status == STATUS_SUPERSEDED:
        raise RecommendationNotReviewableError(
            f"Recommendation {recommendation.id} is superseded and cannot be reviewed."
        )
    if recommendation.status not in REVIEW_ELIGIBLE_STATUSES:
        raise RecommendationNotReviewableError(
            f"Recommendation {recommendation.id} has status {recommendation.status!r} "
            f"which does not accept review events."
        )


def validate_review_event(
    event_type: str,
    reviewer: str,
    notes: str,
) -> None:
    validate_review_event_type(event_type)
    if event_type in {"accepted", "rejected"} and not reviewer.strip():
        raise ReviewerRequiredError(
            f"A reviewer name is required for {event_type!r} events."
        )
    if event_type == "rejected" and not notes.strip():
        raise NotesRequiredError("Notes are required for 'rejected' events.")
