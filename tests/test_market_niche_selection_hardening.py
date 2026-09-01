"""Phase 13D-D.1 tests — Selection hardening: policy snapshot + semantic duplicate suppression.

36 tests A–AJ.

Safety invariants:
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
    SELECTOR_EXPLORATION_SLOT_RATIO,
    SELECTOR_POLICY_VERSION,
    SELECTOR_PRIORITY_WEIGHTS,
    SELECTOR_REGION_CLUSTER_JACCARD,
    SELECTOR_VELOCITY_REF_VIEWS_PER_DAY,
)
from app.intelligence.market.planner_repository import (
    create_exploration_probe,
    create_exploration_run,
    get_exploration_probe,
    get_exploration_run,
    update_probe_status,
)
from app.intelligence.market.selector import (
    NicheEvaluation,
    NicheGuardOutput,
    _find_root,
    _resolve_duplicate_groups,
    _validate_duplicate_refs,
    build_selector_policy_snapshot,
    run_niche_selection,
)

# ---------------------------------------------------------------------------
# Fixtures & shared helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def conn():
    with tempfile.TemporaryDirectory() as d:
        c = open_db(pathlib.Path(d) / "test.db")
        yield c
        c.close()


def _make_run(conn, max_probes: int = 10) -> int:
    return create_exploration_run(conn, max_probes=max_probes).id


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
    if components is not None:
        pc = PriorityComponents(**components)
        update_probe_status(
            conn,
            probe.id,
            status=status,
            priority_components_json=pc.model_dump_json(),
        )
    elif status != "candidate":
        update_probe_status(conn, probe.id, status=status)
    return probe.id


def _fake_provider(
    probe_ids: list[int], eligible: bool = True, fit_score: float = 0.8
) -> FakeProvider:
    evals = [
        {
            "probe_id": pid,
            "eligible": eligible,
            "fit_score": fit_score,
            "rationale": "test",
            "semantic_duplicate_of": None,
        }
        for pid in probe_ids
    ]
    return FakeProvider(json.dumps({"evaluations": evals}))


def _fake_provider_with_dups(
    decisions: list[dict],
) -> FakeProvider:
    """decisions: list of dicts with keys: probe_id, eligible, fit_score,
    [semantic_duplicate_of]."""
    evals = [
        {
            "probe_id": d["probe_id"],
            "eligible": d.get("eligible", True),
            "fit_score": d.get("fit_score", 0.8),
            "rationale": d.get("rationale", "test"),
            "semantic_duplicate_of": d.get("semantic_duplicate_of", None),
        }
        for d in decisions
    ]
    return FakeProvider(json.dumps({"evaluations": evals}))


def _make_scored_item(probe_id: int, query: str, score: float, eval_dup: int | None = None):
    """Build a minimal (_Scored) tuple for unit-testing _resolve_duplicate_groups."""
    from app.intelligence.market.planner_models import (
        ExplorationProbe,
        PriorityComponents,
    )

    probe = ExplorationProbe(
        id=probe_id,
        exploration_run_id=1,
        query_text=query,
        normalized_query=normalize_topic(query),
        probe_type=ExplorationProbeType.MARKET_REGION,
        exploration_depth=0,
        status="candidate",
        collection_policy=CollectionPolicy(),
        input_hash=f"hash-{probe_id}",
        created_at="2026-01-01T00:00:00",
    )
    components = PriorityComponents(niche_fit=score, novelty=1.0, depth_factor=1.0)
    ev = NicheEvaluation(
        probe_id=probe_id,
        eligible=True,
        fit_score=score,
        rationale="test",
        semantic_duplicate_of=eval_dup,
    )
    return (probe, components, score, ev)


# ---------------------------------------------------------------------------
# A — build_selector_policy_snapshot returns required top-level keys
# ---------------------------------------------------------------------------


def test_a_policy_snapshot_has_required_keys():
    snap = build_selector_policy_snapshot()
    for key in (
        "policy_version",
        "priority_weights",
        "applicable_component_policy",
        "semantic_evaluator",
        "semantic_duplicate_policy",
        "diversity_policy",
        "portfolio_policy",
        "velocity_normalization",
        "excluded_topic_jaccard_threshold",
        "cluster_jaccard_threshold",
        "max_batch_size",
    ):
        assert key in snap, f"Missing key: {key}"


# ---------------------------------------------------------------------------
# B — policy_version matches SELECTOR_POLICY_VERSION constant
# ---------------------------------------------------------------------------


def test_b_policy_version_matches_constant():
    snap = build_selector_policy_snapshot()
    assert snap["policy_version"] == SELECTOR_POLICY_VERSION


# ---------------------------------------------------------------------------
# C — priority_weights matches SELECTOR_PRIORITY_WEIGHTS constant
# ---------------------------------------------------------------------------


def test_c_priority_weights_match_constants():
    snap = build_selector_policy_snapshot()
    assert snap["priority_weights"] == dict(SELECTOR_PRIORITY_WEIGHTS)


# ---------------------------------------------------------------------------
# D — velocity_normalization ownership is upstream_adjacent_planner
# ---------------------------------------------------------------------------


def test_d_velocity_normalization_ownership():
    snap = build_selector_policy_snapshot()
    vn = snap["velocity_normalization"]
    assert vn["ownership"] == "upstream_adjacent_planner"


# ---------------------------------------------------------------------------
# E — velocity_normalization.formula is documented
# ---------------------------------------------------------------------------


def test_e_velocity_normalization_formula_documented():
    snap = build_selector_policy_snapshot()
    vn = snap["velocity_normalization"]
    formula = vn.get("formula", "")
    assert "min(1.0" in formula
    assert "peak_vpd" in formula


# ---------------------------------------------------------------------------
# F — semantic_duplicate_policy.output_field is semantic_duplicate_of
# ---------------------------------------------------------------------------


def test_f_semantic_duplicate_policy_output_field():
    snap = build_selector_policy_snapshot()
    sdp = snap["semantic_duplicate_policy"]
    assert sdp["output_field"] == "semantic_duplicate_of"


# ---------------------------------------------------------------------------
# G — diversity_policy.cluster_jaccard_threshold matches constant
# ---------------------------------------------------------------------------


def test_g_diversity_policy_cluster_threshold():
    snap = build_selector_policy_snapshot()
    dp = snap["diversity_policy"]
    assert dp["cluster_jaccard_threshold"] == SELECTOR_REGION_CLUSTER_JACCARD


# ---------------------------------------------------------------------------
# H — portfolio_policy.exploration_slot_ratio matches constant
# ---------------------------------------------------------------------------


def test_h_portfolio_policy_ratio():
    snap = build_selector_policy_snapshot()
    pp = snap["portfolio_policy"]
    assert pp["exploration_slot_ratio"] == SELECTOR_EXPLORATION_SLOT_RATIO


# ---------------------------------------------------------------------------
# I — update_exploration_run_policy persists JSON to run.policy_json
# ---------------------------------------------------------------------------


def test_i_update_exploration_run_policy_persists(conn):
    from app.intelligence.market.planner_repository import update_exploration_run_policy

    run_id = _make_run(conn)
    payload = json.dumps({"test": True})
    run = update_exploration_run_policy(conn, run_id, payload)
    assert run.policy_json == payload
    # Verify persistence via re-read
    reloaded = get_exploration_run(conn, run_id)
    assert reloaded.policy_json == payload


# ---------------------------------------------------------------------------
# J — run_niche_selection persists policy snapshot to run.policy_json
# ---------------------------------------------------------------------------


def test_j_run_niche_selection_persists_policy_snapshot(conn):
    run_id = _make_run(conn)
    p1 = _make_probe(conn, run_id, "python tutorials")
    provider = _fake_provider([p1])
    run_niche_selection(
        conn,
        run_id,
        primary_niche="Python programming",
        excluded_topics=[],
        prior_queries=set(),
        max_probes=5,
        ai_provider=provider,
    )
    run = get_exploration_run(conn, run_id)
    assert run.policy_json is not None


# ---------------------------------------------------------------------------
# K — persisted policy_json is valid parseable JSON
# ---------------------------------------------------------------------------


def test_k_persisted_policy_json_is_valid_json(conn):
    run_id = _make_run(conn)
    p1 = _make_probe(conn, run_id, "python tutorials")
    provider = _fake_provider([p1])
    run_niche_selection(
        conn,
        run_id,
        primary_niche="Python programming",
        excluded_topics=[],
        prior_queries=set(),
        max_probes=5,
        ai_provider=provider,
    )
    run = get_exploration_run(conn, run_id)
    parsed = json.loads(run.policy_json)
    assert isinstance(parsed, dict)
    assert "policy_version" in parsed


# ---------------------------------------------------------------------------
# L — NicheEvaluation.semantic_duplicate_of defaults to None
# ---------------------------------------------------------------------------


def test_l_niche_evaluation_semantic_duplicate_of_defaults_none():
    ev = NicheEvaluation(probe_id=1, eligible=True, fit_score=0.8, rationale="ok")
    assert ev.semantic_duplicate_of is None


# ---------------------------------------------------------------------------
# M — NicheEvaluation accepts integer semantic_duplicate_of
# ---------------------------------------------------------------------------


def test_m_niche_evaluation_accepts_integer_dup():
    ev = NicheEvaluation(
        probe_id=5,
        eligible=True,
        fit_score=0.9,
        rationale="ok",
        semantic_duplicate_of=3,
    )
    assert ev.semantic_duplicate_of == 3


# ---------------------------------------------------------------------------
# N — NicheEvaluation accepts semantic_duplicate_of=None explicitly
# ---------------------------------------------------------------------------


def test_n_niche_evaluation_accepts_none_dup():
    ev = NicheEvaluation(
        probe_id=5,
        eligible=True,
        fit_score=0.9,
        rationale="ok",
        semantic_duplicate_of=None,
    )
    assert ev.semantic_duplicate_of is None


# ---------------------------------------------------------------------------
# O — NicheGuardOutput round-trips JSON with semantic_duplicate_of
# ---------------------------------------------------------------------------


def test_o_niche_guard_output_round_trips_with_dup():
    raw = json.dumps(
        {
            "evaluations": [
                {
                    "probe_id": 1,
                    "eligible": True,
                    "fit_score": 0.9,
                    "rationale": "on niche",
                    "semantic_duplicate_of": None,
                },
                {
                    "probe_id": 2,
                    "eligible": True,
                    "fit_score": 0.85,
                    "rationale": "same as 1",
                    "semantic_duplicate_of": 1,
                },
            ]
        }
    )
    output = NicheGuardOutput.model_validate_json(raw)
    assert output.evaluations[0].semantic_duplicate_of is None
    assert output.evaluations[1].semantic_duplicate_of == 1


# ---------------------------------------------------------------------------
# P — _validate_duplicate_refs clears self-reference
# ---------------------------------------------------------------------------


def test_p_validate_clears_self_reference():
    ev = NicheEvaluation(
        probe_id=10,
        eligible=True,
        fit_score=0.8,
        rationale="ok",
        semantic_duplicate_of=10,  # self-reference
    )
    niche_fits = {10: ev}
    _validate_duplicate_refs(niche_fits, batch_ids={10}, eligible_ids={10})
    assert ev.semantic_duplicate_of is None


# ---------------------------------------------------------------------------
# Q — _validate_duplicate_refs clears reference to unknown probe_id
# ---------------------------------------------------------------------------


def test_q_validate_clears_unknown_probe_id():
    ev = NicheEvaluation(
        probe_id=10,
        eligible=True,
        fit_score=0.8,
        rationale="ok",
        semantic_duplicate_of=99,  # not in batch
    )
    niche_fits = {10: ev}
    _validate_duplicate_refs(niche_fits, batch_ids={10}, eligible_ids={10})
    assert ev.semantic_duplicate_of is None


# ---------------------------------------------------------------------------
# R — _validate_duplicate_refs clears reference to ineligible probe
# ---------------------------------------------------------------------------


def test_r_validate_clears_reference_to_ineligible():
    ev_ineligible = NicheEvaluation(probe_id=5, eligible=False, fit_score=0.1, rationale="off")
    ev_dup = NicheEvaluation(
        probe_id=10,
        eligible=True,
        fit_score=0.8,
        rationale="ok",
        semantic_duplicate_of=5,  # references ineligible
    )
    niche_fits = {5: ev_ineligible, 10: ev_dup}
    _validate_duplicate_refs(niche_fits, batch_ids={5, 10}, eligible_ids={10})
    assert ev_dup.semantic_duplicate_of is None


# ---------------------------------------------------------------------------
# S — _validate_duplicate_refs preserves valid reference
# ---------------------------------------------------------------------------


def test_s_validate_preserves_valid_reference():
    ev_canonical = NicheEvaluation(probe_id=5, eligible=True, fit_score=0.9, rationale="ok")
    ev_dup = NicheEvaluation(
        probe_id=10,
        eligible=True,
        fit_score=0.8,
        rationale="same",
        semantic_duplicate_of=5,
    )
    niche_fits = {5: ev_canonical, 10: ev_dup}
    _validate_duplicate_refs(niche_fits, batch_ids={5, 10}, eligible_ids={5, 10})
    assert ev_dup.semantic_duplicate_of == 5


# ---------------------------------------------------------------------------
# T — _resolve_duplicate_groups: higher-score probe is canonical
# ---------------------------------------------------------------------------


def test_t_resolve_higher_score_is_canonical():
    # probe 1 has score 0.9 (higher), probe 2 has score 0.7 and points to probe 1
    item1 = _make_scored_item(1, "lost civilizations ancient history", 0.9)
    item2 = _make_scored_item(2, "forgotten ancient civilizations", 0.7, eval_dup=1)
    scored = [item1, item2]
    canonicals, non_can = _resolve_duplicate_groups(scored)
    can_ids = {c[0].id for c in canonicals}
    assert 1 in can_ids
    assert 2 not in can_ids
    assert non_can[2] == 1


# ---------------------------------------------------------------------------
# U — _resolve_duplicate_groups tiebreak: lower probe_id wins
# ---------------------------------------------------------------------------


def test_u_resolve_tiebreak_lower_probe_id():
    # Equal scores — probe 1 and probe 2 point to each other effectively
    # probe 2 says it's a dup of probe 1 (both same score)
    item1 = _make_scored_item(1, "python tutorial beginners", 0.8)
    item2 = _make_scored_item(2, "learn python from scratch", 0.8, eval_dup=1)
    scored = [item1, item2]
    canonicals, non_can = _resolve_duplicate_groups(scored)
    can_ids = {c[0].id for c in canonicals}
    assert 1 in can_ids  # lower probe_id wins tiebreak
    assert 2 in non_can
    assert non_can[2] == 1


# ---------------------------------------------------------------------------
# V — non-canonical gets deferred reason "semantic_duplicate_of:<id>"
# ---------------------------------------------------------------------------


def test_v_non_canonical_deferred_reason(conn):
    run_id = _make_run(conn)
    p1 = _make_probe(conn, run_id, "lost civilizations ancient worlds")
    p2 = _make_probe(conn, run_id, "forgotten ancient civilizations history")
    provider = _fake_provider_with_dups(
        [
            {"probe_id": p1, "eligible": True, "fit_score": 0.9},
            {"probe_id": p2, "eligible": True, "fit_score": 0.7, "semantic_duplicate_of": p1},
        ]
    )
    run_niche_selection(
        conn,
        run_id,
        primary_niche="Ancient history",
        excluded_topics=[],
        prior_queries=set(),
        max_probes=5,
        ai_provider=provider,
    )
    probe2 = get_exploration_probe(conn, p2)
    assert probe2.status == "deferred"
    assert probe2.decision_reason.startswith("semantic_duplicate_of:")
    assert str(p1) in probe2.decision_reason


# ---------------------------------------------------------------------------
# W — canonical probe proceeds to selection
# ---------------------------------------------------------------------------


def test_w_canonical_probe_gets_selected(conn):
    run_id = _make_run(conn)
    p1 = _make_probe(conn, run_id, "lost civilizations ancient worlds")
    p2 = _make_probe(conn, run_id, "forgotten ancient civilizations history")
    provider = _fake_provider_with_dups(
        [
            {"probe_id": p1, "eligible": True, "fit_score": 0.9},
            {"probe_id": p2, "eligible": True, "fit_score": 0.7, "semantic_duplicate_of": p1},
        ]
    )
    result = run_niche_selection(
        conn,
        run_id,
        primary_niche="Ancient history",
        excluded_topics=[],
        prior_queries=set(),
        max_probes=5,
        ai_provider=provider,
    )
    probe1 = get_exploration_probe(conn, p1)
    assert probe1.status == "selected"
    assert p1 in result.selected


# ---------------------------------------------------------------------------
# X — three-way duplicate: one canonical selected, two deferred
# ---------------------------------------------------------------------------


def test_x_three_way_duplicate_one_canonical(conn):
    run_id = _make_run(conn)
    p1 = _make_probe(conn, run_id, "ancient lost civilizations history world")
    p2 = _make_probe(conn, run_id, "forgotten civilizations ancient mysteries")
    p3 = _make_probe(conn, run_id, "civilizations lost to history ancient")
    provider = _fake_provider_with_dups(
        [
            {"probe_id": p1, "eligible": True, "fit_score": 0.9},
            {"probe_id": p2, "eligible": True, "fit_score": 0.7, "semantic_duplicate_of": p1},
            {"probe_id": p3, "eligible": True, "fit_score": 0.6, "semantic_duplicate_of": p1},
        ]
    )
    result = run_niche_selection(
        conn,
        run_id,
        primary_niche="Ancient history",
        excluded_topics=[],
        prior_queries=set(),
        max_probes=5,
        ai_provider=provider,
    )
    assert p1 in result.selected
    assert p2 in result.deferred
    assert p3 in result.deferred
    p2_row = get_exploration_probe(conn, p2)
    p3_row = get_exploration_probe(conn, p3)
    assert p2_row.decision_reason.startswith("semantic_duplicate_of:")
    assert p3_row.decision_reason.startswith("semantic_duplicate_of:")


# ---------------------------------------------------------------------------
# Y — _find_root resolves chain correctly
# ---------------------------------------------------------------------------


def test_y_find_root_resolves_chain():
    # A→B, B→C — root of A should be C
    parent = {1: 2, 2: 3}
    assert _find_root(1, parent) == 3
    assert _find_root(2, parent) == 3
    assert _find_root(3, parent) == 3  # no parent


# ---------------------------------------------------------------------------
# Z — _find_root terminates on cycle (cycle protection)
# ---------------------------------------------------------------------------


def test_z_find_root_terminates_on_cycle():
    # A→B, B→A (cycle)
    parent = {1: 2, 2: 1}
    # Should not raise and should return some stable value
    result = _find_root(1, parent)
    assert isinstance(result, int)
    result2 = _find_root(2, parent)
    assert isinstance(result2, int)


# ---------------------------------------------------------------------------
# AA — semantic duplicates deferred before portfolio: don't exhaust slots
# ---------------------------------------------------------------------------


def test_aa_semantic_dups_deferred_before_portfolio(conn):
    """With max_probes=1, a canonical + N duplicates → canonical selected, dups deferred."""
    run_id = _make_run(conn, max_probes=1)
    p1 = _make_probe(conn, run_id, "ancient world civilizations history lost")
    p2 = _make_probe(conn, run_id, "forgotten civilizations ancient world")
    p3 = _make_probe(conn, run_id, "civilizations lost ancient mysteries world")
    provider = _fake_provider_with_dups(
        [
            {"probe_id": p1, "eligible": True, "fit_score": 0.95},
            {"probe_id": p2, "eligible": True, "fit_score": 0.7, "semantic_duplicate_of": p1},
            {"probe_id": p3, "eligible": True, "fit_score": 0.65, "semantic_duplicate_of": p1},
        ]
    )
    result = run_niche_selection(
        conn,
        run_id,
        primary_niche="Ancient history",
        excluded_topics=[],
        prior_queries=set(),
        max_probes=1,
        ai_provider=provider,
    )
    # Canonical selected, duplicates deferred (not capacity_exceeded)
    assert p1 in result.selected
    p2_row = get_exploration_probe(conn, p2)
    p3_row = get_exploration_probe(conn, p3)
    assert p2_row.decision_reason.startswith("semantic_duplicate_of:")
    assert p3_row.decision_reason.startswith("semantic_duplicate_of:")
    # The slot is occupied by canonical, not by the dups
    assert len(result.selected) == 1


# ---------------------------------------------------------------------------
# AB — ineligible probe cannot be referenced as canonical (ref cleared)
# ---------------------------------------------------------------------------


def test_ab_ineligible_cannot_be_canonical(conn):
    """LLM marks p1 as ineligible; p2 references p1 as its dup. After validation,
    the ref is cleared, p2 is treated as independent and may be selected."""
    run_id = _make_run(conn)
    p1 = _make_probe(conn, run_id, "completely off topic woodworking crafts")
    p2 = _make_probe(conn, run_id, "python programming tutorial beginners")
    provider = _fake_provider_with_dups(
        [
            {"probe_id": p1, "eligible": False, "fit_score": 0.1},
            {"probe_id": p2, "eligible": True, "fit_score": 0.9, "semantic_duplicate_of": p1},
        ]
    )
    result = run_niche_selection(
        conn,
        run_id,
        primary_niche="Python programming",
        excluded_topics=[],
        prior_queries=set(),
        max_probes=5,
        ai_provider=provider,
    )
    # p1 rejected, p2's ref to p1 cleared → p2 should be selected
    assert p1 in result.rejected
    assert p2 in result.selected


# ---------------------------------------------------------------------------
# AC — self-reference cleared, probe participates normally in selection
# ---------------------------------------------------------------------------


def test_ac_self_reference_cleared_probe_participates(conn):
    run_id = _make_run(conn)
    p1 = _make_probe(conn, run_id, "python programming tutorial")
    provider = _fake_provider_with_dups(
        [
            {"probe_id": p1, "eligible": True, "fit_score": 0.9, "semantic_duplicate_of": p1},
        ]
    )
    result = run_niche_selection(
        conn,
        run_id,
        primary_niche="Python programming",
        excluded_topics=[],
        prior_queries=set(),
        max_probes=5,
        ai_provider=provider,
    )
    # Self-reference cleared; probe treated as independent, selected normally
    assert p1 in result.selected


# ---------------------------------------------------------------------------
# AD — _find_root handles long chain without infinite loop
# ---------------------------------------------------------------------------


def test_ad_find_root_terminates_on_long_chain():
    # Chain of length 40 (exceeds the 30-hop guard)
    parent = {i: i + 1 for i in range(1, 40)}
    # Should terminate and return some node (the guard kicks in around hop 30)
    result = _find_root(1, parent)
    assert isinstance(result, int)


# ---------------------------------------------------------------------------
# AE — ai_provider=None: no duplicate detection, eligible probes selected normally
# ---------------------------------------------------------------------------


def test_ae_no_provider_no_duplicate_detection(conn):
    run_id = _make_run(conn)
    p1 = _make_probe(conn, run_id, "python programming tutorial beginners")
    p2 = _make_probe(conn, run_id, "learning python from scratch coding")
    # No provider → no LLM, so no niche_fits, no semantic_duplicate_of
    result = run_niche_selection(
        conn,
        run_id,
        primary_niche="Python programming",
        excluded_topics=[],
        prior_queries=set(),
        max_probes=5,
        ai_provider=None,
    )
    # Both should be selected (no LLM rejection, no dup detection)
    assert p1 in result.selected
    assert p2 in result.selected


# ---------------------------------------------------------------------------
# AF — probes in different Jaccard clusters can still be semantic duplicates
# ---------------------------------------------------------------------------


def test_af_cross_cluster_semantic_duplicate(conn):
    """Probes with low Jaccard similarity (different clusters) can still be flagged
    as semantic duplicates by the LLM."""
    run_id = _make_run(conn)
    p1 = _make_probe(conn, run_id, "ancient egypt pharaohs tombs mummies")
    p2 = _make_probe(conn, run_id, "lost empires mesopotamia babylon")
    provider = _fake_provider_with_dups(
        [
            {"probe_id": p1, "eligible": True, "fit_score": 0.85},
            {"probe_id": p2, "eligible": True, "fit_score": 0.75, "semantic_duplicate_of": p1},
        ]
    )
    result = run_niche_selection(
        conn,
        run_id,
        primary_niche="Ancient civilizations",
        excluded_topics=[],
        prior_queries=set(),
        max_probes=5,
        ai_provider=provider,
    )
    # Despite different Jaccard clusters, p2 is still deferred as duplicate of p1
    assert p1 in result.selected
    p2_row = get_exploration_probe(conn, p2)
    assert p2_row.decision_reason.startswith("semantic_duplicate_of:")


# ---------------------------------------------------------------------------
# AG — velocity_normalization ref_views_per_day matches constant
# ---------------------------------------------------------------------------


def test_ag_velocity_ref_views_per_day_documented():
    snap = build_selector_policy_snapshot()
    vn = snap["velocity_normalization"]
    key = "ref_views_per_day_documented_only"
    assert key in vn
    assert vn[key] == SELECTOR_VELOCITY_REF_VIEWS_PER_DAY


# ---------------------------------------------------------------------------
# AH — N-1 duplicates of one canonical: only canonical selected
# ---------------------------------------------------------------------------


def test_ah_all_duplicates_of_one_canonical(conn):
    run_id = _make_run(conn, max_probes=10)
    p1 = _make_probe(conn, run_id, "python programming tutorial beginners guide")
    p2 = _make_probe(conn, run_id, "learn python beginners online tutorial")
    p3 = _make_probe(conn, run_id, "intro python programming beginners course")
    p4 = _make_probe(conn, run_id, "beginners guide python language coding")
    provider = _fake_provider_with_dups(
        [
            {"probe_id": p1, "eligible": True, "fit_score": 0.95},
            {"probe_id": p2, "eligible": True, "fit_score": 0.8, "semantic_duplicate_of": p1},
            {"probe_id": p3, "eligible": True, "fit_score": 0.75, "semantic_duplicate_of": p1},
            {"probe_id": p4, "eligible": True, "fit_score": 0.7, "semantic_duplicate_of": p1},
        ]
    )
    result = run_niche_selection(
        conn,
        run_id,
        primary_niche="Python programming",
        excluded_topics=[],
        prior_queries=set(),
        max_probes=10,
        ai_provider=provider,
    )
    assert result.selected == [p1]
    assert p2 in result.deferred
    assert p3 in result.deferred
    assert p4 in result.deferred


# ---------------------------------------------------------------------------
# AI — policy snapshot round-trips through JSON serialization
# ---------------------------------------------------------------------------


def test_ai_policy_snapshot_json_roundtrip():
    snap = build_selector_policy_snapshot()
    raw = json.dumps(snap)
    restored = json.loads(raw)
    assert restored["policy_version"] == snap["policy_version"]
    assert restored["priority_weights"] == snap["priority_weights"]
    assert restored["velocity_normalization"]["ownership"] == "upstream_adjacent_planner"
    assert restored["semantic_duplicate_policy"]["output_field"] == "semantic_duplicate_of"


# ---------------------------------------------------------------------------
# AJ — full integration: excluded topic + LLM rejection + semantic dup + cluster cap
# ---------------------------------------------------------------------------


def test_aj_full_integration_all_filters_applied(conn):
    """Comprehensive pipeline test exercising every filter layer in one run.

    Setup:
      p_excl  — excluded topic (Layer 1 deterministic rejection)
      p_rej   — LLM marks ineligible (Layer 2 rejection)
      p_dup   — LLM marks as semantic duplicate of p_canon (deferred before portfolio)
      p_canon — canonical; should be selected
      p_cluster2a, p_cluster2b, p_cluster2c — three probes in same Jaccard cluster;
                 cluster cap = 2, so the third should be deferred
    """
    run_id = _make_run(conn, max_probes=10)

    # "woodworking" → Jaccard({woodworking}, {woodworking}) = 1.0 ≥ 0.60 → excluded
    p_excl = _make_probe(conn, run_id, "woodworking")
    p_rej = _make_probe(conn, run_id, "cooking recipes baking desserts")
    p_canon = _make_probe(conn, run_id, "python programming tutorial beginners")
    p_dup = _make_probe(conn, run_id, "learn python from scratch beginners")
    # Three probes that will land in same Jaccard cluster
    p_c2a = _make_probe(conn, run_id, "javascript web development frontend coding")
    p_c2b = _make_probe(conn, run_id, "javascript web development frontend framework")
    p_c2c = _make_probe(conn, run_id, "javascript web development frontend library")

    excluded_topics = ["woodworking", "furniture"]

    provider = _fake_provider_with_dups(
        [
            {"probe_id": p_rej, "eligible": False, "fit_score": 0.05},
            {"probe_id": p_canon, "eligible": True, "fit_score": 0.95},
            {
                "probe_id": p_dup,
                "eligible": True,
                "fit_score": 0.8,
                "semantic_duplicate_of": p_canon,
            },
            {"probe_id": p_c2a, "eligible": True, "fit_score": 0.7},
            {"probe_id": p_c2b, "eligible": True, "fit_score": 0.68},
            {"probe_id": p_c2c, "eligible": True, "fit_score": 0.66},
        ]
    )

    result = run_niche_selection(
        conn,
        run_id,
        primary_niche="Python programming",
        excluded_topics=excluded_topics,
        prior_queries=set(),
        max_probes=10,
        ai_provider=provider,
    )

    # p_excl → rejected by excluded-topic guard
    assert p_excl in result.rejected
    excl_row = get_exploration_probe(conn, p_excl)
    assert "excluded_topic_match" in excl_row.decision_reason

    # p_rej → rejected by LLM niche guard
    assert p_rej in result.rejected
    rej_row = get_exploration_probe(conn, p_rej)
    assert "niche_guard_ineligible" in rej_row.decision_reason

    # p_dup → deferred as semantic duplicate of p_canon
    assert p_dup in result.deferred
    dup_row = get_exploration_probe(conn, p_dup)
    assert dup_row.decision_reason.startswith("semantic_duplicate_of:")

    # p_canon → selected
    assert p_canon in result.selected

    # cluster2: two selected, one deferred by cluster cap
    c2_results = [get_exploration_probe(conn, pid) for pid in (p_c2a, p_c2b, p_c2c)]
    c2_selected = [r for r in c2_results if r.status == "selected"]
    c2_deferred = [r for r in c2_results if r.status == "deferred"]
    assert len(c2_selected) == 2
    assert len(c2_deferred) == 1
    assert "cluster_diversity_cap" in c2_deferred[0].decision_reason
