"""Tests for Phase 6 M6.3B ProviderRegistry."""

from __future__ import annotations

import pytest

from app.narration.errors import UnknownProviderError
from app.narration.fake import FAKE_METADATA, FAKE_PROVIDER_NAME, FakeTTSProvider
from app.narration.registry import ProviderRegistry, get_default_provider_registry


def _registry_with_fake() -> ProviderRegistry:
    r = ProviderRegistry()
    r.register(FakeTTSProvider(), FAKE_METADATA)
    return r


# ── register / get ────────────────────────────────────────────────────────────


def test_register_and_get_provider() -> None:
    r = _registry_with_fake()
    provider = r.get(FAKE_PROVIDER_NAME)
    assert provider.provider_name == FAKE_PROVIDER_NAME


def test_register_and_get_metadata() -> None:
    r = _registry_with_fake()
    meta = r.get_metadata(FAKE_PROVIDER_NAME)
    assert meta is FAKE_METADATA


def test_get_unknown_provider_raises() -> None:
    r = ProviderRegistry()
    with pytest.raises(UnknownProviderError, match="ghost"):
        r.get("ghost")


def test_get_metadata_unknown_raises() -> None:
    r = ProviderRegistry()
    with pytest.raises(UnknownProviderError):
        r.get_metadata("ghost")


def test_unknown_provider_error_stores_name() -> None:
    r = ProviderRegistry()
    with pytest.raises(UnknownProviderError) as exc_info:
        r.get("missing")
    assert exc_info.value.provider_name == "missing"


# ── discover ──────────────────────────────────────────────────────────────────


def test_discover_empty_when_nothing_registered() -> None:
    r = ProviderRegistry()
    assert r.discover() == []


def test_discover_returns_registered_name() -> None:
    r = _registry_with_fake()
    assert FAKE_PROVIDER_NAME in r.discover()


def test_discover_returns_sorted_names() -> None:
    from app.narration.capabilities import ProviderCapabilities, ProviderFeatureFlags
    from app.narration.constants import PROVIDER_LANGUAGE_WILDCARD
    from app.narration.metadata import ProviderMetadata

    def _meta(name: str) -> ProviderMetadata:
        caps = ProviderCapabilities(
            supported_output_formats=frozenset({"wav"}),
            supported_languages=frozenset({PROVIDER_LANGUAGE_WILDCARD}),
            supported_sample_rates_hz=frozenset({22050}),
            min_speaking_rate=0.5,
            max_speaking_rate=2.0,
            max_characters_per_request=10_000,
            feature_flags=ProviderFeatureFlags(),
        )
        return ProviderMetadata(
            provider_name=name,
            provider_version="1.0.0",
            model_id=f"{name}/model",
            api_version=None,
            sdk_name=None,
            sdk_version=None,
            capabilities=caps,
            feature_flags=ProviderFeatureFlags(),
        )

    r = ProviderRegistry()
    r.register(FakeTTSProvider(), _meta("zzz"))
    r.register(FakeTTSProvider(), _meta("aaa"))
    assert r.discover() == ["aaa", "zzz"]


# ── is_registered ─────────────────────────────────────────────────────────────


def test_is_registered_true_after_register() -> None:
    r = _registry_with_fake()
    assert r.is_registered(FAKE_PROVIDER_NAME)


def test_is_registered_false_before_register() -> None:
    r = ProviderRegistry()
    assert not r.is_registered(FAKE_PROVIDER_NAME)


# ── __len__ ───────────────────────────────────────────────────────────────────


def test_len_empty() -> None:
    assert len(ProviderRegistry()) == 0


def test_len_after_register() -> None:
    r = _registry_with_fake()
    assert len(r) == 1


# ── register overwrites ───────────────────────────────────────────────────────


def test_register_overwrites_same_name() -> None:
    r = ProviderRegistry()
    p1 = FakeTTSProvider(words_per_minute=100)
    p2 = FakeTTSProvider(words_per_minute=200)
    r.register(p1, FAKE_METADATA)
    r.register(p2, FAKE_METADATA)
    assert r.get(FAKE_PROVIDER_NAME) is p2
    assert len(r) == 1


# ── get_default_provider_registry ────────────────────────────────────────────


def test_default_registry_has_fake() -> None:
    r = get_default_provider_registry()
    assert r.is_registered(FAKE_PROVIDER_NAME)


def test_default_registry_fake_provider_works() -> None:
    r = get_default_provider_registry()
    provider = r.get(FAKE_PROVIDER_NAME)
    assert isinstance(provider, FakeTTSProvider)


def test_default_registry_returns_new_instance_each_call() -> None:
    r1 = get_default_provider_registry()
    r2 = get_default_provider_registry()
    assert r1 is not r2
