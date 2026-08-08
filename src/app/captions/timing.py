"""Phase 6 M6.3A deterministic estimated timing allocation.

All behaviour is governed by CAPTION_TIMING_ALGORITHM_VERSION.  Any change
to timing rules requires bumping that version constant.

Contract:
  - uses measured audio duration from the approved narration asset
  - allocates cue timings proportionally by display-character count
  - all timestamps are integers (milliseconds)
  - timestamps are segment-relative (0 = start of segment audio)
  - no overlap between consecutive cues
  - last cue end equals the segment duration (within CAPTION_TIMING_ROUNDING_TOLERANCE_MS)
  - rounding remainder allocated via cumulative method (fully deterministic)
"""

from __future__ import annotations

from app.captions.constants import (
    CAPTION_MAX_READING_SPEED_WARN_CPS,
    CAPTION_PREFERRED_MAX_CUE_DURATION_MS,
    CAPTION_PREFERRED_MIN_CUE_DURATION_MS,
)
from app.captions.segmentation import SegmentedCueText


def _display_char_count(lines: list[str]) -> int:
    """Characters when lines are joined with a single space (for speed calculation)."""
    return len(" ".join(lines))


def allocate_timing(
    cues: list[SegmentedCueText],
    segment_duration_ms: int,
) -> list[tuple[int, int, list[str]]]:
    """Allocate segment-relative start/end timestamps to each cue.

    Returns a list of (start_ms, end_ms, warnings) tuples, one per cue.

    Algorithm: cumulative proportional allocation by display-character count.
    For cue i with c_i display characters and total C:
        end_ms[i] = round(segment_duration_ms * cumulative_C[i] / C)
        start_ms[i] = end_ms[i-1]  (0 for the first cue)

    This guarantees:
    - No overlap (consecutive, non-overlapping, non-gapped timeline)
    - Last cue end == segment_duration_ms exactly (sum rounds correctly)
    - All timestamps are integers
    """
    if not cues:
        return []

    # Character counts for proportional allocation.
    char_counts = [max(1, _display_char_count(c.lines)) for c in cues]
    total_chars = sum(char_counts)
    cumulative = 0
    result: list[tuple[int, int, list[str]]] = []

    prev_end = 0
    for i, (cue, cc) in enumerate(zip(cues, char_counts, strict=False)):
        cumulative += cc
        if i == len(cues) - 1:
            # Last cue always ends exactly at segment_duration_ms.
            end_ms = segment_duration_ms
        else:
            end_ms = round(segment_duration_ms * cumulative / total_chars)
        start_ms = prev_end
        # Safety: ensure end_ms > start_ms (at least 1ms per cue).
        if end_ms <= start_ms:
            end_ms = start_ms + 1
        prev_end = end_ms

        warnings = _timing_warnings(cue.lines, start_ms, end_ms)
        result.append((start_ms, end_ms, warnings))

    return result


def _timing_warnings(
    lines: list[str],
    start_ms: int,
    end_ms: int,
) -> list[str]:
    """Return deterministic warnings for a single cue's timing."""
    warnings: list[str] = []
    duration_ms = end_ms - start_ms
    if duration_ms < CAPTION_PREFERRED_MIN_CUE_DURATION_MS:
        warnings.append("cue_too_short")
    if duration_ms > CAPTION_PREFERRED_MAX_CUE_DURATION_MS:
        warnings.append("cue_too_long")
    # Reading speed: characters per second.
    display_chars = _display_char_count(lines)
    if duration_ms > 0:
        cps = display_chars / (duration_ms / 1000.0)
        if cps > CAPTION_MAX_READING_SPEED_WARN_CPS:
            warnings.append("reading_speed_high")
    return warnings
