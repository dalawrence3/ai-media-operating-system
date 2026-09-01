"""Phase 13A — Market Intelligence repository (CRUD layer)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from app.intelligence.market.models import (
    MarketCollectionJob,
    MarketIntelligenceObservation,
    MarketJobObservationLink,
    MarketJobQuotaUsage,
    VelocityEstimate,
)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


# ---------------------------------------------------------------------------
# Row converters
# ---------------------------------------------------------------------------


def _row_to_job(row: sqlite3.Row) -> MarketCollectionJob:
    d = dict(row)
    return MarketCollectionJob(**d)


def _row_to_observation(row: sqlite3.Row) -> MarketIntelligenceObservation:
    d = dict(row)
    return MarketIntelligenceObservation(**d)


def _row_to_quota(row: sqlite3.Row) -> MarketJobQuotaUsage:
    d = dict(row)
    return MarketJobQuotaUsage(**d)


def _row_to_link(row: sqlite3.Row) -> MarketJobObservationLink:
    d = dict(row)
    return MarketJobObservationLink(**d)


# ---------------------------------------------------------------------------
# MarketCollectionJob
# ---------------------------------------------------------------------------


def create_market_collection_job(
    conn: sqlite3.Connection,
    *,
    job_type: str,
    origin_type: str = "manual",
    provider: str = "youtube_data_api",
    platform: str = "youtube",
    workspace_id: str | None = None,
    channel_id: int | None = None,
    parent_job_id: int | None = None,
    exploration_depth: int = 0,
    seeds: list[str] | None = None,
    quota_policy_snapshot: dict[str, Any] | None = None,
    scheduled_for: str | None = None,
) -> MarketCollectionJob:
    seeds_json = json.dumps(seeds or [])
    quota_snapshot_json = json.dumps(quota_policy_snapshot) if quota_policy_snapshot else None
    now = _now()
    cursor = conn.execute(
        """
        INSERT INTO market_collection_jobs
            (workspace_id, channel_id, job_type, origin_type, parent_job_id,
             exploration_depth, provider, platform, seeds_json,
             quota_policy_snapshot_json, status, observation_count,
             quota_consumed_total, scheduled_for, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,'pending',0,0,?,?)
        """,
        (
            workspace_id,
            channel_id,
            job_type,
            origin_type,
            parent_job_id,
            exploration_depth,
            provider,
            platform,
            seeds_json,
            quota_snapshot_json,
            scheduled_for,
            now,
        ),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM market_collection_jobs WHERE id=?", (cursor.lastrowid,)
    ).fetchone()
    return _row_to_job(row)


def get_market_collection_job(conn: sqlite3.Connection, job_id: int) -> MarketCollectionJob | None:
    row = conn.execute("SELECT * FROM market_collection_jobs WHERE id=?", (job_id,)).fetchone()
    return _row_to_job(row) if row else None


def list_market_collection_jobs(
    conn: sqlite3.Connection,
    *,
    channel_id: int | None = None,
    workspace_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[MarketCollectionJob]:
    clauses: list[str] = []
    params: list[Any] = []
    if channel_id is not None:
        clauses.append("channel_id=?")
        params.append(channel_id)
    if workspace_id is not None:
        clauses.append("workspace_id=?")
        params.append(workspace_id)
    if status is not None:
        clauses.append("status=?")
        params.append(status)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    rows = conn.execute(
        f"SELECT * FROM market_collection_jobs {where} ORDER BY created_at DESC LIMIT ?",
        params,
    ).fetchall()
    return [_row_to_job(r) for r in rows]


def update_job_status(
    conn: sqlite3.Connection,
    job_id: int,
    *,
    status: str,
    observation_count: int | None = None,
    quota_consumed_total: int | None = None,
    error_message: str | None = None,
    failure_stage: str | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
) -> None:
    sets: list[str] = ["status=?"]
    params: list[Any] = [status]
    if observation_count is not None:
        sets.append("observation_count=?")
        params.append(observation_count)
    if quota_consumed_total is not None:
        sets.append("quota_consumed_total=?")
        params.append(quota_consumed_total)
    if error_message is not None:
        sets.append("error_message=?")
        params.append(error_message)
    if failure_stage is not None:
        sets.append("failure_stage=?")
        params.append(failure_stage)
    if started_at is not None:
        sets.append("started_at=?")
        params.append(started_at)
    if completed_at is not None:
        sets.append("completed_at=?")
        params.append(completed_at)
    params.append(job_id)
    conn.execute(f"UPDATE market_collection_jobs SET {', '.join(sets)} WHERE id=?", params)
    conn.commit()


# ---------------------------------------------------------------------------
# MarketIntelligenceObservation (immutable, idempotent)
# ---------------------------------------------------------------------------


def persist_observation(
    conn: sqlite3.Connection,
    *,
    platform: str = "youtube",
    provider: str = "youtube_data_api",
    collector_name: str,
    signal_type: str,
    observed_at: str,
    input_hash: str,
    external_video_id: str | None = None,
    external_channel_id: str | None = None,
    query_text: str | None = None,
    normalized_query: str | None = None,
    region_code: str | None = None,
    language_code: str | None = None,
    category_id: str | None = None,
    signal_value_numeric: float | None = None,
    signal_value_text: str | None = None,
    content_published_at: str | None = None,
    content_age_days: float | None = None,
    provider_payload_fingerprint: str | None = None,
    retain_until: str | None = None,
) -> MarketIntelligenceObservation:
    """Persist an observation idempotently.

    Same-day (same input_hash) → returns the existing row without touching it.
    Different day → input_hash differs, so a new row is inserted (velocity tracking).
    """
    now = _now()
    conn.execute(
        """
        INSERT OR IGNORE INTO market_intelligence_observations
            (platform, provider, collector_name, external_video_id, external_channel_id,
             query_text, normalized_query, region_code, language_code, category_id,
             signal_type, signal_value_numeric, signal_value_text,
             content_published_at, content_age_days, observed_at,
             provider_payload_fingerprint, input_hash, retain_until, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            platform,
            provider,
            collector_name,
            external_video_id,
            external_channel_id,
            query_text,
            normalized_query,
            region_code,
            language_code,
            category_id,
            signal_type,
            signal_value_numeric,
            signal_value_text,
            content_published_at,
            content_age_days,
            observed_at,
            provider_payload_fingerprint,
            input_hash,
            retain_until,
            now,
        ),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM market_intelligence_observations WHERE input_hash=?",
        (input_hash,),
    ).fetchone()
    return _row_to_observation(row)


def get_observation(
    conn: sqlite3.Connection, observation_id: int
) -> MarketIntelligenceObservation | None:
    row = conn.execute(
        "SELECT * FROM market_intelligence_observations WHERE id=?", (observation_id,)
    ).fetchone()
    return _row_to_observation(row) if row else None


def list_observations_for_video(
    conn: sqlite3.Connection,
    external_video_id: str,
    *,
    signal_type: str | None = None,
    limit: int = 100,
) -> list[MarketIntelligenceObservation]:
    if signal_type is not None:
        rows = conn.execute(
            """
            SELECT * FROM market_intelligence_observations
            WHERE external_video_id=? AND signal_type=?
            ORDER BY observed_at DESC LIMIT ?
            """,
            (external_video_id, signal_type, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM market_intelligence_observations
            WHERE external_video_id=?
            ORDER BY observed_at DESC LIMIT ?
            """,
            (external_video_id, limit),
        ).fetchall()
    return [_row_to_observation(r) for r in rows]


def list_observations_for_query(
    conn: sqlite3.Connection,
    normalized_query: str,
    *,
    signal_type: str | None = None,
    limit: int = 100,
) -> list[MarketIntelligenceObservation]:
    if signal_type is not None:
        rows = conn.execute(
            """
            SELECT * FROM market_intelligence_observations
            WHERE normalized_query=? AND signal_type=?
            ORDER BY observed_at DESC LIMIT ?
            """,
            (normalized_query, signal_type, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM market_intelligence_observations
            WHERE normalized_query=?
            ORDER BY observed_at DESC LIMIT ?
            """,
            (normalized_query, limit),
        ).fetchall()
    return [_row_to_observation(r) for r in rows]


def get_job_observations(
    conn: sqlite3.Connection, job_id: int
) -> list[MarketIntelligenceObservation]:
    rows = conn.execute(
        """
        SELECT o.* FROM market_intelligence_observations o
        JOIN market_job_observations jl ON jl.observation_id = o.id
        WHERE jl.job_id=?
        ORDER BY jl.discovered_at DESC
        """,
        (job_id,),
    ).fetchall()
    return [_row_to_observation(r) for r in rows]


# ---------------------------------------------------------------------------
# Job ↔ Observation link
# ---------------------------------------------------------------------------


def link_job_observation(conn: sqlite3.Connection, job_id: int, observation_id: int) -> None:
    """Record that a job discovered an observation (idempotent via INSERT OR IGNORE)."""
    conn.execute(
        "INSERT OR IGNORE INTO market_job_observations (job_id, observation_id, discovered_at) "
        "VALUES (?,?,?)",
        (job_id, observation_id, _now()),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Quota usage
# ---------------------------------------------------------------------------


def record_quota_usage(
    conn: sqlite3.Connection,
    *,
    job_id: int,
    provider: str,
    operation: str,
    quota_bucket: str,
    units_consumed: int,
    call_count: int = 1,
    limit_snapshot: int | None = None,
    window_type: str = "daily",
    observed_at: str | None = None,
) -> MarketJobQuotaUsage:
    """Upsert quota usage for a (job, provider, operation, bucket) tuple.

    On conflict (same job+provider+operation+bucket) accumulates units and calls.
    """
    ts = observed_at or _now()
    conn.execute(
        """
        INSERT INTO market_job_quota_usage
            (job_id, provider, operation, quota_bucket, units_consumed, call_count,
             limit_snapshot, window_type, observed_at)
        VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT (job_id, provider, operation, quota_bucket)
        DO UPDATE SET
            units_consumed = units_consumed + excluded.units_consumed,
            call_count     = call_count     + excluded.call_count,
            limit_snapshot = excluded.limit_snapshot,
            observed_at    = excluded.observed_at
        """,
        (
            job_id,
            provider,
            operation,
            quota_bucket,
            units_consumed,
            call_count,
            limit_snapshot,
            window_type,
            ts,
        ),
    )
    conn.commit()
    row = conn.execute(
        """
        SELECT * FROM market_job_quota_usage
        WHERE job_id=? AND provider=? AND operation=? AND quota_bucket=?
        """,
        (job_id, provider, operation, quota_bucket),
    ).fetchone()
    return _row_to_quota(row)


def get_job_quota_usage(conn: sqlite3.Connection, job_id: int) -> list[MarketJobQuotaUsage]:
    rows = conn.execute(
        "SELECT * FROM market_job_quota_usage WHERE job_id=? ORDER BY operation",
        (job_id,),
    ).fetchall()
    return [_row_to_quota(r) for r in rows]


# ---------------------------------------------------------------------------
# Observation history (Phase 13C — velocity)
# ---------------------------------------------------------------------------


def get_video_observation_history(
    conn: sqlite3.Connection,
    external_video_id: str,
    *,
    provider: str = "youtube_data_api",
    platform: str = "youtube",
    signal_type: str = "video_view_count",
    limit: int = 200,
) -> list[MarketIntelligenceObservation]:
    """Return all observations for a video/signal ordered chronologically (ASC).

    Used by the velocity engine to compute adjacent-pair intervals.
    Chronological order is critical — latest two observations form the most
    recent velocity interval.
    """
    rows = conn.execute(
        """
        SELECT * FROM market_intelligence_observations
        WHERE external_video_id=?
          AND provider=?
          AND platform=?
          AND signal_type=?
          AND signal_value_numeric IS NOT NULL
        ORDER BY observed_at ASC
        LIMIT ?
        """,
        (external_video_id, provider, platform, signal_type, limit),
    ).fetchall()
    return [_row_to_observation(r) for r in rows]


def select_rescan_candidates(
    conn: sqlite3.Connection,
    *,
    provider: str = "youtube_data_api",
    platform: str = "youtube",
    signal_type: str = "video_view_count",
    min_age_seconds: int,
    max_tracking_age_days: int = 365,
    max_videos: int = 50,
) -> list[str]:
    """Return deduplicated video IDs eligible for a velocity rescan.

    Eligibility:
    - Has at least one observation for the given signal_type
    - Latest observation is at least min_age_seconds old (ready for refresh)
    - Content was not published more than max_tracking_age_days ago (optional)
    - external_video_id is not NULL
    Ordered by oldest-latest-observation first (most overdue first).
    """
    rows = conn.execute(
        """
        SELECT external_video_id,
               MAX(observed_at) AS latest_obs,
               MIN(content_published_at) AS earliest_pub
        FROM market_intelligence_observations
        WHERE provider=?
          AND platform=?
          AND signal_type=?
          AND external_video_id IS NOT NULL
        GROUP BY external_video_id
        HAVING latest_obs < datetime('now', '-' || ? || ' seconds')
           AND (earliest_pub IS NULL
                OR julianday('now') - julianday(earliest_pub) < ?)
        ORDER BY latest_obs ASC
        LIMIT ?
        """,
        (provider, platform, signal_type, min_age_seconds, max_tracking_age_days, max_videos),
    ).fetchall()
    return [row[0] for row in rows]


# ---------------------------------------------------------------------------
# Velocity estimate persistence (Phase 13C)
# ---------------------------------------------------------------------------


def _row_to_velocity(row: sqlite3.Row) -> VelocityEstimate:
    return VelocityEstimate(**dict(row))


def persist_velocity_estimate(
    conn: sqlite3.Connection,
    *,
    platform: str,
    provider: str,
    external_video_id: str,
    signal_type: str,
    start_observation_id: int,
    end_observation_id: int,
    start_time: str,
    end_time: str,
    start_value: float,
    end_value: float,
    raw_delta: float,
    elapsed_seconds: float,
    units_per_hour: float | None,
    units_per_day: float | None,
    is_negative_delta: bool,
    video_age_hours_at_start: float | None,
    video_age_hours_at_end: float | None,
    velocity_maturity: str,
    calculation_version: str,
    input_hash: str,
) -> VelocityEstimate:
    """Persist a velocity estimate idempotently.

    Same (start_obs, end_obs, calculation_version) → same input_hash →
    INSERT OR IGNORE returns the existing row.
    """
    now = _now()
    conn.execute(
        """
        INSERT OR IGNORE INTO market_velocity_estimates
            (platform, provider, external_video_id, signal_type,
             start_observation_id, end_observation_id,
             start_time, end_time, start_value, end_value,
             raw_delta, elapsed_seconds, units_per_hour, units_per_day,
             is_negative_delta, video_age_hours_at_start, video_age_hours_at_end,
             velocity_maturity, calculation_version, input_hash, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            platform,
            provider,
            external_video_id,
            signal_type,
            start_observation_id,
            end_observation_id,
            start_time,
            end_time,
            start_value,
            end_value,
            raw_delta,
            elapsed_seconds,
            units_per_hour,
            units_per_day,
            1 if is_negative_delta else 0,
            video_age_hours_at_start,
            video_age_hours_at_end,
            velocity_maturity,
            calculation_version,
            input_hash,
            now,
        ),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM market_velocity_estimates WHERE input_hash=?", (input_hash,)
    ).fetchone()
    return _row_to_velocity(row)


def get_velocity_estimates_for_video(
    conn: sqlite3.Connection,
    external_video_id: str,
    *,
    provider: str = "youtube_data_api",
    platform: str = "youtube",
    signal_type: str = "video_view_count",
    limit: int = 100,
) -> list[VelocityEstimate]:
    """Return velocity estimates for a video ordered by end_time DESC (most recent first)."""
    rows = conn.execute(
        """
        SELECT * FROM market_velocity_estimates
        WHERE platform=? AND provider=? AND external_video_id=? AND signal_type=?
        ORDER BY end_time DESC
        LIMIT ?
        """,
        (platform, provider, external_video_id, signal_type, limit),
    ).fetchall()
    return [_row_to_velocity(r) for r in rows]


def get_latest_velocity(
    conn: sqlite3.Connection,
    external_video_id: str,
    *,
    provider: str = "youtube_data_api",
    platform: str = "youtube",
    signal_type: str = "video_view_count",
) -> VelocityEstimate | None:
    """Return the most recent velocity estimate for a video, or None if unavailable."""
    estimates = get_velocity_estimates_for_video(
        conn,
        external_video_id,
        provider=provider,
        platform=platform,
        signal_type=signal_type,
        limit=1,
    )
    return estimates[0] if estimates else None
