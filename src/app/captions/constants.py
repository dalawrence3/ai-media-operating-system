"""Phase 6 M6.3A caption constants.

All version strings and output-affecting limits are grouped here.
Changing any version string changes the caption input hash and forces a
new caption run for affected narration runs.  Changing any limit constant
that affects output (segmentation, timing, export format) requires bumping
the relevant version string simultaneously.
"""

from __future__ import annotations

# ── Version strings (all feed into caption input hash) ───────────────────────

CAPTION_SCHEMA_VERSION: str = "Caption-v1"
CAPTION_SEGMENTATION_VERSION: str = "caption-segment-v1"
CAPTION_TIMING_ALGORITHM_VERSION: str = "caption-timing-estimated-v1"
CAPTION_STYLE_VERSION: str = "caption-style-v1"
CAPTION_EXPORTER_VERSION: str = "caption-exporter-v1"

# ── Segmentation limits (frozen under CAPTION_SEGMENTATION_VERSION) ───────────

CAPTION_MAX_CHARS_PER_LINE: int = 42
CAPTION_MAX_LINES_PER_CUE: int = 2

# ── Timing thresholds (frozen under CAPTION_TIMING_ALGORITHM_VERSION) ─────────

# Preferred cue duration range (warnings issued outside this range)
CAPTION_PREFERRED_MIN_CUE_DURATION_MS: int = 1000
CAPTION_PREFERRED_MAX_CUE_DURATION_MS: int = 7000

# Rounding tolerance: the last cue's end_ms may differ from segment duration
# by at most this many milliseconds due to integer rounding.
CAPTION_TIMING_ROUNDING_TOLERANCE_MS: int = 1

# ── Reading speed (frozen under CAPTION_STYLE_VERSION) ───────────────────────

# Target reading speed in characters per second
CAPTION_TARGET_READING_SPEED_CPS: float = 21.0
# Reading speed above this threshold triggers a warning
CAPTION_MAX_READING_SPEED_WARN_CPS: float = 25.0

# ── Duration deviation warning (stored under CAPTION_TIMING_ALGORITHM_VERSION)

# Warn if estimated segment timing differs from production segment estimate
# by more than this fraction of the production segment estimate.
CAPTION_DURATION_DEVIATION_WARN_FRACTION: float = 0.50

# ── Rejection reason codes ────────────────────────────────────────────────────

CAPTION_REJECTION_REASON_CODES: frozenset[str] = frozenset(
    {
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
    }
)

CAPTION_REJECTION_REASON_CODE_REQUIRING_NOTES: str = "other"

# ── Severity ──────────────────────────────────────────────────────────────────

CAPTION_SEVERITY_MIN: int = 1
CAPTION_SEVERITY_MAX: int = 5

# ── Timing source values ──────────────────────────────────────────────────────

CAPTION_TIMING_SOURCE_ESTIMATED: str = "estimated"
CAPTION_TIMING_SOURCE_PROVIDER_NATIVE: str = "provider_native"
CAPTION_TIMING_SOURCE_FORCED_ALIGNMENT: str = "forced_alignment"

CAPTION_VALID_TIMING_SOURCES: frozenset[str] = frozenset(
    {
        CAPTION_TIMING_SOURCE_ESTIMATED,
        CAPTION_TIMING_SOURCE_PROVIDER_NATIVE,
        CAPTION_TIMING_SOURCE_FORCED_ALIGNMENT,
    }
)

# ── Review event types ────────────────────────────────────────────────────────

CAPTION_EVENT_RUN_APPROVED: str = "run_approved"
CAPTION_EVENT_RUN_REJECTED: str = "run_rejected"
CAPTION_EVENT_CUE_REJECTED: str = "cue_rejected"

CAPTION_VALID_EVENT_TYPES: frozenset[str] = frozenset(
    {
        CAPTION_EVENT_RUN_APPROVED,
        CAPTION_EVENT_RUN_REJECTED,
        CAPTION_EVENT_CUE_REJECTED,
    }
)

# ── Default language ──────────────────────────────────────────────────────────

CAPTION_DEFAULT_LANGUAGE: str = "en-US"

# ── Stale temp-file age ───────────────────────────────────────────────────────

CAPTION_STALE_TEMP_AGE_S: float = 86400.0  # 24 hours
