"""Phase 14F — Experiment Execution Contract tests.

Groups A–X: 78+ tests covering:
  A  — Schema version and table existence
  B  — Contract creation basics (dry_run mode)
  C  — Idempotency (second call returns same row)
  D  — Lineage validation (channel / opportunity mismatch)
  E  — Eligibility recheck (blocked states)
  F  — Treatment config building from experiment_factors
  G  — Delta safety validation (narration_speaking_rate)
  H  — Absolute bounds validation
  I  — Control config building
  J  — Narration speaking_rate override extraction
  K  — Real-mode override: get_experiment_speaking_rate_override
  L  — Real-mode blocked when safety violated
  M  — Contract status lifecycle
  N  — Fidelity: NOT_YET_AVAILABLE (no feature snapshot)
  O  — Fidelity: NOT_OBSERVABLE (column is NULL)
  P  — Fidelity: MATCHED (numeric within tolerance)
  Q  — Fidelity: DEVIATED (numeric out of tolerance)
  R  — Fidelity: MATCHED (boolean/categorical)
  S  — Fidelity: DEVIATED (boolean/categorical mismatch)
  T  — valid_for_learning derivation
  U  — Control drift fidelity and confounding_risk_realized
  V  — persist_fidelity writes to DB
  W  — get_execution_contract / get_contract_for_experiment reads
  X  — Schema integrity (column presence, constraints)

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

import pytest

from app.core.database import SCHEMA_VERSION, open_db
from app.intelligence.experiments.execution_contract import (
    ExecutionMode,
    FidelityOutcome,
)
from app.intelligence.experiments.execution_service import (
    ExecutionContractError,
    compare_intended_vs_actual,
    create_execution_contract,
    get_contract_for_experiment,
    get_execution_contract,
    get_experiment_speaking_rate_override,
    persist_fidelity,
)
from app.learning.constants import NARRATION_PACE_MAX_DELTA

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def db(tmp_path):
    conn = open_db(tmp_path / "test.db")
    yield conn
    conn.close()


def _insert_channel(db: sqlite3.Connection, channel_id: int = 1) -> None:
    db.execute(
        """INSERT OR IGNORE INTO channels
           (id, platform, channel_name, platform_channel_id)
           VALUES (?, 'youtube', 'Test Channel', ?)""",
        (channel_id, f"UC{channel_id}test"),
    )


def _insert_cluster(db: sqlite3.Connection, cluster_id: int = 99) -> None:
    db.execute(
        """INSERT OR IGNORE INTO market_canonical_clusters
           (id, canonical_label, normalized_label, semantic_fingerprint)
           VALUES (?, 'test cluster', 'test cluster', 'fp-test')""",
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
    # Insert channel_profile_version (required by FK chain for discovery_run)
    db.execute(
        """INSERT OR IGNORE INTO channel_profile_versions
           (channel_id, primary_niche, status, version)
           VALUES (?, 'Python tutorials', 'active', 1)""",
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
    exp_id: str = "exp-001",
    channel_id: int = 1,
    opp_id: int = 1,
    status: str = "planned",
) -> str:
    db.execute(
        """INSERT OR IGNORE INTO experiments
           (id, channel_id, opportunity_id, experiment_type, status, hypothesis, input_hash)
           VALUES (?, ?, ?, 'exploration', ?, 'test hypothesis', 'hash-001')""",
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
    db: sqlite3.Connection, run_id: str = "run-1", channel_id: int = 1
) -> None:
    db.execute(
        """INSERT OR IGNORE INTO experiment_planning_runs
           (id, channel_id, status, eligible_count, exploration_only_count,
            general_eligible_count, selected_count, deferred_count, input_hash)
           VALUES (?, ?, 'completed', 1, 0, 1, 1, 0, 'hash-pr')""",
        (run_id, channel_id),
    )


def _insert_candidate_score(
    db: sqlite3.Connection,
    run_id: str = "run-1",
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
                   '[]', '[]', 'low', 0.5, 'hash-cs')
           RETURNING id""",
        (run_id, opp_id, channel_id, cluster_id, eligibility),
    ).fetchone()
    return r["id"]


def _insert_selection_decision(
    db: sqlite3.Connection,
    candidate_score_id: int,
    opp_id: int = 1,
    run_id: str = "run-1",
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
    brief_id: str = "brief-001",
    channel_id: int = 1,
    opp_id: int = 1,
    cluster_id: int | None = 99,
    status: str = "pending_approval",
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
           VALUES (?, ?, 'run-1', ?, ?, ?, 'feature_exploration', 'exploration',
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
    exp_id: str = "exp-001",
    brief_id: str = "brief-001",
    channel_id: int = 1,
    opp_id: int = 1,
    cluster_id: int | None = 99,
    treatment_factors_json: str = "[]",
    controlled_factors_json: str = "[]",
    exp_status: str = "planned",
    eligibility: str = "general_eligible",
) -> tuple[str, str]:
    """Insert the full chain and return (exp_id, brief_id)."""
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
    # Ensure candidate score has the correct eligibility
    db.execute(
        "UPDATE experiment_candidate_scores SET eligibility_classification = ? "
        "WHERE opportunity_id = ?",
        (eligibility, opp_id),
    )
    db.commit()
    return exp_id, brief_id


_plan_counter = 0


def _insert_production_plan(
    db: sqlite3.Connection,
    experiment_id: str = "exp-001",
) -> int:
    """Insert a production_plan row with FK constraints off. Returns the plan id."""
    global _plan_counter
    _plan_counter += 1
    db.commit()
    db.execute("PRAGMA foreign_keys = OFF")
    db.commit()
    r = db.execute(
        """INSERT INTO production_plans
           (topic_id, script_id, script_version, input_hash, script_body_hash,
            plan_schema_version, renderer_version, duration_algorithm_version,
            title, format, total_estimated_duration_s, total_word_count,
            warnings_json, requires_evidence_review, evidence_hash, experiment_id, status)
           VALUES (999, 999, 1, ?, 'body-hash',
                   '1.0', '1.0', '1.0',
                   'test plan', 'short', 60, 100,
                   '[]', 0, 'ev-hash', ?, 'approved')
           RETURNING id""",
        (f"plan-hash-{_plan_counter}", experiment_id),
    ).fetchone()
    db.commit()
    db.execute("PRAGMA foreign_keys = ON")
    db.commit()
    return r["id"]


_snapshot_counter = 0


def _insert_feature_snapshot(
    db: sqlite3.Connection,
    plan_id: int,
    narration_speaking_rate: float | None = 1.0,
    has_hook: int | None = 1,
    has_cta: int | None = 0,
    render_caption_burn_in: int | None = 0,
    script_format: str | None = "narrative",
    narration_voice_id: str | None = "voice-01",
    publish_day_of_week: int | None = None,
) -> None:
    global _snapshot_counter
    _snapshot_counter += 1
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
            _snapshot_counter,
            f"snap-hash-{_snapshot_counter}",
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


# ── A: Schema version ─────────────────────────────────────────────────────────


def test_A_schema_version_is_40(db):
    assert SCHEMA_VERSION == 51


def test_A2_schema_version_row_is_40(db):
    row = db.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    assert row["version"] == 51


def test_A3_execution_contract_table_exists(db):
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='experiment_execution_contracts'"
    ).fetchone()
    assert row is not None


def test_A4_required_columns_present(db):
    cols = {r["name"] for r in db.execute("PRAGMA table_info(experiment_execution_contracts)")}
    required = {
        "id",
        "experiment_id",
        "brief_id",
        "channel_id",
        "opportunity_id",
        "execution_mode",
        "eligibility_recheck_result",
        "eligibility_blocked",
        "treatment_factors_json",
        "control_factors_json",
        "narration_speaking_rate_override",
        "treatment_delta_valid",
        "treatment_abs_valid",
        "status",
        "execution_policy_version",
        "fidelity_json",
        "valid_for_learning",
        "confounding_risk_realized",
        "created_at",
        "approved_at",
        "executed_at",
        "completed_at",
    }
    assert required <= cols


def test_A5_unique_constraint_on_experiment_id(db):
    """Two contracts for the same experiment must not be insertable."""
    _insert_opportunity(db)
    _insert_experiment(db)
    _insert_brief(db)
    db.commit()
    db.execute(
        """INSERT INTO experiment_execution_contracts
           (id, experiment_id, brief_id, channel_id, opportunity_id,
            execution_mode, status, execution_policy_version)
           VALUES ('c1', 'exp-001', 'brief-001', 1, 1, 'dry_run', 'pending', '1.0.0')"""
    )
    db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """INSERT INTO experiment_execution_contracts
               (id, experiment_id, brief_id, channel_id, opportunity_id,
                execution_mode, status, execution_policy_version)
               VALUES ('c2', 'exp-001', 'brief-001', 1, 1, 'dry_run', 'pending', '1.0.0')"""
        )


# ── B: Contract creation basics ────────────────────────────────────────────────


def test_B_create_contract_dry_run_returns_contract(db):
    exp_id, brief_id = _build_contract_chain(db)
    contract = create_execution_contract(db, exp_id, brief_id)
    db.commit()
    assert contract.experiment_id == exp_id
    assert contract.brief_id == brief_id
    assert contract.execution_mode == ExecutionMode.DRY_RUN
    assert contract.id is not None


def test_B2_create_contract_persisted_to_db(db):
    exp_id, brief_id = _build_contract_chain(db)
    contract = create_execution_contract(db, exp_id, brief_id)
    db.commit()
    row = db.execute(
        "SELECT * FROM experiment_execution_contracts WHERE id = ?", (contract.id,)
    ).fetchone()
    assert row is not None
    assert row["experiment_id"] == exp_id
    assert row["execution_mode"] == "dry_run"


def test_B3_create_contract_real_mode(db):
    exp_id, brief_id = _build_contract_chain(db)
    contract = create_execution_contract(db, exp_id, brief_id, mode=ExecutionMode.REAL)
    db.commit()
    assert contract.execution_mode == ExecutionMode.REAL


def test_B4_contract_captures_channel_and_opportunity(db):
    exp_id, brief_id = _build_contract_chain(db)
    contract = create_execution_contract(db, exp_id, brief_id)
    assert contract.channel_id == 1
    assert contract.opportunity_id == 1


def test_B5_contract_captures_canonical_cluster_id(db):
    exp_id, brief_id = _build_contract_chain(db, cluster_id=99)
    contract = create_execution_contract(db, exp_id, brief_id)
    assert contract.canonical_cluster_id == 99


def test_B6_contract_captures_policy_version(db):
    exp_id, brief_id = _build_contract_chain(db)
    contract = create_execution_contract(db, exp_id, brief_id, policy_version="2.0.0")
    assert contract.execution_policy_version == "2.0.0"


def test_B7_missing_experiment_raises(db):
    _insert_opportunity(db)
    _insert_brief(db)
    db.commit()
    with pytest.raises(ExecutionContractError, match="not found"):
        create_execution_contract(db, "nonexistent-exp", "brief-001")


def test_B8_missing_brief_raises(db):
    _insert_opportunity(db)
    _insert_experiment(db)
    db.commit()
    with pytest.raises(ExecutionContractError, match="not found"):
        create_execution_contract(db, "exp-001", "nonexistent-brief")


def test_B9_cancelled_experiment_raises(db):
    exp_id, brief_id = _build_contract_chain(db, exp_status="cancelled")
    with pytest.raises(ExecutionContractError, match="terminal status"):
        create_execution_contract(db, exp_id, brief_id)


def test_B10_completed_experiment_raises(db):
    exp_id, brief_id = _build_contract_chain(db, exp_status="completed")
    with pytest.raises(ExecutionContractError, match="terminal status"):
        create_execution_contract(db, exp_id, brief_id)


# ── C: Idempotency ────────────────────────────────────────────────────────────


def test_C_idempotent_returns_same_id(db):
    exp_id, brief_id = _build_contract_chain(db)
    c1 = create_execution_contract(db, exp_id, brief_id)
    db.commit()
    c2 = create_execution_contract(db, exp_id, brief_id)
    assert c1.id == c2.id


def test_C2_idempotent_only_one_row(db):
    exp_id, brief_id = _build_contract_chain(db)
    create_execution_contract(db, exp_id, brief_id)
    db.commit()
    create_execution_contract(db, exp_id, brief_id)
    db.commit()
    count = db.execute(
        "SELECT COUNT(*) FROM experiment_execution_contracts WHERE experiment_id = ?",
        (exp_id,),
    ).fetchone()[0]
    assert count == 1


def test_C3_idempotent_returns_original_mode(db):
    exp_id, brief_id = _build_contract_chain(db)
    c1 = create_execution_contract(db, exp_id, brief_id, mode=ExecutionMode.REAL)
    db.commit()
    # Second call with dry_run — should still return real (original row)
    c2 = create_execution_contract(db, exp_id, brief_id, mode=ExecutionMode.DRY_RUN)
    assert c2.execution_mode == ExecutionMode.REAL
    assert c2.id == c1.id


# ── D: Lineage validation ─────────────────────────────────────────────────────


def test_D_channel_mismatch_raises(db):
    _insert_opportunity(db, opp_id=1, channel_id=1)
    _insert_opportunity(db, opp_id=2, channel_id=2)
    _insert_experiment(db, "exp-001", channel_id=1, opp_id=1)
    # Brief is for channel 2
    _insert_brief(db, "brief-001", channel_id=2, opp_id=2)
    db.commit()
    with pytest.raises(ExecutionContractError, match="[Cc]hannel"):
        create_execution_contract(db, "exp-001", "brief-001")


def test_D2_superseded_brief_raises(db):
    _insert_opportunity(db)
    _insert_experiment(db)
    # Manually insert a superseded brief
    _insert_planning_run(db)
    cs_id = _insert_candidate_score(db)
    sd_id = _insert_selection_decision(db, cs_id)
    db.execute(
        """INSERT INTO experiment_strategy_briefs
           (id, channel_id, planning_run_id, selection_decision_id, opportunity_id,
            brief_planning_intent, experiment_type, hypothesis, target_metric,
            target_direction, brief_hash, status)
           VALUES ('brief-sup', 1, 'run-1', ?, 1, 'feature_exploration', 'exploration',
                   'h', 'avg', 'higher_is_better', 'hash-sup', 'superseded')""",
        (sd_id,),
    )
    db.commit()
    with pytest.raises(ExecutionContractError, match="superseded"):
        create_execution_contract(db, "exp-001", "brief-sup")


# ── E: Eligibility recheck ────────────────────────────────────────────────────


def test_E_eligible_opportunity_not_blocked(db):
    exp_id, brief_id = _build_contract_chain(db, eligibility="general_eligible")
    contract = create_execution_contract(db, exp_id, brief_id)
    assert not contract.eligibility_blocked
    assert contract.eligibility_recheck_result == "general_eligible"


def test_E2_ineligible_opportunity_blocked(db):
    exp_id, brief_id = _build_contract_chain(db, eligibility="ineligible")
    contract = create_execution_contract(db, exp_id, brief_id)
    assert contract.eligibility_blocked
    assert contract.status == "blocked"


def test_E3_unresolved_opportunity_blocked(db):
    exp_id, brief_id = _build_contract_chain(db, eligibility="unresolved")
    contract = create_execution_contract(db, exp_id, brief_id)
    assert contract.eligibility_blocked
    assert contract.status == "blocked"


def test_E4_requires_refresh_blocked(db):
    exp_id, brief_id = _build_contract_chain(db, eligibility="requires_refresh")
    contract = create_execution_contract(db, exp_id, brief_id)
    assert contract.eligibility_blocked


def test_E5_stale_blocked(db):
    exp_id, brief_id = _build_contract_chain(db, eligibility="stale")
    contract = create_execution_contract(db, exp_id, brief_id)
    assert contract.eligibility_blocked


def test_E6_no_candidate_score_recheck_result_is_none(db):
    """No score row means eligibility_recheck_result=None and NOT blocked."""
    _insert_opportunity(db)
    _insert_experiment(db)
    _insert_brief(db)
    db.commit()  # close transaction so pragma takes effect
    # Disable FKs temporarily to wipe scores without breaking the brief chain
    db.execute("PRAGMA foreign_keys = OFF")
    db.commit()
    db.execute("DELETE FROM experiment_candidate_scores")
    db.commit()
    db.execute("PRAGMA foreign_keys = ON")
    db.commit()
    contract = create_execution_contract(db, "exp-001", "brief-001")
    assert contract.eligibility_recheck_result is None
    assert not contract.eligibility_blocked


# ── F: Treatment config from experiment_factors ───────────────────────────────


def test_F_treatment_config_reads_intended_value(db):
    exp_id, brief_id = _build_contract_chain(
        db,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                }
            ]
        ),
    )
    _insert_experiment_factor(db, exp_id, "narration_speaking_rate", "treatment", "numeric", "1.1")
    db.commit()
    contract = create_execution_contract(db, exp_id, brief_id)
    assert len(contract.treatment_configs) == 1
    tc = contract.treatment_configs[0]
    assert tc.factor_name == "narration_speaking_rate"
    assert tc.intended_value == "1.1"


def test_F2_no_experiment_factor_row_intended_value_is_none(db):
    exp_id, brief_id = _build_contract_chain(
        db,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                }
            ]
        ),
    )
    # No experiment_factors row inserted
    contract = create_execution_contract(db, exp_id, brief_id)
    assert contract.treatment_configs[0].intended_value is None


def test_F3_multiple_treatment_factors(db):
    exp_id, brief_id = _build_contract_chain(
        db,
        treatment_factors_json=json.dumps(
            [
                {"factor_name": "has_hook", "factor_role": "treatment", "value_type": "boolean"},
                {"factor_name": "has_cta", "factor_role": "treatment", "value_type": "boolean"},
            ]
        ),
    )
    _insert_experiment_factor(db, exp_id, "has_hook", "treatment", "boolean", "true")
    _insert_experiment_factor(db, exp_id, "has_cta", "treatment", "boolean", "false")
    db.commit()
    contract = create_execution_contract(db, exp_id, brief_id)
    names = {tc.factor_name for tc in contract.treatment_configs}
    assert "has_hook" in names
    assert "has_cta" in names


# ── G: Delta safety ───────────────────────────────────────────────────────────


def test_G_delta_within_limit_is_valid(db):
    baseline = 1.0
    intended = baseline + NARRATION_PACE_MAX_DELTA  # exactly at limit
    exp_id, brief_id = _build_contract_chain(
        db,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                }
            ]
        ),
        controlled_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "baseline_value": str(baseline),
                    "baseline_source": "voice_profile",
                    "factor_role": "controlled",
                }
            ]
        ),
    )
    _insert_experiment_factor(
        db, exp_id, "narration_speaking_rate", "treatment", "numeric", str(intended)
    )
    db.commit()
    contract = create_execution_contract(db, exp_id, brief_id)
    assert contract.treatment_delta_valid


def test_G2_delta_exceeds_limit_is_invalid(db):
    baseline = 1.0
    intended = baseline + NARRATION_PACE_MAX_DELTA + 0.01  # over limit
    exp_id, brief_id = _build_contract_chain(
        db,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                }
            ]
        ),
        controlled_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "baseline_value": str(baseline),
                    "baseline_source": "voice_profile",
                    "factor_role": "controlled",
                }
            ]
        ),
    )
    _insert_experiment_factor(
        db, exp_id, "narration_speaking_rate", "treatment", "numeric", str(intended)
    )
    db.commit()
    contract = create_execution_contract(db, exp_id, brief_id)
    assert not contract.treatment_delta_valid


def test_G3_no_baseline_delta_valid_true(db):
    """No baseline in controlled_factors → delta check skipped → delta_valid=True."""
    exp_id, brief_id = _build_contract_chain(
        db,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                }
            ]
        ),
    )
    _insert_experiment_factor(db, exp_id, "narration_speaking_rate", "treatment", "numeric", "0.9")
    db.commit()
    contract = create_execution_contract(db, exp_id, brief_id)
    assert contract.treatment_delta_valid


def test_G4_delta_config_reflects_per_tc(db):
    baseline = 1.0
    intended = baseline + NARRATION_PACE_MAX_DELTA + 0.05
    exp_id, brief_id = _build_contract_chain(
        db,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                }
            ]
        ),
        controlled_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "baseline_value": str(baseline),
                    "baseline_source": "voice_profile",
                    "factor_role": "controlled",
                }
            ]
        ),
    )
    _insert_experiment_factor(
        db, exp_id, "narration_speaking_rate", "treatment", "numeric", str(intended)
    )
    db.commit()
    contract = create_execution_contract(db, exp_id, brief_id)
    tc = contract.treatment_configs[0]
    assert not tc.delta_valid
    assert tc.delta_baseline == str(baseline)


# ── H: Absolute bounds ────────────────────────────────────────────────────────


def test_H_within_absolute_bounds_is_valid(db):
    exp_id, brief_id = _build_contract_chain(
        db,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                }
            ]
        ),
    )
    _insert_experiment_factor(db, exp_id, "narration_speaking_rate", "treatment", "numeric", "1.0")
    db.commit()
    contract = create_execution_contract(db, exp_id, brief_id)
    assert contract.treatment_abs_valid


def test_H2_below_absolute_min_is_invalid(db):
    exp_id, brief_id = _build_contract_chain(
        db,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                }
            ]
        ),
    )
    _insert_experiment_factor(db, exp_id, "narration_speaking_rate", "treatment", "numeric", "0.5")
    db.commit()
    contract = create_execution_contract(db, exp_id, brief_id)
    assert not contract.treatment_abs_valid


def test_H3_above_absolute_max_is_invalid(db):
    exp_id, brief_id = _build_contract_chain(
        db,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                }
            ]
        ),
    )
    _insert_experiment_factor(db, exp_id, "narration_speaking_rate", "treatment", "numeric", "2.0")
    db.commit()
    contract = create_execution_contract(db, exp_id, brief_id)
    assert not contract.treatment_abs_valid


def test_H4_abs_valid_flag_on_tc(db):
    exp_id, brief_id = _build_contract_chain(
        db,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                }
            ]
        ),
    )
    _insert_experiment_factor(db, exp_id, "narration_speaking_rate", "treatment", "numeric", "0.5")
    db.commit()
    contract = create_execution_contract(db, exp_id, brief_id)
    tc = contract.treatment_configs[0]
    assert not tc.abs_valid


# ── I: Control config building ────────────────────────────────────────────────


def test_I_control_configs_built_from_brief(db):
    exp_id, brief_id = _build_contract_chain(
        db,
        controlled_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "baseline_value": "1.0",
                    "baseline_source": "voice_profile",
                    "factor_role": "controlled",
                }
            ]
        ),
    )
    contract = create_execution_contract(db, exp_id, brief_id)
    assert len(contract.control_configs) == 1
    cc = contract.control_configs[0]
    assert cc.factor_name == "narration_speaking_rate"
    assert cc.baseline_value == "1.0"
    assert cc.baseline_source == "voice_profile"


def test_I2_control_capability_from_registry(db):
    """narration_speaking_rate is ENFORCED per SAFE_CONTROLLABLE_FACTORS."""
    exp_id, brief_id = _build_contract_chain(
        db,
        controlled_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "baseline_value": "1.0",
                    "baseline_source": "voice_profile",
                    "factor_role": "controlled",
                }
            ]
        ),
    )
    contract = create_execution_contract(db, exp_id, brief_id)
    cc = contract.control_configs[0]
    assert cc.control_capability == "enforced"


def test_I3_soft_control_capability(db):
    """has_hook is SOFT per registry."""
    exp_id, brief_id = _build_contract_chain(
        db,
        controlled_factors_json=json.dumps(
            [
                {
                    "factor_name": "has_hook",
                    "baseline_value": "true",
                    "baseline_source": "feature_snapshot",
                    "factor_role": "controlled",
                }
            ]
        ),
    )
    contract = create_execution_contract(db, exp_id, brief_id)
    cc = contract.control_configs[0]
    assert cc.control_capability == "soft"


# ── J: Narration speaking_rate override extraction ─────────────────────────────


def test_J_speaking_rate_override_set_when_treatment_present(db):
    exp_id, brief_id = _build_contract_chain(
        db,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                }
            ]
        ),
    )
    _insert_experiment_factor(db, exp_id, "narration_speaking_rate", "treatment", "numeric", "1.1")
    db.commit()
    contract = create_execution_contract(db, exp_id, brief_id)
    assert contract.narration_speaking_rate_override == pytest.approx(1.1)


def test_J2_speaking_rate_override_none_when_no_treatment(db):
    exp_id, brief_id = _build_contract_chain(db)  # no treatment factors
    contract = create_execution_contract(db, exp_id, brief_id)
    assert contract.narration_speaking_rate_override is None


def test_J3_speaking_rate_override_none_when_intended_value_none(db):
    exp_id, brief_id = _build_contract_chain(
        db,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                }
            ]
        ),
    )
    # No experiment_factor row → intended_value=None
    contract = create_execution_contract(db, exp_id, brief_id)
    assert contract.narration_speaking_rate_override is None


# ── K: get_experiment_speaking_rate_override ──────────────────────────────────


def test_K_returns_none_when_no_contract(db):
    assert get_experiment_speaking_rate_override(db, "nonexistent") is None


def test_K2_returns_none_for_dry_run_contract(db):
    exp_id, brief_id = _build_contract_chain(
        db,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                }
            ]
        ),
    )
    _insert_experiment_factor(db, exp_id, "narration_speaking_rate", "treatment", "numeric", "1.1")
    db.commit()
    create_execution_contract(db, exp_id, brief_id, mode=ExecutionMode.DRY_RUN)
    db.commit()
    # dry_run → must NOT expose override to narration
    result = get_experiment_speaking_rate_override(db, exp_id)
    assert result is None


def test_K3_returns_override_for_real_approved_contract(db):
    exp_id, brief_id = _build_contract_chain(
        db,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                }
            ]
        ),
    )
    _insert_experiment_factor(db, exp_id, "narration_speaking_rate", "treatment", "numeric", "1.2")
    db.commit()
    contract = create_execution_contract(db, exp_id, brief_id, mode=ExecutionMode.REAL)
    db.execute(
        "UPDATE experiment_execution_contracts SET status = 'approved' WHERE id = ?",
        (contract.id,),
    )
    db.commit()
    result = get_experiment_speaking_rate_override(db, exp_id)
    assert result == pytest.approx(1.2)


def test_K4_returns_none_for_real_pending_contract(db):
    """REAL mode but status=pending → not yet approved → no override."""
    exp_id, brief_id = _build_contract_chain(
        db,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                }
            ]
        ),
    )
    _insert_experiment_factor(db, exp_id, "narration_speaking_rate", "treatment", "numeric", "1.2")
    db.commit()
    create_execution_contract(db, exp_id, brief_id, mode=ExecutionMode.REAL)
    db.commit()
    result = get_experiment_speaking_rate_override(db, exp_id)
    assert result is None


def test_K5_returns_none_when_override_is_null(db):
    """REAL + approved but no narration treatment → narration_speaking_rate_override=NULL."""
    exp_id, brief_id = _build_contract_chain(db)
    contract = create_execution_contract(db, exp_id, brief_id, mode=ExecutionMode.REAL)
    db.execute(
        "UPDATE experiment_execution_contracts SET status = 'approved' WHERE id = ?",
        (contract.id,),
    )
    db.commit()
    result = get_experiment_speaking_rate_override(db, exp_id)
    assert result is None


# ── L: Real-mode blocked when safety violated ─────────────────────────────────


def test_L_real_mode_blocked_when_abs_invalid(db):
    exp_id, brief_id = _build_contract_chain(
        db,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                }
            ]
        ),
    )
    _insert_experiment_factor(db, exp_id, "narration_speaking_rate", "treatment", "numeric", "0.5")
    db.commit()
    contract = create_execution_contract(db, exp_id, brief_id, mode=ExecutionMode.REAL)
    assert contract.status == "blocked"


def test_L2_dry_run_not_blocked_even_when_abs_invalid(db):
    """Dry run always succeeds on safety: it records the problem but doesn't block."""
    exp_id, brief_id = _build_contract_chain(
        db,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                }
            ]
        ),
    )
    _insert_experiment_factor(db, exp_id, "narration_speaking_rate", "treatment", "numeric", "0.5")
    db.commit()
    contract = create_execution_contract(db, exp_id, brief_id, mode=ExecutionMode.DRY_RUN)
    assert contract.status == "pending"
    assert not contract.treatment_abs_valid


def test_L3_eligible_real_contract_status_is_pending(db):
    exp_id, brief_id = _build_contract_chain(db)
    contract = create_execution_contract(db, exp_id, brief_id, mode=ExecutionMode.REAL)
    assert contract.status == "pending"


# ── M: Contract status lifecycle ──────────────────────────────────────────────


def test_M_initial_status_is_pending(db):
    exp_id, brief_id = _build_contract_chain(db)
    contract = create_execution_contract(db, exp_id, brief_id)
    assert contract.status == "pending"


def test_M2_blocked_eligibility_status_is_blocked(db):
    exp_id, brief_id = _build_contract_chain(db, eligibility="ineligible")
    contract = create_execution_contract(db, exp_id, brief_id)
    assert contract.status == "blocked"


# ── N: Fidelity — NOT_YET_AVAILABLE ──────────────────────────────────────────


def test_N_fidelity_not_yet_available_when_no_snapshot(db):
    exp_id, brief_id = _build_contract_chain(
        db,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                }
            ]
        ),
    )
    _insert_experiment_factor(db, exp_id, "narration_speaking_rate", "treatment", "numeric", "1.0")
    db.commit()
    contract = create_execution_contract(db, exp_id, brief_id)
    db.commit()
    fidelity = compare_intended_vs_actual(db, contract)
    assert fidelity.treatment_outcomes[0].outcome == FidelityOutcome.NOT_YET_AVAILABLE


def test_N2_valid_for_learning_is_none_when_not_yet_available(db):
    exp_id, brief_id = _build_contract_chain(
        db,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                }
            ]
        ),
    )
    _insert_experiment_factor(db, exp_id, "narration_speaking_rate", "treatment", "numeric", "1.0")
    db.commit()
    contract = create_execution_contract(db, exp_id, brief_id)
    db.commit()
    fidelity = compare_intended_vs_actual(db, contract)
    assert fidelity.valid_for_learning is None


# ── O: Fidelity — NOT_OBSERVABLE ─────────────────────────────────────────────


def test_O_fidelity_not_observable_when_column_is_null(db):
    exp_id, brief_id = _build_contract_chain(
        db,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                }
            ]
        ),
    )
    _insert_experiment_factor(db, exp_id, "narration_speaking_rate", "treatment", "numeric", "1.0")
    db.commit()
    contract = create_execution_contract(db, exp_id, brief_id)
    db.commit()
    plan_id = _insert_production_plan(db, experiment_id=exp_id)
    _insert_feature_snapshot(db, plan_id=plan_id, narration_speaking_rate=None)
    db.commit()
    fidelity = compare_intended_vs_actual(db, contract)
    assert fidelity.treatment_outcomes[0].outcome == FidelityOutcome.NOT_OBSERVABLE


# ── P: Fidelity — MATCHED (numeric) ──────────────────────────────────────────


def test_P_fidelity_matched_numeric_within_tolerance(db):
    exp_id, brief_id = _build_contract_chain(
        db,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                }
            ]
        ),
    )
    _insert_experiment_factor(db, exp_id, "narration_speaking_rate", "treatment", "numeric", "1.1")
    db.commit()
    contract = create_execution_contract(db, exp_id, brief_id)
    db.commit()
    plan_id = _insert_production_plan(db, experiment_id=exp_id)
    _insert_feature_snapshot(db, plan_id=plan_id, narration_speaking_rate=1.1)
    db.commit()
    fidelity = compare_intended_vs_actual(db, contract)
    assert fidelity.treatment_outcomes[0].outcome == FidelityOutcome.MATCHED


def test_P2_fidelity_matched_within_tolerance_abs(db):
    """actual == intended + 0.0005 → within tolerance_abs=0.001 → MATCHED."""
    exp_id, brief_id = _build_contract_chain(
        db,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                }
            ]
        ),
    )
    _insert_experiment_factor(db, exp_id, "narration_speaking_rate", "treatment", "numeric", "1.1")
    db.commit()
    contract = create_execution_contract(db, exp_id, brief_id)
    db.commit()
    plan_id = _insert_production_plan(db, experiment_id=exp_id)
    _insert_feature_snapshot(db, plan_id=plan_id, narration_speaking_rate=1.1005)
    db.commit()
    fidelity = compare_intended_vs_actual(db, contract)
    assert fidelity.treatment_outcomes[0].outcome == FidelityOutcome.MATCHED


# ── Q: Fidelity — DEVIATED (numeric) ─────────────────────────────────────────


def test_Q_fidelity_deviated_numeric_out_of_tolerance(db):
    exp_id, brief_id = _build_contract_chain(
        db,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                }
            ]
        ),
    )
    _insert_experiment_factor(db, exp_id, "narration_speaking_rate", "treatment", "numeric", "1.1")
    db.commit()
    contract = create_execution_contract(db, exp_id, brief_id)
    db.commit()
    plan_id = _insert_production_plan(db, experiment_id=exp_id)
    _insert_feature_snapshot(db, plan_id=plan_id, narration_speaking_rate=0.9)
    db.commit()
    fidelity = compare_intended_vs_actual(db, contract)
    assert fidelity.treatment_outcomes[0].outcome == FidelityOutcome.DEVIATED


# ── R: Fidelity — MATCHED (boolean) ──────────────────────────────────────────


def test_R_fidelity_matched_boolean(db):
    exp_id, brief_id = _build_contract_chain(
        db,
        treatment_factors_json=json.dumps(
            [{"factor_name": "has_hook", "factor_role": "treatment", "value_type": "boolean"}]
        ),
    )
    _insert_experiment_factor(db, exp_id, "has_hook", "treatment", "boolean", "true")
    db.commit()
    contract = create_execution_contract(db, exp_id, brief_id)
    db.commit()
    plan_id = _insert_production_plan(db, experiment_id=exp_id)
    _insert_feature_snapshot(db, plan_id=plan_id, has_hook=1)  # 1 → "true"
    db.commit()
    fidelity = compare_intended_vs_actual(db, contract)
    tc_outcome = next(f for f in fidelity.treatment_outcomes if f.factor_name == "has_hook")
    assert tc_outcome.outcome == FidelityOutcome.MATCHED


def test_R2_fidelity_matched_boolean_false(db):
    exp_id, brief_id = _build_contract_chain(
        db,
        treatment_factors_json=json.dumps(
            [{"factor_name": "has_hook", "factor_role": "treatment", "value_type": "boolean"}]
        ),
    )
    _insert_experiment_factor(db, exp_id, "has_hook", "treatment", "boolean", "false")
    db.commit()
    contract = create_execution_contract(db, exp_id, brief_id)
    db.commit()
    plan_id = _insert_production_plan(db, experiment_id=exp_id)
    _insert_feature_snapshot(db, plan_id=plan_id, has_hook=0)  # 0 → "false"
    db.commit()
    fidelity = compare_intended_vs_actual(db, contract)
    tc_outcome = next(f for f in fidelity.treatment_outcomes if f.factor_name == "has_hook")
    assert tc_outcome.outcome == FidelityOutcome.MATCHED


# ── S: Fidelity — DEVIATED (boolean mismatch) ─────────────────────────────────


def test_S_fidelity_deviated_boolean_mismatch(db):
    exp_id, brief_id = _build_contract_chain(
        db,
        treatment_factors_json=json.dumps(
            [{"factor_name": "has_hook", "factor_role": "treatment", "value_type": "boolean"}]
        ),
    )
    _insert_experiment_factor(db, exp_id, "has_hook", "treatment", "boolean", "true")
    db.commit()
    contract = create_execution_contract(db, exp_id, brief_id)
    db.commit()
    plan_id = _insert_production_plan(db, experiment_id=exp_id)
    _insert_feature_snapshot(db, plan_id=plan_id, has_hook=0)  # intended=true, actual=false
    db.commit()
    fidelity = compare_intended_vs_actual(db, contract)
    tc_outcome = next(f for f in fidelity.treatment_outcomes if f.factor_name == "has_hook")
    assert tc_outcome.outcome == FidelityOutcome.DEVIATED


# ── T: valid_for_learning derivation ──────────────────────────────────────────


def test_T_valid_for_learning_true_when_treatment_matched(db):
    exp_id, brief_id = _build_contract_chain(
        db,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                }
            ]
        ),
    )
    _insert_experiment_factor(db, exp_id, "narration_speaking_rate", "treatment", "numeric", "1.1")
    db.commit()
    contract = create_execution_contract(db, exp_id, brief_id)
    db.commit()
    plan_id = _insert_production_plan(db, experiment_id=exp_id)
    _insert_feature_snapshot(db, plan_id=plan_id, narration_speaking_rate=1.1)
    db.commit()
    fidelity = compare_intended_vs_actual(db, contract)
    assert fidelity.valid_for_learning is True


def test_T2_valid_for_learning_false_when_treatment_deviated(db):
    exp_id, brief_id = _build_contract_chain(
        db,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                }
            ]
        ),
    )
    _insert_experiment_factor(db, exp_id, "narration_speaking_rate", "treatment", "numeric", "1.1")
    db.commit()
    contract = create_execution_contract(db, exp_id, brief_id)
    db.commit()
    plan_id = _insert_production_plan(db, experiment_id=exp_id)
    _insert_feature_snapshot(db, plan_id=plan_id, narration_speaking_rate=0.8)
    db.commit()
    fidelity = compare_intended_vs_actual(db, contract)
    assert fidelity.valid_for_learning is False


def test_T3_valid_for_learning_none_when_unavailable(db):
    exp_id, brief_id = _build_contract_chain(
        db,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                }
            ]
        ),
    )
    _insert_experiment_factor(db, exp_id, "narration_speaking_rate", "treatment", "numeric", "1.1")
    db.commit()
    contract = create_execution_contract(db, exp_id, brief_id)
    db.commit()
    # No feature snapshot → NOT_YET_AVAILABLE
    fidelity = compare_intended_vs_actual(db, contract)
    assert fidelity.valid_for_learning is None


def test_T4_no_treatment_factors_non_market_not_valid(db):
    """Phase 14F.2 vacuous-truth fix: non-market experiment with zero treatment factors
    is malformed → NOT_VALID, not vacuously True.

    Phase 14F allowed the vacuous 'all([]) == True' result; Phase 14F.2 fixes this.
    """
    exp_id, brief_id = _build_contract_chain(db)
    contract = create_execution_contract(db, exp_id, brief_id)
    db.commit()
    plan_id = _insert_production_plan(db, experiment_id=exp_id)
    _insert_feature_snapshot(db, plan_id=plan_id)
    db.commit()
    fidelity = compare_intended_vs_actual(db, contract)
    assert fidelity.valid_for_learning is False
    from app.intelligence.experiments.execution_contract import FidelityClassification

    assert fidelity.classification == FidelityClassification.NOT_VALID


# ── U: Control drift ──────────────────────────────────────────────────────────


def test_U_control_drift_detected(db):
    exp_id, brief_id = _build_contract_chain(
        db,
        controlled_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "baseline_value": "1.0",
                    "baseline_source": "voice_profile",
                    "factor_role": "controlled",
                }
            ]
        ),
    )
    db.commit()
    contract = create_execution_contract(db, exp_id, brief_id)
    db.commit()
    plan_id = _insert_production_plan(db, experiment_id=exp_id)
    # Control factor actual value drifted from 1.0 to 0.5
    _insert_feature_snapshot(db, plan_id=plan_id, narration_speaking_rate=0.5)
    db.commit()
    fidelity = compare_intended_vs_actual(db, contract)
    co = next(f for f in fidelity.control_outcomes if f.factor_name == "narration_speaking_rate")
    assert co.outcome == FidelityOutcome.DEVIATED


def test_U2_control_drift_sets_confounding_risk_high(db):
    exp_id, brief_id = _build_contract_chain(
        db,
        controlled_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "baseline_value": "1.0",
                    "baseline_source": "voice_profile",
                    "factor_role": "controlled",
                }
            ]
        ),
    )
    db.commit()
    contract = create_execution_contract(db, exp_id, brief_id)
    db.commit()
    plan_id = _insert_production_plan(db, experiment_id=exp_id)
    _insert_feature_snapshot(db, plan_id=plan_id, narration_speaking_rate=0.5)
    db.commit()
    fidelity = compare_intended_vs_actual(db, contract)
    assert fidelity.confounding_risk_realized == "high"


def test_U3_control_no_drift_confounding_risk_low(db):
    exp_id, brief_id = _build_contract_chain(
        db,
        controlled_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "baseline_value": "1.0",
                    "baseline_source": "voice_profile",
                    "factor_role": "controlled",
                }
            ]
        ),
    )
    db.commit()
    contract = create_execution_contract(db, exp_id, brief_id)
    db.commit()
    plan_id = _insert_production_plan(db, experiment_id=exp_id)
    _insert_feature_snapshot(db, plan_id=plan_id, narration_speaking_rate=1.0)
    db.commit()
    fidelity = compare_intended_vs_actual(db, contract)
    assert fidelity.confounding_risk_realized == "low"


# ── V: persist_fidelity ────────────────────────────────────────────────────────


def test_V_persist_fidelity_writes_to_db(db):
    exp_id, brief_id = _build_contract_chain(
        db,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                }
            ]
        ),
    )
    _insert_experiment_factor(db, exp_id, "narration_speaking_rate", "treatment", "numeric", "1.1")
    db.commit()
    contract = create_execution_contract(db, exp_id, brief_id)
    db.commit()
    plan_id = _insert_production_plan(db, experiment_id=exp_id)
    _insert_feature_snapshot(db, plan_id=plan_id, narration_speaking_rate=1.1)
    db.commit()
    fidelity = compare_intended_vs_actual(db, contract)
    persist_fidelity(db, contract.id, fidelity)
    db.commit()
    row = db.execute(
        "SELECT fidelity_json, valid_for_learning, status "
        "FROM experiment_execution_contracts WHERE id = ?",
        (contract.id,),
    ).fetchone()
    assert row["fidelity_json"] is not None
    assert row["valid_for_learning"] == 1
    assert row["status"] == "completed"


def test_V2_persist_fidelity_deviated_valid_for_learning_false(db):
    exp_id, brief_id = _build_contract_chain(
        db,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                }
            ]
        ),
    )
    _insert_experiment_factor(db, exp_id, "narration_speaking_rate", "treatment", "numeric", "1.1")
    db.commit()
    contract = create_execution_contract(db, exp_id, brief_id)
    db.commit()
    plan_id = _insert_production_plan(db, experiment_id=exp_id)
    _insert_feature_snapshot(db, plan_id=plan_id, narration_speaking_rate=0.8)
    db.commit()
    fidelity = compare_intended_vs_actual(db, contract)
    persist_fidelity(db, contract.id, fidelity)
    db.commit()
    row = db.execute(
        "SELECT valid_for_learning FROM experiment_execution_contracts WHERE id = ?",
        (contract.id,),
    ).fetchone()
    assert row["valid_for_learning"] == 0


def test_V3_persist_fidelity_none_valid_for_learning_is_null(db):
    exp_id, brief_id = _build_contract_chain(
        db,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                }
            ]
        ),
    )
    _insert_experiment_factor(db, exp_id, "narration_speaking_rate", "treatment", "numeric", "1.1")
    db.commit()
    contract = create_execution_contract(db, exp_id, brief_id)
    db.commit()
    # No snapshot → valid_for_learning=None
    fidelity = compare_intended_vs_actual(db, contract)
    persist_fidelity(db, contract.id, fidelity)
    db.commit()
    row = db.execute(
        "SELECT valid_for_learning FROM experiment_execution_contracts WHERE id = ?",
        (contract.id,),
    ).fetchone()
    assert row["valid_for_learning"] is None


# ── W: Read functions ──────────────────────────────────────────────────────────


def test_W_get_execution_contract_returns_correct_row(db):
    exp_id, brief_id = _build_contract_chain(db)
    contract = create_execution_contract(db, exp_id, brief_id)
    db.commit()
    fetched = get_execution_contract(db, contract.id)
    assert fetched is not None
    assert fetched.id == contract.id
    assert fetched.experiment_id == exp_id


def test_W2_get_execution_contract_returns_none_for_missing(db):
    result = get_execution_contract(db, "nonexistent-id")
    assert result is None


def test_W3_get_contract_for_experiment(db):
    exp_id, brief_id = _build_contract_chain(db)
    create_execution_contract(db, exp_id, brief_id)
    db.commit()
    fetched = get_contract_for_experiment(db, exp_id)
    assert fetched is not None
    assert fetched.experiment_id == exp_id


def test_W4_get_contract_for_experiment_returns_none_when_absent(db):
    assert get_contract_for_experiment(db, "nonexistent") is None


def test_W5_round_trip_treatment_configs(db):
    """TreatmentConfig round-trips through JSON serialization."""
    exp_id, brief_id = _build_contract_chain(
        db,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                }
            ]
        ),
    )
    _insert_experiment_factor(db, exp_id, "narration_speaking_rate", "treatment", "numeric", "1.15")
    db.commit()
    create_execution_contract(db, exp_id, brief_id)
    db.commit()
    fetched = get_contract_for_experiment(db, exp_id)
    assert fetched is not None
    assert len(fetched.treatment_configs) == 1
    assert fetched.treatment_configs[0].intended_value == "1.15"


def test_W6_round_trip_control_configs(db):
    exp_id, brief_id = _build_contract_chain(
        db,
        controlled_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "baseline_value": "1.0",
                    "baseline_source": "voice_profile",
                    "factor_role": "controlled",
                }
            ]
        ),
    )
    db.commit()
    create_execution_contract(db, exp_id, brief_id)
    db.commit()
    fetched = get_contract_for_experiment(db, exp_id)
    assert fetched is not None
    assert len(fetched.control_configs) == 1
    assert fetched.control_configs[0].baseline_value == "1.0"


def test_W7_fidelity_round_trips_after_persist(db):
    exp_id, brief_id = _build_contract_chain(
        db,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                }
            ]
        ),
    )
    _insert_experiment_factor(db, exp_id, "narration_speaking_rate", "treatment", "numeric", "1.1")
    db.commit()
    contract = create_execution_contract(db, exp_id, brief_id)
    db.commit()
    plan_id = _insert_production_plan(db, experiment_id=exp_id)
    _insert_feature_snapshot(db, plan_id=plan_id, narration_speaking_rate=1.1)
    db.commit()
    fidelity = compare_intended_vs_actual(db, contract)
    persist_fidelity(db, contract.id, fidelity)
    db.commit()
    fetched = get_execution_contract(db, contract.id)
    assert fetched is not None
    assert fetched.fidelity is not None
    assert fetched.fidelity.valid_for_learning is True
    assert len(fetched.fidelity.treatment_outcomes) == 1
    assert fetched.fidelity.treatment_outcomes[0].outcome == FidelityOutcome.MATCHED


# ── X: Schema integrity ────────────────────────────────────────────────────────


def test_X_fresh_db_has_schema_version_40(db):
    row = db.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    assert row["version"] == 51


def test_X2_v39_db_migrates_to_v40(tmp_path):
    """A v39 database gains the execution_contracts table after migration."""
    # Build a v39 DB manually
    import sqlite3 as _sqlite3

    from app.core.database import open_db

    raw = _sqlite3.connect(str(tmp_path / "v39.db"))
    raw.row_factory = _sqlite3.Row
    # Use open_db to initialise at current version, then roll back the version number
    raw.close()
    conn = open_db(tmp_path / "v39.db")
    # Fake it as v39 by removing the execution_contracts table and setting version
    conn.execute("DROP TABLE IF EXISTS experiment_execution_contracts")
    conn.execute("UPDATE schema_version SET version = 39")
    conn.commit()
    conn.close()

    # Now open again — migration must fire
    conn2 = open_db(tmp_path / "v39.db")
    row = conn2.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    assert row["version"] == 51
    tbl = conn2.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='experiment_execution_contracts'"
    ).fetchone()
    assert tbl is not None
    conn2.close()
