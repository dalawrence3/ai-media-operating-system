"""Tests for Phase 6 M6.2 narration constants."""

from __future__ import annotations

from app.narration.constants import (
    NARRATION_ALGORITHM_VERSION,
    NARRATION_DEFAULT_LANGUAGE,
    NARRATION_DEFAULT_OUTPUT_FORMAT,
    NARRATION_DEFAULT_SAMPLE_RATE_HZ,
    NARRATION_DEFAULT_SPEAKING_RATE,
    NARRATION_DURATION_DEVIATION_THRESHOLD,
    NARRATION_REJECTION_REASON_CODE_REQUIRING_NOTES,
    NARRATION_REJECTION_REASON_CODES,
    NARRATION_SCHEMA_VERSION,
    NARRATION_SEVERITY_MAX,
    NARRATION_SEVERITY_MIN,
    NARRATION_STALE_TEMP_AGE_S,
)


def test_schema_version_is_string() -> None:
    assert isinstance(NARRATION_SCHEMA_VERSION, str)
    assert NARRATION_SCHEMA_VERSION == "Narration-v1"


def test_algorithm_version_is_string() -> None:
    assert isinstance(NARRATION_ALGORITHM_VERSION, str)
    assert NARRATION_ALGORITHM_VERSION == "narration-segment-v1"


def test_rejection_reason_codes_is_frozenset() -> None:
    assert isinstance(NARRATION_REJECTION_REASON_CODES, frozenset)


def test_rejection_reason_codes_exact_set() -> None:
    expected = {
        "voice_mismatch",
        "pronunciation",
        "pacing",
        "emotion",
        "robotic_delivery",
        "clipping",
        "silence",
        "volume",
        "timing",
        "wrong_text",
        "provider_quality",
        "other",
    }
    assert NARRATION_REJECTION_REASON_CODES == expected


def test_rejection_reason_code_requiring_notes() -> None:
    assert NARRATION_REJECTION_REASON_CODE_REQUIRING_NOTES == "other"
    assert NARRATION_REJECTION_REASON_CODE_REQUIRING_NOTES in NARRATION_REJECTION_REASON_CODES


def test_severity_bounds() -> None:
    assert NARRATION_SEVERITY_MIN == 1
    assert NARRATION_SEVERITY_MAX == 5
    assert NARRATION_SEVERITY_MIN < NARRATION_SEVERITY_MAX


def test_audio_defaults() -> None:
    assert NARRATION_DEFAULT_OUTPUT_FORMAT == "wav"
    assert isinstance(NARRATION_DEFAULT_SAMPLE_RATE_HZ, int)
    assert NARRATION_DEFAULT_SAMPLE_RATE_HZ > 0
    assert NARRATION_DEFAULT_LANGUAGE == "en-US"
    assert NARRATION_DEFAULT_SPEAKING_RATE == 1.0


def test_duration_deviation_threshold() -> None:
    assert isinstance(NARRATION_DURATION_DEVIATION_THRESHOLD, float)
    assert 0.0 < NARRATION_DURATION_DEVIATION_THRESHOLD < 1.0


def test_stale_temp_age_positive() -> None:
    assert NARRATION_STALE_TEMP_AGE_S > 0
