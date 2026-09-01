"""Phase 13E.1 tests — Semantic Cluster Identity + Interpretation Reproducibility Hardening.

Tests A–AT (46 tests).

SCOPE
-----
- Schema v33 (market_canonical_clusters + 2 new columns)
- Hybrid clustering: Jaccard graph edges + optional LLM semantic adjudication
- VIDEO_TITLE evidence in semantic enrichment
- Canonical cluster identity: create, match, cross-run reuse
- Signal history across interpretation runs via canonical_cluster_id
- Policy snapshot: auto-built, complete, and persisted
- ExternalMarketOpportunityEvidence: canonical_cluster_id present
- Phase 13E.1 contract for Phase 13F
- Regression: multi-video outlier, multi-creator momentum, missing vs zero, policy snapshot

NO live API calls. NO Phase 13F work.
FakeProvider from app.ai.fake used for all LLM tests.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sqlite3
import tempfile

import pytest

from app.ai.fake import FakeProvider
from app.ai.provider import AIResponse
from app.core.database import open_db
from app.intelligence.market.interpretation_models import (
    DEMAND_SINGLE_VIDEO_CAP,
    DEMAND_VIEW_LOG_SCALE,
    FRESHNESS_HALFLIFE_DAYS,
    FRESHNESS_RECENT_THRESHOLD_DAYS,
    MOMENTUM_VPD_LOG_SCALE,
    PERSISTENCE_SPREAD_DAYS,
    SATURATION_CREATOR_SCALE,
    SATURATION_SEARCH_LOG_SCALE,
    SEMANTIC_CLUSTERING_VERSION,
    ClusterAdjudicationGroup,
    ClusterAdjudicationOutput,
    ExternalMarketOpportunityEvidence,
    build_interpretation_policy_snapshot,
    compute_demand_score,
    compute_momentum_score,
    make_canonical_cluster_fingerprint,
    validate_adjudication_output,
)
from app.intelligence.market.interpretation_repository import (
    get_canonical_cluster,
)
from app.intelligence.market.interpreter import (
    _compute_jaccard_edges,
    _groups_from_edges,
    get_canonical_signal_history,
    run_market_interpretation,
)
from app.intelligence.market.models import VIDEO_TITLE

_NOW = "2026-08-20T00:00:00"


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def conn():
    with tempfile.TemporaryDirectory() as d:
        c = open_db(pathlib.Path(d) / "test.db")
        yield c
        c.close()


def _make_probe_and_job(
    conn: sqlite3.Connection,
    query: str,
    market_region_label: str | None = None,
) -> tuple[int, int]:
    """Create exploration run + probe + collection job. Returns (probe_id, job_id)."""
    from app.intelligence.market.planner_repository import (
        create_exploration_probe,
        create_exploration_run,
        update_probe_dispatch,
    )
    from app.intelligence.market.repository import create_market_collection_job

    job = create_market_collection_job(
        conn, job_type="search_scan", origin_type="exploration_planner"
    )
    run = create_exploration_run(conn)
    probe = create_exploration_probe(
        conn,
        run_id=run.id,
        query_text=query,
        normalized_query=query,
        probe_type="market_region",
        market_region_label=market_region_label,
    )
    update_probe_dispatch(conn, probe.id, dispatched_job_id=job.id, dispatched_at=_NOW)
    return probe.id, job.id


def _add_video_title(
    conn: sqlite3.Connection,
    job_id: int,
    video_id: str,
    title: str,
) -> None:
    """Persist a VIDEO_TITLE observation linked to the job."""
    from app.intelligence.market.repository import link_job_observation, persist_observation

    h = hashlib.sha256(
        json.dumps({"job": job_id, "vid": video_id, "t": "TITLE"}, sort_keys=True).encode()
    ).hexdigest()
    obs = persist_observation(
        conn,
        collector_name="test",
        signal_type=VIDEO_TITLE,
        observed_at=_NOW,
        input_hash=h,
        external_video_id=video_id,
        signal_value_text=title,
    )
    link_job_observation(conn, job_id, obs.id)


# ---------------------------------------------------------------------------
# A — Schema v33 has market_canonical_clusters table
# ---------------------------------------------------------------------------


def test_a_schema_v33_canonical_table_exists(conn):
    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "market_canonical_clusters" in tables


# ---------------------------------------------------------------------------
# B — market_topic_clusters has canonical_cluster_id column
# ---------------------------------------------------------------------------


def test_b_topic_clusters_has_canonical_cluster_id(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(market_topic_clusters)").fetchall()}
    assert "canonical_cluster_id" in cols


# ---------------------------------------------------------------------------
# C — market_exploration_probes has market_region_label column
# ---------------------------------------------------------------------------


def test_c_probes_has_market_region_label_column(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(market_exploration_probes)").fetchall()}
    assert "market_region_label" in cols


# ---------------------------------------------------------------------------
# D — market_region_label persisted when creating probe
# ---------------------------------------------------------------------------


def test_d_market_region_label_persisted_on_probe_creation(conn):
    _make_probe_and_job(
        conn, "lost civilizations", market_region_label="Lost & Hidden Civilizations"
    )
    row = conn.execute(
        "SELECT market_region_label FROM market_exploration_probes ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row[0] == "Lost & Hidden Civilizations"


# ---------------------------------------------------------------------------
# E — Jaccard graph edge computation: strong and weak edges
# ---------------------------------------------------------------------------


def test_e_jaccard_graph_strong_and_weak_edges():
    probes = [
        {"id": 1, "normalized_query": "yoga beginners tutorial"},
        {"id": 2, "normalized_query": "yoga beginners morning"},
        {"id": 3, "normalized_query": "python programming tutorial"},
        {"id": 4, "normalized_query": "yoga tutorial basics"},
    ]
    strong, weak = _compute_jaccard_edges(probes, threshold=0.35, soft_threshold=0.15)
    strong_pairs = {(i, j) for i, j, _ in strong}
    # yoga probes share ≥2 tokens → should have strong edges among themselves
    yoga_strong = [(i, j) for i, j in strong_pairs if i in (0, 1, 3) and j in (0, 1, 3)]
    assert len(yoga_strong) >= 1, "yoga probes should be strongly linked"
    # python probe should have no strong edges to pure-yoga probes
    python_strong = [e for e in strong if e[0] == 2 or e[1] == 2]
    assert len(python_strong) == 0, "python probe should not strongly link to yoga"


# ---------------------------------------------------------------------------
# F — _groups_from_edges forms connected components
# ---------------------------------------------------------------------------


def test_f_groups_from_edges_connected_components():
    edges = [(0, 1, 0.8), (1, 2, 0.6)]
    groups = _groups_from_edges(4, edges)
    assert len(groups) == 2
    all_sizes = sorted([len(v) for v in groups.values()])
    assert all_sizes == [1, 3]


# ---------------------------------------------------------------------------
# G — _groups_from_edges with no edges returns singletons
# ---------------------------------------------------------------------------


def test_g_groups_from_edges_empty_edges():
    groups = _groups_from_edges(3, [])
    assert len(groups) == 3
    for v in groups.values():
        assert len(v) == 1


# ---------------------------------------------------------------------------
# H — validate_adjudication_output filters fabricated IDs
# ---------------------------------------------------------------------------


def test_h_validate_adjudication_removes_fabricated():
    output = ClusterAdjudicationOutput(
        final_groups=[
            ClusterAdjudicationGroup(
                member_probe_ids=[1, 2, 999],  # 999 is fabricated
                rationale="test",
                evidence_basis="test",
            )
        ]
    )
    valid_ids = {1, 2, 3}
    all_ids = [1, 2, 3]
    result = validate_adjudication_output(output, valid_ids, all_ids)
    assigned = {pid for group in result.values() for pid in group}
    assert 999 not in assigned
    assert 1 in assigned
    assert 2 in assigned


# ---------------------------------------------------------------------------
# I — validate_adjudication_output: missing probes become singletons
# ---------------------------------------------------------------------------


def test_i_validate_adjudication_missing_becomes_singleton():
    output = ClusterAdjudicationOutput(
        final_groups=[
            ClusterAdjudicationGroup(
                member_probe_ids=[1],
                rationale="test",
                evidence_basis="test",
            )
        ]
    )
    valid_ids = {1, 2, 3}
    all_ids = [1, 2, 3]
    result = validate_adjudication_output(output, valid_ids, all_ids)
    assigned = {pid for group in result.values() for pid in group}
    assert 2 in assigned
    assert 3 in assigned


# ---------------------------------------------------------------------------
# J — LLM adjudication merges semantically related low-Jaccard probes
# ---------------------------------------------------------------------------


def test_j_llm_adjudication_merges_low_jaccard_probes(conn):
    """Probes with zero Jaccard overlap are merged by LLM."""
    probe1_id, _ = _make_probe_and_job(conn, "lost civilizations")
    probe2_id, _ = _make_probe_and_job(conn, "forgotten ancient societies")

    adjudication_json = json.dumps(
        {
            "final_groups": [
                {
                    "member_probe_ids": [probe1_id, probe2_id],
                    "rationale": "Same market theme: ancient civilizations mystery",
                    "evidence_basis": "Both probe titles concern ancient human cultures",
                }
            ]
        }
    )
    fake_ai = FakeProvider(output=adjudication_json)

    result = run_market_interpretation(conn, ai_provider=fake_ai, max_llm_clusters=5)
    assert result["cluster_count"] == 1
    assert result["llm_used"] is True


# ---------------------------------------------------------------------------
# K — Without LLM, low-Jaccard probes remain separate
# ---------------------------------------------------------------------------


def test_k_without_llm_low_jaccard_probes_remain_separate(conn):
    _make_probe_and_job(conn, "lost civilizations")
    _make_probe_and_job(conn, "forgotten ancient societies")

    result = run_market_interpretation(conn, max_llm_clusters=0)
    assert result["cluster_count"] == 2
    assert result["llm_used"] is False


# ---------------------------------------------------------------------------
# L — LLM adjudication separates high-Jaccard probes with different intent
# ---------------------------------------------------------------------------


def test_l_llm_adjudication_separates_high_jaccard_probes(conn):
    """Probes that Jaccard would merge can be kept separate by LLM."""
    probe1_id, _ = _make_probe_and_job(conn, "roman mystery history")
    probe2_id, _ = _make_probe_and_job(conn, "roman mystery travel tour")

    adjudication_json = json.dumps(
        {
            "final_groups": [
                {
                    "member_probe_ids": [probe1_id],
                    "rationale": "History-focused market",
                    "evidence_basis": "Targets history enthusiasts",
                },
                {
                    "member_probe_ids": [probe2_id],
                    "rationale": "Travel/tourism market",
                    "evidence_basis": "Targets travel planners",
                },
            ]
        }
    )
    fake_ai = FakeProvider(output=adjudication_json)

    result = run_market_interpretation(conn, ai_provider=fake_ai, max_llm_clusters=5)
    assert result["cluster_count"] == 2


# ---------------------------------------------------------------------------
# M — VIDEO_TITLE observations available for probe enrichment
# ---------------------------------------------------------------------------


def test_m_video_titles_fetched_for_probe(conn):
    """get_titles_for_probe returns video_title signal observations."""
    from app.intelligence.market.interpretation_repository import get_titles_for_probe

    probe_id, job_id = _make_probe_and_job(conn, "yoga beginners")
    _add_video_title(conn, job_id, "v1", "Yoga for Complete Beginners")
    _add_video_title(conn, job_id, "v2", "Beginner Yoga Morning Flow")

    titles = get_titles_for_probe(conn, probe_id)
    assert "Yoga for Complete Beginners" in titles
    assert "Beginner Yoga Morning Flow" in titles


# ---------------------------------------------------------------------------
# N — VIDEO_TITLE evidence enters LLM prompt (via enriched probes)
# ---------------------------------------------------------------------------


def test_n_video_titles_enter_semantic_adjudication(conn):
    """Enrichment reads titles from DB; CapturingProvider confirms LLM receives them."""
    probe1_id, job1_id = _make_probe_and_job(conn, "lost civilizations")
    probe2_id, _ = _make_probe_and_job(conn, "forgotten ancient societies")

    _add_video_title(conn, job1_id, "v1", "Hidden Lost Civilizations of the World")

    received_prompts: list[str] = []

    class CapturingProvider:
        name = "capture"

        def complete(self, request):
            received_prompts.append(request.user)
            parsed = ClusterAdjudicationOutput(
                final_groups=[
                    ClusterAdjudicationGroup(
                        member_probe_ids=[probe1_id, probe2_id],
                        rationale="same theme",
                        evidence_basis="titles match",
                    )
                ]
            )
            return AIResponse(
                raw_text="",
                provider_name="capture",
                model="capture/fake",
                input_tokens=1,
                output_tokens=1,
                duration_ms=1,
                retry_count=0,
                parsed=parsed,
            )

    run_market_interpretation(conn, ai_provider=CapturingProvider(), max_llm_clusters=5)
    assert len(received_prompts) == 1
    assert "Hidden Lost Civilizations" in received_prompts[0]


# ---------------------------------------------------------------------------
# O — LLM failure falls back to Jaccard grouping safely
# ---------------------------------------------------------------------------


def test_o_llm_failure_falls_back_to_jaccard(conn):
    _make_probe_and_job(conn, "yoga beginners tutorial")
    _make_probe_and_job(conn, "yoga beginners morning")

    class FailingProvider:
        name = "fail"

        def complete(self, request):
            raise RuntimeError("Simulated LLM failure")

    result = run_market_interpretation(conn, ai_provider=FailingProvider(), max_llm_clusters=5)
    assert result["cluster_count"] >= 1
    assert result["llm_used"] is False
    assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# P — Fabricated probe IDs in LLM output are rejected
# ---------------------------------------------------------------------------


def test_p_fabricated_probe_ids_rejected(conn):
    probe1_id, _ = _make_probe_and_job(conn, "yoga beginners")
    probe2_id, _ = _make_probe_and_job(conn, "yoga basics")

    adjudication_json = json.dumps(
        {
            "final_groups": [
                {
                    "member_probe_ids": [probe1_id, probe2_id, 9999],
                    "rationale": "Same theme",
                    "evidence_basis": "Yoga queries",
                }
            ]
        }
    )
    fake_ai = FakeProvider(output=adjudication_json)

    result = run_market_interpretation(conn, ai_provider=fake_ai, max_llm_clusters=5)
    assert result["cluster_count"] == 1
    members = conn.execute(
        "SELECT probe_id FROM market_cluster_members WHERE member_type='probe_origin'"
    ).fetchall()
    assert all(m[0] != 9999 for m in members)


# ---------------------------------------------------------------------------
# Q — No LLM path produces valid deterministic output
# ---------------------------------------------------------------------------


def test_q_no_llm_deterministic_clustering(conn):
    _make_probe_and_job(conn, "yoga beginners tutorial")
    _make_probe_and_job(conn, "yoga beginners morning")
    _make_probe_and_job(conn, "python coding for beginners")

    result = run_market_interpretation(conn, max_llm_clusters=0, ai_provider=None)
    assert result["status"] == "completed"
    assert result["llm_used"] is False
    assert result["cluster_count"] >= 1


# ---------------------------------------------------------------------------
# R — Bounded LLM calls: one call per run (not per pair)
# ---------------------------------------------------------------------------


def test_r_bounded_llm_calls_one_per_run(conn):
    _make_probe_and_job(conn, "yoga beginners")
    _make_probe_and_job(conn, "yoga advanced")
    _make_probe_and_job(conn, "python coding")

    call_count = [0]

    all_probe_ids = [
        r[0]
        for r in conn.execute("SELECT id FROM market_exploration_probes ORDER BY id").fetchall()
    ]

    class CountingProvider:
        name = "counting"

        def complete(self, request):
            call_count[0] += 1
            adjudication = ClusterAdjudicationOutput(
                final_groups=[
                    ClusterAdjudicationGroup(
                        member_probe_ids=[pid],
                        rationale="singleton",
                        evidence_basis="no relation",
                    )
                    for pid in all_probe_ids
                ]
            )
            return AIResponse(
                raw_text="",
                provider_name="counting",
                model="count/1",
                input_tokens=1,
                output_tokens=1,
                duration_ms=1,
                retry_count=0,
                parsed=adjudication,
            )

    run_market_interpretation(conn, ai_provider=CountingProvider(), max_llm_clusters=10)
    assert call_count[0] == 1, "LLM should be called exactly once per interpretation run"


# ---------------------------------------------------------------------------
# S — First interpretation creates canonical cluster identity
# ---------------------------------------------------------------------------


def test_s_first_run_creates_canonical_cluster(conn):
    _make_probe_and_job(conn, "yoga beginners tutorial")

    result = run_market_interpretation(conn)
    clusters = result["clusters"]
    assert len(clusters) == 1

    cluster = clusters[0]
    assert cluster.canonical_cluster_id is not None

    canonical = get_canonical_cluster(conn, cluster.canonical_cluster_id)
    assert canonical.canonical_label is not None
    assert canonical.normalized_label is not None


# ---------------------------------------------------------------------------
# T — Second run same theme reuses canonical cluster identity
# ---------------------------------------------------------------------------


def test_t_second_run_reuses_canonical_cluster(conn):
    _make_probe_and_job(conn, "yoga beginners tutorial")

    result1 = run_market_interpretation(conn)
    canonical_id_1 = result1["clusters"][0].canonical_cluster_id

    # Add second probe with same theme; both probes remain in the DB for run 2
    _make_probe_and_job(conn, "yoga beginners morning")

    result2 = run_market_interpretation(conn)
    # yoga probes cluster together → 1 cluster reusing the same canonical
    yoga_cluster = next(
        (c for c in result2["clusters"] if "yoga" in c.normalized_label),
        None,
    )
    assert yoga_cluster is not None
    assert yoga_cluster.canonical_cluster_id == canonical_id_1


# ---------------------------------------------------------------------------
# U — Member set may change while canonical identity remains same
# ---------------------------------------------------------------------------


def test_u_member_set_change_keeps_canonical_identity(conn):
    _make_probe_and_job(conn, "yoga beginners")
    result1 = run_market_interpretation(conn)
    canonical_id_1 = result1["clusters"][0].canonical_cluster_id

    _make_probe_and_job(conn, "yoga morning routine")
    result2 = run_market_interpretation(conn)
    canonical_ids_r2 = {c.canonical_cluster_id for c in result2["clusters"]}
    assert canonical_id_1 in canonical_ids_r2


# ---------------------------------------------------------------------------
# V — Cluster snapshot IDs differ across runs
# ---------------------------------------------------------------------------


def test_v_cluster_snapshot_ids_differ_across_runs(conn):
    _make_probe_and_job(conn, "yoga beginners tutorial")
    result1 = run_market_interpretation(conn)

    _make_probe_and_job(conn, "yoga morning beginner")
    result2 = run_market_interpretation(conn)

    ids_r1 = {c.id for c in result1["clusters"]}
    ids_r2 = {c.id for c in result2["clusters"]}
    assert not ids_r1.intersection(ids_r2), "Cluster snapshot IDs must differ across runs"


# ---------------------------------------------------------------------------
# W — Signal snapshot IDs differ across runs
# ---------------------------------------------------------------------------


def test_w_signal_snapshot_ids_differ_across_runs(conn):
    _make_probe_and_job(conn, "yoga beginners tutorial")
    result1 = run_market_interpretation(conn)

    _make_probe_and_job(conn, "yoga morning beginner")
    result2 = run_market_interpretation(conn)

    ids_r1 = {s.id for s in result1["signals"]}
    ids_r2 = {s.id for s in result2["signals"]}
    assert not ids_r1.intersection(ids_r2), "Signal snapshot IDs must differ across runs"


# ---------------------------------------------------------------------------
# X — Unrelated theme gets distinct canonical identity
# ---------------------------------------------------------------------------


def test_x_unrelated_theme_gets_distinct_canonical_identity(conn):
    _make_probe_and_job(conn, "yoga beginners tutorial")
    _make_probe_and_job(conn, "python coding advanced")

    result = run_market_interpretation(conn)
    canonical_ids = {c.canonical_cluster_id for c in result["clusters"]}
    assert len(canonical_ids) == len(result["clusters"]), (
        "Each distinct market theme should have its own canonical cluster"
    )


# ---------------------------------------------------------------------------
# Y — Region difference creates distinct canonical identity
# ---------------------------------------------------------------------------


def test_y_region_difference_distinct_canonical():
    fp_us = make_canonical_cluster_fingerprint(
        normalized_queries=["yoga beginners"],
        platform="youtube",
        provider="youtube_data_api",
        region_code="US",
        language_code="en",
    )
    fp_uk = make_canonical_cluster_fingerprint(
        normalized_queries=["yoga beginners"],
        platform="youtube",
        provider="youtube_data_api",
        region_code="GB",
        language_code="en",
    )
    assert fp_us != fp_uk


# ---------------------------------------------------------------------------
# Z — Language difference creates distinct canonical identity
# ---------------------------------------------------------------------------


def test_z_language_difference_distinct_canonical():
    fp_en = make_canonical_cluster_fingerprint(
        normalized_queries=["yoga for beginners"],
        platform="youtube",
        provider="youtube_data_api",
        region_code="US",
        language_code="en",
    )
    fp_es = make_canonical_cluster_fingerprint(
        normalized_queries=["yoga for beginners"],
        platform="youtube",
        provider="youtube_data_api",
        region_code="US",
        language_code="es",
    )
    assert fp_en != fp_es


# ---------------------------------------------------------------------------
# AA — Global identity has no channel ownership
# ---------------------------------------------------------------------------


def test_aa_canonical_cluster_has_no_channel_ownership(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(market_canonical_clusters)").fetchall()}
    assert "channel_id" not in cols
    assert "workspace_id" not in cols


# ---------------------------------------------------------------------------
# AB — Canonical history query returns snapshots from multiple runs
# ---------------------------------------------------------------------------


def test_ab_canonical_signal_history_across_runs(conn):
    _make_probe_and_job(conn, "yoga beginners tutorial")
    result1 = run_market_interpretation(conn)
    canonical_id = result1["clusters"][0].canonical_cluster_id

    _make_probe_and_job(conn, "yoga morning beginner")
    run_market_interpretation(conn)

    history = get_canonical_signal_history(conn, canonical_id)
    assert len(history) >= 2


# ---------------------------------------------------------------------------
# AC — Signal history is chronological (oldest first)
# ---------------------------------------------------------------------------


def test_ac_signal_history_chronological(conn):
    _make_probe_and_job(conn, "yoga beginners")
    result1 = run_market_interpretation(conn)
    canonical_id = result1["clusters"][0].canonical_cluster_id

    _make_probe_and_job(conn, "yoga morning flow")
    run_market_interpretation(conn)

    history = get_canonical_signal_history(conn, canonical_id)
    if len(history) >= 2:
        assert history[0]["signal_id"] < history[1]["signal_id"]


# ---------------------------------------------------------------------------
# AD — Old signal snapshot preserved after new run
# ---------------------------------------------------------------------------


def test_ad_old_signal_preserved_after_new_run(conn):
    _make_probe_and_job(conn, "yoga beginners")
    result1 = run_market_interpretation(conn)
    old_signal_id = result1["signals"][0].id

    _make_probe_and_job(conn, "yoga basics morning")
    run_market_interpretation(conn)

    row = conn.execute(
        "SELECT id FROM market_cluster_signals WHERE id = ?", (old_signal_id,)
    ).fetchone()
    assert row is not None


# ---------------------------------------------------------------------------
# AE — ExternalMarketOpportunityEvidence contains canonical_cluster_id
# ---------------------------------------------------------------------------


def test_ae_opportunity_evidence_contains_canonical_id(conn):
    _make_probe_and_job(conn, "yoga beginners tutorial")
    result = run_market_interpretation(conn)

    assert len(result["opportunities"]) >= 1
    opp = result["opportunities"][0]
    assert isinstance(opp, ExternalMarketOpportunityEvidence)
    assert opp.canonical_cluster_id is not None


# ---------------------------------------------------------------------------
# AF — Phase 13F contract: canonical_cluster_id present alongside cluster_id
# ---------------------------------------------------------------------------


def test_af_phase_13f_contract_has_canonical_cluster_id(conn):
    _make_probe_and_job(conn, "yoga beginners tutorial")
    _make_probe_and_job(conn, "yoga morning routine beginners")

    result = run_market_interpretation(conn)
    for opp in result["opportunities"]:
        assert hasattr(opp, "canonical_cluster_id")
        assert hasattr(opp, "cluster_id")
        assert opp.canonical_cluster_id is not None


# ---------------------------------------------------------------------------
# AG — Policy snapshot: lexical threshold persisted
# ---------------------------------------------------------------------------


def test_ag_policy_snapshot_contains_jaccard_threshold(conn):
    _make_probe_and_job(conn, "yoga beginners")
    result = run_market_interpretation(conn, jaccard_threshold=0.40)
    run_id = result["run_id"]

    row = conn.execute(
        "SELECT policy_snapshot_json FROM market_interpretation_runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    policy = json.loads(row[0])
    assert policy["clustering"]["jaccard_threshold"] == 0.40


# ---------------------------------------------------------------------------
# AH — Policy snapshot: semantic clustering version persisted
# ---------------------------------------------------------------------------


def test_ah_policy_snapshot_contains_semantic_version(conn):
    _make_probe_and_job(conn, "yoga beginners")
    result = run_market_interpretation(conn)
    run_id = result["run_id"]

    row = conn.execute(
        "SELECT policy_snapshot_json FROM market_interpretation_runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    policy = json.loads(row[0])
    assert policy["clustering"]["semantic_version"] == SEMANTIC_CLUSTERING_VERSION


# ---------------------------------------------------------------------------
# AI — Policy snapshot: demand normalization constants persisted
# ---------------------------------------------------------------------------


def test_ai_policy_snapshot_demand_constants(conn):
    _make_probe_and_job(conn, "yoga beginners")
    result = run_market_interpretation(conn)
    run_id = result["run_id"]

    row = conn.execute(
        "SELECT policy_snapshot_json FROM market_interpretation_runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    policy = json.loads(row[0])
    assert policy["demand"]["view_log_scale"] == DEMAND_VIEW_LOG_SCALE
    assert policy["demand"]["single_video_cap"] == DEMAND_SINGLE_VIDEO_CAP


# ---------------------------------------------------------------------------
# AJ — Policy snapshot: demand weights persisted
# ---------------------------------------------------------------------------


def test_aj_policy_snapshot_demand_weights():
    snapshot = build_interpretation_policy_snapshot()
    assert snapshot["demand"]["weights"]["median_normalized"] == 0.80
    assert snapshot["demand"]["weights"]["creator_factor"] == 0.20


# ---------------------------------------------------------------------------
# AK — Policy snapshot: saturation constants/weights persisted
# ---------------------------------------------------------------------------


def test_ak_policy_snapshot_saturation():
    snapshot = build_interpretation_policy_snapshot()
    assert snapshot["saturation"]["creator_scale"] == SATURATION_CREATOR_SCALE
    assert snapshot["saturation"]["search_log_scale"] == SATURATION_SEARCH_LOG_SCALE
    assert snapshot["saturation"]["weights"]["creator"] == 0.60


# ---------------------------------------------------------------------------
# AL — Policy snapshot: freshness constants/weights persisted
# ---------------------------------------------------------------------------


def test_al_policy_snapshot_freshness():
    snapshot = build_interpretation_policy_snapshot()
    assert snapshot["freshness"]["halflife_days"] == FRESHNESS_HALFLIFE_DAYS
    assert snapshot["freshness"]["recent_threshold_days"] == FRESHNESS_RECENT_THRESHOLD_DAYS
    assert snapshot["freshness"]["weights"]["recency"] == 0.60


# ---------------------------------------------------------------------------
# AM — Policy snapshot: momentum constants/weights persisted
# ---------------------------------------------------------------------------


def test_am_policy_snapshot_momentum():
    snapshot = build_interpretation_policy_snapshot()
    assert snapshot["momentum"]["vpd_log_scale"] == MOMENTUM_VPD_LOG_SCALE
    assert snapshot["momentum"]["weights"]["median_vpd"] == 0.70
    assert snapshot["momentum"]["negative_correction_policy"] == "exclude_from_positive"


# ---------------------------------------------------------------------------
# AN — Policy snapshot: persistence/maturity/confidence persisted
# ---------------------------------------------------------------------------


def test_an_policy_snapshot_persistence_maturity_confidence():
    snapshot = build_interpretation_policy_snapshot()
    assert snapshot["persistence"]["spread_days"] == PERSISTENCE_SPREAD_DAYS
    assert snapshot["maturity"]["insufficient_video_threshold"] == 3
    assert snapshot["maturity"]["actionable_creator_minimum"] == 5
    assert snapshot["confidence"]["count_weight"] == 0.40


# ---------------------------------------------------------------------------
# AO — Default caller automatically receives complete snapshot
# ---------------------------------------------------------------------------


def test_ao_default_caller_receives_complete_snapshot(conn):
    _make_probe_and_job(conn, "yoga beginners")
    result = run_market_interpretation(conn)
    run_id = result["run_id"]

    row = conn.execute(
        "SELECT policy_snapshot_json FROM market_interpretation_runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    policy = json.loads(row[0])

    for section in (
        "clustering",
        "demand",
        "saturation",
        "freshness",
        "momentum",
        "persistence",
        "maturity",
        "confidence",
        "state_labels",
    ):
        assert section in policy, f"Missing section: {section}"


# ---------------------------------------------------------------------------
# AP — Custom override does not erase required defaults
# ---------------------------------------------------------------------------


def test_ap_custom_override_does_not_erase_defaults(conn):
    _make_probe_and_job(conn, "yoga beginners")
    result = run_market_interpretation(conn, policy_snapshot={"custom_key": "custom_value"})
    run_id = result["run_id"]

    row = conn.execute(
        "SELECT policy_snapshot_json FROM market_interpretation_runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    policy = json.loads(row[0])

    assert policy.get("custom_key") == "custom_value"
    assert "demand" in policy
    assert "freshness" in policy


# ---------------------------------------------------------------------------
# AQ — Regression: multi-video outlier protection via median demand
# ---------------------------------------------------------------------------


def test_aq_regression_multi_video_outlier_protection():
    """100M-view video among 9 average videos does not dominate demand."""
    view_counts = [100_000.0] * 9 + [100_000_000.0]
    demand_normal, _ = compute_demand_score(view_counts=view_counts, creator_count=5)

    demand_single, _ = compute_demand_score(view_counts=[100_000_000.0], creator_count=5)

    assert demand_normal is not None
    assert demand_single is not None
    assert demand_single <= DEMAND_SINGLE_VIDEO_CAP
    # Multi-video demand reflects majority (100K views median), not the 100M outlier
    assert demand_normal < 0.85, "Outlier should not inflate multi-video demand above 0.85"


# ---------------------------------------------------------------------------
# AR — Regression: multi-creator momentum aggregation
# ---------------------------------------------------------------------------


def test_ar_regression_multi_creator_momentum():
    vpd_list = [500.0, 1200.0, 800.0]
    score, components = compute_momentum_score(
        positive_vpd_list=vpd_list,
        total_velocity_count=3,
    )
    assert score is not None
    assert score > 0
    assert components["positive_count"] == 3
    assert components["positive_ratio"] == 1.0


# ---------------------------------------------------------------------------
# AS — Regression: zero velocity vs missing velocity distinction
# ---------------------------------------------------------------------------


def test_as_regression_zero_vs_missing_velocity():
    """total_velocity_count=0 → None; velocity exists but all negative → 0.0"""
    score_missing, info_missing = compute_momentum_score(
        positive_vpd_list=[], total_velocity_count=0
    )
    assert score_missing is None
    assert info_missing.get("reason") == "no_velocity_data"

    score_negative, info_negative = compute_momentum_score(
        positive_vpd_list=[], total_velocity_count=3
    )
    assert score_negative == 0.0
    assert info_negative["positive_ratio"] == 0.0


# ---------------------------------------------------------------------------
# AT — Policy snapshot auto-built even if caller passes nothing
# ---------------------------------------------------------------------------


def test_at_policy_snapshot_complete_no_caller_input():
    snapshot = build_interpretation_policy_snapshot()
    assert "demand" in snapshot
    assert "view_log_scale" in snapshot["demand"]
    assert "clustering" in snapshot
    assert "jaccard_threshold" in snapshot["clustering"]
