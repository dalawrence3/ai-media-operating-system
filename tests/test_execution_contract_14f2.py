"""Phase 14F.2 — Execution Fidelity Classification tests.

Groups A–AM covering:
  A  — NOT_YET_ASSESSABLE (no snapshot)
  B  — VALID feature treatment exact match + policy version
  C  — NOT_VALID feature treatment deviation
  D  — actual 0.0 is observed, not missing
  E  — NULL actual → UNRESOLVED (NOT_OBSERVABLE treatment)
  F  — ENFORCED control drift → NOT_VALID + confounding_risk=high
  G  — SOFT control drift → VALID_WITH_WARNINGS
  H  — SOFT control NOT_OBSERVABLE → VALID_WITH_WARNINGS, not MATCHED
  I  — MARKET_EXPLORATION + zero production treatments + market match → VALID
  J  — MARKET_EXPLORATION + market theme deviated → NOT_VALID
  K  — MARKET_EXPLORATION + evaluator unresolved/not_yet_available → UNRESOLVED/NOT_YET_ASSESSABLE
  L  — MARKET + Learning App variation on uncontrolled factor → no invalidation
  M  — MARKET + explicit enforced control drift → NOT_VALID
  N  — FEATURE experiment with zero declared treatments → NOT_VALID (vacuous-truth bug fix)
  O  — Non-market experiment (validation/exploitation) with zero treatments → NOT_VALID
  P  — Validation experiment requires treatment condition in snapshot
  Q  — Stored control baseline used; live profile changes ignored
  R  — Tolerance from spec (snapshotted, not recomputed)
  S  — Correct canonical_cluster_id supplied to semantic evaluator
  T  — Deviated cluster id → NOT_VALID
  U  — Evaluator receives script body from experiment's own production plan
  V  — Evaluator exception → UNRESOLVED
  W  — No evaluator + no cluster → UNRESOLVED (no bypass shortcut)
  X  — Fidelity persists to DB
  Y  — Classification round-trips (persist → get_contract)
  Z  — Reasons round-trip
  AA — Fidelity policy version round-trips as "1.1.0"
  AB — VALID fidelity does NOT imply analytics maturity
  AC — NOT_VALID → valid_for_learning=False
  AD — NOT_YET_ASSESSABLE → valid_for_learning=None
  AE — VALID_WITH_WARNINGS → valid_for_learning=True with explicit caveat
  AF — compare_intended_vs_actual() has no DB write side effects
  AG–AM — Regression: Phase 14F.1 / 14F / 14E / earlier suites stay green

Vacuous-truth regression (Step 11):
  VT1 — market + no treatments + evaluator "matched" → VALID
  VT2 — market + no treatments + evaluator "deviated" → NOT_VALID
  VT3 — market + no treatments + evaluator "unresolved" → UNRESOLVED
  VT4 — feature + no treatments → NOT_VALID

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

from app.core.database import open_db
from app.intelligence.experiments.execution_contract import (
    FidelityClassification,
    FidelityOutcome,
)
from app.intelligence.experiments.execution_service import (
    FIDELITY_POLICY_VERSION,
    MARKET_THEME_FACTOR_NAME,
    compare_intended_vs_actual,
    create_execution_contract,
    get_contract_for_experiment,
    persist_fidelity,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def db(tmp_path):
    conn = open_db(tmp_path / "test.db")
    yield conn
    conn.close()


# ── Helpers ───────────────────────────────────────────────────────────────────


def _insert_channel(db: sqlite3.Connection, channel_id: int = 1) -> None:
    db.execute(
        """INSERT OR IGNORE INTO channels
           (id, platform, channel_name, platform_channel_id)
           VALUES (?, 'youtube', 'Test Channel', ?)""",
        (channel_id, f"UC{channel_id}f2"),
    )


def _insert_cluster(db: sqlite3.Connection, cluster_id: int = 99) -> None:
    db.execute(
        """INSERT OR IGNORE INTO market_canonical_clusters
           (id, canonical_label, normalized_label, semantic_fingerprint)
           VALUES (?, 'test cluster f2', 'test cluster f2', 'fp-f2')""",
        (cluster_id,),
    )


def _insert_opportunity(
    db: sqlite3.Connection,
    opp_id: int,
    channel_id: int = 1,
    cluster_id: int | None = 99,
) -> None:
    _insert_channel(db, channel_id)
    if cluster_id is not None:
        _insert_cluster(db, cluster_id)
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
    exp_id: str,
    channel_id: int = 1,
    opp_id: int = 1,
) -> None:
    db.execute(
        """INSERT OR IGNORE INTO experiments
           (id, channel_id, opportunity_id, experiment_type, status,
            hypothesis, input_hash)
           VALUES (?, ?, ?, 'exploration', 'planned',
                   'test hypothesis', ?)""",
        (exp_id, channel_id, opp_id, f"hash-{exp_id}"),
    )


def _insert_experiment_factor(
    db: sqlite3.Connection,
    exp_id: str,
    factor_name: str = "narration_speaking_rate",
    factor_role: str = "treatment",
    value_type: str = "numeric",
    intended_value: str | None = "1.1",
) -> None:
    db.execute(
        """INSERT INTO experiment_factors
           (experiment_id, factor_name, factor_role, value_type, intended_value)
           VALUES (?, ?, ?, ?, ?)""",
        (exp_id, factor_name, factor_role, value_type, intended_value),
    )


_pr_counter = 0


def _insert_planning_run(db: sqlite3.Connection, channel_id: int = 1) -> str:
    global _pr_counter
    _pr_counter += 1
    run_id = f"run-f2-{_pr_counter}"
    db.execute(
        """INSERT OR IGNORE INTO experiment_planning_runs
           (id, channel_id, status, eligible_count, exploration_only_count,
            general_eligible_count, selected_count, deferred_count, input_hash)
           VALUES (?, ?, 'completed', 1, 0, 1, 1, 0, ?)""",
        (run_id, channel_id, f"hash-pr-{_pr_counter}"),
    )
    return run_id


def _insert_brief(
    db: sqlite3.Connection,
    brief_id: str,
    channel_id: int = 1,
    opp_id: int = 1,
    cluster_id: int | None = 99,
    brief_planning_intent: str = "feature_exploration",
    treatment_factors_json: str = "[]",
    controlled_factors_json: str = "[]",
) -> None:
    run_id = _insert_planning_run(db, channel_id)
    cs_id = db.execute(
        """INSERT INTO experiment_candidate_scores
           (planning_run_id, opportunity_id, channel_id, canonical_cluster_id,
            eligibility_classification, planning_intent, experiment_type,
            primary_target_metric, primary_metric_direction, hypothesis_sketch,
            intended_treatment_factors_json, controlled_factors_json,
            feature_change_risk, final_planning_score, input_hash)
           VALUES (?, ?, ?, ?, 'general_eligible', 'exploration', 'exploration',
                   'average_view_percentage', 'higher_is_better', 'hyp',
                   '[]', '[]', 'low', 0.5, ?)
           RETURNING id""",
        (run_id, opp_id, channel_id, cluster_id, f"hash-cs-{brief_id}"),
    ).fetchone()["id"]
    sd_id = db.execute(
        """INSERT INTO experiment_selection_decisions
           (planning_run_id, candidate_score_id, opportunity_id, selected,
            rank_in_pool, pool_type, selection_reason, is_validation_repeat)
           VALUES (?, ?, ?, 1, 1, 'exploration', 'top scored', 0)
           RETURNING id""",
        (run_id, cs_id, opp_id),
    ).fetchone()["id"]
    db.execute(
        """INSERT OR IGNORE INTO experiment_strategy_briefs
           (id, channel_id, planning_run_id, selection_decision_id, opportunity_id,
            canonical_cluster_id, brief_planning_intent, experiment_type,
            hypothesis, target_metric, target_direction, brief_hash, status,
            treatment_factors_json, controlled_factors_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'exploration',
                   'test hypothesis', 'average_view_percentage', 'higher_is_better',
                   ?, 'pending_approval', ?, ?)""",
        (
            brief_id,
            channel_id,
            run_id,
            sd_id,
            opp_id,
            cluster_id,
            brief_planning_intent,
            f"hash-brief-{brief_id}",
            treatment_factors_json,
            controlled_factors_json,
        ),
    )


def _build_chain(
    db: sqlite3.Connection,
    *,
    exp_id: str,
    brief_id: str,
    opp_id: int,
    channel_id: int = 1,
    cluster_id: int | None = 99,
    brief_planning_intent: str = "feature_exploration",
    treatment_factors_json: str = "[]",
    controlled_factors_json: str = "[]",
) -> tuple[str, str]:
    _insert_opportunity(db, opp_id, channel_id, cluster_id)
    _insert_experiment(db, exp_id, channel_id, opp_id)
    _insert_brief(
        db,
        brief_id,
        channel_id,
        opp_id,
        cluster_id,
        brief_planning_intent=brief_planning_intent,
        treatment_factors_json=treatment_factors_json,
        controlled_factors_json=controlled_factors_json,
    )
    db.commit()
    return exp_id, brief_id


_plan_counter = 0
_snap_counter = 0


def _insert_production_plan(db: sqlite3.Connection, exp_id: str) -> int:
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
            warnings_json, requires_evidence_review, evidence_hash,
            experiment_id, status)
           VALUES (999, 999, 1, ?, 'bhash',
                   '1.0', '1.0', '1.0',
                   'plan', 'short', 60, 100,
                   '[]', 0, 'ev', ?, 'approved')
           RETURNING id""",
        (f"ph-f2-{_plan_counter}", exp_id),
    ).fetchone()
    db.commit()
    db.execute("PRAGMA foreign_keys = ON")
    db.commit()
    return r["id"]


def _insert_production_plan_with_script(
    db: sqlite3.Connection, exp_id: str, script_body: str
) -> tuple[int, int]:
    """Insert a script row + production_plan with FK OFF. Returns (plan_id, script_id)."""
    global _plan_counter
    _plan_counter += 1
    db.commit()
    db.execute("PRAGMA foreign_keys = OFF")
    db.commit()
    # scripts table: id, topic_id, version, body, status
    script_id = db.execute(
        """INSERT INTO scripts (topic_id, version, body)
           VALUES (999, ?, ?)
           RETURNING id""",
        (_plan_counter, script_body),
    ).fetchone()["id"]
    plan_id = db.execute(
        """INSERT INTO production_plans
           (topic_id, script_id, script_version, input_hash, script_body_hash,
            plan_schema_version, renderer_version, duration_algorithm_version,
            title, format, total_estimated_duration_s, total_word_count,
            warnings_json, requires_evidence_review, evidence_hash,
            experiment_id, status)
           VALUES (999, ?, 1, ?, 'bhash',
                   '1.0', '1.0', '1.0',
                   'plan', 'short', 60, 100,
                   '[]', 0, 'ev', ?, 'approved')
           RETURNING id""",
        (script_id, f"ph-f2-{_plan_counter}", exp_id),
    ).fetchone()["id"]
    db.commit()
    db.execute("PRAGMA foreign_keys = ON")
    db.commit()
    return plan_id, script_id


def _insert_snapshot(
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
    global _snap_counter
    _snap_counter += 1
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
            render_caption_burn_in, script_format, narration_voice_id,
            publish_day_of_week)
           VALUES (?, 999, '1.0', '1.0',
                   ?, '2026-08-22T00:00:00', '2026-08-22T00:00:00',
                   999, ?, 999, 999, 999, 999, 999, 999,
                   ?, ?, ?, ?, ?, ?, ?)""",
        (
            _snap_counter,
            f"snap-f2-{_snap_counter}",
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


# ── A: NOT_YET_ASSESSABLE ────────────────────────────────────────────────────


def test_A_no_snapshot_returns_not_yet_assessable(db):
    """Feature experiment with a treatment factor but no feature snapshot yet
    → NOT_YET_ASSESSABLE, valid_for_learning=None."""
    _build_chain(
        db,
        exp_id="exp-A",
        brief_id="brief-A",
        opp_id=100,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                },
            ]
        ),
    )
    _insert_experiment_factor(db, "exp-A", intended_value="1.1")
    db.commit()
    contract = create_execution_contract(db, "exp-A", "brief-A")
    db.commit()
    # No production_plan / feature_snapshot inserted → NOT_YET_AVAILABLE
    fidelity = compare_intended_vs_actual(db, contract)
    assert fidelity.classification == FidelityClassification.NOT_YET_ASSESSABLE
    assert fidelity.valid_for_learning is None


# ── B: VALID feature treatment exact match ────────────────────────────────────


def test_B_exact_match_speaking_rate_valid(db):
    """Feature treatment factor matches actual → VALID, valid_for_learning=True."""
    _build_chain(
        db,
        exp_id="exp-B",
        brief_id="brief-B",
        opp_id=101,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                },
            ]
        ),
    )
    _insert_experiment_factor(db, "exp-B", intended_value="1.1")
    db.commit()
    contract = create_execution_contract(db, "exp-B", "brief-B")
    db.commit()
    plan_id = _insert_production_plan(db, "exp-B")
    _insert_snapshot(db, plan_id, narration_speaking_rate=1.1)
    fidelity = compare_intended_vs_actual(db, contract)
    assert fidelity.classification == FidelityClassification.VALID
    assert fidelity.valid_for_learning is True


def test_B2_fidelity_policy_version_is_set(db):
    """compare_intended_vs_actual always stamps fidelity_policy_version."""
    _build_chain(
        db,
        exp_id="exp-B2",
        brief_id="brief-B2",
        opp_id=102,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                },
            ]
        ),
    )
    _insert_experiment_factor(db, "exp-B2", intended_value="1.0")
    db.commit()
    contract = create_execution_contract(db, "exp-B2", "brief-B2")
    db.commit()
    plan_id = _insert_production_plan(db, "exp-B2")
    _insert_snapshot(db, plan_id, narration_speaking_rate=1.0)
    fidelity = compare_intended_vs_actual(db, contract)
    assert fidelity.fidelity_policy_version == FIDELITY_POLICY_VERSION


# ── C: NOT_VALID feature treatment deviation ─────────────────────────────────


def test_C_treatment_deviation_not_valid(db):
    """Actual speaking rate far outside tolerance → NOT_VALID, valid_for_learning=False."""
    _build_chain(
        db,
        exp_id="exp-C",
        brief_id="brief-C",
        opp_id=103,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                },
            ]
        ),
    )
    _insert_experiment_factor(db, "exp-C", intended_value="1.3")
    db.commit()
    contract = create_execution_contract(db, "exp-C", "brief-C")
    db.commit()
    plan_id = _insert_production_plan(db, "exp-C")
    _insert_snapshot(db, plan_id, narration_speaking_rate=0.8)  # far from 1.3
    fidelity = compare_intended_vs_actual(db, contract)
    assert fidelity.classification == FidelityClassification.NOT_VALID
    assert fidelity.valid_for_learning is False
    to = fidelity.treatment_outcomes[0]
    assert to.outcome == FidelityOutcome.DEVIATED


# ── D: actual 0.0 is observed, not missing ───────────────────────────────────


def test_D_actual_zero_is_observed_not_null(db):
    """narration_speaking_rate=0.0 in the snapshot must be treated as an
    observed value, not as NULL/missing (no truthiness checks on 0)."""
    _build_chain(
        db,
        exp_id="exp-D",
        brief_id="brief-D",
        opp_id=104,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                },
            ]
        ),
    )
    _insert_experiment_factor(db, "exp-D", intended_value="0.0")
    db.commit()
    contract = create_execution_contract(db, "exp-D", "brief-D")
    db.commit()
    plan_id = _insert_production_plan(db, "exp-D")
    _insert_snapshot(db, plan_id, narration_speaking_rate=0.0)
    fidelity = compare_intended_vs_actual(db, contract)
    to = fidelity.treatment_outcomes[0]
    # 0.0 is observed — outcome should be MATCHED (diff=0.0 ≤ tol), not NOT_OBSERVABLE
    assert to.outcome in (FidelityOutcome.MATCHED, FidelityOutcome.WITHIN_TOLERANCE)
    assert to.actual_value is not None
    assert to.actual_value == "0.0"


def test_D2_intended_zero_actual_zero_matched(db):
    """intended=0.0, actual=0.0 → MATCHED within tolerance → VALID."""
    _build_chain(
        db,
        exp_id="exp-D2",
        brief_id="brief-D2",
        opp_id=105,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                },
            ]
        ),
    )
    _insert_experiment_factor(db, "exp-D2", intended_value="0.0")
    db.commit()
    contract = create_execution_contract(db, "exp-D2", "brief-D2")
    db.commit()
    plan_id = _insert_production_plan(db, "exp-D2")
    _insert_snapshot(db, plan_id, narration_speaking_rate=0.0)
    fidelity = compare_intended_vs_actual(db, contract)
    assert fidelity.valid_for_learning is True
    assert fidelity.classification == FidelityClassification.VALID


# ── E: NULL actual → UNRESOLVED ──────────────────────────────────────────────


def test_E_null_actual_treatment_unresolved(db):
    """Snapshot exists but narration_speaking_rate column is NULL → NOT_OBSERVABLE
    → treatment_has_unresolved → UNRESOLVED, valid_for_learning=None."""
    _build_chain(
        db,
        exp_id="exp-E",
        brief_id="brief-E",
        opp_id=106,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                },
            ]
        ),
    )
    _insert_experiment_factor(db, "exp-E", intended_value="1.1")
    db.commit()
    contract = create_execution_contract(db, "exp-E", "brief-E")
    db.commit()
    plan_id = _insert_production_plan(db, "exp-E")
    _insert_snapshot(db, plan_id, narration_speaking_rate=None)  # NULL column
    fidelity = compare_intended_vs_actual(db, contract)
    assert fidelity.classification == FidelityClassification.UNRESOLVED
    assert fidelity.valid_for_learning is None
    to = fidelity.treatment_outcomes[0]
    assert to.outcome == FidelityOutcome.NOT_OBSERVABLE


# ── F: ENFORCED control drift → NOT_VALID ────────────────────────────────────


def test_F_enforced_control_drift_not_valid(db):
    """ENFORCED control factor (narration_voice_id) drifts from baseline → NOT_VALID,
    confounding_risk_realized=high.  (Phase 14F.2 fix: previously only set
    confounding_risk without invalidating.)

    narration_voice_id is ENFORCED in SAFE_CONTROLLABLE_FACTORS; treatment is
    narration_speaking_rate which matches.  Control drift alone is enough to
    invalidate the experiment."""
    _build_chain(
        db,
        exp_id="exp-F",
        brief_id="brief-F",
        opp_id=107,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                },
            ]
        ),
        controlled_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_voice_id",
                    "baseline_value": "voice-01",
                    "baseline_source": "voice_profile",
                    "factor_role": "controlled",
                },
            ]
        ),
    )
    _insert_experiment_factor(db, "exp-F", intended_value="1.2")
    db.commit()
    contract = create_execution_contract(db, "exp-F", "brief-F")
    db.commit()
    plan_id = _insert_production_plan(db, "exp-F")
    # speaking rate matches intended; narration_voice_id drifted (voice-01 → voice-02)
    _insert_snapshot(db, plan_id, narration_speaking_rate=1.2, narration_voice_id="voice-02")
    fidelity = compare_intended_vs_actual(db, contract)
    assert fidelity.classification == FidelityClassification.NOT_VALID
    assert fidelity.valid_for_learning is False
    assert fidelity.confounding_risk_realized == "high"
    co = next(f for f in fidelity.control_outcomes if f.factor_name == "narration_voice_id")
    assert co.outcome == FidelityOutcome.DEVIATED


def test_F2_enforced_control_no_drift_valid(db):
    """ENFORCED control factor (narration_voice_id) holds at baseline → VALID."""
    _build_chain(
        db,
        exp_id="exp-F2",
        brief_id="brief-F2",
        opp_id=108,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                },
            ]
        ),
        controlled_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_voice_id",
                    "baseline_value": "voice-01",
                    "baseline_source": "voice_profile",
                    "factor_role": "controlled",
                },
            ]
        ),
    )
    _insert_experiment_factor(db, "exp-F2", intended_value="1.2")
    db.commit()
    contract = create_execution_contract(db, "exp-F2", "brief-F2")
    db.commit()
    plan_id = _insert_production_plan(db, "exp-F2")
    # Both treatment and control hold at intended values
    _insert_snapshot(db, plan_id, narration_speaking_rate=1.2, narration_voice_id="voice-01")
    fidelity = compare_intended_vs_actual(db, contract)
    assert fidelity.classification == FidelityClassification.VALID
    assert fidelity.valid_for_learning is True
    assert fidelity.confounding_risk_realized == "low"


# ── G: SOFT control drift → VALID_WITH_WARNINGS ──────────────────────────────


def test_G_soft_control_drift_valid_with_warnings(db):
    """SOFT-capability control factor drifts → VALID_WITH_WARNINGS,
    valid_for_learning=True (not invalidated), caveat in reasons."""
    # narration_speaking_rate contract is built via direct DB INSERT to get 'soft'
    # capability, since _build_control_configs() always sets the registry default.
    # Build the chain with the treatment factor only:
    _build_chain(
        db,
        exp_id="exp-G",
        brief_id="brief-G",
        opp_id=109,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                },
            ]
        ),
    )
    _insert_experiment_factor(db, "exp-G", intended_value="1.1")
    db.commit()
    contract = create_execution_contract(db, "exp-G", "brief-G")
    db.commit()

    # Inject a SOFT control via direct DB INSERT (bypass _build_control_configs)
    ctrl_json = json.dumps(
        [
            {
                "factor_name": "publish_day_of_week",
                "baseline_value": "1",
                "baseline_source": "historical",
                "control_capability": "soft",
                "tolerance": None,
            },
        ]
    )
    db.execute(
        "UPDATE experiment_execution_contracts SET control_factors_json = ? WHERE id = ?",
        (ctrl_json, contract.id),
    )
    db.commit()
    # Reload contract to pick up the updated control_factors_json
    contract = create_execution_contract(db, "exp-G", "brief-G")

    plan_id = _insert_production_plan(db, "exp-G")
    # speaking rate matched; publish_day drifted (1→3)
    _insert_snapshot(db, plan_id, narration_speaking_rate=1.1, publish_day_of_week=3)
    fidelity = compare_intended_vs_actual(db, contract)
    assert fidelity.classification == FidelityClassification.VALID_WITH_WARNINGS
    assert fidelity.valid_for_learning is True
    assert any("Soft control publish_day_of_week" in r for r in fidelity.reasons)


def test_G2_soft_control_treatment_match_valid_with_warnings(db):
    """Treatment matches + soft control drifts → VALID_WITH_WARNINGS,
    not VALID (warning is preserved in classification)."""
    _build_chain(
        db,
        exp_id="exp-G2",
        brief_id="brief-G2",
        opp_id=110,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                },
            ]
        ),
    )
    _insert_experiment_factor(db, "exp-G2", intended_value="1.0")
    db.commit()
    contract = create_execution_contract(db, "exp-G2", "brief-G2")
    db.commit()
    ctrl_json = json.dumps(
        [
            {
                "factor_name": "script_format",
                "baseline_value": "narrative",
                "baseline_source": "historical",
                "control_capability": "soft",
                "tolerance": None,
            },
        ]
    )
    db.execute(
        "UPDATE experiment_execution_contracts SET control_factors_json = ? WHERE id = ?",
        (ctrl_json, contract.id),
    )
    db.commit()
    contract = create_execution_contract(db, "exp-G2", "brief-G2")
    plan_id = _insert_production_plan(db, "exp-G2")
    _insert_snapshot(db, plan_id, narration_speaking_rate=1.0, script_format="listicle")
    fidelity = compare_intended_vs_actual(db, contract)
    assert fidelity.classification == FidelityClassification.VALID_WITH_WARNINGS
    assert fidelity.valid_for_learning is True


# ── H: SOFT control NOT_OBSERVABLE → VALID_WITH_WARNINGS, not MATCHED ────────


def test_H_soft_control_not_observable_valid_with_warnings(db):
    """SOFT control column returns NULL → NOT_OBSERVABLE outcome for the control,
    but overall fidelity = VALID_WITH_WARNINGS (not MATCHED, not VALID)."""
    _build_chain(
        db,
        exp_id="exp-H",
        brief_id="brief-H",
        opp_id=111,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                },
            ]
        ),
    )
    _insert_experiment_factor(db, "exp-H", intended_value="1.1")
    db.commit()
    contract = create_execution_contract(db, "exp-H", "brief-H")
    db.commit()
    ctrl_json = json.dumps(
        [
            {
                "factor_name": "publish_day_of_week",
                "baseline_value": "2",
                "baseline_source": "historical",
                "control_capability": "soft",
                "tolerance": None,
            },
        ]
    )
    db.execute(
        "UPDATE experiment_execution_contracts SET control_factors_json = ? WHERE id = ?",
        (ctrl_json, contract.id),
    )
    db.commit()
    contract = create_execution_contract(db, "exp-H", "brief-H")
    plan_id = _insert_production_plan(db, "exp-H")
    # publish_day_of_week=None → NULL → NOT_OBSERVABLE for the soft control
    _insert_snapshot(db, plan_id, narration_speaking_rate=1.1, publish_day_of_week=None)
    fidelity = compare_intended_vs_actual(db, contract)
    co = next(f for f in fidelity.control_outcomes if f.factor_name == "publish_day_of_week")
    assert co.outcome == FidelityOutcome.NOT_OBSERVABLE
    assert fidelity.classification == FidelityClassification.VALID_WITH_WARNINGS
    assert fidelity.valid_for_learning is True


# ── I: MARKET_EXPLORATION + zero production treatments + match → VALID ────────


def test_I_market_exploration_theme_matched_valid(db):
    """Pure market exploration with no declared production treatment factors.
    Market-theme evaluator returns 'matched' → VALID, valid_for_learning=True.

    This is the correct Phase 14F.2 behaviour for market experiments — contrast
    with test N where the same zero-treatment pattern makes a feature experiment
    NOT_VALID."""
    _build_chain(
        db,
        exp_id="exp-I",
        brief_id="brief-I",
        opp_id=112,
        brief_planning_intent="market_exploration",
        cluster_id=77,
    )
    db.commit()
    contract = create_execution_contract(db, "exp-I", "brief-I")
    assert contract.canonical_cluster_id == 77
    assert contract.treatment_configs == []

    def evaluator(cid, body):
        return "matched"

    fidelity = compare_intended_vs_actual(db, contract, market_theme_evaluator=evaluator)
    assert fidelity.classification == FidelityClassification.VALID
    assert fidelity.valid_for_learning is True
    mt = next(f for f in fidelity.treatment_outcomes if f.factor_name == MARKET_THEME_FACTOR_NAME)
    assert mt.outcome == FidelityOutcome.MATCHED


def test_I2_market_exploration_no_evaluator_unresolved(db):
    """Market experiment but no evaluator provided → UNRESOLVED.
    Absence of evaluator must not default to VALID."""
    _build_chain(
        db,
        exp_id="exp-I2",
        brief_id="brief-I2",
        opp_id=113,
        brief_planning_intent="market_exploration",
        cluster_id=77,
    )
    db.commit()
    contract = create_execution_contract(db, "exp-I2", "brief-I2")
    fidelity = compare_intended_vs_actual(db, contract)  # no evaluator
    assert fidelity.classification == FidelityClassification.UNRESOLVED
    assert fidelity.valid_for_learning is None


# ── J: MARKET_EXPLORATION + theme deviated → NOT_VALID ───────────────────────


def test_J_market_exploration_theme_deviated_not_valid(db):
    """Market-theme evaluator returns 'deviated' → NOT_VALID, valid_for_learning=False."""
    _build_chain(
        db,
        exp_id="exp-J",
        brief_id="brief-J",
        opp_id=114,
        brief_planning_intent="market_exploration",
        cluster_id=77,
    )
    db.commit()
    contract = create_execution_contract(db, "exp-J", "brief-J")

    def evaluator(cid, body):
        return "deviated"

    fidelity = compare_intended_vs_actual(db, contract, market_theme_evaluator=evaluator)
    assert fidelity.classification == FidelityClassification.NOT_VALID
    assert fidelity.valid_for_learning is False
    mt = next(f for f in fidelity.treatment_outcomes if f.factor_name == MARKET_THEME_FACTOR_NAME)
    assert mt.outcome == FidelityOutcome.DEVIATED


# ── K: MARKET evaluator unresolved / not_yet_available ───────────────────────


def test_K_market_exploration_evaluator_unresolved(db):
    """Market-theme evaluator returns 'unresolved' → UNRESOLVED, valid_for_learning=None."""
    _build_chain(
        db,
        exp_id="exp-K",
        brief_id="brief-K",
        opp_id=115,
        brief_planning_intent="market_exploration",
        cluster_id=77,
    )
    db.commit()
    contract = create_execution_contract(db, "exp-K", "brief-K")

    def evaluator(cid, body):
        return "unresolved"

    fidelity = compare_intended_vs_actual(db, contract, market_theme_evaluator=evaluator)
    assert fidelity.classification == FidelityClassification.UNRESOLVED
    assert fidelity.valid_for_learning is None


def test_K2_market_exploration_evaluator_not_yet_available(db):
    """Market-theme evaluator returns 'not_yet_available' → NOT_YET_ASSESSABLE."""
    _build_chain(
        db,
        exp_id="exp-K2",
        brief_id="brief-K2",
        opp_id=116,
        brief_planning_intent="market_exploration",
        cluster_id=77,
    )
    db.commit()
    contract = create_execution_contract(db, "exp-K2", "brief-K2")

    def evaluator(cid, body):
        return "not_yet_available"

    fidelity = compare_intended_vs_actual(db, contract, market_theme_evaluator=evaluator)
    assert fidelity.classification == FidelityClassification.NOT_YET_ASSESSABLE
    assert fidelity.valid_for_learning is None


# ── L: Market + Learning Application on uncontrolled factor → no invalidation ─


def test_L_market_uncontrolled_factor_change_does_not_invalidate(db):
    """Market experiment does not control narration_speaking_rate.
    Learning Application legitimately changed speaking rate in production.
    This must NOT appear as experiment drift — rate is simply not evaluated."""
    _build_chain(
        db,
        exp_id="exp-L",
        brief_id="brief-L",
        opp_id=117,
        brief_planning_intent="market_exploration",
        cluster_id=77,
        # No controlled_factors_json mentioning speaking rate
    )
    db.commit()
    contract = create_execution_contract(db, "exp-L", "brief-L")
    # Confirm speaking rate is not in treatment_configs or control_configs
    rate_in_treatment = any(
        tc.factor_name == "narration_speaking_rate" for tc in contract.treatment_configs
    )
    rate_in_control = any(
        cc.factor_name == "narration_speaking_rate" for cc in contract.control_configs
    )
    assert not rate_in_treatment
    assert not rate_in_control

    plan_id = _insert_production_plan(db, "exp-L")
    # Learning Application changed speaking rate — but experiment doesn't care
    _insert_snapshot(db, plan_id, narration_speaking_rate=1.4)

    def evaluator(cid, body):
        return "matched"

    fidelity = compare_intended_vs_actual(db, contract, market_theme_evaluator=evaluator)
    # Rate change is invisible to fidelity — no control_outcomes for it
    rate_outcomes = [
        f for f in fidelity.control_outcomes if f.factor_name == "narration_speaking_rate"
    ]
    assert rate_outcomes == []
    assert fidelity.classification == FidelityClassification.VALID
    assert fidelity.valid_for_learning is True


# ── M: Market + explicit enforced control drift → NOT_VALID ──────────────────


def test_M_market_with_enforced_control_drift_not_valid(db):
    """Market experiment with narration_speaking_rate as an explicit ENFORCED
    control.  Actual rate drifts → NOT_VALID (confounding control failure)."""
    _build_chain(
        db,
        exp_id="exp-M",
        brief_id="brief-M",
        opp_id=118,
        brief_planning_intent="market_exploration",
        cluster_id=77,
        controlled_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "baseline_value": "1.0",
                    "baseline_source": "voice_profile",
                    "factor_role": "controlled",
                },
            ]
        ),
    )
    db.commit()
    contract = create_execution_contract(db, "exp-M", "brief-M")
    db.commit()
    plan_id = _insert_production_plan(db, "exp-M")
    # Rate drifted; market theme matched
    _insert_snapshot(db, plan_id, narration_speaking_rate=0.6)

    def evaluator(cid, body):
        return "matched"

    fidelity = compare_intended_vs_actual(db, contract, market_theme_evaluator=evaluator)
    # Market theme matched → treatment_has_deviated=False
    # But ENFORCED control drifted → NOT_VALID
    assert fidelity.classification == FidelityClassification.NOT_VALID
    assert fidelity.valid_for_learning is False
    assert fidelity.confounding_risk_realized == "high"


# ── N: Feature experiment with zero declared treatments → NOT_VALID ───────────


def test_N_feature_zero_treatments_not_valid(db):
    """Vacuous-truth regression: feature_exploration experiment with no
    treatment factors declared → NOT_VALID (structurally malformed).

    Previously (Phase 14F) this returned valid_for_learning=True because
    'all([]) == True'.  Phase 14F.2 fixes this."""
    _build_chain(
        db,
        exp_id="exp-N",
        brief_id="brief-N",
        opp_id=119,
        brief_planning_intent="feature_exploration",
        # treatment_factors_json defaults to "[]"
    )
    db.commit()
    contract = create_execution_contract(db, "exp-N", "brief-N")
    assert contract.treatment_configs == []
    fidelity = compare_intended_vs_actual(db, contract)
    assert fidelity.classification == FidelityClassification.NOT_VALID
    assert fidelity.valid_for_learning is False
    assert any("No treatment factors" in r for r in fidelity.reasons)


# ── O: Other non-market experiments with zero treatments → NOT_VALID ──────────


def test_O_validation_zero_treatments_not_valid(db):
    """'validation' brief_planning_intent with zero treatments → NOT_VALID."""
    _build_chain(
        db,
        exp_id="exp-O",
        brief_id="brief-O",
        opp_id=120,
        brief_planning_intent="validation",
    )
    db.commit()
    contract = create_execution_contract(db, "exp-O", "brief-O")
    fidelity = compare_intended_vs_actual(db, contract)
    assert fidelity.classification == FidelityClassification.NOT_VALID
    assert fidelity.valid_for_learning is False


def test_O2_exploitation_zero_treatments_not_valid(db):
    """'exploitation' brief_planning_intent with zero treatments → NOT_VALID."""
    _build_chain(
        db,
        exp_id="exp-O2",
        brief_id="brief-O2",
        opp_id=121,
        brief_planning_intent="exploitation",
    )
    db.commit()
    contract = create_execution_contract(db, "exp-O2", "brief-O2")
    fidelity = compare_intended_vs_actual(db, contract)
    assert fidelity.classification == FidelityClassification.NOT_VALID
    assert fidelity.valid_for_learning is False


# ── P: Validation experiment requires treatment match ────────────────────────


def test_P_validation_treatment_matched_valid(db):
    """Validation experiment with declared treatment and matching snapshot → VALID."""
    _build_chain(
        db,
        exp_id="exp-P",
        brief_id="brief-P",
        opp_id=122,
        brief_planning_intent="validation",
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                },
            ]
        ),
    )
    _insert_experiment_factor(db, "exp-P", intended_value="1.1")
    db.commit()
    contract = create_execution_contract(db, "exp-P", "brief-P")
    db.commit()
    plan_id = _insert_production_plan(db, "exp-P")
    _insert_snapshot(db, plan_id, narration_speaking_rate=1.1)
    fidelity = compare_intended_vs_actual(db, contract)
    assert fidelity.classification == FidelityClassification.VALID
    assert fidelity.valid_for_learning is True


def test_P2_validation_treatment_deviated_not_valid(db):
    """Validation experiment where actual does not match intended → NOT_VALID.
    Operational success (video produced) does not imply experiment fidelity."""
    _build_chain(
        db,
        exp_id="exp-P2",
        brief_id="brief-P2",
        opp_id=123,
        brief_planning_intent="validation",
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                },
            ]
        ),
    )
    _insert_experiment_factor(db, "exp-P2", intended_value="1.1")
    db.commit()
    contract = create_execution_contract(db, "exp-P2", "brief-P2")
    db.commit()
    plan_id = _insert_production_plan(db, "exp-P2")
    _insert_snapshot(db, plan_id, narration_speaking_rate=0.7)  # deviated
    fidelity = compare_intended_vs_actual(db, contract)
    assert fidelity.classification == FidelityClassification.NOT_VALID
    assert fidelity.valid_for_learning is False


# ── Q: Stored baseline used at evaluation time, not re-queried live ───────────


def test_Q_stored_baseline_not_reread_from_profile(db):
    """The control baseline stored in the contract row at creation time is used
    for fidelity evaluation — not a fresh live lookup.

    We simulate this by creating the contract, then verifying that the baseline
    in control_configs comes from what was stored, not the current brief row."""
    _build_chain(
        db,
        exp_id="exp-Q",
        brief_id="brief-Q",
        opp_id=124,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                },
            ]
        ),
        controlled_factors_json=json.dumps(
            [
                {
                    "factor_name": "has_hook",
                    "baseline_value": "true",
                    "baseline_source": "historical",
                    "factor_role": "controlled",
                },
            ]
        ),
    )
    _insert_experiment_factor(db, "exp-Q", intended_value="1.1")
    db.commit()
    contract = create_execution_contract(db, "exp-Q", "brief-Q")
    db.commit()

    # Simulate: brief's controlled_factors_json is "updated" after contract creation
    db.execute(
        "UPDATE experiment_strategy_briefs SET controlled_factors_json = ? WHERE id = ?",
        (
            json.dumps(
                [
                    {
                        "factor_name": "has_hook",
                        "baseline_value": "false",  # CHANGED
                        "baseline_source": "historical",
                        "factor_role": "controlled",
                    },
                ]
            ),
            "brief-Q",
        ),
    )
    db.commit()

    plan_id = _insert_production_plan(db, "exp-Q")
    # has_hook actual = true; original baseline = "true"
    _insert_snapshot(db, plan_id, narration_speaking_rate=1.1, has_hook=1)
    # Contract was built with the original baseline "true", so should MATCH
    fidelity = compare_intended_vs_actual(db, contract)
    co = next(f for f in fidelity.control_outcomes if f.factor_name == "has_hook")
    assert co.outcome == FidelityOutcome.MATCHED
    assert fidelity.classification == FidelityClassification.VALID


# ── R: Tolerance from spec ───────────────────────────────────────────────────


def test_R_small_numeric_diff_within_tolerance_matched(db):
    """Numeric diff ≤ tolerance_abs from SAFE_CONTROLLABLE_FACTORS spec → MATCHED.
    narration_speaking_rate has tolerance_abs=0.001."""
    _build_chain(
        db,
        exp_id="exp-R",
        brief_id="brief-R",
        opp_id=125,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                },
            ]
        ),
    )
    _insert_experiment_factor(db, "exp-R", intended_value="1.1")
    db.commit()
    contract = create_execution_contract(db, "exp-R", "brief-R")
    db.commit()
    plan_id = _insert_production_plan(db, "exp-R")
    # 1.1 + 0.0005 is within the 0.001 tolerance
    _insert_snapshot(db, plan_id, narration_speaking_rate=1.1005)
    fidelity = compare_intended_vs_actual(db, contract)
    to = fidelity.treatment_outcomes[0]
    assert to.outcome == FidelityOutcome.MATCHED
    assert fidelity.classification == FidelityClassification.VALID


def test_R2_numeric_diff_outside_tolerance_deviated(db):
    """Numeric diff > tolerance_abs → DEVIATED, NOT_VALID."""
    _build_chain(
        db,
        exp_id="exp-R2",
        brief_id="brief-R2",
        opp_id=126,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                },
            ]
        ),
    )
    _insert_experiment_factor(db, "exp-R2", intended_value="1.1")
    db.commit()
    contract = create_execution_contract(db, "exp-R2", "brief-R2")
    db.commit()
    plan_id = _insert_production_plan(db, "exp-R2")
    # 1.1 + 0.002 exceeds the 0.001 tolerance
    _insert_snapshot(db, plan_id, narration_speaking_rate=1.102)
    fidelity = compare_intended_vs_actual(db, contract)
    to = fidelity.treatment_outcomes[0]
    assert to.outcome == FidelityOutcome.DEVIATED
    assert fidelity.classification == FidelityClassification.NOT_VALID


# ── S: Correct canonical_cluster_id supplied to evaluator ────────────────────


def test_S_evaluator_receives_correct_cluster_id(db):
    """The canonical_cluster_id from the contract is passed to the evaluator."""
    received_cluster_ids: list[int | None] = []

    def capturing_evaluator(cluster_id: int, script_body: str | None) -> str:
        received_cluster_ids.append(cluster_id)
        return "matched"

    _build_chain(
        db,
        exp_id="exp-S",
        brief_id="brief-S",
        opp_id=127,
        brief_planning_intent="market_exploration",
        cluster_id=42,
    )
    db.commit()
    contract = create_execution_contract(db, "exp-S", "brief-S")
    assert contract.canonical_cluster_id == 42
    compare_intended_vs_actual(db, contract, market_theme_evaluator=capturing_evaluator)
    assert received_cluster_ids == [42]


# ── T: Wrong cluster produces deviation ──────────────────────────────────────


def test_T_wrong_cluster_id_evaluator_detects_deviation(db):
    """Evaluator that verifies cluster identity returns 'deviated' for a mismatch
    → NOT_VALID.  The canonical_cluster_id on the contract is the authority; a
    different cluster that looks similar is NOT valid."""
    approved_cluster = 42

    def cluster_guard(cluster_id: int, script_body: str | None) -> str:
        # Simulate: content was produced for cluster 99, not 42
        return "deviated" if cluster_id == approved_cluster else "matched"

    _build_chain(
        db,
        exp_id="exp-T",
        brief_id="brief-T",
        opp_id=128,
        brief_planning_intent="market_exploration",
        cluster_id=approved_cluster,
    )
    db.commit()
    contract = create_execution_contract(db, "exp-T", "brief-T")
    assert contract.canonical_cluster_id == approved_cluster
    fidelity = compare_intended_vs_actual(db, contract, market_theme_evaluator=cluster_guard)
    assert fidelity.classification == FidelityClassification.NOT_VALID
    assert fidelity.valid_for_learning is False


# ── U: Evaluator receives script body from experiment's production plan ───────


def test_U_evaluator_receives_experiment_script_body(db):
    """market_theme_evaluator receives the script body from the experiment's
    own production plan, not None or a different script."""
    received_bodies: list[str | None] = []

    def body_capturing_evaluator(cluster_id: int, script_body: str | None) -> str:
        received_bodies.append(script_body)
        return "matched"

    _build_chain(
        db,
        exp_id="exp-U",
        brief_id="brief-U",
        opp_id=129,
        brief_planning_intent="market_exploration",
        cluster_id=77,
    )
    db.commit()
    contract = create_execution_contract(db, "exp-U", "brief-U")
    db.commit()
    expected_body = "This is a script about Python decorators."
    _insert_production_plan_with_script(db, "exp-U", script_body=expected_body)
    compare_intended_vs_actual(db, contract, market_theme_evaluator=body_capturing_evaluator)
    assert received_bodies == [expected_body]


def test_U2_evaluator_receives_none_when_no_production_plan(db):
    """When no production plan exists yet, script_body passed to evaluator is None."""
    received_bodies: list[str | None] = []

    def body_capturing_evaluator(cluster_id: int, script_body: str | None) -> str:
        received_bodies.append(script_body)
        return "unresolved"

    _build_chain(
        db,
        exp_id="exp-U2",
        brief_id="brief-U2",
        opp_id=130,
        brief_planning_intent="market_exploration",
        cluster_id=77,
    )
    db.commit()
    contract = create_execution_contract(db, "exp-U2", "brief-U2")
    compare_intended_vs_actual(db, contract, market_theme_evaluator=body_capturing_evaluator)
    assert received_bodies == [None]


# ── V: Evaluator exception → UNRESOLVED ─────────────────────────────────────


def test_V_evaluator_exception_unresolved(db):
    """If the market_theme_evaluator raises an exception, fidelity is UNRESOLVED
    (not VALID — exception is not treated as implicit clearance)."""

    def failing_evaluator(cluster_id: int, script_body: str | None) -> str:
        raise RuntimeError("semantic service unavailable")

    _build_chain(
        db,
        exp_id="exp-V",
        brief_id="brief-V",
        opp_id=131,
        brief_planning_intent="market_exploration",
        cluster_id=77,
    )
    db.commit()
    contract = create_execution_contract(db, "exp-V", "brief-V")
    fidelity = compare_intended_vs_actual(db, contract, market_theme_evaluator=failing_evaluator)
    assert fidelity.classification == FidelityClassification.UNRESOLVED
    assert fidelity.valid_for_learning is None
    assert any("evaluator raised" in r for r in fidelity.reasons)


# ── W: No evaluator + no cluster → UNRESOLVED ────────────────────────────────


def test_W_no_evaluator_no_cluster_unresolved(db):
    """Market experiment with no cluster_id and no evaluator → UNRESOLVED.
    No bypass shortcut exists in Phase 14F.2 that returns VALID without evidence."""
    _build_chain(
        db,
        exp_id="exp-W",
        brief_id="brief-W",
        opp_id=132,
        brief_planning_intent="market_exploration",
        cluster_id=None,
    )
    db.commit()
    contract = create_execution_contract(db, "exp-W", "brief-W")
    assert contract.canonical_cluster_id is None
    fidelity = compare_intended_vs_actual(db, contract)
    assert fidelity.classification == FidelityClassification.UNRESOLVED
    assert fidelity.valid_for_learning is None


# ── X: Fidelity persists ─────────────────────────────────────────────────────


def test_X_fidelity_persists_to_db(db):
    """persist_fidelity() writes classification and policy version to DB."""
    _build_chain(
        db,
        exp_id="exp-X",
        brief_id="brief-X",
        opp_id=133,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                },
            ]
        ),
    )
    _insert_experiment_factor(db, "exp-X", intended_value="1.1")
    db.commit()
    contract = create_execution_contract(db, "exp-X", "brief-X")
    db.commit()
    plan_id = _insert_production_plan(db, "exp-X")
    _insert_snapshot(db, plan_id, narration_speaking_rate=1.1)
    fidelity = compare_intended_vs_actual(db, contract)
    assert fidelity.classification == FidelityClassification.VALID
    persist_fidelity(db, contract.id, fidelity)
    db.commit()
    row = db.execute(
        "SELECT fidelity_json, valid_for_learning, status FROM "
        "experiment_execution_contracts WHERE id = ?",
        (contract.id,),
    ).fetchone()
    assert row["valid_for_learning"] == 1
    assert row["status"] == "completed"
    fj = json.loads(row["fidelity_json"])
    assert fj["classification"] == "valid"
    assert fj["fidelity_policy_version"] == FIDELITY_POLICY_VERSION


# ── Y: Classification round-trips ────────────────────────────────────────────


def test_Y_classification_roundtrip_via_get_contract(db):
    """Persist fidelity, reload via get_contract_for_experiment → classification
    and fidelity_policy_version survive the JSON serialization round-trip."""
    _build_chain(
        db,
        exp_id="exp-Y",
        brief_id="brief-Y",
        opp_id=134,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                },
            ]
        ),
    )
    _insert_experiment_factor(db, "exp-Y", intended_value="1.1")
    db.commit()
    contract = create_execution_contract(db, "exp-Y", "brief-Y")
    db.commit()
    plan_id = _insert_production_plan(db, "exp-Y")
    _insert_snapshot(db, plan_id, narration_speaking_rate=0.5)  # deviated
    fidelity = compare_intended_vs_actual(db, contract)
    assert fidelity.classification == FidelityClassification.NOT_VALID
    persist_fidelity(db, contract.id, fidelity)
    db.commit()
    reloaded = get_contract_for_experiment(db, "exp-Y")
    assert reloaded is not None
    assert reloaded.fidelity is not None
    assert reloaded.fidelity.classification == FidelityClassification.NOT_VALID
    assert reloaded.fidelity.fidelity_policy_version == FIDELITY_POLICY_VERSION


# ── Z: Reasons round-trip ────────────────────────────────────────────────────


def test_Z_reasons_roundtrip(db):
    """Reasons list persists through fidelity_json and is restored on reload."""
    _build_chain(
        db,
        exp_id="exp-Z",
        brief_id="brief-Z",
        opp_id=135,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                },
            ]
        ),
    )
    _insert_experiment_factor(db, "exp-Z", intended_value="1.1")
    db.commit()
    contract = create_execution_contract(db, "exp-Z", "brief-Z")
    db.commit()
    plan_id = _insert_production_plan(db, "exp-Z")
    _insert_snapshot(db, plan_id, narration_speaking_rate=0.5)
    fidelity = compare_intended_vs_actual(db, contract)
    assert len(fidelity.reasons) > 0
    persist_fidelity(db, contract.id, fidelity)
    db.commit()
    reloaded = get_contract_for_experiment(db, "exp-Z")
    assert reloaded.fidelity.reasons == fidelity.reasons


# ── AA: Policy version round-trips ───────────────────────────────────────────


def test_AA_fidelity_policy_version_roundtrip(db):
    """fidelity_policy_version='1.1.0' survives persist + reload."""
    _build_chain(
        db,
        exp_id="exp-AA",
        brief_id="brief-AA",
        opp_id=136,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                },
            ]
        ),
    )
    _insert_experiment_factor(db, "exp-AA", intended_value="1.0")
    db.commit()
    contract = create_execution_contract(db, "exp-AA", "brief-AA")
    db.commit()
    plan_id = _insert_production_plan(db, "exp-AA")
    _insert_snapshot(db, plan_id, narration_speaking_rate=1.0)
    fidelity = compare_intended_vs_actual(db, contract)
    persist_fidelity(db, contract.id, fidelity)
    db.commit()
    reloaded = get_contract_for_experiment(db, "exp-AA")
    assert reloaded.fidelity.fidelity_policy_version == "1.1.0"


# ── AB: VALID fidelity does NOT imply analytics maturity ─────────────────────


def test_AB_valid_fidelity_does_not_imply_analytics_maturity(db):
    """Experiment fidelity (did we execute the design?) and analytics maturity
    (do we have enough performance data?) are separate concerns.  VALID fidelity
    carries no analytics claim — the contract has no analytics fields."""
    _build_chain(
        db,
        exp_id="exp-AB",
        brief_id="brief-AB",
        opp_id=137,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                },
            ]
        ),
    )
    _insert_experiment_factor(db, "exp-AB", intended_value="1.0")
    db.commit()
    contract = create_execution_contract(db, "exp-AB", "brief-AB")
    db.commit()
    plan_id = _insert_production_plan(db, "exp-AB")
    _insert_snapshot(db, plan_id, narration_speaking_rate=1.0)
    fidelity = compare_intended_vs_actual(db, contract)
    assert fidelity.classification == FidelityClassification.VALID
    # No analytics-maturity or outcome fields on ExecutionFidelity
    assert not hasattr(fidelity, "analytics_mature")
    assert not hasattr(fidelity, "outcome_favorable")
    assert not hasattr(fidelity, "observation_maturity")


# ── AC: NOT_VALID → valid_for_learning=False ─────────────────────────────────


def test_AC_not_valid_maps_to_false_for_learning(db):
    """NOT_VALID classification → valid_for_learning=False.
    Phase 14G learning gate checks this field; False must block learning ingestion."""
    _build_chain(
        db,
        exp_id="exp-AC",
        brief_id="brief-AC",
        opp_id=138,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                },
            ]
        ),
    )
    _insert_experiment_factor(db, "exp-AC", intended_value="1.3")
    db.commit()
    contract = create_execution_contract(db, "exp-AC", "brief-AC")
    db.commit()
    plan_id = _insert_production_plan(db, "exp-AC")
    _insert_snapshot(db, plan_id, narration_speaking_rate=0.7)
    fidelity = compare_intended_vs_actual(db, contract)
    assert fidelity.classification == FidelityClassification.NOT_VALID
    assert fidelity.valid_for_learning is False


# ── AD: NOT_YET_ASSESSABLE → valid_for_learning=None ────────────────────────


def test_AD_not_yet_assessable_maps_to_none(db):
    """NOT_YET_ASSESSABLE → valid_for_learning=None.
    None means 'cannot yet determine', not True.  Phase 14G must not treat this
    as clearance for learning."""
    _build_chain(
        db,
        exp_id="exp-AD2",
        brief_id="brief-AD2",
        opp_id=139,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                },
            ]
        ),
    )
    _insert_experiment_factor(db, "exp-AD2", intended_value="1.1")
    db.commit()
    contract = create_execution_contract(db, "exp-AD2", "brief-AD2")
    # No snapshot → NOT_YET_ASSESSABLE
    fidelity = compare_intended_vs_actual(db, contract)
    assert fidelity.classification == FidelityClassification.NOT_YET_ASSESSABLE
    assert fidelity.valid_for_learning is None


# ── AE: VALID_WITH_WARNINGS → valid_for_learning=True ───────────────────────


def test_AE_valid_with_warnings_valid_for_learning_true_with_caveat(db):
    """VALID_WITH_WARNINGS → valid_for_learning=True.
    The experiment result can inform learning but with noted caveats.
    Distinct from VALID: the classification must be VALID_WITH_WARNINGS, not VALID."""
    _build_chain(
        db,
        exp_id="exp-AE",
        brief_id="brief-AE",
        opp_id=140,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                },
            ]
        ),
    )
    _insert_experiment_factor(db, "exp-AE", intended_value="1.0")
    db.commit()
    contract = create_execution_contract(db, "exp-AE", "brief-AE")
    db.commit()
    ctrl_json = json.dumps(
        [
            {
                "factor_name": "publish_day_of_week",
                "baseline_value": "1",
                "baseline_source": "historical",
                "control_capability": "soft",
                "tolerance": None,
            },
        ]
    )
    db.execute(
        "UPDATE experiment_execution_contracts SET control_factors_json = ? WHERE id = ?",
        (ctrl_json, contract.id),
    )
    db.commit()
    contract = create_execution_contract(db, "exp-AE", "brief-AE")
    plan_id = _insert_production_plan(db, "exp-AE")
    _insert_snapshot(db, plan_id, narration_speaking_rate=1.0, publish_day_of_week=5)
    fidelity = compare_intended_vs_actual(db, contract)
    assert fidelity.classification == FidelityClassification.VALID_WITH_WARNINGS
    assert fidelity.valid_for_learning is True


# ── AF: compare_intended_vs_actual has no DB write side effects ───────────────


def test_AF_compare_does_not_write_to_db(db):
    """compare_intended_vs_actual() must be read-only — it must not update the
    experiment_execution_contracts row or any other table."""
    _build_chain(
        db,
        exp_id="exp-AF",
        brief_id="brief-AF",
        opp_id=141,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                },
            ]
        ),
    )
    _insert_experiment_factor(db, "exp-AF", intended_value="1.1")
    db.commit()
    contract = create_execution_contract(db, "exp-AF", "brief-AF")
    db.commit()
    plan_id = _insert_production_plan(db, "exp-AF")
    _insert_snapshot(db, plan_id, narration_speaking_rate=1.1)
    # Snapshot the contract row before
    before = db.execute(
        "SELECT fidelity_json, valid_for_learning, status, completed_at "
        "FROM experiment_execution_contracts WHERE id = ?",
        (contract.id,),
    ).fetchone()
    compare_intended_vs_actual(db, contract)
    after = db.execute(
        "SELECT fidelity_json, valid_for_learning, status, completed_at "
        "FROM experiment_execution_contracts WHERE id = ?",
        (contract.id,),
    ).fetchone()
    assert dict(before) == dict(after)


# ── Vacuous-truth regression (Step 11) ───────────────────────────────────────


def test_VT1_market_no_treatments_evaluator_matched_valid(db):
    """CASE 1: market exploration + no production treatments + evaluator 'matched'
    → VALID.  Zero treatments is valid for pure market experiments."""
    _build_chain(
        db,
        exp_id="exp-VT1",
        brief_id="brief-VT1",
        opp_id=150,
        brief_planning_intent="market_exploration",
        cluster_id=77,
    )
    db.commit()
    contract = create_execution_contract(db, "exp-VT1", "brief-VT1")
    assert contract.treatment_configs == []
    fidelity = compare_intended_vs_actual(
        db, contract, market_theme_evaluator=lambda cid, b: "matched"
    )
    assert fidelity.classification == FidelityClassification.VALID
    assert fidelity.valid_for_learning is True


def test_VT2_market_no_treatments_evaluator_deviated_not_valid(db):
    """CASE 2: market exploration + no production treatments + evaluator 'deviated'
    → NOT_VALID.  Market theme deviation invalidates the experiment."""
    _build_chain(
        db,
        exp_id="exp-VT2",
        brief_id="brief-VT2",
        opp_id=151,
        brief_planning_intent="market_exploration",
        cluster_id=77,
    )
    db.commit()
    contract = create_execution_contract(db, "exp-VT2", "brief-VT2")
    fidelity = compare_intended_vs_actual(
        db, contract, market_theme_evaluator=lambda cid, b: "deviated"
    )
    assert fidelity.classification == FidelityClassification.NOT_VALID
    assert fidelity.valid_for_learning is False


def test_VT3_market_no_treatments_evaluator_unresolved(db):
    """CASE 3: market exploration + no production treatments + evaluator 'unresolved'
    → UNRESOLVED, valid_for_learning=None."""
    _build_chain(
        db,
        exp_id="exp-VT3",
        brief_id="brief-VT3",
        opp_id=152,
        brief_planning_intent="market_exploration",
        cluster_id=77,
    )
    db.commit()
    contract = create_execution_contract(db, "exp-VT3", "brief-VT3")
    fidelity = compare_intended_vs_actual(
        db, contract, market_theme_evaluator=lambda cid, b: "unresolved"
    )
    assert fidelity.classification == FidelityClassification.UNRESOLVED
    assert fidelity.valid_for_learning is None


def test_VT4_feature_no_treatments_not_valid(db):
    """CASE 4: feature experiment + no feature treatment → NOT_VALID.
    This was the vacuous-truth bug: previously returned valid_for_learning=True
    because 'all([]) == True'.  Phase 14F.2 fixes it."""
    _build_chain(
        db,
        exp_id="exp-VT4",
        brief_id="brief-VT4",
        opp_id=153,
        brief_planning_intent="feature_exploration",
        # zero treatment_factors_json
    )
    db.commit()
    contract = create_execution_contract(db, "exp-VT4", "brief-VT4")
    assert contract.treatment_configs == []
    fidelity = compare_intended_vs_actual(db, contract)
    assert fidelity.classification == FidelityClassification.NOT_VALID
    assert fidelity.valid_for_learning is False


# ── AG–AM: Regression ────────────────────────────────────────────────────────


def test_AG_phase_14f1_execution_service_imports_intact():
    """Phase 14F.1 public symbols remain importable and functional."""
    from app.intelligence.experiments.execution_service import (
        EXECUTION_POLICY_VERSION,
        FIDELITY_POLICY_VERSION,
        MARKET_THEME_FACTOR_NAME,
    )

    assert EXECUTION_POLICY_VERSION == "1.0.0"
    assert FIDELITY_POLICY_VERSION == "1.1.0"
    assert MARKET_THEME_FACTOR_NAME == "market_canonical_cluster"


def test_AH_fidelity_classification_enum_values():
    """All five FidelityClassification values are present with correct strings."""
    assert FidelityClassification.VALID.value == "valid"
    assert FidelityClassification.VALID_WITH_WARNINGS.value == "valid_with_warnings"
    assert FidelityClassification.NOT_VALID.value == "not_valid"
    assert FidelityClassification.NOT_YET_ASSESSABLE.value == "not_yet_assessable"
    assert FidelityClassification.UNRESOLVED.value == "unresolved"


def test_AI_execution_fidelity_has_classification_field():
    """ExecutionFidelity dataclass exposes classification and fidelity_policy_version."""
    from app.intelligence.experiments.execution_contract import ExecutionFidelity

    f = ExecutionFidelity()
    assert f.classification is None
    assert f.fidelity_policy_version == ""
    f2 = ExecutionFidelity(
        classification=FidelityClassification.VALID,
        fidelity_policy_version="1.1.0",
    )
    assert f2.classification == FidelityClassification.VALID


def test_AJ_compare_intended_vs_actual_accepts_market_theme_evaluator():
    """The function signature accepts the keyword-only market_theme_evaluator param."""
    import inspect

    from app.intelligence.experiments.execution_service import compare_intended_vs_actual

    sig = inspect.signature(compare_intended_vs_actual)
    assert "market_theme_evaluator" in sig.parameters
    param = sig.parameters["market_theme_evaluator"]
    assert param.kind == inspect.Parameter.KEYWORD_ONLY


def test_AK_schema_version_41(db):
    """SCHEMA_VERSION is 41 — Phase 14G added experiment_outcomes table."""
    from app.core.database import SCHEMA_VERSION

    assert SCHEMA_VERSION == 51


def test_AL_persist_fidelity_includes_classification_in_json(db):
    """persist_fidelity() serialises classification into fidelity_json blob."""
    _build_chain(
        db,
        exp_id="exp-AL",
        brief_id="brief-AL",
        opp_id=160,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                },
            ]
        ),
    )
    _insert_experiment_factor(db, "exp-AL", intended_value="1.0")
    db.commit()
    contract = create_execution_contract(db, "exp-AL", "brief-AL")
    db.commit()
    plan_id = _insert_production_plan(db, "exp-AL")
    _insert_snapshot(db, plan_id, narration_speaking_rate=1.0)
    fidelity = compare_intended_vs_actual(db, contract)
    persist_fidelity(db, contract.id, fidelity)
    db.commit()
    row = db.execute(
        "SELECT fidelity_json FROM experiment_execution_contracts WHERE id = ?",
        (contract.id,),
    ).fetchone()
    fj = json.loads(row["fidelity_json"])
    assert "classification" in fj
    assert "fidelity_policy_version" in fj
    assert fj["classification"] == "valid"


def test_AM_old_fidelity_json_without_classification_deserialises_gracefully(db):
    """Historical fidelity_json blobs written before Phase 14F.2 (no 'classification'
    key) must deserialise without error — classification defaults to None,
    fidelity_policy_version defaults to ''."""
    _build_chain(
        db,
        exp_id="exp-AM",
        brief_id="brief-AM",
        opp_id=161,
        treatment_factors_json=json.dumps(
            [
                {
                    "factor_name": "narration_speaking_rate",
                    "factor_role": "treatment",
                    "value_type": "numeric",
                },
            ]
        ),
    )
    _insert_experiment_factor(db, "exp-AM", intended_value="1.0")
    db.commit()
    contract = create_execution_contract(db, "exp-AM", "brief-AM")
    db.commit()
    # Write a legacy fidelity_json with no 'classification' or 'fidelity_policy_version'
    legacy_fj = json.dumps(
        {
            "treatment_outcomes": [
                {
                    "factor_name": "narration_speaking_rate",
                    "intended_value": "1.0",
                    "actual_value": "1.0",
                    "outcome": "matched",
                    "reason": "exact match",
                },
            ],
            "control_outcomes": [],
            "valid_for_learning": True,
            "confounding_risk_realized": "low",
            "reasons": [],
            # no 'classification', no 'fidelity_policy_version'
        }
    )
    db.execute(
        "UPDATE experiment_execution_contracts SET fidelity_json = ?, valid_for_learning = 1 "
        "WHERE id = ?",
        (legacy_fj, contract.id),
    )
    db.commit()
    reloaded = get_contract_for_experiment(db, "exp-AM")
    assert reloaded is not None
    assert reloaded.fidelity is not None
    assert reloaded.fidelity.classification is None  # graceful default
    assert reloaded.fidelity.fidelity_policy_version == ""  # graceful default
    assert reloaded.valid_for_learning is True
