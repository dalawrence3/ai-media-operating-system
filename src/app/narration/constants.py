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

# ── Provider feature flag names (M6.3B) ──────────────────────────────────────
# String constants for ProviderCapabilities.has_feature() and selection criteria.

PROVIDER_FEATURE_STYLE_TRANSFER: str = "style_transfer"
PROVIDER_FEATURE_EMOTION_CONTROL: str = "emotion_control"
PROVIDER_FEATURE_MULTI_SPEAKER: str = "multi_speaker"
PROVIDER_FEATURE_WORD_TIMESTAMPS: str = "word_timestamps"
PROVIDER_FEATURE_PHONEME_TIMESTAMPS: str = "phoneme_timestamps"
PROVIDER_FEATURE_STREAMING: str = "streaming"
PROVIDER_FEATURE_SSML: str = "ssml"
PROVIDER_FEATURE_SPEAKING_RATE: str = "speaking_rate"
PROVIDER_FEATURE_STABILITY: str = "stability"
PROVIDER_FEATURE_SIMILARITY_BOOST: str = "similarity_boost"

# Additional feature flags (M6.3C).
PROVIDER_FEATURE_ALIGNMENT: str = "alignment"
PROVIDER_FEATURE_SEED: str = "seed"
PROVIDER_FEATURE_VOICE_CLONING: str = "voice_cloning"
PROVIDER_FEATURE_PRONUNCIATION_DICTIONARY: str = "pronunciation_dictionary"

# All known feature flag names — kept as a frozenset for validation.
PROVIDER_FEATURE_NAMES: frozenset[str] = frozenset(
    {
        PROVIDER_FEATURE_STYLE_TRANSFER,
        PROVIDER_FEATURE_EMOTION_CONTROL,
        PROVIDER_FEATURE_MULTI_SPEAKER,
        PROVIDER_FEATURE_WORD_TIMESTAMPS,
        PROVIDER_FEATURE_PHONEME_TIMESTAMPS,
        PROVIDER_FEATURE_STREAMING,
        PROVIDER_FEATURE_SSML,
        PROVIDER_FEATURE_SPEAKING_RATE,
        PROVIDER_FEATURE_STABILITY,
        PROVIDER_FEATURE_SIMILARITY_BOOST,
        PROVIDER_FEATURE_ALIGNMENT,
        PROVIDER_FEATURE_SEED,
        PROVIDER_FEATURE_VOICE_CLONING,
        PROVIDER_FEATURE_PRONUNCIATION_DICTIONARY,
    }
)

# Sentinel value for supported_languages: provider accepts any BCP-47 code.
PROVIDER_LANGUAGE_WILDCARD: str = "*"

# Version string for M6.3B provider infrastructure layer.
PROVIDER_INFRASTRUCTURE_VERSION: str = "provider-infra-v1"
