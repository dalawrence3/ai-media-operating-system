"""Phase 14D — Experiment Candidate Scoring, Information Gain, and Portfolio Selection.

Tests A–BT (72 tests).

Contracts:
- No YouTube calls
- No LLM calls
- No content generation
- No Experiment row creation during planning
- Deterministic: same inputs → same output
- Cluster diversity: max 40% cluster share, max 2 consecutive from same cluster
- Max 1 treatment factor per candidate
- HIGH OPPORTUNITY SCORE does NOT mean MAKE THIS NEXT (separate exploit/explore models)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_assessment(
    opportunity_id: int,
    channel_id: int = 1,
    classification: str = "general_eligible",
    signal_maturity: str = "directional",
    signal_confidence: float = 0.70,
    has_active_conflict: bool = False,
    signal_maturity_override: str | None = None,
):
    from app.intelligence.experiments.eligibility import (
        ExperimentEligibilityAssessment,
        ExperimentEligibilityClassification,
    )

    maturity = signal_maturity_override or signal_maturity
    return ExperimentEligibilityAssessment(
        opportunity_id=opportunity_id,
        channel_id=channel_id,
        classification=ExperimentEligibilityClassification(classification),
        findings=[],
        policy_snapshot_json="{}",
        assessed_at="2026-08-21T12:00:00",
        signal_maturity=maturity,
        signal_confidence=signal_confidence,
        has_active_conflict=has_active_conflict,
    )


@pytest.fixture()
def db(tmp_path: Path):
    from app.core.database import open_db

    conn = open_db(tmp_path / "test_14d.db")
    conn.execute("PRAGMA foreign_keys = OFF")
    yield conn
    conn.close()


def _insert_channel(conn, channel_id: int = 1) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO channels
           (id, platform, channel_name, platform_channel_id)
           VALUES (?, ?, ?, ?)""",
        (channel_id, "youtube", "Test Channel", f"UC{channel_id}test"),
    )
    conn.commit()


def _insert_opportunity(
    conn,
    opp_id: int,
    channel_id: int = 1,
    topic: str = "test topic",
    cluster_id: int | None = None,
    lifecycle_state: str = "approved",
) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO opportunities
           (id, channel_id, discovery_run_id, normalized_topic, raw_topic,
            canonical_cluster_id, current_lifecycle_state,
            created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            opp_id,
            channel_id,
            1,
            topic,
            topic,
            cluster_id,
            lifecycle_state,
            "2026-08-21T12:00:00",
            "2026-08-21T12:00:00",
        ),
    )
    conn.commit()


def _insert_opportunity_score(conn, opp_id: int, composite: float = 0.70) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO opportunity_scores
           (opportunity_id, scoring_policy_id, channel_profile_version_id,
            composite_score, confidence,
            eff_weight_trend_strength, eff_weight_audience_demand,
            eff_weight_competition, eff_weight_evergreen_value,
            eff_weight_audience_fit, eff_weight_content_novelty,
            input_hash, scorer_version, scored_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            opp_id,
            1,
            1,
            composite,
            0.80,
            0.05,
            0.20,
            0.15,
            0.20,
            0.30,
            0.10,
            f"hash_{opp_id}",
            "1.0",
            "2026-08-21T12:00:00",
        ),
    )
    conn.commit()


def _insert_prior_experiment(
    conn, opp_id: int, channel_id: int = 1, status: str = "in_production"
) -> str:
    """Insert a prior non-cancelled experiment so the cluster is not 'new'."""
    import uuid

    exp_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO experiments
           (id, channel_id, opportunity_id, experiment_type, status, hypothesis,
            created_at, updated_at)
           VALUES (?, ?, ?, 'exploration', ?, 'prior exploration', '2026-01-01', '2026-01-01')""",
        (exp_id, channel_id, opp_id, status),
    )
    conn.commit()
    return exp_id


def _seed_channel_evidence(
    conn,
    channel_id: int = 1,
    *,
    maturity: str = "actionable",
    publication_count: int = 12,
    metric_name: str = "average_view_percentage",
) -> str:
    """Give the channel real cross-publication evidence of its own.

    Phase 18D routes exploitation on the channel's OWN measured evidence
    (Phase 12C baselines) rather than on market cluster maturity, so a test
    that wants to reach the exploitation pool has to give the channel
    something it has actually learned. Baselines are keyed by the
    control-plane channel UUID, so this also creates the identity bridge the
    planner crosses to find them.
    """
    import uuid

    cp_channel_id = str(uuid.uuid4())
    conn.execute("UPDATE channels SET cp_channel_id = ? WHERE id = ?", (cp_channel_id, channel_id))
    conn.execute(
        """INSERT INTO channel_performance_baselines
           (channel_id, workspace_id, metric_name, period_type, publication_count,
            mean, median, min_value, max_value, std_dev, sample_maturity,
            source_publication_ids_json, source_snapshot_ids_json,
            comparison_schema_version, observer_version, input_hash,
            created_at, updated_at)
           VALUES (?, 'ws', ?, 'lifetime', ?, 50.0, 50.0, 40.0, 60.0, 5.0, ?,
                   '[]', '[]', 'v1', 'v1', ?, '2026-01-01', '2026-01-01')""",
        (cp_channel_id, metric_name, publication_count, maturity, str(uuid.uuid4())[:16]),
    )
    conn.commit()
    return cp_channel_id


# ── Tests A–I: Imports and model integrity ─────────────────────────────────────


def test_A_import_planning_module():
    """A: planning.py importable without error."""
    from app.intelligence.experiments import planning  # noqa: F401

    assert planning.PlanningIntent is not None


def test_B_planning_policy_v1_defaults():
    """B: PlanningPolicy.v1() has expected default fields."""
    from app.intelligence.experiments.planning import PlanningPolicy

    p = PlanningPolicy.v1()
    assert p.version == "1.0.0"
    assert p.max_exploration_slots == 2
    assert p.max_exploitation_slots == 1
    assert p.max_cluster_share == 0.40
    assert p.max_consecutive_same_cluster == 2
    assert p.primary_target_metric == "average_view_percentage"
    assert p.primary_metric_direction == "higher_is_better"
    assert p.max_treatment_factors == 1


def test_C_planning_policy_to_json_round_trips():
    """C: PlanningPolicy.to_json() contains all expected keys."""
    from app.intelligence.experiments.planning import PlanningPolicy

    p = PlanningPolicy.v1()
    data = json.loads(p.to_json())
    expected_keys = {
        "version",
        "max_exploration_slots",
        "max_exploitation_slots",
        "max_cluster_share",
        "max_consecutive_same_cluster",
        "w_exploitation_attractiveness",
        "w_exploitation_evidence",
        "w_exploitation_feasibility",
        "w_exploration_information_gain",
        "w_exploration_uncertainty",
        "w_exploration_cluster_coverage",
        "w_ig_cluster_newness",
        "w_ig_maturity_gap",
        "w_ig_feature_coverage",
        "maturity_strength",
        "primary_target_metric",
        "primary_metric_direction",
        "max_treatment_factors",
    }
    assert expected_keys.issubset(set(data.keys()))


def test_D_safe_controllable_factors_narration_rate():
    """D: SAFE_CONTROLLABLE_FACTORS has narration_speaking_rate with correct range."""
    from app.intelligence.experiments.planning import SAFE_CONTROLLABLE_FACTORS

    assert "narration_speaking_rate" in SAFE_CONTROLLABLE_FACTORS
    spec = SAFE_CONTROLLABLE_FACTORS["narration_speaking_rate"]
    assert spec.value_type == "numeric"
    assert spec.safe_range_min == 0.7
    assert spec.safe_range_max == 1.5
    assert spec.bucket_width == 0.1


def test_E_safe_controllable_factors_has_hook():
    """E: SAFE_CONTROLLABLE_FACTORS has has_hook as boolean."""
    from app.intelligence.experiments.planning import SAFE_CONTROLLABLE_FACTORS

    assert "has_hook" in SAFE_CONTROLLABLE_FACTORS
    spec = SAFE_CONTROLLABLE_FACTORS["has_hook"]
    assert spec.value_type == "boolean"
    assert "true" in spec.safe_values
    assert "false" in spec.safe_values


def test_F_planning_intent_enum_values():
    """F: PlanningIntent has exploration, exploitation, validation."""
    from app.intelligence.experiments.planning import PlanningIntent

    assert PlanningIntent.EXPLORATION.value == "exploration"
    assert PlanningIntent.EXPLOITATION.value == "exploitation"
    assert PlanningIntent.VALIDATION.value == "validation"


def test_G_exploitation_weights_sum_to_one():
    """G: PlanningPolicy exploitation weights sum to 1.0."""
    from app.intelligence.experiments.planning import PlanningPolicy

    p = PlanningPolicy.v1()
    total = (
        p.w_exploitation_attractiveness + p.w_exploitation_evidence + p.w_exploitation_feasibility
    )
    assert abs(total - 1.0) < 1e-9


def test_H_exploration_weights_sum_to_one():
    """H: PlanningPolicy exploration weights sum to 1.0."""
    from app.intelligence.experiments.planning import PlanningPolicy

    p = PlanningPolicy.v1()
    total = (
        p.w_exploration_information_gain
        + p.w_exploration_uncertainty
        + p.w_exploration_cluster_coverage
    )
    assert abs(total - 1.0) < 1e-9


def test_I_ig_weights_sum_to_one():
    """I: PlanningPolicy information_gain sub-weights sum to 1.0."""
    from app.intelligence.experiments.planning import PlanningPolicy

    p = PlanningPolicy.v1()
    total = p.w_ig_cluster_newness + p.w_ig_maturity_gap + p.w_ig_feature_coverage
    assert abs(total - 1.0) < 1e-9


# ── Tests J–V: Score component computation ────────────────────────────────────


def test_J_feature_coverage_gap_all_untested():
    """J: _compute_feature_coverage_gap returns 1.0 when no features have coverage."""
    from app.intelligence.experiments.planning_service import _compute_feature_coverage_gap

    assert _compute_feature_coverage_gap({}) == 1.0


def test_K_feature_coverage_gap_all_covered():
    """K: _compute_feature_coverage_gap returns 0.0 when all features have at least one bucket."""
    from app.intelligence.experiments.planning import SAFE_CONTROLLABLE_FACTORS
    from app.intelligence.experiments.planning_service import _compute_feature_coverage_gap

    full_coverage = {name: {"bucket_a": {}} for name in SAFE_CONTROLLABLE_FACTORS}
    assert _compute_feature_coverage_gap(full_coverage) == 0.0


def test_L_feature_coverage_gap_partial():
    """L: _compute_feature_coverage_gap returns partial fraction when some covered."""
    from app.intelligence.experiments.planning import SAFE_CONTROLLABLE_FACTORS
    from app.intelligence.experiments.planning_service import _compute_feature_coverage_gap

    all_names = list(SAFE_CONTROLLABLE_FACTORS.keys())
    half = all_names[: len(all_names) // 2]
    coverage = {name: {"b": {}} for name in half}
    gap = _compute_feature_coverage_gap(coverage)
    assert 0.0 < gap <= 1.0
    # Exactly the uncovered fraction
    expected = (len(all_names) - len(half)) / len(all_names)
    assert abs(gap - expected) < 1e-9


def test_M_internal_evidence_strength_mapping():
    """M: maturity maps correctly to internal_evidence_strength values."""
    from app.intelligence.experiments.planning import PlanningPolicy

    p = PlanningPolicy.v1()
    assert p.maturity_strength["insufficient"] == 0.0
    assert p.maturity_strength["exploratory"] == 0.33
    assert p.maturity_strength["directional"] == 0.67
    assert p.maturity_strength["actionable"] == 1.0


def test_N_uncertainty_from_confidence():
    """N: uncertainty = 1 - signal_confidence."""
    from app.intelligence.experiments.planning import PlanningIntent, PlanningPolicy
    from app.intelligence.experiments.planning_service import _score_candidate

    assessment = _make_assessment(1, signal_confidence=0.70)
    policy = PlanningPolicy.v1()
    score = _score_candidate(
        assessment=assessment,
        cluster_id=None,
        existing_cluster_counts={},
        feature_coverage={},
        policy=policy,
        planning_intent=PlanningIntent.EXPLORATION,
        composite_score=0.60,
    )
    assert abs(score.uncertainty - 0.30) < 1e-9


def test_O_cluster_coverage_need_untested():
    """O: cluster_coverage_need = 1.0 for a cluster with no existing experiments."""
    from app.intelligence.experiments.planning import PlanningIntent, PlanningPolicy
    from app.intelligence.experiments.planning_service import _score_candidate

    assessment = _make_assessment(1)
    policy = PlanningPolicy.v1()
    score = _score_candidate(
        assessment=assessment,
        cluster_id=42,
        existing_cluster_counts={},  # cluster 42 not in dict → count=0
        feature_coverage={},
        policy=policy,
        planning_intent=PlanningIntent.EXPLORATION,
        composite_score=0.60,
    )
    assert abs(score.cluster_coverage_need - 1.0) < 1e-9


def test_P_cluster_coverage_need_with_one_experiment():
    """P: cluster_coverage_need = 0.5 for cluster with 1 existing experiment."""
    from app.intelligence.experiments.planning import PlanningIntent, PlanningPolicy
    from app.intelligence.experiments.planning_service import _score_candidate

    assessment = _make_assessment(1)
    policy = PlanningPolicy.v1()
    score = _score_candidate(
        assessment=assessment,
        cluster_id=42,
        existing_cluster_counts={42: 1},
        feature_coverage={},
        policy=policy,
        planning_intent=PlanningIntent.EXPLORATION,
        composite_score=0.60,
    )
    assert abs(score.cluster_coverage_need - 0.5) < 1e-9  # 1/(1+1)


def test_Q_opportunity_attractiveness_is_composite_score():
    """Q: opportunity_attractiveness = composite_score directly (no re-derivation)."""
    from app.intelligence.experiments.planning import PlanningIntent, PlanningPolicy
    from app.intelligence.experiments.planning_service import _score_candidate

    assessment = _make_assessment(1)
    policy = PlanningPolicy.v1()
    score = _score_candidate(
        assessment=assessment,
        cluster_id=None,
        existing_cluster_counts={},
        feature_coverage={},
        policy=policy,
        planning_intent=PlanningIntent.EXPLOITATION,
        composite_score=0.83,
    )
    assert abs(score.opportunity_attractiveness - 0.83) < 1e-9


def test_R_exploitation_value_no_double_confidence():
    """R: exploitation_value does NOT re-multiply by signal_confidence (no double-discount)."""
    from app.intelligence.experiments.planning import PlanningIntent, PlanningPolicy
    from app.intelligence.experiments.planning_service import _score_candidate

    policy = PlanningPolicy.v1()
    # Same composite_score, different signal_confidence → exploitation_value changes
    # only through evidence_strength (maturity), NOT through direct confidence re-weighting.
    base_score = 0.80
    assessment_hi_conf = _make_assessment(1, signal_confidence=0.90, signal_maturity="directional")
    assessment_lo_conf = _make_assessment(2, signal_confidence=0.30, signal_maturity="directional")

    hi = _score_candidate(
        assessment=assessment_hi_conf,
        cluster_id=None,
        existing_cluster_counts={},
        feature_coverage={},
        policy=policy,
        planning_intent=PlanningIntent.EXPLOITATION,
        composite_score=base_score,
    )
    lo = _score_candidate(
        assessment=assessment_lo_conf,
        cluster_id=None,
        existing_cluster_counts={},
        feature_coverage={},
        policy=policy,
        planning_intent=PlanningIntent.EXPLOITATION,
        composite_score=base_score,
    )
    # Same composite_score + same maturity → same exploitation_value (confidence doesn't affect it)
    assert abs(hi.exploitation_value - lo.exploitation_value) < 1e-9


def test_S_exploitation_value_bounded():
    """S: exploitation_value is in [0, 1]."""
    from app.intelligence.experiments.planning import PlanningIntent, PlanningPolicy
    from app.intelligence.experiments.planning_service import _score_candidate

    policy = PlanningPolicy.v1()
    assessment = _make_assessment(1)
    score = _score_candidate(
        assessment=assessment,
        cluster_id=None,
        existing_cluster_counts={},
        feature_coverage={},
        policy=policy,
        planning_intent=PlanningIntent.EXPLOITATION,
        composite_score=1.0,
    )
    assert score.exploitation_value is not None
    assert 0.0 <= score.exploitation_value <= 1.0


def test_T_exploration_value_bounded():
    """T: exploration_value is in [0, 1]."""
    from app.intelligence.experiments.planning import PlanningIntent, PlanningPolicy
    from app.intelligence.experiments.planning_service import _score_candidate

    policy = PlanningPolicy.v1()
    assessment = _make_assessment(1, signal_confidence=0.0)
    score = _score_candidate(
        assessment=assessment,
        cluster_id=None,
        existing_cluster_counts={},
        feature_coverage={},
        policy=policy,
        planning_intent=PlanningIntent.EXPLORATION,
        composite_score=None,
    )
    assert 0.0 <= score.exploration_value <= 1.0


def test_U_information_gain_bounded():
    """U: information_gain is in [0, 1]."""
    from app.intelligence.experiments.planning import PlanningIntent, PlanningPolicy
    from app.intelligence.experiments.planning_service import _score_candidate

    policy = PlanningPolicy.v1()
    assessment = _make_assessment(1)
    score = _score_candidate(
        assessment=assessment,
        cluster_id=100,
        existing_cluster_counts={},
        feature_coverage={},
        policy=policy,
        planning_intent=PlanningIntent.EXPLORATION,
        composite_score=0.60,
    )
    assert 0.0 <= score.information_gain <= 1.0


def test_V_information_gain_higher_for_untested_cluster():
    """V: information_gain is higher for an untested cluster vs a tested one."""
    from app.intelligence.experiments.planning import PlanningIntent, PlanningPolicy
    from app.intelligence.experiments.planning_service import _score_candidate

    policy = PlanningPolicy.v1()
    assessment = _make_assessment(1)

    untested = _score_candidate(
        assessment=assessment,
        cluster_id=1,
        existing_cluster_counts={},
        feature_coverage={},
        policy=policy,
        planning_intent=PlanningIntent.EXPLORATION,
        composite_score=0.60,
    )
    tested = _score_candidate(
        assessment=assessment,
        cluster_id=1,
        existing_cluster_counts={1: 3},
        feature_coverage={},
        policy=policy,
        planning_intent=PlanningIntent.EXPLORATION,
        composite_score=0.60,
    )
    assert untested.information_gain > tested.information_gain


# ── Tests W–X: Hypothesis sketch ──────────────────────────────────────────────


def test_W_hypothesis_sketch_exploration_contains_topic():
    """W: exploration hypothesis sketch references the opportunity topic."""
    from app.intelligence.experiments.planning import PlanningIntent
    from app.intelligence.experiments.planning_service import _build_hypothesis_sketch

    sketch = _build_hypothesis_sketch("meditation for beginners", PlanningIntent.EXPLORATION)
    assert "meditation for beginners" in sketch
    assert len(sketch) > 10


def test_X_hypothesis_sketch_exploitation_contains_topic():
    """X: exploitation hypothesis sketch references the opportunity topic."""
    from app.intelligence.experiments.planning import PlanningIntent
    from app.intelligence.experiments.planning_service import _build_hypothesis_sketch

    sketch = _build_hypothesis_sketch("stoic philosophy", PlanningIntent.EXPLOITATION)
    assert "stoic philosophy" in sketch
    assert len(sketch) > 10


# ── Tests Y–AK: Portfolio selection algorithm ─────────────────────────────────


def test_Y_empty_assessments_returns_empty_plan(db):
    """Y: build_portfolio_plan with empty assessments → 0 selected."""
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    plan = build_portfolio_plan(db, 1, [], dry_run=True)
    assert plan.selected_count == 0
    assert plan.deferred_count == 0
    assert plan.eligible_count == 0


def test_Z_all_ineligible_returns_empty_plan(db):
    """Z: build_portfolio_plan with all INELIGIBLE → 0 eligible, 0 selected."""
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=10)
    _insert_opportunity(db, 2, cluster_id=11)
    assessments = [
        _make_assessment(1, classification="ineligible"),
        _make_assessment(2, classification="ineligible"),
    ]
    plan = build_portfolio_plan(db, 1, assessments, dry_run=True)
    assert plan.eligible_count == 0
    assert plan.selected_count == 0


def test_AA_one_general_eligible_produces_selection(db):
    """AA: One GENERAL_ELIGIBLE assessment → at least 1 selected."""
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=10)
    plan = build_portfolio_plan(db, 1, [_make_assessment(1)], dry_run=True)
    assert plan.eligible_count == 1
    assert plan.selected_count >= 1


def test_AB_exploration_only_not_in_exploitation_pool(db):
    """AB: EXPLORATION_ONLY candidate is deferred from exploitation, selected for exploration."""
    from app.intelligence.experiments.planning import PlanningPolicy
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=10)
    policy = PlanningPolicy.v1()
    assessments = [_make_assessment(1, classification="exploration_only")]
    plan = build_portfolio_plan(db, 1, assessments, policy=policy, dry_run=True)
    # Should be in exploration pool, not exploitation
    selected = [d for d in plan.decisions if d.selected]
    assert all(d.pool_type == "exploration" for d in selected)


def test_AC_general_eligible_can_go_to_exploitation_pool(db):
    """AC: existing cluster + mature CHANNEL evidence → exploitation pool.

    Two preconditions, both deliberate:
      - a prior non-cancelled experiment, so the cluster is not 'new'
        (new clusters always explore first — Phase 16D.1.1)
      - mature evidence from this channel's own publications, because
        Phase 18D routes exploitation on what Orvella has measured rather
        than on market cluster maturity
    """
    from app.intelligence.experiments.planning import PlanningPolicy
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=10)
    _insert_opportunity_score(db, 1, composite=0.80)
    # Seed a prior experiment so cluster_is_new=False → exploitation is eligible
    _insert_prior_experiment(db, opp_id=1)
    _seed_channel_evidence(db, maturity="actionable")
    policy = PlanningPolicy.v1()
    assessments = [_make_assessment(1, signal_maturity="actionable", signal_confidence=0.90)]
    plan = build_portfolio_plan(db, 1, assessments, policy=policy, dry_run=True)
    selected = [d for d in plan.decisions if d.selected]
    exploitation_selected = [d for d in selected if d.pool_type == "exploitation"]
    assert len(exploitation_selected) >= 1


def test_AD_max_exploitation_slots_enforced(db):
    """AD: max_exploitation_slots=1 with 3 GENERAL_ELIGIBLE (existing clusters) → 1 exploitation.

    Requires prior non-cancelled experiments so clusters are not 'new'.
    New clusters always get EXPLORATION first (Phase 16D.1.1 fix).
    """
    from app.intelligence.experiments.planning import PlanningPolicy
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    for i in range(1, 4):
        _insert_opportunity(db, i, cluster_id=i * 10)
        _insert_opportunity_score(db, i, composite=0.70 + i * 0.01)
        # Seed prior experiments so all clusters are 'existing' → exploitation eligible
        _insert_prior_experiment(db, opp_id=i)
    _seed_channel_evidence(db, maturity="actionable")
    policy = PlanningPolicy.v1()
    assessments = [_make_assessment(i, signal_maturity="actionable") for i in range(1, 4)]
    plan = build_portfolio_plan(db, 1, assessments, policy=policy, dry_run=True)
    exploit_selected = [d for d in plan.decisions if d.selected and d.pool_type == "exploitation"]
    assert len(exploit_selected) == 1


def test_AE_max_exploration_slots_enforced(db):
    """AE: max_exploration_slots=2 with 5 candidates → at most 2 exploration selected."""
    from app.intelligence.experiments.planning import PlanningPolicy
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    for i in range(1, 6):
        _insert_opportunity(db, i, cluster_id=i * 10)
    policy = PlanningPolicy.v1()
    assessments = [_make_assessment(i, classification="exploration_only") for i in range(1, 6)]
    plan = build_portfolio_plan(db, 1, assessments, policy=policy, dry_run=True)
    explore_selected = [d for d in plan.decisions if d.selected and d.pool_type == "exploration"]
    assert len(explore_selected) <= policy.max_exploration_slots


def test_AF_requires_refresh_excluded_from_pools(db):
    """AF: REQUIRES_REFRESH is not counted as eligible and produces no selection."""
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=10)
    assessments = [_make_assessment(1, classification="requires_refresh")]
    plan = build_portfolio_plan(db, 1, assessments, dry_run=True)
    assert plan.eligible_count == 0
    assert plan.selected_count == 0


def test_AG_unresolved_excluded_from_pools(db):
    """AG: UNRESOLVED is not counted as eligible and produces no selection."""
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=10)
    assessments = [_make_assessment(1, classification="unresolved")]
    plan = build_portfolio_plan(db, 1, assessments, dry_run=True)
    assert plan.eligible_count == 0
    assert plan.selected_count == 0


def test_AH_cluster_share_guard_enforced(db):
    """AH: Cluster share guard — at most floor(0.4 * total_slots) from one cluster."""
    from app.intelligence.experiments.planning import PlanningPolicy
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    # 3 opportunities from same cluster (cluster_id=99)
    for i in range(1, 4):
        _insert_opportunity(db, i, cluster_id=99, topic=f"topic {i}")
    # 1 opportunity from different cluster (cluster_id=100)
    _insert_opportunity(db, 4, cluster_id=100, topic="other topic")

    policy = PlanningPolicy.v1()
    # total_slots = 1 + 2 = 3; max_from_cluster = max(1, floor(0.40 * 3)) = max(1, 1) = 1
    assessments = [_make_assessment(i, classification="exploration_only") for i in range(1, 5)]
    plan = build_portfolio_plan(db, 1, assessments, policy=policy, dry_run=True)

    total_slots = policy.max_exploitation_slots + policy.max_exploration_slots
    max_from_cluster = max(1, int(policy.max_cluster_share * total_slots))
    for cid in [99, 100]:
        selected_from_cluster = sum(
            1 for d in plan.decisions if d.selected and d.candidate.canonical_cluster_id == cid
        )
        assert selected_from_cluster <= max_from_cluster, (
            f"Cluster {cid}: {selected_from_cluster} selected, max allowed {max_from_cluster}"
        )


def test_AI_consecutive_cluster_guard_enforced(db):
    """AI: At most max_consecutive_same_cluster consecutive from same cluster."""
    from app.intelligence.experiments.planning import PlanningPolicy
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    # Arrange: 4 opportunities — first 3 from cluster 1, last from cluster 2
    for i in range(1, 5):
        cid = 1 if i <= 3 else 2
        _insert_opportunity(db, i, cluster_id=cid, topic=f"topic {i}")
    policy = PlanningPolicy(
        max_exploitation_slots=1, max_exploration_slots=3, max_cluster_share=1.0
    )
    # With max_cluster_share=1.0, share is never the block; only consecutive matters
    assessments = [_make_assessment(i, classification="exploration_only") for i in range(1, 5)]
    plan = build_portfolio_plan(db, 1, assessments, policy=policy, dry_run=True)

    selected = [d for d in plan.decisions if d.selected]
    consecutive = 0
    max_consecutive = 0
    prev_cid = None
    for d in selected:
        cid = d.candidate.canonical_cluster_id
        if cid == prev_cid:
            consecutive += 1
        else:
            consecutive = 1
        max_consecutive = max(max_consecutive, consecutive)
        prev_cid = cid
    assert max_consecutive <= policy.max_consecutive_same_cluster


def test_AJ_selected_count_matches_decisions(db):
    """AJ: plan.selected_count == count of selected decisions."""
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    for i in range(1, 5):
        _insert_opportunity(db, i, cluster_id=i)
    assessments = [_make_assessment(i) for i in range(1, 5)]
    plan = build_portfolio_plan(db, 1, assessments, dry_run=True)
    assert plan.selected_count == sum(1 for d in plan.decisions if d.selected)


def test_AK_deferred_count_matches_decisions(db):
    """AK: plan.deferred_count == count of deferred decisions."""
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    for i in range(1, 5):
        _insert_opportunity(db, i, cluster_id=i)
    assessments = [_make_assessment(i) for i in range(1, 5)]
    plan = build_portfolio_plan(db, 1, assessments, dry_run=True)
    assert plan.deferred_count == sum(1 for d in plan.decisions if not d.selected)


def test_AL_exploitation_pool_ranked_by_exploitation_value(db):
    """AL: Exploitation pool is ranked by exploitation_value DESC."""
    from app.intelligence.experiments.planning import PlanningPolicy
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    # Opp 1 has high composite, opp 2 has low — both actionable maturity
    _insert_opportunity(db, 1, cluster_id=1)
    _insert_opportunity(db, 2, cluster_id=2)
    _insert_opportunity_score(db, 1, composite=0.90)
    _insert_opportunity_score(db, 2, composite=0.30)
    policy = PlanningPolicy(max_exploitation_slots=2, max_exploration_slots=0)
    assessments = [
        _make_assessment(1, signal_maturity="actionable"),
        _make_assessment(2, signal_maturity="actionable"),
    ]
    plan = build_portfolio_plan(db, 1, assessments, policy=policy, dry_run=True)
    exploit_selected = sorted(
        [d for d in plan.decisions if d.selected and d.pool_type == "exploitation"],
        key=lambda d: d.rank_in_pool,
    )
    if len(exploit_selected) >= 2:
        first_ev = exploit_selected[0].candidate.score.exploitation_value
        second_ev = exploit_selected[1].candidate.score.exploitation_value
        assert first_ev >= second_ev


def test_AM_exploration_pool_ranked_by_exploration_value(db):
    """AM: Exploration pool is ranked by exploration_value DESC."""
    from app.intelligence.experiments.planning import PlanningPolicy
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    # Opp 1: low confidence (high uncertainty) → high exploration_value
    # Opp 2: high confidence (low uncertainty) → lower exploration_value
    _insert_opportunity(db, 1, cluster_id=1)
    _insert_opportunity(db, 2, cluster_id=2)
    policy = PlanningPolicy(max_exploitation_slots=0, max_exploration_slots=2)
    assessments = [
        _make_assessment(1, classification="exploration_only", signal_confidence=0.10),
        _make_assessment(2, classification="exploration_only", signal_confidence=0.95),
    ]
    plan = build_portfolio_plan(db, 1, assessments, policy=policy, dry_run=True)
    explore_selected = sorted(
        [d for d in plan.decisions if d.selected and d.pool_type == "exploration"],
        key=lambda d: d.rank_in_pool,
    )
    if len(explore_selected) >= 2:
        first_xv = explore_selected[0].candidate.score.exploration_value
        second_xv = explore_selected[1].candidate.score.exploration_value
        assert first_xv >= second_xv


def test_AN_tiebreak_lower_opportunity_id_wins(db):
    """AN: When scores are equal, lower opportunity_id is selected first."""
    from app.intelligence.experiments.planning import PlanningPolicy
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    _insert_opportunity(db, 10, cluster_id=10)
    _insert_opportunity(db, 20, cluster_id=20)
    # Same composite_score, same maturity → identical scores
    _insert_opportunity_score(db, 10, composite=0.70)
    _insert_opportunity_score(db, 20, composite=0.70)
    policy = PlanningPolicy(max_exploitation_slots=1, max_exploration_slots=0)
    assessments = [
        _make_assessment(10, signal_maturity="actionable"),
        _make_assessment(20, signal_maturity="actionable"),
    ]
    plan = build_portfolio_plan(db, 1, assessments, policy=policy, dry_run=True)
    selected = [d for d in plan.decisions if d.selected]
    if selected:
        assert selected[0].opportunity_id == 10  # lower id wins


# ── Tests AO–AV: Persistence ──────────────────────────────────────────────────


def test_AO_dry_run_no_rows_in_planning_runs(db):
    """AO: dry_run=True → no rows inserted in experiment_planning_runs."""
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=10)
    build_portfolio_plan(db, 1, [_make_assessment(1)], dry_run=True)
    count = db.execute("SELECT COUNT(*) FROM experiment_planning_runs").fetchone()[0]
    assert count == 0


def test_AP_not_dry_run_inserts_planning_run(db):
    """AP: dry_run=False → row inserted in experiment_planning_runs."""
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=10)
    plan = build_portfolio_plan(db, 1, [_make_assessment(1)], dry_run=False)
    count = db.execute(
        "SELECT COUNT(*) FROM experiment_planning_runs WHERE id = ?", (plan.run_id,)
    ).fetchone()[0]
    assert count == 1


def test_AQ_not_dry_run_inserts_candidate_scores(db):
    """AQ: dry_run=False → rows in experiment_candidate_scores."""
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=10)
    plan = build_portfolio_plan(db, 1, [_make_assessment(1)], dry_run=False)
    count = db.execute(
        "SELECT COUNT(*) FROM experiment_candidate_scores WHERE planning_run_id = ?",
        (plan.run_id,),
    ).fetchone()[0]
    assert count >= 1


def test_AR_not_dry_run_inserts_selection_decisions(db):
    """AR: dry_run=False → rows in experiment_selection_decisions."""
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=10)
    plan = build_portfolio_plan(db, 1, [_make_assessment(1)], dry_run=False)
    count = db.execute(
        "SELECT COUNT(*) FROM experiment_selection_decisions WHERE planning_run_id = ?",
        (plan.run_id,),
    ).fetchone()[0]
    assert count >= 1


def test_AS_input_hash_deterministic(db):
    """AS: Same inputs produce the same input_hash."""
    from app.intelligence.experiments.planning import PlanningPolicy
    from app.intelligence.experiments.planning_service import _plan_input_hash

    assessments = [_make_assessment(1), _make_assessment(2)]
    policy = PlanningPolicy.v1()
    h1 = _plan_input_hash(1, assessments, policy)
    h2 = _plan_input_hash(1, assessments, policy)
    assert h1 == h2


def test_AT_input_hash_changes_with_assessments(db):
    """AT: input_hash changes when assessments change."""
    from app.intelligence.experiments.planning import PlanningPolicy
    from app.intelligence.experiments.planning_service import _plan_input_hash

    policy = PlanningPolicy.v1()
    h1 = _plan_input_hash(1, [_make_assessment(1)], policy)
    h2 = _plan_input_hash(1, [_make_assessment(2)], policy)
    assert h1 != h2


def test_AU_is_validation_repeat_false_for_new_cluster(db):
    """AU: is_validation_repeat=False when cluster has no prior experiments."""
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=10)
    plan = build_portfolio_plan(db, 1, [_make_assessment(1)], dry_run=True)
    for d in plan.decisions:
        assert d.is_validation_repeat is False


def test_AV_is_validation_repeat_true_for_prior_cluster(db):
    """AV: is_validation_repeat=True when cluster has prior experiments in DB."""
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=99)
    # Prior experiment on opp 1 (cluster 99); status=completed avoids active-conflict gate
    db.execute(
        """INSERT INTO experiments
           (id, channel_id, opportunity_id, experiment_type, status, hypothesis, input_hash)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("exp-prior-1", 1, 1, "exploration", "completed", "prior hypothesis", "prior-hash"),
    )
    db.commit()
    plan = build_portfolio_plan(db, 1, [_make_assessment(1)], dry_run=True)
    for d in plan.decisions:
        assert d.is_validation_repeat is True


def test_AW_no_experiment_rows_created(db):
    """AW: build_portfolio_plan creates no rows in the experiments table."""
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=10)
    build_portfolio_plan(db, 1, [_make_assessment(1)], dry_run=False)
    count = db.execute("SELECT COUNT(*) FROM experiments").fetchone()[0]
    assert count == 0


def test_AX_no_network_calls_contractual():
    """AX: planning_service module does not import requests/httpx/google-auth at module level."""
    import sys

    # Ensure it's freshly loaded
    mod_name = "app.intelligence.experiments.planning_service"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    import app.intelligence.experiments.planning_service  # noqa: F401

    # Module loads without triggering network imports
    assert "requests" not in dir(app.intelligence.experiments.planning_service)
    assert "httpx" not in dir(app.intelligence.experiments.planning_service)


# ── Tests AY–BH: Scoring and candidate details ───────────────────────────────


def test_AY_max_one_treatment_factor(db):
    """AY: Each candidate has at most 1 treatment factor."""
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    for i in range(1, 4):
        _insert_opportunity(db, i, cluster_id=i)
    assessments = [_make_assessment(i) for i in range(1, 4)]
    plan = build_portfolio_plan(db, 1, assessments, dry_run=True)
    for d in plan.decisions:
        treatment = [
            f for f in d.candidate.intended_treatment_factors if f.factor_role == "treatment"
        ]
        assert len(treatment) <= 1


def test_AZ_feature_change_risk_low_for_safe_factor(db):
    """AZ: feature_change_risk = 'low' when treatment factor is in SAFE_CONTROLLABLE_FACTORS."""
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=10)
    plan = build_portfolio_plan(db, 1, [_make_assessment(1)], dry_run=True)
    for d in plan.decisions:
        if d.candidate.intended_treatment_factors:
            assert d.candidate.feature_change_risk == "low"


def test_BA_final_score_is_exploitation_value_for_exploitation_pool(db):
    """BA: final_planning_score equals exploitation_value for exploitation pool."""
    from app.intelligence.experiments.planning import PlanningPolicy
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=10)
    _insert_opportunity_score(db, 1, composite=0.80)
    policy = PlanningPolicy.v1()
    assessments = [_make_assessment(1, signal_maturity="actionable")]
    plan = build_portfolio_plan(db, 1, assessments, policy=policy, dry_run=True)
    for d in plan.decisions:
        if d.pool_type == "exploitation":
            score = d.candidate.score
            assert abs(score.final_planning_score - (score.exploitation_value or 0.0)) < 1e-6


def test_BB_final_score_is_exploration_value_for_exploration_pool(db):
    """BB: final_planning_score equals exploration_value for exploration pool."""
    from app.intelligence.experiments.planning import PlanningPolicy
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=10)
    policy = PlanningPolicy(max_exploitation_slots=0, max_exploration_slots=2)
    assessments = [_make_assessment(1, classification="exploration_only")]
    plan = build_portfolio_plan(db, 1, assessments, policy=policy, dry_run=True)
    for d in plan.decisions:
        if d.pool_type == "exploration":
            score = d.candidate.score
            assert abs(score.final_planning_score - score.exploration_value) < 1e-6


def test_BC_ineligible_not_scored(db):
    """BC: INELIGIBLE candidates are filtered before scoring (eligible_count=0)."""
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=10)
    plan = build_portfolio_plan(
        db, 1, [_make_assessment(1, classification="ineligible")], dry_run=True
    )
    assert plan.eligible_count == 0
    assert plan.decisions == []


def test_BD_unresolved_not_scored(db):
    """BD: UNRESOLVED candidates are excluded from eligible set."""
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=10)
    plan = build_portfolio_plan(
        db, 1, [_make_assessment(1, classification="unresolved")], dry_run=True
    )
    assert plan.eligible_count == 0


def test_BE_requires_refresh_not_scored(db):
    """BE: REQUIRES_REFRESH candidates excluded from eligible set."""
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=10)
    plan = build_portfolio_plan(
        db, 1, [_make_assessment(1, classification="requires_refresh")], dry_run=True
    )
    assert plan.eligible_count == 0


def test_BF_no_experiment_rows_created_full_run(db):
    """BF: Full non-dry-run build_portfolio_plan still creates no experiment rows."""
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    for i in range(1, 4):
        _insert_opportunity(db, i, cluster_id=i)
    assessments = [_make_assessment(i) for i in range(1, 4)]
    build_portfolio_plan(db, 1, assessments, dry_run=False)
    count = db.execute("SELECT COUNT(*) FROM experiments").fetchone()[0]
    assert count == 0


def test_BG_policy_snapshot_json_matches_policy(db):
    """BG: plan.policy_snapshot_json matches policy.to_json()."""
    from app.intelligence.experiments.planning import PlanningPolicy
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=10)
    policy = PlanningPolicy.v1()
    plan = build_portfolio_plan(db, 1, [_make_assessment(1)], policy=policy, dry_run=True)
    assert plan.policy_snapshot_json == policy.to_json()


def test_BH_cold_start_zero_publications_evidence_strength_zero(db):
    """BH: Cold start (0 publications) → internal_evidence_strength = 0.0
    (insufficient maturity)."""
    from app.intelligence.experiments.planning import PlanningIntent, PlanningPolicy
    from app.intelligence.experiments.planning_service import _score_candidate

    assessment = _make_assessment(1, signal_maturity="insufficient")
    policy = PlanningPolicy.v1()
    score = _score_candidate(
        assessment=assessment,
        cluster_id=None,
        existing_cluster_counts={},
        feature_coverage={},
        policy=policy,
        planning_intent=PlanningIntent.EXPLORATION,
        composite_score=None,
    )
    assert score.internal_evidence_strength == 0.0


# ── Tests BI–BM: Database schema ─────────────────────────────────────────────


def test_BI_fresh_db_has_schema_version_38(tmp_path):
    """BI: Fresh open_db produces SCHEMA_VERSION=40."""
    from app.core.database import SCHEMA_VERSION, _get_version, open_db

    conn = open_db(tmp_path / "test.db")
    assert SCHEMA_VERSION == 51
    assert _get_version(conn) == 51
    conn.close()


def test_BJ_v37_db_migrates_to_v38(tmp_path):
    """BJ: A DB stuck at version 37 migrates to 40 on next open_db."""
    from app.core.database import _get_version, _set_version, open_db

    # Create a v40 DB then roll back to v37 to simulate an old schema
    conn = open_db(tmp_path / "test.db")
    # Drop planning tables to simulate v37 state
    conn.execute("DROP TABLE IF EXISTS experiment_selection_decisions")
    conn.execute("DROP TABLE IF EXISTS experiment_candidate_scores")
    conn.execute("DROP TABLE IF EXISTS experiment_planning_runs")
    _set_version(conn, 37)
    conn.commit()
    conn.close()

    # Re-open: should migrate to 40
    conn2 = open_db(tmp_path / "test.db")
    assert _get_version(conn2) == 51
    # Tables should exist
    tables = {
        row[0]
        for row in conn2.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "experiment_planning_runs" in tables
    assert "experiment_candidate_scores" in tables
    assert "experiment_selection_decisions" in tables
    conn2.close()


def test_BK_experiment_planning_runs_table_exists(db):
    """BK: experiment_planning_runs table exists in schema v38."""
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='experiment_planning_runs'"
    ).fetchone()
    assert row is not None


def test_BL_experiment_candidate_scores_table_exists(db):
    """BL: experiment_candidate_scores table exists in schema v38."""
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='experiment_candidate_scores'"
    ).fetchone()
    assert row is not None


def test_BM_experiment_selection_decisions_table_exists(db):
    """BM: experiment_selection_decisions table exists in schema v38."""
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='experiment_selection_decisions'"
    ).fetchone()
    assert row is not None


# ── Tests BN–BT: Final contracts and integration ──────────────────────────────


def test_BN_import_planning_service():
    """BN: planning_service module importable without error."""
    from app.intelligence.experiments import planning_service  # noqa: F401

    assert hasattr(planning_service, "build_portfolio_plan")


def test_BO_cli_has_plan_command():
    """BO: experiment_app CLI has 'plan' command registered."""
    from app.intelligence.experiments.cli import experiment_app

    command_names = {cmd.name for cmd in experiment_app.registered_commands}
    assert "plan" in command_names


def test_BP_schema_version_constant_is_38():
    """BP: SCHEMA_VERSION constant equals 38."""
    from app.core.database import SCHEMA_VERSION

    assert SCHEMA_VERSION == 51


def test_BQ_run_id_is_uuid_string(db):
    """BQ: plan.run_id is a valid UUID string."""
    import uuid

    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=10)
    plan = build_portfolio_plan(db, 1, [_make_assessment(1)], dry_run=True)
    parsed = uuid.UUID(plan.run_id)
    assert str(parsed) == plan.run_id


def test_BR_end_to_end_mixed_assessments_correct_counts(db):
    """BR: End-to-end with 3 mixed assessments → plan with correct counts."""
    from app.intelligence.experiments.planning import PlanningPolicy
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=1)
    _insert_opportunity(db, 2, cluster_id=2)
    _insert_opportunity(db, 3, cluster_id=3)
    policy = PlanningPolicy.v1()
    assessments = [
        _make_assessment(1, classification="general_eligible"),
        _make_assessment(2, classification="exploration_only"),
        _make_assessment(3, classification="ineligible"),
    ]
    plan = build_portfolio_plan(db, 1, assessments, policy=policy, dry_run=True)
    assert plan.eligible_count == 2
    assert plan.general_eligible_count == 1
    assert plan.exploration_only_count == 1
    assert plan.selected_count + plan.deferred_count == 2


def test_BS_eligible_count_sums_general_and_exploration_only(db):
    """BS: plan.eligible_count == general_eligible_count + exploration_only_count."""
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    for i in range(1, 5):
        _insert_opportunity(db, i, cluster_id=i)
    assessments = [
        _make_assessment(1, classification="general_eligible"),
        _make_assessment(2, classification="general_eligible"),
        _make_assessment(3, classification="exploration_only"),
        _make_assessment(4, classification="ineligible"),
    ]
    plan = build_portfolio_plan(db, 1, assessments, dry_run=True)
    assert plan.eligible_count == plan.general_eligible_count + plan.exploration_only_count
    assert plan.eligible_count == 3


def test_BT_deferred_decisions_have_deferral_reason(db):
    """BT: All deferred decisions have a non-empty deferral_reason."""
    from app.intelligence.experiments.planning import PlanningPolicy
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    # 5 candidates in 5 different clusters but only 3 slots total
    for i in range(1, 6):
        _insert_opportunity(db, i, cluster_id=i)
    policy = PlanningPolicy.v1()  # 1 exploitation + 2 exploration = 3 slots
    assessments = [_make_assessment(i, classification="exploration_only") for i in range(1, 6)]
    plan = build_portfolio_plan(db, 1, assessments, policy=policy, dry_run=True)
    deferred = [d for d in plan.decisions if not d.selected]
    for d in deferred:
        assert d.deferral_reason is not None
        assert len(d.deferral_reason) > 0


# ── Tests BU–BZ: Phase 14D.1 hardening ───────────────────────────────────────


def test_BU_no_treatment_factor_for_untested_cluster_exploration(db):
    """BU: Pure market exploration (untested cluster + EXPLORATION intent)
    proposes no treatment factor.

    Rationale: simultaneously varying the topic cluster AND a production feature
    (e.g. speaking rate) confounds the experiment — results cannot be attributed
    to either variable.  Only tested-cluster or exploitation candidates may carry
    a treatment factor.
    """
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=10)
    # No prior experiments on cluster 10 → cluster_is_new=True
    assessments = [
        _make_assessment(1, classification="exploration_only", signal_maturity="insufficient")
    ]
    plan = build_portfolio_plan(db, 1, assessments, dry_run=True)
    for d in plan.decisions:
        treatment = [
            f for f in d.candidate.intended_treatment_factors if f.factor_role == "treatment"
        ]
        assert len(treatment) == 0, (
            f"Pure market exploration (untested cluster) should have 0 treatment "
            f"factors, got {treatment}"
        )


def test_BV_treatment_factor_allowed_for_tested_cluster_exploration(db):
    """BV: Feature exploration on a TESTED cluster may carry a treatment factor."""
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=10)
    # Insert a prior experiment on cluster 10 → cluster is tested
    db.execute(
        "INSERT INTO experiments "
        "(id, channel_id, opportunity_id, experiment_type, status, hypothesis, input_hash) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("exp-1", 1, 1, "exploration", "completed", "prior", "prior-hash"),
    )
    db.commit()
    assessments = [
        _make_assessment(1, classification="exploration_only", signal_maturity="exploratory")
    ]
    plan = build_portfolio_plan(db, 1, assessments, dry_run=True)
    for d in plan.decisions:
        treatment = [
            f for f in d.candidate.intended_treatment_factors if f.factor_role == "treatment"
        ]
        # Tested cluster → feature treatment is allowed (1 factor)
        assert len(treatment) <= 1


def test_BW_treatment_factor_allowed_for_exploitation_candidate(db):
    """BW: Exploitation candidate (known cluster, GENERAL_ELIGIBLE) may carry a treatment factor."""
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=10)
    _insert_opportunity_score(db, 1, composite=0.80)
    # actionable maturity → exploitation intent; cluster 10 untested in history
    # BUT exploitation intent bypasses the pure-market-exploration guard
    assessments = [_make_assessment(1, signal_maturity="actionable", signal_confidence=0.90)]
    plan = build_portfolio_plan(db, 1, assessments, dry_run=True)
    exploit_decisions = [d for d in plan.decisions if d.pool_type == "exploitation"]
    for d in exploit_decisions:
        treatment = [
            f for f in d.candidate.intended_treatment_factors if f.factor_role == "treatment"
        ]
        assert len(treatment) <= 1  # may carry a treatment factor


def test_BX_exploration_only_candidates_excluded_from_exploitation_pool_high_score(db):
    """BX: EXPLORATION_ONLY with very high composite score cannot enter exploitation pool."""
    from app.intelligence.experiments.planning import PlanningPolicy
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=1)
    _insert_opportunity(db, 2, cluster_id=2)
    _insert_opportunity_score(db, 1, composite=0.99)
    _insert_opportunity_score(db, 2, composite=0.40)
    policy = PlanningPolicy.v1()
    assessments = [
        _make_assessment(1, classification="exploration_only", signal_maturity="actionable"),
        _make_assessment(2, classification="general_eligible", signal_maturity="directional"),
    ]
    plan = build_portfolio_plan(db, 1, assessments, policy=policy, dry_run=True)
    exploit_selected = [d for d in plan.decisions if d.selected and d.pool_type == "exploitation"]
    for d in exploit_selected:
        assert d.candidate.eligibility_classification != "exploration_only", (
            "EXPLORATION_ONLY must never appear in exploitation pool regardless of score"
        )


def test_BY_input_hash_changes_with_policy_version(db):
    """BY: input_hash changes when policy version changes."""
    from app.intelligence.experiments.planning import PlanningPolicy
    from app.intelligence.experiments.planning_service import _plan_input_hash

    a = _make_assessment(1)
    p1 = PlanningPolicy.v1()
    p2 = PlanningPolicy(version="2.0.0")
    h1 = _plan_input_hash(1, [a], p1)
    h2 = _plan_input_hash(1, [a], p2)
    assert h1 != h2


def test_BZ_cold_start_all_exploration_only_selects_two_not_three(db):
    """BZ: Cold start with all exploration_only and 3 slots selects at most 2 (exploit slot unused).

    Documents the known behavior: exploration_only candidates cannot fill the exploitation slot.
    With V1 policy (1 exploit + 2 explore) and all exploration_only candidates, max 2 selected.
    """
    from app.intelligence.experiments.planning import PlanningPolicy
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    for i in range(1, 4):
        _insert_opportunity(db, i, cluster_id=i)
    policy = PlanningPolicy.v1()  # 1 exploit + 2 explore = 3 slots
    assessments = [
        _make_assessment(i, classification="exploration_only", signal_maturity="insufficient")
        for i in range(1, 4)
    ]
    plan = build_portfolio_plan(db, 1, assessments, policy=policy, dry_run=True)
    # exploration_only cannot fill exploitation slot → at most 2 selected
    assert plan.selected_count <= policy.max_exploration_slots
    exploit_selected = [d for d in plan.decisions if d.selected and d.pool_type == "exploitation"]
    assert len(exploit_selected) == 0
