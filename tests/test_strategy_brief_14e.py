"""Phase 14E — Experiment Strategy Brief tests.

Groups A–CC: 75+ tests covering brief creation, planning intent mapping,
treatment factor specs, controlled baselines, confounding risk, hypothesis
generation, content constraints, idempotency, eligibility recheck,
schema integrity, and factor autonomy classification.

Safety invariants:
- No LLM calls
- No YouTube calls
- No Experiment row creation
- No autonomous treatment value assignment
- No content generation
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from app.core.database import SCHEMA_VERSION, open_db
from app.intelligence.experiments.brief_service import (
    BriefCreationError,
    FactorAutonomy,
    _compute_confounding_risk,
    _map_brief_planning_intent,
    create_strategy_brief,
    get_brief_for_decision,
    get_factor_autonomy,
    get_strategy_brief,
    list_briefs_for_channel,
)
from app.intelligence.experiments.planning import PlanningPolicy
from app.intelligence.experiments.strategy_brief import (
    BriefPlanningIntent,
    ConfoundingRisk,
)
from app.learning.constants import (
    NARRATION_PACE_SPEAKING_RATE_MAX,
    NARRATION_PACE_SPEAKING_RATE_MIN,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def db(tmp_path):
    conn = open_db(tmp_path / "test.db")
    yield conn
    conn.close()


_profile_id_cache: dict[int, int] = {}


def _insert_channel(db: sqlite3.Connection, channel_id: int = 1) -> None:
    db.execute(
        """INSERT OR IGNORE INTO channels
           (id, platform, channel_name, platform_channel_id)
           VALUES (?, 'youtube', 'Test Channel', ?)""",
        (channel_id, f"UC{channel_id}test"),
    )


def _insert_channel_profile(
    db: sqlite3.Connection,
    channel_id: int = 1,
    primary_niche: str = "Python tutorials",
    excluded_topics: list | None = None,
) -> int:
    excluded = json.dumps(excluded_topics or [])
    # Get max existing version for this channel
    row = db.execute(
        "SELECT MAX(version) AS v FROM channel_profile_versions WHERE channel_id = ?",
        (channel_id,),
    ).fetchone()
    next_version = (row["v"] or 0) + 1
    # Deactivate existing active profiles
    db.execute(
        "UPDATE channel_profile_versions SET status = 'superseded' "
        "WHERE channel_id = ? AND status = 'active'",
        (channel_id,),
    )
    r = db.execute(
        """INSERT INTO channel_profile_versions
           (channel_id, version, primary_niche, secondary_niches_json, excluded_topics_json,
            brand_voice, content_style, audience_description, status, created_by)
           VALUES (?, ?, ?, '[]', ?, 'conversational', 'explainer', 'developers', 'active', 'test')
           RETURNING id""",
        (channel_id, next_version, primary_niche, excluded),
    ).fetchone()
    db.execute(
        "UPDATE channels SET current_profile_version_id = ? WHERE id = ?",
        (r["id"], channel_id),
    )
    return r["id"]


def _get_or_create_profile_version(db: sqlite3.Connection, channel_id: int) -> int:
    """Get the active profile version for a channel, creating one if needed."""
    row = db.execute(
        "SELECT id FROM channel_profile_versions "
        "WHERE channel_id = ? AND status = 'active' LIMIT 1",
        (channel_id,),
    ).fetchone()
    if row:
        return row["id"]
    return _insert_channel_profile(db, channel_id)


def _insert_discovery_run(db: sqlite3.Connection, channel_id: int = 1) -> int:
    profile_version_id = _get_or_create_profile_version(db, channel_id)
    r = db.execute(
        """INSERT INTO discovery_runs
           (channel_id, profile_version_id, adapter_name, status, started_at)
           VALUES (?, ?, 'manual', 'completed', '2026-08-22T00:00:00')
           RETURNING id""",
        (channel_id, profile_version_id),
    ).fetchone()
    return r["id"]


def _insert_cluster(db: sqlite3.Connection, cluster_id: int = 99) -> None:
    db.execute(
        """INSERT OR IGNORE INTO market_canonical_clusters
           (id, canonical_label, normalized_label, semantic_fingerprint)
           VALUES (?, 'test cluster', 'test cluster', 'fp-test')""",
        (cluster_id,),
    )


def _insert_opportunity(
    db: sqlite3.Connection,
    opp_id: int,
    channel_id: int = 1,
    cluster_id: int | None = 99,
    topic: str = "test topic",
) -> None:
    run_id = _insert_discovery_run(db, channel_id)
    if cluster_id is not None:
        _insert_cluster(db, cluster_id)
    db.execute(
        """INSERT OR IGNORE INTO opportunities
           (id, channel_id, discovery_run_id, normalized_topic, raw_topic, canonical_cluster_id,
            created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, '2026-08-22T00:00:00', '2026-08-22T00:00:00')""",
        (opp_id, channel_id, run_id, topic, topic, cluster_id),
    )


def _insert_planning_run(
    db: sqlite3.Connection, run_id: str = "run-1", channel_id: int = 1
) -> None:
    db.execute(
        """INSERT INTO experiment_planning_runs
           (id, channel_id, status, eligible_count, exploration_only_count,
            general_eligible_count, selected_count, deferred_count, input_hash)
           VALUES (?, ?, 'completed', 1, 0, 1, 1, 0, 'hash-1')""",
        (run_id, channel_id),
    )


def _insert_candidate_score(
    db: sqlite3.Connection,
    *,
    run_id: str = "run-1",
    opp_id: int = 1,
    channel_id: int = 1,
    cluster_id: int | None = 99,
    planning_intent: str = "exploration",
    experiment_type: str = "exploration",
    eligibility: str = "general_eligible",
    treatment_factors_json: str = "[]",
    final_score: float = 0.5,
) -> int:
    r = db.execute(
        """INSERT INTO experiment_candidate_scores
           (planning_run_id, opportunity_id, channel_id, canonical_cluster_id,
            eligibility_classification, planning_intent, experiment_type,
            primary_target_metric, primary_metric_direction, hypothesis_sketch,
            intended_treatment_factors_json, controlled_factors_json,
            feature_change_risk, final_planning_score, input_hash)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'average_view_percentage', 'higher_is_better',
                   'test hypothesis', ?, '[]', 'low', ?, 'hash-cand')
           RETURNING id""",
        (
            run_id,
            opp_id,
            channel_id,
            cluster_id,
            eligibility,
            planning_intent,
            experiment_type,
            treatment_factors_json,
            final_score,
        ),
    ).fetchone()
    return r["id"]


def _insert_selection_decision(
    db: sqlite3.Connection,
    *,
    run_id: str = "run-1",
    candidate_score_id: int,
    opp_id: int = 1,
    selected: int = 1,
    is_validation_repeat: int = 0,
) -> int:
    r = db.execute(
        """INSERT INTO experiment_selection_decisions
           (planning_run_id, candidate_score_id, opportunity_id, selected,
            rank_in_pool, pool_type, selection_reason, is_validation_repeat)
           VALUES (?, ?, ?, ?, 1, 'exploration', 'top scored', ?)
           RETURNING id""",
        (run_id, candidate_score_id, opp_id, selected, is_validation_repeat),
    ).fetchone()
    return r["id"]


def _build_selected_decision(
    db: sqlite3.Connection,
    *,
    opp_id: int = 1,
    channel_id: int = 1,
    cluster_id: int | None = 99,
    planning_intent: str = "exploration",
    experiment_type: str = "exploration",
    treatment_factors_json: str = "[]",
    is_validation_repeat: int = 0,
    eligibility: str = "general_eligible",
) -> int:
    """Insert a full chain and return selection_decision_id."""
    _insert_channel(db, channel_id)
    _insert_opportunity(db, opp_id, channel_id, cluster_id)
    _insert_planning_run(db, channel_id=channel_id)
    cs_id = _insert_candidate_score(
        db,
        opp_id=opp_id,
        channel_id=channel_id,
        cluster_id=cluster_id,
        planning_intent=planning_intent,
        experiment_type=experiment_type,
        eligibility=eligibility,
        treatment_factors_json=treatment_factors_json,
    )
    sd_id = _insert_selection_decision(
        db,
        candidate_score_id=cs_id,
        opp_id=opp_id,
        selected=1,
        is_validation_repeat=is_validation_repeat,
    )
    db.commit()
    return sd_id


# ── A: Schema version ─────────────────────────────────────────────────────────


def test_A_schema_version_is_39(db):
    assert SCHEMA_VERSION == 51


def test_A2_strategy_brief_table_exists(db):
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='experiment_strategy_briefs'"
    ).fetchone()
    assert row is not None


def test_A3_idea_candidates_table_exists(db):
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='experiment_idea_candidates'"
    ).fetchone()
    assert row is not None


def test_A4_strategy_brief_has_required_columns(db):
    cols = {
        r["name"] for r in db.execute("PRAGMA table_info(experiment_strategy_briefs)").fetchall()
    }
    for col in [
        "id",
        "channel_id",
        "planning_run_id",
        "selection_decision_id",
        "opportunity_id",
        "canonical_cluster_id",
        "channel_profile_version_id",
        "brief_planning_intent",
        "experiment_type",
        "market_theme",
        "canonical_topic",
        "strategic_reason",
        "information_gain_reason",
        "hypothesis",
        "target_metric",
        "target_direction",
        "treatment_factors_json",
        "controlled_factors_json",
        "content_constraints_json",
        "confounding_risk",
        "policy_version",
        "eligibility_classification",
        "score_decomposition_json",
        "brief_hash",
        "status",
        "created_at",
    ]:
        assert col in cols, f"Missing column: {col}"


def test_A5_brief_hash_is_unique(db):
    """UNIQUE constraint on brief_hash is enforced: two briefs with same hash cannot coexist."""
    # Create two separate decisions (two opportunities, one brief each)
    sd1 = _build_selected_decision(db, opp_id=1, cluster_id=97)
    # Second opportunity on second channel so no uniqueness violation on selection_decision
    _insert_channel(db, 2)
    _insert_opportunity(db, 2, channel_id=2, cluster_id=98)
    _insert_planning_run(db, "run-2", channel_id=2)
    cs2 = _insert_candidate_score(db, run_id="run-2", opp_id=2, channel_id=2, cluster_id=98)
    sd2 = _insert_selection_decision(db, run_id="run-2", candidate_score_id=cs2, opp_id=2)
    db.commit()

    b1 = create_strategy_brief(db, sd1)
    db.commit()
    # Manually try to insert a brief with the same hash as b1 — must fail
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """INSERT INTO experiment_strategy_briefs
               (id, channel_id, planning_run_id, selection_decision_id, opportunity_id,
                brief_planning_intent, experiment_type, hypothesis, target_metric,
                target_direction, brief_hash, status)
               VALUES ('dup', ?, ?, ?, ?, 'market_exploration', 'exploration',
                       'h', 'metric', 'higher_is_better', ?, 'pending_approval')""",
            (2, "run-2", sd2, 2, b1.brief_hash),
        )


# ── B: Brief identity ─────────────────────────────────────────────────────────


def test_B_brief_has_channel_and_run_identity(db):
    sd_id = _build_selected_decision(db)
    brief = create_strategy_brief(db, sd_id)
    db.commit()

    assert brief.channel_id == 1
    assert brief.planning_run_id == "run-1"
    assert brief.selection_decision_id == sd_id
    assert brief.opportunity_id == 1
    assert brief.id is not None and len(brief.id) == 36  # UUID format


def test_B2_brief_has_status_pending_approval(db):
    sd_id = _build_selected_decision(db)
    brief = create_strategy_brief(db, sd_id)
    db.commit()
    assert brief.status == "pending_approval"


def test_B3_brief_persisted_in_db(db):
    sd_id = _build_selected_decision(db)
    brief = create_strategy_brief(db, sd_id)
    db.commit()

    row = db.execute(
        "SELECT * FROM experiment_strategy_briefs WHERE id = ?", (brief.id,)
    ).fetchone()
    assert row is not None
    assert row["channel_id"] == 1


# ── C: Canonical market identity ──────────────────────────────────────────────


def test_C_canonical_topic_loaded_from_opportunity(db):
    _insert_channel(db)
    _insert_opportunity(db, 1, topic="cooking techniques")
    _insert_planning_run(db)
    cs_id = _insert_candidate_score(db, opp_id=1)
    sd_id = _insert_selection_decision(db, candidate_score_id=cs_id)
    db.commit()

    brief = create_strategy_brief(db, sd_id)
    db.commit()
    assert brief.canonical_topic == "cooking techniques"


def test_C2_market_theme_equals_canonical_topic(db):
    sd_id = _build_selected_decision(db)
    brief = create_strategy_brief(db, sd_id)
    assert brief.market_theme == brief.canonical_topic


def test_C3_canonical_cluster_id_preserved(db):
    sd_id = _build_selected_decision(db, cluster_id=42)
    brief = create_strategy_brief(db, sd_id)
    assert brief.canonical_cluster_id == 42


# ── D: Opportunity lineage ────────────────────────────────────────────────────


def test_D_selection_decision_id_on_brief(db):
    sd_id = _build_selected_decision(db)
    brief = create_strategy_brief(db, sd_id)
    assert brief.selection_decision_id == sd_id


def test_D2_planning_run_id_on_brief(db):
    sd_id = _build_selected_decision(db)
    brief = create_strategy_brief(db, sd_id)
    assert brief.planning_run_id == "run-1"


def test_D3_opportunity_id_on_brief(db):
    sd_id = _build_selected_decision(db, opp_id=7)
    brief = create_strategy_brief(db, sd_id)
    assert brief.opportunity_id == 7


# ── E: Planning intent mapping ────────────────────────────────────────────────


def test_E_exploration_new_cluster_maps_to_market_exploration():
    intent = _map_brief_planning_intent(
        "exploration", cluster_is_new=True, is_validation_repeat=False
    )
    assert intent == BriefPlanningIntent.MARKET_EXPLORATION


def test_E2_exploration_tested_cluster_maps_to_feature_exploration():
    intent = _map_brief_planning_intent(
        "exploration", cluster_is_new=False, is_validation_repeat=False
    )
    assert intent == BriefPlanningIntent.FEATURE_EXPLORATION


def test_E3_exploitation_maps_to_exploitation():
    intent = _map_brief_planning_intent(
        "exploitation", cluster_is_new=False, is_validation_repeat=False
    )
    assert intent == BriefPlanningIntent.EXPLOITATION


def test_E4_exploration_with_validation_repeat_maps_to_validation():
    intent = _map_brief_planning_intent(
        "exploration", cluster_is_new=False, is_validation_repeat=True
    )
    assert intent == BriefPlanningIntent.VALIDATION


def test_E5_new_cluster_exploration_no_validation_repeat():
    # is_validation_repeat=True on new cluster is inconsistent, but VALIDATION wins when set
    intent = _map_brief_planning_intent(
        "exploration", cluster_is_new=True, is_validation_repeat=True
    )
    assert intent == BriefPlanningIntent.VALIDATION


def test_E6_exploitation_ignores_validation_repeat():
    # exploitation intent always maps to EXPLOITATION regardless of is_validation_repeat
    intent = _map_brief_planning_intent(
        "exploitation", cluster_is_new=False, is_validation_repeat=True
    )
    assert intent == BriefPlanningIntent.EXPLOITATION


# ── F: Pure market exploration brief ─────────────────────────────────────────


def test_F_market_exploration_brief_has_zero_treatment_factors(db):
    """Untested cluster + exploration intent = 0 treatment factors."""
    sd_id = _build_selected_decision(
        db,
        planning_intent="exploration",
        treatment_factors_json="[]",
        cluster_id=99,
    )
    brief = create_strategy_brief(db, sd_id)
    assert brief.brief_planning_intent == BriefPlanningIntent.MARKET_EXPLORATION
    assert len(brief.treatment_factors) == 0


def test_F2_market_exploration_confounding_risk_is_low(db):
    sd_id = _build_selected_decision(db, treatment_factors_json="[]")
    brief = create_strategy_brief(db, sd_id)
    assert brief.confounding_risk == ConfoundingRisk.LOW


def test_F3_market_exploration_hypothesis_contains_cluster_signal(db):
    sd_id = _build_selected_decision(db, treatment_factors_json="[]")
    brief = create_strategy_brief(db, sd_id)
    assert "signal" in brief.hypothesis or "baseline" in brief.hypothesis


# ── G: Feature exploration brief ─────────────────────────────────────────────


def test_G_feature_exploration_brief_has_one_treatment_factor(db):
    """Tested cluster + exploration + 1 treatment factor = FEATURE_EXPLORATION."""
    # Insert prior experiment to make cluster "tested"
    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=99, topic="ml optimization")
    _insert_planning_run(db)
    cs_id = _insert_candidate_score(
        db,
        opp_id=1,
        planning_intent="exploration",
        treatment_factors_json=json.dumps(
            [{"factor_name": "has_hook", "factor_role": "treatment"}]
        ),
    )
    sd_id = _insert_selection_decision(db, candidate_score_id=cs_id)
    # Insert prior experiment so cluster is "tested"
    db.execute(
        """INSERT INTO experiments
           (id, channel_id, opportunity_id, experiment_type, status, hypothesis, input_hash)
           VALUES ('prior-exp', 1, 1, 'exploration', 'completed', 'prior', 'prior-hash')"""
    )
    db.commit()

    brief = create_strategy_brief(db, sd_id)
    assert brief.brief_planning_intent == BriefPlanningIntent.FEATURE_EXPLORATION
    assert len(brief.treatment_factors) == 1
    assert brief.treatment_factors[0].factor_name == "has_hook"


def test_G2_feature_exploration_confounding_risk_is_low(db):
    """One factor on tested cluster = low confound."""
    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=99)
    _insert_planning_run(db)
    cs_id = _insert_candidate_score(
        db,
        treatment_factors_json=json.dumps(
            [{"factor_name": "has_hook", "factor_role": "treatment"}]
        ),
        planning_intent="exploration",
    )
    sd_id = _insert_selection_decision(db, candidate_score_id=cs_id)
    db.execute(
        "INSERT INTO experiments (id, channel_id, opportunity_id, experiment_type, "
        "status, hypothesis, input_hash)"
        " VALUES ('prev', 1, 1, 'exploration', 'completed', 'prev hyp', 'prev-hash')"
    )
    db.commit()
    brief = create_strategy_brief(db, sd_id)
    assert brief.confounding_risk == ConfoundingRisk.LOW


# ── H: Safe factor source of truth ───────────────────────────────────────────


def test_H_planning_py_imports_bounds_from_learning_constants():
    """planning.py must import narration bounds from learning.constants, not hardcode them."""
    from app.intelligence.experiments.planning import SAFE_CONTROLLABLE_FACTORS

    spec = SAFE_CONTROLLABLE_FACTORS["narration_speaking_rate"]
    assert spec.safe_range_min == NARRATION_PACE_SPEAKING_RATE_MIN
    assert spec.safe_range_max == NARRATION_PACE_SPEAKING_RATE_MAX


def test_H2_speaking_rate_bounds_match_learning_constants():
    """The bounds in SAFE_CONTROLLABLE_FACTORS must exactly match learning.constants."""
    from app.intelligence.experiments.planning import SAFE_CONTROLLABLE_FACTORS

    spec = SAFE_CONTROLLABLE_FACTORS["narration_speaking_rate"]
    assert spec.safe_range_min == 0.7
    assert spec.safe_range_max == 1.5


def test_H3_no_hardcoded_bounds_in_planning_module():
    """Verify that 0.7 and 1.5 are not hardcoded directly in planning.py."""
    import inspect

    from app.intelligence.experiments import planning

    src = inspect.getsource(planning)
    # The bounds should appear only via imported names, not as literals
    # Check that the literals aren't assigned to safe_range_min/max directly
    import re

    # safe_range_min=0.7 or safe_range_max=1.5 should not appear as literals
    assert not re.search(r"safe_range_min\s*=\s*0\.7", src), (
        "0.7 is hardcoded in planning.py instead of imported from learning.constants"
    )
    assert not re.search(r"safe_range_max\s*=\s*1\.5", src), (
        "1.5 is hardcoded in planning.py instead of imported from learning.constants"
    )


# ── I: Factor autonomy classification ────────────────────────────────────────


def test_I_narration_speaking_rate_is_autonomously_assignable():
    assert (
        get_factor_autonomy("narration_speaking_rate") == FactorAutonomy.AUTONOMOUSLY_ASSIGNABLE_NOW
    )


def test_I2_has_hook_is_autonomously_assignable():
    assert get_factor_autonomy("has_hook") == FactorAutonomy.AUTONOMOUSLY_ASSIGNABLE_NOW


def test_I3_has_cta_is_autonomously_assignable():
    assert get_factor_autonomy("has_cta") == FactorAutonomy.AUTONOMOUSLY_ASSIGNABLE_NOW


def test_I4_render_caption_burn_in_is_autonomously_assignable():
    assert (
        get_factor_autonomy("render_caption_burn_in") == FactorAutonomy.AUTONOMOUSLY_ASSIGNABLE_NOW
    )


def test_I5_script_format_is_manual_only():
    assert get_factor_autonomy("script_format") == FactorAutonomy.MANUAL_ONLY


def test_I6_narration_voice_id_is_manual_only():
    assert get_factor_autonomy("narration_voice_id") == FactorAutonomy.MANUAL_ONLY


def test_I7_publish_day_of_week_is_manual_only():
    assert get_factor_autonomy("publish_day_of_week") == FactorAutonomy.MANUAL_ONLY


def test_I8_unknown_factor_defaults_to_manual_only():
    assert get_factor_autonomy("unknown_factor_xyz") == FactorAutonomy.MANUAL_ONLY


# ── J: Controlled factors ─────────────────────────────────────────────────────


def test_J_controlled_factors_contain_all_non_treatment_safe_factors(db):
    """All safe factors not used as treatment must appear in controlled_factors."""
    from app.intelligence.experiments.planning import SAFE_CONTROLLABLE_FACTORS

    sd_id = _build_selected_decision(
        db,
        treatment_factors_json=json.dumps(
            [{"factor_name": "has_hook", "factor_role": "treatment"}]
        ),
        planning_intent="exploration",
    )
    # Insert prior to make cluster tested
    db.execute(
        "INSERT INTO experiments (id, channel_id, opportunity_id, experiment_type, "
        "status, hypothesis, input_hash)"
        " VALUES ('prev', 1, 1, 'exploration', 'completed', 'h', 'h1')"
    )
    db.commit()
    brief = create_strategy_brief(db, sd_id)

    controlled_names = {cf.factor_name for cf in brief.controlled_factors}
    for name in SAFE_CONTROLLABLE_FACTORS:
        if name != "has_hook":  # has_hook is treatment
            assert name in controlled_names, f"Missing controlled factor: {name}"


def test_J2_treatment_factor_excluded_from_controlled(db):
    sd_id = _build_selected_decision(
        db,
        treatment_factors_json=json.dumps(
            [{"factor_name": "narration_speaking_rate", "factor_role": "treatment"}]
        ),
        planning_intent="exploration",
    )
    db.execute(
        "INSERT INTO experiments (id, channel_id, opportunity_id, experiment_type, "
        "status, hypothesis, input_hash)"
        " VALUES ('prev', 1, 1, 'exploration', 'completed', 'h', 'h1')"
    )
    db.commit()
    brief = create_strategy_brief(db, sd_id)
    controlled_names = [cf.factor_name for cf in brief.controlled_factors]
    assert "narration_speaking_rate" not in controlled_names


# ── K: Controlled factor baseline source ──────────────────────────────────────


def test_K_narration_speaking_rate_baseline_source_is_voice_profile(db):
    sd_id = _build_selected_decision(db, treatment_factors_json="[]")
    brief = create_strategy_brief(db, sd_id)
    sr_cf = next(
        (cf for cf in brief.controlled_factors if cf.factor_name == "narration_speaking_rate"),
        None,
    )
    assert sr_cf is not None
    assert sr_cf.baseline_source == "voice_profile"


def test_K2_narration_speaking_rate_baseline_none_when_no_narration_history(db):
    """When no narration runs exist, baseline_value is None (unknown)."""
    sd_id = _build_selected_decision(db, treatment_factors_json="[]")
    brief = create_strategy_brief(db, sd_id)
    sr_cf = next(
        cf for cf in brief.controlled_factors if cf.factor_name == "narration_speaking_rate"
    )
    assert sr_cf.baseline_value is None  # no voice profile data seeded


# ── L: Confounding risk ───────────────────────────────────────────────────────


def test_L_zero_treatment_factors_is_low_risk():
    risk = _compute_confounding_risk(BriefPlanningIntent.MARKET_EXPLORATION, 0)
    assert risk == ConfoundingRisk.LOW


def test_L2_one_treatment_feature_exploration_is_low_risk():
    risk = _compute_confounding_risk(BriefPlanningIntent.FEATURE_EXPLORATION, 1)
    assert risk == ConfoundingRisk.LOW


def test_L3_one_treatment_exploitation_is_low_risk():
    risk = _compute_confounding_risk(BriefPlanningIntent.EXPLOITATION, 1)
    assert risk == ConfoundingRisk.LOW


def test_L4_one_treatment_market_exploration_is_high_risk():
    """New cluster + treatment factor = confound — should not happen after 14D.1 fix."""
    risk = _compute_confounding_risk(BriefPlanningIntent.MARKET_EXPLORATION, 1)
    assert risk == ConfoundingRisk.HIGH


def test_L5_two_treatment_factors_is_high_risk():
    risk = _compute_confounding_risk(BriefPlanningIntent.FEATURE_EXPLORATION, 2)
    assert risk == ConfoundingRisk.HIGH


# ── M: Hypothesis generation ──────────────────────────────────────────────────


def test_M_market_exploration_hypothesis_template(db):
    sd_id = _build_selected_decision(db, treatment_factors_json="[]")
    brief = create_strategy_brief(db, sd_id)
    # Should reference the topic and "baseline data" or "signal"
    assert "baseline" in brief.hypothesis.lower() or "signal" in brief.hypothesis.lower()


def test_M2_feature_exploration_hypothesis_mentions_treatment_factor(db):
    _insert_channel(db)
    _insert_opportunity(db, 1)
    _insert_planning_run(db)
    cs_id = _insert_candidate_score(
        db,
        treatment_factors_json=json.dumps(
            [{"factor_name": "has_hook", "factor_role": "treatment"}]
        ),
        planning_intent="exploration",
    )
    sd_id = _insert_selection_decision(db, candidate_score_id=cs_id)
    db.execute(
        "INSERT INTO experiments (id, channel_id, opportunity_id, experiment_type, "
        "status, hypothesis, input_hash)"
        " VALUES ('prev', 1, 1, 'exploration', 'completed', 'h', 'h')"
    )
    db.commit()

    brief = create_strategy_brief(db, sd_id)
    assert "has_hook" in brief.hypothesis


def test_M3_exploitation_hypothesis_mentions_improve(db):
    _insert_channel(db)
    _insert_opportunity(db, 1)
    _insert_planning_run(db)
    cs_id = _insert_candidate_score(
        db,
        planning_intent="exploitation",
        experiment_type="exploitation",
        eligibility="general_eligible",
        treatment_factors_json=json.dumps([{"factor_name": "has_cta", "factor_role": "treatment"}]),
    )
    sd_id = _insert_selection_decision(db, candidate_score_id=cs_id)
    db.commit()
    brief = create_strategy_brief(db, sd_id)
    assert "improve" in brief.hypothesis.lower() or "exploit" in brief.hypothesis.lower()


def test_M4_validation_hypothesis_mentions_confirm_or_revise(db):
    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=99)
    _insert_planning_run(db)
    cs_id = _insert_candidate_score(
        db,
        planning_intent="exploration",
        treatment_factors_json="[]",
    )
    sd_id = _insert_selection_decision(db, candidate_score_id=cs_id, is_validation_repeat=1)
    db.execute(
        "INSERT INTO experiments (id, channel_id, opportunity_id, experiment_type, "
        "status, hypothesis, input_hash)"
        " VALUES ('prev', 1, 1, 'exploration', 'completed', 'h', 'h')"
    )
    db.commit()
    brief = create_strategy_brief(db, sd_id)
    assert brief.brief_planning_intent == BriefPlanningIntent.VALIDATION
    assert "confirm" in brief.hypothesis.lower() or "revise" in brief.hypothesis.lower()


def test_M5_hypothesis_is_deterministic(db):
    """Same inputs → same hypothesis string."""
    sd_id = _build_selected_decision(db, treatment_factors_json="[]")
    brief1 = create_strategy_brief(db, sd_id)
    db.commit()

    # Retrieve again from DB
    brief2 = get_brief_for_decision(db, sd_id)
    assert brief1.hypothesis == brief2.hypothesis


# ── N: Target metric ──────────────────────────────────────────────────────────


def test_N_target_metric_comes_from_candidate_score(db):
    sd_id = _build_selected_decision(db, treatment_factors_json="[]")
    brief = create_strategy_brief(db, sd_id)
    assert brief.target_metric == "average_view_percentage"


def test_N2_target_direction_comes_from_candidate_score(db):
    sd_id = _build_selected_decision(db, treatment_factors_json="[]")
    brief = create_strategy_brief(db, sd_id)
    assert brief.target_direction == "higher_is_better"


# ── O: Content constraints ────────────────────────────────────────────────────


def test_O_content_constraints_loaded_from_channel_profile(db):
    _insert_channel(db)
    _insert_channel_profile(db, excluded_topics=["politics", "religion"])
    _insert_opportunity(db, 1)
    _insert_planning_run(db)
    cs_id = _insert_candidate_score(db)
    sd_id = _insert_selection_decision(db, candidate_score_id=cs_id)
    db.commit()

    brief = create_strategy_brief(db, sd_id)
    assert "politics" in brief.content_constraints.excluded_topics
    assert "religion" in brief.content_constraints.excluded_topics


def test_O2_primary_niche_on_content_constraints(db):
    _insert_channel(db)
    _insert_channel_profile(db, primary_niche="machine learning")
    _insert_opportunity(db, 1)
    _insert_planning_run(db)
    cs_id = _insert_candidate_score(db)
    sd_id = _insert_selection_decision(db, candidate_score_id=cs_id)
    db.commit()

    brief = create_strategy_brief(db, sd_id)
    assert brief.content_constraints.primary_niche == "machine learning"


def test_O3_content_constraints_empty_when_no_profile(db):
    """Brief can be created even with no active channel profile; constraints are empty."""
    sd_id = _build_selected_decision(db)
    # Supersede the profile created by the FK chain so no active profile is found
    db.execute("UPDATE channel_profile_versions SET status = 'superseded' WHERE status = 'active'")
    db.commit()
    brief = create_strategy_brief(db, sd_id)
    assert brief.content_constraints.excluded_topics == ()
    assert brief.content_constraints.primary_niche == ""


# ── P: Treatment factor spec ──────────────────────────────────────────────────


def test_P_treatment_factor_has_no_intended_value(db):
    """intended_value must always be None — operators decide."""
    _insert_channel(db)
    _insert_opportunity(db, 1)
    _insert_planning_run(db)
    cs_id = _insert_candidate_score(
        db,
        treatment_factors_json=json.dumps(
            [{"factor_name": "has_hook", "factor_role": "treatment"}]
        ),
        planning_intent="exploration",
    )
    sd_id = _insert_selection_decision(db, candidate_score_id=cs_id)
    db.execute(
        "INSERT INTO experiments (id, channel_id, opportunity_id, experiment_type, "
        "status, hypothesis, input_hash)"
        " VALUES ('prev', 1, 1, 'exploration', 'completed', 'h', 'h')"
    )
    db.commit()

    brief = create_strategy_brief(db, sd_id)
    for tf in brief.treatment_factors:
        assert tf.intended_value is None, "intended_value must never be assigned autonomously"


def test_P2_numeric_factor_has_safe_range(db):
    _insert_channel(db)
    _insert_opportunity(db, 1)
    _insert_planning_run(db)
    cs_id = _insert_candidate_score(
        db,
        treatment_factors_json=json.dumps(
            [{"factor_name": "narration_speaking_rate", "factor_role": "treatment"}]
        ),
        planning_intent="exploration",
    )
    sd_id = _insert_selection_decision(db, candidate_score_id=cs_id)
    db.execute(
        "INSERT INTO experiments (id, channel_id, opportunity_id, experiment_type, "
        "status, hypothesis, input_hash)"
        " VALUES ('prev', 1, 1, 'exploration', 'completed', 'h', 'h')"
    )
    db.commit()

    brief = create_strategy_brief(db, sd_id)
    tf = brief.treatment_factors[0]
    assert tf.safe_range_min == NARRATION_PACE_SPEAKING_RATE_MIN
    assert tf.safe_range_max == NARRATION_PACE_SPEAKING_RATE_MAX


def test_P3_boolean_factor_has_safe_values(db):
    _insert_channel(db)
    _insert_opportunity(db, 1)
    _insert_planning_run(db)
    cs_id = _insert_candidate_score(
        db,
        treatment_factors_json=json.dumps([{"factor_name": "has_cta", "factor_role": "treatment"}]),
        planning_intent="exploration",
    )
    sd_id = _insert_selection_decision(db, candidate_score_id=cs_id)
    db.execute(
        "INSERT INTO experiments (id, channel_id, opportunity_id, experiment_type, "
        "status, hypothesis, input_hash)"
        " VALUES ('prev', 1, 1, 'exploration', 'completed', 'h', 'h')"
    )
    db.commit()

    brief = create_strategy_brief(db, sd_id)
    tf = brief.treatment_factors[0]
    assert tf.safe_values is not None
    assert set(tf.safe_values) == {"true", "false"}


def test_P4_manual_only_categorical_factor_in_treatment(db):
    _insert_channel(db)
    _insert_opportunity(db, 1)
    _insert_planning_run(db)
    cs_id = _insert_candidate_score(
        db,
        treatment_factors_json=json.dumps(
            [{"factor_name": "script_format", "factor_role": "treatment"}]
        ),
        planning_intent="exploration",
    )
    sd_id = _insert_selection_decision(db, candidate_score_id=cs_id)
    db.execute(
        "INSERT INTO experiments (id, channel_id, opportunity_id, experiment_type, "
        "status, hypothesis, input_hash)"
        " VALUES ('prev', 1, 1, 'exploration', 'completed', 'h', 'h')"
    )
    db.commit()

    brief = create_strategy_brief(db, sd_id)
    tf = brief.treatment_factors[0]
    assert tf.autonomy == FactorAutonomy.MANUAL_ONLY
    assert tf.intended_value is None


# ── Q: Brief idempotency ──────────────────────────────────────────────────────


def test_Q_create_strategy_brief_is_idempotent(db):
    sd_id = _build_selected_decision(db)
    brief1 = create_strategy_brief(db, sd_id)
    db.commit()
    brief2 = create_strategy_brief(db, sd_id)
    db.commit()

    assert brief1.id == brief2.id
    assert brief1.brief_hash == brief2.brief_hash


def test_Q2_only_one_brief_per_decision_in_db(db):
    sd_id = _build_selected_decision(db)
    create_strategy_brief(db, sd_id)
    db.commit()
    create_strategy_brief(db, sd_id)
    db.commit()

    rows = db.execute(
        "SELECT COUNT(*) AS cnt FROM experiment_strategy_briefs WHERE selection_decision_id = ?",
        (sd_id,),
    ).fetchone()
    assert rows["cnt"] == 1


def test_Q3_brief_hash_is_stable_on_repeated_calls(db):
    sd_id = _build_selected_decision(db)
    b1 = create_strategy_brief(db, sd_id)
    db.commit()
    b2 = get_brief_for_decision(db, sd_id)
    assert b1.brief_hash == b2.brief_hash


# ── R: Eligibility recheck ────────────────────────────────────────────────────


def test_R_deferred_decision_raises_brief_creation_error(db):
    _insert_channel(db)
    _insert_opportunity(db, 1)
    _insert_planning_run(db)
    cs_id = _insert_candidate_score(db)
    sd_id = _insert_selection_decision(db, candidate_score_id=cs_id, selected=0)
    db.commit()

    with pytest.raises(BriefCreationError, match="not selected"):
        create_strategy_brief(db, sd_id)


def test_R2_ineligible_stored_classification_raises_brief_creation_error(db):
    """If candidate score stores INELIGIBLE, brief creation must be blocked.

    Phase 14E uses the stored eligibility_classification from experiment_candidate_scores
    (the last known eligibility at planning time). A live re-assessment is not performed
    here because it may invoke LLMs.
    """
    _insert_channel(db)
    _insert_opportunity(db, 1)
    _insert_planning_run(db)
    cs_id = _insert_candidate_score(db, eligibility="ineligible")
    sd_id = _insert_selection_decision(db, candidate_score_id=cs_id)
    db.commit()

    with pytest.raises(BriefCreationError, match="ineligible"):
        create_strategy_brief(db, sd_id)


def test_R3_stale_stored_classification_raises_brief_creation_error(db):
    _insert_channel(db)
    _insert_opportunity(db, 1)
    _insert_planning_run(db)
    cs_id = _insert_candidate_score(db, eligibility="stale")
    sd_id = _insert_selection_decision(db, candidate_score_id=cs_id)
    db.commit()

    with pytest.raises(BriefCreationError, match="stale"):
        create_strategy_brief(db, sd_id)


def test_R4_general_eligible_passes_stored_recheck(db):
    sd_id = _build_selected_decision(db, eligibility="general_eligible")
    brief = create_strategy_brief(db, sd_id)
    assert brief is not None


def test_R5_nonexistent_decision_raises_brief_creation_error(db):
    _insert_channel(db)
    db.commit()
    with pytest.raises(BriefCreationError, match="not found"):
        create_strategy_brief(db, 9999)


# ── S: Brief hash ─────────────────────────────────────────────────────────────


def test_S_brief_hash_is_deterministic(db):
    sd_id = _build_selected_decision(db)
    b = create_strategy_brief(db, sd_id)
    db.commit()

    # Recreate via get and confirm hash is same
    b2 = get_strategy_brief(db, b.id)
    assert b.brief_hash == b2.brief_hash


def test_S2_different_decisions_have_different_hashes(db):
    # Two channels, two decisions
    _insert_channel(db, 1)
    _insert_channel(db, 2)
    _insert_opportunity(db, 1, channel_id=1)
    _insert_opportunity(db, 2, channel_id=2)
    _insert_planning_run(db, "run-1", channel_id=1)
    _insert_planning_run(db, "run-2", channel_id=2)

    cs1 = _insert_candidate_score(db, run_id="run-1", opp_id=1, channel_id=1)
    cs2 = _insert_candidate_score(db, run_id="run-2", opp_id=2, channel_id=2)
    sd1 = _insert_selection_decision(db, run_id="run-1", candidate_score_id=cs1, opp_id=1)
    sd2 = _insert_selection_decision(db, run_id="run-2", candidate_score_id=cs2, opp_id=2)
    db.commit()

    b1 = create_strategy_brief(db, sd1)
    b2 = create_strategy_brief(db, sd2)
    assert b1.brief_hash != b2.brief_hash


def test_S3_brief_hash_excludes_timestamp():
    """Brief hash must be stable: it must not incorporate any timestamp."""
    from app.intelligence.experiments.brief_service import _compute_brief_hash
    from app.intelligence.experiments.strategy_brief import BriefPlanningIntent

    h1 = _compute_brief_hash(
        channel_id=1,
        selection_decision_id=5,
        brief_planning_intent=BriefPlanningIntent.MARKET_EXPLORATION,
        treatment_factors=[],
        target_metric="views",
        policy_version="1.0.0",
    )
    h2 = _compute_brief_hash(
        channel_id=1,
        selection_decision_id=5,
        brief_planning_intent=BriefPlanningIntent.MARKET_EXPLORATION,
        treatment_factors=[],
        target_metric="views",
        policy_version="1.0.0",
    )
    assert h1 == h2


def test_S4_policy_version_change_changes_hash():
    from app.intelligence.experiments.brief_service import _compute_brief_hash
    from app.intelligence.experiments.strategy_brief import BriefPlanningIntent

    h1 = _compute_brief_hash(
        channel_id=1,
        selection_decision_id=5,
        brief_planning_intent=BriefPlanningIntent.MARKET_EXPLORATION,
        treatment_factors=[],
        target_metric="views",
        policy_version="1.0.0",
    )
    h2 = _compute_brief_hash(
        channel_id=1,
        selection_decision_id=5,
        brief_planning_intent=BriefPlanningIntent.MARKET_EXPLORATION,
        treatment_factors=[],
        target_metric="views",
        policy_version="2.0.0",
    )
    assert h1 != h2


# ── T: Score decomposition ────────────────────────────────────────────────────


def test_T_score_decomposition_json_persisted(db):
    sd_id = _build_selected_decision(db)
    brief = create_strategy_brief(db, sd_id)
    db.commit()

    decomp = json.loads(brief.score_decomposition_json)
    assert "final_planning_score" in decomp


def test_T2_score_decomposition_contains_all_components(db):
    sd_id = _build_selected_decision(db)
    brief = create_strategy_brief(db, sd_id)
    for key in [
        "final_planning_score",
        "opportunity_attractiveness",
        "exploitation_value",
        "exploration_value",
        "information_gain",
        "internal_evidence_strength",
        "uncertainty",
        "cluster_coverage_need",
    ]:
        decomp = json.loads(brief.score_decomposition_json)
        assert key in decomp, f"Missing key in score_decomposition: {key}"


# ── U: Retrieval functions ────────────────────────────────────────────────────


def test_U_get_strategy_brief_by_id(db):
    sd_id = _build_selected_decision(db)
    brief = create_strategy_brief(db, sd_id)
    db.commit()

    retrieved = get_strategy_brief(db, brief.id)
    assert retrieved is not None
    assert retrieved.id == brief.id


def test_U2_get_brief_for_decision(db):
    sd_id = _build_selected_decision(db)
    create_strategy_brief(db, sd_id)
    db.commit()

    retrieved = get_brief_for_decision(db, sd_id)
    assert retrieved is not None
    assert retrieved.selection_decision_id == sd_id


def test_U3_get_strategy_brief_returns_none_for_missing_id(db):
    result = get_strategy_brief(db, "nonexistent-id")
    assert result is None


def test_U4_get_brief_for_decision_returns_none_when_not_created(db):
    result = get_brief_for_decision(db, 9999)
    assert result is None


def test_U5_list_briefs_for_channel(db):
    sd_id = _build_selected_decision(db)
    create_strategy_brief(db, sd_id)
    db.commit()

    briefs = list_briefs_for_channel(db, 1)
    assert len(briefs) == 1


def test_U6_list_briefs_filtered_by_status(db):
    sd_id = _build_selected_decision(db)
    create_strategy_brief(db, sd_id)
    db.commit()

    pending = list_briefs_for_channel(db, 1, status="pending_approval")
    approved = list_briefs_for_channel(db, 1, status="approved")
    assert len(pending) == 1
    assert len(approved) == 0


# ── V: No LLM / No production / No experiment creation ──────────────────────


def test_V_no_experiment_rows_created(db):
    """create_strategy_brief must NOT insert into the experiments table."""
    sd_id = _build_selected_decision(db)
    create_strategy_brief(db, sd_id)
    db.commit()

    count = db.execute("SELECT COUNT(*) AS cnt FROM experiments").fetchone()["cnt"]
    assert count == 0


def test_V2_no_youtube_api_call_required(db):
    """Brief creation must complete without any live API call."""
    # This test just verifies the function returns without raising — if it
    # tried to call YouTube it would fail in the test environment.
    sd_id = _build_selected_decision(db)
    brief = create_strategy_brief(db, sd_id)
    assert brief is not None


def test_V3_channel_profile_version_id_captured_when_available(db):
    _insert_channel(db)
    profile_id = _insert_channel_profile(db)
    _insert_opportunity(db, 1)
    _insert_planning_run(db)
    cs_id = _insert_candidate_score(db)
    sd_id = _insert_selection_decision(db, candidate_score_id=cs_id)
    db.commit()

    brief = create_strategy_brief(db, sd_id)
    assert brief.channel_profile_version_id == profile_id


# ── W: Policy version ─────────────────────────────────────────────────────────


def test_W_default_policy_version_on_brief(db):
    sd_id = _build_selected_decision(db)
    brief = create_strategy_brief(db, sd_id, policy=PlanningPolicy())
    assert brief.policy_version == "1.0.0"


def test_W2_custom_policy_version_recorded(db):
    sd_id = _build_selected_decision(db)
    policy = PlanningPolicy(version="2.0.0")
    brief = create_strategy_brief(db, sd_id, policy=policy)
    assert brief.policy_version == "2.0.0"


# ── X: Eligibility classification provenance ─────────────────────────────────


def test_X_eligibility_classification_from_candidate_score(db):
    sd_id = _build_selected_decision(db, eligibility="general_eligible")
    brief = create_strategy_brief(db, sd_id)
    assert brief.eligibility_classification == "general_eligible"


def test_X2_exploration_only_classification_preserved(db):
    sd_id = _build_selected_decision(db, eligibility="exploration_only")
    brief = create_strategy_brief(db, sd_id)
    assert brief.eligibility_classification == "exploration_only"


# ── Y: Strategic reason and information gain reason ──────────────────────────


def test_Y_strategic_reason_not_empty(db):
    sd_id = _build_selected_decision(db)
    brief = create_strategy_brief(db, sd_id)
    assert brief.strategic_reason.strip() != ""


def test_Y2_information_gain_reason_not_empty(db):
    sd_id = _build_selected_decision(db)
    brief = create_strategy_brief(db, sd_id)
    assert brief.information_gain_reason.strip() != ""


def test_Y3_market_exploration_strategic_reason_mentions_no_prior(db):
    sd_id = _build_selected_decision(db, treatment_factors_json="[]")
    brief = create_strategy_brief(db, sd_id)
    # market_exploration should mention it's an untested cluster
    assert "prior" in brief.strategic_reason.lower() or "baseline" in brief.strategic_reason.lower()


# ── Z: Schema v39 integrity ───────────────────────────────────────────────────


def test_Z_schema_version_is_39(db):
    row = db.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    assert row["version"] == 51


def test_Z2_strategy_briefs_fk_on_planning_runs(db):
    """experiment_strategy_briefs.planning_run_id references experiment_planning_runs."""
    # Attempting to insert a brief with a non-existent planning_run_id must fail
    sd_id = _build_selected_decision(db)
    create_strategy_brief(db, sd_id)
    db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """INSERT INTO experiment_strategy_briefs
               (id, channel_id, planning_run_id, selection_decision_id, opportunity_id,
                brief_planning_intent, experiment_type, hypothesis, target_metric,
                target_direction, brief_hash, status)
               VALUES ('bfk', 1, 'nonexistent-run-xyz', 9999, 1,
                       'market_exploration', 'exploration', 'h', 'm', 'higher_is_better',
                       'hash-fk-unique', 'pending_approval')"""
        )


def test_Z3_strategy_brief_status_check_constraint(db):
    """DB rejects invalid status values."""
    sd_id = _build_selected_decision(db)
    b = create_strategy_brief(db, sd_id)
    db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "UPDATE experiment_strategy_briefs SET status = 'invalid_status' WHERE id = ?",
            (b.id,),
        )


def test_Z4_brief_planning_intent_check_constraint(db):
    """DB rejects invalid brief_planning_intent values."""
    sd_id = _build_selected_decision(db)
    b = create_strategy_brief(db, sd_id)
    db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "UPDATE experiment_strategy_briefs "
            "SET brief_planning_intent = 'invalid_intent' WHERE id = ?",
            (b.id,),
        )


# ── AA: Experiment type mapping ───────────────────────────────────────────────


def test_AA_exploration_planning_intent_sets_experiment_type_exploration(db):
    sd_id = _build_selected_decision(
        db, planning_intent="exploration", experiment_type="exploration"
    )
    brief = create_strategy_brief(db, sd_id)
    assert brief.experiment_type == "exploration"


def test_AA2_exploitation_planning_intent_sets_experiment_type_exploitation(db):
    sd_id = _build_selected_decision(
        db, planning_intent="exploitation", experiment_type="exploitation"
    )
    brief = create_strategy_brief(db, sd_id)
    assert brief.experiment_type == "exploitation"


# ── BB: Idea candidates table ─────────────────────────────────────────────────


def test_BB_idea_candidates_table_has_required_columns(db):
    cols = {
        r["name"] for r in db.execute("PRAGMA table_info(experiment_idea_candidates)").fetchall()
    }
    for col in [
        "id",
        "brief_id",
        "channel_id",
        "title_sketch",
        "hook_sketch",
        "content_angle",
        "constraint_flags_json",
        "semantic_fit_score",
        "is_duplicate",
        "selection_rank",
        "status",
        "created_at",
    ]:
        assert col in cols, f"Missing column: {col}"


def test_BB2_idea_candidate_status_check_constraint(db):
    sd_id = _build_selected_decision(db)
    b = create_strategy_brief(db, sd_id)
    db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO experiment_idea_candidates (brief_id, channel_id, status) "
            "VALUES (?, 1, 'invalid_status')",
            (b.id,),
        )


# ── CC: Round-trip brief round-trip ──────────────────────────────────────────


def test_CC_full_round_trip_create_and_retrieve(db):
    """End-to-end: create brief, retrieve from DB, all fields match."""
    _insert_channel(db)
    _insert_channel_profile(db, primary_niche="data science", excluded_topics=["crypto"])
    _insert_opportunity(db, 1, topic="pandas dataframes")
    _insert_planning_run(db)
    cs_id = _insert_candidate_score(
        db,
        treatment_factors_json=json.dumps(
            [{"factor_name": "has_hook", "factor_role": "treatment"}]
        ),
        planning_intent="exploration",
    )
    sd_id = _insert_selection_decision(db, candidate_score_id=cs_id)
    db.execute(
        "INSERT INTO experiments (id, channel_id, opportunity_id, experiment_type, "
        "status, hypothesis, input_hash)"
        " VALUES ('prev', 1, 1, 'exploration', 'completed', 'h', 'h')"
    )
    db.commit()

    created = create_strategy_brief(db, sd_id)
    db.commit()

    retrieved = get_strategy_brief(db, created.id)
    assert retrieved is not None
    assert retrieved.channel_id == created.channel_id
    assert retrieved.opportunity_id == created.opportunity_id
    assert retrieved.canonical_topic == "pandas dataframes"
    assert retrieved.brief_planning_intent == BriefPlanningIntent.FEATURE_EXPLORATION
    assert retrieved.confounding_risk == ConfoundingRisk.LOW
    assert len(retrieved.treatment_factors) == 1
    assert retrieved.treatment_factors[0].factor_name == "has_hook"
    assert retrieved.treatment_factors[0].intended_value is None
    assert retrieved.content_constraints.primary_niche == "data science"
    assert "crypto" in retrieved.content_constraints.excluded_topics
    assert retrieved.status == "pending_approval"
    assert retrieved.brief_hash == created.brief_hash
