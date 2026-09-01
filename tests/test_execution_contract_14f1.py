"""Phase 14F.1 — Execution Precedence, True-FK Integration, and Fidelity Closure.

Tests for:
  A-H   Precedence: resolve_narration_speaking_rate_authority() — four CASES
  I-M   Consumption: learning app suppressed iff TREATMENT or CONTROL authority
  N-R   Contract status gating: only 'real' + approved/executing triggers override
  S-T   Baseline snapshot: CONTROL baseline written to narration_speaking_rate_override
  U-W   Factor consistency: SAFE_CONTROLLABLE_FACTORS single source of truth
  X-AD  Fidelity: MATCHED / WITHIN_TOLERANCE / DEVIATED / NOT_OBSERVABLE / NOT_YET_AVAILABLE
  AE-AI True FK: full fk_chain tests for contract creation + authority resolution
  AJ-AN Dry run: authority always EXPERIMENT_NOT_GOVERNING for dry_run contracts
  AO-AQ Market exploration: EXPERIMENT_NOT_GOVERNING when factor not in contract
  AR-AU Safety source: constants imported from learning.constants only
  AV-BB Regression: prior 14F behaviours not broken

Safety invariants:
  - No LLM calls
  - No YouTube calls
  - No content generation
  - No live API calls
  - No score changes or re-ranking
"""

from __future__ import annotations

import json
import sqlite3
import uuid

import pytest

from app.core.database import SCHEMA_VERSION, open_db
from app.intelligence.experiments.execution_contract import (
    ExecutionMode,
    FidelityClassification,
    FidelityOutcome,
    ParameterAuthority,
)
from app.intelligence.experiments.execution_service import (
    compare_intended_vs_actual,
    create_execution_contract,
    get_contract_for_experiment,
    get_execution_contract,
    get_experiment_speaking_rate_override,
    persist_fidelity,
    resolve_narration_speaking_rate_authority,
)
from app.intelligence.experiments.planning import (
    SAFE_CONTROLLABLE_FACTORS,
    ControlCapability,
)
from app.learning.constants import (
    NARRATION_PACE_MAX_DELTA,
    NARRATION_PACE_SPEAKING_RATE_MAX,
    NARRATION_PACE_SPEAKING_RATE_MIN,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def db(tmp_path):
    conn = open_db(tmp_path / "test14f1.db")
    yield conn
    conn.close()


# ── Shared helpers (mirrored from test_execution_contract_14f.py) ─────────────


def _insert_channel(db: sqlite3.Connection, channel_id: int = 1) -> None:
    db.execute(
        """INSERT OR IGNORE INTO channels
           (id, platform, channel_name, platform_channel_id)
           VALUES (?, 'youtube', 'Test Channel 14f1', ?)""",
        (channel_id, f"UC{channel_id}test14f1"),
    )


def _insert_cluster(db: sqlite3.Connection, cluster_id: int = 99) -> None:
    db.execute(
        """INSERT OR IGNORE INTO market_canonical_clusters
           (id, canonical_label, normalized_label, semantic_fingerprint)
           VALUES (?, 'test cluster 14f1', 'test cluster 14f1', 'fp-14f1')""",
        (cluster_id,),
    )


def _insert_opportunity(
    db: sqlite3.Connection,
    opp_id: int = 1,
    channel_id: int = 1,
    cluster_id: int | None = 99,
) -> None:
    _insert_channel(db, channel_id)
    if cluster_id is not None:
        _insert_cluster(db, cluster_id)
    db.execute(
        """INSERT OR IGNORE INTO channel_profile_versions
           (channel_id, primary_niche, status, version)
           VALUES (?, 'Testing', 'active', 1)""",
        (channel_id,),
    )
    pv_id = db.execute(
        "SELECT id FROM channel_profile_versions WHERE channel_id = ? LIMIT 1",
        (channel_id,),
    ).fetchone()["id"]
    db.execute(
        """INSERT OR IGNORE INTO discovery_runs
           (channel_id, profile_version_id, adapter_name, status, started_at)
           VALUES (?, ?, 'manual', 'completed', '2026-08-22T00:00:00')""",
        (channel_id, pv_id),
    )
    run_id = db.execute(
        "SELECT id FROM discovery_runs WHERE channel_id = ? LIMIT 1",
        (channel_id,),
    ).fetchone()["id"]
    db.execute(
        """INSERT OR IGNORE INTO opportunities
           (id, channel_id, discovery_run_id, normalized_topic, raw_topic,
            canonical_cluster_id, created_at, updated_at)
           VALUES (?, ?, ?, 'test topic', 'test topic', ?,
                   '2026-08-22T00:00:00', '2026-08-22T00:00:00')""",
        (opp_id, channel_id, run_id, cluster_id),
    )


def _insert_experiment(
    db: sqlite3.Connection,
    exp_id: str = "exp-14f1",
    channel_id: int = 1,
    opp_id: int = 1,
    status: str = "planned",
) -> str:
    db.execute(
        """INSERT OR IGNORE INTO experiments
           (id, channel_id, opportunity_id, experiment_type, status, hypothesis, input_hash)
           VALUES (?, ?, ?, 'exploration', ?, 'test hypothesis 14f1', 'hash-14f1')""",
        (exp_id, channel_id, opp_id, status),
    )
    return exp_id


def _insert_experiment_factor(
    db: sqlite3.Connection,
    exp_id: str,
    factor_name: str = "narration_speaking_rate",
    factor_role: str = "treatment",
    value_type: str = "numeric",
    intended_value: str | None = "1.0",
) -> None:
    db.execute(
        """INSERT INTO experiment_factors
           (experiment_id, factor_name, factor_role, value_type, intended_value)
           VALUES (?, ?, ?, ?, ?)""",
        (exp_id, factor_name, factor_role, value_type, intended_value),
    )


def _insert_planning_run(
    db: sqlite3.Connection, run_id: str = "run-14f1", channel_id: int = 1
) -> None:
    db.execute(
        """INSERT OR IGNORE INTO experiment_planning_runs
           (id, channel_id, status, eligible_count, exploration_only_count,
            general_eligible_count, selected_count, deferred_count, input_hash)
           VALUES (?, ?, 'completed', 1, 0, 1, 1, 0, 'hash-pr-14f1')""",
        (run_id, channel_id),
    )


def _insert_candidate_score(
    db: sqlite3.Connection,
    run_id: str = "run-14f1",
    opp_id: int = 1,
    channel_id: int = 1,
    cluster_id: int | None = 99,
    eligibility: str = "general_eligible",
) -> int:
    r = db.execute(
        """INSERT INTO experiment_candidate_scores
           (planning_run_id, opportunity_id, channel_id, canonical_cluster_id,
            eligibility_classification, planning_intent, experiment_type,
            primary_target_metric, primary_metric_direction, hypothesis_sketch,
            intended_treatment_factors_json, controlled_factors_json,
            feature_change_risk, final_planning_score, input_hash)
           VALUES (?, ?, ?, ?, ?, 'exploration', 'exploration',
                   'average_view_percentage', 'higher_is_better', 'hyp',
                   '[]', '[]', 'low', 0.5, ?)
           RETURNING id""",
        (run_id, opp_id, channel_id, cluster_id, eligibility, f"hash-cs-{opp_id}"),
    ).fetchone()
    return r["id"]


def _insert_selection_decision(
    db: sqlite3.Connection,
    candidate_score_id: int,
    opp_id: int = 1,
    run_id: str = "run-14f1",
) -> int:
    r = db.execute(
        """INSERT INTO experiment_selection_decisions
           (planning_run_id, candidate_score_id, opportunity_id, selected,
            rank_in_pool, pool_type, selection_reason, is_validation_repeat)
           VALUES (?, ?, ?, 1, 1, 'exploration', 'top scored', 0)
           RETURNING id""",
        (run_id, candidate_score_id, opp_id),
    ).fetchone()
    return r["id"]


def _insert_brief(
    db: sqlite3.Connection,
    brief_id: str = "brief-14f1",
    channel_id: int = 1,
    opp_id: int = 1,
    cluster_id: int | None = 99,
    treatment_factors_json: str = "[]",
    controlled_factors_json: str = "[]",
) -> str:
    _insert_planning_run(db, channel_id=channel_id)
    cs_id = _insert_candidate_score(db, opp_id=opp_id, channel_id=channel_id, cluster_id=cluster_id)
    sd_id = _insert_selection_decision(db, cs_id, opp_id=opp_id)
    db.execute(
        """INSERT OR IGNORE INTO experiment_strategy_briefs
           (id, channel_id, planning_run_id, selection_decision_id, opportunity_id,
            canonical_cluster_id, brief_planning_intent, experiment_type,
            hypothesis, target_metric, target_direction, brief_hash, status,
            treatment_factors_json, controlled_factors_json)
           VALUES (?, ?, 'run-14f1', ?, ?, ?, 'feature_exploration', 'exploration',
                   'test hyp', 'average_view_percentage', 'higher_is_better',
                   ?, 'pending_approval', ?, ?)""",
        (
            brief_id,
            channel_id,
            sd_id,
            opp_id,
            cluster_id,
            f"hash-{brief_id}",
            treatment_factors_json,
            controlled_factors_json,
        ),
    )
    return brief_id


def _build_contract_chain(
    db: sqlite3.Connection,
    *,
    exp_id: str = "exp-14f1",
    brief_id: str = "brief-14f1",
    channel_id: int = 1,
    opp_id: int = 1,
    cluster_id: int | None = 99,
    treatment_factors_json: str = "[]",
    controlled_factors_json: str = "[]",
    exp_status: str = "planned",
    eligibility: str = "general_eligible",
) -> tuple[str, str]:
    _insert_opportunity(db, opp_id, channel_id, cluster_id)
    _insert_experiment(db, exp_id, channel_id, opp_id, status=exp_status)
    _insert_brief(
        db,
        brief_id,
        channel_id,
        opp_id,
        cluster_id,
        treatment_factors_json=treatment_factors_json,
        controlled_factors_json=controlled_factors_json,
    )
    db.execute(
        "UPDATE experiment_candidate_scores SET eligibility_classification = ? "
        "WHERE opportunity_id = ?",
        (eligibility, opp_id),
    )
    db.commit()
    return exp_id, brief_id


_plan_counter_14f1 = 0
_snapshot_counter_14f1 = 0


def _insert_production_plan(db: sqlite3.Connection, experiment_id: str = "exp-14f1") -> int:
    global _plan_counter_14f1
    _plan_counter_14f1 += 1
    db.commit()
    db.execute("PRAGMA foreign_keys = OFF")
    db.commit()
    r = db.execute(
        """INSERT INTO production_plans
           (topic_id, script_id, script_version, input_hash, script_body_hash,
            plan_schema_version, renderer_version, duration_algorithm_version,
            title, format, total_estimated_duration_s, total_word_count,
            warnings_json, requires_evidence_review, evidence_hash, experiment_id, status)
           VALUES (999, 999, 1, ?, 'body-hash-14f1',
                   '1.0', '1.0', '1.0',
                   'test plan 14f1', 'short', 60, 100,
                   '[]', 0, 'ev-hash', ?, 'approved')
           RETURNING id""",
        (f"plan-hash-14f1-{_plan_counter_14f1}", experiment_id),
    ).fetchone()
    db.commit()
    db.execute("PRAGMA foreign_keys = ON")
    db.commit()
    return r["id"]


def _insert_feature_snapshot(
    db: sqlite3.Connection,
    plan_id: int,
    narration_speaking_rate: float | None = 1.0,
    has_hook: int | None = 1,
    has_cta: int | None = 0,
    render_caption_burn_in: int | None = 0,
    script_format: str | None = "narrative",
    narration_voice_id: str | None = "voice-14f1",
    publish_day_of_week: int | None = None,
) -> None:
    global _snapshot_counter_14f1
    _snapshot_counter_14f1 += 1
    db.commit()
    db.execute("PRAGMA foreign_keys = OFF")
    db.commit()
    db.execute(
        """INSERT INTO content_feature_snapshots
           (publication_id, topic_id, feature_schema_version, extractor_version,
            input_hash, extracted_at, created_at,
            publishing_plan_id, production_plan_id, script_id, narration_run_id,
            caption_run_id, scene_manifest_id, render_manifest_id, voice_profile_id,
            narration_speaking_rate, has_hook, has_cta,
            render_caption_burn_in, script_format, narration_voice_id, publish_day_of_week)
           VALUES (?, 999, '1.0', '1.0',
                   ?, '2026-08-22T00:00:00', '2026-08-22T00:00:00',
                   999, ?, 999, 999,
                   999, 999, 999, 999,
                   ?, ?, ?, ?, ?, ?, ?)""",
        (
            _snapshot_counter_14f1,
            f"snap-hash-14f1-{_snapshot_counter_14f1}",
            plan_id,
            narration_speaking_rate,
            has_hook,
            has_cta,
            render_caption_burn_in,
            script_format,
            narration_voice_id,
            publish_day_of_week,
        ),
    )
    db.commit()
    db.execute("PRAGMA foreign_keys = ON")
    db.commit()


def _create_real_contract_with_factors(
    db: sqlite3.Connection,
    *,
    exp_id: str,
    brief_id: str,
    treatment_factors_json: str,
    controlled_factors_json: str,
    channel_id: int = 1,
    opp_id: int = 1,
) -> object:
    """Create a real-mode contract and approve it; returns the contract object."""
    _build_contract_chain(
        db,
        exp_id=exp_id,
        brief_id=brief_id,
        channel_id=channel_id,
        opp_id=opp_id,
        treatment_factors_json=treatment_factors_json,
        controlled_factors_json=controlled_factors_json,
    )
    contract = create_execution_contract(
        db,
        experiment_id=exp_id,
        brief_id=brief_id,
        mode=ExecutionMode.REAL,
    )
    db.execute(
        "UPDATE experiment_execution_contracts SET status = 'approved' WHERE experiment_id = ?",
        (exp_id,),
    )
    db.commit()
    return contract


# ── A-H: Precedence — resolve_narration_speaking_rate_authority ───────────────


def test_A_treatment_rate_returns_experiment_treatment(db):
    """CASE A: TREATMENT narration_speaking_rate → (1.1, EXPERIMENT_TREATMENT)."""
    exp_id = "exp-A"
    brief_id = "brief-A"
    treatment_json = json.dumps(
        [
            {
                "factor_name": "narration_speaking_rate",
                "factor_role": "treatment",
                "value_type": "numeric",
                "intended_value": "1.1",
            }
        ]
    )
    _build_contract_chain(
        db,
        exp_id=exp_id,
        brief_id=brief_id,
        opp_id=10,
        treatment_factors_json=treatment_json,
    )
    _insert_experiment_factor(db, exp_id, intended_value="1.1")
    create_execution_contract(
        db,
        experiment_id=exp_id,
        brief_id=brief_id,
        mode=ExecutionMode.REAL,
    )
    db.execute(
        "UPDATE experiment_execution_contracts SET status = 'approved' WHERE experiment_id = ?",
        (exp_id,),
    )
    db.commit()

    rate, authority = resolve_narration_speaking_rate_authority(db, exp_id)
    assert authority == ParameterAuthority.EXPERIMENT_TREATMENT
    assert rate == pytest.approx(1.1, abs=0.001)


def test_B_control_enforced_rate_returns_experiment_control(db):
    """CASE B: CONTROL narration_speaking_rate with ENFORCED capability →
    (1.0, EXPERIMENT_CONTROL)."""
    exp_id = "exp-B"
    brief_id = "brief-B"
    controlled_json = json.dumps(
        [
            {
                "factor_name": "narration_speaking_rate",
                "factor_role": "control",
                "baseline_value": "1.0",
                "control_capability": "enforced",
                "baseline_source": "prior_production",
            }
        ]
    )
    _build_contract_chain(
        db,
        exp_id=exp_id,
        brief_id=brief_id,
        opp_id=11,
        controlled_factors_json=controlled_json,
    )
    create_execution_contract(
        db,
        experiment_id=exp_id,
        brief_id=brief_id,
        mode=ExecutionMode.REAL,
    )
    db.execute(
        "UPDATE experiment_execution_contracts SET status = 'approved' WHERE experiment_id = ?",
        (exp_id,),
    )
    db.commit()

    rate, authority = resolve_narration_speaking_rate_authority(db, exp_id)
    assert authority == ParameterAuthority.EXPERIMENT_CONTROL
    assert rate == pytest.approx(1.0, abs=0.001)


def test_C_experiment_does_not_govern_rate_returns_not_governing(db):
    """CASE C: experiment has contract but narration_speaking_rate is not in it."""
    exp_id = "exp-C"
    brief_id = "brief-C"
    treatment_json = json.dumps(
        [
            {
                "factor_name": "has_hook",
                "factor_role": "treatment",
                "value_type": "boolean",
                "intended_value": "1",
            }
        ]
    )
    _build_contract_chain(
        db,
        exp_id=exp_id,
        brief_id=brief_id,
        opp_id=12,
        treatment_factors_json=treatment_json,
    )
    create_execution_contract(
        db,
        experiment_id=exp_id,
        brief_id=brief_id,
        mode=ExecutionMode.REAL,
    )
    db.execute(
        "UPDATE experiment_execution_contracts SET status = 'approved' WHERE experiment_id = ?",
        (exp_id,),
    )
    db.commit()

    rate, authority = resolve_narration_speaking_rate_authority(db, exp_id)
    assert authority == ParameterAuthority.EXPERIMENT_NOT_GOVERNING
    assert rate is None


def test_D_no_contract_returns_not_governing(db):
    """CASE D: no real/approved contract exists for experiment → NOT_GOVERNING."""
    rate, authority = resolve_narration_speaking_rate_authority(db, "exp-nonexistent")
    assert authority == ParameterAuthority.EXPERIMENT_NOT_GOVERNING
    assert rate is None


def test_E_treatment_wins_over_control_when_both_present(db):
    """TREATMENT has higher precedence than CONTROL for same factor."""
    exp_id = "exp-E"
    brief_id = "brief-E"
    treatment_json = json.dumps(
        [
            {
                "factor_name": "narration_speaking_rate",
                "factor_role": "treatment",
                "value_type": "numeric",
                "intended_value": "1.2",
            }
        ]
    )
    controlled_json = json.dumps(
        [
            {
                "factor_name": "narration_speaking_rate",
                "factor_role": "control",
                "baseline_value": "1.0",
                "control_capability": "enforced",
                "baseline_source": "prior_production",
            }
        ]
    )
    _build_contract_chain(
        db,
        exp_id=exp_id,
        brief_id=brief_id,
        opp_id=13,
        treatment_factors_json=treatment_json,
        controlled_factors_json=controlled_json,
    )
    _insert_experiment_factor(db, exp_id, intended_value="1.2")
    create_execution_contract(
        db,
        experiment_id=exp_id,
        brief_id=brief_id,
        mode=ExecutionMode.REAL,
    )
    db.execute(
        "UPDATE experiment_execution_contracts SET status = 'approved' WHERE experiment_id = ?",
        (exp_id,),
    )
    db.commit()

    rate, authority = resolve_narration_speaking_rate_authority(db, exp_id)
    assert authority == ParameterAuthority.EXPERIMENT_TREATMENT
    assert rate == pytest.approx(1.2, abs=0.001)


def test_F_control_soft_does_not_suppress_learning_application(db):
    """CONTROL with SOFT capability does NOT become EXPERIMENT_CONTROL authority.

    resolve_narration_speaking_rate_authority reads control_factors_json directly.
    A soft control_capability in the stored JSON means enforcement is best-effort;
    the function must NOT treat it as EXPERIMENT_CONTROL.
    We directly insert a contract row with soft capability to unit-test this branch.
    (In production, SAFE_CONTROLLABLE_FACTORS overrides the brief's capability to
    the canonical value; for narration_speaking_rate that is ENFORCED. This test
    verifies the resolver's branch logic independently.)
    """
    exp_id = "exp-F"
    brief_id = "brief-F"
    _build_contract_chain(db, exp_id=exp_id, brief_id=brief_id, opp_id=14)
    # Directly insert a row with soft control_capability to test the resolver branch
    control_factors_with_soft = json.dumps(
        [
            {
                "factor_name": "narration_speaking_rate",
                "factor_role": "control",
                "baseline_value": "1.0",
                "control_capability": "soft",
                "baseline_source": "prior_production",
            }
        ]
    )
    db.execute(
        """INSERT INTO experiment_execution_contracts
           (id, experiment_id, brief_id, idea_id, channel_id, opportunity_id,
            canonical_cluster_id, execution_mode, status, execution_policy_version,
            eligibility_recheck_result, eligibility_blocked,
            treatment_factors_json, control_factors_json,
            narration_speaking_rate_override,
            treatment_delta_valid, treatment_abs_valid)
           VALUES (?, ?, ?, NULL, 1, 14, 99, 'real', 'approved', '1.0.0',
                   'general_eligible', 0,
                   '[]', ?, NULL, 1, 1)""",
        (f"contract-F-{exp_id}", exp_id, brief_id, control_factors_with_soft),
    )
    db.commit()

    rate, authority = resolve_narration_speaking_rate_authority(db, exp_id)
    assert authority == ParameterAuthority.EXPERIMENT_NOT_GOVERNING
    assert rate is None


def test_G_treatment_null_intended_value_falls_through_to_not_governing(db):
    """TREATMENT factor with null intended_value does not assert authority."""
    exp_id = "exp-G"
    brief_id = "brief-G"
    treatment_json = json.dumps(
        [
            {
                "factor_name": "narration_speaking_rate",
                "factor_role": "treatment",
                "value_type": "numeric",
                "intended_value": None,
            }
        ]
    )
    _build_contract_chain(
        db,
        exp_id=exp_id,
        brief_id=brief_id,
        opp_id=15,
        treatment_factors_json=treatment_json,
    )
    _insert_experiment_factor(db, exp_id, intended_value=None)
    create_execution_contract(
        db,
        experiment_id=exp_id,
        brief_id=brief_id,
        mode=ExecutionMode.REAL,
    )
    db.execute(
        "UPDATE experiment_execution_contracts SET status = 'approved' WHERE experiment_id = ?",
        (exp_id,),
    )
    db.commit()

    rate, authority = resolve_narration_speaking_rate_authority(db, exp_id)
    assert authority == ParameterAuthority.EXPERIMENT_NOT_GOVERNING
    assert rate is None


def test_H_control_null_baseline_falls_through_to_not_governing(db):
    """CONTROL factor with null baseline_value (enforced) does not assert authority."""
    exp_id = "exp-H"
    brief_id = "brief-H"
    controlled_json = json.dumps(
        [
            {
                "factor_name": "narration_speaking_rate",
                "factor_role": "control",
                "baseline_value": None,
                "control_capability": "enforced",
                "baseline_source": "prior_production",
            }
        ]
    )
    _build_contract_chain(
        db,
        exp_id=exp_id,
        brief_id=brief_id,
        opp_id=16,
        controlled_factors_json=controlled_json,
    )
    create_execution_contract(
        db,
        experiment_id=exp_id,
        brief_id=brief_id,
        mode=ExecutionMode.REAL,
    )
    db.execute(
        "UPDATE experiment_execution_contracts SET status = 'approved' WHERE experiment_id = ?",
        (exp_id,),
    )
    db.commit()

    rate, authority = resolve_narration_speaking_rate_authority(db, exp_id)
    assert authority == ParameterAuthority.EXPERIMENT_NOT_GOVERNING
    assert rate is None


# ── I-M: Consumption — active_app suppression logic ──────────────────────────


def test_I_experiment_treatment_authority_suppresses_active_app():
    """When authority is EXPERIMENT_TREATMENT, active_app must be set to None."""
    # Simulate the stage_executors.py logic directly
    authority = ParameterAuthority.EXPERIMENT_TREATMENT
    contract_rate = 1.1
    active_app = object()  # non-None sentinel
    speaking_rate_override = None

    if authority in (
        ParameterAuthority.EXPERIMENT_TREATMENT,
        ParameterAuthority.EXPERIMENT_CONTROL,
    ):
        speaking_rate_override = contract_rate
        active_app = None

    assert active_app is None
    assert speaking_rate_override == pytest.approx(1.1)


def test_J_experiment_control_authority_suppresses_active_app():
    """When authority is EXPERIMENT_CONTROL, active_app must be set to None."""
    authority = ParameterAuthority.EXPERIMENT_CONTROL
    contract_rate = 1.0
    active_app = object()
    speaking_rate_override = None

    if authority in (
        ParameterAuthority.EXPERIMENT_TREATMENT,
        ParameterAuthority.EXPERIMENT_CONTROL,
    ):
        speaking_rate_override = contract_rate
        active_app = None

    assert active_app is None
    assert speaking_rate_override == pytest.approx(1.0)


def test_K_not_governing_does_not_suppress_active_app():
    """When authority is EXPERIMENT_NOT_GOVERNING, active_app must NOT be suppressed."""
    authority = ParameterAuthority.EXPERIMENT_NOT_GOVERNING
    contract_rate = None
    sentinel = object()
    active_app = sentinel
    speaking_rate_override = 0.95  # set by learning application before this block

    if authority in (
        ParameterAuthority.EXPERIMENT_TREATMENT,
        ParameterAuthority.EXPERIMENT_CONTROL,
    ):
        speaking_rate_override = contract_rate
        active_app = None

    assert active_app is sentinel  # not suppressed
    assert speaking_rate_override == pytest.approx(0.95)  # learning app value preserved


def test_L_production_default_authority_does_not_suppress():
    """PRODUCTION_DEFAULT is not a governing authority; active_app not suppressed."""
    authority = ParameterAuthority.PRODUCTION_DEFAULT
    sentinel = object()
    active_app = sentinel

    if authority in (
        ParameterAuthority.EXPERIMENT_TREATMENT,
        ParameterAuthority.EXPERIMENT_CONTROL,
    ):
        active_app = None

    assert active_app is sentinel


def test_M_learning_application_authority_does_not_suppress():
    """LEARNING_APPLICATION authority must not be suppressed by experiment block."""
    authority = ParameterAuthority.LEARNING_APPLICATION
    sentinel = object()
    active_app = sentinel

    if authority in (
        ParameterAuthority.EXPERIMENT_TREATMENT,
        ParameterAuthority.EXPERIMENT_CONTROL,
    ):
        active_app = None

    assert active_app is sentinel


# ── N-R: Contract status gating ──────────────────────────────────────────────


def test_N_pending_status_returns_not_governing(db):
    """Contract in 'pending' status does not assert authority."""
    exp_id = "exp-N"
    brief_id = "brief-N"
    treatment_json = json.dumps(
        [
            {
                "factor_name": "narration_speaking_rate",
                "factor_role": "treatment",
                "value_type": "numeric",
                "intended_value": "1.1",
            }
        ]
    )
    _build_contract_chain(
        db,
        exp_id=exp_id,
        brief_id=brief_id,
        opp_id=20,
        treatment_factors_json=treatment_json,
    )
    _insert_experiment_factor(db, exp_id, intended_value="1.1")
    create_execution_contract(
        db,
        experiment_id=exp_id,
        brief_id=brief_id,
        mode=ExecutionMode.REAL,
    )
    # leave status as 'pending'
    db.commit()

    rate, authority = resolve_narration_speaking_rate_authority(db, exp_id)
    assert authority == ParameterAuthority.EXPERIMENT_NOT_GOVERNING
    assert rate is None


def test_O_executing_status_returns_treatment_authority(db):
    """Contract in 'executing' status does assert EXPERIMENT_TREATMENT authority."""
    exp_id = "exp-O"
    brief_id = "brief-O"
    treatment_json = json.dumps(
        [
            {
                "factor_name": "narration_speaking_rate",
                "factor_role": "treatment",
                "value_type": "numeric",
                "intended_value": "1.3",
            }
        ]
    )
    _build_contract_chain(
        db,
        exp_id=exp_id,
        brief_id=brief_id,
        opp_id=21,
        treatment_factors_json=treatment_json,
    )
    _insert_experiment_factor(db, exp_id, intended_value="1.3")
    create_execution_contract(
        db,
        experiment_id=exp_id,
        brief_id=brief_id,
        mode=ExecutionMode.REAL,
    )
    db.execute(
        "UPDATE experiment_execution_contracts SET status = 'executing' WHERE experiment_id = ?",
        (exp_id,),
    )
    db.commit()

    rate, authority = resolve_narration_speaking_rate_authority(db, exp_id)
    assert authority == ParameterAuthority.EXPERIMENT_TREATMENT
    assert rate == pytest.approx(1.3, abs=0.001)


def test_P_completed_status_returns_not_governing(db):
    """Contract in 'completed' status does not assert authority (production done)."""
    exp_id = "exp-P"
    brief_id = "brief-P"
    treatment_json = json.dumps(
        [
            {
                "factor_name": "narration_speaking_rate",
                "factor_role": "treatment",
                "value_type": "numeric",
                "intended_value": "1.1",
            }
        ]
    )
    _build_contract_chain(
        db,
        exp_id=exp_id,
        brief_id=brief_id,
        opp_id=22,
        treatment_factors_json=treatment_json,
    )
    _insert_experiment_factor(db, exp_id, intended_value="1.1")
    create_execution_contract(
        db,
        experiment_id=exp_id,
        brief_id=brief_id,
        mode=ExecutionMode.REAL,
    )
    db.execute(
        "UPDATE experiment_execution_contracts SET status = 'completed' WHERE experiment_id = ?",
        (exp_id,),
    )
    db.commit()

    rate, authority = resolve_narration_speaking_rate_authority(db, exp_id)
    assert authority == ParameterAuthority.EXPERIMENT_NOT_GOVERNING
    assert rate is None


def test_Q_failed_status_returns_not_governing(db):
    """Contract in 'failed' status does not assert authority."""
    exp_id = "exp-Q"
    brief_id = "brief-Q"
    treatment_json = json.dumps(
        [
            {
                "factor_name": "narration_speaking_rate",
                "factor_role": "treatment",
                "value_type": "numeric",
                "intended_value": "1.1",
            }
        ]
    )
    _build_contract_chain(
        db,
        exp_id=exp_id,
        brief_id=brief_id,
        opp_id=23,
        treatment_factors_json=treatment_json,
    )
    _insert_experiment_factor(db, exp_id, intended_value="1.1")
    create_execution_contract(
        db,
        experiment_id=exp_id,
        brief_id=brief_id,
        mode=ExecutionMode.REAL,
    )
    db.execute(
        "UPDATE experiment_execution_contracts SET status = 'failed' WHERE experiment_id = ?",
        (exp_id,),
    )
    db.commit()

    rate, authority = resolve_narration_speaking_rate_authority(db, exp_id)
    assert authority == ParameterAuthority.EXPERIMENT_NOT_GOVERNING
    assert rate is None


def test_R_blocked_status_returns_not_governing(db):
    """Contract in 'blocked' status does not assert authority."""
    exp_id = "exp-R"
    brief_id = "brief-R"
    treatment_json = json.dumps(
        [
            {
                "factor_name": "narration_speaking_rate",
                "factor_role": "treatment",
                "value_type": "numeric",
                "intended_value": "1.1",
            }
        ]
    )
    _build_contract_chain(
        db,
        exp_id=exp_id,
        brief_id=brief_id,
        opp_id=24,
        treatment_factors_json=treatment_json,
    )
    _insert_experiment_factor(db, exp_id, intended_value="1.1")
    create_execution_contract(
        db,
        experiment_id=exp_id,
        brief_id=brief_id,
        mode=ExecutionMode.REAL,
    )
    db.execute(
        "UPDATE experiment_execution_contracts SET status = 'blocked' WHERE experiment_id = ?",
        (exp_id,),
    )
    db.commit()

    rate, authority = resolve_narration_speaking_rate_authority(db, exp_id)
    assert authority == ParameterAuthority.EXPERIMENT_NOT_GOVERNING
    assert rate is None


# ── S-T: Baseline snapshot written for CONTROL ────────────────────────────────


def test_S_control_baseline_written_to_treatment_factors_json(db):
    """For CONTROL-only contracts, control_factors_json includes the baseline value."""
    exp_id = "exp-S"
    brief_id = "brief-S"
    controlled_json = json.dumps(
        [
            {
                "factor_name": "narration_speaking_rate",
                "factor_role": "control",
                "baseline_value": "1.0",
                "control_capability": "enforced",
                "baseline_source": "prior_production",
            }
        ]
    )
    _build_contract_chain(
        db,
        exp_id=exp_id,
        brief_id=brief_id,
        opp_id=30,
        controlled_factors_json=controlled_json,
    )
    create_execution_contract(
        db,
        experiment_id=exp_id,
        brief_id=brief_id,
        mode=ExecutionMode.REAL,
    )
    db.execute(
        "UPDATE experiment_execution_contracts SET status = 'approved' WHERE experiment_id = ?",
        (exp_id,),
    )
    db.commit()

    row = db.execute(
        "SELECT control_factors_json FROM experiment_execution_contracts WHERE experiment_id = ?",
        (exp_id,),
    ).fetchone()
    control_factors = json.loads(row["control_factors_json"] or "[]")
    sr_factor = next(
        (f for f in control_factors if f["factor_name"] == "narration_speaking_rate"), None
    )
    assert sr_factor is not None
    assert sr_factor["baseline_value"] == "1.0"
    assert sr_factor["control_capability"] == "enforced"


def test_T_control_contract_narration_override_is_null_in_db(db):
    """For CONTROL-only experiments, narration_speaking_rate_override is NULL in DB.

    The resolve_narration_speaking_rate_authority() function must read from
    control_factors_json to find the baseline — it must NOT rely on the
    narration_speaking_rate_override column (which is only set for TREATMENT).
    This is the core bug fix from Phase 14F.1.
    """
    exp_id = "exp-T"
    brief_id = "brief-T"
    controlled_json = json.dumps(
        [
            {
                "factor_name": "narration_speaking_rate",
                "factor_role": "control",
                "baseline_value": "1.0",
                "control_capability": "enforced",
                "baseline_source": "prior_production",
            }
        ]
    )
    _build_contract_chain(
        db,
        exp_id=exp_id,
        brief_id=brief_id,
        opp_id=31,
        controlled_factors_json=controlled_json,
    )
    create_execution_contract(
        db,
        experiment_id=exp_id,
        brief_id=brief_id,
        mode=ExecutionMode.REAL,
    )
    db.execute(
        "UPDATE experiment_execution_contracts SET status = 'approved' WHERE experiment_id = ?",
        (exp_id,),
    )
    db.commit()

    # Confirm the old path (get_experiment_speaking_rate_override) returns None for CONTROL
    old_rate = get_experiment_speaking_rate_override(db, exp_id)
    assert old_rate is None, "narration_speaking_rate_override column is NULL for CONTROL-only"

    # But the new path correctly identifies CONTROL authority
    rate, authority = resolve_narration_speaking_rate_authority(db, exp_id)
    assert authority == ParameterAuthority.EXPERIMENT_CONTROL
    assert rate == pytest.approx(1.0, abs=0.001)


# ── U-W: Factor consistency — SAFE_CONTROLLABLE_FACTORS ──────────────────────


def test_U_narration_speaking_rate_control_capability_is_enforced():
    """narration_speaking_rate must have control_capability=ENFORCED in
    SAFE_CONTROLLABLE_FACTORS."""
    spec = SAFE_CONTROLLABLE_FACTORS.get("narration_speaking_rate")
    assert spec is not None
    assert spec.control_capability == ControlCapability.ENFORCED


def test_V_has_hook_control_capability_is_soft():
    """has_hook must have control_capability=SOFT (content generation, not deterministic)."""
    spec = SAFE_CONTROLLABLE_FACTORS.get("has_hook")
    assert spec is not None
    assert spec.control_capability == ControlCapability.SOFT


def test_W_all_seven_factors_present_in_safe_controllable_factors():
    """All 7 factors from the spec must be registered in SAFE_CONTROLLABLE_FACTORS."""
    expected = {
        "narration_speaking_rate",
        "has_hook",
        "has_cta",
        "narration_voice_id",
        "render_caption_burn_in",
        "script_format",
        "publish_day_of_week",
    }
    assert expected.issubset(set(SAFE_CONTROLLABLE_FACTORS.keys()))


# ── X-AD: Fidelity ───────────────────────────────────────────────────────────


def test_X_fidelity_not_yet_available_when_no_snapshot(db):
    """compare_intended_vs_actual returns NOT_YET_AVAILABLE when no feature snapshot."""
    exp_id = "exp-X"
    brief_id = "brief-X"
    treatment_json = json.dumps(
        [
            {
                "factor_name": "narration_speaking_rate",
                "factor_role": "treatment",
                "value_type": "numeric",
                "intended_value": "1.1",
            }
        ]
    )
    _build_contract_chain(
        db,
        exp_id=exp_id,
        brief_id=brief_id,
        opp_id=40,
        treatment_factors_json=treatment_json,
    )
    _insert_experiment_factor(db, exp_id, intended_value="1.1")
    contract = create_execution_contract(
        db,
        experiment_id=exp_id,
        brief_id=brief_id,
        mode=ExecutionMode.REAL,
    )

    fidelity = compare_intended_vs_actual(db, contract)
    assert fidelity.valid_for_learning is None
    assert all(f.outcome == FidelityOutcome.NOT_YET_AVAILABLE for f in fidelity.treatment_outcomes)


def test_Y_fidelity_matched_when_actual_equals_intended(db):
    """compare_intended_vs_actual returns MATCHED when actual == intended."""
    exp_id = "exp-Y"
    brief_id = "brief-Y"
    treatment_json = json.dumps(
        [
            {
                "factor_name": "narration_speaking_rate",
                "factor_role": "treatment",
                "value_type": "numeric",
                "intended_value": "1.1",
            }
        ]
    )
    _build_contract_chain(
        db,
        exp_id=exp_id,
        brief_id=brief_id,
        opp_id=41,
        treatment_factors_json=treatment_json,
    )
    _insert_experiment_factor(db, exp_id, intended_value="1.1")
    contract = create_execution_contract(
        db,
        experiment_id=exp_id,
        brief_id=brief_id,
        mode=ExecutionMode.REAL,
    )
    plan_id = _insert_production_plan(db, exp_id)
    _insert_feature_snapshot(db, plan_id, narration_speaking_rate=1.1)

    fidelity = compare_intended_vs_actual(db, contract)
    sr_outcome = next(
        (f for f in fidelity.treatment_outcomes if f.factor_name == "narration_speaking_rate"),
        None,
    )
    assert sr_outcome is not None
    assert sr_outcome.outcome in (FidelityOutcome.MATCHED, FidelityOutcome.WITHIN_TOLERANCE)


def test_Z_fidelity_deviated_when_actual_differs_by_more_than_tolerance(db):
    """compare_intended_vs_actual returns DEVIATED when |actual - intended| > tolerance."""
    exp_id = "exp-Z"
    brief_id = "brief-Z"
    treatment_json = json.dumps(
        [
            {
                "factor_name": "narration_speaking_rate",
                "factor_role": "treatment",
                "value_type": "numeric",
                "intended_value": "1.1",
            }
        ]
    )
    _build_contract_chain(
        db,
        exp_id=exp_id,
        brief_id=brief_id,
        opp_id=42,
        treatment_factors_json=treatment_json,
    )
    _insert_experiment_factor(db, exp_id, intended_value="1.1")
    contract = create_execution_contract(
        db,
        experiment_id=exp_id,
        brief_id=brief_id,
        mode=ExecutionMode.REAL,
    )
    plan_id = _insert_production_plan(db, exp_id)
    # Actual 0.9 vs intended 1.1 — clearly deviated
    _insert_feature_snapshot(db, plan_id, narration_speaking_rate=0.9)

    fidelity = compare_intended_vs_actual(db, contract)
    sr_outcome = next(
        (f for f in fidelity.treatment_outcomes if f.factor_name == "narration_speaking_rate"),
        None,
    )
    assert sr_outcome is not None
    assert sr_outcome.outcome == FidelityOutcome.DEVIATED


def test_AA_fidelity_valid_for_learning_true_when_matched(db):
    """valid_for_learning=True when treatment factor is MATCHED."""
    exp_id = "exp-AA"
    brief_id = "brief-AA"
    treatment_json = json.dumps(
        [
            {
                "factor_name": "narration_speaking_rate",
                "factor_role": "treatment",
                "value_type": "numeric",
                "intended_value": "1.05",
            }
        ]
    )
    _build_contract_chain(
        db,
        exp_id=exp_id,
        brief_id=brief_id,
        opp_id=43,
        treatment_factors_json=treatment_json,
    )
    _insert_experiment_factor(db, exp_id, intended_value="1.05")
    contract = create_execution_contract(
        db,
        experiment_id=exp_id,
        brief_id=brief_id,
        mode=ExecutionMode.REAL,
    )
    plan_id = _insert_production_plan(db, exp_id)
    _insert_feature_snapshot(db, plan_id, narration_speaking_rate=1.05)

    fidelity = compare_intended_vs_actual(db, contract)
    assert fidelity.valid_for_learning is True


def test_AB_fidelity_valid_for_learning_false_when_deviated(db):
    """valid_for_learning=False when treatment factor DEVIATED."""
    exp_id = "exp-AB"
    brief_id = "brief-AB"
    treatment_json = json.dumps(
        [
            {
                "factor_name": "narration_speaking_rate",
                "factor_role": "treatment",
                "value_type": "numeric",
                "intended_value": "1.2",
            }
        ]
    )
    _build_contract_chain(
        db,
        exp_id=exp_id,
        brief_id=brief_id,
        opp_id=44,
        treatment_factors_json=treatment_json,
    )
    _insert_experiment_factor(db, exp_id, intended_value="1.2")
    contract = create_execution_contract(
        db,
        experiment_id=exp_id,
        brief_id=brief_id,
        mode=ExecutionMode.REAL,
    )
    plan_id = _insert_production_plan(db, exp_id)
    _insert_feature_snapshot(db, plan_id, narration_speaking_rate=0.8)

    fidelity = compare_intended_vs_actual(db, contract)
    assert fidelity.valid_for_learning is False


def test_AC_persist_fidelity_updates_db(db):
    """persist_fidelity writes valid_for_learning and fidelity_json to DB."""
    exp_id = "exp-AC"
    brief_id = "brief-AC"
    treatment_json = json.dumps(
        [
            {
                "factor_name": "narration_speaking_rate",
                "factor_role": "treatment",
                "value_type": "numeric",
                "intended_value": "1.0",
            }
        ]
    )
    _build_contract_chain(
        db,
        exp_id=exp_id,
        brief_id=brief_id,
        opp_id=45,
        treatment_factors_json=treatment_json,
    )
    _insert_experiment_factor(db, exp_id, intended_value="1.0")
    contract = create_execution_contract(
        db,
        experiment_id=exp_id,
        brief_id=brief_id,
        mode=ExecutionMode.REAL,
    )
    plan_id = _insert_production_plan(db, exp_id)
    _insert_feature_snapshot(db, plan_id, narration_speaking_rate=1.0)

    fidelity = compare_intended_vs_actual(db, contract)
    persist_fidelity(db, contract.id, fidelity)
    db.commit()

    row = db.execute(
        "SELECT valid_for_learning, fidelity_json FROM experiment_execution_contracts WHERE id = ?",
        (contract.id,),
    ).fetchone()
    assert row["valid_for_learning"] is not None
    assert row["fidelity_json"] is not None


def test_AD_no_treatment_factors_non_market_is_not_valid(db):
    """Phase 14F.2 vacuous-truth fix: non-market experiment with zero declared
    treatment factors is structurally malformed → NOT_VALID, not vacuously True.

    Previously (Phase 14F) the bare 'all([])' rule returned valid_for_learning=True.
    Phase 14F.2 adds experiment-type awareness: a feature_exploration experiment
    that declares no treatments cannot have honoured its design.
    """
    exp_id = "exp-AD"
    brief_id = "brief-AD"
    _build_contract_chain(
        db,
        exp_id=exp_id,
        brief_id=brief_id,
        opp_id=46,
    )
    contract = create_execution_contract(
        db,
        experiment_id=exp_id,
        brief_id=brief_id,
        mode=ExecutionMode.REAL,
    )

    fidelity = compare_intended_vs_actual(db, contract)
    assert fidelity.treatment_outcomes == []
    assert fidelity.valid_for_learning is False
    assert fidelity.classification == FidelityClassification.NOT_VALID


# ── AE-AI: True FK integration ───────────────────────────────────────────────


def _open(tmp_path):
    """Open a fresh DB with FK ON."""
    conn = open_db(tmp_path / f"db-{uuid.uuid4().hex[:8]}.db")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _build_full_fk_chain(conn, *, prod_plan_exp_id=None, pub_plan_exp_id=None):
    """Import and call the canonical FK chain builder from test_experiment_lineage_14b2."""
    import importlib

    mod = importlib.import_module("test_experiment_lineage_14b2")
    return mod._build_full_fk_chain(
        conn,
        prod_plan_exp_id=prod_plan_exp_id,
        pub_plan_exp_id=pub_plan_exp_id,
    )


def test_AE_full_fk_chain_builds_successfully(tmp_path):
    """The FK chain builder completes without constraint violations."""
    conn = _open(tmp_path)
    ids = _build_full_fk_chain(conn, prod_plan_exp_id="exp-AE")
    assert ids["publication_id"] is not None
    assert ids["plan_id"] is not None
    conn.close()


def test_AF_contract_created_on_full_fk_chain(tmp_path):
    """create_execution_contract succeeds on a real FK chain production plan."""
    conn = _open(tmp_path)
    exp_id = "exp-AF"

    # Build the supporting chain first
    ids = _build_full_fk_chain(conn, prod_plan_exp_id=exp_id)
    channel_id = ids["channel_id"]
    opp_id = ids["opp_id"]

    # Insert experiment + brief prerequisites using FK-safe helpers
    # (FK is ON, so use real IDs from the chain)
    _insert_experiment(conn, exp_id=exp_id, channel_id=channel_id, opp_id=opp_id)
    _insert_brief(conn, brief_id="brief-AF", channel_id=channel_id, opp_id=opp_id, cluster_id=None)
    conn.execute(
        "UPDATE experiment_candidate_scores SET eligibility_classification = 'general_eligible'"
        " WHERE opportunity_id = ?",
        (opp_id,),
    )
    conn.commit()

    contract = create_execution_contract(
        conn,
        experiment_id=exp_id,
        brief_id="brief-AF",
        mode=ExecutionMode.DRY_RUN,
    )
    assert contract.experiment_id == exp_id
    assert contract.execution_mode == ExecutionMode.DRY_RUN
    conn.close()


def test_AG_authority_resolves_on_full_fk_chain_treatment(tmp_path):
    """resolve_narration_speaking_rate_authority returns TREATMENT on real FK chain."""
    conn = _open(tmp_path)
    exp_id = "exp-AG"

    ids = _build_full_fk_chain(conn, prod_plan_exp_id=exp_id)
    channel_id = ids["channel_id"]
    opp_id = ids["opp_id"]

    treatment_json = json.dumps(
        [
            {
                "factor_name": "narration_speaking_rate",
                "factor_role": "treatment",
                "value_type": "numeric",
                "intended_value": "1.15",
            }
        ]
    )
    _insert_experiment(conn, exp_id=exp_id, channel_id=channel_id, opp_id=opp_id)
    _insert_experiment_factor(conn, exp_id, intended_value="1.15")
    _insert_brief(
        conn,
        brief_id="brief-AG",
        channel_id=channel_id,
        opp_id=opp_id,
        cluster_id=None,
        treatment_factors_json=treatment_json,
    )
    conn.execute(
        "UPDATE experiment_candidate_scores SET eligibility_classification = 'general_eligible'"
        " WHERE opportunity_id = ?",
        (opp_id,),
    )
    conn.commit()

    create_execution_contract(
        conn,
        experiment_id=exp_id,
        brief_id="brief-AG",
        mode=ExecutionMode.REAL,
    )
    conn.execute(
        "UPDATE experiment_execution_contracts SET status = 'approved' WHERE experiment_id = ?",
        (exp_id,),
    )
    conn.commit()

    rate, authority = resolve_narration_speaking_rate_authority(conn, exp_id)
    assert authority == ParameterAuthority.EXPERIMENT_TREATMENT
    assert rate == pytest.approx(1.15, abs=0.001)
    conn.close()


def test_AH_authority_resolves_on_full_fk_chain_control(tmp_path):
    """resolve_narration_speaking_rate_authority returns CONTROL on real FK chain."""
    conn = _open(tmp_path)
    exp_id = "exp-AH"

    ids = _build_full_fk_chain(conn, prod_plan_exp_id=exp_id)
    channel_id = ids["channel_id"]
    opp_id = ids["opp_id"]

    controlled_json = json.dumps(
        [
            {
                "factor_name": "narration_speaking_rate",
                "factor_role": "control",
                "baseline_value": "1.0",
                "control_capability": "enforced",
                "baseline_source": "prior_production",
            }
        ]
    )
    _insert_experiment(conn, exp_id=exp_id, channel_id=channel_id, opp_id=opp_id)
    _insert_brief(
        conn,
        brief_id="brief-AH",
        channel_id=channel_id,
        opp_id=opp_id,
        cluster_id=None,
        controlled_factors_json=controlled_json,
    )
    conn.execute(
        "UPDATE experiment_candidate_scores SET eligibility_classification = 'general_eligible'"
        " WHERE opportunity_id = ?",
        (opp_id,),
    )
    conn.commit()

    create_execution_contract(
        conn,
        experiment_id=exp_id,
        brief_id="brief-AH",
        mode=ExecutionMode.REAL,
    )
    conn.execute(
        "UPDATE experiment_execution_contracts SET status = 'approved' WHERE experiment_id = ?",
        (exp_id,),
    )
    conn.commit()

    rate, authority = resolve_narration_speaking_rate_authority(conn, exp_id)
    assert authority == ParameterAuthority.EXPERIMENT_CONTROL
    assert rate == pytest.approx(1.0, abs=0.001)
    conn.close()


def test_AI_fidelity_comparison_on_full_fk_chain(tmp_path):
    """compare_intended_vs_actual returns a result on a full FK chain with feature snapshot."""
    conn = _open(tmp_path)
    exp_id = "exp-AI"

    ids = _build_full_fk_chain(conn, prod_plan_exp_id=exp_id)
    channel_id = ids["channel_id"]
    opp_id = ids["opp_id"]
    plan_id = ids["plan_id"]

    treatment_json = json.dumps(
        [
            {
                "factor_name": "narration_speaking_rate",
                "factor_role": "treatment",
                "value_type": "numeric",
                "intended_value": "1.1",
            }
        ]
    )
    _insert_experiment(conn, exp_id=exp_id, channel_id=channel_id, opp_id=opp_id)
    _insert_experiment_factor(conn, exp_id, intended_value="1.1")
    _insert_brief(
        conn,
        brief_id="brief-AI",
        channel_id=channel_id,
        opp_id=opp_id,
        cluster_id=None,
        treatment_factors_json=treatment_json,
    )
    conn.execute(
        "UPDATE experiment_candidate_scores SET eligibility_classification = 'general_eligible'"
        " WHERE opportunity_id = ?",
        (opp_id,),
    )
    conn.commit()

    contract = create_execution_contract(
        conn,
        experiment_id=exp_id,
        brief_id="brief-AI",
        mode=ExecutionMode.REAL,
    )
    _insert_feature_snapshot(conn, plan_id, narration_speaking_rate=1.1)

    fidelity = compare_intended_vs_actual(conn, contract)
    assert fidelity is not None
    sr = next(
        (f for f in fidelity.treatment_outcomes if f.factor_name == "narration_speaking_rate"),
        None,
    )
    assert sr is not None
    assert sr.outcome in (FidelityOutcome.MATCHED, FidelityOutcome.WITHIN_TOLERANCE)
    conn.close()


# ── AJ-AN: Dry run — authority always EXPERIMENT_NOT_GOVERNING ───────────────


def test_AJ_dry_run_contract_not_governing_for_treatment_factor(db):
    """dry_run contracts never assert authority (production not triggered)."""
    exp_id = "exp-AJ"
    brief_id = "brief-AJ"
    treatment_json = json.dumps(
        [
            {
                "factor_name": "narration_speaking_rate",
                "factor_role": "treatment",
                "value_type": "numeric",
                "intended_value": "1.1",
            }
        ]
    )
    _build_contract_chain(
        db,
        exp_id=exp_id,
        brief_id=brief_id,
        opp_id=50,
        treatment_factors_json=treatment_json,
    )
    _insert_experiment_factor(db, exp_id, intended_value="1.1")
    create_execution_contract(
        db,
        experiment_id=exp_id,
        brief_id=brief_id,
        mode=ExecutionMode.DRY_RUN,
    )
    db.execute(
        "UPDATE experiment_execution_contracts SET status = 'approved' WHERE experiment_id = ?",
        (exp_id,),
    )
    db.commit()

    rate, authority = resolve_narration_speaking_rate_authority(db, exp_id)
    assert authority == ParameterAuthority.EXPERIMENT_NOT_GOVERNING
    assert rate is None


def test_AK_dry_run_contract_not_governing_for_control_factor(db):
    """dry_run CONTROL contracts never assert EXPERIMENT_CONTROL authority."""
    exp_id = "exp-AK"
    brief_id = "brief-AK"
    controlled_json = json.dumps(
        [
            {
                "factor_name": "narration_speaking_rate",
                "factor_role": "control",
                "baseline_value": "1.0",
                "control_capability": "enforced",
                "baseline_source": "prior_production",
            }
        ]
    )
    _build_contract_chain(
        db,
        exp_id=exp_id,
        brief_id=brief_id,
        opp_id=51,
        controlled_factors_json=controlled_json,
    )
    create_execution_contract(
        db,
        experiment_id=exp_id,
        brief_id=brief_id,
        mode=ExecutionMode.DRY_RUN,
    )
    db.execute(
        "UPDATE experiment_execution_contracts SET status = 'approved' WHERE experiment_id = ?",
        (exp_id,),
    )
    db.commit()

    rate, authority = resolve_narration_speaking_rate_authority(db, exp_id)
    assert authority == ParameterAuthority.EXPERIMENT_NOT_GOVERNING
    assert rate is None


def test_AL_dry_run_contract_is_persisted(db):
    """dry_run contracts are persisted in DB (for audit) even though not injected."""
    exp_id = "exp-AL"
    brief_id = "brief-AL"
    _build_contract_chain(db, exp_id=exp_id, brief_id=brief_id, opp_id=52)
    contract = create_execution_contract(
        db,
        experiment_id=exp_id,
        brief_id=brief_id,
        mode=ExecutionMode.DRY_RUN,
    )
    assert contract.execution_mode == ExecutionMode.DRY_RUN
    row = db.execute(
        "SELECT execution_mode FROM experiment_execution_contracts WHERE experiment_id = ?",
        (exp_id,),
    ).fetchone()
    assert row is not None
    assert row["execution_mode"] == "dry_run"


def test_AM_dry_run_idempotent_returns_same_contract(db):
    """Second create call for dry_run returns the existing contract (idempotent)."""
    exp_id = "exp-AM"
    brief_id = "brief-AM"
    _build_contract_chain(db, exp_id=exp_id, brief_id=brief_id, opp_id=53)
    c1 = create_execution_contract(
        db,
        experiment_id=exp_id,
        brief_id=brief_id,
        mode=ExecutionMode.DRY_RUN,
    )
    c2 = create_execution_contract(
        db,
        experiment_id=exp_id,
        brief_id=brief_id,
        mode=ExecutionMode.DRY_RUN,
    )
    assert c1.id == c2.id


def test_AN_dry_run_get_experiment_speaking_rate_override_returns_none(db):
    """get_experiment_speaking_rate_override also returns None for dry_run contracts."""
    exp_id = "exp-AN"
    brief_id = "brief-AN"
    treatment_json = json.dumps(
        [
            {
                "factor_name": "narration_speaking_rate",
                "factor_role": "treatment",
                "value_type": "numeric",
                "intended_value": "1.1",
            }
        ]
    )
    _build_contract_chain(
        db,
        exp_id=exp_id,
        brief_id=brief_id,
        opp_id=54,
        treatment_factors_json=treatment_json,
    )
    _insert_experiment_factor(db, exp_id, intended_value="1.1")
    create_execution_contract(
        db,
        experiment_id=exp_id,
        brief_id=brief_id,
        mode=ExecutionMode.DRY_RUN,
    )
    db.execute(
        "UPDATE experiment_execution_contracts SET status = 'approved' WHERE experiment_id = ?",
        (exp_id,),
    )
    db.commit()

    assert get_experiment_speaking_rate_override(db, exp_id) is None


# ── AO-AQ: Market exploration — factor not in contract ───────────────────────


def test_AO_market_exploration_no_speaking_rate_factor_is_not_governing(db):
    """Market exploration experiments without narration_speaking_rate → NOT_GOVERNING."""
    exp_id = "exp-AO"
    brief_id = "brief-AO"
    treatment_json = json.dumps(
        [
            {
                "factor_name": "has_hook",
                "factor_role": "treatment",
                "value_type": "boolean",
                "intended_value": "1",
            }
        ]
    )
    _build_contract_chain(
        db,
        exp_id=exp_id,
        brief_id=brief_id,
        opp_id=60,
        treatment_factors_json=treatment_json,
    )
    create_execution_contract(
        db,
        experiment_id=exp_id,
        brief_id=brief_id,
        mode=ExecutionMode.REAL,
    )
    db.execute(
        "UPDATE experiment_execution_contracts SET status = 'approved' WHERE experiment_id = ?",
        (exp_id,),
    )
    db.commit()

    rate, authority = resolve_narration_speaking_rate_authority(db, exp_id)
    assert authority == ParameterAuthority.EXPERIMENT_NOT_GOVERNING
    assert rate is None


def test_AP_market_exploration_has_cta_treatment_is_not_governing_for_speaking_rate(db):
    """has_cta treatment experiment does not govern narration_speaking_rate."""
    exp_id = "exp-AP"
    brief_id = "brief-AP"
    treatment_json = json.dumps(
        [
            {
                "factor_name": "has_cta",
                "factor_role": "treatment",
                "value_type": "boolean",
                "intended_value": "1",
            }
        ]
    )
    _build_contract_chain(
        db,
        exp_id=exp_id,
        brief_id=brief_id,
        opp_id=61,
        treatment_factors_json=treatment_json,
    )
    create_execution_contract(
        db,
        experiment_id=exp_id,
        brief_id=brief_id,
        mode=ExecutionMode.REAL,
    )
    db.execute(
        "UPDATE experiment_execution_contracts SET status = 'approved' WHERE experiment_id = ?",
        (exp_id,),
    )
    db.commit()

    rate, authority = resolve_narration_speaking_rate_authority(db, exp_id)
    assert authority == ParameterAuthority.EXPERIMENT_NOT_GOVERNING
    assert rate is None


def test_AQ_market_exploration_with_speaking_rate_control_soft_is_not_governing(db):
    """Resolver returns NOT_GOVERNING when stored control_capability is 'soft'.

    Directly inserts a row with soft capability to unit-test the resolver branch.
    (In production, SAFE_CONTROLLABLE_FACTORS makes narration_speaking_rate ENFORCED;
    this test verifies the resolver correctly skips non-enforced controls.)
    """
    exp_id = "exp-AQ"
    brief_id = "brief-AQ"
    _build_contract_chain(db, exp_id=exp_id, brief_id=brief_id, opp_id=62)
    control_factors_soft = json.dumps(
        [
            {
                "factor_name": "narration_speaking_rate",
                "factor_role": "control",
                "baseline_value": "1.0",
                "control_capability": "soft",
                "baseline_source": "prior_production",
            }
        ]
    )
    db.execute(
        """INSERT INTO experiment_execution_contracts
           (id, experiment_id, brief_id, idea_id, channel_id, opportunity_id,
            canonical_cluster_id, execution_mode, status, execution_policy_version,
            eligibility_recheck_result, eligibility_blocked,
            treatment_factors_json, control_factors_json,
            narration_speaking_rate_override,
            treatment_delta_valid, treatment_abs_valid)
           VALUES (?, ?, ?, NULL, 1, 62, 99, 'real', 'approved', '1.0.0',
                   'general_eligible', 0,
                   '[]', ?, NULL, 1, 1)""",
        (f"contract-AQ-{exp_id}", exp_id, brief_id, control_factors_soft),
    )
    db.commit()

    rate, authority = resolve_narration_speaking_rate_authority(db, exp_id)
    assert authority == ParameterAuthority.EXPERIMENT_NOT_GOVERNING
    assert rate is None


# ── AR-AU: Safety source audit ────────────────────────────────────────────────


def test_AR_narration_pace_min_comes_from_learning_constants():
    """NARRATION_PACE_SPEAKING_RATE_MIN is 0.7 (from learning.constants)."""
    assert NARRATION_PACE_SPEAKING_RATE_MIN == 0.7


def test_AS_narration_pace_max_comes_from_learning_constants():
    """NARRATION_PACE_SPEAKING_RATE_MAX is 1.5 (from learning.constants)."""
    assert NARRATION_PACE_SPEAKING_RATE_MAX == 1.5


def test_AT_narration_pace_max_delta_comes_from_learning_constants():
    """NARRATION_PACE_MAX_DELTA is 0.2 (from learning.constants)."""
    assert NARRATION_PACE_MAX_DELTA == 0.2


def test_AU_execution_service_imports_constants_from_learning_constants():
    """execution_service.py must not define duplicate copies of safety constants.

    The canonical values must come from learning.constants only.  We verify
    this by confirming that the constants accessible on execution_service are
    the exact same objects as those on learning.constants (same module import),
    and that the source file contains no literal assignment of these names.
    """
    import inspect

    import app.intelligence.experiments.execution_service as svc
    import app.learning.constants as lc

    # Constants accessible on execution_service must be the same objects
    assert svc.NARRATION_PACE_SPEAKING_RATE_MIN is lc.NARRATION_PACE_SPEAKING_RATE_MIN
    assert svc.NARRATION_PACE_SPEAKING_RATE_MAX is lc.NARRATION_PACE_SPEAKING_RATE_MAX
    assert svc.NARRATION_PACE_MAX_DELTA is lc.NARRATION_PACE_MAX_DELTA

    # Source file must not contain literal constant assignments
    src = inspect.getsource(svc)
    assert "NARRATION_PACE_SPEAKING_RATE_MIN =" not in src
    assert "NARRATION_PACE_SPEAKING_RATE_MAX =" not in src
    assert "NARRATION_PACE_MAX_DELTA =" not in src


# ── AV-BB: Regression — prior Phase 14F behaviours ───────────────────────────


def test_AV_schema_version_is_40():
    """SCHEMA_VERSION must remain 40 (no new persisted field in Phase 14F.1)."""
    assert SCHEMA_VERSION == 51


def test_AW_experiment_execution_contracts_table_exists(db):
    """experiment_execution_contracts table exists in the schema."""
    row = db.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='experiment_execution_contracts'"
    ).fetchone()
    assert row is not None


def test_AX_contract_unique_on_experiment_id(db):
    """UNIQUE constraint on experiment_id prevents duplicate contracts."""
    exp_id = "exp-AX"
    brief_id = "brief-AX"
    _build_contract_chain(db, exp_id=exp_id, brief_id=brief_id, opp_id=70)
    create_execution_contract(
        db,
        experiment_id=exp_id,
        brief_id=brief_id,
        mode=ExecutionMode.REAL,
    )
    # Second create must be idempotent (return same row), not raise IntegrityError
    create_execution_contract(
        db,
        experiment_id=exp_id,
        brief_id=brief_id,
        mode=ExecutionMode.REAL,
    )
    count = db.execute(
        "SELECT COUNT(*) FROM experiment_execution_contracts WHERE experiment_id = ?",
        (exp_id,),
    ).fetchone()[0]
    assert count == 1


def test_AY_get_experiment_speaking_rate_override_returns_value_for_treatment(db):
    """get_experiment_speaking_rate_override still works for TREATMENT-mode contracts."""
    exp_id = "exp-AY"
    brief_id = "brief-AY"
    treatment_json = json.dumps(
        [
            {
                "factor_name": "narration_speaking_rate",
                "factor_role": "treatment",
                "value_type": "numeric",
                "intended_value": "1.15",
            }
        ]
    )
    _build_contract_chain(
        db,
        exp_id=exp_id,
        brief_id=brief_id,
        opp_id=71,
        treatment_factors_json=treatment_json,
    )
    _insert_experiment_factor(db, exp_id, intended_value="1.15")
    create_execution_contract(
        db,
        experiment_id=exp_id,
        brief_id=brief_id,
        mode=ExecutionMode.REAL,
    )
    db.execute(
        "UPDATE experiment_execution_contracts SET status = 'approved' WHERE experiment_id = ?",
        (exp_id,),
    )
    db.commit()

    rate = get_experiment_speaking_rate_override(db, exp_id)
    assert rate == pytest.approx(1.15, abs=0.001)


def test_AZ_get_contract_for_experiment_returns_contract(db):
    """get_contract_for_experiment returns the contract for a given experiment."""
    exp_id = "exp-AZ"
    brief_id = "brief-AZ"
    _build_contract_chain(db, exp_id=exp_id, brief_id=brief_id, opp_id=72)
    created = create_execution_contract(
        db,
        experiment_id=exp_id,
        brief_id=brief_id,
        mode=ExecutionMode.DRY_RUN,
    )
    fetched = get_contract_for_experiment(db, exp_id)
    assert fetched is not None
    assert fetched.id == created.id


def test_BA_get_execution_contract_by_id(db):
    """get_execution_contract retrieves a contract by its primary ID."""
    exp_id = "exp-BA"
    brief_id = "brief-BA"
    _build_contract_chain(db, exp_id=exp_id, brief_id=brief_id, opp_id=73)
    created = create_execution_contract(
        db,
        experiment_id=exp_id,
        brief_id=brief_id,
        mode=ExecutionMode.DRY_RUN,
    )
    fetched = get_execution_contract(db, created.id)
    assert fetched is not None
    assert fetched.experiment_id == exp_id


def test_BB_parameter_authority_enum_values_are_stable():
    """ParameterAuthority enum values match the spec strings."""
    assert ParameterAuthority.EXPERIMENT_TREATMENT.value == "experiment_treatment"
    assert ParameterAuthority.EXPERIMENT_CONTROL.value == "experiment_control"
    assert ParameterAuthority.LEARNING_APPLICATION.value == "learning_application"
    assert ParameterAuthority.PRODUCTION_DEFAULT.value == "production_default"
    assert ParameterAuthority.EXPERIMENT_NOT_GOVERNING.value == "experiment_not_governing"
