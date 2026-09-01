"""Phase 13A — Provider capability definitions (Python-only, not in DB).

These are kept in code (not in cp_provider_registry) because the registry's
domain CHECK constraint does not include 'market_intelligence' and SQLite
requires full table recreation to modify CHECK constraints.

YouTube Data API v3 quota model (corrected June 2026):
  search.list      — 1 unit per HTTP request; 100 requests/day limit (search_list bucket)
  videos.batchGetStats — 1 unit per call; 10,000/day limit (video_stats_batch bucket)
  videos.list      — 1 unit per call; 10,000/day limit (general_data_api bucket)
  channels.list    — 1 unit per call; shared general_data_api bucket

The old "100 units per search.list call" figure was an error in the initial
implementation; the daily limit of 100 calls was misread as a per-call cost.
"""

from __future__ import annotations

from dataclasses import dataclass

# Only official APIs. No pytrends, no HTML scraping.
OFFICIAL_PROVIDERS_ONLY: bool = True


@dataclass(frozen=True)
class ProviderQuotaBucket:
    name: str
    window_type: str  # 'daily' | 'per_request' | 'per_minute'
    daily_limit: int | None = None


@dataclass(frozen=True)
class ProviderOperation:
    name: str
    quota_cost: int
    quota_bucket: str


@dataclass(frozen=True)
class ProviderCapabilities:
    provider_id: str
    platform: str
    display_name: str
    is_official_api: bool
    operations: tuple[ProviderOperation, ...]
    quota_buckets: tuple[ProviderQuotaBucket, ...]
    notes: str = ""


_YOUTUBE_DATA_API_V3 = ProviderCapabilities(
    provider_id="youtube_data_api",
    platform="youtube",
    display_name="YouTube Data API v3",
    is_official_api=True,
    operations=(
        # 1 unit per HTTP request; provider caps at 100 requests/day
        ProviderOperation(name="search.list", quota_cost=1, quota_bucket="search_list"),
        # 1 unit per call; preferred enrichment endpoint
        ProviderOperation(
            name="videos.batchGetStats", quota_cost=1, quota_bucket="video_stats_batch"
        ),
        # 1 unit per call; fallback enrichment endpoint (used only on batchGetStats 404/501)
        ProviderOperation(name="videos.list", quota_cost=1, quota_bucket="general_data_api"),
        # 1 unit per call; channel metadata (Phase 13C+)
        ProviderOperation(name="channels.list", quota_cost=1, quota_bucket="general_data_api"),
    ),
    quota_buckets=(
        ProviderQuotaBucket(name="search_list", window_type="daily", daily_limit=100),
        ProviderQuotaBucket(name="video_stats_batch", window_type="daily", daily_limit=10_000),
        ProviderQuotaBucket(name="general_data_api", window_type="daily", daily_limit=10_000),
    ),
    notes=(
        "search.list costs 1 unit per request with a 100 request/day limit. "
        "videos.batchGetStats and videos.list each cost 1 unit from separate buckets."
    ),
)

# Optional future placeholder — no implementation in Phase 13A.
# Kept here to satisfy the 'official providers only' policy note.
_GOOGLE_TRENDS_PLACEHOLDER = ProviderCapabilities(
    provider_id="google_trends_official",
    platform="google",
    display_name="Google Trends (Official API — not yet available)",
    is_official_api=False,  # No official public API exists as of 2026-08
    operations=(),
    quota_buckets=(),
    notes="Placeholder only. pytrends and HTML scraping are explicitly prohibited.",
)

KNOWN_PROVIDERS: dict[str, ProviderCapabilities] = {
    "youtube_data_api": _YOUTUBE_DATA_API_V3,
}


def get_provider(provider_id: str) -> ProviderCapabilities | None:
    return KNOWN_PROVIDERS.get(provider_id)


def get_operation_quota_cost(provider_id: str, operation: str) -> int | None:
    cap = KNOWN_PROVIDERS.get(provider_id)
    if cap is None:
        return None
    for op in cap.operations:
        if op.name == operation:
            return op.quota_cost
    return None


def get_bucket(provider_id: str, bucket_name: str) -> ProviderQuotaBucket | None:
    cap = KNOWN_PROVIDERS.get(provider_id)
    if cap is None:
        return None
    for b in cap.quota_buckets:
        if b.name == bucket_name:
            return b
    return None
