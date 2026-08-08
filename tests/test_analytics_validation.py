"""Tests for analytics validation module."""

from __future__ import annotations

import pytest

from app.analytics.errors import ReviewNotesRequiredError, UnknownPeriodTypeError
from app.analytics.validation import (
    validate_ingest_draft,
    validate_period_type,
    validate_review_notes,
    validate_review_severity,
)


class TestValidatePeriodType:
    def test_valid_daily(self):
        validate_period_type("daily")

    def test_valid_weekly(self):
        validate_period_type("weekly")

    def test_valid_monthly(self):
        validate_period_type("monthly")

    def test_valid_lifetime(self):
        validate_period_type("lifetime")

    def test_invalid_raises(self):
        with pytest.raises(UnknownPeriodTypeError):
            validate_period_type("quarterly")

    def test_empty_raises(self):
        with pytest.raises(UnknownPeriodTypeError):
            validate_period_type("")


class TestValidateReviewSeverity:
    def test_valid_severities(self):
        for sev in ("info", "warning", "error", "critical", "other"):
            validate_review_severity(sev)

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="Invalid severity"):
            validate_review_severity("debug")


class TestValidateReviewNotes:
    def test_other_requires_notes(self):
        with pytest.raises(ReviewNotesRequiredError):
            validate_review_notes("other", "")

    def test_other_with_whitespace_only_raises(self):
        with pytest.raises(ReviewNotesRequiredError):
            validate_review_notes("other", "   ")

    def test_other_with_notes_passes(self):
        validate_review_notes("other", "some note")

    def test_info_without_notes_passes(self):
        validate_review_notes("info", "")

    def test_warning_without_notes_passes(self):
        validate_review_notes("warning", "")

    def test_error_without_notes_passes(self):
        validate_review_notes("error", "")

    def test_critical_without_notes_passes(self):
        validate_review_notes("critical", "")


class TestValidateIngestDraft:
    def test_valid_passes(self):
        validate_ingest_draft("fake", 1, 1, 1, 1)

    def test_empty_provider_raises(self):
        with pytest.raises(ValueError, match="provider"):
            validate_ingest_draft("", 1, 1, 1, 1)

    def test_zero_publication_id_raises(self):
        with pytest.raises(ValueError, match="publication_id"):
            validate_ingest_draft("fake", 0, 1, 1, 1)

    def test_negative_topic_id_raises(self):
        with pytest.raises(ValueError, match="topic_id"):
            validate_ingest_draft("fake", 1, 1, 1, -1)

    def test_whitespace_only_provider_raises(self):
        with pytest.raises(ValueError, match="provider"):
            validate_ingest_draft("   ", 1, 1, 1, 1)
