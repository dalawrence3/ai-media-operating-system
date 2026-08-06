"""Phase 6 M6.3B: Provider response cache abstraction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from app.narration.protocol import TTSResponse


@dataclass(frozen=True)
class CacheKey:
    """Deterministic identity for a synthesis request.

    All fields that affect the audio output must be included so that
    two different requests never share a cache entry.
    """

    text_hash: str
    provider: str
    model: str
    voice_id: str
    language: str
    speaking_rate: float
    output_format: str
    sample_rate_hz: int
    style: str | None
    stability: float | None
    similarity_boost: float | None
    settings_json: str


@dataclass(frozen=True)
class CacheEntry:
    """A cached TTSResponse plus the key and timestamp."""

    key: CacheKey
    response: TTSResponse
    cached_at: datetime


@runtime_checkable
class ProviderResponseCache(Protocol):
    """Read-through cache for TTS synthesis responses.

    NoOpResponseCache is the default — a cache miss on every lookup.
    Live caching implementations are deferred to a future milestone.
    """

    def get(self, key: CacheKey) -> CacheEntry | None: ...

    def put(self, key: CacheKey, response: TTSResponse) -> None: ...

    def invalidate(self, key: CacheKey) -> bool: ...

    def size(self) -> int: ...


class NoOpResponseCache:
    """Always-miss cache — every get returns None.

    Used as the default until a caching backend is configured.
    """

    def get(self, key: CacheKey) -> CacheEntry | None:
        return None

    def put(self, key: CacheKey, response: TTSResponse) -> None:
        pass

    def invalidate(self, key: CacheKey) -> bool:
        return False

    def size(self) -> int:
        return 0


class InMemoryResponseCache:
    """Bounded in-memory LRU-style cache for synthesis responses.

    Evicts oldest entry when capacity is exceeded.
    """

    def __init__(self, max_size: int = 128) -> None:
        if max_size <= 0:
            raise ValueError(f"max_size must be positive, got {max_size}")
        self._max_size = max_size
        self._store: dict[CacheKey, CacheEntry] = {}
        self._order: list[CacheKey] = []

    def get(self, key: CacheKey) -> CacheEntry | None:
        entry = self._store.get(key)
        if entry is not None:
            self._order.remove(key)
            self._order.append(key)
        return entry

    def put(self, key: CacheKey, response: TTSResponse) -> None:
        if key in self._store:
            self._order.remove(key)
        elif len(self._store) >= self._max_size:
            oldest = self._order.pop(0)
            del self._store[oldest]
        entry = CacheEntry(
            key=key, response=response, cached_at=datetime.now(tz=UTC)
        )
        self._store[key] = entry
        self._order.append(key)

    def invalidate(self, key: CacheKey) -> bool:
        if key in self._store:
            del self._store[key]
            self._order.remove(key)
            return True
        return False

    def size(self) -> int:
        return len(self._store)
