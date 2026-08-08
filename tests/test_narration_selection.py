"""Tests for Phase 6 M6.3B ProviderSelector."""

from __future__ import annotations

import pytest

from app.narration.constants import PROVIDER_FEATURE_STREAMING
from app.narration.errors import ProviderSelectionError
from app.narration.fake import FAKE_METADATA, FAKE_PROVIDER_NAME, FakeTTSProvider
from app.narration.registry import ProviderRegistry, get_default_provider_registry
from app.narration.selection import DefaultProviderSelector, ProviderSelectionCriteria


def _registry() -> ProviderRegistry:
    r = ProviderRegistry()
    r.register(FakeTTSProvider(), FAKE_METADATA)
    return r


def _select(
    registry: ProviderRegistry | None = None, **criteria_kwargs
) -> str:
    reg = registry if registry is not None else _registry()
    selector = DefaultProviderSelector()
    return selector.select(reg, ProviderSelectionCriteria(**criteria_kwargs))


# ── Empty registry ────────────────────────────────────────────────────────────


def test_empty_registry_raises() -> None:
    with pytest.raises(ProviderSelectionError, match="No TTS providers"):
        _select(registry=ProviderRegistry())


# ── Preferred provider ────────────────────────────────────────────────────────


def test_preferred_provider_returned_when_registered() -> None:
    result = _select(preferred_provider=FAKE_PROVIDER_NAME)
    assert result == FAKE_PROVIDER_NAME


def test_preferred_provider_not_registered_falls_through() -> None:
    result = _select(preferred_provider="nonexistent")
    assert result == FAKE_PROVIDER_NAME


# ── Language filter ───────────────────────────────────────────────────────────


def test_wildcard_language_accepts_any() -> None:
    result = _select(required_language="fr-FR")
    assert result == FAKE_PROVIDER_NAME


def test_no_language_filter_selects_first() -> None:
    result = _select()
    assert result == FAKE_PROVIDER_NAME


def test_specific_language_no_match_raises(monkeypatch) -> None:
    from app.narration.capabilities import ProviderCapabilities
    from app.narration.metadata import ProviderMetadata

    caps = FAKE_METADATA.capabilities
    restricted_caps = ProviderCapabilities(
        supported_output_formats=caps.supported_output_formats,
        supported_languages=frozenset({"en-US"}),
        supported_sample_rates_hz=caps.supported_sample_rates_hz,
        min_speaking_rate=caps.min_speaking_rate,
        max_speaking_rate=caps.max_speaking_rate,
        max_characters_per_request=caps.max_characters_per_request,
        feature_flags=caps.feature_flags,
    )
    restricted_meta = ProviderMetadata(
        provider_name=FAKE_PROVIDER_NAME,
        provider_version=FAKE_METADATA.provider_version,
        model_id=FAKE_METADATA.model_id,
        api_version=None,
        sdk_name=None,
        sdk_version=None,
        capabilities=restricted_caps,
        feature_flags=caps.feature_flags,
    )
    r = ProviderRegistry()
    r.register(FakeTTSProvider(), restricted_meta)
    selector = DefaultProviderSelector()
    with pytest.raises(ProviderSelectionError):
        selector.select(r, ProviderSelectionCriteria(required_language="fr-FR"))


# ── Output format filter ──────────────────────────────────────────────────────


def test_required_format_wav_matches_fake() -> None:
    result = _select(required_output_format="wav")
    assert result == FAKE_PROVIDER_NAME


def test_required_format_no_match_raises() -> None:
    with pytest.raises(ProviderSelectionError):
        _select(required_output_format="flac")


# ── Sample rate filter ────────────────────────────────────────────────────────


def test_required_sample_rate_match() -> None:
    result = _select(required_sample_rate_hz=22050)
    assert result == FAKE_PROVIDER_NAME


def test_required_sample_rate_no_match_raises() -> None:
    with pytest.raises(ProviderSelectionError):
        _select(required_sample_rate_hz=999_999)


# ── Feature filter ────────────────────────────────────────────────────────────


def test_required_feature_present_matches() -> None:
    from app.narration.constants import PROVIDER_FEATURE_SPEAKING_RATE
    result = _select(required_features=frozenset({PROVIDER_FEATURE_SPEAKING_RATE}))
    assert result == FAKE_PROVIDER_NAME


def test_required_feature_absent_raises() -> None:
    with pytest.raises(ProviderSelectionError):
        _select(required_features=frozenset({PROVIDER_FEATURE_STREAMING}))


# ── Default registry integration ──────────────────────────────────────────────


def test_select_from_default_registry() -> None:
    r = get_default_provider_registry()
    selector = DefaultProviderSelector()
    result = selector.select(r, ProviderSelectionCriteria())
    assert result == FAKE_PROVIDER_NAME
