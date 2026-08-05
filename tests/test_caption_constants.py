"""Tests for src/app/captions/constants.py."""

from __future__ import annotations

from app.captions.constants import (
    CAPTION_DEFAULT_LANGUAGE,
    CAPTION_EVENT_CUE_REJECTED,
    CAPTION_EVENT_RUN_APPROVED,
    CAPTION_EVENT_RUN_REJECTED,
    CAPTION_EXPORTER_VERSION,
    CAPTION_MAX_CHARS_PER_LINE,
    CAPTION_MAX_LINES_PER_CUE,
    CAPTION_MAX_READING_SPEED_WARN_CPS,
    CAPTION_PREFERRED_MAX_CUE_DURATION_MS,
    CAPTION_PREFERRED_MIN_CUE_DURATION_MS,
    CAPTION_REJECTION_REASON_CODE_REQUIRING_NOTES,
    CAPTION_REJECTION_REASON_CODES,
    CAPTION_SCHEMA_VERSION,
    CAPTION_SEGMENTATION_VERSION,
    CAPTION_SEVERITY_MAX,
    CAPTION_SEVERITY_MIN,
    CAPTION_STALE_TEMP_AGE_S,
    CAPTION_STYLE_VERSION,
    CAPTION_TARGET_READING_SPEED_CPS,
    CAPTION_TIMING_ALGORITHM_VERSION,
    CAPTION_TIMING_ROUNDING_TOLERANCE_MS,
    CAPTION_TIMING_SOURCE_ESTIMATED,
    CAPTION_TIMING_SOURCE_FORCED_ALIGNMENT,
    CAPTION_TIMING_SOURCE_PROVIDER_NATIVE,
    CAPTION_VALID_EVENT_TYPES,
    CAPTION_VALID_TIMING_SOURCES,
)


class TestVersionStrings:
    def test_schema_version_is_string(self):
        assert isinstance(CAPTION_SCHEMA_VERSION, str)
        assert CAPTION_SCHEMA_VERSION.startswith("Caption-")

    def test_segmentation_version_is_string(self):
        assert isinstance(CAPTION_SEGMENTATION_VERSION, str)

    def test_timing_algorithm_version_is_string(self):
        assert isinstance(CAPTION_TIMING_ALGORITHM_VERSION, str)

    def test_style_version_is_string(self):
        assert isinstance(CAPTION_STYLE_VERSION, str)

    def test_exporter_version_is_string(self):
        assert isinstance(CAPTION_EXPORTER_VERSION, str)

    def test_all_version_strings_nonempty(self):
        for v in (
            CAPTION_SCHEMA_VERSION,
            CAPTION_SEGMENTATION_VERSION,
            CAPTION_TIMING_ALGORITHM_VERSION,
            CAPTION_STYLE_VERSION,
            CAPTION_EXPORTER_VERSION,
        ):
            assert v, f"Version string is empty: {v!r}"


class TestSegmentationLimits:
    def test_max_chars_per_line_positive(self):
        assert CAPTION_MAX_CHARS_PER_LINE > 0

    def test_max_lines_per_cue_at_least_two(self):
        assert CAPTION_MAX_LINES_PER_CUE >= 2

    def test_preferred_min_less_than_max(self):
        assert CAPTION_PREFERRED_MIN_CUE_DURATION_MS < CAPTION_PREFERRED_MAX_CUE_DURATION_MS

    def test_preferred_min_positive(self):
        assert CAPTION_PREFERRED_MIN_CUE_DURATION_MS > 0

    def test_rounding_tolerance_small(self):
        assert 0 <= CAPTION_TIMING_ROUNDING_TOLERANCE_MS <= 10


class TestReadingSpeed:
    def test_target_positive(self):
        assert CAPTION_TARGET_READING_SPEED_CPS > 0

    def test_warn_above_target(self):
        assert CAPTION_MAX_READING_SPEED_WARN_CPS > CAPTION_TARGET_READING_SPEED_CPS


class TestSeverity:
    def test_severity_min_max(self):
        assert CAPTION_SEVERITY_MIN == 1
        assert CAPTION_SEVERITY_MAX == 5


class TestRejectionReasonCodes:
    def test_other_requires_notes(self):
        assert CAPTION_REJECTION_REASON_CODE_REQUIRING_NOTES in CAPTION_REJECTION_REASON_CODES

    def test_known_codes_present(self):
        for code in (
            "timing",
            "segmentation",
            "line_break",
            "punctuation",
            "capitalization",
            "missing_text",
            "extra_text",
            "wrong_word",
            "reading_speed",
            "overlap",
            "safe_zone",
            "style",
            "language",
            "other",
        ):
            assert code in CAPTION_REJECTION_REASON_CODES

    def test_rejection_reason_codes_is_frozenset(self):
        assert isinstance(CAPTION_REJECTION_REASON_CODES, frozenset)


class TestEventTypes:
    def test_all_event_types_in_set(self):
        for v in (
            CAPTION_EVENT_RUN_APPROVED,
            CAPTION_EVENT_RUN_REJECTED,
            CAPTION_EVENT_CUE_REJECTED,
        ):
            assert v in CAPTION_VALID_EVENT_TYPES

    def test_event_types_underscore_delimited(self):
        for ev in CAPTION_VALID_EVENT_TYPES:
            assert " " not in ev, f"Event type has space: {ev!r}"

    def test_valid_event_types_frozenset(self):
        assert isinstance(CAPTION_VALID_EVENT_TYPES, frozenset)


class TestTimingSources:
    def test_estimated_in_valid_sources(self):
        assert CAPTION_TIMING_SOURCE_ESTIMATED in CAPTION_VALID_TIMING_SOURCES

    def test_provider_native_in_valid_sources(self):
        assert CAPTION_TIMING_SOURCE_PROVIDER_NATIVE in CAPTION_VALID_TIMING_SOURCES

    def test_forced_alignment_in_valid_sources(self):
        assert CAPTION_TIMING_SOURCE_FORCED_ALIGNMENT in CAPTION_VALID_TIMING_SOURCES

    def test_valid_timing_sources_frozenset(self):
        assert isinstance(CAPTION_VALID_TIMING_SOURCES, frozenset)


class TestDefaults:
    def test_default_language_is_en_us(self):
        assert CAPTION_DEFAULT_LANGUAGE == "en-US"

    def test_stale_temp_age_positive(self):
        assert CAPTION_STALE_TEMP_AGE_S > 0
