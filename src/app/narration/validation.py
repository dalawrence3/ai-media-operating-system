"""Phase 6 M6.3B: Provider validation and compatibility checking."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.narration.errors import ProviderCompatibilityError
from app.narration.metadata import ProviderMetadata
from app.narration.protocol import TTSProvider, TTSRequest


@dataclass(frozen=True)
class ProviderCompatibilityResult:
    """Outcome of a compatibility check between a request and a provider."""

    is_compatible: bool
    provider_name: str
    model_id: str
    issues: tuple[str, ...]

    @classmethod
    def ok(cls, provider_name: str, model_id: str) -> ProviderCompatibilityResult:
        return cls(
            is_compatible=True,
            provider_name=provider_name,
            model_id=model_id,
            issues=(),
        )

    @classmethod
    def fail(
        cls,
        provider_name: str,
        model_id: str,
        issues: list[str],
    ) -> ProviderCompatibilityResult:
        return cls(
            is_compatible=False,
            provider_name=provider_name,
            model_id=model_id,
            issues=tuple(issues),
        )

    def raise_if_incompatible(self) -> None:
        if not self.is_compatible:
            detail = "; ".join(self.issues)
            raise ProviderCompatibilityError(
                f"Provider {self.provider_name!r} incompatible: {detail}"
            )


@runtime_checkable
class ProviderValidator(Protocol):
    """Checks that a TTSRequest is compatible with a provider's capabilities."""

    def validate(
        self,
        provider: TTSProvider,
        metadata: ProviderMetadata,
        request: TTSRequest,
    ) -> ProviderCompatibilityResult: ...


class DefaultProviderValidator:
    """Validates language, format, sample rate, speaking rate, and text length."""

    def validate(
        self,
        provider: TTSProvider,
        metadata: ProviderMetadata,
        request: TTSRequest,
    ) -> ProviderCompatibilityResult:
        caps = metadata.capabilities
        issues: list[str] = []

        if not caps.accepts_language(request.language):
            issues.append(
                f"language {request.language!r} not in supported set "
                f"{sorted(caps.supported_languages)}"
            )
        if not caps.accepts_output_format(request.output_format):
            issues.append(
                f"output_format {request.output_format!r} not in "
                f"{sorted(caps.supported_output_formats)}"
            )
        if not caps.accepts_sample_rate(request.sample_rate_hz):
            issues.append(
                f"sample_rate_hz {request.sample_rate_hz} not in "
                f"{sorted(caps.supported_sample_rates_hz)}"
            )
        if not caps.accepts_speaking_rate(request.speaking_rate):
            issues.append(
                f"speaking_rate {request.speaking_rate} outside "
                f"[{caps.min_speaking_rate}, {caps.max_speaking_rate}]"
            )
        if not caps.accepts_character_count(len(request.text)):
            issues.append(
                f"text length {len(request.text)} exceeds "
                f"max_characters_per_request {caps.max_characters_per_request}"
            )

        if issues:
            return ProviderCompatibilityResult.fail(
                metadata.provider_name, metadata.model_id, issues
            )
        return ProviderCompatibilityResult.ok(metadata.provider_name, metadata.model_id)
