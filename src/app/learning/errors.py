"""Phase 11 — Learning & Optimization Engine error hierarchy."""

from __future__ import annotations


class LearningError(Exception):
    """Base class for all Learning Engine errors."""


# ── Learning run errors ───────────────────────────────────────────────────────


class LearningRunNotFoundError(LearningError):
    """No learning run with the given ID exists."""


class LearningRunAlreadyCompleteError(LearningError):
    """A learning run is already in a terminal state and cannot be re-run."""


class InsufficientAnalyticsDataError(LearningError):
    """Not enough analytics data to generate recommendations for a topic."""


# ── Recommendation errors ─────────────────────────────────────────────────────


class RecommendationNotFoundError(LearningError):
    """No recommendation with the given ID exists."""


class InvalidRecommendationDomainError(LearningError):
    """The domain value is not in RECOMMENDATION_DOMAINS."""


class InvalidRecommendationSubsystemError(LearningError):
    """The subsystem value is not in RECOMMENDATION_SUBSYSTEMS."""


class InvalidRecommendationStatusError(LearningError):
    """The status value is not in RECOMMENDATION_STATUSES."""


class InvalidConfidenceLevelError(LearningError):
    """The confidence value is not in CONFIDENCE_LEVELS."""


class InvalidConfidenceScoreError(LearningError):
    """The confidence_score is outside the valid [0.0, 1.0] range."""


class InvalidEvidenceClassificationError(LearningError):
    """The evidence_classification value is not in EVIDENCE_CLASSIFICATIONS."""


class InvalidRecommendationStrengthError(LearningError):
    """The recommendation_strength value is not in RECOMMENDATION_STRENGTHS."""


class RecommendationAlreadyReviewedError(LearningError):
    """Cannot transition a recommendation that is already superseded."""


class RecommendationNotReviewableError(LearningError):
    """The recommendation is in a status that does not accept review events."""


# ── Review event errors ───────────────────────────────────────────────────────


class InvalidReviewEventTypeError(LearningError):
    """The event_type value is not in REVIEW_EVENT_TYPES."""


class ReviewerRequiredError(LearningError):
    """A reviewer name is required for accept/reject events."""


class NotesRequiredError(LearningError):
    """Notes are required for reject events."""


# ── Hashing / determinism errors ─────────────────────────────────────────────


class HashInputError(LearningError):
    """The hash input is malformed or contains non-serializable values."""


# ── Attribution errors ────────────────────────────────────────────────────────


class AttributionError(LearningError):
    """Could not attribute a recommendation to an upstream entity."""


class MissingAttributionFieldError(AttributionError):
    """A required attribution field is missing from the analytics handoff."""
