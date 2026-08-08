"""Phase 6 M6.3B: Provider routing abstraction."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from app.narration.metadata import ProviderMetadata
from app.narration.protocol import TTSProvider, TTSRequest
from app.narration.registry import ProviderRegistry
from app.narration.selection import (
    DefaultProviderSelector,
    ProviderSelectionCriteria,
    ProviderSelector,
)


@dataclass
class RoutingRequest:
    """Describes a synthesis request that needs to be routed to a provider."""

    tts_request: TTSRequest
    criteria: ProviderSelectionCriteria = field(default_factory=ProviderSelectionCriteria)
    attempt: int = 0


@dataclass(frozen=True)
class RoutingResult:
    """The resolved provider, instance, and metadata for a routing decision."""

    provider_name: str
    provider: TTSProvider
    metadata: ProviderMetadata


@runtime_checkable
class ProviderRouter(Protocol):
    """Routes a RoutingRequest to a concrete TTSProvider."""

    def route(self, request: RoutingRequest) -> RoutingResult: ...


class DefaultProviderRouter:
    """Delegates selection to ProviderSelector; resolves from ProviderRegistry."""

    def __init__(
        self,
        registry: ProviderRegistry,
        selector: ProviderSelector | None = None,
    ) -> None:
        self._registry = registry
        self._selector: ProviderSelector = selector or DefaultProviderSelector()

    def route(self, request: RoutingRequest) -> RoutingResult:
        provider_name = self._selector.select(self._registry, request.criteria)
        provider = self._registry.get(provider_name)
        metadata = self._registry.get_metadata(provider_name)
        return RoutingResult(
            provider_name=provider_name,
            provider=provider,
            metadata=metadata,
        )
