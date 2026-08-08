"""Phase 6 M6.3B: Provider configuration objects."""

from __future__ import annotations

from dataclasses import dataclass

from app.narration.errors import ProviderConfigError


@dataclass(frozen=True)
class ProviderConfig:
    """Static configuration for instantiating a TTS provider.

    Used by ProviderFactory to create provider instances.  Does NOT contain
    credentials — those remain outside the codebase.
    """

    provider_name: str
    model_id: str
    voice_id: str
    language: str
    speaking_rate: float
    output_format: str
    sample_rate_hz: int
    style: str | None = None
    stability: float | None = None
    similarity_boost: float | None = None
    extra_settings_json: str = "{}"

    def __post_init__(self) -> None:
        if not self.provider_name:
            raise ProviderConfigError("provider_name must not be empty")
        if not self.model_id:
            raise ProviderConfigError("model_id must not be empty")
        if self.speaking_rate <= 0.0:
            raise ProviderConfigError(f"speaking_rate must be positive, got {self.speaking_rate}")
        if self.sample_rate_hz <= 0:
            raise ProviderConfigError(f"sample_rate_hz must be positive, got {self.sample_rate_hz}")

    def to_dict(self) -> dict:
        return {
            "provider_name": self.provider_name,
            "model_id": self.model_id,
            "voice_id": self.voice_id,
            "language": self.language,
            "speaking_rate": self.speaking_rate,
            "output_format": self.output_format,
            "sample_rate_hz": self.sample_rate_hz,
            "style": self.style,
            "stability": self.stability,
            "similarity_boost": self.similarity_boost,
        }


class ProviderConfigRegistry:
    """Maps named configuration keys to ProviderConfig objects."""

    def __init__(self) -> None:
        self._configs: dict[str, ProviderConfig] = {}

    def register(self, name: str, config: ProviderConfig) -> None:
        self._configs[name] = config

    def get(self, name: str) -> ProviderConfig:
        try:
            return self._configs[name]
        except KeyError:
            raise ProviderConfigError(f"No provider config registered under {name!r}") from None

    def names(self) -> list[str]:
        return list(self._configs.keys())

    def is_registered(self, name: str) -> bool:
        return name in self._configs
