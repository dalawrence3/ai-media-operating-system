"""Tests for Phase 6 M6.3B ProviderCapabilities and ProviderFeatureFlags."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from app.narration.capabilities import ProviderCapabilities, ProviderFeatureFlags
from app.narration.constants import (
    PROVIDER_FEATURE_NAMES,
    PROVIDER_FEATURE_SPEAKING_RATE,
    PROVIDER_FEATURE_STREAMING,
    PROVIDER_FEATURE_STYLE_TRANSFER,
    PROVIDER_LANGUAGE_WILDCARD,
)


def _caps(
    *,
    formats=frozenset({"wav"}),
    languages=frozenset({PROVIDER_LANGUAGE_WILDCARD}),
    sample_rates=frozenset({22050}),
    min_rate=0.5,
    max_rate=2.0,
    max_chars=100_000,
    flags=None,
) -> ProviderCapabilities:
    return ProviderCapabilities(
        supported_output_formats=formats,
        supported_languages=languages,
        supported_sample_rates_hz=sample_rates,
        min_speaking_rate=min_rate,
        max_speaking_rate=max_rate,
        max_characters_per_request=max_chars,
        feature_flags=flags or ProviderFeatureFlags(),
    )


# ── ProviderFeatureFlags ──────────────────────────────────────────────────────


def test_feature_flags_all_false_by_default() -> None:
    flags = ProviderFeatureFlags()
    assert not any(flags.as_dict().values())


def test_feature_flags_speaking_rate_true() -> None:
    flags = ProviderFeatureFlags(supports_speaking_rate=True)
    assert flags.supports_speaking_rate


def test_feature_flags_as_dict_contains_all_known_features() -> None:
    flags = ProviderFeatureFlags()
    assert set(flags.as_dict().keys()) == PROVIDER_FEATURE_NAMES


def test_feature_flags_enabled_features_empty_when_none() -> None:
    flags = ProviderFeatureFlags()
    assert flags.enabled_features() == frozenset()


def test_feature_flags_enabled_features_returns_enabled() -> None:
    flags = ProviderFeatureFlags(supports_speaking_rate=True, supports_ssml=True)
    enabled = flags.enabled_features()
    assert PROVIDER_FEATURE_SPEAKING_RATE in enabled
    assert "ssml" in enabled


def test_feature_flags_frozen() -> None:
    flags = ProviderFeatureFlags()
    with pytest.raises(FrozenInstanceError):
        flags.supports_speaking_rate = True  # type: ignore[misc]


# ── ProviderCapabilities — language ──────────────────────────────────────────


def test_accepts_language_wildcard() -> None:
    caps = _caps(languages=frozenset({PROVIDER_LANGUAGE_WILDCARD}))
    assert caps.accepts_language("en-US")
    assert caps.accepts_language("fr-FR")
    assert caps.accepts_language("zh-CN")


def test_accepts_language_explicit_match() -> None:
    caps = _caps(languages=frozenset({"en-US", "de-DE"}))
    assert caps.accepts_language("en-US")
    assert caps.accepts_language("de-DE")


def test_rejects_language_not_in_set() -> None:
    caps = _caps(languages=frozenset({"en-US"}))
    assert not caps.accepts_language("fr-FR")


# ── ProviderCapabilities — output format ─────────────────────────────────────


def test_accepts_output_format_present() -> None:
    caps = _caps(formats=frozenset({"wav", "mp3"}))
    assert caps.accepts_output_format("wav")
    assert caps.accepts_output_format("mp3")


def test_rejects_output_format_absent() -> None:
    caps = _caps(formats=frozenset({"wav"}))
    assert not caps.accepts_output_format("ogg")


# ── ProviderCapabilities — sample rate ───────────────────────────────────────


def test_accepts_sample_rate_present() -> None:
    caps = _caps(sample_rates=frozenset({22050, 44100}))
    assert caps.accepts_sample_rate(22050)
    assert caps.accepts_sample_rate(44100)


def test_rejects_sample_rate_absent() -> None:
    caps = _caps(sample_rates=frozenset({22050}))
    assert not caps.accepts_sample_rate(48000)


# ── ProviderCapabilities — speaking rate ─────────────────────────────────────


def test_accepts_speaking_rate_within_range() -> None:
    caps = _caps(min_rate=0.5, max_rate=2.0)
    assert caps.accepts_speaking_rate(0.5)
    assert caps.accepts_speaking_rate(1.0)
    assert caps.accepts_speaking_rate(2.0)


def test_rejects_speaking_rate_below_min() -> None:
    caps = _caps(min_rate=0.5, max_rate=2.0)
    assert not caps.accepts_speaking_rate(0.4)


def test_rejects_speaking_rate_above_max() -> None:
    caps = _caps(min_rate=0.5, max_rate=2.0)
    assert not caps.accepts_speaking_rate(2.1)


# ── ProviderCapabilities — character count ───────────────────────────────────


def test_accepts_character_count_at_limit() -> None:
    caps = _caps(max_chars=500)
    assert caps.accepts_character_count(500)


def test_rejects_character_count_over_limit() -> None:
    caps = _caps(max_chars=500)
    assert not caps.accepts_character_count(501)


# ── ProviderCapabilities — feature lookup ────────────────────────────────────


def test_has_feature_true_when_enabled() -> None:
    flags = ProviderFeatureFlags(supports_style_transfer=True)
    caps = _caps(flags=flags)
    assert caps.has_feature(PROVIDER_FEATURE_STYLE_TRANSFER)


def test_has_feature_false_when_disabled() -> None:
    caps = _caps()
    assert not caps.has_feature(PROVIDER_FEATURE_STYLE_TRANSFER)


def test_has_feature_false_for_unknown_name() -> None:
    caps = _caps()
    assert not caps.has_feature("nonexistent_feature")


def test_supported_features_delegates_to_flags() -> None:
    flags = ProviderFeatureFlags(supports_speaking_rate=True, supports_streaming=True)
    caps = _caps(flags=flags)
    assert PROVIDER_FEATURE_SPEAKING_RATE in caps.supported_features()
    assert PROVIDER_FEATURE_STREAMING in caps.supported_features()


# ── to_dict ───────────────────────────────────────────────────────────────────


def test_to_dict_has_required_keys() -> None:
    caps = _caps()
    d = caps.to_dict()
    assert "supported_output_formats" in d
    assert "supported_languages" in d
    assert "supported_sample_rates_hz" in d
    assert "min_speaking_rate" in d
    assert "max_speaking_rate" in d
    assert "max_characters_per_request" in d
    assert "feature_flags" in d


def test_to_dict_formats_sorted() -> None:
    caps = _caps(formats=frozenset({"wav", "mp3", "ogg"}))
    assert caps.to_dict()["supported_output_formats"] == ["mp3", "ogg", "wav"]
