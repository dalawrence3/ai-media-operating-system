"""Phase 6 M6.3B: Provider factory and loader."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.narration.config import ProviderConfig
from app.narration.errors import ProviderFactoryError
from app.narration.metadata import ProviderMetadata
from app.narration.protocol import TTSProvider
from app.narration.registry import ProviderRegistry


@runtime_checkable
class ProviderFactory(Protocol):
    """Creates a TTSProvider instance given a name and optional config."""

    def create(
        self,
        provider_name: str,
        config: ProviderConfig | None = None,
    ) -> TTSProvider: ...


@runtime_checkable
class ProviderLoader(Protocol):
    """Loads a provider and its metadata in one call."""

    def load(self, provider_name: str) -> tuple[TTSProvider, ProviderMetadata]: ...


class DefaultProviderFactory:
    """Creates providers from the built-in catalogue."""

    def create(
        self,
        provider_name: str,
        config: ProviderConfig | None = None,
    ) -> TTSProvider:
        if provider_name == "fake":
            from app.narration.fake import FakeTTSProvider

            return FakeTTSProvider()
        if provider_name == "elevenlabs":
            from app.narration.providers.elevenlabs import ElevenLabsTTSProvider

            return ElevenLabsTTSProvider()
        raise ProviderFactoryError(
            f"No factory entry for provider {provider_name!r}. "
            "Add the provider in DefaultProviderFactory.create() when its "
            "milestone is implemented."
        )


class DefaultProviderLoader:
    """Loads provider + metadata for a given name.

    Delegates instantiation to DefaultProviderFactory and retrieves
    metadata from the built-in catalogue.
    """

    def __init__(self, factory: ProviderFactory | None = None) -> None:
        self._factory: ProviderFactory = factory or DefaultProviderFactory()

    def load(self, provider_name: str) -> tuple[TTSProvider, ProviderMetadata]:
        if provider_name == "fake":
            from app.narration.fake import FAKE_METADATA

            provider = self._factory.create(provider_name)
            return provider, FAKE_METADATA
        if provider_name == "elevenlabs":
            from app.narration.providers.elevenlabs import ELEVENLABS_METADATA

            provider = self._factory.create(provider_name)
            return provider, ELEVENLABS_METADATA
        raise ProviderFactoryError(f"No loader entry for provider {provider_name!r}")


class RegisteringProviderLoader:
    """Loads a provider and registers it in a ProviderRegistry in one call."""

    def __init__(
        self,
        registry: ProviderRegistry,
        loader: ProviderLoader | None = None,
    ) -> None:
        self._registry = registry
        self._loader: ProviderLoader = loader or DefaultProviderLoader()

    def load_and_register(self, provider_name: str) -> tuple[TTSProvider, ProviderMetadata]:
        provider, metadata = self._loader.load(provider_name)
        self._registry.register(provider, metadata)
        return provider, metadata
