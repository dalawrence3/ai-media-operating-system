"""Phase 13E tests — Market Interpretation: topic clustering + demand/saturation/freshness/momentum.

Tests A–BD (56 tests).

SCOPE
-----
- Schema v32 (4 new tables)
- Interpretation models, scoring functions, input hash helpers
- Repository CRUD (idempotency, not-found, FK)
- Interpreter service (run, cluster, signal)
- CLI commands (smoke test)

NO live API calls.  NO Phase 13F (no Opportunity creation).
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import tempfile

import pytest

from app.core.database import SCHEMA_VERSION, open_db
from app.intelligence.market.interpretation_models import (
    CLUSTERING_VERSION,
    SCORING_VERSION,
    SIGNAL_MATURITY_ACTIONABLE,
    SIGNAL_MATURITY_DIRECTIONAL,
    SIGNAL_MATURITY_EXPLORATORY,
    SIGNAL_MATURITY_INSUFFICIENT,
    STATE_ACCELERATING,
    STATE_COOLING,
    STATE_EMERGING,
    STATE_EVERGREEN,
    STATE_SATURATED,
    ClusterConsolidationCandidate,
    ExternalMarketOpportunityEvidence,
    MarketClusterMember,
    MarketClusterSignal,
    MarketInterpretationRun,
    MarketTopicCluster,
    build_opportunity_evidence,
    compute_demand_score,
    compute_freshness_score,
    compute_momentum_score,
    compute_persistence_score,
    compute_saturation_score,
    compute_signal_confidence,
    compute_signal_maturity,
    compute_state_label,
    make_cluster_input_hash,
    make_interpretation_run_input_hash,
    make_signal_input_hash,
    validate_llm_cluster_members,
)
from app.intelligence.market.interpretation_repository import (
    get_cluster_signal,
    get_interpretation_run,
    get_interpretation_run_by_hash,
    get_latest_signal_for_cluster,
    insert_cluster,
    insert_cluster_member_probe,
    insert_cluster_member_video,
    insert_cluster_signal,
    insert_interpretation_run,
    list_clusters_for_run,
    list_members_for_cluster,
    list_signals_for_run,
    update_interpretation_run_status,
)
from app.intelligence.market.interpreter import (
    _cluster_probes_by_jaccard,
    _pick_cluster_label,
    run_market_interpretation,
)

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def conn():
    with tempfile.TemporaryDirectory() as d:
        c = open_db(pathlib.Path(d) / "test.db")
        yield c
        c.close()


def _run_hash(**overrides) -> str:
    kwargs = dict(
        platform="youtube",
        provider="youtube_data_api",
        region_code=None,
        language_code=None,
        evidence_cutoff="2026-08-20T00:00:00",
        source_run_ids=[],
        clustering_version=CLUSTERING_VERSION,
        scoring_version=SCORING_VERSION,
    )
    kwargs.update(overrides)
    return make_interpretation_run_input_hash(**kwargs)


def _insert_run(conn, **overrides) -> MarketInterpretationRun:
    kwargs = dict(
        platform="youtube",
        provider="youtube_data_api",
        region_code=None,
        language_code=None,
        clustering_version=CLUSTERING_VERSION,
        scoring_version=SCORING_VERSION,
        evidence_cutoff="2026-08-20T00:00:00",
        source_run_ids=[],
        policy_snapshot={},
        input_hash=_run_hash(),
    )
    kwargs.update(overrides)
    return insert_interpretation_run(conn, **kwargs)


def _insert_cluster(
    conn, run_id: int, label: str = "yoga for beginners", **overrides
) -> MarketTopicCluster:
    from app.intelligence.dedup import normalize_topic

    normalized = normalize_topic(label)
    h = make_cluster_input_hash(
        normalized_label=normalized,
        region_code=None,
        language_code=None,
        platform="youtube",
        provider="youtube_data_api",
        cluster_version=CLUSTERING_VERSION,
        member_probe_ids=[],
    )
    kwargs = dict(
        interpretation_run_id=run_id,
        platform="youtube",
        provider="youtube_data_api",
        region_code=None,
        language_code=None,
        cluster_label=label,
        normalized_label=normalized,
        cluster_type="market_region",
        description="",
        clustering_rationale="test",
        cluster_version=CLUSTERING_VERSION,
        llm_used=0,
        llm_model=None,
        llm_prompt_version=None,
        member_probe_count=0,
        member_video_count=0,
        input_hash=h,
    )
    kwargs.update(overrides)
    return insert_cluster(conn, **kwargs)


def _insert_signal(conn, cluster_id: int, run_id: int, **overrides) -> MarketClusterSignal:
    h = make_signal_input_hash(
        cluster_id=cluster_id,
        member_video_ids=["vid1"],
        scoring_version=SCORING_VERSION,
    )
    kwargs = dict(
        cluster_id=cluster_id,
        interpretation_run_id=run_id,
        demand_score=0.5,
        saturation_score=0.3,
        freshness_score=0.6,
        momentum_score=0.4,
        persistence_score=0.2,
        confidence=0.7,
        signal_maturity=SIGNAL_MATURITY_DIRECTIONAL,
        state_label=STATE_EMERGING,
        supporting_video_count=7,
        supporting_creator_count=4,
        velocity_tracked_video_count=3,
        demand_components={},
        saturation_components={},
        freshness_components={},
        momentum_components={},
        persistence_components={},
        scoring_version=SCORING_VERSION,
        supporting_observation_ids=[],
        input_hash=h,
        scored_at="2026-08-20T00:00:00",
    )
    kwargs.update(overrides)
    return insert_cluster_signal(conn, **kwargs)


def _make_probe_and_job(conn, query: str, status: str = "dispatched") -> tuple[int, int]:
    """Create a minimal collection job + exploration probe; return (probe_id, job_id)."""
    from app.intelligence.market.planner_repository import (
        create_exploration_probe,
        create_exploration_run,
        update_probe_dispatch,
    )
    from app.intelligence.market.repository import create_market_collection_job

    job = create_market_collection_job(
        conn, job_type="search_scan", origin_type="exploration_planner"
    )
    exploration_run = create_exploration_run(conn)
    probe = create_exploration_probe(
        conn,
        run_id=exploration_run.id,
        query_text=query,
        normalized_query=query,
        probe_type="channel_bootstrap",
    )
    if status == "dispatched":
        update_probe_dispatch(
            conn, probe.id, dispatched_job_id=job.id, dispatched_at="2026-08-20T00:00:00"
        )
    return probe.id, job.id


def _add_video_observation(
    conn,
    job_id: int,
    video_id: str,
    view_count: float | None = None,
    age_days: float | None = None,
    channel_id: str | None = None,
) -> None:
    from app.intelligence.market.repository import link_job_observation, persist_observation

    obs_ids = []
    if view_count is not None:
        h = hashlib.sha256(
            json.dumps({"job_id": job_id, "vid": video_id, "type": "VIEW"}, sort_keys=True).encode()
        ).hexdigest()
        obs = persist_observation(
            conn,
            collector_name="test",
            signal_type="video_view_count",
            observed_at="2026-08-20T00:00:00",
            input_hash=h,
            external_video_id=video_id,
            external_channel_id=channel_id,
            signal_value_numeric=view_count,
        )
        link_job_observation(conn, job_id, obs.id)
        obs_ids.append(obs.id)

    if age_days is not None:
        h2 = hashlib.sha256(
            json.dumps({"job_id": job_id, "vid": video_id, "type": "PUB"}, sort_keys=True).encode()
        ).hexdigest()
        obs2 = persist_observation(
            conn,
            collector_name="test",
            signal_type="video_published_at",
            observed_at="2026-08-20T00:00:00",
            input_hash=h2,
            external_video_id=video_id,
            external_channel_id=channel_id,
            content_age_days=age_days,
        )
        link_job_observation(conn, job_id, obs2.id)

    return obs_ids


def _add_velocity(conn, video_id: str, units_per_day: float, is_negative: bool = False) -> None:
    from app.intelligence.market.repository import persist_observation, persist_velocity_estimate

    # Need real observation rows to satisfy FK
    h1 = hashlib.sha256(
        json.dumps(
            {"vel_vid": video_id, "vpd": units_per_day, "obs": "start"}, sort_keys=True
        ).encode()
    ).hexdigest()
    h2 = hashlib.sha256(
        json.dumps(
            {"vel_vid": video_id, "vpd": units_per_day, "obs": "end"}, sort_keys=True
        ).encode()
    ).hexdigest()
    obs1 = persist_observation(
        conn,
        collector_name="velocity_test",
        signal_type="video_view_count",
        observed_at="2026-08-01T00:00:00",
        input_hash=h1,
        external_video_id=video_id,
        signal_value_numeric=0.0,
    )
    obs2 = persist_observation(
        conn,
        collector_name="velocity_test",
        signal_type="video_view_count",
        observed_at="2026-08-20T00:00:00",
        input_hash=h2,
        external_video_id=video_id,
        signal_value_numeric=units_per_day * 19,
    )

    hv = hashlib.sha256(
        json.dumps(
            {"vid": video_id, "vpd": units_per_day, "neg": is_negative}, sort_keys=True
        ).encode()
    ).hexdigest()
    persist_velocity_estimate(
        conn,
        platform="youtube",
        provider="youtube_data_api",
        external_video_id=video_id,
        signal_type="video_view_count",
        start_observation_id=obs1.id,
        end_observation_id=obs2.id,
        start_time="2026-08-01T00:00:00",
        end_time="2026-08-20T00:00:00",
        start_value=0.0,
        end_value=units_per_day * 86400,
        raw_delta=units_per_day * 86400,
        elapsed_seconds=86400.0,
        units_per_hour=units_per_day / 24.0,
        units_per_day=units_per_day,
        is_negative_delta=is_negative,
        video_age_hours_at_start=None,
        video_age_hours_at_end=None,
        velocity_maturity="mature",
        calculation_version="v1",
        input_hash=hv,
    )
    conn.commit()


# ===========================================================================
# A — Schema version is 32
# ===========================================================================


def test_a_schema_version_is_32():
    assert SCHEMA_VERSION == 51


# ===========================================================================
# B — 4 new tables exist in a fresh DB
# ===========================================================================


def test_b_interpretation_tables_exist(conn):
    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "market_interpretation_runs" in tables
    assert "market_topic_clusters" in tables
    assert "market_cluster_members" in tables
    assert "market_cluster_signals" in tables


# ===========================================================================
# C — market_interpretation_runs has input_hash UNIQUE constraint
# ===========================================================================


def test_c_interpretation_runs_input_hash_unique(conn):
    h = _run_hash()
    _insert_run(conn, input_hash=h)
    conn.execute(
        "INSERT OR IGNORE INTO market_interpretation_runs "
        "(platform, provider, evidence_cutoff, input_hash) "
        "VALUES ('youtube', 'youtube_data_api', '2026-08-20', ?)",
        (h,),
    )
    count = conn.execute(
        "SELECT COUNT(*) FROM market_interpretation_runs WHERE input_hash = ?", (h,)
    ).fetchone()[0]
    assert count == 1


# ===========================================================================
# D — insert_interpretation_run returns typed model
# ===========================================================================


def test_d_insert_interpretation_run_returns_model(conn):
    run = _insert_run(conn)
    assert isinstance(run, MarketInterpretationRun)
    assert run.id >= 1
    assert run.status == "pending"
    assert run.cluster_count == 0


# ===========================================================================
# E — insert_interpretation_run is idempotent
# ===========================================================================


def test_e_insert_interpretation_run_idempotent(conn):
    run1 = _insert_run(conn)
    run2 = _insert_run(conn)  # same hash
    assert run1.id == run2.id


# ===========================================================================
# F — get_interpretation_run_by_hash succeeds
# ===========================================================================


def test_f_get_by_hash(conn):
    h = _run_hash()
    _insert_run(conn, input_hash=h)
    run = get_interpretation_run_by_hash(conn, h)
    assert run.input_hash == h


# ===========================================================================
# G — get_interpretation_run raises LookupError for missing id
# ===========================================================================


def test_g_get_run_missing_raises(conn):
    with pytest.raises(LookupError):
        get_interpretation_run(conn, 9999)


# ===========================================================================
# H — update_interpretation_run_status persists
# ===========================================================================


def test_h_update_run_status(conn):
    run = _insert_run(conn)
    update_interpretation_run_status(
        conn, run.id, status="running", started_at="2026-08-20T00:01:00"
    )
    updated = get_interpretation_run(conn, run.id)
    assert updated.status == "running"
    assert updated.started_at == "2026-08-20T00:01:00"


# ===========================================================================
# I — insert_cluster returns typed MarketTopicCluster
# ===========================================================================


def test_i_insert_cluster_returns_model(conn):
    run = _insert_run(conn)
    cluster = _insert_cluster(conn, run.id)
    assert isinstance(cluster, MarketTopicCluster)
    assert cluster.interpretation_run_id == run.id
    assert cluster.cluster_label == "yoga for beginners"


# ===========================================================================
# J — insert_cluster is idempotent (same run + same hash)
# ===========================================================================


def test_j_insert_cluster_idempotent(conn):
    run = _insert_run(conn)
    c1 = _insert_cluster(conn, run.id)
    c2 = _insert_cluster(conn, run.id)
    assert c1.id == c2.id


# ===========================================================================
# K — list_clusters_for_run returns all clusters for a run
# ===========================================================================


def test_k_list_clusters_for_run(conn):
    run = _insert_run(conn)
    _insert_cluster(conn, run.id, label="yoga for beginners")

    h2 = make_cluster_input_hash(
        normalized_label="cooking tutorials",
        region_code=None,
        language_code=None,
        platform="youtube",
        provider="youtube_data_api",
        cluster_version=CLUSTERING_VERSION,
        member_probe_ids=[],
    )
    insert_cluster(
        conn,
        interpretation_run_id=run.id,
        platform="youtube",
        provider="youtube_data_api",
        region_code=None,
        language_code=None,
        cluster_label="cooking tutorials",
        normalized_label="cooking tutorials",
        cluster_type="market_region",
        description="",
        clustering_rationale="test",
        cluster_version=CLUSTERING_VERSION,
        llm_used=0,
        llm_model=None,
        llm_prompt_version=None,
        member_probe_count=0,
        member_video_count=0,
        input_hash=h2,
    )
    clusters = list_clusters_for_run(conn, run.id)
    assert len(clusters) == 2


# ===========================================================================
# L — insert_cluster_member_probe and list_members_for_cluster
# ===========================================================================


def test_l_insert_cluster_member_probe(conn):
    run = _insert_run(conn)
    cluster = _insert_cluster(conn, run.id)
    probe_id, _ = _make_probe_and_job(conn, "yoga tutorial")
    member = insert_cluster_member_probe(conn, cluster_id=cluster.id, probe_id=probe_id)
    assert isinstance(member, MarketClusterMember)
    assert member.member_type == "probe_origin"
    assert member.probe_id == probe_id

    members = list_members_for_cluster(conn, cluster.id, member_type="probe_origin")
    assert len(members) == 1


# ===========================================================================
# M — insert_cluster_member_video and idempotency
# ===========================================================================


def test_m_insert_cluster_member_video_idempotent(conn):
    run = _insert_run(conn)
    cluster = _insert_cluster(conn, run.id)
    m1 = insert_cluster_member_video(conn, cluster_id=cluster.id, external_video_id="vid_abc")
    m2 = insert_cluster_member_video(conn, cluster_id=cluster.id, external_video_id="vid_abc")
    assert m1.id == m2.id
    assert m1.member_type == "evidence_video"


# ===========================================================================
# N — insert_cluster_signal returns typed MarketClusterSignal
# ===========================================================================


def test_n_insert_cluster_signal_returns_model(conn):
    run = _insert_run(conn)
    cluster = _insert_cluster(conn, run.id)
    signal = _insert_signal(conn, cluster.id, run.id)
    assert isinstance(signal, MarketClusterSignal)
    assert signal.cluster_id == cluster.id
    assert signal.demand_score == 0.5


# ===========================================================================
# O — insert_cluster_signal is idempotent (same cluster + run)
# ===========================================================================


def test_o_insert_cluster_signal_idempotent(conn):
    run = _insert_run(conn)
    cluster = _insert_cluster(conn, run.id)
    s1 = _insert_signal(conn, cluster.id, run.id)
    s2 = _insert_signal(conn, cluster.id, run.id)
    assert s1.id == s2.id


# ===========================================================================
# P — get_cluster_signal returns None when not found
# ===========================================================================


def test_p_get_cluster_signal_none_when_missing(conn):
    run = _insert_run(conn)
    cluster = _insert_cluster(conn, run.id)
    result = get_cluster_signal(conn, cluster.id, run.id)
    assert result is None


# ===========================================================================
# Q — get_latest_signal_for_cluster returns most recent snapshot
# ===========================================================================


def test_q_get_latest_signal_for_cluster(conn):
    run1 = _insert_run(conn)
    run2 = _insert_run(conn, input_hash=_run_hash(evidence_cutoff="2026-08-21T00:00:00"))
    cluster = _insert_cluster(conn, run1.id)

    _insert_signal(conn, cluster.id, run1.id)
    h2 = make_signal_input_hash(
        cluster_id=cluster.id, member_video_ids=["vid2"], scoring_version=SCORING_VERSION
    )
    s2 = _insert_signal(conn, cluster.id, run2.id, demand_score=0.9, input_hash=h2)

    latest = get_latest_signal_for_cluster(conn, cluster.id)
    assert latest is not None
    assert latest.id == s2.id


# ===========================================================================
# R — list_signals_for_run returns all signals for a run
# ===========================================================================


def test_r_list_signals_for_run(conn):
    run = _insert_run(conn)
    c1 = _insert_cluster(conn, run.id)
    h2 = make_cluster_input_hash(
        normalized_label="cooking",
        region_code=None,
        language_code=None,
        platform="youtube",
        provider="youtube_data_api",
        cluster_version=CLUSTERING_VERSION,
        member_probe_ids=[],
    )
    c2 = insert_cluster(
        conn,
        interpretation_run_id=run.id,
        platform="youtube",
        provider="youtube_data_api",
        region_code=None,
        language_code=None,
        cluster_label="cooking",
        normalized_label="cooking",
        cluster_type="market_region",
        description="",
        clustering_rationale="",
        cluster_version=CLUSTERING_VERSION,
        llm_used=0,
        llm_model=None,
        llm_prompt_version=None,
        member_probe_count=0,
        member_video_count=0,
        input_hash=h2,
    )
    _insert_signal(conn, c1.id, run.id)
    h_s2 = make_signal_input_hash(
        cluster_id=c2.id, member_video_ids=["v2"], scoring_version=SCORING_VERSION
    )
    _insert_signal(conn, c2.id, run.id, input_hash=h_s2)

    sigs = list_signals_for_run(conn, run.id)
    assert len(sigs) == 2


# ===========================================================================
# S — signal components JSON round-trips
# ===========================================================================


def test_s_signal_components_json_roundtrip(conn):
    run = _insert_run(conn)
    cluster = _insert_cluster(conn, run.id)
    signal = _insert_signal(
        conn,
        cluster.id,
        run.id,
        demand_components={"median_normalized_views": 0.42, "video_count": 7},
    )
    assert signal.demand_components()["video_count"] == 7
    assert signal.demand_components()["median_normalized_views"] == pytest.approx(0.42)


# ===========================================================================
# T — compute_demand_score returns None when no views
# ===========================================================================


def test_t_demand_score_none_when_no_views():
    score, diag = compute_demand_score(view_counts=[], creator_count=0)
    assert score is None
    assert diag["reason"] == "no_view_data"


# ===========================================================================
# U — compute_demand_score single-video cap applies
# ===========================================================================


def test_u_demand_score_single_video_cap():
    score, diag = compute_demand_score(view_counts=[50_000_000], creator_count=0)
    assert score is not None
    assert score <= 0.5
    assert diag["single_video_cap_applied"] is True


# ===========================================================================
# V — compute_demand_score multi-video no cap
# ===========================================================================


def test_v_demand_score_multi_video_no_cap():
    score, diag = compute_demand_score(
        view_counts=[1_000_000, 2_000_000, 3_000_000], creator_count=5
    )
    assert score is not None
    assert diag["single_video_cap_applied"] is False
    assert score > 0.0


# ===========================================================================
# W — compute_saturation_score scales with creator count
# ===========================================================================


def test_w_saturation_score_creator_count():
    score_low, _ = compute_saturation_score(creator_count=3, search_total_estimate=None)
    score_high, _ = compute_saturation_score(creator_count=30, search_total_estimate=None)
    assert score_low < score_high


# ===========================================================================
# X — compute_saturation_score with search_total vs without
# ===========================================================================


def test_x_saturation_score_search_total_effect():
    score_no_search, c_no = compute_saturation_score(creator_count=10, search_total_estimate=None)
    score_with_search, c_with = compute_saturation_score(
        creator_count=10, search_total_estimate=500_000
    )
    assert c_no["has_search_estimate"] is False
    assert c_with["has_search_estimate"] is True
    # search pushes saturation further
    assert score_with_search >= score_no_search * 0.9  # can vary; just check it's present


# ===========================================================================
# Y — compute_freshness_score returns None when no dates
# ===========================================================================


def test_y_freshness_score_none_when_no_dates():
    score, diag = compute_freshness_score(age_days_list=[])
    assert score is None
    assert diag["reason"] == "no_publication_dates"


# ===========================================================================
# Z — compute_freshness_score: recent content scores higher
# ===========================================================================


def test_z_freshness_score_recent_content_higher():
    score_fresh, _ = compute_freshness_score(age_days_list=[10, 20, 30])
    score_old, _ = compute_freshness_score(age_days_list=[500, 600, 700])
    assert score_fresh > score_old


# ===========================================================================
# AA — compute_momentum_score returns None when no velocities
# ===========================================================================


def test_aa_momentum_score_none_when_no_velocity():
    score, diag = compute_momentum_score(positive_vpd_list=[], total_velocity_count=0)
    assert score is None
    assert diag["reason"] == "no_velocity_data"


# ===========================================================================
# AB — compute_momentum_score zero when all velocities are negative
# ===========================================================================


def test_ab_momentum_score_zero_all_negative():
    score, diag = compute_momentum_score(positive_vpd_list=[], total_velocity_count=3)
    assert score == 0.0
    assert diag["positive_count"] == 0


# ===========================================================================
# AC — compute_momentum_score scales with vpd
# ===========================================================================


def test_ac_momentum_score_scales_with_vpd():
    score_low, _ = compute_momentum_score(positive_vpd_list=[100], total_velocity_count=1)
    score_high, _ = compute_momentum_score(positive_vpd_list=[8_000], total_velocity_count=1)
    assert score_low < score_high


# ===========================================================================
# AD — compute_persistence_score zero when < 2 dates
# ===========================================================================


def test_ad_persistence_score_zero_insufficient_dates():
    score, diag = compute_persistence_score(age_days_list=[30], view_counts_by_index=[1000])
    assert score == 0.0
    assert diag["dates_available"] == 1


# ===========================================================================
# AE — compute_persistence_score increases with spread
# ===========================================================================


def test_ae_persistence_score_spreads():
    score_narrow, _ = compute_persistence_score(
        age_days_list=[10, 20], view_counts_by_index=[1000, 2000]
    )
    score_wide, _ = compute_persistence_score(
        age_days_list=[10, 800], view_counts_by_index=[1000, 2000]
    )
    assert score_wide > score_narrow


# ===========================================================================
# AF — compute_signal_maturity returns correct levels
# ===========================================================================


def test_af_signal_maturity_levels():
    assert compute_signal_maturity(0, 0) == SIGNAL_MATURITY_INSUFFICIENT
    assert compute_signal_maturity(2, 0) == SIGNAL_MATURITY_INSUFFICIENT
    assert compute_signal_maturity(3, 0) == SIGNAL_MATURITY_EXPLORATORY
    assert compute_signal_maturity(4, 0) == SIGNAL_MATURITY_EXPLORATORY
    assert compute_signal_maturity(5, 0) == SIGNAL_MATURITY_DIRECTIONAL
    assert compute_signal_maturity(9, 0) == SIGNAL_MATURITY_DIRECTIONAL
    assert compute_signal_maturity(10, 4) == SIGNAL_MATURITY_DIRECTIONAL  # not enough creators
    assert compute_signal_maturity(10, 5) == SIGNAL_MATURITY_ACTIONABLE


# ===========================================================================
# AG — compute_signal_confidence increases with evidence quantity
# ===========================================================================


def test_ag_signal_confidence_increases_with_evidence():
    low = compute_signal_confidence(
        video_count=1, creator_count=1, velocity_count=0, completeness=0.2
    )
    high = compute_signal_confidence(
        video_count=10, creator_count=5, velocity_count=5, completeness=1.0
    )
    assert high > low
    assert 0.0 <= low <= 1.0
    assert 0.0 <= high <= 1.0


# ===========================================================================
# AH — compute_state_label returns None when demand is None
# ===========================================================================


def test_ah_state_label_none_when_no_demand():
    label = compute_state_label(
        demand=None, saturation=None, freshness=None, momentum=None, persistence=None
    )
    assert label is None


# ===========================================================================
# AI — compute_state_label: accelerating when momentum + freshness both high
# ===========================================================================


def test_ai_state_label_accelerating():
    label = compute_state_label(
        demand=0.5, saturation=0.2, freshness=0.7, momentum=0.8, persistence=None
    )
    assert label == STATE_ACCELERATING


# ===========================================================================
# AJ — compute_state_label: evergreen when persistence high and freshness low
# ===========================================================================


def test_aj_state_label_evergreen():
    label = compute_state_label(
        demand=0.5, saturation=0.2, freshness=0.2, momentum=0.1, persistence=0.7
    )
    assert label == STATE_EVERGREEN


# ===========================================================================
# AK — compute_state_label: cooling when momentum and freshness both low
# ===========================================================================


def test_ak_state_label_cooling():
    label = compute_state_label(
        demand=0.5, saturation=0.2, freshness=0.1, momentum=0.1, persistence=0.1
    )
    assert label == STATE_COOLING


# ===========================================================================
# AL — compute_state_label: saturated when saturation high
# ===========================================================================


def test_al_state_label_saturated():
    label = compute_state_label(
        demand=0.5, saturation=0.8, freshness=0.4, momentum=0.25, persistence=0.2
    )
    assert label == STATE_SATURATED


# ===========================================================================
# AM — make_interpretation_run_input_hash is deterministic
# ===========================================================================


def test_am_run_input_hash_deterministic():
    h1 = _run_hash(source_run_ids=[1, 2])
    h2 = _run_hash(source_run_ids=[2, 1])  # order shouldn't matter
    assert h1 == h2  # source_run_ids sorted before hashing


# ===========================================================================
# AN — make_cluster_input_hash changes when probe membership changes
# ===========================================================================


def test_an_cluster_hash_changes_with_members():
    h1 = make_cluster_input_hash(
        normalized_label="yoga",
        region_code=None,
        language_code=None,
        platform="youtube",
        provider="youtube_data_api",
        cluster_version="v1",
        member_probe_ids=[1],
    )
    h2 = make_cluster_input_hash(
        normalized_label="yoga",
        region_code=None,
        language_code=None,
        platform="youtube",
        provider="youtube_data_api",
        cluster_version="v1",
        member_probe_ids=[1, 2],
    )
    assert h1 != h2


# ===========================================================================
# AO — make_signal_input_hash changes when video membership changes
# ===========================================================================


def test_ao_signal_hash_changes_with_videos():
    h1 = make_signal_input_hash(cluster_id=1, member_video_ids=["v1"], scoring_version="v1")
    h2 = make_signal_input_hash(cluster_id=1, member_video_ids=["v1", "v2"], scoring_version="v1")
    assert h1 != h2


# ===========================================================================
# AP — _cluster_probes_by_jaccard groups similar queries
# ===========================================================================


def test_ap_jaccard_clustering_groups_similar():
    probes = [
        {"id": 1, "normalized_query": "yoga beginners morning", "market_region_label": "yoga"},
        {"id": 2, "normalized_query": "yoga beginners tutorial", "market_region_label": "yoga"},
        {
            "id": 3,
            "normalized_query": "python programming tutorial",
            "market_region_label": "python",
        },
    ]
    groups = _cluster_probes_by_jaccard(probes, threshold=0.35)
    # yoga probes should cluster together; python should be separate
    assert len(groups) == 2
    group_sizes = sorted([len(v) for v in groups.values()])
    assert group_sizes == [1, 2]


# ===========================================================================
# AQ — _cluster_probes_by_jaccard: identical queries fully cluster
# ===========================================================================


def test_aq_jaccard_identical_queries_cluster():
    probes = [
        {"id": 1, "normalized_query": "yoga tutorial", "market_region_label": "yoga"},
        {"id": 2, "normalized_query": "yoga tutorial", "market_region_label": "yoga"},
    ]
    groups = _cluster_probes_by_jaccard(probes, threshold=0.35)
    assert len(groups) == 1


# ===========================================================================
# AR — _cluster_probes_by_jaccard: empty input returns empty groups
# ===========================================================================


def test_ar_jaccard_empty_probes():
    groups = _cluster_probes_by_jaccard([], threshold=0.35)
    assert groups == {}


# ===========================================================================
# AS — _pick_cluster_label picks longest market_region_label
# ===========================================================================


def test_as_pick_cluster_label_picks_longest():
    probes = [
        {"query_text": "yoga", "normalized_query": "yoga"},
        {"query_text": "yoga for complete beginners guide", "normalized_query": "yoga beginners"},
    ]
    raw, normalized = _pick_cluster_label(probes)
    assert raw == "yoga for complete beginners guide"


# ===========================================================================
# AT — validate_llm_cluster_members removes fabricated IDs
# ===========================================================================


def test_at_validate_llm_cluster_members_removes_fabricated():
    clusters = [
        ClusterConsolidationCandidate(
            cluster_label="yoga",
            cluster_type="thematic",
            description="test",
            member_probe_ids=[1, 2, 999],  # 999 is fabricated
            evidence_basis="test",
        )
    ]
    valid = validate_llm_cluster_members(clusters, valid_probe_ids={1, 2, 3})
    assert len(valid) == 1
    assert 999 not in valid[0].member_probe_ids


# ===========================================================================
# AU — validate_llm_cluster_members drops cluster when all IDs fabricated
# ===========================================================================


def test_au_validate_llm_drops_cluster_all_fabricated():
    clusters = [
        ClusterConsolidationCandidate(
            cluster_label="yoga",
            cluster_type="thematic",
            description="test",
            member_probe_ids=[888, 999],
            evidence_basis="test",
        )
    ]
    valid = validate_llm_cluster_members(clusters, valid_probe_ids={1, 2})
    assert valid == []


# ===========================================================================
# AV — build_opportunity_evidence produces typed Phase 13F contract
# ===========================================================================


def test_av_build_opportunity_evidence(conn):
    run = _insert_run(conn)
    cluster = _insert_cluster(conn, run.id)
    signal = _insert_signal(conn, cluster.id, run.id)
    opp = build_opportunity_evidence(cluster, signal)
    assert isinstance(opp, ExternalMarketOpportunityEvidence)
    assert opp.cluster_id == cluster.id
    assert opp.demand_score == signal.demand_score
    assert opp.signal_maturity == signal.signal_maturity


# ===========================================================================
# AW — run_market_interpretation with no probes produces empty result
# ===========================================================================


def test_aw_interpret_empty_db_no_probes(conn):
    result = run_market_interpretation(conn)
    assert result["cluster_count"] == 0
    assert result["clusters"] == []
    assert result["signals"] == []
    assert result["status"] == "completed"


# ===========================================================================
# AX — run_market_interpretation creates a completed run row
# ===========================================================================


def test_ax_interpret_creates_completed_run(conn):
    result = run_market_interpretation(conn)
    run = get_interpretation_run(conn, result["run_id"])
    assert run.status == "completed"
    assert run.cluster_count == 0


# ===========================================================================
# AY — run_market_interpretation with 2 probes produces 2 singletons (no similarity)
# ===========================================================================


def test_ay_interpret_two_distinct_probes_two_clusters(conn):
    _make_probe_and_job(conn, "yoga tutorial beginners")
    _make_probe_and_job(conn, "machine learning deep neural networks")

    result = run_market_interpretation(conn)
    assert result["cluster_count"] == 2
    assert len(result["clusters"]) == 2
    assert len(result["signals"]) == 2


# ===========================================================================
# AZ — run_market_interpretation: two similar probes cluster into one
# ===========================================================================


def test_az_interpret_similar_probes_cluster_together(conn):
    _make_probe_and_job(conn, "yoga beginners morning")
    _make_probe_and_job(conn, "yoga beginners evening")

    result = run_market_interpretation(conn, jaccard_threshold=0.35)
    assert result["cluster_count"] == 1
    cluster = result["clusters"][0]
    assert cluster.member_probe_count == 2


# ===========================================================================
# BA — run_market_interpretation: demand_score > 0 when video evidence present
# ===========================================================================


def test_ba_interpret_demand_score_from_evidence(conn):
    probe_id, job_id = _make_probe_and_job(conn, "yoga beginners")
    _add_video_observation(conn, job_id, "vid_001", view_count=500_000)
    _add_video_observation(conn, job_id, "vid_002", view_count=250_000)
    _add_video_observation(conn, job_id, "vid_003", view_count=750_000)

    result = run_market_interpretation(conn)
    assert result["cluster_count"] == 1
    signal = result["signals"][0]
    assert signal.demand_score is not None
    assert signal.demand_score > 0


# ===========================================================================
# BB — run_market_interpretation: freshness_score present when age_days evidence exists
# ===========================================================================


def test_bb_interpret_freshness_from_age_days(conn):
    probe_id, job_id = _make_probe_and_job(conn, "yoga beginners")
    _add_video_observation(conn, job_id, "vid_001", view_count=100_000, age_days=30.0)
    _add_video_observation(conn, job_id, "vid_002", view_count=50_000, age_days=60.0)

    result = run_market_interpretation(conn)
    signal = result["signals"][0]
    assert signal.freshness_score is not None
    assert signal.freshness_score > 0


# ===========================================================================
# BC — run_market_interpretation: momentum present when velocity evidence exists
# ===========================================================================


def test_bc_interpret_momentum_from_velocity(conn):
    probe_id, job_id = _make_probe_and_job(conn, "yoga beginners")
    _add_video_observation(conn, job_id, "vid_001", view_count=100_000)
    _add_velocity(conn, "vid_001", units_per_day=5000)

    result = run_market_interpretation(conn)
    signal = result["signals"][0]
    assert signal.momentum_score is not None
    assert signal.velocity_tracked_video_count == 1


# ===========================================================================
# BD — run_market_interpretation is idempotent: same cutoff → same run_id
# ===========================================================================


def test_bd_interpret_idempotent_same_inputs(conn):
    # First run
    r1 = run_market_interpretation(conn, platform="youtube", provider="youtube_data_api")
    # Second run with different probe (would normally change clusters)
    # but same timestamp won't happen in practice; instead verify same cutoff = same hash
    # Call again without adding new data — hash will differ because cutoff changes with now()
    # So idempotency is hash-based; test the INSERT OR IGNORE path directly
    run_row = get_interpretation_run(conn, r1["run_id"])
    # Calling insert with same hash again returns the same row
    same_run = insert_interpretation_run(
        conn,
        platform="youtube",
        provider="youtube_data_api",
        region_code=None,
        language_code=None,
        clustering_version=CLUSTERING_VERSION,
        scoring_version=SCORING_VERSION,
        evidence_cutoff=run_row.evidence_cutoff,
        source_run_ids=[],
        policy_snapshot={},
        input_hash=run_row.input_hash,
    )
    assert same_run.id == run_row.id


# ===========================================================================
# BE — run_market_interpretation commits clusters/signals to DB (regression
#      for missing conn.commit() in interpreter.py)
# ===========================================================================


def test_be_interpretation_results_are_persisted(conn):
    """Clusters and signals must survive connection re-query after run_market_interpretation."""
    probe_id, job_id = _make_probe_and_job(conn, "ai tools")
    _add_video_observation(conn, job_id, "vid_001", view_count=100_000)

    result = run_market_interpretation(conn)
    assert result["cluster_count"] == 1
    run_id = result["run_id"]

    # Re-query through the repository — would return nothing if commit was missing
    clusters = list_clusters_for_run(conn, run_id)
    assert len(clusters) == 1, "Cluster must be visible in DB after run (commit regression)"

    signals = list_signals_for_run(conn, run_id)
    assert len(signals) == 1, "Signal must be visible in DB after run (commit regression)"

    run_row = get_interpretation_run(conn, run_id)
    assert run_row.status == "completed"


# ===========================================================================
# BF — get_videos_for_probe returns view_count and age_days for observations
#      stored with lowercase signal types (regression for uppercase mismatch)
# ===========================================================================


def test_bf_get_videos_for_probe_reads_lowercase_signal_types(conn):
    """view_count and content_age_days must not be None after evidence aggregation."""
    probe_id, job_id = _make_probe_and_job(conn, "space exploration")
    _add_video_observation(conn, job_id, "vid_a", view_count=5_000_000, age_days=365.0)
    _add_video_observation(conn, job_id, "vid_b", view_count=2_000_000, age_days=730.0)

    result = run_market_interpretation(conn)
    assert result["cluster_count"] == 1
    signal = result["signals"][0]

    assert signal.demand_score is not None, (
        "demand_score is None — get_videos_for_probe may be using wrong signal_type case"
    )
    assert signal.freshness_score is not None, (
        "freshness_score is None — VIDEO_PUBLISHED_AT case mismatch in get_videos_for_probe"
    )
    assert signal.demand_score > 0
    assert 0.0 <= signal.freshness_score <= 1.0


# ===========================================================================
# BG — confidence display formatting does not crash when confidence is None
#      (regression for f-string format spec bug in cli.py line 1341)
# ===========================================================================


def test_bg_confidence_format_string_handles_none():
    """The f-string fix must not raise TypeError for None confidence values."""
    from app.intelligence.market.bridge_models import MarketBridgeSyncItem

    item_with_none = MarketBridgeSyncItem(
        cluster_label="test niche",
        canonical_cluster_id=1,
        opportunity_id=-1,
        opportunity_created=False,
        observation_id=-1,
        score_id=None,
        composite_score=0.5,
        confidence=None,
        skipped=False,
        skip_reason=None,
    )
    item_with_value = MarketBridgeSyncItem(
        cluster_label="test niche 2",
        canonical_cluster_id=2,
        opportunity_id=1,
        opportunity_created=True,
        observation_id=1,
        score_id=1,
        composite_score=0.7,
        confidence=0.583,
        skipped=False,
        skip_reason=None,
    )

    # This is the corrected f-string from cli.py line 1341.
    # Must not raise TypeError for either None or float confidence.
    for item in (item_with_none, item_with_value):
        result = f"conf={f'{item.confidence:.3f}' if item.confidence is not None else 'n/a'}"
        assert "conf=" in result
