"""Phase 6 M6.3B: Provider registry — central hub for registered TTS providers."""

from __future__ import annotations

from app.narration.errors import UnknownProviderError
from app.narration.metadata import ProviderMetadata
from app.narration.protocol import TTSProvider


class ProviderRegistry:
    """Maps provider_name → (TTSProvider instance, ProviderMetadata).

    Provider discovery: call discover() to enumerate registered names.
    Thread safety: not guaranteed; intended for single-threaded use.
    """

    def __init__(self) -> None:
        self._providers: dict[str, TTSProvider] = {}
        self._metadata: dict[str, ProviderMetadata] = {}

    def register(self, provider: TTSProvider, metadata: ProviderMetadata) -> None:
        name = metadata.provider_name
        self._providers[name] = provider
        self._metadata[name] = metadata

    def get(self, provider_name: str) -> TTSProvider:
        try:
            return self._providers[provider_name]
        except KeyError:
            raise UnknownProviderError(provider_name) from None

    def get_metadata(self, provider_name: str) -> ProviderMetadata:
        try:
            return self._metadata[provider_name]
        except KeyError:
            raise UnknownProviderError(provider_name) from None

    def discover(self) -> list[str]:
        """Return sorted list of all registered provider names."""
        return sorted(self._providers.keys())

    def is_registered(self, provider_name: str) -> bool:
        return provider_name in self._providers

    def __len__(self) -> int:
        return len(self._providers)


def get_default_provider_registry() -> ProviderRegistry:
    """Return a ProviderRegistry pre-loaded with FakeTTSProvider.

    Live providers (e.g. ElevenLabs) are NOT registered here because they require
    credentials and explicit initialization.  Register them via DefaultProviderLoader
    or DefaultProviderFactory when a live provider is explicitly requested.
    """
    from app.narration.fake import FAKE_METADATA, FakeTTSProvider

    registry = ProviderRegistry()
    registry.register(FakeTTSProvider(), FAKE_METADATA)
    return registry
