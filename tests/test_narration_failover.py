"""Tests for Phase 6 M6.3B FailoverPolicy implementations."""

from __future__ import annotations

import pytest

from app.narration.failover import (
    FailoverContext,
    FailoverPolicy,
    NoFailoverPolicy,
    ProviderFailoverChain,
)
from app.narration.fake import FAKE_PROVIDER_NAME, FakeTTSProvider
from app.narration.registry import ProviderRegistry


def _registry(*names: str) -> ProviderRegistry:
    r = ProviderRegistry()
    for name in names:
        from app.narration.capabilities import ProviderCapabilities, ProviderFeatureFlags
        from app.narration.constants import PROVIDER_LANGUAGE_WILDCARD
        from app.narration.metadata import ProviderMetadata

        caps = ProviderCapabilities(
            supported_output_formats=frozenset({"wav"}),
            supported_languages=frozenset({PROVIDER_LANGUAGE_WILDCARD}),
            supported_sample_rates_hz=frozenset({22050}),
            min_speaking_rate=0.5,
            max_speaking_rate=2.0,
            max_characters_per_request=10_000,
            feature_flags=ProviderFeatureFlags(),
        )
        meta = ProviderMetadata(
            provider_name=name,
            provider_version="1.0.0",
            model_id=f"{name}/model",
            api_version=None,
            sdk_name=None,
            sdk_version=None,
            capabilities=caps,
            feature_flags=ProviderFeatureFlags(),
        )
        r.register(FakeTTSProvider(), meta)
    return r


# ── NoFailoverPolicy ──────────────────────────────────────────────────────────


def test_no_failover_satisfies_protocol() -> None:
    assert isinstance(NoFailoverPolicy(), FailoverPolicy)


def test_no_failover_always_returns_none() -> None:
    policy = NoFailoverPolicy()
    ctx = FailoverContext(original_provider=FAKE_PROVIDER_NAME, attempt=0)
    assert policy.next_provider(_registry(FAKE_PROVIDER_NAME), ctx) is None


def test_no_failover_regardless_of_attempt() -> None:
    policy = NoFailoverPolicy()
    r = _registry(FAKE_PROVIDER_NAME)
    for attempt in range(5):
        ctx = FailoverContext(original_provider=FAKE_PROVIDER_NAME, attempt=attempt)
        assert policy.next_provider(r, ctx) is None


# ── ProviderFailoverChain — construction ──────────────────────────────────────


def test_empty_providers_raises() -> None:
    with pytest.raises(ValueError, match="empty"):
        ProviderFailoverChain([])


def test_chain_stores_providers() -> None:
    chain = ProviderFailoverChain(["a", "b"])
    assert chain.chain == ["a", "b"]


def test_chain_stores_max_attempts() -> None:
    chain = ProviderFailoverChain(["a"], max_attempts=5)
    assert chain.max_attempts == 5


# ── ProviderFailoverChain — next_provider ────────────────────────────────────


def test_chain_attempt_zero_returns_none() -> None:
    r = _registry("fake", "backup")
    chain = ProviderFailoverChain(["fake", "backup"])
    ctx = FailoverContext(original_provider="fake", attempt=0)
    assert chain.next_provider(r, ctx) is None


def test_chain_skips_original_and_returns_backup_on_attempt_one() -> None:
    r = _registry("fake", "backup")
    chain = ProviderFailoverChain(["fake", "backup"])
    ctx = FailoverContext(original_provider="fake", attempt=1)
    assert chain.next_provider(r, ctx) == "backup"


def test_chain_cycles_through_alternatives() -> None:
    r = _registry("orig", "b1", "b2")
    chain = ProviderFailoverChain(["orig", "b1", "b2"])
    r1 = chain.next_provider(r, FailoverContext(original_provider="orig", attempt=1))
    r2 = chain.next_provider(r, FailoverContext(original_provider="orig", attempt=2))
    assert r1 == "b1"
    assert r2 == "b2"


def test_chain_returns_none_when_max_attempts_reached() -> None:
    r = _registry("fake", "backup")
    chain = ProviderFailoverChain(["fake", "backup"], max_attempts=1)
    ctx = FailoverContext(original_provider="fake", attempt=1)
    assert chain.next_provider(r, ctx) is None


def test_chain_returns_none_when_all_unregistered() -> None:
    r = _registry("fake")
    chain = ProviderFailoverChain(["fake", "other"])
    ctx = FailoverContext(original_provider="fake", attempt=0)
    # "other" is in chain but not in registry
    # "fake" is skipped as original
    result = chain.next_provider(r, ctx)
    assert result is None


def test_chain_satisfies_protocol() -> None:
    assert isinstance(ProviderFailoverChain(["a"]), FailoverPolicy)
