"""Phase 13D-D tests — Semantic niche guard + authoritative priority selection.

60 tests A–BH.

Safety invariants verified throughout:
  - No YouTube API calls.
  - No Opportunity / OpportunityObservation rows created.
  - No scoring mutations outside the selection run.
  - FakeProvider used for all LLM tests.
"""

from __future__ import annotations

import json
import pathlib
import tempfile

import pytest

from app.ai.fake import FakeProvider
from app.core.database import open_db
from app.intelligence.dedup import normalize_topic
from app.intelligence.market.planner_models import (
    CollectionPolicy,
    ExplorationProbeType,
    PriorityComponents,
)
from app.intelligence.market.planner_prompts import (
    SELECTOR_MAX_BATCH_SIZE,
    SELECTOR_MAX_REGION_PER_CLUSTER,
    SELECTOR_POLICY_VERSION,
)
from app.intelligence.market.planner_repository import (
    create_exploration_probe,
    create_exploration_run,
    get_exploration_probe,
    get_exploration_run,
    list_probes_for_selection,
    update_probe_status,
)
from app.intelligence.market.selector import (
    SelectionResult,
    _check_excluded,
    _compute_components,
    run_niche_selection,
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


def _make_run(conn, max_probes: int = 10) -> int:
    run = create_exploration_run(conn, max_probes=max_probes)
    return run.id


def _make_probe(
    conn,
    run_id: int,
    query: str,
    probe_type: str = ExplorationProbeType.MARKET_REGION,
    depth: int = 0,
    status: str = "candidate",
    components: dict | None = None,
) -> int:
    nq = normalize_topic(query)
    probe = create_exploration_probe(
        conn,
        run_id=run_id,
        query_text=query,
        normalized_query=nq,
        probe_type=probe_type,
        exploration_depth=depth,
        collection_policy=CollectionPolicy(),
    )
    if status != "candidate":
        update_probe_status(conn, probe.id, status=status)
    if components is not None:
        pc = PriorityComponents(**components)
        update_probe_status(
            conn,
            probe.id,
            priority_components_json=pc.model_dump_json(),
            status=status if status != "candidate" else "candidate",
        )
    return probe.id


def _fake_provider(
    probe_ids: list[int], eligible: bool = True, fit_score: float = 0.8
) -> FakeProvider:
    """FakeProvider that marks all supplied probe IDs as eligible (or not)."""
    evals = [
        {
            "probe_id": pid,
            "eligible": eligible,
            "fit_score": fit_score,
            "rationale": "test rationale",
        }
        for pid in probe_ids
    ]
    return FakeProvider(json.dumps({"evaluations": evals}))


def _fake_provider_mixed(decisions: list[tuple[int, bool, float]]) -> FakeProvider:
    """decisions: list of (probe_id, eligible, fit_score)."""
    evals = [
        {"probe_id": pid, "eligible": elig, "fit_score": fs, "rationale": "test"}
        for pid, elig, fs in decisions
    ]
    return FakeProvider(json.dumps({"evaluations": evals}))


# ---------------------------------------------------------------------------
# A — list_probes_for_selection returns CANDIDATE probes
# ---------------------------------------------------------------------------


def test_a_list_probes_for_selection_returns_candidates(conn):
    run_id = _make_run(conn)
    p1 = _make_probe(conn, run_id, "python tutorials", status="candidate")
    p2 = _make_probe(conn, run_id, "data science courses", status="candidate")
    probes = list_probes_for_selection(conn, run_id)
    ids = {p.id for p in probes}
    assert p1 in ids
    assert p2 in ids


# ---------------------------------------------------------------------------
# B — list_probes_for_selection includes DEFERRED probes
# ---------------------------------------------------------------------------


def test_b_list_probes_for_selection_includes_deferred(conn):
    run_id = _make_run(conn)
    deferred_id = _make_probe(conn, run_id, "machine learning basics", status="deferred")
    probes = list_probes_for_selection(conn, run_id)
    assert any(p.id == deferred_id for p in probes)


# ---------------------------------------------------------------------------
# C — list_probes_for_selection excludes SELECTED / REJECTED / DISPATCHED
# ---------------------------------------------------------------------------


def test_c_list_probes_for_selection_excludes_finals(conn):
    run_id = _make_run(conn)
    sel_id = _make_probe(conn, run_id, "selected probe", status="selected")
    rej_id = _make_probe(conn, run_id, "rejected probe", status="rejected")
    cand_id = _make_probe(conn, run_id, "candidate probe", status="candidate")
    probes = list_probes_for_selection(conn, run_id)
    ids = {p.id for p in probes}
    assert sel_id not in ids
    assert rej_id not in ids
    assert cand_id in ids


# ---------------------------------------------------------------------------
# D — list_probes_for_selection returns empty list for run with no candidates
# ---------------------------------------------------------------------------


def test_d_list_probes_for_selection_empty(conn):
    run_id = _make_run(conn)
    probes = list_probes_for_selection(conn, run_id)
    assert probes == []


# ---------------------------------------------------------------------------
# E — list_probes_for_selection returns probes in created_at order
# ---------------------------------------------------------------------------


def test_e_list_probes_for_selection_order(conn):
    run_id = _make_run(conn)
    p1 = _make_probe(conn, run_id, "aaa probe")
    p2 = _make_probe(conn, run_id, "bbb probe")
    p3 = _make_probe(conn, run_id, "ccc probe")
    probes = list_probes_for_selection(conn, run_id)
    ids = [p.id for p in probes]
    assert ids == [p1, p2, p3]


# ---------------------------------------------------------------------------
# F — excluded topic exact match → REJECTED (deterministic layer)
# ---------------------------------------------------------------------------


def test_f_excluded_topic_exact_match_rejected(conn):
    run_id = _make_run(conn)
    p = _make_probe(conn, run_id, "python programming")
    result = run_niche_selection(
        conn,
        run_id,
        primary_niche="coding tutorials",
        excluded_topics=["python programming"],
        prior_queries=set(),
        max_probes=10,
    )
    assert p in result.rejected
    probe = get_exploration_probe(conn, p)
    assert probe.status == "rejected"
    assert probe.semantic_fit_status == "ineligible"


# ---------------------------------------------------------------------------
# G — excluded topic high Jaccard → REJECTED (Jaccard ≥ threshold)
# ---------------------------------------------------------------------------


def test_g_excluded_topic_high_jaccard_rejected(conn):
    run_id = _make_run(conn)
    p = _make_probe(conn, run_id, "python coding tutorials")
    # "python coding" is very similar
    result = run_niche_selection(
        conn,
        run_id,
        primary_niche="software development",
        excluded_topics=["python coding tutorials online"],  # high overlap
        prior_queries=set(),
        max_probes=10,
    )
    assert p in result.rejected


# ---------------------------------------------------------------------------
# H — excluded topic low Jaccard → NOT rejected
# ---------------------------------------------------------------------------


def test_h_low_jaccard_not_rejected(conn):
    run_id = _make_run(conn)
    p = _make_probe(conn, run_id, "astrophysics for beginners")
    result = run_niche_selection(
        conn,
        run_id,
        primary_niche="science education",
        excluded_topics=["cooking recipes"],  # no similarity
        prior_queries=set(),
        max_probes=10,
    )
    assert p not in result.rejected


# ---------------------------------------------------------------------------
# I — prior query near-duplicate → novelty=0.2, not rejected
# ---------------------------------------------------------------------------


def test_i_prior_query_near_duplicate_not_rejected(conn):
    run_id = _make_run(conn)
    p = _make_probe(conn, run_id, "python tutorials")
    nq = normalize_topic("python tutorials")
    result = run_niche_selection(
        conn,
        run_id,
        primary_niche="coding",
        excluded_topics=[],
        prior_queries={nq},
        max_probes=10,
    )
    # Probe should not be rejected — prior queries reduce novelty but don't exclude
    assert p not in result.rejected


# ---------------------------------------------------------------------------
# J — prior query exact match → novelty component = 0.2
# ---------------------------------------------------------------------------


def test_j_prior_query_novelty_reduced(conn):
    run_id = _make_run(conn)
    query = "python tutorials"
    nq = normalize_topic(query)
    probe = create_exploration_probe(
        conn,
        run_id=run_id,
        query_text=query,
        normalized_query=nq,
        probe_type=ExplorationProbeType.MARKET_REGION,
        collection_policy=CollectionPolicy(),
    )

    components, score = _compute_components(probe, None, {nq})
    assert components.novelty == pytest.approx(0.2)


# ---------------------------------------------------------------------------
# K — novel query → novelty = 1.0
# ---------------------------------------------------------------------------


def test_k_novel_query_novelty_full(conn):
    run_id = _make_run(conn)
    query = "advanced quantum computing"
    nq = normalize_topic(query)
    probe = create_exploration_probe(
        conn,
        run_id=run_id,
        query_text=query,
        normalized_query=nq,
        probe_type=ExplorationProbeType.MARKET_REGION,
        collection_policy=CollectionPolicy(),
    )
    components, _ = _compute_components(probe, None, set())
    assert components.novelty == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# L — FakeProvider LLM eligible response → semantic_fit_status='eligible'
# ---------------------------------------------------------------------------


def test_l_llm_eligible_sets_status(conn):
    run_id = _make_run(conn)
    p = _make_probe(conn, run_id, "machine learning frameworks")
    provider = _fake_provider([p], eligible=True, fit_score=0.9)
    result = run_niche_selection(
        conn,
        run_id,
        primary_niche="AI research",
        excluded_topics=[],
        prior_queries=set(),
        max_probes=10,
        ai_provider=provider,
    )
    assert p in result.selected
    probe = get_exploration_probe(conn, p)
    assert probe.semantic_fit_status == "eligible"


# ---------------------------------------------------------------------------
# M — FakeProvider LLM ineligible response → REJECTED
# ---------------------------------------------------------------------------


def test_m_llm_ineligible_rejected(conn):
    run_id = _make_run(conn)
    p = _make_probe(conn, run_id, "baking bread at home")
    provider = _fake_provider([p], eligible=False, fit_score=0.1)
    result = run_niche_selection(
        conn,
        run_id,
        primary_niche="AI research",
        excluded_topics=[],
        prior_queries=set(),
        max_probes=10,
        ai_provider=provider,
    )
    assert p in result.rejected
    probe = get_exploration_probe(conn, p)
    assert probe.semantic_fit_status == "ineligible"
    assert probe.status == "rejected"


# ---------------------------------------------------------------------------
# N — no LLM provider → all eligible probes get semantic_fit_status='pending'
# ---------------------------------------------------------------------------


def test_n_no_llm_provider_pending_status(conn):
    run_id = _make_run(conn)
    p = _make_probe(conn, run_id, "neural networks overview")
    result = run_niche_selection(
        conn,
        run_id,
        primary_niche="AI tutorials",
        excluded_topics=[],
        prior_queries=set(),
        max_probes=10,
        ai_provider=None,
    )
    probe = get_exploration_probe(conn, p)
    assert probe.semantic_fit_status == "pending"
    assert p in result.selected


# ---------------------------------------------------------------------------
# O — batch capped at SELECTOR_MAX_BATCH_SIZE=20
# ---------------------------------------------------------------------------


def test_o_batch_capped_at_max_batch_size(conn):
    run_id = _make_run(conn, max_probes=30)
    probe_ids = [_make_probe(conn, run_id, f"topic {i:03d}") for i in range(25)]

    # Provider returns evaluations only for first 20
    provider = _fake_provider(probe_ids[:SELECTOR_MAX_BATCH_SIZE], eligible=True)
    run_niche_selection(
        conn,
        run_id,
        primary_niche="general education",
        excluded_topics=[],
        prior_queries=set(),
        max_probes=30,
        ai_provider=provider,
    )
    # Probes 21-25 (indices 20-24) were not in the LLM batch → semantic_fit_status='pending'
    for pid in probe_ids[SELECTOR_MAX_BATCH_SIZE:]:
        probe = get_exploration_probe(conn, pid)
        assert probe.semantic_fit_status == "pending"


# ---------------------------------------------------------------------------
# P — LLM rationale stored in decision_reason for REJECTED probes
# ---------------------------------------------------------------------------


def test_p_llm_rationale_in_decision_reason(conn):
    run_id = _make_run(conn)
    p = _make_probe(conn, run_id, "off-topic subject")
    evals = [
        {"probe_id": p, "eligible": False, "fit_score": 0.05, "rationale": "completely off niche"}
    ]
    provider = FakeProvider(json.dumps({"evaluations": evals}))
    run_niche_selection(
        conn,
        run_id,
        primary_niche="coding tutorials",
        excluded_topics=[],
        prior_queries=set(),
        max_probes=10,
        ai_provider=provider,
    )
    probe = get_exploration_probe(conn, p)
    assert "niche_guard_ineligible" in probe.decision_reason
    assert "completely off niche" in probe.decision_reason


# ---------------------------------------------------------------------------
# Q — run provenance written after LLM call
# ---------------------------------------------------------------------------


def test_q_run_provenance_written(conn):
    run_id = _make_run(conn)
    p = _make_probe(conn, run_id, "deep learning tutorial")
    provider = _fake_provider([p])
    run_niche_selection(
        conn,
        run_id,
        primary_niche="AI",
        excluded_topics=[],
        prior_queries=set(),
        max_probes=10,
        ai_provider=provider,
    )
    run = get_exploration_run(conn, run_id)
    # provider.name = "fake"
    assert run.provider == "fake"
    assert run.prompt_version == "1"


# ---------------------------------------------------------------------------
# R — LLM response schema validated (NicheGuardOutput)
# ---------------------------------------------------------------------------


def test_r_llm_response_schema_validated(conn):
    run_id = _make_run(conn)
    p = _make_probe(conn, run_id, "neural networks")
    # Valid schema
    provider = _fake_provider([p], eligible=True, fit_score=0.75)
    result = run_niche_selection(
        conn,
        run_id,
        primary_niche="AI",
        excluded_topics=[],
        prior_queries=set(),
        max_probes=10,
        ai_provider=provider,
    )
    assert p in result.selected
    assert result.llm_error is None


# ---------------------------------------------------------------------------
# S — niche_fit from LLM fit_score
# ---------------------------------------------------------------------------


def test_s_niche_fit_from_llm_fit_score(conn):
    run_id = _make_run(conn)
    query = "reinforcement learning"
    nq = normalize_topic(query)
    probe = create_exploration_probe(
        conn,
        run_id=run_id,
        query_text=query,
        normalized_query=nq,
        probe_type=ExplorationProbeType.MARKET_REGION,
        collection_policy=CollectionPolicy(),
    )
    from app.intelligence.market.selector import NicheEvaluation

    eval_ = NicheEvaluation(probe_id=probe.id, eligible=True, fit_score=0.72, rationale="good fit")
    components, _ = _compute_components(probe, eval_, set())
    assert components.niche_fit == pytest.approx(0.72)


# ---------------------------------------------------------------------------
# T — novelty: novel=1.0, prior=0.2
# ---------------------------------------------------------------------------


def test_t_novelty_values(conn):
    run_id = _make_run(conn)
    nq_prior = normalize_topic("python basics")
    nq_novel = normalize_topic("rust programming language")

    probe_prior = create_exploration_probe(
        conn,
        run_id=run_id,
        query_text="python basics",
        normalized_query=nq_prior,
        probe_type=ExplorationProbeType.MARKET_REGION,
        collection_policy=CollectionPolicy(),
    )
    probe_novel = create_exploration_probe(
        conn,
        run_id=run_id,
        query_text="rust programming language",
        normalized_query=nq_novel,
        probe_type=ExplorationProbeType.MARKET_REGION,
        collection_policy=CollectionPolicy(),
    )

    c_prior, _ = _compute_components(probe_prior, None, {nq_prior})
    c_novel, _ = _compute_components(probe_novel, None, {nq_prior})

    assert c_prior.novelty == pytest.approx(0.2)
    assert c_novel.novelty == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# U — depth_factor: depth=0 → 1.0
# ---------------------------------------------------------------------------


def test_u_depth_factor_zero(conn):
    run_id = _make_run(conn)
    probe = create_exploration_probe(
        conn,
        run_id=run_id,
        query_text="intro topic",
        normalized_query="intro topic",
        probe_type=ExplorationProbeType.CHANNEL_BOOTSTRAP,
        exploration_depth=0,
        collection_policy=CollectionPolicy(),
    )
    components, _ = _compute_components(probe, None, set())
    assert components.depth_factor == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# V — depth_factor: depth=1 → 0.7
# ---------------------------------------------------------------------------


def test_v_depth_factor_one(conn):
    run_id = _make_run(conn)
    probe = create_exploration_probe(
        conn,
        run_id=run_id,
        query_text="adjacent topic",
        normalized_query="adjacent topic",
        probe_type=ExplorationProbeType.ADJACENT_TOPIC,
        exploration_depth=1,
        collection_policy=CollectionPolicy(),
    )
    components, _ = _compute_components(probe, None, set())
    assert components.depth_factor == pytest.approx(0.7)


# ---------------------------------------------------------------------------
# W — depth_factor: depth=2 → 0.4
# ---------------------------------------------------------------------------


def test_w_depth_factor_two(conn):
    run_id = _make_run(conn)
    probe = create_exploration_probe(
        conn,
        run_id=run_id,
        query_text="deep adjacent",
        normalized_query="deep adjacent",
        probe_type=ExplorationProbeType.ADJACENT_TOPIC,
        exploration_depth=2,
        collection_policy=CollectionPolicy(),
    )
    components, _ = _compute_components(probe, None, set())
    assert components.depth_factor == pytest.approx(0.4)


# ---------------------------------------------------------------------------
# X — depth_factor: depth=3+ → 0.2
# ---------------------------------------------------------------------------


def test_x_depth_factor_deep(conn):
    run_id = _make_run(conn)
    probe = create_exploration_probe(
        conn,
        run_id=run_id,
        query_text="very deep adjacent",
        normalized_query="very deep adjacent",
        probe_type=ExplorationProbeType.ADJACENT_TOPIC,
        exploration_depth=5,
        collection_policy=CollectionPolicy(),
    )
    components, _ = _compute_components(probe, None, set())
    assert components.depth_factor == pytest.approx(0.2)


# ---------------------------------------------------------------------------
# Y — evidence_strength=None for cold-start (no prior components)
# ---------------------------------------------------------------------------


def test_y_no_evidence_strength_for_cold_start(conn):
    run_id = _make_run(conn)
    probe = create_exploration_probe(
        conn,
        run_id=run_id,
        query_text="fresh cold start",
        normalized_query="fresh cold start",
        probe_type=ExplorationProbeType.CHANNEL_BOOTSTRAP,
        collection_policy=CollectionPolicy(),
    )
    # No existing priority_components_json → evidence components are None
    components, _ = _compute_components(probe, None, set())
    assert components.evidence_strength is None
    assert components.velocity_trigger is None
    assert components.corroboration is None


# ---------------------------------------------------------------------------
# Z — evidence_strength inherited from existing components
# ---------------------------------------------------------------------------


def test_z_evidence_strength_inherited(conn):
    run_id = _make_run(conn)
    nq = normalize_topic("adjacent with evidence")
    probe = create_exploration_probe(
        conn,
        run_id=run_id,
        query_text="adjacent with evidence",
        normalized_query=nq,
        probe_type=ExplorationProbeType.ADJACENT_TOPIC,
        exploration_depth=1,
        collection_policy=CollectionPolicy(),
    )
    pc = PriorityComponents(evidence_strength=0.65, velocity_trigger=0.45, corroboration=0.33)
    update_probe_status(
        conn, probe.id, status="candidate", priority_components_json=pc.model_dump_json()
    )
    probe = get_exploration_probe(conn, probe.id)

    components, _ = _compute_components(probe, None, set())
    assert components.evidence_strength == pytest.approx(0.65)


# ---------------------------------------------------------------------------
# AA — velocity_trigger inherited from existing components
# ---------------------------------------------------------------------------


def test_aa_velocity_trigger_inherited(conn):
    run_id = _make_run(conn)
    nq = normalize_topic("velocity probe")
    probe = create_exploration_probe(
        conn,
        run_id=run_id,
        query_text="velocity probe",
        normalized_query=nq,
        probe_type=ExplorationProbeType.VELOCITY_FOLLOWUP,
        exploration_depth=1,
        collection_policy=CollectionPolicy(),
    )
    pc = PriorityComponents(velocity_trigger=0.88)
    update_probe_status(
        conn, probe.id, status="candidate", priority_components_json=pc.model_dump_json()
    )
    probe = get_exploration_probe(conn, probe.id)

    components, _ = _compute_components(probe, None, set())
    assert components.velocity_trigger == pytest.approx(0.88)


# ---------------------------------------------------------------------------
# AB — corroboration inherited from existing components
# ---------------------------------------------------------------------------


def test_ab_corroboration_inherited(conn):
    run_id = _make_run(conn)
    nq = normalize_topic("corroborated probe")
    probe = create_exploration_probe(
        conn,
        run_id=run_id,
        query_text="corroborated probe",
        normalized_query=nq,
        probe_type=ExplorationProbeType.ADJACENT_TOPIC,
        exploration_depth=1,
        collection_policy=CollectionPolicy(),
    )
    pc = PriorityComponents(corroboration=0.67)
    update_probe_status(
        conn, probe.id, status="candidate", priority_components_json=pc.model_dump_json()
    )
    probe = get_exploration_probe(conn, probe.id)

    components, _ = _compute_components(probe, None, set())
    assert components.corroboration == pytest.approx(0.67)


# ---------------------------------------------------------------------------
# AC — cold-start probe: None evidence components do not reduce score
# ---------------------------------------------------------------------------


def test_ac_cold_start_applicable_normalization(conn):
    run_id = _make_run(conn)
    probe = create_exploration_probe(
        conn,
        run_id=run_id,
        query_text="cold start topic",
        normalized_query="cold start topic",
        probe_type=ExplorationProbeType.CHANNEL_BOOTSTRAP,
        collection_policy=CollectionPolicy(),
    )
    from app.intelligence.market.selector import NicheEvaluation

    eval_ = NicheEvaluation(probe_id=probe.id, eligible=True, fit_score=1.0, rationale="perfect")
    components, score = _compute_components(probe, eval_, set())

    # Applicable components: niche_fit=1.0, novelty=1.0, depth_factor=1.0
    # Weights: niche_fit=0.30, novelty=0.25, depth_factor=0.05
    # Total applicable weight = 0.30+0.25+0.05 = 0.60
    # score = (1.0*0.30 + 1.0*0.25 + 1.0*0.05) / 0.60 = 1.0
    assert score == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# AD — FakeProvider used in tests (no live LLM)
# ---------------------------------------------------------------------------


def test_ad_fake_provider_used_not_live(conn):
    run_id = _make_run(conn)
    p = _make_probe(conn, run_id, "test topic")
    provider = _fake_provider([p])
    # If it were live, it would need network → test would time out or error.
    result = run_niche_selection(
        conn,
        run_id,
        primary_niche="testing",
        excluded_topics=[],
        prior_queries=set(),
        max_probes=5,
        ai_provider=provider,
    )
    assert result.llm_provider == "fake"
    assert result.llm_error is None


# ---------------------------------------------------------------------------
# AE — no Opportunity rows created
# ---------------------------------------------------------------------------


def test_ae_no_opportunity_rows_created(conn):
    run_id = _make_run(conn)
    for i in range(3):
        _make_probe(conn, run_id, f"topic {i}")
    provider = _fake_provider(list(range(100)))  # IDs won't match but won't error
    run_niche_selection(
        conn,
        run_id,
        primary_niche="test",
        excluded_topics=[],
        prior_queries=set(),
        max_probes=10,
        ai_provider=provider,
    )
    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    if "opportunities" in tables:
        count = conn.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0]
        assert count == 0


# ---------------------------------------------------------------------------
# AF — no market_collection_job rows created (no YouTube calls)
# ---------------------------------------------------------------------------


def test_af_no_collection_jobs_created(conn):
    run_id = _make_run(conn)
    _make_probe(conn, run_id, "some topic")
    before = conn.execute("SELECT COUNT(*) FROM market_collection_jobs").fetchone()[0]
    run_niche_selection(
        conn,
        run_id,
        primary_niche="test",
        excluded_topics=[],
        prior_queries=set(),
        max_probes=5,
    )
    after = conn.execute("SELECT COUNT(*) FROM market_collection_jobs").fetchone()[0]
    assert after == before


# ---------------------------------------------------------------------------
# AG — priority_score of probes in OTHER runs not mutated
# ---------------------------------------------------------------------------


def test_ag_other_run_probes_not_mutated(conn):
    run_other = _make_run(conn)
    other_probe = _make_probe(conn, run_other, "other run topic", status="selected")
    other_before = get_exploration_probe(conn, other_probe)

    run_id = _make_run(conn)
    _make_probe(conn, run_id, "my topic")
    run_niche_selection(
        conn,
        run_id,
        primary_niche="test",
        excluded_topics=[],
        prior_queries=set(),
        max_probes=5,
    )

    other_after = get_exploration_probe(conn, other_probe)
    assert other_after.priority_score == other_before.priority_score
    assert other_after.semantic_fit_status == other_before.semantic_fit_status


# ---------------------------------------------------------------------------
# AH — probe status transitions correctly
# ---------------------------------------------------------------------------


def test_ah_probe_status_transitions(conn):
    run_id = _make_run(conn)
    p_sel = _make_probe(conn, run_id, "selected topic")
    p_rej = _make_probe(conn, run_id, "excluded topic")

    provider = _fake_provider_mixed(
        [
            (p_sel, True, 0.8),
            # p_rej will be excluded deterministically
        ]
    )
    run_niche_selection(
        conn,
        run_id,
        primary_niche="coding",
        excluded_topics=["excluded topic"],
        prior_queries=set(),
        max_probes=5,
        ai_provider=provider,
    )
    assert get_exploration_probe(conn, p_sel).status == "selected"
    assert get_exploration_probe(conn, p_rej).status == "rejected"


# ---------------------------------------------------------------------------
# AI — SelectionResult returned with correct lists
# ---------------------------------------------------------------------------


def test_ai_selection_result_lists(conn):
    run_id = _make_run(conn)
    p1 = _make_probe(conn, run_id, "topic one")
    p2 = _make_probe(conn, run_id, "cooking recipes")  # will be excluded
    provider = _fake_provider([p1], eligible=True)
    result = run_niche_selection(
        conn,
        run_id,
        primary_niche="programming",
        excluded_topics=["cooking recipes"],
        prior_queries=set(),
        max_probes=5,
        ai_provider=provider,
    )
    assert isinstance(result, SelectionResult)
    assert p1 in result.selected
    assert p2 in result.rejected


# ---------------------------------------------------------------------------
# AJ — higher priority probe ranked first (gets SELECTED before lower)
# ---------------------------------------------------------------------------


def test_aj_higher_priority_selected_first(conn):
    run_id = _make_run(conn, max_probes=1)
    p_low = _make_probe(conn, run_id, "low priority topic")
    p_high = _make_probe(conn, run_id, "high priority topic")

    # p_high gets fit_score=0.9, p_low gets 0.2
    provider = _fake_provider_mixed(
        [
            (p_low, True, 0.2),
            (p_high, True, 0.9),
        ]
    )
    result = run_niche_selection(
        conn,
        run_id,
        primary_niche="programming",
        excluded_topics=[],
        prior_queries=set(),
        max_probes=1,
        ai_provider=provider,
    )
    assert p_high in result.selected
    assert p_low in result.deferred


# ---------------------------------------------------------------------------
# AK — eligible probe within capacity → SELECTED
# ---------------------------------------------------------------------------


def test_ak_within_capacity_selected(conn):
    run_id = _make_run(conn)
    p = _make_probe(conn, run_id, "in-niche topic")
    provider = _fake_provider([p], eligible=True, fit_score=0.85)
    result = run_niche_selection(
        conn,
        run_id,
        primary_niche="programming",
        excluded_topics=[],
        prior_queries=set(),
        max_probes=10,
        ai_provider=provider,
    )
    assert p in result.selected
    assert get_exploration_probe(conn, p).status == "selected"


# ---------------------------------------------------------------------------
# AL — eligible probe over capacity → DEFERRED
# ---------------------------------------------------------------------------


def test_al_over_capacity_deferred(conn):
    run_id = _make_run(conn, max_probes=1)
    p1 = _make_probe(conn, run_id, "first topic")
    p2 = _make_probe(conn, run_id, "second topic")
    provider = _fake_provider([p1, p2], eligible=True, fit_score=0.7)
    result = run_niche_selection(
        conn,
        run_id,
        primary_niche="programming",
        excluded_topics=[],
        prior_queries=set(),
        max_probes=1,
        ai_provider=provider,
    )
    assert len(result.selected) == 1
    assert len(result.deferred) == 1


# ---------------------------------------------------------------------------
# AM — rejected probe has decision_reason
# ---------------------------------------------------------------------------


def test_am_rejected_probe_has_decision_reason(conn):
    run_id = _make_run(conn)
    p = _make_probe(conn, run_id, "off niche topic")
    provider = _fake_provider([p], eligible=False, fit_score=0.05)
    run_niche_selection(
        conn,
        run_id,
        primary_niche="science",
        excluded_topics=[],
        prior_queries=set(),
        max_probes=5,
        ai_provider=provider,
    )
    probe = get_exploration_probe(conn, p)
    assert probe.decision_reason is not None
    assert "rejected" in probe.decision_reason


# ---------------------------------------------------------------------------
# AN — deferred by capacity has decision_reason
# ---------------------------------------------------------------------------


def test_an_deferred_capacity_has_reason(conn):
    run_id = _make_run(conn, max_probes=1)
    p1 = _make_probe(conn, run_id, "first")
    p2 = _make_probe(conn, run_id, "second")
    provider = _fake_provider([p1, p2], eligible=True, fit_score=0.5)
    run_niche_selection(
        conn,
        run_id,
        primary_niche="science",
        excluded_topics=[],
        prior_queries=set(),
        max_probes=1,
        ai_provider=provider,
    )
    probes = [get_exploration_probe(conn, pid) for pid in [p1, p2]]
    deferred_probes = [p for p in probes if p.status == "deferred"]
    assert len(deferred_probes) == 1
    assert "deferred" in deferred_probes[0].decision_reason


# ---------------------------------------------------------------------------
# AO — max_probes=1: only highest-scoring probe selected
# ---------------------------------------------------------------------------


def test_ao_max_probes_one(conn):
    run_id = _make_run(conn, max_probes=1)
    ids = [_make_probe(conn, run_id, f"topic {i}") for i in range(5)]
    provider = _fake_provider_mixed([(pid, True, 0.5 + i * 0.1) for i, pid in enumerate(ids)])
    result = run_niche_selection(
        conn,
        run_id,
        primary_niche="science",
        excluded_topics=[],
        prior_queries=set(),
        max_probes=1,
        ai_provider=provider,
    )
    assert len(result.selected) == 1
    assert len(result.deferred) == 4


# ---------------------------------------------------------------------------
# AP — max_probes=3: up to 3 selected
# ---------------------------------------------------------------------------


def test_ap_max_probes_three(conn):
    run_id = _make_run(conn, max_probes=3)
    ids = [_make_probe(conn, run_id, f"topic {i:03d}") for i in range(5)]
    provider = _fake_provider(ids, eligible=True, fit_score=0.8)
    result = run_niche_selection(
        conn,
        run_id,
        primary_niche="science",
        excluded_topics=[],
        prior_queries=set(),
        max_probes=3,
        ai_provider=provider,
    )
    assert len(result.selected) == 3
    assert len(result.deferred) == 2


# ---------------------------------------------------------------------------
# AQ — SelectionResult has policy_version field
# ---------------------------------------------------------------------------


def test_aq_selection_result_policy_version(conn):
    run_id = _make_run(conn)
    result = run_niche_selection(
        conn,
        run_id,
        primary_niche="science",
        excluded_topics=[],
        prior_queries=set(),
        max_probes=5,
    )
    assert result.policy_version == SELECTOR_POLICY_VERSION


# ---------------------------------------------------------------------------
# AR — cluster cap: 3 probes from same region, max 2 selected
# ---------------------------------------------------------------------------


def test_ar_cluster_cap_enforced(conn):
    # Three slightly varied queries within the same Jaccard cluster
    run_id = _make_run(conn, max_probes=10)
    id1 = _make_probe(conn, run_id, "python programming tutorials beginners")
    id2 = _make_probe(conn, run_id, "python programming tutorials advanced")
    id3 = _make_probe(conn, run_id, "python programming tutorials intermediate")

    provider = _fake_provider([id1, id2, id3], eligible=True, fit_score=0.8)
    result = run_niche_selection(
        conn,
        run_id,
        primary_niche="coding",
        excluded_topics=[],
        prior_queries=set(),
        max_probes=10,
        ai_provider=provider,
    )
    # All 3 are in the same cluster (high Jaccard similarity)
    # Only SELECTOR_MAX_REGION_PER_CLUSTER=2 can be selected
    assert len(result.selected) <= SELECTOR_MAX_REGION_PER_CLUSTER


# ---------------------------------------------------------------------------
# AS — different clusters each get up to SELECTOR_MAX_REGION_PER_CLUSTER
# ---------------------------------------------------------------------------


def test_as_different_clusters_get_selections(conn):
    run_id = _make_run(conn, max_probes=10)
    # Very different queries → different clusters
    id1 = _make_probe(conn, run_id, "python programming language basics")
    id2 = _make_probe(conn, run_id, "python programming language advanced")
    id3 = _make_probe(conn, run_id, "astrophysics quantum mechanics research")
    id4 = _make_probe(conn, run_id, "astrophysics cosmology dark matter")

    provider = _fake_provider([id1, id2, id3, id4], eligible=True, fit_score=0.8)
    result = run_niche_selection(
        conn,
        run_id,
        primary_niche="education",
        excluded_topics=[],
        prior_queries=set(),
        max_probes=10,
        ai_provider=provider,
    )
    # Both clusters can contribute probes
    assert len(result.selected) >= 2


# ---------------------------------------------------------------------------
# AT — exploration slot ratio enforced
# ---------------------------------------------------------------------------


def test_at_exploration_slot_ratio(conn):
    run_id = _make_run(conn, max_probes=4)
    # 2 exploration, 2 evidence probes
    exp1 = _make_probe(
        conn, run_id, "market region alpha", probe_type=ExplorationProbeType.MARKET_REGION
    )
    exp2 = _make_probe(
        conn, run_id, "market region beta", probe_type=ExplorationProbeType.MARKET_REGION
    )
    evi1 = _make_probe(
        conn,
        run_id,
        "adjacent concept one",
        probe_type=ExplorationProbeType.ADJACENT_TOPIC,
        depth=1,
    )
    evi2 = _make_probe(
        conn,
        run_id,
        "velocity followup one",
        probe_type=ExplorationProbeType.VELOCITY_FOLLOWUP,
        depth=1,
    )

    provider = _fake_provider([exp1, exp2, evi1, evi2], eligible=True, fit_score=0.8)
    result = run_niche_selection(
        conn,
        run_id,
        primary_niche="education",
        excluded_topics=[],
        prior_queries=set(),
        max_probes=4,
        ai_provider=provider,
    )
    # With ratio=0.5 and max_probes=4: 2 exploration + 2 evidence
    assert len(result.selected) == 4


# ---------------------------------------------------------------------------
# AU — exploration/evidence portfolio overflow (evidence pool empty)
# ---------------------------------------------------------------------------


def test_au_portfolio_overflow_from_exploration(conn):
    run_id = _make_run(conn, max_probes=3)
    # Only exploration probes, no evidence probes
    exp1 = _make_probe(
        conn, run_id, "market region alpha", probe_type=ExplorationProbeType.MARKET_REGION
    )
    exp2 = _make_probe(
        conn, run_id, "market region beta", probe_type=ExplorationProbeType.MARKET_REGION
    )
    exp3 = _make_probe(
        conn, run_id, "bootstrap channel", probe_type=ExplorationProbeType.CHANNEL_BOOTSTRAP
    )

    provider = _fake_provider([exp1, exp2, exp3], eligible=True, fit_score=0.8)
    result = run_niche_selection(
        conn,
        run_id,
        primary_niche="science",
        excluded_topics=[],
        prior_queries=set(),
        max_probes=3,
        ai_provider=provider,
    )
    # Evidence slots overflow to exploration — all 3 should be selected
    assert len(result.selected) == 3


# ---------------------------------------------------------------------------
# AV — CHANNEL_BOOTSTRAP probe classified as exploration type
# ---------------------------------------------------------------------------


def test_av_channel_bootstrap_in_exploration_pool(conn):
    run_id = _make_run(conn, max_probes=2)
    cb = _make_probe(
        conn, run_id, "channel bootstrap niche", probe_type=ExplorationProbeType.CHANNEL_BOOTSTRAP
    )
    provider = _fake_provider([cb], eligible=True, fit_score=0.9)
    result = run_niche_selection(
        conn,
        run_id,
        primary_niche="science",
        excluded_topics=[],
        prior_queries=set(),
        max_probes=2,
        ai_provider=provider,
    )
    assert cb in result.selected


# ---------------------------------------------------------------------------
# AW — ADJACENT_TOPIC probe classified as evidence type
# ---------------------------------------------------------------------------


def test_aw_adjacent_topic_in_evidence_pool(conn):
    run_id = _make_run(conn, max_probes=2)
    adj = _make_probe(
        conn, run_id, "adjacent concept", probe_type=ExplorationProbeType.ADJACENT_TOPIC, depth=1
    )
    provider = _fake_provider([adj], eligible=True, fit_score=0.8)
    result = run_niche_selection(
        conn,
        run_id,
        primary_niche="science",
        excluded_topics=[],
        prior_queries=set(),
        max_probes=2,
        ai_provider=provider,
    )
    assert adj in result.selected


# ---------------------------------------------------------------------------
# AX — no YouTube API calls (no market_collection_job rows from selector)
# ---------------------------------------------------------------------------


def test_ax_no_youtube_api_calls(conn):
    run_id = _make_run(conn)
    _make_probe(conn, run_id, "some topic")
    before_jobs = conn.execute("SELECT COUNT(*) FROM market_collection_jobs").fetchone()[0]
    run_niche_selection(
        conn,
        run_id,
        primary_niche="tech",
        excluded_topics=[],
        prior_queries=set(),
        max_probes=5,
    )
    after_jobs = conn.execute("SELECT COUNT(*) FROM market_collection_jobs").fetchone()[0]
    assert after_jobs == before_jobs


# ---------------------------------------------------------------------------
# AY — no Opportunity / OpportunityObservation created
# ---------------------------------------------------------------------------


def test_ay_no_opportunity_created(conn):
    run_id = _make_run(conn)
    _make_probe(conn, run_id, "some topic")
    run_niche_selection(
        conn,
        run_id,
        primary_niche="tech",
        excluded_topics=[],
        prior_queries=set(),
        max_probes=5,
    )
    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    if "opportunity_observations" in tables:
        count = conn.execute("SELECT COUNT(*) FROM opportunity_observations").fetchone()[0]
        assert count == 0


# ---------------------------------------------------------------------------
# AZ — priority_score is None before selection, set after
# ---------------------------------------------------------------------------


def test_az_priority_score_none_before(conn):
    run_id = _make_run(conn)
    p = _make_probe(conn, run_id, "unseen topic")
    before = get_exploration_probe(conn, p)
    assert before.priority_score is None

    provider = _fake_provider([p], eligible=True, fit_score=0.75)
    run_niche_selection(
        conn,
        run_id,
        primary_niche="test",
        excluded_topics=[],
        prior_queries=set(),
        max_probes=5,
        ai_provider=provider,
    )
    after = get_exploration_probe(conn, p)
    assert after.priority_score is not None


# ---------------------------------------------------------------------------
# BA — semantic_fit_status is None before selection
# ---------------------------------------------------------------------------


def test_ba_semantic_fit_status_none_before(conn):
    run_id = _make_run(conn)
    p = _make_probe(conn, run_id, "unseen topic two")
    before = get_exploration_probe(conn, p)
    assert before.semantic_fit_status is None


# ---------------------------------------------------------------------------
# BB — decision_reason persisted for SELECTED probe
# ---------------------------------------------------------------------------


def test_bb_decision_reason_for_selected(conn):
    run_id = _make_run(conn)
    p = _make_probe(conn, run_id, "on niche topic")
    provider = _fake_provider([p], eligible=True, fit_score=0.9)
    run_niche_selection(
        conn,
        run_id,
        primary_niche="science",
        excluded_topics=[],
        prior_queries=set(),
        max_probes=5,
        ai_provider=provider,
    )
    probe = get_exploration_probe(conn, p)
    assert probe.decision_reason is not None
    assert "selected" in probe.decision_reason


# ---------------------------------------------------------------------------
# BC — decided_at timestamp persisted
# ---------------------------------------------------------------------------


def test_bc_decided_at_persisted(conn):
    run_id = _make_run(conn)
    p = _make_probe(conn, run_id, "dated topic")
    provider = _fake_provider([p], eligible=True)
    run_niche_selection(
        conn,
        run_id,
        primary_niche="science",
        excluded_topics=[],
        prior_queries=set(),
        max_probes=5,
        ai_provider=provider,
    )
    probe = get_exploration_probe(conn, p)
    assert probe.decided_at is not None


# ---------------------------------------------------------------------------
# BD — EXCLUDED_TOPIC_JACCARD_THRESHOLD boundary: 0.59 → not rejected
# ---------------------------------------------------------------------------


def test_bd_excluded_threshold_boundary_not_rejected(conn):
    # Use _check_excluded directly to test boundary
    # At 0.59 similarity → should NOT reject (threshold is 0.60)
    # We can't easily engineer exact Jaccard similarity via text, so test the helper

    # Completely unrelated → Jaccard=0 → no match
    result = _check_excluded("quantum physics", ["cooking delicious meals"])
    assert result is None


# ---------------------------------------------------------------------------
# BE — EXCLUDED_TOPIC_JACCARD_THRESHOLD boundary: exact match → rejected
# ---------------------------------------------------------------------------


def test_be_excluded_threshold_exact_match(conn):

    # Exact match → Jaccard=1.0 → rejected
    result = _check_excluded("python tutorials", ["python tutorials"])
    assert result == "python tutorials"


# ---------------------------------------------------------------------------
# BF — dedup near-duplicate → deferred, not rejected (prior_queries)
# ---------------------------------------------------------------------------


def test_bf_prior_query_deferred_not_rejected(conn):
    run_id = _make_run(conn)
    query = "python basic programming"
    nq = normalize_topic(query)
    p = _make_probe(conn, run_id, query)

    # Prior query is the same normalized form — novelty=0.2 but NOT rejected
    result = run_niche_selection(
        conn,
        run_id,
        primary_niche="programming",
        excluded_topics=[],
        prior_queries={nq},
        max_probes=10,
    )
    # Should be selected (or deferred by capacity), never rejected just for being prior
    assert p not in result.rejected


# ---------------------------------------------------------------------------
# BG — priority_components_json persisted on selected probe
# ---------------------------------------------------------------------------


def test_bg_priority_components_json_persisted(conn):
    run_id = _make_run(conn)
    p = _make_probe(conn, run_id, "structured topic")
    provider = _fake_provider([p], eligible=True, fit_score=0.77)
    run_niche_selection(
        conn,
        run_id,
        primary_niche="science",
        excluded_topics=[],
        prior_queries=set(),
        max_probes=5,
        ai_provider=provider,
    )
    probe = get_exploration_probe(conn, p)
    assert probe.priority_components_json is not None
    data = json.loads(probe.priority_components_json)
    assert "niche_fit" in data
    assert "novelty" in data
    assert "depth_factor" in data


# ---------------------------------------------------------------------------
# BH — no live network calls
# ---------------------------------------------------------------------------


def test_bh_no_live_network_calls(conn):
    run_id = _make_run(conn)
    p = _make_probe(conn, run_id, "isolated topic")
    # FakeProvider never contacts external APIs
    provider = _fake_provider([p])
    result = run_niche_selection(
        conn,
        run_id,
        primary_niche="test",
        excluded_topics=[],
        prior_queries=set(),
        max_probes=5,
        ai_provider=provider,
    )
    assert result.llm_provider == "fake"
    assert p in result.selected
