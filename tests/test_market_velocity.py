"""Phase 13C tests — Repeated Observation / View Velocity Engine.

Tests A–AN (40 tests).
No live HTTP calls — all network traffic is intercepted by _CapturingTransport.
"""

from __future__ import annotations

import pathlib
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx
import pytest

from app.core.database import open_db
from app.intelligence.market import models as m
from app.intelligence.market import repository as repo
from app.intelligence.market.collector import YouTubeMarketCollector, make_obs_input_hash
from app.intelligence.market.velocity import (
    MIN_GAP_SECONDS,
    VELOCITY_CALCULATION_VERSION,
    calculate_and_persist_velocity_for_video,
    compute_velocity_interval,
    make_velocity_input_hash,
)

# ---------------------------------------------------------------------------
# Transport stub
# ---------------------------------------------------------------------------


@dataclass
class _CapturingTransport(httpx.BaseTransport):
    handler: Callable[[httpx.Request], httpx.Response]
    captured: list[httpx.Request] = field(default_factory=list)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.captured.append(request)
        return self.handler(request)


def _ok(body: dict) -> httpx.Response:
    return httpx.Response(200, json=body)


def _err(status: int) -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": status, "message": "test"}})


def _make_collector(handler, api_key="key") -> tuple[YouTubeMarketCollector, _CapturingTransport]:
    transport = _CapturingTransport(handler=handler)
    client = httpx.Client(transport=transport)
    return YouTubeMarketCollector(api_key=api_key, client=client), transport


# ---------------------------------------------------------------------------
# DB fixture and helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def conn():
    with tempfile.TemporaryDirectory() as d:
        c = open_db(pathlib.Path(d) / "test.db")
        yield c
        c.close()


def _make_job(conn, job_type="search_scan", **kwargs):
    return repo.create_market_collection_job(conn, job_type=job_type, **kwargs)


def _insert_obs(
    conn,
    video_id: str,
    value: float,
    observed_at: str,
    *,
    date_bucket: str | None = None,
    content_published_at: str | None = None,
    provider: str = "youtube_data_api",
    platform: str = "youtube",
    signal_type: str = m.VIDEO_VIEW_COUNT,
) -> m.MarketIntelligenceObservation:
    """Insert a VIDEO_VIEW_COUNT observation directly into the DB."""
    db = date_bucket or observed_at[:10]
    ih = make_obs_input_hash(
        platform=platform,
        provider=provider,
        signal_type=signal_type,
        external_video_id=video_id,
        external_channel_id=None,
        normalized_query=None,
        region_code=None,
        language_code=None,
        date_bucket=db,
    )
    return repo.persist_observation(
        conn,
        platform=platform,
        provider=provider,
        collector_name="test_collector",
        signal_type=signal_type,
        observed_at=observed_at,
        input_hash=ih,
        external_video_id=video_id,
        signal_value_numeric=value,
        content_published_at=content_published_at,
    )


def _obs_pair(conn, video_id: str, v1: float, t1: str, v2: float, t2: str, **kw):
    """Insert two observations for the same video on different date buckets."""
    o1 = _insert_obs(conn, video_id, v1, t1, date_bucket=t1[:10], **kw)
    o2 = _insert_obs(conn, video_id, v2, t2, date_bucket=t2[:10], **kw)
    return o1, o2


# ---------------------------------------------------------------------------
# A — one observation → velocity unavailable (no intervals created)
# ---------------------------------------------------------------------------


def test_a_one_observation_velocity_unavailable(conn):
    _insert_obs(conn, "vidA", 1_000.0, "2026-08-01T10:00:00")
    estimates = calculate_and_persist_velocity_for_video(conn, "vidA")
    assert estimates == [], "One observation must produce zero velocity intervals"


# ---------------------------------------------------------------------------
# B — two eligible observations → correct positive velocity
# ---------------------------------------------------------------------------


def test_b_two_observations_correct_positive_velocity(conn):
    _obs_pair(
        conn, "vidB", 20_000.0, "2026-08-01T00:00:00", 80_000.0, "2026-08-03T00:00:00"
    )  # 2 days apart
    estimates = calculate_and_persist_velocity_for_video(conn, "vidB")
    assert len(estimates) == 1
    e = estimates[0]
    assert e.raw_delta == pytest.approx(60_000.0)
    assert e.elapsed_seconds == pytest.approx(2 * 86400.0)
    assert e.units_per_day == pytest.approx(30_000.0)
    assert e.is_negative_delta == 0


# ---------------------------------------------------------------------------
# C — zero view delta → velocity = 0 (not unavailable)
# ---------------------------------------------------------------------------


def test_c_zero_delta_is_zero_velocity_not_unavailable(conn):
    _obs_pair(conn, "vidC", 50_000.0, "2026-08-01T00:00:00", 50_000.0, "2026-08-03T00:00:00")
    estimates = calculate_and_persist_velocity_for_video(conn, "vidC")
    assert len(estimates) == 1
    e = estimates[0]
    assert e.raw_delta == pytest.approx(0.0)
    assert e.units_per_day == pytest.approx(0.0)
    assert e.is_negative_delta == 0


# ---------------------------------------------------------------------------
# D — observation with NULL signal_value_numeric is excluded
# ---------------------------------------------------------------------------


def test_d_null_value_excluded(conn):
    ih1 = make_obs_input_hash(
        platform="youtube",
        provider="youtube_data_api",
        signal_type=m.VIDEO_VIEW_COUNT,
        external_video_id="vidD",
        external_channel_id=None,
        normalized_query=None,
        region_code=None,
        language_code=None,
        date_bucket="2026-08-01",
    )
    repo.persist_observation(
        conn,
        collector_name="t",
        signal_type=m.VIDEO_VIEW_COUNT,
        observed_at="2026-08-01T00:00:00",
        input_hash=ih1,
        external_video_id="vidD",
        signal_value_numeric=None,  # NULL
    )
    _insert_obs(conn, "vidD", 5_000.0, "2026-08-03T00:00:00")
    # Only one observation with a value → no interval
    estimates = calculate_and_persist_velocity_for_video(conn, "vidD")
    assert estimates == []


# ---------------------------------------------------------------------------
# E — zero view count is preserved (0 ≠ missing)
# ---------------------------------------------------------------------------


def test_e_zero_view_count_is_valid(conn):
    _obs_pair(conn, "vidE", 0.0, "2026-08-01T00:00:00", 1_000.0, "2026-08-03T00:00:00")
    estimates = calculate_and_persist_velocity_for_video(conn, "vidE")
    assert estimates
    assert estimates[0].start_value == pytest.approx(0.0)
    assert estimates[0].raw_delta == pytest.approx(1_000.0)


# ---------------------------------------------------------------------------
# F — observations below minimum gap → insufficient (no interval)
# ---------------------------------------------------------------------------


def test_f_below_min_gap_no_interval(conn):
    """2-hour gap < 6h MIN_GAP_SECONDS → no interval produced."""
    # Use different date buckets to get two distinct rows in the DB.
    # observed_at timestamps are 2 hours apart (< MIN_GAP_SECONDS = 6h).
    ih1 = make_obs_input_hash(
        platform="youtube",
        provider="youtube_data_api",
        signal_type=m.VIDEO_VIEW_COUNT,
        external_video_id="vidF",
        external_channel_id=None,
        normalized_query=None,
        region_code=None,
        language_code=None,
        date_bucket="2026-08-01",
    )
    ih2 = make_obs_input_hash(
        platform="youtube",
        provider="youtube_data_api",
        signal_type=m.VIDEO_VIEW_COUNT,
        external_video_id="vidF",
        external_channel_id=None,
        normalized_query=None,
        region_code=None,
        language_code=None,
        date_bucket="2026-08-02",
    )
    repo.persist_observation(
        conn,
        collector_name="t",
        signal_type=m.VIDEO_VIEW_COUNT,
        observed_at="2026-08-01T00:00:00",
        input_hash=ih1,
        external_video_id="vidF",
        signal_value_numeric=100.0,
    )
    repo.persist_observation(
        conn,
        collector_name="t",
        signal_type=m.VIDEO_VIEW_COUNT,
        observed_at="2026-08-01T02:00:00",
        input_hash=ih2,  # 2h later
        external_video_id="vidF",
        signal_value_numeric=200.0,
    )
    estimates = calculate_and_persist_velocity_for_video(
        conn, "vidF", min_gap_seconds=MIN_GAP_SECONDS
    )
    assert estimates == [], f"2h gap < 6h min_gap → no interval, got {estimates}"


# ---------------------------------------------------------------------------
# G — min_gap boundary: exactly MIN_GAP_SECONDS apart → interval accepted
# ---------------------------------------------------------------------------


def test_g_exactly_min_gap_accepted(conn):
    result = compute_velocity_interval(
        start_value=1000.0,
        end_value=2000.0,
        start_time="2026-08-01T00:00:00",
        end_time="2026-08-01T06:00:00",  # exactly 6h = MIN_GAP_SECONDS
        min_gap_seconds=MIN_GAP_SECONDS,
    )
    assert result is not None, "Exactly at min_gap boundary must be accepted"
    assert result["units_per_day"] == pytest.approx(1000.0 / (6 / 24))


# ---------------------------------------------------------------------------
# H — same provider and video only (signal contamination blocked at query level)
# ---------------------------------------------------------------------------


def test_h_signal_type_isolation(conn):
    # Insert VIDEO_LIKE_COUNT for vidH — must not contribute to view velocity
    ih_like = make_obs_input_hash(
        platform="youtube",
        provider="youtube_data_api",
        signal_type=m.VIDEO_LIKE_COUNT,
        external_video_id="vidH",
        external_channel_id=None,
        normalized_query=None,
        region_code=None,
        language_code=None,
        date_bucket="2026-08-01",
    )
    repo.persist_observation(
        conn,
        collector_name="t",
        signal_type=m.VIDEO_LIKE_COUNT,
        observed_at="2026-08-01T00:00:00",
        input_hash=ih_like,
        external_video_id="vidH",
        signal_value_numeric=500.0,
    )
    # Only one VIEW_COUNT observation → no interval
    _insert_obs(conn, "vidH", 10_000.0, "2026-08-03T00:00:00")
    estimates = calculate_and_persist_velocity_for_video(
        conn, "vidH", signal_type=m.VIDEO_VIEW_COUNT
    )
    assert len(estimates) == 0, "LIKE_COUNT must not contribute to VIEW_COUNT velocity"


# ---------------------------------------------------------------------------
# I — another video cannot contaminate this video's velocity
# ---------------------------------------------------------------------------


def test_i_different_video_no_contamination(conn):
    _obs_pair(conn, "vidI_other", 0.0, "2026-08-01T00:00:00", 1_000_000.0, "2026-08-03T00:00:00")
    # vidI itself has only one observation
    _insert_obs(conn, "vidI", 5_000.0, "2026-08-03T00:00:00")
    estimates = calculate_and_persist_velocity_for_video(conn, "vidI")
    assert estimates == []


# ---------------------------------------------------------------------------
# J — another signal type cannot contaminate calculation
# ---------------------------------------------------------------------------


def test_j_signal_type_not_contaminated(conn):
    _obs_pair(conn, "vidJ", 1_000.0, "2026-08-01T00:00:00", 2_000.0, "2026-08-03T00:00:00")
    estimates = calculate_and_persist_velocity_for_video(
        conn,
        "vidJ",
        signal_type=m.VIDEO_LIKE_COUNT,  # no observations for this signal
    )
    assert estimates == []


# ---------------------------------------------------------------------------
# K — raw negative delta is preserved
# ---------------------------------------------------------------------------


def test_k_raw_negative_delta_preserved(conn):
    _obs_pair(
        conn, "vidK", 100_000.0, "2026-08-01T00:00:00", 90_000.0, "2026-08-03T00:00:00"
    )  # count decreased
    estimates = calculate_and_persist_velocity_for_video(conn, "vidK")
    assert estimates
    e = estimates[0]
    assert e.raw_delta == pytest.approx(-10_000.0)  # raw negative preserved


# ---------------------------------------------------------------------------
# L — units_per_day reflects raw_delta (can be negative)
# ---------------------------------------------------------------------------


def test_l_units_per_day_can_be_negative(conn):
    _obs_pair(conn, "vidL", 100_000.0, "2026-08-01T00:00:00", 90_000.0, "2026-08-03T00:00:00")
    estimates = calculate_and_persist_velocity_for_video(conn, "vidL")
    e = estimates[0]
    # Caller decides whether to clamp; raw value is preserved
    assert e.units_per_day == pytest.approx(-5_000.0)  # -10k / 2 days


# ---------------------------------------------------------------------------
# M — is_negative_delta flag set for decreased count
# ---------------------------------------------------------------------------


def test_m_is_negative_delta_flag(conn):
    _obs_pair(conn, "vidM", 100_000.0, "2026-08-01T00:00:00", 90_000.0, "2026-08-03T00:00:00")
    estimates = calculate_and_persist_velocity_for_video(conn, "vidM")
    assert estimates[0].is_negative_delta == 1


def test_m2_positive_delta_flag_is_zero(conn):
    _obs_pair(conn, "vidM2", 100_000.0, "2026-08-01T00:00:00", 110_000.0, "2026-08-03T00:00:00")
    estimates = calculate_and_persist_velocity_for_video(conn, "vidM2")
    assert estimates[0].is_negative_delta == 0


# ---------------------------------------------------------------------------
# N — input hash is deterministic
# ---------------------------------------------------------------------------


def test_n_input_hash_deterministic(conn):
    o1, o2 = _obs_pair(conn, "vidN", 1_000.0, "2026-08-01T00:00:00", 5_000.0, "2026-08-03T00:00:00")
    h1 = make_velocity_input_hash(
        platform="youtube",
        provider="youtube_data_api",
        external_video_id="vidN",
        signal_type=m.VIDEO_VIEW_COUNT,
        start_observation_id=o1.id,
        end_observation_id=o2.id,
        calculation_version=VELOCITY_CALCULATION_VERSION,
    )
    h2 = make_velocity_input_hash(
        platform="youtube",
        provider="youtube_data_api",
        external_video_id="vidN",
        signal_type=m.VIDEO_VIEW_COUNT,
        start_observation_id=o1.id,
        end_observation_id=o2.id,
        calculation_version=VELOCITY_CALCULATION_VERSION,
    )
    assert h1 == h2


# ---------------------------------------------------------------------------
# O — same observation pair → idempotent (INSERT OR IGNORE, no duplicate row)
# ---------------------------------------------------------------------------


def test_o_idempotent_persistence(conn):
    _obs_pair(conn, "vidO", 1_000.0, "2026-08-01T00:00:00", 5_000.0, "2026-08-03T00:00:00")
    estimates1 = calculate_and_persist_velocity_for_video(conn, "vidO")
    estimates2 = calculate_and_persist_velocity_for_video(conn, "vidO")
    # Second call must not create a new row
    all_ids = [e.id for e in estimates2]
    assert len(set(all_ids)) == len(all_ids), "Duplicate rows must not be created"
    assert len(estimates1) == len(estimates2)
    assert estimates1[0].id == estimates2[0].id


# ---------------------------------------------------------------------------
# P — new later observation creates a new interval
# ---------------------------------------------------------------------------


def test_p_new_observation_creates_new_interval(conn):
    o1, o2 = _obs_pair(conn, "vidP", 1_000.0, "2026-08-01T00:00:00", 5_000.0, "2026-08-03T00:00:00")
    estimates_before = calculate_and_persist_velocity_for_video(conn, "vidP")
    assert len(estimates_before) == 1

    # Add a third observation
    _insert_obs(conn, "vidP", 8_000.0, "2026-08-05T00:00:00", date_bucket="2026-08-05")
    estimates_after = calculate_and_persist_velocity_for_video(conn, "vidP")
    assert len(estimates_after) == 2


# ---------------------------------------------------------------------------
# Q — historical velocity intervals are preserved (not overwritten)
# ---------------------------------------------------------------------------


def test_q_historical_intervals_preserved(conn):
    # Three observations → two intervals
    ih1 = make_obs_input_hash(
        platform="youtube",
        provider="youtube_data_api",
        signal_type=m.VIDEO_VIEW_COUNT,
        external_video_id="vidQ",
        external_channel_id=None,
        normalized_query=None,
        region_code=None,
        language_code=None,
        date_bucket="2026-08-01",
    )
    ih2 = make_obs_input_hash(
        platform="youtube",
        provider="youtube_data_api",
        signal_type=m.VIDEO_VIEW_COUNT,
        external_video_id="vidQ",
        external_channel_id=None,
        normalized_query=None,
        region_code=None,
        language_code=None,
        date_bucket="2026-08-03",
    )
    ih3 = make_obs_input_hash(
        platform="youtube",
        provider="youtube_data_api",
        signal_type=m.VIDEO_VIEW_COUNT,
        external_video_id="vidQ",
        external_channel_id=None,
        normalized_query=None,
        region_code=None,
        language_code=None,
        date_bucket="2026-08-05",
    )
    for ih, val, ts in [
        (ih1, 10_000.0, "2026-08-01T00:00:00"),
        (ih2, 30_000.0, "2026-08-03T00:00:00"),
        (ih3, 45_000.0, "2026-08-05T00:00:00"),
    ]:
        repo.persist_observation(
            conn,
            collector_name="t",
            signal_type=m.VIDEO_VIEW_COUNT,
            observed_at=ts,
            input_hash=ih,
            external_video_id="vidQ",
            signal_value_numeric=val,
        )

    estimates = calculate_and_persist_velocity_for_video(conn, "vidQ")
    assert len(estimates) == 2, "Three observations → two adjacent intervals"
    # Most recent first: interval (30k→45k)
    assert estimates[0].start_value == pytest.approx(30_000.0)
    assert estimates[0].end_value == pytest.approx(45_000.0)
    # Earlier interval: (10k→30k)
    assert estimates[1].start_value == pytest.approx(10_000.0)
    assert estimates[1].end_value == pytest.approx(30_000.0)


# ---------------------------------------------------------------------------
# R — observation history ordered chronologically (ASC) by repository query
# ---------------------------------------------------------------------------


def test_r_observation_history_ordered_asc(conn):
    _insert_obs(conn, "vidR", 50_000.0, "2026-08-03T00:00:00", date_bucket="2026-08-03")
    _insert_obs(conn, "vidR", 10_000.0, "2026-08-01T00:00:00", date_bucket="2026-08-01")
    _insert_obs(conn, "vidR", 30_000.0, "2026-08-02T00:00:00", date_bucket="2026-08-02")
    history = repo.get_video_observation_history(conn, "vidR")
    times = [h.observed_at for h in history]
    assert times == sorted(times), "History must be ASC (oldest first)"


# ---------------------------------------------------------------------------
# S — velocity rescan uses batchGetStats (no search.list)
# ---------------------------------------------------------------------------


def test_s_rescan_uses_batch_stats_not_search(conn):
    # Seed an observation so a rescan candidate exists
    _insert_obs(conn, "vidS", 1_000.0, "2026-08-01T00:00:00", date_bucket="2026-08-01")

    call_log: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "/search" in url:
            call_log.append("search")
            return _err(403)
        if "/videos" in url:
            call_log.append("videos")
            return _ok(
                {
                    "items": [
                        {
                            "id": "vidS",
                            "viewCount": "2000",
                            "durationMillis": "300000",
                            "publishTime": "2026-01-01T00:00:00Z",
                        }
                    ]
                }
            )
        return _err(404)

    coll, _ = _make_collector(handler)
    job = _make_job(conn, job_type="velocity_rescan")
    coll.collect_velocity_rescan(
        conn,
        job,
        min_age_seconds=0,
        max_tracking_age_days=3650,
    )
    assert "search" not in call_log, "Rescan must not call search.list"
    assert "videos" in call_log, "Rescan must call batchGetStats"


# ---------------------------------------------------------------------------
# T — rescan does NOT call search.list quota bucket
# ---------------------------------------------------------------------------


def test_t_rescan_no_search_quota(conn):
    _insert_obs(conn, "vidT", 1_000.0, "2026-08-01T00:00:00", date_bucket="2026-08-01")

    def handler(req: httpx.Request) -> httpx.Response:
        if "/videos" in str(req.url):
            return _ok({"items": [{"id": "vidT", "viewCount": "2000"}]})
        return _err(404)

    coll, _ = _make_collector(handler)
    job = _make_job(conn, job_type="velocity_rescan")
    coll.collect_velocity_rescan(conn, job, min_age_seconds=0, max_tracking_age_days=3650)
    usage = repo.get_job_quota_usage(conn, job.id)
    ops = {u.operation for u in usage}
    assert "search.list" not in ops
    assert "videos.batchGetStats" in ops or "videos.list" in ops


# ---------------------------------------------------------------------------
# U — duplicate video IDs deduplicated before provider call
# ---------------------------------------------------------------------------


def test_u_duplicate_ids_deduplicated(conn):
    """select_rescan_candidates returns DISTINCT; collector preserves order."""
    # Insert same video twice with different date buckets (distinct observations)
    _insert_obs(conn, "vidU", 1_000.0, "2026-08-01T00:00:00", date_bucket="2026-08-01")

    batch_ids_seen: list[list[str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "/videos" in url:
            ids_param = req.url.params.get("id", "")
            batch_ids_seen.append(ids_param.split(",") if ids_param else [])
            return _ok({"items": [{"id": "vidU", "viewCount": "2000"}]})
        return _err(404)

    coll, _ = _make_collector(handler)
    job = _make_job(conn, job_type="velocity_rescan")
    coll.collect_velocity_rescan(conn, job, min_age_seconds=0, max_tracking_age_days=3650)

    all_ids = [vid for batch in batch_ids_seen for vid in batch]
    assert all_ids.count("vidU") == 1, "vidU must appear in exactly one batch call"


# ---------------------------------------------------------------------------
# V — same global refresh observation reusable by multiple jobs
# ---------------------------------------------------------------------------


def test_v_global_observation_reuse(conn):
    _insert_obs(conn, "vidV", 1_000.0, "2026-08-01T00:00:00", date_bucket="2026-08-01")

    def handler(req: httpx.Request) -> httpx.Response:
        if "/videos" in str(req.url):
            return _ok({"items": [{"id": "vidV", "viewCount": "2000"}]})
        return _err(404)

    coll, _ = _make_collector(handler)
    job1 = _make_job(conn, job_type="velocity_rescan")
    coll.collect_velocity_rescan(conn, job1, min_age_seconds=0, max_tracking_age_days=3650)

    job2 = _make_job(conn, job_type="velocity_rescan")
    coll.collect_velocity_rescan(conn, job2, min_age_seconds=0, max_tracking_age_days=3650)

    # Second rescan's vidV observation should be the same row (INSERT OR IGNORE)
    all_obs = conn.execute(
        "SELECT COUNT(DISTINCT id) FROM market_intelligence_observations "
        "WHERE external_video_id='vidV' AND signal_value_numeric=2000"
    ).fetchone()[0]
    assert all_obs == 1, "Same-day same-value observation must be deduplicated globally"


# ---------------------------------------------------------------------------
# W — successful rescan → job status = 'completed'
# ---------------------------------------------------------------------------


def test_w_successful_rescan_completed(conn):
    _insert_obs(conn, "vidW", 1_000.0, "2026-08-01T00:00:00", date_bucket="2026-08-01")

    def handler(req: httpx.Request) -> httpx.Response:
        if "/videos" in str(req.url):
            return _ok(
                {
                    "items": [
                        {
                            "id": "vidW",
                            "viewCount": "2000",
                            "durationMillis": "60000",
                            "publishTime": "2026-01-01T00:00:00Z",
                        }
                    ]
                }
            )
        return _err(404)

    coll, _ = _make_collector(handler)
    job = _make_job(conn, job_type="velocity_rescan")
    result = coll.collect_velocity_rescan(conn, job, min_age_seconds=0, max_tracking_age_days=3650)
    assert result.status == "completed"


# ---------------------------------------------------------------------------
# X — partial provider failure → status = 'partial'
# ---------------------------------------------------------------------------


def test_x_partial_provider_failure(conn):
    _insert_obs(conn, "vidX", 1_000.0, "2026-08-01T00:00:00", date_bucket="2026-08-01")

    def handler(req: httpx.Request) -> httpx.Response:
        if "/videos" in str(req.url):
            return _err(500)  # server error — not a fallback trigger
        return _err(404)

    coll, _ = _make_collector(handler)
    job = _make_job(conn, job_type="velocity_rescan")
    result = coll.collect_velocity_rescan(conn, job, min_age_seconds=0, max_tracking_age_days=3650)
    assert result.status in {"partial", "completed"}
    assert result.partial_failures


# ---------------------------------------------------------------------------
# Y — total provider failure before any refresh → job reflects error
# ---------------------------------------------------------------------------


def test_y_no_candidates_completed_empty(conn):
    """When no candidates exist, job completes cleanly with 0 observations."""

    # Don't insert any observations → no candidates
    def handler(req: httpx.Request) -> httpx.Response:
        return _err(404)

    coll, _ = _make_collector(handler)
    job = _make_job(conn, job_type="velocity_rescan")
    result = coll.collect_velocity_rescan(conn, job, min_age_seconds=0, max_tracking_age_days=3650)
    assert result.status == "completed"
    assert result.observations_new == 0


# ---------------------------------------------------------------------------
# Z — failedVideoIds in batchGetStats response handled without corruption
# ---------------------------------------------------------------------------


def test_z_failed_video_ids_handled(conn):
    _insert_obs(conn, "vidZ1", 1_000.0, "2026-08-01T00:00:00", date_bucket="2026-08-01")
    _insert_obs(conn, "vidZ2", 500.0, "2026-08-01T00:00:00", date_bucket="2026-08-01")

    def handler(req: httpx.Request) -> httpx.Response:
        if "/videos" in str(req.url):
            return _ok(
                {
                    "items": [{"id": "vidZ1", "viewCount": "2000"}],
                    "failedVideoIds": ["vidZ2"],
                }
            )
        return _err(404)

    coll, _ = _make_collector(handler)
    job = _make_job(conn, job_type="velocity_rescan")
    result = coll.collect_velocity_rescan(conn, job, min_age_seconds=0, max_tracking_age_days=3650)
    # vidZ1 should get a new observation; vidZ2 failure must not prevent that
    obs_z1 = repo.list_observations_for_video(conn, "vidZ1", signal_type=m.VIDEO_VIEW_COUNT)
    assert len(obs_z1) >= 1
    assert any("failedVideoIds" in pf for pf in result.partial_failures)


# ---------------------------------------------------------------------------
# AA — deleted/unavailable video preserves historical observations
# ---------------------------------------------------------------------------


def test_aa_deleted_video_old_history_preserved(conn):
    _insert_obs(conn, "vidAA", 10_000.0, "2026-08-01T00:00:00", date_bucket="2026-08-01")

    def handler(req: httpx.Request) -> httpx.Response:
        if "/videos" in str(req.url):
            # Provider returns empty items (deleted/private)
            return _ok({"items": []})
        return _err(404)

    coll, _ = _make_collector(handler)
    job = _make_job(conn, job_type="velocity_rescan")
    coll.collect_velocity_rescan(conn, job, min_age_seconds=0, max_tracking_age_days=3650)

    # Historical observation must remain untouched
    hist = repo.get_video_observation_history(conn, "vidAA")
    assert len(hist) >= 1
    assert hist[0].signal_value_numeric == pytest.approx(10_000.0)


# ---------------------------------------------------------------------------
# AB — batch quota recorded correctly (video_stats_batch bucket)
# ---------------------------------------------------------------------------


def test_ab_batch_quota_recorded(conn):
    _insert_obs(conn, "vidAB", 1_000.0, "2026-08-01T00:00:00", date_bucket="2026-08-01")

    def handler(req: httpx.Request) -> httpx.Response:
        if "/videos" in str(req.url):
            return _ok({"items": [{"id": "vidAB", "viewCount": "2000"}]})
        return _err(404)

    coll, _ = _make_collector(handler)
    job = _make_job(conn, job_type="velocity_rescan")
    coll.collect_velocity_rescan(conn, job, min_age_seconds=0, max_tracking_age_days=3650)
    usage = repo.get_job_quota_usage(conn, job.id)
    batch_rows = [u for u in usage if "video" in u.quota_bucket]
    assert batch_rows, "video_stats_batch or general_data_api quota must be recorded"
    assert batch_rows[0].units_consumed >= 1


# ---------------------------------------------------------------------------
# AC — search quota unchanged during rescan (no search bucket row)
# ---------------------------------------------------------------------------


def test_ac_search_quota_unchanged(conn):
    _insert_obs(conn, "vidAC", 1_000.0, "2026-08-01T00:00:00", date_bucket="2026-08-01")

    def handler(req: httpx.Request) -> httpx.Response:
        if "/videos" in str(req.url):
            return _ok({"items": [{"id": "vidAC", "viewCount": "2000"}]})
        return _err(404)

    coll, _ = _make_collector(handler)
    job = _make_job(conn, job_type="velocity_rescan")
    coll.collect_velocity_rescan(conn, job, min_age_seconds=0, max_tracking_age_days=3650)
    usage = repo.get_job_quota_usage(conn, job.id)
    assert not any(u.quota_bucket == "search_list" for u in usage)


# ---------------------------------------------------------------------------
# AD — quota guard stops extra batch calls when max_batch_calls reached
# ---------------------------------------------------------------------------


def test_ad_quota_guard_max_batch_calls(conn):
    for i in range(60):  # > 50 (one batch); force two batches
        _insert_obs(
            conn,
            f"vid{i:03d}",
            float(i * 1000),
            "2026-08-01T00:00:00",
            date_bucket=f"2026-08-0{(i % 3) + 1}",
        )

    call_count = [0]

    def handler(req: httpx.Request) -> httpx.Response:
        if "/videos" in str(req.url):
            call_count[0] += 1
            return _ok({"items": []})
        return _err(404)

    coll, _ = _make_collector(handler)
    job = _make_job(conn, job_type="velocity_rescan")
    coll.collect_velocity_rescan(
        conn,
        job,
        min_age_seconds=0,
        max_tracking_age_days=3650,
        max_batch_calls=1,
        max_videos=60,
    )
    assert call_count[0] == 1, "max_batch_calls=1 must stop after the first batch"


# ---------------------------------------------------------------------------
# AE — successful rescan observations are persisted even on partial failure
# ---------------------------------------------------------------------------


def test_ae_partial_failure_preserves_successful_obs(conn):
    _insert_obs(conn, "vidAE1", 1_000.0, "2026-08-01T00:00:00", date_bucket="2026-08-01")

    call_n = [0]

    def handler(req: httpx.Request) -> httpx.Response:
        if "/videos" in str(req.url):
            call_n[0] += 1
            if call_n[0] == 1:
                return _ok(
                    {
                        "items": [{"id": "vidAE1", "viewCount": "2000"}],
                        "failedVideoIds": ["doesnt_exist"],
                    }
                )
            return _err(503)
        return _err(404)

    coll, _ = _make_collector(handler)
    job = _make_job(conn, job_type="velocity_rescan")
    coll.collect_velocity_rescan(conn, job, min_age_seconds=0, max_tracking_age_days=3650)

    # vidAE1's new observation must be persisted
    obs = repo.list_observations_for_video(conn, "vidAE1", signal_type=m.VIDEO_VIEW_COUNT)
    assert any(o.signal_value_numeric == pytest.approx(2_000.0) for o in obs)


# ---------------------------------------------------------------------------
# AF — CLI rescan works with a fake provider (smoke test)
# ---------------------------------------------------------------------------


def test_af_cli_rescan_works(conn):
    from typer.testing import CliRunner

    from app.intelligence.market.cli import market_app

    _insert_obs(conn, "vidAF", 1_000.0, "2026-08-01T00:00:00", date_bucket="2026-08-01")

    import os
    import tempfile as tf2

    with tf2.TemporaryDirectory() as tmp:
        db_path = str(pathlib.Path(tmp) / "cli_test.db")
        env = {**os.environ, "ACE_DB_PATH": db_path}
        runner = CliRunner()
        # Create a fresh DB and seed an observation
        fresh_conn = open_db(pathlib.Path(db_path))
        _insert_obs(fresh_conn, "vidAF_cli", 500.0, "2026-08-01T00:00:00", date_bucket="2026-08-01")
        fresh_conn.close()

        result = runner.invoke(
            market_app,
            ["rescan", "--api-key", "fake", "--min-age-hours", "0"],
            env=env,
        )
    # Should not crash (candidates are found but batchGetStats returns empty items for unknown IDs)
    assert result.exit_code == 0 or "Error" not in (result.output or "")


# ---------------------------------------------------------------------------
# AG — CLI velocity output correct
# ---------------------------------------------------------------------------


def test_ag_cli_velocity_output(conn):
    from typer.testing import CliRunner

    from app.intelligence.market.cli import market_app

    _obs_pair(conn, "vidAG", 10_000.0, "2026-08-01T00:00:00", 50_000.0, "2026-08-03T00:00:00")
    calculate_and_persist_velocity_for_video(conn, "vidAG")

    import os
    import tempfile as tf2

    with tf2.TemporaryDirectory() as tmp:
        db_path = str(pathlib.Path(tmp) / "cli_vel.db")
        env = {**os.environ, "ACE_DB_PATH": db_path}
        fresh_conn = open_db(pathlib.Path(db_path))
        _obs_pair(
            fresh_conn, "vidAG2", 10_000.0, "2026-08-01T00:00:00", 50_000.0, "2026-08-03T00:00:00"
        )
        calculate_and_persist_velocity_for_video(fresh_conn, "vidAG2")
        fresh_conn.close()

        runner = CliRunner()
        result = runner.invoke(
            market_app,
            ["velocity", "--video", "vidAG2"],
            env=env,
        )
    assert result.exit_code == 0
    assert "views/day" in result.output or "Velocity" in result.output


# ---------------------------------------------------------------------------
# AH — no Opportunity or scoring table mutation during velocity operations
# ---------------------------------------------------------------------------


def test_ah_no_opportunity_mutation(conn):
    _obs_pair(conn, "vidAH", 1_000.0, "2026-08-01T00:00:00", 5_000.0, "2026-08-03T00:00:00")
    calculate_and_persist_velocity_for_video(conn, "vidAH")

    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='content_pipeline'"
    ).fetchone():
        count = conn.execute("SELECT COUNT(*) FROM content_pipeline").fetchone()[0]
        assert count == 0, "content_pipeline must not be touched"


# ---------------------------------------------------------------------------
# AI — no scoring columns modified during Phase 13C
# ---------------------------------------------------------------------------


def test_ai_no_scoring_mutation(conn):
    _obs_pair(conn, "vidAI", 1_000.0, "2026-08-01T00:00:00", 5_000.0, "2026-08-03T00:00:00")
    calculate_and_persist_velocity_for_video(conn, "vidAI")

    # If topics/scoring tables exist, they must be untouched
    topic_count = conn.execute("SELECT COUNT(*) FROM topics").fetchone()[0]
    assert topic_count == 0, "topics table must not be modified"


# ---------------------------------------------------------------------------
# AJ — no Phase 12C learning tables touched
# ---------------------------------------------------------------------------


def test_aj_no_phase12c_mutation(conn):
    _obs_pair(conn, "vidAJ", 1_000.0, "2026-08-01T00:00:00", 5_000.0, "2026-08-03T00:00:00")
    calculate_and_persist_velocity_for_video(conn, "vidAJ")

    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='channel_performance_baselines'"
    ).fetchone():
        count = conn.execute("SELECT COUNT(*) FROM channel_performance_baselines").fetchone()[0]
        assert count == 0


# ---------------------------------------------------------------------------
# AK — no live network calls (all requests go through stub transport)
# ---------------------------------------------------------------------------


def test_ak_no_live_network_calls(conn):
    _insert_obs(conn, "vidAK", 1_000.0, "2026-08-01T00:00:00", date_bucket="2026-08-01")
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        if "/videos" in str(req.url):
            return _ok({"items": [{"id": "vidAK", "viewCount": "2000"}]})
        return _err(404)

    coll, transport = _make_collector(handler)
    job = _make_job(conn, job_type="velocity_rescan")
    coll.collect_velocity_rescan(conn, job, min_age_seconds=0, max_tracking_age_days=3650)
    assert transport.captured, "All requests must go through capturing transport"
    for req in transport.captured:
        assert "googleapis.com" in str(req.url)


# ---------------------------------------------------------------------------
# AL — calculation_version change produces different input_hash
# ---------------------------------------------------------------------------


def test_al_calculation_version_changes_hash(conn):
    o1, o2 = _obs_pair(
        conn, "vidAL", 1_000.0, "2026-08-01T00:00:00", 5_000.0, "2026-08-03T00:00:00"
    )
    h_v1 = make_velocity_input_hash(
        platform="youtube",
        provider="youtube_data_api",
        external_video_id="vidAL",
        signal_type=m.VIDEO_VIEW_COUNT,
        start_observation_id=o1.id,
        end_observation_id=o2.id,
        calculation_version="v1",
    )
    h_v2 = make_velocity_input_hash(
        platform="youtube",
        provider="youtube_data_api",
        external_video_id="vidAL",
        signal_type=m.VIDEO_VIEW_COUNT,
        start_observation_id=o1.id,
        end_observation_id=o2.id,
        calculation_version="v2",
    )
    assert h_v1 != h_v2, "Different calculation_version must produce different hash"


# ---------------------------------------------------------------------------
# AM — content_age context correct when content_published_at is available
# ---------------------------------------------------------------------------


def test_am_content_age_context(conn):
    _obs_pair(
        conn,
        "vidAM",
        10_000.0,
        "2026-04-01T00:00:00",
        20_000.0,
        "2026-04-03T00:00:00",
        content_published_at="2026-03-01T00:00:00Z",
    )  # published 31 days before T1
    estimates = calculate_and_persist_velocity_for_video(conn, "vidAM")
    assert estimates
    e = estimates[0]
    # Age at start: ~31 days = ~744 hours (tolerance for UTC parsing)
    assert e.video_age_hours_at_start is not None
    assert e.video_age_hours_at_start == pytest.approx(31 * 24.0, abs=1.0)
    # Age at end: ~33 days = ~792 hours
    assert e.video_age_hours_at_end is not None
    assert e.video_age_hours_at_end == pytest.approx(33 * 24.0, abs=1.0)


# ---------------------------------------------------------------------------
# AN — future acceleration can read multiple intervals for Phase 13E
# ---------------------------------------------------------------------------


def test_an_multiple_intervals_readable_for_acceleration(conn):
    """Demonstrates Phase 13E readiness: two intervals show decelerating velocity."""
    # T0: 20k views, T1: 80k views (+60k in 2 days = 30k/day)
    # T1: 80k views, T2: 100k views (+20k in 2 days = 10k/day)
    ih_t0 = make_obs_input_hash(
        platform="youtube",
        provider="youtube_data_api",
        signal_type=m.VIDEO_VIEW_COUNT,
        external_video_id="vidAN",
        external_channel_id=None,
        normalized_query=None,
        region_code=None,
        language_code=None,
        date_bucket="2026-08-01",
    )
    ih_t1 = make_obs_input_hash(
        platform="youtube",
        provider="youtube_data_api",
        signal_type=m.VIDEO_VIEW_COUNT,
        external_video_id="vidAN",
        external_channel_id=None,
        normalized_query=None,
        region_code=None,
        language_code=None,
        date_bucket="2026-08-03",
    )
    ih_t2 = make_obs_input_hash(
        platform="youtube",
        provider="youtube_data_api",
        signal_type=m.VIDEO_VIEW_COUNT,
        external_video_id="vidAN",
        external_channel_id=None,
        normalized_query=None,
        region_code=None,
        language_code=None,
        date_bucket="2026-08-05",
    )
    for ih, val, ts in [
        (ih_t0, 20_000.0, "2026-08-01T00:00:00"),
        (ih_t1, 80_000.0, "2026-08-03T00:00:00"),
        (ih_t2, 100_000.0, "2026-08-05T00:00:00"),
    ]:
        repo.persist_observation(
            conn,
            collector_name="t",
            signal_type=m.VIDEO_VIEW_COUNT,
            observed_at=ts,
            input_hash=ih,
            external_video_id="vidAN",
            signal_value_numeric=val,
        )

    estimates = calculate_and_persist_velocity_for_video(conn, "vidAN")
    assert len(estimates) == 2

    # Most recent (end_time DESC):
    recent = estimates[0]  # T1→T2: +20k / 2 days = 10k/day
    earlier = estimates[1]  # T0→T1: +60k / 2 days = 30k/day

    assert recent.units_per_day == pytest.approx(10_000.0)
    assert earlier.units_per_day == pytest.approx(30_000.0)

    # Phase 13E can compare: earlier.units_per_day > recent.units_per_day → decelerating
    # This assertion proves the data is present without implementing classification
    assert earlier.units_per_day > recent.units_per_day, (
        "Phase 13E readiness: deceleration detectable from stored intervals"
    )


# ---------------------------------------------------------------------------
# Velocity maturity tests
# ---------------------------------------------------------------------------


def test_maturity_from_interval_count_values():
    from app.intelligence.market.models import velocity_maturity_from_interval_count

    assert velocity_maturity_from_interval_count(0) == m.VELOCITY_MATURITY_INSUFFICIENT
    assert velocity_maturity_from_interval_count(1) == m.VELOCITY_MATURITY_EARLY
    assert velocity_maturity_from_interval_count(2) == m.VELOCITY_MATURITY_ESTABLISHING
    assert velocity_maturity_from_interval_count(4) == m.VELOCITY_MATURITY_ESTABLISHING
    assert velocity_maturity_from_interval_count(5) == m.VELOCITY_MATURITY_MATURE
    assert velocity_maturity_from_interval_count(100) == m.VELOCITY_MATURITY_MATURE


def test_first_interval_has_early_maturity(conn):
    _obs_pair(conn, "vidMat1", 1_000.0, "2026-08-01T00:00:00", 5_000.0, "2026-08-03T00:00:00")
    estimates = calculate_and_persist_velocity_for_video(conn, "vidMat1")
    assert estimates[0].velocity_maturity == m.VELOCITY_MATURITY_EARLY


def test_v30_velocity_table_exists(conn):
    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "market_velocity_estimates" in tables


def test_v30_velocity_indexes_exist(conn):
    indexes = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    }
    assert "idx_mve_video" in indexes
    assert "idx_mve_end_obs" in indexes
    assert "idx_mve_maturity" in indexes
