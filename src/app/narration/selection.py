"""Phase 6 M6.3B: Provider selection — criteria and selector protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from app.narration.errors import ProviderSelectionError
from app.narration.registry import ProviderRegistry


@dataclass
class ProviderSelectionCriteria:
    """Constraints used to pick a provider from the registry.

    All fields are optional filters.  A provider satisfies the criteria only
    when every non-None / non-empty constraint is met.
    """

    preferred_provider: str | None = None
    required_language: str | None = None
    required_output_format: str | None = None
    required_sample_rate_hz: int | None = None
    required_features: frozenset[str] = field(default_factory=frozenset)


@runtime_checkable
class ProviderSelector(Protocol):
    """Chooses a provider name from a registry given selection criteria."""

    def select(
        self,
        registry: ProviderRegistry,
        criteria: ProviderSelectionCriteria,
    ) -> str: ...


class DefaultProviderSelector:
    """Selects the first provider that satisfies all criteria.

    Selection order:
    1. If criteria.preferred_provider is set and registered → return it
       (subject to capability checks against the criteria).
    2. Otherwise iterate discovered providers in sorted order and return
       the first that satisfies all non-None criteria.
    3. Raise ProviderSelectionError if no provider qualifies.
    """

    def select(
        self,
        registry: ProviderRegistry,
        criteria: ProviderSelectionCriteria,
    ) -> str:
        candidates = registry.discover()
        if not candidates:
            raise ProviderSelectionError("No TTS providers are registered")

        if criteria.preferred_provider and registry.is_registered(
            criteria.preferred_provider
        ):
            if self._satisfies(registry, criteria.preferred_provider, criteria):
                return criteria.preferred_provider

        for name in candidates:
            if self._satisfies(registry, name, criteria):
                return name

        raise ProviderSelectionError(
            f"No registered provider satisfies criteria: {criteria!r}"
        )

    @staticmethod
    def _satisfies(
        registry: ProviderRegistry,
        provider_name: str,
        criteria: ProviderSelectionCriteria,
    ) -> bool:
        meta = registry.get_metadata(provider_name)
        caps = meta.capabilities

        if criteria.required_language and not caps.accepts_language(
            criteria.required_language
        ):
            return False
        if criteria.required_output_format and not caps.accepts_output_format(
            criteria.required_output_format
        ):
            return False
        if criteria.required_sample_rate_hz is not None and not caps.accepts_sample_rate(
            criteria.required_sample_rate_hz
        ):
            return False
        for feature in criteria.required_features:
            if not caps.has_feature(feature):
                return False
        return True
