"""Tests for Phase 6 M6.3B ProviderFactory, ProviderLoader, RegisteringProviderLoader."""

from __future__ import annotations

import pytest

from app.narration.errors import ProviderFactoryError
from app.narration.factory import (
    DefaultProviderFactory,
    DefaultProviderLoader,
    ProviderFactory,
    ProviderLoader,
    RegisteringProviderLoader,
)
from app.narration.fake import FAKE_METADATA, FAKE_PROVIDER_NAME, FakeTTSProvider
from app.narration.protocol import TTSProvider
from app.narration.registry import ProviderRegistry

# ── DefaultProviderFactory ────────────────────────────────────────────────────


def test_factory_satisfies_protocol() -> None:
    assert isinstance(DefaultProviderFactory(), ProviderFactory)


def test_factory_creates_fake_provider() -> None:
    factory = DefaultProviderFactory()
    provider = factory.create("fake")
    assert isinstance(provider, FakeTTSProvider)


def test_factory_creates_tts_provider() -> None:
    factory = DefaultProviderFactory()
    provider = factory.create("fake")
    assert isinstance(provider, TTSProvider)


def test_factory_unknown_provider_raises() -> None:
    factory = DefaultProviderFactory()
    with pytest.raises(ProviderFactoryError, match="google"):
        factory.create("google")


def test_factory_creates_elevenlabs_provider() -> None:
    from app.narration.providers.elevenlabs import ElevenLabsTTSProvider

    factory = DefaultProviderFactory()
    provider = factory.create("elevenlabs")
    assert isinstance(provider, ElevenLabsTTSProvider)
    assert isinstance(provider, TTSProvider)


def test_factory_creates_fresh_instance_each_call() -> None:
    factory = DefaultProviderFactory()
    p1 = factory.create("fake")
    p2 = factory.create("fake")
    assert p1 is not p2


def test_factory_accepts_config_kwarg() -> None:
    factory = DefaultProviderFactory()
    config = None
    provider = factory.create("fake", config)
    assert provider is not None


# ── DefaultProviderLoader ─────────────────────────────────────────────────────


def test_loader_satisfies_protocol() -> None:
    assert isinstance(DefaultProviderLoader(), ProviderLoader)


def test_loader_returns_fake_provider_and_metadata() -> None:
    loader = DefaultProviderLoader()
    provider, metadata = loader.load("fake")
    assert isinstance(provider, FakeTTSProvider)
    assert metadata is FAKE_METADATA


def test_loader_unknown_provider_raises() -> None:
    loader = DefaultProviderLoader()
    with pytest.raises(ProviderFactoryError):
        loader.load("azure")


def test_loader_uses_injected_factory() -> None:
    """DefaultProviderLoader delegates creation to the injected factory."""
    factory = DefaultProviderFactory()
    loader = DefaultProviderLoader(factory=factory)
    provider, _ = loader.load("fake")
    assert isinstance(provider, FakeTTSProvider)


# ── RegisteringProviderLoader ─────────────────────────────────────────────────


def test_registering_loader_adds_to_registry() -> None:
    registry = ProviderRegistry()
    loader = RegisteringProviderLoader(registry)
    loader.load_and_register(FAKE_PROVIDER_NAME)
    assert registry.is_registered(FAKE_PROVIDER_NAME)


def test_registering_loader_returns_provider_and_metadata() -> None:
    registry = ProviderRegistry()
    loader = RegisteringProviderLoader(registry)
    provider, metadata = loader.load_and_register(FAKE_PROVIDER_NAME)
    assert isinstance(provider, FakeTTSProvider)
    assert metadata is FAKE_METADATA


def test_registering_loader_registered_provider_works() -> None:
    registry = ProviderRegistry()
    loader = RegisteringProviderLoader(registry)
    loader.load_and_register(FAKE_PROVIDER_NAME)
    provider = registry.get(FAKE_PROVIDER_NAME)
    assert provider.provider_name == FAKE_PROVIDER_NAME


def test_registering_loader_unknown_provider_raises() -> None:
    registry = ProviderRegistry()
    loader = RegisteringProviderLoader(registry)
    with pytest.raises(ProviderFactoryError):
        loader.load_and_register("google")
