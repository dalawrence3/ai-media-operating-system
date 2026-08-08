"""Tests for src/app/captions/timing.py."""

from __future__ import annotations

from app.captions.constants import (
    CAPTION_PREFERRED_MAX_CUE_DURATION_MS,
)
from app.captions.segmentation import SegmentedCueText
from app.captions.timing import allocate_timing


def _make_cue(text: str) -> SegmentedCueText:
    return SegmentedCueText(lines=text.split("\n"))


class TestAllocateTiming:
    def test_empty_cues_returns_empty(self):
        assert allocate_timing([], 5000) == []

    def test_single_cue_spans_full_duration(self):
        cues = [_make_cue("Hello world")]
        result = allocate_timing(cues, 3000)
        assert len(result) == 1
        start, end, warnings = result[0]
        assert start == 0
        assert end == 3000

    def test_multiple_cues_no_overlap(self):
        cues = [_make_cue("First part"), _make_cue("Second part"), _make_cue("Third")]
        result = allocate_timing(cues, 9000)
        for i in range(len(result) - 1):
            _, end_i, _ = result[i]
            start_next, _, _ = result[i + 1]
            assert end_i == start_next, (
                f"Gap/overlap at cue {i}: end={end_i}, next_start={start_next}"
            )

    def test_first_cue_starts_at_zero(self):
        cues = [_make_cue("First"), _make_cue("Second")]
        result = allocate_timing(cues, 4000)
        assert result[0][0] == 0

    def test_last_cue_ends_at_segment_duration(self):
        cues = [_make_cue("First"), _make_cue("Second"), _make_cue("Third")]
        duration = 7500
        result = allocate_timing(cues, duration)
        assert result[-1][1] == duration

    def test_all_timestamps_are_integers(self):
        cues = [_make_cue(f"cue {i}") for i in range(5)]
        result = allocate_timing(cues, 10001)
        for start, end, _ in result:
            assert isinstance(start, int), f"start is not int: {start!r}"
            assert isinstance(end, int), f"end is not int: {end!r}"

    def test_start_strictly_less_than_end(self):
        cues = [_make_cue("Short"), _make_cue("Also short")]
        result = allocate_timing(cues, 2000)
        for start, end, _ in result:
            assert start < end, f"start={start} >= end={end}"

    def test_proportional_allocation_longer_cue_gets_more_time(self):
        short_cue = _make_cue("Hi")  # 2 chars
        long_cue = _make_cue("This is a much longer caption cue text")  # 38 chars
        result = allocate_timing([short_cue, long_cue], 10000)
        _, short_end, _ = result[0]
        long_start, long_end, _ = result[1]
        short_dur = short_end - 0
        long_dur = long_end - long_start
        assert long_dur > short_dur, (
            f"Longer cue should get more time: short={short_dur}, long={long_dur}"
        )

    def test_short_cue_duration_warning(self):
        # A very short total duration forces tiny cue durations
        cues = [_make_cue("a"), _make_cue("b"), _make_cue("c"), _make_cue("d")]
        result = allocate_timing(cues, 100)  # Only 100ms total → each ~25ms → below min
        warnings_combined = [w for _, _, ws in result for w in ws]
        assert "cue_too_short" in warnings_combined

    def test_long_cue_duration_warning(self):
        # One very long sentence with a very long duration
        cues = [_make_cue("Hello world")]
        result = allocate_timing(cues, CAPTION_PREFERRED_MAX_CUE_DURATION_MS + 5000)
        _, _, warnings = result[0]
        assert "cue_too_long" in warnings

    def test_rounding_tolerance_last_cue(self):
        """The last cue end must exactly equal segment_duration_ms."""
        cues = [_make_cue(f"word{i}") for i in range(7)]
        duration = 12345
        result = allocate_timing(cues, duration)
        assert result[-1][1] == duration

    def test_deterministic_same_input_same_output(self):
        cues = [_make_cue("First sentence here"), _make_cue("Second sentence")]
        r1 = allocate_timing(cues, 6000)
        r2 = allocate_timing(cues, 6000)
        assert r1 == r2

    def test_odd_millisecond_duration(self):
        cues = [_make_cue("Hello"), _make_cue("World")]
        result = allocate_timing(cues, 3001)
        assert result[-1][1] == 3001
        assert result[0][0] == 0
