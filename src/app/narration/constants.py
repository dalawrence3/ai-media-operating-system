"""Phase 6 M6.2 narration constants.

Every version string is frozen: changing any value changes the narration
input hash and forces a new synthesis run for affected segments.
"""

from __future__ import annotations

# ── Version strings (all feed into input hash) ──────────────────────────────

NARRATION_SCHEMA_VERSION: str = "Narration-v1"
NARRATION_ALGORITHM_VERSION: str = "narration-segment-v1"

# ── Rejection reason codes ───────────────────────────────────────────────────

NARRATION_REJECTION_REASON_CODES: frozenset[str] = frozenset(
    {
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
)

NARRATION_REJECTION_REASON_CODE_REQUIRING_NOTES: str = "other"

# ── Severity ─────────────────────────────────────────────────────────────────

NARRATION_SEVERITY_MIN: int = 1
NARRATION_SEVERITY_MAX: int = 5

# ── Audio defaults ───────────────────────────────────────────────────────────

NARRATION_DEFAULT_OUTPUT_FORMAT: str = "wav"
NARRATION_DEFAULT_SAMPLE_RATE_HZ: int = 22050
NARRATION_DEFAULT_LANGUAGE: str = "en-US"
NARRATION_DEFAULT_SPEAKING_RATE: float = 1.0

# ── Duration deviation warning threshold ─────────────────────────────────────

NARRATION_DURATION_DEVIATION_THRESHOLD: float = 0.5  # 50% of estimated_duration_s

# ── Stale temp-file age ──────────────────────────────────────────────────────

NARRATION_STALE_TEMP_AGE_S: float = 86400.0  # 24 hours
