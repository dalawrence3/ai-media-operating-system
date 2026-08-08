"""AnalyticsProvider Protocol — provider-neutral analytics interface.

Generic orchestration code depends only on this protocol.
No provider names, API endpoints, or credential types may appear here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class ProviderMetrics:
    """Raw metrics returned by a provider adapter, in provider-specific naming."""

    provider_video_id: str
    raw: dict[str, object] = field(default_factory=dict)
    period_start: str | None = None
    period_end: str | None = None


@dataclass
class ProviderHealthReport:
    """Provider readiness status."""

    ok: bool
    provider: str
    provider_version: str
    message: str = ""
    details: dict = field(default_factory=dict)


@dataclass
class ProviderCapabilities:
    """Static capabilities for an analytics provider."""

    name: str
    version: str
    supports_period_queries: bool = False
    supports_revenue: bool = False
    supports_impression_data: bool = False


@runtime_checkable
class AnalyticsProvider(Protocol):
    """Provider-neutral interface for analytics adapters."""

    @property
    def provider_name(self) -> str: ...

    @property
    def provider_version(self) -> str: ...

    def initialize(self) -> None:
        """One-time initialization (credential loading, session setup)."""
        ...

    def validate_credentials(self) -> bool:
        """Return True if credentials are configured and valid."""
        ...

    def fetch_metrics(
        self,
        provider_video_id: str,
        *,
        period_start: str | None = None,
        period_end: str | None = None,
    ) -> ProviderMetrics:
        """Fetch raw metrics from the provider for the given video."""
        ...

    def normalize(self, raw: ProviderMetrics) -> dict[str, float]:
        """Translate provider-specific field names to canonical metric names.

        Returns only the metrics this provider supports.
        Keys must all be members of CANONICAL_METRICS.
        Provider-specific naming must not leak past this method.
        """
        ...

    def health(self) -> ProviderHealthReport:
        """Check provider connectivity and credential validity."""
        ...

    def capabilities(self) -> ProviderCapabilities:
        """Return static provider capability declarations."""
        ...

    def shutdown(self) -> None:
        """Release resources."""
        ...
