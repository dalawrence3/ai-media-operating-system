"""YouTube analytics adapter — maps YouTube Analytics API field names to canonical metrics.

This adapter is the ONLY place that may reference YouTube-specific field names.
No analytics code outside this file may check provider == "youtube".
No live API calls are made without explicit credential validation.

Authentication
--------------
YouTube Analytics Reporting API requires OAuth 2.0 — a bare Data API key is not
accepted.  Construct this provider via build_authenticated_analytics_provider()
in app.analytics.gate, which validates the stored OAuth grant contains
yt-analytics.readonly before building the provider.

Non-monetary scope (yt-analytics.readonly) — basic user activity video query
  views, estimatedMinutesWatched, averageViewDuration, averageViewPercentage,
  likes, comments, shares, subscribersGained, subscribersLost

Monetary scope (yt-analytics-monetary.readonly — not yet requested)
  estimatedRevenue

Metrics NOT available from the Analytics API targeted query
-----------------------------------------------------------
  impressions / impressionClickThroughRate (thumbnail reach + CTR):
    These are NOT valid metric names in the YouTube Analytics API.
    Thumbnail impressions and CTR are ONLY available via the YouTube Reporting
    API (youtubeanalytics.googleapis.com bulk/scheduled path) as
    video_thumbnail_impressions and video_thumbnail_impressions_ctr.
    The canonical metric slots METRIC_IMPRESSIONS and METRIC_CTR are reserved
    in constants.py for a future Reporting API integration.

  audienceWatchRatio / relativeRetentionPerformance (retention curves):
    Available from the Analytics API but require a separate query with the
    elapsedVideoTimeRatio dimension (one row per time-ratio bucket) — they
    cannot be added to a scalar basic-user-activity request.

Note: YouTube deprecated public access to the "dislikes" metric in December 2021.
It is excluded from the API request to avoid validation errors, but kept in the
field map so any legacy raw dict containing it is still normalized correctly.
"""

from __future__ import annotations

from app.analytics.constants import (
    METRIC_AVERAGE_VIEW_DURATION,
    METRIC_AVERAGE_VIEW_PERCENTAGE,
    METRIC_COMMENTS,
    METRIC_DISLIKES,
    METRIC_ENGAGED_VIEWS,
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
# dislikes is intentionally absent: deprecated by YouTube Dec 2021 (API error
# if requested).  Kept in map so legacy raw dicts still normalize correctly.
#
# impressions / impressionClickThroughRate are intentionally absent: these names
# do not exist in the YouTube Analytics API.  Thumbnail reach data lives in the
# YouTube Reporting API (bulk) as video_thumbnail_impressions /
# video_thumbnail_impressions_ctr.  The canonical METRIC_IMPRESSIONS and
# METRIC_CTR slots in constants.py are reserved for that future integration.
_YT_FIELD_MAP: dict[str, str] = {
    "views": METRIC_VIEWS,
    "engagedViews": METRIC_ENGAGED_VIEWS,
    "averageViewDuration": METRIC_AVERAGE_VIEW_DURATION,
    "averageViewPercentage": METRIC_AVERAGE_VIEW_PERCENTAGE,
    "likes": METRIC_LIKES,
    "dislikes": METRIC_DISLIKES,
    "comments": METRIC_COMMENTS,
    "shares": METRIC_SHARES,
    "subscribersGained": METRIC_SUBSCRIBERS_GAINED,
    "subscribersLost": METRIC_SUBSCRIBERS_LOST,
    "estimatedRevenue": METRIC_REVENUE_ESTIMATE,
}

# Metrics requested from the Analytics API basic user activity video query
# (ids=channel==MINE, filters=video==<id>).
#
# Excluded:
#   dislikes              — deprecated Dec 2021; API returns 400 if requested
#   impressions           — not a valid Analytics API metric name; use Reporting API
#   impressionClickThroughRate — same; use Reporting API video_thumbnail_impressions_ctr
_NON_MONETARY_REQUEST_METRICS: list[str] = [
    "engagedViews",
    "views",
    "estimatedMinutesWatched",
    "averageViewDuration",
    "averageViewPercentage",
    "likes",
    "comments",
    "shares",
    "subscribersGained",
    "subscribersLost",
]

# Additional metrics available only with yt-analytics-monetary.readonly.
_MONETARY_REQUEST_METRICS: list[str] = ["estimatedRevenue"]


class YouTubeAnalyticsProvider:
    """YouTube Analytics adapter implementing AnalyticsProvider.

    Authenticates via OAuth 2.0 stored token.  Construct via
    build_authenticated_analytics_provider() in app.analytics.gate —
    that factory validates the yt-analytics.readonly scope is present
    before building this object.

    Injecting a fake service is supported for tests: pass api_service_override
    to bypass the real googleapiclient.discovery.build call.
    """

    provider_name: str = "youtube"
    provider_version: str = _YOUTUBE_PROVIDER_VERSION

    def __init__(
        self,
        access_token: str = "",
        *,
        refresh_token: str | None = None,
        token_uri: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        monetary_scope_granted: bool = False,
        api_service_override: object | None = None,
    ) -> None:
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._token_uri = token_uri or "https://oauth2.googleapis.com/token"
        self._client_id = client_id
        self._client_secret = client_secret
        self._monetary_scope_granted = monetary_scope_granted
        self._service: object | None = api_service_override

    def initialize(self) -> None:
        pass

    def validate_credentials(self) -> bool:
        return bool(self._access_token)

    def fetch_metrics(
        self,
        provider_video_id: str,
        *,
        period_start: str | None = None,
        period_end: str | None = None,
    ) -> ProviderMetrics:
        """Fetch raw YouTube Analytics API metrics for a single video.

        Calls youtubeanalytics.googleapis.com/v2/reports with ids=channel==MINE
        and filters=video=={provider_video_id}.  Returns aggregated (non-time-series)
        scalar metrics for the given date range.

        period_start / period_end must be ISO date strings (YYYY-MM-DD or
        YYYY-MM-DDTHH:MM:SSZ — time part is stripped).  If omitted, the range
        defaults to 2020-01-01 → today.

        Raises ProviderAdapterError on any API failure or missing credentials.
        """
        from datetime import date

        from app.analytics.errors import ProviderAdapterError

        if not self._access_token:
            raise ProviderAdapterError(
                "YouTubeAnalyticsProvider requires an OAuth access_token. "
                "Use build_authenticated_analytics_provider() to construct a live provider."
            )

        requested_metrics = list(_NON_MONETARY_REQUEST_METRICS)
        if self._monetary_scope_granted:
            requested_metrics.extend(_MONETARY_REQUEST_METRICS)

        # Strip time component if caller passed full ISO timestamps
        start = (period_start or "2020-01-01")[:10]
        end = (period_end or date.today().isoformat())[:10]

        try:
            service = self._get_service()
            response = (
                service.reports()  # type: ignore[attr-defined]
                .query(
                    ids="channel==MINE",
                    startDate=start,
                    endDate=end,
                    metrics=",".join(requested_metrics),
                    filters=f"video=={provider_video_id}",
                )
                .execute()
            )
        except Exception as exc:
            raise ProviderAdapterError(
                f"YouTube Analytics API call failed for video {provider_video_id!r}: {exc}"
            ) from exc

        column_headers = response.get("columnHeaders", [])
        rows = response.get("rows", [])
        raw: dict[str, object] = {}
        if rows:
            raw = {h["name"]: v for h, v in zip(column_headers, rows[0], strict=False)}

        return ProviderMetrics(
            provider_video_id=provider_video_id,
            raw=raw,
            period_start=start,
            period_end=end,
        )

    def normalize(self, raw: ProviderMetrics) -> dict[str, float]:
        """Translate YouTube API field names to canonical metric names.

        Converts estimatedMinutesWatched → watch_time_seconds (×60).
        Currency codes are NOT emitted as float metrics; they remain in raw.
        Revenue fields are absent when monetary scope was not granted at
        fetch_metrics time (the API simply didn't return them).
        """
        out: dict[str, float | None] = {}

        for yt_field, canonical in _YT_FIELD_MAP.items():
            raw_value = raw.raw.get(yt_field)
            if raw_value is None:
                continue
            out[canonical] = safe_float(raw_value, yt_field)

        raw_minutes = raw.raw.get("estimatedMinutesWatched")
        if raw_minutes is not None:
            minutes = safe_float(raw_minutes, "estimatedMinutesWatched")
            if minutes is not None:
                if METRIC_WATCH_TIME_SECONDS not in out:
                    out[METRIC_WATCH_TIME_SECONDS] = minutes * _SECONDS_PER_MINUTE

        return filter_none_metrics(out)  # type: ignore[arg-type]

    def health(self) -> ProviderHealthReport:
        ok = self.validate_credentials()
        return ProviderHealthReport(
            ok=ok,
            provider=self.provider_name,
            provider_version=self.provider_version,
            message="OAuth credentials present" if ok else "Missing OAuth access token",
        )

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name=self.provider_name,
            version=self.provider_version,
            supports_period_queries=True,
            supports_revenue=self._monetary_scope_granted,
            # Thumbnail impressions and CTR are NOT available from the Analytics API
            # targeted query. They require a separate YouTube Reporting API integration
            # (bulk, scheduled). Set False until that integration exists.
            supports_impression_data=False,
        )

    def shutdown(self) -> None:
        self._service = None

    def _get_service(self) -> object:
        if self._service is not None:
            return self._service
        try:
            from google.oauth2.credentials import Credentials  # type: ignore[import]
            from googleapiclient.discovery import build  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "google-api-python-client is required for live YouTube Analytics. "
                "Run: pip install google-api-python-client"
            ) from exc
        credentials = Credentials(
            token=self._access_token,
            refresh_token=self._refresh_token,
            token_uri=self._token_uri,
            client_id=self._client_id,
            client_secret=self._client_secret,
        )
        self._service = build("youtubeAnalytics", "v2", credentials=credentials)
        return self._service


assert isinstance(
    YouTubeAnalyticsProvider("tok"),
    AnalyticsProvider,
)
