"""YouTube analytics adapter — maps YouTube Analytics API field names to canonical metrics.

This adapter is the ONLY place that may reference YouTube-specific field names.
No analytics code outside this file may check provider == "youtube".
No live API calls are made without explicit credential validation.

Accuracy note
-------------
This adapter is an injectable normalization boundary tested with deterministic
fixtures.  It has NOT been verified against a live YouTube Analytics or
Reporting API client.  A real integration would require:
- YouTube OAuth 2.0 setup (service account or user consent)
- Requests to https://youtubeanalytics.googleapis.com/v2/reports
- Handling pagination, quota, and partial-data responses

All tests use FakeAnalyticsProvider.  YouTubeAnalyticsProvider.fetch_metrics
raises ProviderAdapterError immediately — no actual HTTP requests are made.
"""

from __future__ import annotations

import os

from app.analytics.constants import (
    METRIC_AVERAGE_VIEW_DURATION,
    METRIC_COMMENTS,
    METRIC_CTR,
    METRIC_DISLIKES,
    METRIC_IMPRESSIONS,
    METRIC_LIKES,
    METRIC_REVENUE_ESTIMATE,
    METRIC_SHARES,
    METRIC_SUBSCRIBERS_GAINED,
    METRIC_SUBSCRIBERS_LOST,
    METRIC_VIEWS,
    METRIC_WATCH_TIME_SECONDS,
)
from app.analytics.normalization import filter_none_metrics, safe_float
from app.analytics.protocol import (
    AnalyticsProvider,
    ProviderCapabilities,
    ProviderHealthReport,
    ProviderMetrics,
)

_YOUTUBE_PROVIDER_VERSION = "1.0.0"
_SECONDS_PER_MINUTE = 60.0

# YouTube Analytics API field → canonical metric name.
#
# estimatedMinutesWatched is intentionally absent: the normalization step
# converts it to watch_time_seconds (seconds) before any canonical storage.
# currency is intentionally absent: it is a string code and cannot be stored
# as a REAL column.  Currency codes are preserved in raw_metrics_json only.
_YT_FIELD_MAP: dict[str, str] = {
    "views":                        METRIC_VIEWS,
    "averageViewDuration":          METRIC_AVERAGE_VIEW_DURATION,
    "impressions":                  METRIC_IMPRESSIONS,
    "impressionClickThroughRate":   METRIC_CTR,
    "likes":                        METRIC_LIKES,
    "dislikes":                     METRIC_DISLIKES,
    "comments":                     METRIC_COMMENTS,
    "shares":                       METRIC_SHARES,
    "subscribersGained":            METRIC_SUBSCRIBERS_GAINED,
    "subscribersLost":              METRIC_SUBSCRIBERS_LOST,
    "estimatedRevenue":             METRIC_REVENUE_ESTIMATE,
}


class YouTubeAnalyticsProvider:
    """YouTube adapter implementing AnalyticsProvider.

    Credentials are read from environment variables only.
    No live calls are made in tests; tests use FakeAnalyticsProvider instead.
    """

    provider_name: str = "youtube"
    provider_version: str = _YOUTUBE_PROVIDER_VERSION

    _API_KEY_ENV = "YOUTUBE_API_KEY"
    _CHANNEL_ID_ENV = "YOUTUBE_CHANNEL_ID"

    def initialize(self) -> None:
        pass

    def validate_credentials(self) -> bool:
        return bool(os.environ.get(self._API_KEY_ENV))

    def fetch_metrics(
        self,
        provider_video_id: str,
        *,
        period_start: str | None = None,
        period_end: str | None = None,
    ) -> ProviderMetrics:
        """Fetch raw YouTube Analytics API metrics.

        Requires YOUTUBE_API_KEY in environment.
        Raises ProviderAdapterError if credentials are missing or the call fails.
        """
        from app.analytics.errors import ProviderAdapterError

        api_key = os.environ.get(self._API_KEY_ENV)
        if not api_key:
            raise ProviderAdapterError(
                f"Missing required environment variable {self._API_KEY_ENV!r}. "
                "Configure credentials before calling fetch_metrics."
            )

        # Live HTTP call intentionally not implemented.
        # See module docstring for integration requirements.
        raise ProviderAdapterError(
            "YouTubeAnalyticsProvider.fetch_metrics requires a live YouTube "
            "Analytics API connection. Use FakeAnalyticsProvider in tests."
        )

    def normalize(self, raw: ProviderMetrics) -> dict[str, float]:
        """Translate YouTube API field names to canonical metric names.

        Converts estimatedMinutesWatched → watch_time_seconds (×60).
        Currency codes are NOT emitted as float metrics; they remain in raw.
        """
        out: dict[str, float | None] = {}

        # Standard field mapping
        for yt_field, canonical in _YT_FIELD_MAP.items():
            raw_value = raw.raw.get(yt_field)
            if raw_value is None:
                continue
            out[canonical] = safe_float(raw_value, yt_field)

        # estimatedMinutesWatched → watch_time_seconds (seconds is canonical)
        raw_minutes = raw.raw.get("estimatedMinutesWatched")
        if raw_minutes is not None:
            minutes = safe_float(raw_minutes, "estimatedMinutesWatched")
            if minutes is not None:
                # Prefer direct averageViewDuration seconds if already present
                if METRIC_WATCH_TIME_SECONDS not in out:
                    out[METRIC_WATCH_TIME_SECONDS] = minutes * _SECONDS_PER_MINUTE

        return filter_none_metrics(out)  # type: ignore[arg-type]

    def health(self) -> ProviderHealthReport:
        ok = self.validate_credentials()
        return ProviderHealthReport(
            ok=ok,
            provider=self.provider_name,
            provider_version=self.provider_version,
            message="Credentials present" if ok else f"Missing {self._API_KEY_ENV}",
        )

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name=self.provider_name,
            version=self.provider_version,
            supports_period_queries=True,
            supports_revenue=True,
            supports_impression_data=True,
        )

    def shutdown(self) -> None:
        pass


assert isinstance(YouTubeAnalyticsProvider(), AnalyticsProvider)
