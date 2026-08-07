"""Phase 10 analytics input validation."""

from __future__ import annotations

from app.analytics.constants import PERIOD_TYPES, REVIEW_SEVERITIES, SEVERITY_REQUIRES_NOTES
from app.analytics.errors import ReviewNotesRequiredError, UnknownPeriodTypeError


def validate_period_type(period_type: str) -> None:
    """Raise UnknownPeriodTypeError for unrecognised period types."""
    if period_type not in PERIOD_TYPES:
        raise UnknownPeriodTypeError(period_type)


def validate_review_severity(severity: str) -> None:
    """Raise ValueError for unrecognised severity values."""
    if severity not in REVIEW_SEVERITIES:
        raise ValueError(
            f"Invalid severity {severity!r}. Must be one of: "
            + ", ".join(sorted(REVIEW_SEVERITIES))
        )


def validate_review_notes(severity: str, notes: str) -> None:
    """Raise ReviewNotesRequiredError when notes are required but empty."""
    if severity in SEVERITY_REQUIRES_NOTES and not notes.strip():
        raise ReviewNotesRequiredError(severity)


def validate_ingest_draft(
    provider: str,
    publication_id: int,
    publishing_plan_id: int,
    publishing_job_id: int,
    topic_id: int,
) -> None:
    """Raise ValueError for clearly invalid ingest fields."""
    if not provider.strip():
        raise ValueError("provider must not be empty")
    for name, val in [
        ("publication_id", publication_id),
        ("publishing_plan_id", publishing_plan_id),
        ("publishing_job_id", publishing_job_id),
        ("topic_id", topic_id),
    ]:
        if val <= 0:
            raise ValueError(f"{name} must be a positive integer, got {val}")
