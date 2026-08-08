"""Tests for Phase 6 M6.3B provider response cache."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from app.narration.cache import (
    CacheKey,
    InMemoryResponseCache,
    NoOpResponseCache,
    ProviderResponseCache,
)
from app.narration.fake import (
    FAKE_MODEL_NAME,
    FAKE_PROVIDER_NAME,
    FAKE_VOICE_ID,
    FakeTTSProvider,
)
from app.narration.protocol import TTSRequest, TTSResponse


def _key(**overrides) -> CacheKey:
    defaults = dict(
        text_hash="abc123",
        provider=FAKE_PROVIDER_NAME,
        model=FAKE_MODEL_NAME,
        voice_id=FAKE_VOICE_ID,
        language="en-US",
        speaking_rate=1.0,
        output_format="wav",
        sample_rate_hz=22050,
        style=None,
        stability=None,
        similarity_boost=None,
        settings_json="{}",
    )
    defaults.update(overrides)
    return CacheKey(**defaults)


def _response() -> TTSResponse:
    provider = FakeTTSProvider()
    return provider.synthesize(
        TTSRequest(
            text="Hello",
            provider=FAKE_PROVIDER_NAME,
            model=FAKE_MODEL_NAME,
            voice_id=FAKE_VOICE_ID,
            language="en-US",
            speaking_rate=1.0,
            output_format="wav",
            sample_rate_hz=22050,
        )
    )


# ── CacheKey ──────────────────────────────────────────────────────────────────


def test_cache_key_is_frozen() -> None:
    key = _key()
    with pytest.raises(FrozenInstanceError):
        key.text_hash = "other"  # type: ignore[misc]


def test_cache_key_equality() -> None:
    k1 = _key(text_hash="abc")
    k2 = _key(text_hash="abc")
    assert k1 == k2


def test_cache_key_inequality_on_field_change() -> None:
    k1 = _key(text_hash="abc")
    k2 = _key(text_hash="xyz")
    assert k1 != k2


def test_cache_key_hashable() -> None:
    key = _key()
    d = {key: "value"}
    assert d[key] == "value"


# ── NoOpResponseCache ─────────────────────────────────────────────────────────


def test_noop_satisfies_protocol() -> None:
    assert isinstance(NoOpResponseCache(), ProviderResponseCache)


def test_noop_get_always_returns_none() -> None:
    cache = NoOpResponseCache()
    assert cache.get(_key()) is None


def test_noop_put_is_silent() -> None:
    cache = NoOpResponseCache()
    cache.put(_key(), _response())  # should not raise


def test_noop_invalidate_returns_false() -> None:
    cache = NoOpResponseCache()
    assert not cache.invalidate(_key())


def test_noop_size_is_zero() -> None:
    cache = NoOpResponseCache()
    cache.put(_key(), _response())
    assert cache.size() == 0


# ── InMemoryResponseCache ─────────────────────────────────────────────────────


def test_in_memory_satisfies_protocol() -> None:
    assert isinstance(InMemoryResponseCache(), ProviderResponseCache)


def test_in_memory_get_miss_returns_none() -> None:
    cache = InMemoryResponseCache()
    assert cache.get(_key()) is None


def test_in_memory_put_then_get_hit() -> None:
    cache = InMemoryResponseCache()
    key = _key()
    resp = _response()
    cache.put(key, resp)
    entry = cache.get(key)
    assert entry is not None
    assert entry.response is resp


def test_in_memory_entry_key_matches() -> None:
    cache = InMemoryResponseCache()
    key = _key()
    cache.put(key, _response())
    assert cache.get(key).key == key


def test_in_memory_entry_cached_at_set() -> None:
    cache = InMemoryResponseCache()
    cache.put(_key(), _response())
    entry = cache.get(_key())
    assert entry.cached_at is not None


def test_in_memory_size_increases() -> None:
    cache = InMemoryResponseCache()
    cache.put(_key(text_hash="a"), _response())
    cache.put(_key(text_hash="b"), _response())
    assert cache.size() == 2


def test_in_memory_invalidate_removes_entry() -> None:
    cache = InMemoryResponseCache()
    key = _key()
    cache.put(key, _response())
    assert cache.invalidate(key)
    assert cache.get(key) is None


def test_in_memory_invalidate_absent_returns_false() -> None:
    cache = InMemoryResponseCache()
    assert not cache.invalidate(_key())


def test_in_memory_evicts_oldest_at_capacity() -> None:
    cache = InMemoryResponseCache(max_size=2)
    k1 = _key(text_hash="1")
    k2 = _key(text_hash="2")
    k3 = _key(text_hash="3")
    cache.put(k1, _response())
    cache.put(k2, _response())
    cache.put(k3, _response())
    assert cache.size() == 2
    assert cache.get(k1) is None  # evicted


def test_in_memory_max_size_one() -> None:
    cache = InMemoryResponseCache(max_size=1)
    k1 = _key(text_hash="a")
    k2 = _key(text_hash="b")
    cache.put(k1, _response())
    cache.put(k2, _response())
    assert cache.size() == 1
    assert cache.get(k2) is not None


def test_in_memory_invalid_max_size_raises() -> None:
    with pytest.raises(ValueError):
        InMemoryResponseCache(max_size=0)
