"""Phase 13B — YouTube Discovery & Enrichment Collector.

Enrichment strategy (effective June 2026):
  PRIMARY: videos:batchGetStats
    GET https://www.googleapis.com/youtube/v3/videos:batchGetStats
    - 1 unit per call from the video_stats_batch quota bucket
    - returns viewCount, likeCount, commentCount, publishTime, duration, durationMillis
    - no generated client required — raw httpx GET with API key
  FALLBACK: videos.list (snippet,statistics,contentDetails)
    - used only when batchGetStats returns HTTP 404 or 501 (endpoint not supported)
    - records operation="videos.list" / bucket="general_data_api"
    - NOT triggered by 403, 429, or 5xx (those are errors, not missing-endpoint signals)

search.list quota (effective June 2026):
  - 1 unit per HTTP request (not 100)
  - limit of 100 requests/day is a provider-side cap, not a per-call cost
  - bucket = search_list (independent from enrichment buckets)
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from app.intelligence.market import repository as repo
from app.intelligence.market.models import (
    SEARCH_RESULT_RANK,
    SEARCH_RESULT_TOTAL_ESTIMATE,
    VIDEO_COMMENT_COUNT,
    VIDEO_DURATION_SECONDS,
    VIDEO_LIKE_COUNT,
    VIDEO_PUBLISHED_AT,
    VIDEO_TITLE,
    VIDEO_VIEW_COUNT,
    MarketCollectionJob,
)

_YOUTUBE_BASE = "https://www.googleapis.com/youtube/v3"
_BATCH_STATS_ENDPOINT = f"{_YOUTUBE_BASE}/videos:batchGetStats"

# Per-call unit costs (each request = 1 unit from its respective bucket).
# The 100-requests/day limit for search.list is a provider-side cap,
# not 100 units consumed per call.
_SEARCH_LIST_QUOTA = 1
_BATCH_STATS_QUOTA = 1
_VIDEOS_LIST_QUOTA = 1

_VIDEOS_BATCH_SIZE = 50  # safe max IDs per batchGetStats or videos.list request

# Fall back to videos.list ONLY when batchGetStats returns these status codes
# (endpoint does not exist in this API surface version).
# 403, 429, 5xx → do NOT fall back; record as partial failure.
_BATCH_STATS_FALLBACK_STATUS_CODES: frozenset[int] = frozenset({404, 501})

# Stat keys normalised into the provider payload fingerprint.
_FINGERPRINT_STAT_KEYS = frozenset({"viewCount", "likeCount", "commentCount", "favoriteCount"})

_ISO8601_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<mins>\d+)M)?(?:(?P<secs>\d+(?:\.\d+)?)S)?)?$"
)


# ---------------------------------------------------------------------------
# Utilities (exported for testing)
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def normalize_market_query(query: str) -> str:
    """Lowercase + collapse whitespace. Keeps all characters (not topic normalizer)."""
    return " ".join(query.lower().split())


def parse_iso8601_duration(s: str | None) -> float | None:
    """Convert ISO 8601 duration string to total seconds. Returns None on failure."""
    if not s:
        return None
    m = _ISO8601_RE.match(s)
    if not m:
        return None
    days = float(m.group("days") or 0)
    hours = float(m.group("hours") or 0)
    mins = float(m.group("mins") or 0)
    secs = float(m.group("secs") or 0)
    return days * 86400 + hours * 3600 + mins * 60 + secs


def make_obs_input_hash(
    *,
    platform: str,
    provider: str,
    signal_type: str,
    external_video_id: str | None,
    external_channel_id: str | None,
    normalized_query: str | None,
    region_code: str | None,
    language_code: str | None,
    date_bucket: str,
) -> str:
    """Deterministic SHA-256 identity for one observation slot."""
    payload = {
        "platform": platform,
        "provider": provider,
        "signal_type": signal_type,
        "external_video_id": external_video_id,
        "external_channel_id": external_channel_id,
        "normalized_query": normalized_query,
        "region_code": region_code,
        "language_code": language_code,
        "date_bucket": date_bucket,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def get_stat_value(item: dict, key: str) -> tuple[bool, float | None]:
    """Return (present, value) for a stat field.

    Checks both flat top-level keys (batchGetStats) and item["statistics"][key]
    (videos.list).  present=False means the field was absent from the response;
    present=True with value=0.0 is explicitly zero — these are not the same.
    """
    if key in item and item[key] is not None:
        return True, float(item[key])
    stats = item.get("statistics", {})
    if key in stats and stats[key] is not None:
        return True, float(stats[key])
    return False, None


def compute_payload_fingerprint(video_item: dict) -> str:
    """SHA-256 of the engagement statistics + duration of a provider video item.

    Normalises both batchGetStats (flat top-level fields) and videos.list
    (nested statistics / contentDetails) so that identical content produces
    an identical fingerprint regardless of which API path fetched it.
    Changed viewCount / likeCount / etc. → different fingerprint.
    """
    # Collect stat values from either flat or nested location
    stats_norm: dict[str, str | None] = {}
    for key in _FINGERPRINT_STAT_KEYS:
        present, val = get_stat_value(video_item, key)
        if present:
            stats_norm[key] = str(val)

    # Duration: prefer durationMillis (numeric ms), fall back to ISO 8601 string
    details = video_item.get("contentDetails", {})
    dur_ms = video_item.get("durationMillis") or details.get("durationMillis")
    dur_str = video_item.get("duration") or details.get("duration")
    duration_repr = str(dur_ms) if dur_ms is not None else dur_str

    data = {"statistics": stats_norm, "duration": duration_repr}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def _extract_published_at(item: dict) -> str | None:
    """Extract publish timestamp from either batchGetStats or videos.list item."""
    snippet = item.get("snippet", {})
    return (
        item.get("publishTime")  # batchGetStats flat field
        or snippet.get("publishTime")  # batchGetStats via snippet
        or snippet.get("publishedAt")  # videos.list
    )


def _extract_duration_secs(item: dict) -> float | None:
    """Extract duration in seconds from batchGetStats or videos.list item.

    Prefers durationMillis (precise integer ms) when present.
    Falls back to ISO 8601 duration string.
    """
    details = item.get("contentDetails", {})
    dur_ms = item.get("durationMillis") or details.get("durationMillis")
    if dur_ms is not None:
        return float(dur_ms) / 1000.0
    dur_str = item.get("duration") or details.get("duration")
    return parse_iso8601_duration(dur_str)


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------


@dataclass
class CollectionResult:
    job_id: int
    status: str
    observations_new: int = 0  # observations linked to this job (new + reused combined)
    observations_reused: int = 0  # deduplication tracking (see note below)
    search_calls: int = 0
    enrichment_calls: int = 0
    partial_failures: list[str] = field(default_factory=list)

    @property
    def observations_total(self) -> int:
        return self.observations_new + self.observations_reused


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------


class YouTubeMarketCollector:
    """Collects external market intelligence from YouTube Data API v3.

    Inject `client` to use a stub in tests; leave None for the real HTTP client.
    Call .close() when done if you did not inject a client.

    Phase 13B scope:
      - search.list  → SEARCH_RESULT_RANK + SEARCH_RESULT_TOTAL_ESTIMATE
      - videos:batchGetStats (primary) or videos.list (fallback) → VIDEO_* signals

    Channel enrichment (channels.list → CHANNEL_* signals) is deferred to a
    later phase to keep this milestone focused on the core search → statistics
    pipeline. The Phase 13A schema and quota model already support it.
    """

    PROVIDER = "youtube_data_api"
    PLATFORM = "youtube"
    COLLECTOR_NAME = "youtube_search_scan_v1"

    def __init__(self, api_key: str, client: httpx.Client | None = None) -> None:
        self._api_key = api_key
        self._owns_client = client is None
        self._client = client if client is not None else httpx.Client(timeout=30.0)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def collect_search_scan(
        self,
        conn,
        job: MarketCollectionJob,
        *,
        query: str,
        region_code: str | None = None,
        language_code: str | None = None,
        published_after: str | None = None,
        order: str = "relevance",
        max_results: int = 25,
        max_pages: int = 1,
        max_search_calls: int | None = None,
        max_enrichment_calls: int | None = None,
    ) -> CollectionResult:
        """Execute a search_scan job and persist all observations.

        Flow: pending → running → search pages → enrichment → completed/partial/failed

        observed_at is captured once per invocation so all observations from
        this collection event share the same timestamp. date_bucket (YYYY-MM-DD)
        governs idempotency: same video + same signal on the same calendar day
        deduplicates; a new calendar day creates a new row for velocity.
        """
        observed_at = _now()
        date_bucket = observed_at[:10]
        normalized_query = normalize_market_query(query)
        result = CollectionResult(job_id=job.id, status="running")

        repo.update_job_status(conn, job.id, status="running", started_at=observed_at)

        search_calls_made = 0
        page_token: str | None = None

        for _page in range(max_pages):
            # --- quota guard ---
            if max_search_calls is not None and search_calls_made >= max_search_calls:
                break

            params: dict = {
                "q": query,
                "part": "snippet",
                "type": "video",
                "maxResults": min(max_results, 50),
                "order": order,
                "key": self._api_key,
            }
            if region_code:
                params["regionCode"] = region_code
            if language_code:
                params["relevanceLanguage"] = language_code
            if published_after:
                params["publishedAfter"] = published_after
            if page_token:
                params["pageToken"] = page_token

            try:
                search_resp = self._client.get(f"{_YOUTUBE_BASE}/search", params=params)
                search_resp.raise_for_status()
            except httpx.HTTPError as exc:
                repo.update_job_status(
                    conn,
                    job.id,
                    status="failed",
                    error_message=str(exc),
                    failure_stage="search",
                    completed_at=_now(),
                )
                result.status = "failed"
                return result

            search_calls_made += 1
            result.search_calls += 1
            repo.record_quota_usage(
                conn,
                job_id=job.id,
                provider=self.PROVIDER,
                operation="search.list",
                quota_bucket="search_list",
                units_consumed=_SEARCH_LIST_QUOTA,  # 1 per HTTP request
                call_count=1,
                window_type="daily",
                observed_at=observed_at,
            )

            search_data = search_resp.json()
            items = search_data.get("items", [])

            # SEARCH_RESULT_TOTAL_ESTIMATE (provider approximation — not a fact)
            total_results = search_data.get("pageInfo", {}).get("totalResults")
            if total_results is not None:
                ih = make_obs_input_hash(
                    platform=self.PLATFORM,
                    provider=self.PROVIDER,
                    signal_type=SEARCH_RESULT_TOTAL_ESTIMATE,
                    external_video_id=None,
                    external_channel_id=None,
                    normalized_query=normalized_query,
                    region_code=region_code,
                    language_code=language_code,
                    date_bucket=date_bucket,
                )
                obs = repo.persist_observation(
                    conn,
                    platform=self.PLATFORM,
                    provider=self.PROVIDER,
                    collector_name=self.COLLECTOR_NAME,
                    signal_type=SEARCH_RESULT_TOTAL_ESTIMATE,
                    observed_at=observed_at,
                    input_hash=ih,
                    normalized_query=normalized_query,
                    query_text=query,
                    region_code=region_code,
                    language_code=language_code,
                    signal_value_numeric=float(total_results),
                )
                repo.link_job_observation(conn, job.id, obs.id)
                result.observations_new += 1

            # SEARCH_RESULT_RANK + VIDEO_TITLE (from snippet) + collect video IDs
            video_ids: list[str] = []
            for rank, item in enumerate(items):
                video_id = item.get("id", {}).get("videoId")
                if not video_id:
                    continue
                snippet = item.get("snippet", {})
                ext_channel_id = snippet.get("channelId")
                video_ids.append(video_id)

                ih = make_obs_input_hash(
                    platform=self.PLATFORM,
                    provider=self.PROVIDER,
                    signal_type=SEARCH_RESULT_RANK,
                    external_video_id=video_id,
                    external_channel_id=ext_channel_id,
                    normalized_query=normalized_query,
                    region_code=region_code,
                    language_code=language_code,
                    date_bucket=date_bucket,
                )
                obs = repo.persist_observation(
                    conn,
                    platform=self.PLATFORM,
                    provider=self.PROVIDER,
                    collector_name=self.COLLECTOR_NAME,
                    signal_type=SEARCH_RESULT_RANK,
                    observed_at=observed_at,
                    input_hash=ih,
                    external_video_id=video_id,
                    external_channel_id=ext_channel_id,
                    normalized_query=normalized_query,
                    query_text=query,
                    region_code=region_code,
                    language_code=language_code,
                    signal_value_numeric=float(rank),
                )
                repo.link_job_observation(conn, job.id, obs.id)
                result.observations_new += 1

                # VIDEO_TITLE — zero extra quota: already in search.list snippet.
                title = snippet.get("title", "").strip()
                if title:
                    ih_title = make_obs_input_hash(
                        platform=self.PLATFORM,
                        provider=self.PROVIDER,
                        signal_type=VIDEO_TITLE,
                        external_video_id=video_id,
                        external_channel_id=ext_channel_id,
                        normalized_query=None,
                        region_code=region_code,
                        language_code=language_code,
                        date_bucket=date_bucket,
                    )
                    obs_title = repo.persist_observation(
                        conn,
                        platform=self.PLATFORM,
                        provider=self.PROVIDER,
                        collector_name=self.COLLECTOR_NAME,
                        signal_type=VIDEO_TITLE,
                        observed_at=observed_at,
                        input_hash=ih_title,
                        external_video_id=video_id,
                        external_channel_id=ext_channel_id,
                        region_code=region_code,
                        language_code=language_code,
                        signal_value_text=title,
                    )
                    repo.link_job_observation(conn, job.id, obs_title.id)
                    result.observations_new += 1

            if video_ids:
                self._enrich_videos(
                    conn,
                    job,
                    video_ids,
                    observed_at,
                    date_bucket,
                    region_code,
                    language_code,
                    result,
                    max_enrichment_calls,
                )

            page_token = search_data.get("nextPageToken")
            if not page_token:
                break

        final_status = "partial" if result.partial_failures else "completed"
        repo.update_job_status(
            conn,
            job.id,
            status=final_status,
            observation_count=result.observations_total,
            quota_consumed_total=self._total_quota(conn, job.id),
            completed_at=_now(),
        )
        result.status = final_status
        return result

    # ------------------------------------------------------------------
    # Enrichment: batchGetStats (primary) → videos.list (fallback)
    # ------------------------------------------------------------------

    def _enrich_videos(
        self,
        conn,
        job: MarketCollectionJob,
        video_ids: list[str],
        observed_at: str,
        date_bucket: str,
        region_code: str | None,
        language_code: str | None,
        result: CollectionResult,
        max_enrichment_calls: int | None,
    ) -> None:
        enrichment_calls_made = 0
        for batch_start in range(0, len(video_ids), _VIDEOS_BATCH_SIZE):
            if max_enrichment_calls is not None and enrichment_calls_made >= max_enrichment_calls:
                break
            batch = video_ids[batch_start : batch_start + _VIDEOS_BATCH_SIZE]

            used_fallback = self._enrich_batch(
                conn,
                job,
                batch,
                observed_at,
                date_bucket,
                region_code,
                language_code,
                result,
            )
            if used_fallback is not None:  # None means the batch was skipped entirely
                enrichment_calls_made += 1
                result.enrichment_calls += 1

    def _enrich_batch(
        self,
        conn,
        job: MarketCollectionJob,
        batch: list[str],
        observed_at: str,
        date_bucket: str,
        region_code: str | None,
        language_code: str | None,
        result: CollectionResult,
    ) -> bool | None:
        """Try batchGetStats; fall back to videos.list on endpoint-not-found.

        Returns True  → used fallback (videos.list)
                False → used batchGetStats
                None  → batch skipped (non-fallback error)
        """
        params = {
            "id": ",".join(batch),
            "part": "snippet,statistics,contentDetails",
            "key": self._api_key,
        }

        # --- primary: batchGetStats ---
        try:
            resp = self._client.get(_BATCH_STATS_ENDPOINT, params=params)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code in _BATCH_STATS_FALLBACK_STATUS_CODES:
                # Endpoint not available in this API version → authorised fallback
                result.partial_failures.append(
                    f"batchGetStats unavailable (HTTP {status_code}), falling back to videos.list"
                )
                return self._enrich_batch_videos_list(
                    conn,
                    job,
                    batch,
                    observed_at,
                    date_bucket,
                    region_code,
                    language_code,
                    result,
                )
            else:
                # Auth error, quota exhaustion, server error — do NOT circumvent with fallback
                result.partial_failures.append(f"batchGetStats error (HTTP {status_code}): {exc}")
                return None
        except httpx.HTTPError as exc:
            # Network / transport error — do not fall back
            result.partial_failures.append(f"batchGetStats network error: {exc}")
            return None

        # --- success: record quota and persist observations ---
        repo.record_quota_usage(
            conn,
            job_id=job.id,
            provider=self.PROVIDER,
            operation="videos.batchGetStats",
            quota_bucket="video_stats_batch",
            units_consumed=_BATCH_STATS_QUOTA,
            call_count=1,
            window_type="daily",
            observed_at=observed_at,
        )

        data = resp.json()
        items = data.get("items", [])
        failed_ids = data.get("failedVideoIds", [])
        if failed_ids:
            result.partial_failures.append(f"batchGetStats failedVideoIds: {failed_ids}")

        returned_ids = {item.get("id") for item in items}
        missing = [
            vid for vid in batch if vid not in returned_ids and vid not in (failed_ids or [])
        ]
        if missing:
            result.partial_failures.append(f"videos not returned by provider: {missing}")

        for item in items:
            self._persist_video_observations(
                conn,
                job,
                item,
                observed_at,
                date_bucket,
                region_code,
                language_code,
                result,
            )
        return False

    def _enrich_batch_videos_list(
        self,
        conn,
        job: MarketCollectionJob,
        batch: list[str],
        observed_at: str,
        date_bucket: str,
        region_code: str | None,
        language_code: str | None,
        result: CollectionResult,
    ) -> bool | None:
        """Fallback enrichment path using videos.list."""
        try:
            resp = self._client.get(
                f"{_YOUTUBE_BASE}/videos",
                params={
                    "id": ",".join(batch),
                    "part": "snippet,statistics,contentDetails",
                    "key": self._api_key,
                },
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            result.partial_failures.append(f"videos.list fallback error: {exc}")
            return None

        repo.record_quota_usage(
            conn,
            job_id=job.id,
            provider=self.PROVIDER,
            operation="videos.list",
            quota_bucket="general_data_api",
            units_consumed=_VIDEOS_LIST_QUOTA,
            call_count=1,
            window_type="daily",
            observed_at=observed_at,
        )

        items = resp.json().get("items", [])
        returned_ids = {item.get("id") for item in items}
        missing = [vid for vid in batch if vid not in returned_ids]
        if missing:
            result.partial_failures.append(f"videos.list: videos not returned: {missing}")

        for item in items:
            self._persist_video_observations(
                conn,
                job,
                item,
                observed_at,
                date_bucket,
                region_code,
                language_code,
                result,
            )
        return True

    # ------------------------------------------------------------------
    # Observation persistence
    # ------------------------------------------------------------------

    def _persist_video_observations(
        self,
        conn,
        job: MarketCollectionJob,
        item: dict,
        observed_at: str,
        date_bucket: str,
        region_code: str | None,
        language_code: str | None,
        result: CollectionResult,
    ) -> None:
        """Persist all available signal observations for one video item.

        Handles both batchGetStats (flat top-level fields) and videos.list
        (nested statistics / contentDetails / snippet) response shapes.
        """
        video_id: str = item.get("id", "")
        if not video_id:
            return

        snippet = item.get("snippet", {})
        ext_channel_id = item.get("channelId") or snippet.get("channelId")
        category_id = item.get("categoryId") or snippet.get("categoryId")
        published_at = _extract_published_at(item)
        duration_secs = _extract_duration_secs(item)
        fingerprint = compute_payload_fingerprint(item)

        content_age_days: float | None = None
        if published_at:
            try:
                pub_dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
                obs_dt = datetime.fromisoformat(observed_at).replace(tzinfo=UTC)
                content_age_days = float((obs_dt - pub_dt).days)
            except (ValueError, TypeError):
                pass

        def _persist(signal_type: str, value_numeric: float | None, value_text: str | None = None):
            ih = make_obs_input_hash(
                platform=self.PLATFORM,
                provider=self.PROVIDER,
                signal_type=signal_type,
                external_video_id=video_id,
                external_channel_id=ext_channel_id,
                normalized_query=None,
                region_code=region_code,
                language_code=language_code,
                date_bucket=date_bucket,
            )
            obs = repo.persist_observation(
                conn,
                platform=self.PLATFORM,
                provider=self.PROVIDER,
                collector_name=self.COLLECTOR_NAME,
                signal_type=signal_type,
                observed_at=observed_at,
                input_hash=ih,
                external_video_id=video_id,
                external_channel_id=ext_channel_id,
                category_id=category_id,
                region_code=region_code,
                language_code=language_code,
                signal_value_numeric=value_numeric,
                signal_value_text=value_text,
                content_published_at=published_at,
                content_age_days=content_age_days,
                provider_payload_fingerprint=fingerprint,
            )
            repo.link_job_observation(conn, job.id, obs.id)
            result.observations_new += 1

        # Persist only fields present in the response — 0 ≠ missing
        present, val = get_stat_value(item, "viewCount")
        if present:
            _persist(VIDEO_VIEW_COUNT, val)

        present, val = get_stat_value(item, "likeCount")
        if present:
            _persist(VIDEO_LIKE_COUNT, val)

        present, val = get_stat_value(item, "commentCount")
        if present:
            _persist(VIDEO_COMMENT_COUNT, val)

        if duration_secs is not None:
            _persist(VIDEO_DURATION_SECONDS, duration_secs)

        if published_at:
            _persist(VIDEO_PUBLISHED_AT, None, value_text=published_at)

        # VIDEO_TITLE — only available when videos.list fallback provides snippet.
        # batchGetStats (primary path) does not return snippet, so no title there.
        title = snippet.get("title", "").strip()
        if title:
            _persist(VIDEO_TITLE, None, value_text=title)

    # ------------------------------------------------------------------
    # Velocity rescan (Phase 13C)
    # ------------------------------------------------------------------

    def collect_velocity_rescan(
        self,
        conn,
        job: MarketCollectionJob,
        *,
        max_videos: int = 50,
        min_age_seconds: int = 6 * 3600,
        max_tracking_age_days: int = 365,
        max_batch_calls: int | None = None,
        signal_type: str = VIDEO_VIEW_COUNT,
    ) -> CollectionResult:
        """Refresh statistics for previously-observed videos and compute velocity intervals.

        Flow: pending → running → select candidates → batchGetStats batches →
              new observations → velocity calculation → completed/partial/failed.

        No search.list call is made. Only the video_stats_batch quota bucket is consumed.
        Velocity calculation runs in-process after all stats are refreshed.
        """
        from app.intelligence.market import repository as repo_mod
        from app.intelligence.market.velocity import calculate_and_persist_velocity_for_video

        observed_at = _now()
        date_bucket = observed_at[:10]
        result = CollectionResult(job_id=job.id, status="running")

        repo.update_job_status(conn, job.id, status="running", started_at=observed_at)

        # --- 1. Candidate selection (deduplicated) ---
        candidates = repo_mod.select_rescan_candidates(
            conn,
            provider=self.PROVIDER,
            platform=self.PLATFORM,
            signal_type=signal_type,
            min_age_seconds=min_age_seconds,
            max_tracking_age_days=max_tracking_age_days,
            max_videos=max_videos,
        )

        if not candidates:
            repo.update_job_status(
                conn,
                job.id,
                status="completed",
                observation_count=0,
                quota_consumed_total=0,
                completed_at=_now(),
            )
            result.status = "completed"
            return result

        refreshed_video_ids: list[str] = []
        batch_calls_made = 0

        # --- 2. Batch refresh ---
        for batch_start in range(0, len(candidates), _VIDEOS_BATCH_SIZE):
            if max_batch_calls is not None and batch_calls_made >= max_batch_calls:
                break
            batch = candidates[batch_start : batch_start + _VIDEOS_BATCH_SIZE]

            used_fallback = self._enrich_batch(
                conn,
                job,
                batch,
                observed_at,
                date_bucket,
                None,
                None,  # no region/language context for rescan observations
                result,
            )
            if used_fallback is not None:
                batch_calls_made += 1
                result.enrichment_calls += 1
                refreshed_video_ids.extend(batch)

        # --- 3. Velocity calculation for all refreshed videos ---
        velocity_estimates_created = 0
        for vid in dict.fromkeys(refreshed_video_ids):  # preserve order, deduplicate
            estimates = calculate_and_persist_velocity_for_video(
                conn,
                vid,
                provider=self.PROVIDER,
                platform=self.PLATFORM,
                signal_type=signal_type,
            )
            velocity_estimates_created += len(estimates)

        final_status = "partial" if result.partial_failures else "completed"
        repo.update_job_status(
            conn,
            job.id,
            status=final_status,
            observation_count=result.observations_total,
            quota_consumed_total=self._total_quota(conn, job.id),
            completed_at=_now(),
        )
        result.status = final_status
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _total_quota(self, conn, job_id: int) -> int:
        return sum(r.units_consumed for r in repo.get_job_quota_usage(conn, job_id))
