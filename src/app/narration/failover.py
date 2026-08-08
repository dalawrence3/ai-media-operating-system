"""Phase 6 M6.3B: Provider failover abstraction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.narration.registry import ProviderRegistry


@dataclass
class FailoverContext:
    """State passed to a FailoverPolicy when a synthesis call fails."""

    original_provider: str
    attempt: int
    last_error: str | None = None


@runtime_checkable
class FailoverPolicy(Protocol):
    """Decides which provider to try next after a failure.

    Returns None if no further candidates exist (caller should propagate error).
    """

    def next_provider(
        self,
        registry: ProviderRegistry,
        context: FailoverContext,
    ) -> str | None: ...


class NoFailoverPolicy:
    """Never fails over — always returns None.

    Default policy when only one provider is configured.
    """

    def next_provider(
        self,
        registry: ProviderRegistry,
        context: FailoverContext,
    ) -> str | None:
        return None


class ProviderFailoverChain:
    """Tries providers in a fixed ordered list.

    Skips the original provider and any unregistered providers.
    context.attempt is 1-based: attempt=1 returns the first alternative,
    attempt=2 returns the second, and so on.  attempt=0 always returns None
    (no failover needed yet).
    """

    def __init__(
        self,
        providers: list[str],
        max_attempts: int = 3,
    ) -> None:
        if not providers:
            raise ValueError("providers list must not be empty")
        self._providers = list(providers)
        self._max_attempts = max_attempts

    def next_provider(
        self,
        registry: ProviderRegistry,
        context: FailoverContext,
    ) -> str | None:
        if context.attempt >= self._max_attempts:
            return None

        alternatives = [
            name for name in self._providers
            if name != context.original_provider and registry.is_registered(name)
        ]
        idx = context.attempt - 1
        if idx < 0 or idx >= len(alternatives):
            return None
        return alternatives[idx]

    @property
    def chain(self) -> list[str]:
        return list(self._providers)

    @property
    def max_attempts(self) -> int:
        return self._max_attempts
