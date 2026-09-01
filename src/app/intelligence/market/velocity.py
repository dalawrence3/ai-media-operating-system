"""Phase 13C — Repeated Observation / View Velocity Engine.

Transforms static market observations into temporal velocity evidence.

Velocity semantics (V1):
  - Primary signal: VIDEO_VIEW_COUNT (views per day)
  - Each velocity estimate covers one adjacent observation pair (T_start → T_end)
  - Multiple adjacent intervals build a time series that Phase 13E can use to
    detect acceleration (velocity increasing) or deceleration (velocity decreasing)
  - units_per_day is the canonical human-readable velocity unit
  - units_per_hour is stored for precision on short intervals

Pairing policy (V1):
  - Adjacent pairs ordered chronologically: (obs[0],obs[1]), (obs[1],obs[2]), …
  - All valid intervals within the MAX_WINDOW are retained (not just latest two)
  - This preserves acceleration readiness for Phase 13E

Minimum gap: MIN_GAP_SECONDS = 6 hours
  - YouTube view counts update within minutes, but meaningful velocity requires
    temporal separation. 6 hours balances cold-start trend detection against
    spurious short-interval noise.

Maximum window: MAX_WINDOW_SECONDS = 30 days
  - Velocity over longer windows conflates different demand phases.
  - Phase 13E will segment and compare intervals; Phase 13C only produces them.

Negative delta handling:
  - Provider count corrections occasionally produce views_T2 < views_T1.
  - raw_delta is preserved (can be negative).
  - is_negative_delta=1 flags these as anomaly/correction events.
  - units_per_day / units_per_hour use the raw_delta (caller decides to clamp).

Generic design:
  - signal_type parameter allows the same machinery for like_velocity / comment_velocity.
  - V1 requires only VIDEO_VIEW_COUNT; secondary signals are opt-in.

Calculation version: 'v1'
  - input_hash encodes calculation_version so formula changes create new rows
    rather than silently reinterpreting historical intervals.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime

from app.intelligence.market import repository as repo
from app.intelligence.market.models import (
    VIDEO_VIEW_COUNT,
    VelocityEstimate,
    velocity_maturity_from_interval_count,
)

# ---------------------------------------------------------------------------
# Configuration constants (V1 defaults — override in tests/callers as needed)
# ---------------------------------------------------------------------------

MIN_GAP_SECONDS: int = 6 * 3600  # 6 hours — minimum interval for meaningful velocity
MAX_WINDOW_SECONDS: int = 30 * 86400  # 30 days — maximum interval to include in a calculation

VELOCITY_CALCULATION_VERSION: str = "v1"

# Default rescan staleness threshold: eligible when latest observation is ≥ this old.
DEFAULT_MIN_RESCAN_AGE_SECONDS: int = MIN_GAP_SECONDS  # 6h — same as min gap


# ---------------------------------------------------------------------------
# Input hash
# ---------------------------------------------------------------------------


def make_velocity_input_hash(
    *,
    platform: str,
    provider: str,
    external_video_id: str,
    signal_type: str,
    start_observation_id: int,
    end_observation_id: int,
    calculation_version: str,
) -> str:
    """Deterministic SHA-256 identity for one velocity interval.

    Same (start_obs_id, end_obs_id, version) → same hash → INSERT OR IGNORE deduplicates.
    Formula change → new version → new hash → new row preserving history.
    """
    payload = {
        "platform": platform,
        "provider": provider,
        "external_video_id": external_video_id,
        "signal_type": signal_type,
        "start_observation_id": start_observation_id,
        "end_observation_id": end_observation_id,
        "calculation_version": calculation_version,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=True).encode()
    ).hexdigest()


# ---------------------------------------------------------------------------
# Pure calculation (no DB side effects)
# ---------------------------------------------------------------------------


def _parse_ts(ts: str) -> datetime:
    """Parse ISO 8601 timestamp to UTC-aware datetime."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=UTC)


def _parse_published_at(s: str | None) -> datetime | None:
    if s is None:
        return None
    try:
        return _parse_ts(s)
    except (ValueError, TypeError):
        return None


def compute_velocity_interval(
    *,
    start_value: float,
    end_value: float,
    start_time: str,
    end_time: str,
    content_published_at: str | None = None,
    min_gap_seconds: int = MIN_GAP_SECONDS,
    max_window_seconds: int = MAX_WINDOW_SECONDS,
) -> dict | None:
    """Compute one velocity interval from two scalar observations.

    Returns a dict of computed fields if the interval is valid, or None if:
    - elapsed_seconds < min_gap_seconds (insufficient temporal separation)
    - elapsed_seconds > max_window_seconds (window too wide)
    - start_time >= end_time (observations in wrong order)

    Returned dict keys:
      raw_delta, elapsed_seconds, units_per_hour, units_per_day,
      is_negative_delta, video_age_hours_at_start, video_age_hours_at_end
    """
    try:
        t_start = _parse_ts(start_time)
        t_end = _parse_ts(end_time)
    except (ValueError, TypeError):
        return None

    elapsed = (t_end - t_start).total_seconds()
    if elapsed < min_gap_seconds or elapsed > max_window_seconds or elapsed <= 0:
        return None

    raw_delta = end_value - start_value
    is_negative = raw_delta < 0

    units_per_hour = raw_delta / (elapsed / 3600)
    units_per_day = raw_delta / (elapsed / 86400)

    # Content age context (useful for Phase 13E growth-phase classification)
    pub_dt = _parse_published_at(content_published_at)
    age_at_start: float | None = None
    age_at_end: float | None = None
    if pub_dt is not None:
        age_at_start = (t_start - pub_dt).total_seconds() / 3600
        age_at_end = (t_end - pub_dt).total_seconds() / 3600

    return {
        "raw_delta": raw_delta,
        "elapsed_seconds": elapsed,
        "units_per_hour": units_per_hour,
        "units_per_day": units_per_day,
        "is_negative_delta": is_negative,
        "video_age_hours_at_start": age_at_start,
        "video_age_hours_at_end": age_at_end,
    }


# ---------------------------------------------------------------------------
# Database pipeline
# ---------------------------------------------------------------------------


def calculate_and_persist_velocity_for_video(
    conn: sqlite3.Connection,
    external_video_id: str,
    *,
    provider: str = "youtube_data_api",
    platform: str = "youtube",
    signal_type: str = VIDEO_VIEW_COUNT,
    min_gap_seconds: int = MIN_GAP_SECONDS,
    max_window_seconds: int = MAX_WINDOW_SECONDS,
    calculation_version: str = VELOCITY_CALCULATION_VERSION,
) -> list[VelocityEstimate]:
    """Compute and persist all eligible adjacent-pair velocity intervals for a video.

    Reads the full chronological observation history for the video/signal,
    iterates over adjacent pairs, skips pairs that fail gap/window validation,
    and persists each valid interval via INSERT OR IGNORE (idempotent).

    Returns all estimates that exist after the call (new + previously persisted).
    The list is ordered end_time DESC (most recent first).
    """
    history = repo.get_video_observation_history(
        conn,
        external_video_id,
        provider=provider,
        platform=platform,
        signal_type=signal_type,
    )
    if len(history) < 2:
        # Fewer than 2 observations → no interval possible → velocity unavailable
        return []

    # Compute and persist new intervals for each adjacent pair
    for i in range(len(history) - 1):
        obs_start = history[i]
        obs_end = history[i + 1]

        if obs_start.signal_value_numeric is None or obs_end.signal_value_numeric is None:
            continue

        calc = compute_velocity_interval(
            start_value=obs_start.signal_value_numeric,
            end_value=obs_end.signal_value_numeric,
            start_time=obs_start.observed_at,
            end_time=obs_end.observed_at,
            content_published_at=obs_start.content_published_at,
            min_gap_seconds=min_gap_seconds,
            max_window_seconds=max_window_seconds,
        )
        if calc is None:
            continue

        ih = make_velocity_input_hash(
            platform=platform,
            provider=provider,
            external_video_id=external_video_id,
            signal_type=signal_type,
            start_observation_id=obs_start.id,
            end_observation_id=obs_end.id,
            calculation_version=calculation_version,
        )

        # Determine maturity based on how many intervals exist after this one
        # is added. Query existing count then assign position-based maturity.
        existing_count = conn.execute(
            """
            SELECT COUNT(*) FROM market_velocity_estimates
            WHERE platform=? AND provider=? AND external_video_id=? AND signal_type=?
              AND calculation_version=?
            """,
            (platform, provider, external_video_id, signal_type, calculation_version),
        ).fetchone()[0]

        maturity = velocity_maturity_from_interval_count(existing_count + 1)

        repo.persist_velocity_estimate(
            conn,
            platform=platform,
            provider=provider,
            external_video_id=external_video_id,
            signal_type=signal_type,
            start_observation_id=obs_start.id,
            end_observation_id=obs_end.id,
            start_time=obs_start.observed_at,
            end_time=obs_end.observed_at,
            start_value=obs_start.signal_value_numeric,
            end_value=obs_end.signal_value_numeric,
            raw_delta=calc["raw_delta"],
            elapsed_seconds=calc["elapsed_seconds"],
            units_per_hour=calc["units_per_hour"],
            units_per_day=calc["units_per_day"],
            is_negative_delta=calc["is_negative_delta"],
            video_age_hours_at_start=calc["video_age_hours_at_start"],
            video_age_hours_at_end=calc["video_age_hours_at_end"],
            velocity_maturity=maturity,
            calculation_version=calculation_version,
            input_hash=ih,
        )

    return repo.get_velocity_estimates_for_video(
        conn,
        external_video_id,
        provider=provider,
        platform=platform,
        signal_type=signal_type,
    )


def calculate_velocity_for_all_refreshed(
    conn: sqlite3.Connection,
    video_ids: list[str],
    *,
    provider: str = "youtube_data_api",
    platform: str = "youtube",
    signal_type: str = VIDEO_VIEW_COUNT,
    min_gap_seconds: int = MIN_GAP_SECONDS,
    max_window_seconds: int = MAX_WINDOW_SECONDS,
    calculation_version: str = VELOCITY_CALCULATION_VERSION,
) -> int:
    """Run velocity calculation for a list of videos. Returns total estimates created."""
    total = 0
    for vid in video_ids:
        estimates = calculate_and_persist_velocity_for_video(
            conn,
            vid,
            provider=provider,
            platform=platform,
            signal_type=signal_type,
            min_gap_seconds=min_gap_seconds,
            max_window_seconds=max_window_seconds,
            calculation_version=calculation_version,
        )
        total += len(estimates)
    return total
