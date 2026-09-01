"""Phase 13D-A tests — Market Exploration Planner: schema, models, repository.

Tests A–AN (40 tests).
"""

from __future__ import annotations

import pathlib
import sqlite3
import tempfile

import pytest
from pydantic import ValidationError

from app.core.database import SCHEMA_VERSION, open_db
from app.intelligence.market.planner_models import (
    CollectionPolicy,
    ExplorationProbeStatus,
    ExplorationProbeType,
    ExplorationRunStatus,
    PriorityComponents,
    ProbeEvidenceType,
    SemanticFitEvaluatorType,
    SemanticNicheFit,
    make_probe_input_hash,
)
from app.intelligence.market.planner_repository import (
    create_exploration_probe,
    create_exploration_run,
    get_exploration_probe,
    get_exploration_run,
    get_probe_evidence,
    link_probe_evidence,
    list_exploration_probes,
    list_exploration_runs,
    update_exploration_run_status,
    update_probe_dispatch,
    update_probe_status,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def conn():
    with tempfile.TemporaryDirectory() as d:
        c = open_db(pathlib.Path(d) / "test.db")
        yield c
        c.close()


def _make_run(conn, **overrides):
    defaults = dict(channel_id=None, workspace_id=None)
    defaults.update(overrides)
    return create_exploration_run(conn, **defaults)


def _make_probe(conn, run_id: int, **overrides):
    defaults = dict(
        run_id=run_id,
        query_text="python tutorials",
        normalized_query="python tutorials",
        probe_type=ExplorationProbeType.CHANNEL_BOOTSTRAP,
        channel_id=None,
    )
    defaults.update(overrides)
    return create_exploration_probe(conn, **defaults)


# ---------------------------------------------------------------------------
# A — Schema version is 32
# ---------------------------------------------------------------------------


def test_a_schema_version_is_31():
    assert SCHEMA_VERSION == 51


# ---------------------------------------------------------------------------
# B — market_exploration_runs table exists in a fresh DB
# ---------------------------------------------------------------------------


def test_b_exploration_runs_table_exists(conn):
    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "market_exploration_runs" in tables


# ---------------------------------------------------------------------------
# C — market_exploration_probes table exists
# ---------------------------------------------------------------------------


def test_c_exploration_probes_table_exists(conn):
    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "market_exploration_probes" in tables


# ---------------------------------------------------------------------------
# D — market_probe_evidence table exists
# ---------------------------------------------------------------------------


def test_d_probe_evidence_table_exists(conn):
    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "market_probe_evidence" in tables


# ---------------------------------------------------------------------------
# E — market_exploration_runs indexes present
# ---------------------------------------------------------------------------


def test_e_exploration_runs_indexes(conn):
    indexes = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    }
    assert "idx_mer_channel" in indexes
    assert "idx_mer_workspace" in indexes
    assert "idx_mer_status" in indexes


# ---------------------------------------------------------------------------
# F — market_exploration_probes indexes present
# ---------------------------------------------------------------------------


def test_f_exploration_probes_indexes(conn):
    indexes = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    }
    assert "idx_mep_run" in indexes
    assert "idx_mep_channel" in indexes
    assert "idx_mep_parent_probe" in indexes
    assert "idx_mep_dispatched_job" in indexes
    assert "idx_mep_probe_type" in indexes


# ---------------------------------------------------------------------------
# G — market_probe_evidence indexes present
# ---------------------------------------------------------------------------


def test_g_probe_evidence_indexes(conn):
    indexes = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    }
    assert "idx_mpe_probe" in indexes
    assert "idx_mpe_observation" in indexes
    assert "idx_mpe_velocity" in indexes


# ---------------------------------------------------------------------------
# H — market_exploration_runs columns match spec
# ---------------------------------------------------------------------------


def test_h_exploration_runs_columns(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(market_exploration_runs)").fetchall()}
    required = {
        "id",
        "workspace_id",
        "channel_id",
        "planner_version",
        "prompt_version",
        "provider",
        "model",
        "max_depth",
        "max_probes",
        "search_budget",
        "policy_json",
        "input_hash",
        "status",
        "candidate_count",
        "selected_count",
        "deferred_count",
        "rejected_count",
        "dispatched_count",
        "error_message",
        "started_at",
        "completed_at",
        "created_at",
    }
    assert required <= cols


# ---------------------------------------------------------------------------
# I — market_exploration_probes columns match spec
# ---------------------------------------------------------------------------


def test_i_exploration_probes_columns(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(market_exploration_probes)").fetchall()}
    required = {
        "id",
        "exploration_run_id",
        "workspace_id",
        "channel_id",
        "query_text",
        "normalized_query",
        "probe_type",
        "parent_probe_id",
        "parent_job_id",
        "exploration_depth",
        "region_code",
        "language_code",
        "collection_policy_json",
        "status",
        "priority_score",
        "priority_components_json",
        "niche_fit_score",
        "semantic_fit_status",
        "decision_reason",
        "corroboration_count",
        "dispatched_job_id",
        "planner_version",
        "input_hash",
        "decided_at",
        "dispatched_at",
        "created_at",
    }
    assert required <= cols


# ---------------------------------------------------------------------------
# J — ExplorationRunStatus enum values
# ---------------------------------------------------------------------------


def test_j_run_status_enum_values():
    assert ExplorationRunStatus.PENDING == "pending"
    assert ExplorationRunStatus.RUNNING == "running"
    assert ExplorationRunStatus.COMPLETED == "completed"
    assert ExplorationRunStatus.PARTIAL == "partial"
    assert ExplorationRunStatus.FAILED == "failed"


# ---------------------------------------------------------------------------
# K — ExplorationProbeType enum values
# ---------------------------------------------------------------------------


def test_k_probe_type_enum_values():
    assert ExplorationProbeType.CHANNEL_BOOTSTRAP == "channel_bootstrap"
    assert ExplorationProbeType.MARKET_REGION == "market_region"
    assert ExplorationProbeType.ADJACENT_TOPIC == "adjacent_topic"
    assert ExplorationProbeType.VELOCITY_FOLLOWUP == "velocity_followup"
    assert ExplorationProbeType.VALIDATION == "validation"


# ---------------------------------------------------------------------------
# L — ExplorationProbeStatus enum values
# ---------------------------------------------------------------------------


def test_l_probe_status_enum_values():
    assert ExplorationProbeStatus.CANDIDATE == "candidate"
    assert ExplorationProbeStatus.SELECTED == "selected"
    assert ExplorationProbeStatus.DEFERRED == "deferred"
    assert ExplorationProbeStatus.REJECTED == "rejected"
    assert ExplorationProbeStatus.DISPATCHED == "dispatched"


# ---------------------------------------------------------------------------
# M — CollectionPolicy defaults and validation
# ---------------------------------------------------------------------------


def test_m_collection_policy_defaults():
    p = CollectionPolicy()
    assert p.max_pages == 1
    assert p.max_results == 25
    assert p.order == "relevance"
    assert p.expected_max_search_calls == 1
    assert p.region_code is None
    assert p.published_after is None


def test_m2_collection_policy_expected_calls_must_gte_max_pages():
    with pytest.raises(ValidationError):
        CollectionPolicy(max_pages=3, expected_max_search_calls=2)


def test_m3_collection_policy_invalid_order_rejected():
    with pytest.raises(ValidationError):
        CollectionPolicy(order="invalid_order")


# ---------------------------------------------------------------------------
# N — PriorityComponents all optional, range-validated
# ---------------------------------------------------------------------------


def test_n_priority_components_all_none():
    pc = PriorityComponents()
    assert pc.niche_fit is None
    assert pc.novelty is None
    assert pc.evidence_strength is None
    assert pc.velocity_trigger is None
    assert pc.corroboration is None
    assert pc.depth_factor is None


def test_n2_priority_components_bounds_enforced():
    with pytest.raises(ValidationError):
        PriorityComponents(niche_fit=1.1)
    with pytest.raises(ValidationError):
        PriorityComponents(novelty=-0.01)


# ---------------------------------------------------------------------------
# O — SemanticNicheFit contract
# ---------------------------------------------------------------------------


def test_o_semantic_niche_fit_eligible():
    snf = SemanticNicheFit(
        eligible=True,
        fit_score=0.87,
        rationale="high overlap with primary niche",
        evaluator_type=SemanticFitEvaluatorType.LEXICAL,
        evaluator_version="v1",
    )
    assert snf.eligible is True
    assert snf.fit_score == 0.87
    assert snf.evaluator_type == "lexical"


def test_o2_semantic_niche_fit_score_bounds_enforced():
    with pytest.raises(ValidationError):
        SemanticNicheFit(
            eligible=False,
            fit_score=1.5,
            rationale="x",
            evaluator_type="lexical",
            evaluator_version="v1",
        )


# ---------------------------------------------------------------------------
# P — make_probe_input_hash determinism
# ---------------------------------------------------------------------------


def test_p_probe_input_hash_is_deterministic():
    h1 = make_probe_input_hash(
        normalized_query="python tutorials",
        probe_type="channel_bootstrap",
        region_code="US",
        language_code="en",
        max_pages=1,
        max_results=25,
        order="relevance",
        published_after=None,
        planner_version="v1",
    )
    h2 = make_probe_input_hash(
        normalized_query="python tutorials",
        probe_type="channel_bootstrap",
        region_code="US",
        language_code="en",
        max_pages=1,
        max_results=25,
        order="relevance",
        published_after=None,
        planner_version="v1",
    )
    assert h1 == h2
    assert len(h1) == 64


# ---------------------------------------------------------------------------
# Q — make_probe_input_hash changes with any field
# ---------------------------------------------------------------------------


def test_q_probe_input_hash_differs_on_query_change():
    base = dict(
        normalized_query="python tutorials",
        probe_type="channel_bootstrap",
        region_code=None,
        language_code=None,
        max_pages=1,
        max_results=25,
        order="relevance",
        published_after=None,
        planner_version="v1",
    )
    h1 = make_probe_input_hash(**base)
    h2 = make_probe_input_hash(**{**base, "normalized_query": "data science"})
    h3 = make_probe_input_hash(**{**base, "probe_type": "market_region"})
    assert h1 != h2
    assert h1 != h3
    # max_pages is in hash so different max_pages -> different hash
    h4 = make_probe_input_hash(**{**base, "max_pages": 2})
    assert h1 != h4


# ---------------------------------------------------------------------------
# R — create_exploration_run returns correct fields
# ---------------------------------------------------------------------------


def test_r_create_exploration_run(conn):
    run = _make_run(conn)
    assert run.id > 0
    assert run.status == "pending"
    assert run.planner_version == "v1"
    assert run.channel_id is None
    assert run.workspace_id is None
    assert run.max_depth == 3
    assert run.max_probes == 10
    assert run.search_budget == 20
    assert run.candidate_count == 0
    assert len(run.input_hash) == 64


# ---------------------------------------------------------------------------
# S — get_exploration_run returns None for missing ID
# ---------------------------------------------------------------------------


def test_s_get_missing_run_returns_none(conn):
    assert get_exploration_run(conn, 9999) is None


# ---------------------------------------------------------------------------
# T — list_exploration_runs filters by status
# ---------------------------------------------------------------------------


def test_t_list_runs_filter_by_status(conn):
    r1 = _make_run(conn)
    r2 = _make_run(conn)
    update_exploration_run_status(conn, r2.id, status="running")

    pending = list_exploration_runs(conn, status="pending")
    running = list_exploration_runs(conn, status="running")
    assert all(r.status == "pending" for r in pending)
    assert all(r.status == "running" for r in running)
    assert r1.id in {r.id for r in pending}
    assert r2.id in {r.id for r in running}


# ---------------------------------------------------------------------------
# U — update_exploration_run_status transitions correctly
# ---------------------------------------------------------------------------


def test_u_update_run_status(conn):
    run = _make_run(conn)
    updated = update_exploration_run_status(
        conn,
        run.id,
        status="running",
        started_at="2026-08-20T10:00:00",
        candidate_count=5,
    )
    assert updated.status == "running"
    assert updated.started_at == "2026-08-20T10:00:00"
    assert updated.candidate_count == 5


def test_u2_update_run_status_completed(conn):
    run = _make_run(conn)
    update_exploration_run_status(conn, run.id, status="running")
    done = update_exploration_run_status(
        conn,
        run.id,
        status="completed",
        selected_count=3,
        deferred_count=2,
        rejected_count=1,
        dispatched_count=3,
        completed_at="2026-08-20T10:30:00",
    )
    assert done.status == "completed"
    assert done.selected_count == 3
    assert done.deferred_count == 2
    assert done.rejected_count == 1
    assert done.dispatched_count == 3


# ---------------------------------------------------------------------------
# V — create_exploration_probe returns correct fields
# ---------------------------------------------------------------------------


def test_v_create_probe(conn):
    run = _make_run(conn)
    probe = _make_probe(conn, run.id)
    assert probe.id > 0
    assert probe.exploration_run_id == run.id
    assert probe.status == "candidate"
    assert probe.probe_type == "channel_bootstrap"
    assert probe.query_text == "python tutorials"
    assert probe.corroboration_count == 0
    assert probe.exploration_depth == 0
    assert len(probe.input_hash) == 64


# ---------------------------------------------------------------------------
# W — within-run probe deduplication via UNIQUE(run_id, input_hash)
# ---------------------------------------------------------------------------


def test_w_within_run_probe_dedup(conn):
    run = _make_run(conn)
    _make_probe(conn, run.id, query_text="python tutorials", normalized_query="python tutorials")
    with pytest.raises(sqlite3.IntegrityError):
        _make_probe(
            conn, run.id, query_text="Python Tutorials!", normalized_query="python tutorials"
        )


# ---------------------------------------------------------------------------
# X — same normalized query in different runs is allowed (cross-run NOT deduplicated)
# ---------------------------------------------------------------------------


def test_x_cross_run_probe_allowed(conn):
    run1 = _make_run(conn)
    run2 = _make_run(conn)
    p1 = _make_probe(conn, run1.id, normalized_query="python tutorials")
    p2 = _make_probe(conn, run2.id, normalized_query="python tutorials")
    assert p1.id != p2.id


# ---------------------------------------------------------------------------
# Y — get_exploration_probe returns the probe
# ---------------------------------------------------------------------------


def test_y_get_probe(conn):
    run = _make_run(conn)
    probe = _make_probe(conn, run.id)
    fetched = get_exploration_probe(conn, probe.id)
    assert fetched is not None
    assert fetched.id == probe.id
    assert fetched.query_text == probe.query_text


# ---------------------------------------------------------------------------
# Z — list_exploration_probes filters by run_id and status
# ---------------------------------------------------------------------------


def test_z_list_probes_filter(conn):
    run1 = _make_run(conn)
    run2 = _make_run(conn)
    p1 = _make_probe(conn, run1.id, normalized_query="q1")
    p2 = _make_probe(conn, run1.id, normalized_query="q2", probe_type="market_region")
    _make_probe(conn, run2.id, normalized_query="q3")

    run1_probes = list_exploration_probes(conn, run_id=run1.id)
    assert len(run1_probes) == 2
    assert {p.id for p in run1_probes} == {p1.id, p2.id}


# ---------------------------------------------------------------------------
# AA — update_probe_status transitions and sets fields
# ---------------------------------------------------------------------------


def test_aa_update_probe_status(conn):
    run = _make_run(conn)
    probe = _make_probe(conn, run.id)
    updated = update_probe_status(
        conn,
        probe.id,
        status="selected",
        decided_at="2026-08-20T10:01:00",
        decision_reason="niche fit sufficient",
        priority_score=0.75,
        niche_fit_score=0.82,
        semantic_fit_status="eligible",
    )
    assert updated.status == "selected"
    assert updated.decided_at == "2026-08-20T10:01:00"
    assert updated.decision_reason == "niche fit sufficient"
    assert updated.priority_score == pytest.approx(0.75)
    assert updated.niche_fit_score == pytest.approx(0.82)
    assert updated.semantic_fit_status == "eligible"


def test_aa2_update_probe_status_deferred(conn):
    run = _make_run(conn)
    probe = _make_probe(conn, run.id)
    updated = update_probe_status(
        conn, probe.id, status="deferred", decision_reason="quota exceeded"
    )
    assert updated.status == "deferred"
    assert updated.decision_reason == "quota exceeded"


def test_aa3_update_probe_status_rejected(conn):
    run = _make_run(conn)
    probe = _make_probe(conn, run.id)
    updated = update_probe_status(
        conn, probe.id, status="rejected", decision_reason="niche drift detected"
    )
    assert updated.status == "rejected"


# ---------------------------------------------------------------------------
# AB — update_probe_dispatch sets dispatched_job_id and status
# ---------------------------------------------------------------------------


def test_ab_update_probe_dispatch(conn):
    from app.intelligence.market.repository import create_market_collection_job

    run = _make_run(conn)
    probe = _make_probe(conn, run.id)
    update_probe_status(conn, probe.id, status="selected")

    job = create_market_collection_job(conn, job_type="search_scan")
    dispatched = update_probe_dispatch(
        conn,
        probe.id,
        dispatched_job_id=job.id,
        dispatched_at="2026-08-20T10:05:00",
    )
    assert dispatched.status == "dispatched"
    assert dispatched.dispatched_job_id == job.id
    assert dispatched.dispatched_at == "2026-08-20T10:05:00"


# ---------------------------------------------------------------------------
# AC — link_probe_evidence creates an evidence row
# ---------------------------------------------------------------------------


def test_ac_link_probe_evidence(conn):
    import hashlib

    from app.intelligence.market import models as m
    from app.intelligence.market import repository as mrepo

    run = _make_run(conn)
    probe = _make_probe(conn, run.id)

    obs = mrepo.persist_observation(
        conn,
        collector_name="test",
        signal_type=m.VIDEO_VIEW_COUNT,
        observed_at="2026-08-20T10:00:00",
        input_hash=hashlib.sha256(b"obs_evidence_test").hexdigest(),
        external_video_id="vid_x",
        signal_value_numeric=1000.0,
    )
    evidence = link_probe_evidence(
        conn,
        probe_id=probe.id,
        evidence_type=ProbeEvidenceType.OBSERVATION,
        observation_id=obs.id,
        notes="top search result",
    )
    assert evidence.id > 0
    assert evidence.probe_id == probe.id
    assert evidence.evidence_type == "observation"
    assert evidence.observation_id == obs.id
    assert evidence.evidence_notes == "top search result"


# ---------------------------------------------------------------------------
# AD — get_probe_evidence returns all links
# ---------------------------------------------------------------------------


def test_ad_get_probe_evidence_multiple(conn):
    import hashlib

    from app.intelligence.market import models as m
    from app.intelligence.market import repository as mrepo

    run = _make_run(conn)
    probe = _make_probe(conn, run.id)

    for i in range(3):
        obs = mrepo.persist_observation(
            conn,
            collector_name="test",
            signal_type=m.VIDEO_VIEW_COUNT,
            observed_at="2026-08-20T10:00:00",
            input_hash=hashlib.sha256(f"obs_multi_{i}".encode()).hexdigest(),
            external_video_id=f"vid_{i}",
            signal_value_numeric=float(i * 1000),
        )
        link_probe_evidence(
            conn, probe_id=probe.id, evidence_type="observation", observation_id=obs.id
        )

    evidence = get_probe_evidence(conn, probe.id)
    assert len(evidence) == 3


# ---------------------------------------------------------------------------
# AE — get_probe_evidence returns empty list for probe with no evidence
# ---------------------------------------------------------------------------


def test_ae_get_probe_evidence_empty(conn):
    run = _make_run(conn)
    probe = _make_probe(conn, run.id)
    evidence = get_probe_evidence(conn, probe.id)
    assert evidence == []


# ---------------------------------------------------------------------------
# AF — ExplorationRun.policy() deserializes to dict
# ---------------------------------------------------------------------------


def test_af_run_policy_helper(conn):
    policy = CollectionPolicy(max_pages=2, max_results=30, expected_max_search_calls=2)
    run = create_exploration_run(conn, channel_id=None, policy_snapshot=policy)
    d = run.policy()
    assert isinstance(d, dict)
    assert d["max_pages"] == 2
    assert d["max_results"] == 30


# ---------------------------------------------------------------------------
# AG — ExplorationProbe.collection_policy() deserializes to CollectionPolicy
# ---------------------------------------------------------------------------


def test_ag_probe_collection_policy_helper(conn):
    run = _make_run(conn)
    policy = CollectionPolicy(max_pages=2, region_code="US", expected_max_search_calls=2)
    probe = create_exploration_probe(
        conn,
        run_id=run.id,
        query_text="ai music",
        normalized_query="ai music",
        probe_type="market_region",
        collection_policy=policy,
    )
    recovered = probe.collection_policy()
    assert isinstance(recovered, CollectionPolicy)
    assert recovered.max_pages == 2
    assert recovered.region_code == "US"


# ---------------------------------------------------------------------------
# AH — ExplorationProbe.priority_components() returns None when not set
# ---------------------------------------------------------------------------


def test_ah_probe_priority_components_none_by_default(conn):
    run = _make_run(conn)
    probe = _make_probe(conn, run.id)
    assert probe.priority_components() is None


# ---------------------------------------------------------------------------
# AI — priority_components serialized/deserialized round-trip
# ---------------------------------------------------------------------------


def test_ai_probe_priority_components_roundtrip(conn):
    run = _make_run(conn)
    probe = _make_probe(conn, run.id)
    pc = PriorityComponents(niche_fit=0.8, novelty=0.6, depth_factor=0.5)
    update_probe_status(
        conn,
        probe.id,
        status="selected",
        priority_components_json=pc.model_dump_json(),
    )
    fetched = get_exploration_probe(conn, probe.id)
    recovered = fetched.priority_components()
    assert recovered is not None
    assert recovered.niche_fit == pytest.approx(0.8)
    assert recovered.novelty == pytest.approx(0.6)
    assert recovered.depth_factor == pytest.approx(0.5)
    assert recovered.corroboration is None


# ---------------------------------------------------------------------------
# AJ — probe tree: parent_probe_id self-reference
# ---------------------------------------------------------------------------


def test_aj_probe_tree_parent_reference(conn):
    run = _make_run(conn)
    parent = _make_probe(
        conn,
        run.id,
        normalized_query="python tutorials root",
        query_text="python tutorials root",
    )
    child = create_exploration_probe(
        conn,
        run_id=run.id,
        query_text="python tutorial for beginners",
        normalized_query="python tutorial beginners",
        probe_type=ExplorationProbeType.ADJACENT_TOPIC,
        parent_probe_id=parent.id,
        exploration_depth=1,
    )
    assert child.parent_probe_id == parent.id
    assert child.exploration_depth == 1


# ---------------------------------------------------------------------------
# AK — DB CHECK constraint on probe_type rejects invalid value
# ---------------------------------------------------------------------------


def test_ak_probe_type_check_constraint(conn):
    run = _make_run(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO market_exploration_probes
                (exploration_run_id, query_text, normalized_query, probe_type,
                 collection_policy_json, status, corroboration_count,
                 planner_version, input_hash, created_at)
            VALUES (?, 'q', 'q', 'invalid_type', '{}', 'candidate', 0, 'v1',
                    'abc123', '2026-08-20T00:00:00')
            """,
            (run.id,),
        )


# ---------------------------------------------------------------------------
# AL — DB CHECK constraint on run status rejects invalid value
# ---------------------------------------------------------------------------


def test_al_run_status_check_constraint(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO market_exploration_runs
                (planner_version, max_depth, max_probes, search_budget,
                 policy_json, input_hash, status, created_at)
            VALUES ('v1', 3, 10, 20, '{}', 'hash_al_test', 'invalid_status', '2026-08-20T00:00:00')
            """
        )


# ---------------------------------------------------------------------------
# AM — Observations table has no channel_id (global scope unchanged)
# ---------------------------------------------------------------------------


def test_am_observations_still_global_no_channel_id(conn):
    cols = {
        r[1] for r in conn.execute("PRAGMA table_info(market_intelligence_observations)").fetchall()
    }
    assert "channel_id" not in cols


# ---------------------------------------------------------------------------
# AN — Exploration tables ARE channel-scoped (have channel_id)
# ---------------------------------------------------------------------------


def test_an_exploration_tables_are_channel_scoped(conn):
    run_cols = {r[1] for r in conn.execute("PRAGMA table_info(market_exploration_runs)").fetchall()}
    probe_cols = {
        r[1] for r in conn.execute("PRAGMA table_info(market_exploration_probes)").fetchall()
    }
    assert "channel_id" in run_cols
    assert "channel_id" in probe_cols
