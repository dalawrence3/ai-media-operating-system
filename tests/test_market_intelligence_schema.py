"""Phase 13A tests — Market Intelligence schema, models, repository, providers.

Tests A–Z (26 tests).
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sqlite3
import tempfile

import pytest

from app.core.database import SCHEMA_VERSION, open_db
from app.intelligence.market import models as m
from app.intelligence.market import providers as prov
from app.intelligence.market import repository as repo

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def conn():
    with tempfile.TemporaryDirectory() as d:
        c = open_db(pathlib.Path(d) / "test.db")
        yield c
        c.close()


def _input_hash(
    platform: str = "youtube",
    provider: str = "youtube_data_api",
    signal_type: str = m.VIDEO_VIEW_COUNT,
    external_video_id: str | None = "vid_abc",
    external_channel_id: str | None = None,
    normalized_query: str | None = None,
    region_code: str | None = None,
    language_code: str | None = None,
    date_bucket: str = "2026-08-19",
) -> str:
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


def _make_obs(conn, **overrides):
    defaults = dict(
        collector_name="test_collector",
        signal_type=m.VIDEO_VIEW_COUNT,
        observed_at="2026-08-19T12:00:00",
        input_hash=_input_hash(),
        external_video_id="vid_abc",
        signal_value_numeric=1_000_000.0,
    )
    defaults.update(overrides)
    return repo.persist_observation(conn, **defaults)


# ---------------------------------------------------------------------------
# A — Schema version is 29
# ---------------------------------------------------------------------------


def test_a_schema_version_is_31():
    assert SCHEMA_VERSION == 51


# ---------------------------------------------------------------------------
# B — market_collection_jobs table exists in a fresh DB
# ---------------------------------------------------------------------------


def test_b_market_collection_jobs_table_exists(conn):
    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "market_collection_jobs" in tables


# ---------------------------------------------------------------------------
# C — market_intelligence_observations table exists
# ---------------------------------------------------------------------------


def test_c_market_intelligence_observations_table_exists(conn):
    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "market_intelligence_observations" in tables


# ---------------------------------------------------------------------------
# D — market_job_observations join table exists
# ---------------------------------------------------------------------------


def test_d_market_job_observations_join_table_exists(conn):
    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "market_job_observations" in tables


# ---------------------------------------------------------------------------
# E — market_job_quota_usage table exists
# ---------------------------------------------------------------------------


def test_e_market_job_quota_usage_table_exists(conn):
    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "market_job_quota_usage" in tables


# ---------------------------------------------------------------------------
# F — market_collection_jobs indexes present
# ---------------------------------------------------------------------------


def test_f_market_collection_jobs_indexes(conn):
    indexes = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    }
    assert "idx_mcj_channel" in indexes
    assert "idx_mcj_workspace" in indexes
    assert "idx_mcj_status" in indexes
    assert "idx_mcj_parent" in indexes


# ---------------------------------------------------------------------------
# G — market_intelligence_observations indexes present
# ---------------------------------------------------------------------------


def test_g_market_intelligence_observations_indexes(conn):
    indexes = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    }
    assert "idx_mio_platform_provider" in indexes
    assert "idx_mio_video" in indexes
    assert "idx_mio_channel_ext" in indexes
    assert "idx_mio_signal_type" in indexes
    assert "idx_mio_query" in indexes
    assert "idx_mio_observed_at" in indexes


# ---------------------------------------------------------------------------
# H — create_market_collection_job returns a job with correct fields
# ---------------------------------------------------------------------------


def test_h_create_market_collection_job(conn):
    job = repo.create_market_collection_job(
        conn,
        job_type="search_scan",
        seeds=["python tutorials", "data science"],
        channel_id=None,
    )
    assert job.id > 0
    assert job.job_type == "search_scan"
    assert job.status == "pending"
    assert job.origin_type == "manual"
    assert job.seeds() == ["python tutorials", "data science"]
    assert job.observation_count == 0
    assert job.exploration_depth == 0


# ---------------------------------------------------------------------------
# I — get_market_collection_job returns None for missing ID
# ---------------------------------------------------------------------------


def test_i_get_missing_job_returns_none(conn):
    assert repo.get_market_collection_job(conn, 9999) is None


# ---------------------------------------------------------------------------
# J — update_job_status transitions job to running and completed
# ---------------------------------------------------------------------------


def test_j_update_job_status(conn):
    job = repo.create_market_collection_job(conn, job_type="search_scan")
    repo.update_job_status(conn, job.id, status="running", started_at="2026-08-19T10:00:00")
    running = repo.get_market_collection_job(conn, job.id)
    assert running.status == "running"
    assert running.started_at == "2026-08-19T10:00:00"

    repo.update_job_status(
        conn,
        job.id,
        status="completed",
        observation_count=42,
        quota_consumed_total=300,
        completed_at="2026-08-19T10:05:00",
    )
    done = repo.get_market_collection_job(conn, job.id)
    assert done.status == "completed"
    assert done.observation_count == 42
    assert done.quota_consumed_total == 300


# ---------------------------------------------------------------------------
# K — persist_observation inserts a new row
# ---------------------------------------------------------------------------


def test_k_persist_observation_inserts(conn):
    obs = _make_obs(conn)
    assert obs.id > 0
    assert obs.signal_type == m.VIDEO_VIEW_COUNT
    assert obs.signal_value_numeric == 1_000_000.0
    assert obs.external_video_id == "vid_abc"


# ---------------------------------------------------------------------------
# L — same-day idempotency: persist_observation returns existing row on hash collision
# ---------------------------------------------------------------------------


def test_l_persist_observation_same_day_idempotent(conn):
    obs1 = _make_obs(conn)
    obs2 = _make_obs(conn, signal_value_numeric=999.0)  # different value, same hash
    assert obs1.id == obs2.id
    # The original value must be preserved (no update on collision)
    assert obs2.signal_value_numeric == 1_000_000.0


# ---------------------------------------------------------------------------
# M — different-day creates a second row (velocity tracking)
# ---------------------------------------------------------------------------


def test_m_different_day_creates_new_observation(conn):
    obs1 = _make_obs(
        conn, observed_at="2026-08-18T12:00:00", input_hash=_input_hash(date_bucket="2026-08-18")
    )
    obs2 = _make_obs(
        conn, observed_at="2026-08-19T12:00:00", input_hash=_input_hash(date_bucket="2026-08-19")
    )
    assert obs1.id != obs2.id


# ---------------------------------------------------------------------------
# N — link_job_observation and get_job_observations
# ---------------------------------------------------------------------------


def test_n_link_job_observation(conn):
    job = repo.create_market_collection_job(conn, job_type="search_scan")
    obs = _make_obs(conn)
    repo.link_job_observation(conn, job.id, obs.id)
    linked = repo.get_job_observations(conn, job.id)
    assert len(linked) == 1
    assert linked[0].id == obs.id


# ---------------------------------------------------------------------------
# O — link_job_observation is idempotent
# ---------------------------------------------------------------------------


def test_o_link_job_observation_idempotent(conn):
    job = repo.create_market_collection_job(conn, job_type="search_scan")
    obs = _make_obs(conn)
    repo.link_job_observation(conn, job.id, obs.id)
    repo.link_job_observation(conn, job.id, obs.id)  # second call must not raise
    linked = repo.get_job_observations(conn, job.id)
    assert len(linked) == 1


# ---------------------------------------------------------------------------
# P — record_quota_usage upsert accumulates correctly
# ---------------------------------------------------------------------------


def test_p_record_quota_usage_accumulates(conn):
    job = repo.create_market_collection_job(conn, job_type="search_scan")
    q1 = repo.record_quota_usage(
        conn,
        job_id=job.id,
        provider="youtube_data_api",
        operation="search.list",
        quota_bucket="units",
        units_consumed=100,
        observed_at="2026-08-19T10:00:00",
    )
    assert q1.units_consumed == 100
    assert q1.call_count == 1

    q2 = repo.record_quota_usage(
        conn,
        job_id=job.id,
        provider="youtube_data_api",
        operation="search.list",
        quota_bucket="units",
        units_consumed=100,
        observed_at="2026-08-19T10:01:00",
    )
    assert q2.units_consumed == 200
    assert q2.call_count == 2


# ---------------------------------------------------------------------------
# Q — get_job_quota_usage returns per-operation rows
# ---------------------------------------------------------------------------


def test_q_get_job_quota_usage(conn):
    job = repo.create_market_collection_job(conn, job_type="search_scan")
    repo.record_quota_usage(
        conn,
        job_id=job.id,
        provider="youtube_data_api",
        operation="search.list",
        quota_bucket="units",
        units_consumed=100,
        observed_at="2026-08-19T10:00:00",
    )
    repo.record_quota_usage(
        conn,
        job_id=job.id,
        provider="youtube_data_api",
        operation="videos.list",
        quota_bucket="units",
        units_consumed=1,
        observed_at="2026-08-19T10:00:01",
    )
    usage = repo.get_job_quota_usage(conn, job.id)
    assert len(usage) == 2
    ops = {u.operation for u in usage}
    assert ops == {"search.list", "videos.list"}


# ---------------------------------------------------------------------------
# R — list_observations_for_video filters correctly
# ---------------------------------------------------------------------------


def test_r_list_observations_for_video(conn):
    _make_obs(
        conn, external_video_id="vid_aaa", input_hash=_input_hash(external_video_id="vid_aaa")
    )
    _make_obs(
        conn,
        external_video_id="vid_bbb",
        signal_type=m.VIDEO_LIKE_COUNT,
        input_hash=_input_hash(external_video_id="vid_bbb", signal_type=m.VIDEO_LIKE_COUNT),
    )

    results = repo.list_observations_for_video(conn, "vid_aaa")
    assert all(o.external_video_id == "vid_aaa" for o in results)
    assert len(results) == 1


# ---------------------------------------------------------------------------
# S — list_observations_for_query filters correctly
# ---------------------------------------------------------------------------


def test_s_list_observations_for_query(conn):
    repo.persist_observation(
        conn,
        collector_name="c",
        signal_type=m.SEARCH_RESULT_RANK,
        observed_at="2026-08-19T00:00:00",
        input_hash=_input_hash(signal_type=m.SEARCH_RESULT_RANK, normalized_query="python"),
        normalized_query="python",
        signal_value_numeric=1.0,
    )
    repo.persist_observation(
        conn,
        collector_name="c",
        signal_type=m.SEARCH_RESULT_RANK,
        observed_at="2026-08-19T00:00:00",
        input_hash=_input_hash(signal_type=m.SEARCH_RESULT_RANK, normalized_query="java"),
        normalized_query="java",
        signal_value_numeric=2.0,
    )
    results = repo.list_observations_for_query(conn, "python")
    assert len(results) == 1
    assert results[0].normalized_query == "python"


# ---------------------------------------------------------------------------
# T — observation has no channel_id column (global, not channel-owned)
# ---------------------------------------------------------------------------


def test_t_observations_have_no_channel_id_column(conn):
    cols = {
        r[1] for r in conn.execute("PRAGMA table_info(market_intelligence_observations)").fetchall()
    }
    assert "channel_id" not in cols


# ---------------------------------------------------------------------------
# U — zero vs NULL distinction preserved
# ---------------------------------------------------------------------------


def test_u_zero_vs_null_preserved(conn):
    obs_zero = repo.persist_observation(
        conn,
        collector_name="c",
        signal_type=m.VIDEO_COMMENT_COUNT,
        observed_at="2026-08-19T00:00:00",
        input_hash=_input_hash(signal_type=m.VIDEO_COMMENT_COUNT),
        signal_value_numeric=0.0,  # explicitly zero
        external_video_id="vid_abc",
    )
    assert obs_zero.signal_value_numeric == 0.0

    obs_null = repo.persist_observation(
        conn,
        collector_name="c",
        signal_type=m.VIDEO_LIKE_COUNT,
        observed_at="2026-08-19T00:00:00",
        input_hash=_input_hash(signal_type=m.VIDEO_LIKE_COUNT),
        signal_value_numeric=None,  # not collected
        external_video_id="vid_abc",
    )
    assert obs_null.signal_value_numeric is None


# ---------------------------------------------------------------------------
# V — parent_job_id supports exploration depth lineage
# ---------------------------------------------------------------------------


def test_v_parent_job_id_lineage(conn):
    parent = repo.create_market_collection_job(conn, job_type="search_scan", origin_type="manual")
    child = repo.create_market_collection_job(
        conn,
        job_type="search_scan",
        origin_type="adjacent_topic",
        parent_job_id=parent.id,
        exploration_depth=1,
    )
    assert child.parent_job_id == parent.id
    assert child.exploration_depth == 1


# ---------------------------------------------------------------------------
# W — list_market_collection_jobs filters by status
# ---------------------------------------------------------------------------


def test_w_list_jobs_filter_by_status(conn):
    repo.create_market_collection_job(conn, job_type="search_scan")
    j2 = repo.create_market_collection_job(conn, job_type="velocity_rescan")
    repo.update_job_status(conn, j2.id, status="completed")

    pending = repo.list_market_collection_jobs(conn, status="pending")
    completed = repo.list_market_collection_jobs(conn, status="completed")
    assert all(j.status == "pending" for j in pending)
    assert all(j.status == "completed" for j in completed)


# ---------------------------------------------------------------------------
# X — provider capabilities: YouTube Data API known, cost correct
# ---------------------------------------------------------------------------


def test_x_provider_capabilities_youtube(conn):
    cap = prov.get_provider("youtube_data_api")
    assert cap is not None
    assert cap.is_official_api is True
    assert cap.platform == "youtube"
    assert (
        prov.get_operation_quota_cost("youtube_data_api", "search.list") == 1
    )  # 1 unit per request, 100 req/day limit
    assert prov.get_operation_quota_cost("youtube_data_api", "videos.batchGetStats") == 1
    assert prov.get_operation_quota_cost("youtube_data_api", "videos.list") == 1


# ---------------------------------------------------------------------------
# Y — signal type constants defined and KNOWN_SIGNAL_TYPES is a frozenset
# ---------------------------------------------------------------------------


def test_y_signal_type_constants():
    assert m.VIDEO_VIEW_COUNT == "video_view_count"
    assert m.CHANNEL_SUBSCRIBER_COUNT == "channel_subscriber_count"
    assert m.SEARCH_RESULT_RANK == "search_result_rank"
    assert isinstance(m.KNOWN_SIGNAL_TYPES, frozenset)
    assert m.VIDEO_VIEW_COUNT in m.KNOWN_SIGNAL_TYPES
    assert len(m.KNOWN_SIGNAL_TYPES) == 11  # 10 original + video_title (Phase 13D-C.1)
    assert m.VIDEO_TITLE in m.KNOWN_SIGNAL_TYPES


# ---------------------------------------------------------------------------
# Z — job_type and status CHECK constraints enforced by DB
# ---------------------------------------------------------------------------


def test_z_check_constraints_enforced(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO market_collection_jobs "
            "(job_type, provider, platform, seeds_json, status, observation_count, "
            "quota_consumed_total, created_at) "
            "VALUES ('invalid_type','youtube_data_api','youtube','[]','pending',"
            "0,0,'2026-08-19T00:00:00')"
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO market_collection_jobs "
            "(job_type, provider, platform, seeds_json, status, observation_count, "
            "quota_consumed_total, created_at) "
            "VALUES ('search_scan','youtube_data_api','youtube','[]','invalid_status',"
            "0,0,'2026-08-19T00:00:00')"
        )
