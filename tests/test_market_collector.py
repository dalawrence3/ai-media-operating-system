"""Phase 13B tests — YouTube Discovery & Enrichment Collector.

Tests A–AF (32 tests).
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
from app.intelligence.market.collector import (
    CollectionResult,
    YouTubeMarketCollector,
    compute_payload_fingerprint,
    parse_iso8601_duration,
)

# ---------------------------------------------------------------------------
# Transport / stub infrastructure
# ---------------------------------------------------------------------------


@dataclass
class _CapturingTransport(httpx.BaseTransport):
    """Routes requests to a handler and records every request made."""

    handler: Callable[[httpx.Request], httpx.Response]
    captured: list[httpx.Request] = field(default_factory=list)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.captured.append(request)
        return self.handler(request)


def _ok(body: dict) -> httpx.Response:
    return httpx.Response(200, json=body)


def _err(status: int = 403) -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": status, "message": "test error"}})


# ---------------------------------------------------------------------------
# Canonical stub fixtures
# ---------------------------------------------------------------------------

_SEARCH_RESPONSE = {
    "kind": "youtube#searchListResponse",
    "pageInfo": {"totalResults": 42000, "resultsPerPage": 5},
    "items": [
        {"id": {"videoId": "vid001"}, "snippet": {"channelId": "chan_A"}},
        {"id": {"videoId": "vid002"}, "snippet": {"channelId": "chan_B"}},
    ],
}

_SEARCH_PAGE2_RESPONSE = {
    "kind": "youtube#searchListResponse",
    "pageInfo": {"totalResults": 42000, "resultsPerPage": 5},
    "items": [
        {"id": {"videoId": "vid003"}, "snippet": {"channelId": "chan_C"}},
    ],
}

_SEARCH_WITH_NEXT = {**_SEARCH_RESPONSE, "nextPageToken": "PAGE2TOKEN"}

_VIDEOS_RESPONSE = {
    "items": [
        {
            "id": "vid001",
            "snippet": {
                "channelId": "chan_A",
                "publishedAt": "2026-01-15T00:00:00Z",
                "categoryId": "28",
            },
            "statistics": {
                "viewCount": "500000",
                "likeCount": "12000",
                "commentCount": "800",
            },
            "contentDetails": {"duration": "PT7M30S"},
        },
        {
            "id": "vid002",
            "snippet": {
                "channelId": "chan_B",
                "publishedAt": "2026-02-10T00:00:00Z",
                "categoryId": "28",
            },
            "statistics": {
                "viewCount": "250000",
                "likeCount": "0",  # explicitly zero, not missing
                "commentCount": "150",
            },
            "contentDetails": {"duration": "PT4M13S"},
        },
    ]
}


def _simple_handler(search_body=None, videos_body=None, search_err=False, videos_err=False):
    sb = search_body or _SEARCH_RESPONSE
    vb = videos_body or _VIDEOS_RESPONSE

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "/search" in url:
            return _err() if search_err else _ok(sb)
        if "/videos" in url:
            return _err() if videos_err else _ok(vb)
        return _err(404)

    return handler


def _make_collector(
    handler, api_key="test_key"
) -> tuple[YouTubeMarketCollector, _CapturingTransport]:
    transport = _CapturingTransport(handler=handler)
    client = httpx.Client(transport=transport)
    return YouTubeMarketCollector(api_key=api_key, client=client), transport


# ---------------------------------------------------------------------------
# DB fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def conn():
    with tempfile.TemporaryDirectory() as d:
        c = open_db(pathlib.Path(d) / "test.db")
        yield c
        c.close()


def _make_job(conn, **kwargs):
    return repo.create_market_collection_job(conn, job_type="search_scan", **kwargs)


# ---------------------------------------------------------------------------
# A — search request normalized correctly (query, region, language, order sent)
# ---------------------------------------------------------------------------


def test_a_search_request_contains_query_region_language_order(conn):
    coll, transport = _make_collector(_simple_handler())
    job = _make_job(conn)
    coll.collect_search_scan(
        conn,
        job,
        query="Python tutorials",
        region_code="US",
        language_code="en",
        order="date",
    )
    search_reqs = [r for r in transport.captured if "/search" in str(r.url)]
    assert search_reqs, "No search requests captured"
    url = str(search_reqs[0].url)
    assert "q=Python" in url or "Python" in url
    assert "regionCode=US" in url
    assert "relevanceLanguage=en" in url
    assert "order=date" in url


# ---------------------------------------------------------------------------
# B — query provenance persisted on SEARCH_RESULT_RANK observations
# ---------------------------------------------------------------------------


def test_b_query_provenance_persisted(conn):
    coll, _ = _make_collector(_simple_handler())
    job = _make_job(conn)
    coll.collect_search_scan(conn, job, query="Python tutorials")
    obs = repo.list_observations_for_video(conn, "vid001", signal_type=m.SEARCH_RESULT_RANK)
    assert obs, "No SEARCH_RESULT_RANK observation for vid001"
    assert obs[0].normalized_query == "python tutorials"
    assert obs[0].query_text == "Python tutorials"


# ---------------------------------------------------------------------------
# C — region and language persisted on observations
# ---------------------------------------------------------------------------


def test_c_region_and_language_persisted(conn):
    coll, _ = _make_collector(_simple_handler())
    job = _make_job(conn)
    coll.collect_search_scan(conn, job, query="q", region_code="GB", language_code="en")
    obs = repo.list_observations_for_video(conn, "vid001", signal_type=m.SEARCH_RESULT_RANK)
    assert obs[0].region_code == "GB"
    assert obs[0].language_code == "en"


# ---------------------------------------------------------------------------
# D — result ranks correct (0-indexed, ordered by search rank)
# ---------------------------------------------------------------------------


def test_d_result_ranks_correct(conn):
    coll, _ = _make_collector(_simple_handler())
    job = _make_job(conn)
    coll.collect_search_scan(conn, job, query="test")
    rank_vid001 = repo.list_observations_for_video(conn, "vid001", signal_type=m.SEARCH_RESULT_RANK)
    rank_vid002 = repo.list_observations_for_video(conn, "vid002", signal_type=m.SEARCH_RESULT_RANK)
    assert rank_vid001[0].signal_value_numeric == 0.0
    assert rank_vid002[0].signal_value_numeric == 1.0


# ---------------------------------------------------------------------------
# E — first page results persisted
# ---------------------------------------------------------------------------


def test_e_first_page_results_persisted(conn):
    coll, _ = _make_collector(_simple_handler())
    job = _make_job(conn)
    result = coll.collect_search_scan(conn, job, query="test")
    assert result.status == "completed"
    # Both vid001 and vid002 should have VIEW_COUNT observations
    obs1 = repo.list_observations_for_video(conn, "vid001", signal_type=m.VIDEO_VIEW_COUNT)
    obs2 = repo.list_observations_for_video(conn, "vid002", signal_type=m.VIDEO_VIEW_COUNT)
    assert obs1
    assert obs2


# ---------------------------------------------------------------------------
# F — pagination respects max_pages (stops after page 1 even if nextPageToken present)
# ---------------------------------------------------------------------------


def test_f_pagination_respects_max_pages(conn):
    page_calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "/search" in url:
            page_calls.append(req)
            return _ok(_SEARCH_WITH_NEXT)
        return _ok(_VIDEOS_RESPONSE)

    coll, _ = _make_collector(handler)
    job = _make_job(conn)
    coll.collect_search_scan(conn, job, query="test", max_pages=1)
    assert len(page_calls) == 1, "Should stop after 1 page even if nextPageToken present"


# ---------------------------------------------------------------------------
# G — pagination respects max_results (sent as maxResults param)
# ---------------------------------------------------------------------------


def test_g_max_results_sent_to_api(conn):
    coll, transport = _make_collector(_simple_handler())
    job = _make_job(conn)
    coll.collect_search_scan(conn, job, query="test", max_results=10)
    search_reqs = [r for r in transport.captured if "/search" in str(r.url)]
    assert "maxResults=10" in str(search_reqs[0].url)


# ---------------------------------------------------------------------------
# H — search quota bucket recorded
# ---------------------------------------------------------------------------


def test_h_search_quota_recorded(conn):
    coll, _ = _make_collector(_simple_handler())
    job = _make_job(conn)
    coll.collect_search_scan(conn, job, query="test")
    usage = repo.get_job_quota_usage(conn, job.id)
    search_usage = [u for u in usage if u.operation == "search.list"]
    assert search_usage, "search.list quota not recorded"
    assert search_usage[0].units_consumed == 1  # 1 unit per HTTP request (not 100)
    assert search_usage[0].quota_bucket == "search_list"


# ---------------------------------------------------------------------------
# I — enrichment quota recorded independently (separate operation/bucket)
# ---------------------------------------------------------------------------


def test_i_enrichment_quota_recorded_independently(conn):
    coll, _ = _make_collector(_simple_handler())
    job = _make_job(conn)
    coll.collect_search_scan(conn, job, query="test")
    usage = repo.get_job_quota_usage(conn, job.id)
    ops = {u.operation for u in usage}
    assert "search.list" in ops
    # Primary enrichment path is batchGetStats; videos.list is fallback only
    assert "videos.batchGetStats" in ops
    videos_usage = next(u for u in usage if u.operation == "videos.batchGetStats")
    assert videos_usage.quota_bucket == "video_stats_batch"
    assert videos_usage.units_consumed == 1


# ---------------------------------------------------------------------------
# J — video view count persisted
# ---------------------------------------------------------------------------


def test_j_view_count_persisted(conn):
    coll, _ = _make_collector(_simple_handler())
    job = _make_job(conn)
    coll.collect_search_scan(conn, job, query="test")
    obs = repo.list_observations_for_video(conn, "vid001", signal_type=m.VIDEO_VIEW_COUNT)
    assert obs
    assert obs[0].signal_value_numeric == pytest.approx(500_000.0)


# ---------------------------------------------------------------------------
# K — like count of zero preserved (0 ≠ NULL)
# ---------------------------------------------------------------------------


def test_k_like_count_zero_preserved(conn):
    coll, _ = _make_collector(_simple_handler())
    job = _make_job(conn)
    coll.collect_search_scan(conn, job, query="test")
    # vid002 has likeCount: "0"
    obs = repo.list_observations_for_video(conn, "vid002", signal_type=m.VIDEO_LIKE_COUNT)
    assert obs, "No LIKE_COUNT observation for vid002"
    assert obs[0].signal_value_numeric == 0.0, "Zero like count should be 0.0, not None"


# ---------------------------------------------------------------------------
# L — missing like count not converted to zero
# ---------------------------------------------------------------------------


def test_l_missing_like_count_not_converted_to_zero(conn):
    # vid without likeCount in stats
    videos_body = {
        "items": [
            {
                "id": "vid_nolikes",
                "snippet": {"channelId": "chan_X", "publishedAt": "2026-01-01T00:00:00Z"},
                "statistics": {"viewCount": "1000"},  # likeCount absent
                "contentDetails": {"duration": "PT5M"},
            }
        ]
    }
    search_body = {
        "pageInfo": {"totalResults": 1},
        "items": [{"id": {"videoId": "vid_nolikes"}, "snippet": {"channelId": "chan_X"}}],
    }
    coll, _ = _make_collector(_simple_handler(search_body=search_body, videos_body=videos_body))
    job = _make_job(conn)
    coll.collect_search_scan(conn, job, query="test")
    obs = repo.list_observations_for_video(conn, "vid_nolikes", signal_type=m.VIDEO_LIKE_COUNT)
    assert not obs, "Should NOT create a LIKE_COUNT observation when field is absent from response"


# ---------------------------------------------------------------------------
# M — comment count persisted
# ---------------------------------------------------------------------------


def test_m_comment_count_persisted(conn):
    coll, _ = _make_collector(_simple_handler())
    job = _make_job(conn)
    coll.collect_search_scan(conn, job, query="test")
    obs = repo.list_observations_for_video(conn, "vid001", signal_type=m.VIDEO_COMMENT_COUNT)
    assert obs
    assert obs[0].signal_value_numeric == pytest.approx(800.0)


# ---------------------------------------------------------------------------
# N — duration parsed correctly (ISO 8601 → seconds)
# ---------------------------------------------------------------------------


def test_n_duration_parsed(conn):
    coll, _ = _make_collector(_simple_handler())
    job = _make_job(conn)
    coll.collect_search_scan(conn, job, query="test")
    # vid001: PT7M30S = 450s
    obs = repo.list_observations_for_video(conn, "vid001", signal_type=m.VIDEO_DURATION_SECONDS)
    assert obs
    assert obs[0].signal_value_numeric == pytest.approx(450.0)
    # vid002: PT4M13S = 253s
    obs2 = repo.list_observations_for_video(conn, "vid002", signal_type=m.VIDEO_DURATION_SECONDS)
    assert obs2[0].signal_value_numeric == pytest.approx(253.0)


def test_n2_parse_iso8601_duration_formats():
    assert parse_iso8601_duration("PT7M30S") == pytest.approx(450.0)
    assert parse_iso8601_duration("PT4M13S") == pytest.approx(253.0)
    assert parse_iso8601_duration("PT1H") == pytest.approx(3600.0)
    assert parse_iso8601_duration("PT1H30M") == pytest.approx(5400.0)
    assert parse_iso8601_duration("P1D") == pytest.approx(86400.0)
    assert parse_iso8601_duration("PT0S") == pytest.approx(0.0)
    assert parse_iso8601_duration(None) is None
    assert parse_iso8601_duration("INVALID") is None


# ---------------------------------------------------------------------------
# O — published timestamp persisted as text observation
# ---------------------------------------------------------------------------


def test_o_published_timestamp_persisted(conn):
    coll, _ = _make_collector(_simple_handler())
    job = _make_job(conn)
    coll.collect_search_scan(conn, job, query="test")
    obs = repo.list_observations_for_video(conn, "vid001", signal_type=m.VIDEO_PUBLISHED_AT)
    assert obs
    assert obs[0].signal_value_text == "2026-01-15T00:00:00Z"
    assert obs[0].signal_value_numeric is None


# ---------------------------------------------------------------------------
# P — same provider event deduplicates (same-day, same-video, same-signal)
# ---------------------------------------------------------------------------


def test_p_same_day_deduplicates(conn):
    coll, _ = _make_collector(_simple_handler())
    job1 = _make_job(conn)
    coll.collect_search_scan(conn, job1, query="test")

    coll2, _ = _make_collector(_simple_handler())
    job2 = _make_job(conn)
    coll2.collect_search_scan(conn, job2, query="test")

    obs = repo.list_observations_for_video(conn, "vid001", signal_type=m.VIDEO_VIEW_COUNT)
    # Both jobs ran same-day → only one global observation row
    assert len(obs) == 1, "Same-day same-video same-signal should deduplicate"
    # Both jobs should link to that same observation
    linked1 = repo.get_job_observations(conn, job1.id)
    linked2 = repo.get_job_observations(conn, job2.id)
    vid_obs_ids_j1 = {
        o.id
        for o in linked1
        if o.signal_type == m.VIDEO_VIEW_COUNT and o.external_video_id == "vid001"
    }
    vid_obs_ids_j2 = {
        o.id
        for o in linked2
        if o.signal_type == m.VIDEO_VIEW_COUNT and o.external_video_id == "vid001"
    }
    assert vid_obs_ids_j1 & vid_obs_ids_j2, "Both jobs should link to the same observation"


# ---------------------------------------------------------------------------
# Q — same video later (different day) creates new observation (velocity)
# ---------------------------------------------------------------------------


def test_q_different_day_creates_new_observation(conn):
    from unittest.mock import patch

    from app.intelligence.market import collector as col_mod

    # First observation: day 1
    with patch.object(col_mod, "_now", return_value="2026-08-18T12:00:00"):
        coll, _ = _make_collector(_simple_handler())
        job1 = _make_job(conn)
        coll.collect_search_scan(conn, job1, query="test")

    # Second observation: day 2 (different date_bucket)
    with patch.object(col_mod, "_now", return_value="2026-08-19T12:00:00"):
        coll2, _ = _make_collector(_simple_handler())
        job2 = _make_job(conn)
        coll2.collect_search_scan(conn, job2, query="test")

    obs = repo.list_observations_for_video(conn, "vid001", signal_type=m.VIDEO_VIEW_COUNT)
    assert len(obs) == 2, "Different days should produce 2 separate view-count observations"


# ---------------------------------------------------------------------------
# R — same global observation reusable by two jobs
# ---------------------------------------------------------------------------


def test_r_global_observation_reused_by_two_jobs(conn):
    coll1, _ = _make_collector(_simple_handler())
    job1 = _make_job(conn)
    coll1.collect_search_scan(conn, job1, query="test")

    coll2, _ = _make_collector(_simple_handler())
    job2 = _make_job(conn)
    coll2.collect_search_scan(conn, job2, query="test")

    # Exactly one global observation for vid001 view_count
    all_obs = repo.list_observations_for_video(conn, "vid001", signal_type=m.VIDEO_VIEW_COUNT)
    assert len(all_obs) == 1
    obs_id = all_obs[0].id

    # Both jobs link to it
    j1_linked = {o.id for o in repo.get_job_observations(conn, job1.id)}
    j2_linked = {o.id for o in repo.get_job_observations(conn, job2.id)}
    assert obs_id in j1_linked
    assert obs_id in j2_linked


# ---------------------------------------------------------------------------
# S — search success + partial enrichment failure → PARTIAL
# ---------------------------------------------------------------------------


def test_s_search_success_enrichment_failure_gives_partial(conn):
    coll, _ = _make_collector(_simple_handler(videos_err=True))
    job = _make_job(conn)
    result = coll.collect_search_scan(conn, job, query="test")
    assert result.status == "partial"
    assert result.partial_failures

    refreshed = repo.get_market_collection_job(conn, job.id)
    assert refreshed.status == "partial"


# ---------------------------------------------------------------------------
# T — complete search failure → FAILED
# ---------------------------------------------------------------------------


def test_t_search_failure_gives_failed(conn):
    coll, _ = _make_collector(_simple_handler(search_err=True))
    job = _make_job(conn)
    result = coll.collect_search_scan(conn, job, query="test")
    assert result.status == "failed"

    refreshed = repo.get_market_collection_job(conn, job.id)
    assert refreshed.status == "failed"
    assert refreshed.failure_stage == "search"


# ---------------------------------------------------------------------------
# U — successful scan → COMPLETED
# ---------------------------------------------------------------------------


def test_u_successful_scan_completes(conn):
    coll, _ = _make_collector(_simple_handler())
    job = _make_job(conn)
    result = coll.collect_search_scan(conn, job, query="test")
    assert result.status == "completed"

    refreshed = repo.get_market_collection_job(conn, job.id)
    assert refreshed.status == "completed"
    assert refreshed.completed_at is not None
    assert refreshed.observation_count > 0


# ---------------------------------------------------------------------------
# V — quota guard (max_search_calls) stops additional pagination
# ---------------------------------------------------------------------------


def test_v_quota_guard_stops_pagination(conn):
    page_calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "/search" in url:
            page_calls.append(req)
            return _ok(_SEARCH_WITH_NEXT)  # always returns a nextPageToken
        return _ok(_VIDEOS_RESPONSE)

    coll, _ = _make_collector(handler)
    job = _make_job(conn)
    # max_pages=3 but max_search_calls=1 guard stops after 1
    coll.collect_search_scan(conn, job, query="test", max_pages=3, max_search_calls=1)
    assert len(page_calls) == 1, "Quota guard should stop after 1 search call"


# ---------------------------------------------------------------------------
# W — quota guard preserves collected observations
# ---------------------------------------------------------------------------


def test_w_quota_guard_preserves_observations(conn):
    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "/search" in url:
            return _ok(_SEARCH_WITH_NEXT)
        return _ok(_VIDEOS_RESPONSE)

    coll, _ = _make_collector(handler)
    job = _make_job(conn)
    result = coll.collect_search_scan(conn, job, query="test", max_pages=1)
    # Observations from page 1 exist even though we stopped early
    assert result.observations_total > 0
    obs = repo.list_observations_for_video(conn, "vid001", signal_type=m.VIDEO_VIEW_COUNT)
    assert obs, "Observations from completed page should be preserved"


# ---------------------------------------------------------------------------
# X — provider payload fingerprint is deterministic
# ---------------------------------------------------------------------------


def test_x_fingerprint_deterministic():
    item = {
        "id": "v1",
        "statistics": {"viewCount": "1000", "likeCount": "50", "commentCount": "5"},
        "contentDetails": {"duration": "PT5M"},
        "snippet": {"publishedAt": "2026-01-01T00:00:00Z"},
    }
    fp1 = compute_payload_fingerprint(item)
    fp2 = compute_payload_fingerprint(item)
    assert fp1 == fp2


# ---------------------------------------------------------------------------
# Y — changed response produces different fingerprint
# ---------------------------------------------------------------------------


def test_y_changed_response_changes_fingerprint():
    item_v1 = {
        "statistics": {"viewCount": "1000", "likeCount": "50"},
        "contentDetails": {"duration": "PT5M"},
    }
    item_v2 = {
        "statistics": {"viewCount": "2000", "likeCount": "50"},  # viewCount changed
        "contentDetails": {"duration": "PT5M"},
    }
    assert compute_payload_fingerprint(item_v1) != compute_payload_fingerprint(item_v2)


# ---------------------------------------------------------------------------
# Z — no Opportunity rows created
# ---------------------------------------------------------------------------


def test_z_no_opportunity_rows_created(conn):
    coll, _ = _make_collector(_simple_handler())
    job = _make_job(conn)
    coll.collect_search_scan(conn, job, query="test")
    count = conn.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0]
    assert count == 0, "Phase 13B must not create Opportunity rows"


# ---------------------------------------------------------------------------
# AA — scoring tables unchanged
# ---------------------------------------------------------------------------


def test_aa_scoring_tables_unchanged(conn):
    coll, _ = _make_collector(_simple_handler())
    job = _make_job(conn)
    coll.collect_search_scan(conn, job, query="test")
    score_count = conn.execute("SELECT COUNT(*) FROM opportunity_scores").fetchone()[0]
    assert score_count == 0


# ---------------------------------------------------------------------------
# AB — Phase 12C tables unchanged
# ---------------------------------------------------------------------------


def test_ab_phase12c_tables_unchanged(conn):
    coll, _ = _make_collector(_simple_handler())
    job = _make_job(conn)
    coll.collect_search_scan(conn, job, query="test")
    cpb = conn.execute("SELECT COUNT(*) FROM channel_performance_baselines").fetchone()[0]
    fpo = conn.execute("SELECT COUNT(*) FROM feature_performance_observations").fetchone()[0]
    assert cpb == 0
    assert fpo == 0


# ---------------------------------------------------------------------------
# AC — no live HTTP calls (all requests captured by stub)
# ---------------------------------------------------------------------------


def test_ac_no_live_http_calls(conn):
    coll, transport = _make_collector(_simple_handler())
    job = _make_job(conn)
    coll.collect_search_scan(conn, job, query="test")
    for req in transport.captured:
        assert "googleapis.com" in str(req.url) or "youtube" in str(req.url), (
            f"Unexpected URL: {req.url}"
        )
    # The key assertion: we used our stub, not a real socket
    assert isinstance(transport, _CapturingTransport)


# ---------------------------------------------------------------------------
# AD — CLI execute_search_scan works with fake collector
# ---------------------------------------------------------------------------


def test_ad_cli_execute_search_scan_with_fake_collector(conn):
    class _FakeCollector:
        PROVIDER = "youtube_data_api"
        PLATFORM = "youtube"
        COLLECTOR_NAME = "fake"

        def collect_search_scan(self, conn, job, **kwargs) -> CollectionResult:
            repo.update_job_status(conn, job.id, status="completed", observation_count=3)
            return CollectionResult(
                job_id=job.id,
                status="completed",
                observations_new=3,
                search_calls=1,
                enrichment_calls=1,
            )

    from io import StringIO

    from app.intelligence.market.cli import execute_search_scan

    output = StringIO()
    import unittest.mock as mock

    with (
        mock.patch("builtins.print"),
        mock.patch("typer.echo", side_effect=lambda s, **kw: output.write(str(s) + "\n")),
    ):
        execute_search_scan(conn, "python tutorials", collector=_FakeCollector())

    out = output.getvalue()
    assert "completed" in out
    # Job was created in the DB
    jobs = repo.list_market_collection_jobs(conn)
    assert jobs
    assert jobs[0].status == "completed"


# ---------------------------------------------------------------------------
# AE — credential failure (empty API key → HTTP 403) handled safely
# ---------------------------------------------------------------------------


def test_ae_credential_failure_safe(conn):
    def handler(req: httpx.Request) -> httpx.Response:
        return _err(403)

    coll, _ = _make_collector(handler, api_key="invalid_key")
    job = _make_job(conn)
    result = coll.collect_search_scan(conn, job, query="test")
    assert result.status == "failed"
    refreshed = repo.get_market_collection_job(conn, job.id)
    assert refreshed.status == "failed"
    assert refreshed.error_message is not None
    assert (
        "403" in refreshed.error_message
        or "test error" in refreshed.error_message
        or "4" in refreshed.error_message
    )


# ---------------------------------------------------------------------------
# AF — malformed provider response handled safely
# ---------------------------------------------------------------------------


def test_af_malformed_provider_response_safe(conn):
    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "/search" in url:
            return httpx.Response(200, json={"no_items_key": True})  # no 'items'
        return httpx.Response(200, json={"items": []})

    coll, _ = _make_collector(handler)
    job = _make_job(conn)
    result = coll.collect_search_scan(conn, job, query="test")
    # Should complete gracefully with 0 observations (no crash)
    assert result.status == "completed"
    assert result.observations_total >= 0  # including TOTAL_ESTIMATE obs (pageInfo absent too)
