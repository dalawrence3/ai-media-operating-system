"""Phase 6 M6.3B: Provider capability model and feature flags."""

from __future__ import annotations

from dataclasses import dataclass

from app.narration.constants import (
    PROVIDER_FEATURE_EMOTION_CONTROL,
    PROVIDER_FEATURE_MULTI_SPEAKER,
    PROVIDER_FEATURE_PHONEME_TIMESTAMPS,
    PROVIDER_FEATURE_SIMILARITY_BOOST,
    PROVIDER_FEATURE_SPEAKING_RATE,
    PROVIDER_FEATURE_SSML,
    PROVIDER_FEATURE_STABILITY,
    PROVIDER_FEATURE_STREAMING,
    PROVIDER_FEATURE_STYLE_TRANSFER,
    PROVIDER_FEATURE_WORD_TIMESTAMPS,
    PROVIDER_LANGUAGE_WILDCARD,
)


@dataclass(frozen=True)
class ProviderFeatureFlags:
    """Boolean capability flags declared by a TTS provider.

    Every flag defaults to False; providers opt in to each capability.
    Use ProviderCapabilities.has_feature(name) for string-keyed lookups.
    """

    supports_speaking_rate: bool = False
    supports_style_transfer: bool = False
    supports_emotion_control: bool = False
    supports_multi_speaker: bool = False
    supports_word_timestamps: bool = False
    supports_phoneme_timestamps: bool = False
    supports_streaming: bool = False
    supports_ssml: bool = False
    supports_stability: bool = False
    supports_similarity_boost: bool = False

    def as_dict(self) -> dict[str, bool]:
        return {
            PROVIDER_FEATURE_SPEAKING_RATE: self.supports_speaking_rate,
            PROVIDER_FEATURE_STYLE_TRANSFER: self.supports_style_transfer,
            PROVIDER_FEATURE_EMOTION_CONTROL: self.supports_emotion_control,
            PROVIDER_FEATURE_MULTI_SPEAKER: self.supports_multi_speaker,
            PROVIDER_FEATURE_WORD_TIMESTAMPS: self.supports_word_timestamps,
            PROVIDER_FEATURE_PHONEME_TIMESTAMPS: self.supports_phoneme_timestamps,
            PROVIDER_FEATURE_STREAMING: self.supports_streaming,
            PROVIDER_FEATURE_SSML: self.supports_ssml,
            PROVIDER_FEATURE_STABILITY: self.supports_stability,
            PROVIDER_FEATURE_SIMILARITY_BOOST: self.supports_similarity_boost,
        }

    def enabled_features(self) -> frozenset[str]:
        return frozenset(k for k, v in self.as_dict().items() if v)


@dataclass(frozen=True)
class ProviderCapabilities:
    """Static capability declaration for a TTS provider.

    Describes what the provider accepts.  Used by the validator and selector.
    All frozensets use exact string matching except supported_languages, which
    accepts PROVIDER_LANGUAGE_WILDCARD ("*") to mean "any language".
    """

    supported_output_formats: frozenset[str]
    supported_languages: frozenset[str]
    supported_sample_rates_hz: frozenset[int]
    min_speaking_rate: float
    max_speaking_rate: float
    max_characters_per_request: int
    feature_flags: ProviderFeatureFlags

    def accepts_language(self, language: str) -> bool:
        return (
            PROVIDER_LANGUAGE_WILDCARD in self.supported_languages
            or language in self.supported_languages
        )

    def accepts_output_format(self, fmt: str) -> bool:
        return fmt in self.supported_output_formats

    def accepts_sample_rate(self, sample_rate_hz: int) -> bool:
        return sample_rate_hz in self.supported_sample_rates_hz

    def accepts_speaking_rate(self, rate: float) -> bool:
        return self.min_speaking_rate <= rate <= self.max_speaking_rate

    def accepts_character_count(self, count: int) -> bool:
        return count <= self.max_characters_per_request

    def has_feature(self, feature_name: str) -> bool:
        """Return True if the named feature is supported.

        Unknown feature names always return False (future-proofing).
        """
        return self.feature_flags.as_dict().get(feature_name, False)

    def supported_features(self) -> frozenset[str]:
        return self.feature_flags.enabled_features()

    def to_dict(self) -> dict:
        return {
            "supported_output_formats": sorted(self.supported_output_formats),
            "supported_languages": sorted(self.supported_languages),
            "supported_sample_rates_hz": sorted(self.supported_sample_rates_hz),
            "min_speaking_rate": self.min_speaking_rate,
            "max_speaking_rate": self.max_speaking_rate,
            "max_characters_per_request": self.max_characters_per_request,
            "feature_flags": self.feature_flags.as_dict(),
        }
