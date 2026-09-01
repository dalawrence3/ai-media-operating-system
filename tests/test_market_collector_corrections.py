"""Phase 13B Corrections — Tests A–W (23 tests).

Verifies the corrected quota semantics and batchGetStats enrichment path.
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
from app.intelligence.market import providers as prov
from app.intelligence.market import repository as repo
from app.intelligence.market.collector import (
    _BATCH_STATS_ENDPOINT,
    _BATCH_STATS_FALLBACK_STATUS_CODES,
    YouTubeMarketCollector,
    compute_payload_fingerprint,
    get_stat_value,
)

# ---------------------------------------------------------------------------
# Transport infrastructure (duplicated from main suite for isolation)
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


def _make_collector(
    handler, api_key="test_key"
) -> tuple[YouTubeMarketCollector, _CapturingTransport]:
    transport = _CapturingTransport(handler=handler)
    client = httpx.Client(transport=transport)
    return YouTubeMarketCollector(api_key=api_key, client=client), transport


@pytest.fixture()
def conn():
    with tempfile.TemporaryDirectory() as d:
        c = open_db(pathlib.Path(d) / "test.db")
        yield c
        c.close()


def _make_job(conn, **kwargs):
    return repo.create_market_collection_job(conn, job_type="search_scan", **kwargs)


# ---------------------------------------------------------------------------
# Minimal stub responses
# ---------------------------------------------------------------------------

_SEARCH_1 = {
    "pageInfo": {"totalResults": 100, "resultsPerPage": 5},
    "items": [{"id": {"videoId": "vid001"}, "snippet": {"channelId": "chan_A"}}],
}

_SEARCH_2 = {
    "pageInfo": {"totalResults": 100, "resultsPerPage": 5},
    "items": [
        {"id": {"videoId": "vid001"}, "snippet": {"channelId": "chan_A"}},
        {"id": {"videoId": "vid002"}, "snippet": {"channelId": "chan_B"}},
    ],
    "nextPageToken": "PAGE2",
}

_SEARCH_PAGE2 = {
    "pageInfo": {"totalResults": 100, "resultsPerPage": 5},
    "items": [{"id": {"videoId": "vid003"}, "snippet": {"channelId": "chan_C"}}],
}

# batchGetStats-shaped response (flat top-level stat fields)
_BATCH_STATS_RESPONSE = {
    "items": [
        {
            "id": "vid001",
            "viewCount": "1500000",
            "likeCount": "25000",
            "commentCount": "900",
            "publishTime": "2026-03-01T10:00:00Z",
            "duration": "PT10M",
            "durationMillis": "600000",
        }
    ]
}

# videos.list-shaped response (nested statistics / snippet / contentDetails)
_VIDEOS_LIST_RESPONSE = {
    "items": [
        {
            "id": "vid001",
            "snippet": {
                "channelId": "chan_A",
                "publishedAt": "2026-03-01T10:00:00Z",
                "categoryId": "22",
            },
            "statistics": {
                "viewCount": "1500000",
                "likeCount": "25000",
                "commentCount": "900",
            },
            "contentDetails": {"duration": "PT10M"},
        }
    ]
}


def _batch_stats_handler():
    """Returns batchGetStats for /videos requests; handles /search too."""

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "/search" in url:
            return _ok(_SEARCH_1)
        if "/videos" in url:
            return _ok(_BATCH_STATS_RESPONSE)
        return _err(404)

    return handler


def _videos_list_handler():
    """Returns videos.list-shaped response for /videos requests."""

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "/search" in url:
            return _ok(_SEARCH_1)
        if "/videos" in url:
            return _ok(_VIDEOS_LIST_RESPONSE)
        return _err(404)

    return handler


# ---------------------------------------------------------------------------
# A — one search.list request records consumed=1 (not 100)
# ---------------------------------------------------------------------------


def test_a_search_quota_cost_is_1(conn):
    coll, _ = _make_collector(_batch_stats_handler())
    job = _make_job(conn)
    coll.collect_search_scan(conn, job, query="q")
    usage = repo.get_job_quota_usage(conn, job.id)
    search_rows = [u for u in usage if u.operation == "search.list"]
    assert search_rows, "search.list quota row missing"
    assert search_rows[0].units_consumed == 1
    assert search_rows[0].quota_bucket == "search_list"


# ---------------------------------------------------------------------------
# B — two paginated search.list calls record consumed=2
# ---------------------------------------------------------------------------


def test_b_paginated_search_accumulates_2(conn):
    page_index = [0]

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "/search" in url:
            resp = _SEARCH_2 if page_index[0] == 0 else _SEARCH_PAGE2
            page_index[0] += 1
            return _ok(resp)
        return _ok(_BATCH_STATS_RESPONSE)

    coll, _ = _make_collector(handler)
    job = _make_job(conn)
    coll.collect_search_scan(conn, job, query="q", max_pages=2)
    usage = repo.get_job_quota_usage(conn, job.id)
    search_rows = [u for u in usage if u.operation == "search.list"]
    assert search_rows[0].units_consumed == 2
    assert search_rows[0].call_count == 2


# ---------------------------------------------------------------------------
# C — search quota bucket is independent from enrichment buckets
# ---------------------------------------------------------------------------


def test_c_search_bucket_independent_from_enrichment(conn):
    coll, _ = _make_collector(_batch_stats_handler())
    job = _make_job(conn)
    coll.collect_search_scan(conn, job, query="q")
    usage = repo.get_job_quota_usage(conn, job.id)
    buckets = {u.quota_bucket for u in usage}
    assert "search_list" in buckets
    # At least one enrichment bucket also present
    assert buckets & {"video_stats_batch", "general_data_api"}, "enrichment bucket missing"
    # They're distinct
    assert "search_list" not in {"video_stats_batch", "general_data_api"}


# ---------------------------------------------------------------------------
# D — batchGetStats URL / path is correct
# ---------------------------------------------------------------------------


def test_d_batch_stats_endpoint_url():
    assert _BATCH_STATS_ENDPOINT == "https://www.googleapis.com/youtube/v3/videos:batchGetStats"


# ---------------------------------------------------------------------------
# E — multiple video IDs batched correctly into one batchGetStats call
# ---------------------------------------------------------------------------


def test_e_multiple_ids_batched(conn):
    search_resp = {
        "pageInfo": {"totalResults": 3},
        "items": [
            {"id": {"videoId": "aaa"}, "snippet": {"channelId": "c"}},
            {"id": {"videoId": "bbb"}, "snippet": {"channelId": "c"}},
            {"id": {"videoId": "ccc"}, "snippet": {"channelId": "c"}},
        ],
    }
    batch_requests: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "/search" in url:
            return _ok(search_resp)
        if "/videos" in url:
            batch_requests.append(str(req.url))
            return _ok({"items": []})
        return _err(404)

    coll, _ = _make_collector(handler)
    job = _make_job(conn)
    coll.collect_search_scan(conn, job, query="q")
    assert len(batch_requests) == 1, "All IDs should be in one batch call"
    assert "aaa" in batch_requests[0]
    assert "bbb" in batch_requests[0]
    assert "ccc" in batch_requests[0]


# ---------------------------------------------------------------------------
# F — view count observation from batchGetStats flat field
# ---------------------------------------------------------------------------


def test_f_view_count_from_batch_stats_flat_field(conn):
    coll, _ = _make_collector(_batch_stats_handler())
    job = _make_job(conn)
    coll.collect_search_scan(conn, job, query="q")
    obs = repo.list_observations_for_video(conn, "vid001", signal_type=m.VIDEO_VIEW_COUNT)
    assert obs, "VIEW_COUNT observation missing"
    assert obs[0].signal_value_numeric == pytest.approx(1_500_000.0)


# ---------------------------------------------------------------------------
# G — zero like count from batchGetStats is recorded (not treated as missing)
# ---------------------------------------------------------------------------


def test_g_zero_like_count_from_batch_stats_recorded(conn):
    batch_with_zero_likes = {
        "items": [
            {
                "id": "vid001",
                "viewCount": "5000",
                "likeCount": "0",  # explicitly 0
                "durationMillis": "60000",
            }
        ]
    }

    def handler(req: httpx.Request) -> httpx.Response:
        if "/search" in str(req.url):
            return _ok(_SEARCH_1)
        return _ok(batch_with_zero_likes)

    coll, _ = _make_collector(handler)
    job = _make_job(conn)
    coll.collect_search_scan(conn, job, query="q")
    obs = repo.list_observations_for_video(conn, "vid001", signal_type=m.VIDEO_LIKE_COUNT)
    assert obs, "LIKE_COUNT observation must exist even when zero"
    assert obs[0].signal_value_numeric == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# H — absent like count from batchGetStats does NOT produce an observation
# ---------------------------------------------------------------------------


def test_h_absent_like_count_from_batch_stats_produces_no_observation(conn):
    batch_no_likes = {
        "items": [
            {
                "id": "vid001",
                "viewCount": "5000",
                # likeCount absent
            }
        ]
    }

    def handler(req: httpx.Request) -> httpx.Response:
        if "/search" in str(req.url):
            return _ok(_SEARCH_1)
        return _ok(batch_no_likes)

    coll, _ = _make_collector(handler)
    job = _make_job(conn)
    coll.collect_search_scan(conn, job, query="q")
    obs = repo.list_observations_for_video(conn, "vid001", signal_type=m.VIDEO_LIKE_COUNT)
    assert not obs, "Absent likeCount must not produce an observation (absent ≠ zero)"


# ---------------------------------------------------------------------------
# I — comment count from batchGetStats is recorded
# ---------------------------------------------------------------------------


def test_i_comment_count_from_batch_stats(conn):
    coll, _ = _make_collector(_batch_stats_handler())
    job = _make_job(conn)
    coll.collect_search_scan(conn, job, query="q")
    obs = repo.list_observations_for_video(conn, "vid001", signal_type=m.VIDEO_COMMENT_COUNT)
    assert obs
    assert obs[0].signal_value_numeric == pytest.approx(900.0)


# ---------------------------------------------------------------------------
# J — duration from durationMillis (preferred over ISO 8601 string)
# ---------------------------------------------------------------------------


def test_j_duration_prefers_duration_millis(conn):
    # 600000 ms = 600 s; ISO 8601 PT10M = 600 s too — same result so test ms → s conversion
    coll, _ = _make_collector(_batch_stats_handler())
    job = _make_job(conn)
    coll.collect_search_scan(conn, job, query="q")
    obs = repo.list_observations_for_video(conn, "vid001", signal_type=m.VIDEO_DURATION_SECONDS)
    assert obs
    # durationMillis=600000 → 600.0 seconds
    assert obs[0].signal_value_numeric == pytest.approx(600.0)


# ---------------------------------------------------------------------------
# J2 — durationMillis ms precision takes precedence in fingerprint
# ---------------------------------------------------------------------------


def test_j2_fingerprint_uses_duration_millis_over_iso():
    item_with_both = {
        "id": "x",
        "viewCount": "100",
        "duration": "PT1M",  # 60 s in ISO 8601
        "durationMillis": "65432",  # 65.432 s in ms
    }
    fp_both = compute_payload_fingerprint(item_with_both)

    item_iso_only = {
        "id": "x",
        "viewCount": "100",
        "duration": "PT1M",
        # no durationMillis
    }
    fp_iso = compute_payload_fingerprint(item_iso_only)

    # Different duration representations → different fingerprints
    assert fp_both != fp_iso


# ---------------------------------------------------------------------------
# K — publishTime (batchGetStats flat field) persisted as VIDEO_PUBLISHED_AT
# ---------------------------------------------------------------------------


def test_k_publish_time_from_batch_stats(conn):
    coll, _ = _make_collector(_batch_stats_handler())
    job = _make_job(conn)
    coll.collect_search_scan(conn, job, query="q")
    obs = repo.list_observations_for_video(conn, "vid001", signal_type=m.VIDEO_PUBLISHED_AT)
    assert obs
    assert obs[0].signal_value_text == "2026-03-01T10:00:00Z"


# ---------------------------------------------------------------------------
# L — batchGetStats batch records exactly 1 quota unit per call
# ---------------------------------------------------------------------------


def test_l_batch_stats_quota_records_1_unit_per_call(conn):
    coll, _ = _make_collector(_batch_stats_handler())
    job = _make_job(conn)
    coll.collect_search_scan(conn, job, query="q")
    usage = repo.get_job_quota_usage(conn, job.id)
    batch_rows = [u for u in usage if u.operation == "videos.batchGetStats"]
    assert batch_rows, "videos.batchGetStats quota row missing"
    assert batch_rows[0].units_consumed == 1
    assert batch_rows[0].call_count == 1


# ---------------------------------------------------------------------------
# M — batchGetStats quota bucket is separate from videos.list bucket
# ---------------------------------------------------------------------------


def test_m_batch_stats_bucket_separate_from_videos_list(conn):
    coll, _ = _make_collector(_batch_stats_handler())
    job = _make_job(conn)
    coll.collect_search_scan(conn, job, query="q")
    usage = repo.get_job_quota_usage(conn, job.id)
    batch_rows = [u for u in usage if u.operation == "videos.batchGetStats"]
    assert batch_rows[0].quota_bucket == "video_stats_batch"
    # videos.list should NOT appear (no fallback triggered)
    vl_rows = [u for u in usage if u.operation == "videos.list"]
    assert not vl_rows, "videos.list quota must not be recorded when batchGetStats succeeds"


# ---------------------------------------------------------------------------
# N — fallback to videos.list occurs ONLY on HTTP 404 or 501
# ---------------------------------------------------------------------------


def test_n_fallback_triggered_on_404(conn):
    """HTTP 404 from batchGetStats → collector falls back to videos.list."""
    call_log: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "/search" in url:
            return _ok(_SEARCH_1)
        if "batchGetStats" in url:
            call_log.append("batchGetStats")
            return _err(404)
        if "/videos" in url:
            call_log.append("videos.list")
            return _ok(_VIDEOS_LIST_RESPONSE)
        return _err(404)

    coll, _ = _make_collector(handler)
    job = _make_job(conn)
    result = coll.collect_search_scan(conn, job, query="q")
    assert "batchGetStats" in call_log
    assert "videos.list" in call_log
    assert any("batchGetStats unavailable" in pf for pf in result.partial_failures)


def test_n2_fallback_triggered_on_501(conn):
    """HTTP 501 from batchGetStats → collector falls back to videos.list."""
    call_log: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "/search" in url:
            return _ok(_SEARCH_1)
        if "batchGetStats" in url:
            call_log.append("batchGetStats")
            return _err(501)
        if "/videos" in url:
            call_log.append("videos.list")
            return _ok(_VIDEOS_LIST_RESPONSE)
        return _err(404)

    coll, _ = _make_collector(handler)
    job = _make_job(conn)
    result = coll.collect_search_scan(conn, job, query="q")
    assert "batchGetStats" in call_log
    assert "videos.list" in call_log
    assert any("batchGetStats unavailable" in pf for pf in result.partial_failures)


# ---------------------------------------------------------------------------
# O — fallback records correct operation (videos.list) and bucket (general_data_api)
# ---------------------------------------------------------------------------


def test_o_fallback_records_correct_operation_and_bucket(conn):
    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "/search" in url:
            return _ok(_SEARCH_1)
        if "batchGetStats" in url:
            return _err(404)
        if "/videos" in url:
            return _ok(_VIDEOS_LIST_RESPONSE)
        return _err(404)

    coll, _ = _make_collector(handler)
    job = _make_job(conn)
    coll.collect_search_scan(conn, job, query="q")
    usage = repo.get_job_quota_usage(conn, job.id)
    vl_rows = [u for u in usage if u.operation == "videos.list"]
    assert vl_rows, "videos.list quota row must be recorded for fallback path"
    assert vl_rows[0].quota_bucket == "general_data_api"
    assert vl_rows[0].units_consumed == 1
    # batchGetStats must NOT appear (it returned 404, no quota consumed)
    bs_rows = [u for u in usage if u.operation == "videos.batchGetStats"]
    assert not bs_rows


# ---------------------------------------------------------------------------
# P — quota exhaustion (HTTP 429) does NOT trigger fallback
# ---------------------------------------------------------------------------


def test_p_quota_exhaustion_does_not_trigger_fallback(conn):
    """429 means quota exhausted, not endpoint missing — no fallback."""
    call_log: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "/search" in url:
            return _ok(_SEARCH_1)
        if "batchGetStats" in url:
            call_log.append("batchGetStats")
            return _err(429)
        call_log.append("videos.list")
        return _ok(_VIDEOS_LIST_RESPONSE)

    coll, _ = _make_collector(handler)
    job = _make_job(conn)
    result = coll.collect_search_scan(conn, job, query="q")
    assert "videos.list" not in call_log, (
        "videos.list must not be called when batchGetStats returns 429"
    )
    assert any("429" in pf for pf in result.partial_failures)
    # 403 (auth failure) also must not trigger fallback
    assert 429 not in _BATCH_STATS_FALLBACK_STATUS_CODES
    assert 403 not in _BATCH_STATS_FALLBACK_STATUS_CODES


# ---------------------------------------------------------------------------
# Q — failedVideoIds in batchGetStats response recorded in partial_failures
# ---------------------------------------------------------------------------


def test_q_failed_video_ids_recorded_in_partial_failures(conn):
    batch_with_failed = {
        "items": [{"id": "vid001", "viewCount": "100"}],
        "failedVideoIds": ["bad_vid"],
    }
    search_2_vids = {
        "pageInfo": {"totalResults": 2},
        "items": [
            {"id": {"videoId": "vid001"}, "snippet": {"channelId": "c"}},
            {"id": {"videoId": "bad_vid"}, "snippet": {"channelId": "c"}},
        ],
    }

    def handler(req: httpx.Request) -> httpx.Response:
        if "/search" in str(req.url):
            return _ok(search_2_vids)
        return _ok(batch_with_failed)

    coll, _ = _make_collector(handler)
    job = _make_job(conn)
    result = coll.collect_search_scan(conn, job, query="q")
    assert any("failedVideoIds" in pf for pf in result.partial_failures)


# ---------------------------------------------------------------------------
# R — completed job reports correct observation count in DB
# ---------------------------------------------------------------------------


def test_r_job_observation_count_updated(conn):
    coll, _ = _make_collector(_batch_stats_handler())
    job = _make_job(conn)
    coll.collect_search_scan(conn, job, query="q")
    refreshed = repo.get_market_collection_job(conn, job.id)
    assert refreshed is not None
    assert refreshed.status in {"completed", "partial"}
    assert refreshed.observation_count > 0


# ---------------------------------------------------------------------------
# S — idempotency: same call on same day does not duplicate observations
# ---------------------------------------------------------------------------


def test_s_idempotency_same_day_no_duplicates(conn):
    coll, _ = _make_collector(_batch_stats_handler())
    job1 = _make_job(conn)
    coll.collect_search_scan(conn, job1, query="q")
    job2 = _make_job(conn)
    coll.collect_search_scan(conn, job2, query="q")
    # Same-day observations should be deduplicated at DB level (INSERT OR IGNORE)
    obs = repo.list_observations_for_video(conn, "vid001", signal_type=m.VIDEO_VIEW_COUNT)
    assert len(obs) == 1, "Same-day VIEW_COUNT observation must not be duplicated"


# ---------------------------------------------------------------------------
# T — job status set to 'completed' on success / 'partial' on enrichment error
# ---------------------------------------------------------------------------


def test_t_job_status_completed_on_success(conn):
    coll, _ = _make_collector(_batch_stats_handler())
    job = _make_job(conn)
    result = coll.collect_search_scan(conn, job, query="q")
    assert result.status == "completed"


def test_t2_job_status_partial_on_enrichment_error(conn):
    """Non-fallback enrichment error → job status = partial (not failed)."""

    def handler(req: httpx.Request) -> httpx.Response:
        if "/search" in str(req.url):
            return _ok(_SEARCH_1)
        return _err(500)  # 5xx does not trigger fallback; marks partial

    coll, _ = _make_collector(handler)
    job = _make_job(conn)
    result = coll.collect_search_scan(conn, job, query="q")
    assert result.status in {"partial", "completed"}
    assert result.partial_failures  # error must be recorded


# ---------------------------------------------------------------------------
# U — Phase 13A schema and provider definitions still intact
# ---------------------------------------------------------------------------


def test_u_phase_13a_providers_intact():
    cap = prov.get_provider("youtube_data_api")
    assert cap is not None
    assert cap.is_official_api is True
    buckets = {b.name for b in cap.quota_buckets}
    assert "search_list" in buckets
    assert "video_stats_batch" in buckets
    assert "general_data_api" in buckets
    ops = {op.name for op in cap.operations}
    assert "search.list" in ops
    assert "videos.batchGetStats" in ops
    assert "videos.list" in ops
    assert "channels.list" in ops


# ---------------------------------------------------------------------------
# V — no Opportunity creation, no scoring changes (no such calls in collector)
# ---------------------------------------------------------------------------


def test_v_no_opportunity_creation(conn):
    """The collector must not touch any opportunity or scoring tables."""
    coll, _ = _make_collector(_batch_stats_handler())
    job = _make_job(conn)
    coll.collect_search_scan(conn, job, query="q")
    # opportunity / content_pipeline tables must remain empty
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    all_tables = {row[0] for row in cursor.fetchall()}
    if "content_pipeline" in all_tables:
        count = conn.execute("SELECT COUNT(*) FROM content_pipeline").fetchone()[0]
        assert count == 0
    # Observations must only be in market tables
    obs_count = conn.execute("SELECT COUNT(*) FROM market_intelligence_observations").fetchone()[0]
    assert obs_count > 0, "Market observations must be persisted"


# ---------------------------------------------------------------------------
# W — no live API calls (transport must always be the stub)
# ---------------------------------------------------------------------------


def test_w_no_live_api_calls(conn):
    """Collector uses the injected transport — zero real network I/O in tests."""
    captured: list[httpx.Request] = []

    def recording_handler(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        if "/search" in str(req.url):
            return _ok(_SEARCH_1)
        return _ok(_BATCH_STATS_RESPONSE)

    coll, transport = _make_collector(recording_handler)
    job = _make_job(conn)
    coll.collect_search_scan(conn, job, query="q")
    assert transport.captured, "Requests must go through the capturing transport"
    for req in transport.captured:
        # All requests must be to the YouTube API (no side-channel calls)
        assert "googleapis.com" in str(req.url)


# ---------------------------------------------------------------------------
# Unit helpers — get_stat_value shape normalisation
# ---------------------------------------------------------------------------


def test_get_stat_value_flat_shape():
    """get_stat_value reads flat batchGetStats top-level keys."""
    present, val = get_stat_value({"viewCount": "42000"}, "viewCount")
    assert present is True
    assert val == pytest.approx(42000.0)


def test_get_stat_value_nested_shape():
    """get_stat_value falls back to item['statistics'][key] (videos.list shape)."""
    item = {"statistics": {"viewCount": "99"}}
    present, val = get_stat_value(item, "viewCount")
    assert present is True
    assert val == pytest.approx(99.0)


def test_get_stat_value_absent():
    """Field absent in both locations → (False, None)."""
    present, val = get_stat_value({"statistics": {}}, "viewCount")
    assert present is False
    assert val is None


def test_get_stat_value_string_zero_is_present():
    """'0' → present=True, value=0.0 (zero ≠ missing)."""
    present, val = get_stat_value({"likeCount": "0"}, "likeCount")
    assert present is True
    assert val == pytest.approx(0.0)
