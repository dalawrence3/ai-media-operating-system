"""Fake analytics provider — default for tests and local development.

No network calls.  No credentials.  Deterministic output.
"""

from __future__ import annotations

from app.analytics.constants import (
    METRIC_CTR,
    METRIC_IMPRESSIONS,
    METRIC_LIKES,
    METRIC_VIEWS,
    METRIC_WATCH_TIME_SECONDS,
)
from app.analytics.protocol import (
    AnalyticsProvider,
    ProviderCapabilities,
    ProviderHealthReport,
    ProviderMetrics,
)

_FAKE_PROVIDER_VERSION = "1.0.0"


class FakeAnalyticsProvider:
    """Deterministic stub implementing AnalyticsProvider for tests."""

    provider_name: str = "fake"
    provider_version: str = _FAKE_PROVIDER_VERSION

    def __init__(self, metrics: dict[str, float] | None = None) -> None:
        self._metrics = (
            metrics
            if metrics is not None
            else {
                METRIC_VIEWS: 1000.0,
                METRIC_WATCH_TIME_SECONDS: 45000.0,
                METRIC_IMPRESSIONS: 5000.0,
                METRIC_CTR: 0.05,
                METRIC_LIKES: 80.0,
            }
        )

    def initialize(self) -> None:
        pass

    def validate_credentials(self) -> bool:
        return True

    def fetch_metrics(
        self,
        provider_video_id: str,
        *,
        period_start: str | None = None,
        period_end: str | None = None,
    ) -> ProviderMetrics:
        return ProviderMetrics(
            provider_video_id=provider_video_id,
            raw=dict(self._metrics),
            period_start=period_start,
            period_end=period_end,
        )

    def normalize(self, raw: ProviderMetrics) -> dict[str, float]:
        # Fake provider uses canonical names directly
        return {k: float(v) for k, v in raw.raw.items() if isinstance(v, int | float)}

    def health(self) -> ProviderHealthReport:
        return ProviderHealthReport(
            ok=True,
            provider=self.provider_name,
            provider_version=self.provider_version,
            message="Fake provider always healthy",
        )

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name=self.provider_name,
            version=self.provider_version,
            supports_period_queries=True,
            supports_revenue=False,
            supports_impression_data=True,
        )

    def shutdown(self) -> None:
        pass


assert isinstance(FakeAnalyticsProvider(), AnalyticsProvider)
