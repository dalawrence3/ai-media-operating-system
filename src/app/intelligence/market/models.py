"""Domain models for Phase 13A — Global Market Intelligence."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Signal type constants — kept as Python strings, NOT as DB CHECK constraints,
# so future providers can add new types without schema migrations.
# ---------------------------------------------------------------------------

VIDEO_VIEW_COUNT = "video_view_count"
VIDEO_LIKE_COUNT = "video_like_count"
VIDEO_COMMENT_COUNT = "video_comment_count"
VIDEO_DURATION_SECONDS = "video_duration_seconds"
VIDEO_PUBLISHED_AT = "video_published_at"
VIDEO_TITLE = "video_title"
SEARCH_RESULT_RANK = "search_result_rank"
SEARCH_RESULT_TOTAL_ESTIMATE = "search_result_total_estimate"
CHANNEL_SUBSCRIBER_COUNT = "channel_subscriber_count"
CHANNEL_TOTAL_VIEW_COUNT = "channel_total_view_count"
CHANNEL_VIDEO_COUNT = "channel_video_count"

KNOWN_SIGNAL_TYPES: frozenset[str] = frozenset(
    {
        VIDEO_VIEW_COUNT,
        VIDEO_LIKE_COUNT,
        VIDEO_COMMENT_COUNT,
        VIDEO_DURATION_SECONDS,
        VIDEO_PUBLISHED_AT,
        VIDEO_TITLE,
        SEARCH_RESULT_RANK,
        SEARCH_RESULT_TOTAL_ESTIMATE,
        CHANNEL_SUBSCRIBER_COUNT,
        CHANNEL_TOTAL_VIEW_COUNT,
        CHANNEL_VIDEO_COUNT,
    }
)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class MarketJobType(StrEnum):
    SEARCH_SCAN = "search_scan"
    VELOCITY_RESCAN = "velocity_rescan"
    COMPETITOR_SCAN = "competitor_scan"
    CHANNEL_STATS = "channel_stats"


class MarketJobOriginType(StrEnum):
    MANUAL = "manual"
    CHANNEL_BOOTSTRAP = "channel_bootstrap"
    EXPLORATION_PLANNER = "exploration_planner"
    ADJACENT_TOPIC = "adjacent_topic"
    REFRESH = "refresh"
    VELOCITY_RESCAN = "velocity_rescan"


class MarketJobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class QuotaWindowType(StrEnum):
    DAILY = "daily"
    PER_REQUEST = "per_request"
    PER_MINUTE = "per_minute"


# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------


class MarketCollectionJob(BaseModel):
    id: int
    workspace_id: str | None = None
    channel_id: int | None = None
    job_type: str
    origin_type: str = "manual"
    parent_job_id: int | None = None
    exploration_depth: int = 0
    provider: str = "youtube_data_api"
    platform: str = "youtube"
    seeds_json: str = "[]"
    quota_policy_snapshot_json: str | None = None
    status: str = "pending"
    observation_count: int = 0
    quota_consumed_total: int = 0
    error_message: str | None = None
    failure_stage: str | None = None
    scheduled_for: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    created_at: str

    def seeds(self) -> list[str]:
        import json

        return json.loads(self.seeds_json)

    def quota_policy(self) -> dict[str, Any] | None:
        import json

        if self.quota_policy_snapshot_json is None:
            return None
        return json.loads(self.quota_policy_snapshot_json)


class MarketIntelligenceObservation(BaseModel):
    id: int
    platform: str = "youtube"
    provider: str = "youtube_data_api"
    collector_name: str
    external_video_id: str | None = None
    external_channel_id: str | None = None
    query_text: str | None = None
    normalized_query: str | None = None
    region_code: str | None = None
    language_code: str | None = None
    category_id: str | None = None
    signal_type: str
    signal_value_numeric: float | None = None
    signal_value_text: str | None = None
    content_published_at: str | None = None
    content_age_days: float | None = None
    observed_at: str
    provider_payload_fingerprint: str | None = None
    input_hash: str
    retain_until: str | None = None
    created_at: str


class MarketJobObservationLink(BaseModel):
    job_id: int
    observation_id: int
    discovered_at: str


class MarketJobQuotaUsage(BaseModel):
    id: int
    job_id: int
    provider: str
    operation: str
    quota_bucket: str
    units_consumed: int = 0
    call_count: int = 1
    limit_snapshot: int | None = None
    window_type: str = "daily"
    observed_at: str


# ---------------------------------------------------------------------------
# Velocity maturity — temporal evidence depth for a video's observed statistics.
# Distinct from cross-publication learning maturity (which counts publications).
# ---------------------------------------------------------------------------

VELOCITY_MATURITY_INSUFFICIENT = "insufficient"  # < 2 eligible observations; no interval
VELOCITY_MATURITY_EARLY = "early"  # 1 interval (2 observations)
VELOCITY_MATURITY_ESTABLISHING = "establishing"  # 2–4 intervals
VELOCITY_MATURITY_MATURE = "mature"  # 5+ intervals

VELOCITY_MATURITY_ORDERED = (
    VELOCITY_MATURITY_INSUFFICIENT,
    VELOCITY_MATURITY_EARLY,
    VELOCITY_MATURITY_ESTABLISHING,
    VELOCITY_MATURITY_MATURE,
)


def velocity_maturity_from_interval_count(n: int) -> str:
    """Return velocity maturity label for n completed intervals (adjacent pairs).

    n=0 → insufficient (no completed interval yet)
    n=1 → early
    n=2..4 → establishing
    n≥5 → mature
    """
    if n <= 0:
        return VELOCITY_MATURITY_INSUFFICIENT
    if n == 1:
        return VELOCITY_MATURITY_EARLY
    if n <= 4:
        return VELOCITY_MATURITY_ESTABLISHING
    return VELOCITY_MATURITY_MATURE


class VelocityEstimate(BaseModel):
    """Derived temporal velocity estimate computed from two market observations.

    Each row represents one observation interval (start → end) for one video
    and signal type.  Multiple rows for the same video build a time series that
    Phase 13E can use to detect acceleration or deceleration.
    """

    id: int
    platform: str = "youtube"
    provider: str = "youtube_data_api"
    external_video_id: str
    signal_type: str = "video_view_count"
    start_observation_id: int
    end_observation_id: int
    start_time: str
    end_time: str
    start_value: float
    end_value: float
    raw_delta: float  # end_value - start_value (may be negative on provider correction)
    elapsed_seconds: float
    units_per_hour: float | None = None
    units_per_day: float | None = None
    is_negative_delta: int = 0  # 1 = provider count decreased; anomaly / correction event
    video_age_hours_at_start: float | None = None
    video_age_hours_at_end: float | None = None
    velocity_maturity: str = VELOCITY_MATURITY_EARLY
    calculation_version: str = "v1"
    input_hash: str
    created_at: str
