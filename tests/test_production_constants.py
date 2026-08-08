"""Tests for Phase 6 M6.1 production plan constants."""

from __future__ import annotations

from app.production.constants import (
    PRODUCTION_DURATION_VERSION,
    PRODUCTION_PLAN_RENDERER_VERSION,
    PRODUCTION_PLAN_SCHEMA_VERSION,
    REJECTION_REASON_CODE_REQUIRING_NOTES,
    REJECTION_REASON_CODES,
)


def test_plan_schema_version_value() -> None:
    assert PRODUCTION_PLAN_SCHEMA_VERSION == "ProductionPlan-v1"


def test_plan_renderer_version_value() -> None:
    assert PRODUCTION_PLAN_RENDERER_VERSION == "production-renderer-v1"


def test_duration_version_value() -> None:
    assert PRODUCTION_DURATION_VERSION == "duration-150wpm-v1"


def test_version_strings_are_non_empty() -> None:
    for val in (
        PRODUCTION_PLAN_SCHEMA_VERSION,
        PRODUCTION_PLAN_RENDERER_VERSION,
        PRODUCTION_DURATION_VERSION,
    ):
        assert isinstance(val, str)
        assert len(val) > 0


def test_rejection_reason_codes_complete() -> None:
    expected = {
        "segment_structure",
        "narration_wording",
        "pacing",
        "duration",
        "citation_mapping",
        "evidence_concern",
        "format_mismatch",
        "other",
    }
    assert REJECTION_REASON_CODES == expected


def test_rejection_reason_requiring_notes_is_other() -> None:
    assert REJECTION_REASON_CODE_REQUIRING_NOTES == "other"
    assert REJECTION_REASON_CODE_REQUIRING_NOTES in REJECTION_REASON_CODES


def test_all_reason_codes_are_strings() -> None:
    for code in REJECTION_REASON_CODES:
        assert isinstance(code, str)
        assert len(code) > 0
