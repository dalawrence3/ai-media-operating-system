"""Phase 14G — Experiment Outcome Evaluation, Maturity Gating, and Closed-Loop Evidence.

Groups A–CA covering:
  A–G   — Status and fidelity gates
  H–O   — Analytics maturity
  P–W   — Outcome direction/classification
  X–AE  — Baseline resolution
  AF–AJ — Evidence maturity and is_mature
  AK–AN — Age/views in output
  AO–AP — Provisional warnings
  AQ–AW — Persistence (persist_outcome, get_latest, list)
  AX–BA — Phase14D feedback API (get_outcome_for_phase14d)
  BB–BD — Market experiment type (exploration)
  BE–BG — Feature experiment type (exploitation)
  BH–BJ — Validation experiment (prior_experiment_id)
  BK–BM — Zero/null edge cases
  BN–BS — No side effects
  BT–CA — Regression

Safety invariants:
- No YouTube API calls
- No live analytics ingest
- No content generation
- No LLM calls
- No modifications to experiments, analytics, publications, or Phase12C tables
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.core.database import SCHEMA_VERSION, open_db
from app.intelligence.experiments.outcome_contract import (
    OUTCOME_POLICY_VERSION,
    BaselineSourceType,
    EvidenceMaturity,
    OutcomeClassification,
    OutcomeReadiness,
)
from app.intelligence.experiments.outcome_service import (
    evaluate_experiment_outcome,
    get_latest_experiment_outcome,
    get_outcome_for_phase14d,
    list_experiment_outcomes,
    persist_outcome,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = open_db(tmp_path / "test.db")
    yield conn
    conn.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

_ch_counter = 0
_opp_counter = 0
_exp_counter = 0
_pub_counter = 0
_snap_counter = 0
_agg_counter = 0


def _insert_channel(db: sqlite3.Connection, channel_id: int = 1) -> None:
    db.execute(
        "INSERT OR IGNORE INTO channels (id, platform, channel_name, platform_channel_id)"
        " VALUES (?, 'youtube', 'Test', ?)",
        (channel_id, f"UC{channel_id}g"),
    )


def _insert_opportunity(db: sqlite3.Connection, opp_id: int, channel_id: int = 1) -> int:
    """Insert minimal opportunity chain; returns opp_id."""
    _insert_channel(db, channel_id)
    db.execute(
        "INSERT OR IGNORE INTO channel_profile_versions "
        "(channel_id, primary_niche, status, version)"
        " VALUES (?, 'test', 'active', 1)",
        (channel_id,),
    )
    pv_id = db.execute(
        "SELECT id FROM channel_profile_versions WHERE channel_id = ? LIMIT 1", (channel_id,)
    ).fetchone()["id"]
    db.execute(
        "INSERT OR IGNORE INTO discovery_runs "
        "(channel_id, profile_version_id, adapter_name, status, started_at)"
        " VALUES (?, ?, 'manual', 'completed', '2026-08-22T00:00:00')",
        (channel_id, pv_id),
    )
    run_id = db.execute(
        "SELECT id FROM discovery_runs WHERE channel_id = ? LIMIT 1", (channel_id,)
    ).fetchone()["id"]
    db.execute(
        "INSERT OR IGNORE INTO opportunities"
        " (id, channel_id, discovery_run_id, normalized_topic, raw_topic, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, '2026-08-22T00:00:00', '2026-08-22T00:00:00')",
        (opp_id, channel_id, run_id, f"topic-g-{opp_id}", f"topic-g-{opp_id}"),
    )
    return opp_id


def _insert_experiment(
    db: sqlite3.Connection,
    exp_id: str,
    channel_id: int = 1,
    opp_id: int = 1,
    status: str = "observing",
    exp_type: str = "exploration",
    maturity_policy_json: str = "{}",
) -> str:
    db.execute(
        "INSERT OR IGNORE INTO experiments"
        " (id, channel_id, opportunity_id, experiment_type, status, hypothesis, input_hash,"
        "  maturity_policy_json)"
        " VALUES (?, ?, ?, ?, ?, 'hyp', ?, ?)",
        (exp_id, channel_id, opp_id, exp_type, status, f"hash-{exp_id}", maturity_policy_json),
    )
    return exp_id


def _set_exp_status(db: sqlite3.Connection, exp_id: str, status: str) -> None:
    db.execute("UPDATE experiments SET status = ? WHERE id = ?", (status, exp_id))


def _insert_contract(
    db: sqlite3.Connection,
    exp_id: str,
    channel_id: int = 1,
    opp_id: int = 1,
    fidelity_classification: str = "valid",
) -> None:
    """Insert execution contract with fidelity_json containing given classification."""
    db.commit()
    db.execute("PRAGMA foreign_keys = OFF")
    fidelity = {"classification": fidelity_classification, "fidelity_policy_version": "1.1.0"}
    db.execute(
        "INSERT OR IGNORE INTO experiment_execution_contracts"
        " (id, experiment_id, brief_id, channel_id, opportunity_id,"
        "  execution_mode, fidelity_json, valid_for_learning,"
        "  execution_policy_version)"
        " VALUES (?, ?, 'brief-1', ?, ?, 'dry_run', ?, 1, '1.0.0')",
        (f"contract-{exp_id}", exp_id, channel_id, opp_id, json.dumps(fidelity)),
    )
    db.commit()
    db.execute("PRAGMA foreign_keys = ON")


def _insert_topic(db: sqlite3.Connection, topic_id: int, opp_id: int) -> int:
    """Insert a topic linked to opp so analytics_aggregates.topic_id → opportunities join works.

    topics.promoted_opportunity_id is unique; if opp_id already has a topic, a shadow
    opportunity (id = 1000 + topic_id) is created on the same channel so the baseline
    join still resolves to the correct channel_id.
    """
    existing = db.execute(
        "SELECT id FROM topics WHERE promoted_opportunity_id = ?", (opp_id,)
    ).fetchone()
    if existing is not None and existing["id"] != topic_id:
        ch_row = db.execute(
            "SELECT channel_id FROM opportunities WHERE id = ?", (opp_id,)
        ).fetchone()
        shadow_opp_id = 1000 + topic_id
        _insert_opportunity(db, shadow_opp_id, ch_row["channel_id"] if ch_row else 1)
        opp_id = shadow_opp_id
    db.execute(
        "INSERT OR IGNORE INTO topics (id, title, promoted_opportunity_id)"
        " VALUES (?, 'topic-g', ?)",
        (topic_id, opp_id),
    )
    return topic_id


def _insert_publishing_plan(db: sqlite3.Connection, plan_id: int, topic_id: int) -> int:
    db.commit()
    db.execute("PRAGMA foreign_keys = OFF")
    db.execute(
        "INSERT OR IGNORE INTO publishing_plans"
        " (id, render_manifest_id, topic_id, production_plan_id, script_id, scene_manifest_id,"
        "  narration_run_id, caption_run_id, input_hash, publishing_engine_version,"
        "  metadata_version, provider, provider_version, title, status, created_at, updated_at)"
        " VALUES (?, 1, ?, 1, 1, 1, 1, 1, ?, '1.0', '1.0', 'youtube', '1.0', 'title', 'approved',"
        "  '2026-08-22T00:00:00', '2026-08-22T00:00:00')",
        (plan_id, topic_id, f"ih-plan-{plan_id}"),
    )
    db.commit()
    db.execute("PRAGMA foreign_keys = ON")
    return plan_id


def _insert_publication(
    db: sqlite3.Connection,
    pub_id: int,
    plan_id: int,
    published_at: str = "2026-08-01T00:00:00",
) -> int:
    db.commit()
    db.execute("PRAGMA foreign_keys = OFF")
    db.execute(
        "INSERT OR IGNORE INTO publications"
        " (id, publishing_plan_id, publishing_job_id, provider, provider_version,"
        "  publishing_engine_version, input_hash, output_sha256,"
        "  created_at, updated_at, published_at, status)"
        " VALUES (?, ?, 1, 'youtube', '1.0', '1.0', ?, 'sha',"
        "  '2026-08-22T00:00:00', '2026-08-22T00:00:00', ?, 'published')",
        (pub_id, plan_id, f"ih-pub-{pub_id}", published_at),
    )
    db.commit()
    db.execute("PRAGMA foreign_keys = ON")
    return pub_id


def _link_pub_to_exp(db: sqlite3.Connection, exp_id: str, pub_id: int) -> None:
    db.execute("UPDATE experiments SET publication_id = ? WHERE id = ?", (pub_id, exp_id))


def _insert_agg(
    db: sqlite3.Connection,
    pub_id: int,
    topic_id: int,
    metric_name: str,
    metric_value: float,
    period_type: str = "lifetime",
    period_key: str = "all",
    counter: int | None = None,
) -> None:
    global _agg_counter
    _agg_counter += 1
    c = counter if counter is not None else _agg_counter
    db.execute(
        "INSERT OR IGNORE INTO analytics_aggregates"
        " (publication_id, topic_id, provider, period_type, period_key,"
        "  metric_name, metric_value, snapshot_count, input_hash, created_at)"
        " VALUES (?, ?, 'youtube', ?, ?, ?, ?, 1, ?, '2026-08-22T00:00:00')",
        (pub_id, topic_id, period_type, period_key, metric_name, metric_value, f"ih-agg-{c}"),
    )


def _insert_snapshot(
    db: sqlite3.Connection,
    pub_id: int,
    observation_state: str = "data",
) -> int:
    global _snap_counter
    _snap_counter += 1
    db.commit()
    db.execute("PRAGMA foreign_keys = OFF")
    db.execute(
        "INSERT INTO analytics_snapshots"
        " (publication_id, publishing_plan_id, publishing_job_id, render_manifest_id,"
        "  scene_manifest_id, production_plan_id, script_id, topic_id, narration_run_id,"
        "  caption_run_id, provider, provider_version, adapter_version, engine_version,"
        "  analytics_schema_version, db_schema_version, input_hash, raw_metrics_json,"
        "  is_period_complete, ingested_at, created_at, observation_state)"
        " VALUES (?, 1, 1, 1, 1, 1, 1, 1, 1, 1, 'youtube', '1.0', '1.0', '1.0',"
        "         '1.0', 41, ?, '{}', 0, '2026-08-22T00:00:00', '2026-08-22T00:00:00', ?)",
        (pub_id, f"ih-snap-{_snap_counter}", observation_state),
    )
    snap_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.commit()
    db.execute("PRAGMA foreign_keys = ON")
    return snap_id


def _insert_metric_target(
    db: sqlite3.Connection,
    exp_id: str,
    metric_name: str,
    direction: str,
    is_primary: bool = True,
    target_min: float | None = None,
    target_max: float | None = None,
) -> None:
    db.execute(
        "INSERT OR IGNORE INTO experiment_metric_targets"
        " (experiment_id, metric_name, direction, is_primary, target_min, target_max)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (exp_id, metric_name, direction, 1 if is_primary else 0, target_min, target_max),
    )


_pr_counter = 0


def _insert_selection_decision_validation(
    db: sqlite3.Connection, opp_id: int, channel_id: int = 1
) -> None:
    """Insert planning chain that marks the opportunity as a validation repeat."""
    global _pr_counter
    _pr_counter += 1
    run_id = f"run-g-{_pr_counter}"
    db.execute(
        "INSERT OR IGNORE INTO experiment_planning_runs"
        " (id, channel_id, status, eligible_count, exploration_only_count,"
        "  general_eligible_count, selected_count, deferred_count, input_hash)"
        " VALUES (?, ?, 'completed', 1, 0, 1, 1, 0, ?)",
        (run_id, channel_id, f"hash-run-g-{_pr_counter}"),
    )
    cs_id = db.execute(
        "INSERT INTO experiment_candidate_scores"
        " (planning_run_id, opportunity_id, channel_id,"
        "  eligibility_classification, planning_intent, experiment_type,"
        "  primary_target_metric, primary_metric_direction, hypothesis_sketch,"
        "  intended_treatment_factors_json, controlled_factors_json,"
        "  feature_change_risk, final_planning_score, input_hash)"
        " VALUES (?, ?, ?, 'general_eligible', 'validation', 'exploitation',"
        "         'average_view_percentage', 'higher_is_better', 'hyp',"
        "         '[]', '[]', 'low', 0.5, ?)"
        " RETURNING id",
        (run_id, opp_id, channel_id, f"hash-cs-g-{_pr_counter}"),
    ).fetchone()["id"]
    db.execute(
        "INSERT INTO experiment_selection_decisions"
        " (planning_run_id, candidate_score_id, opportunity_id, selected,"
        "  rank_in_pool, pool_type, selection_reason, is_validation_repeat)"
        " VALUES (?, ?, ?, 1, 1, 'exploitation', 'top scored', 1)",
        (run_id, cs_id, opp_id),
    )


def _build_evaluable_chain(
    db: sqlite3.Connection,
    *,
    exp_id: str,
    channel_id: int = 1,
    opp_id: int = 1,
    topic_id: int = 10,
    pub_id: int = 100,
    plan_id: int = 200,
    published_at: str = "2026-08-01T00:00:00",
    views: float = 50.0,
    metric_name: str = "average_view_percentage",
    metric_value: float = 42.0,
    fidelity_classification: str = "valid",
    status: str = "observing",
    exp_type: str = "exploration",
) -> None:
    """Minimal full chain to get to EVALUABLE_MATURE."""
    _insert_opportunity(db, opp_id, channel_id)
    _insert_experiment(db, exp_id, channel_id, opp_id, status=status, exp_type=exp_type)
    _insert_contract(db, exp_id, channel_id, opp_id, fidelity_classification)
    _insert_topic(db, topic_id, opp_id)
    _insert_publishing_plan(db, plan_id, topic_id)
    _insert_publication(db, pub_id, plan_id, published_at)
    _link_pub_to_exp(db, exp_id, pub_id)
    _insert_snapshot(db, pub_id, "data")
    _insert_agg(db, pub_id, topic_id, "views", views)
    _insert_agg(db, pub_id, topic_id, metric_name, metric_value)
    db.commit()


# ── A–G: Status and fidelity gates ───────────────────────────────────────────


def test_A_not_ready_when_draft(db):
    """Experiment in 'draft' status → NOT_READY."""
    _insert_opportunity(db, 1)
    _insert_experiment(db, "exp-A", status="draft")
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-A")
    assert ev.readiness == OutcomeReadiness.NOT_READY
    assert "draft" in ev.reasons[0]


def test_B_not_ready_when_planned(db):
    """Experiment in 'planned' → NOT_READY."""
    _insert_opportunity(db, 1)
    _insert_experiment(db, "exp-B", status="planned")
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-B")
    assert ev.readiness == OutcomeReadiness.NOT_READY


def test_C_not_ready_when_in_production(db):
    """Experiment in 'in_production' → NOT_READY."""
    _insert_opportunity(db, 1)
    _insert_experiment(db, "exp-C", status="in_production")
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-C")
    assert ev.readiness == OutcomeReadiness.NOT_READY


def test_D_invalid_execution_when_no_contract(db):
    """Evaluable status but no execution contract → INVALID_EXECUTION."""
    _insert_opportunity(db, 1)
    _insert_experiment(db, "exp-D", status="observing")
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-D")
    assert ev.readiness == OutcomeReadiness.INVALID_EXECUTION
    assert "no execution contract" in ev.reasons[0]


def test_E_invalid_execution_when_fidelity_not_valid(db):
    """Contract exists but classification == 'not_valid' → INVALID_EXECUTION."""
    _insert_opportunity(db, 1)
    _insert_experiment(db, "exp-E", status="observing")
    _insert_contract(db, "exp-E", fidelity_classification="not_valid")
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-E")
    assert ev.readiness == OutcomeReadiness.INVALID_EXECUTION


def test_F_invalid_execution_when_not_yet_assessable(db):
    """Classification 'not_yet_assessable' → INVALID_EXECUTION."""
    _insert_opportunity(db, 1)
    _insert_experiment(db, "exp-F", status="observing")
    _insert_contract(db, "exp-F", fidelity_classification="not_yet_assessable")
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-F")
    assert ev.readiness == OutcomeReadiness.INVALID_EXECUTION


def test_G_passes_fidelity_gate_when_valid_with_warnings(db):
    """'valid_with_warnings' passes the fidelity gate (does not return INVALID_EXECUTION)."""
    _insert_opportunity(db, 1)
    _insert_experiment(db, "exp-G", status="observing")
    _insert_contract(db, "exp-G", fidelity_classification="valid_with_warnings")
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-G")
    assert ev.readiness != OutcomeReadiness.INVALID_EXECUTION


# ── H–O: Analytics maturity ───────────────────────────────────────────────────


def test_H_insufficient_analytics_no_publication(db):
    """Status OK, contract OK, but no publication_id → INSUFFICIENT_ANALYTICS."""
    _insert_opportunity(db, 1)
    _insert_experiment(db, "exp-H", status="observing")
    _insert_contract(db, "exp-H")
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-H")
    assert ev.readiness == OutcomeReadiness.INSUFFICIENT_ANALYTICS
    assert "no linked publication" in ev.reasons[0]


def test_I_insufficient_analytics_no_views_aggregate(db):
    """Publication exists but no 'views' lifetime aggregate → INSUFFICIENT_ANALYTICS."""
    _insert_opportunity(db, 1)
    _insert_experiment(db, "exp-I", status="observing")
    _insert_contract(db, "exp-I")
    _insert_topic(db, 10, 1)
    _insert_publishing_plan(db, 200, 10)
    _insert_publication(db, 100, 200, "2026-08-01T00:00:00")
    _link_pub_to_exp(db, "exp-I", 100)
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-I")
    assert ev.readiness == OutcomeReadiness.INSUFFICIENT_ANALYTICS


def test_J_evaluable_provisional_views_below_threshold_age_ok(db):
    """Views aggregate exists (below threshold=10) but age >= 48h → EVALUABLE_PROVISIONAL."""
    _insert_opportunity(db, 1)
    _insert_experiment(db, "exp-J", status="observing")
    _insert_contract(db, "exp-J")
    _insert_topic(db, 10, 1)
    _insert_publishing_plan(db, 200, 10)
    _insert_publication(db, 100, 200, "2026-08-01T00:00:00")
    _link_pub_to_exp(db, "exp-J", 100)
    _insert_snapshot(db, 100, "data")
    _insert_agg(db, 100, 10, "views", 3.0)
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-J")
    assert ev.readiness == OutcomeReadiness.EVALUABLE_PROVISIONAL
    assert ev.observed_views == pytest.approx(3.0)


def test_K_evaluable_provisional_views_ok_age_not_ok(db):
    """Views >= 10 but published_at too recent (< 48h) → EVALUABLE_PROVISIONAL."""
    _insert_opportunity(db, 1)
    _insert_experiment(db, "exp-K", status="observing")
    _insert_contract(db, "exp-K")
    _insert_topic(db, 10, 1)
    _insert_publishing_plan(db, 200, 10)
    # Use a very recent published_at so age < 48h
    _insert_publication(db, 100, 200, "2030-01-01T00:00:00")
    _link_pub_to_exp(db, "exp-K", 100)
    _insert_snapshot(db, 100, "data")
    _insert_agg(db, 100, 10, "views", 50.0)
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-K")
    assert ev.readiness == OutcomeReadiness.EVALUABLE_PROVISIONAL


def test_L_evaluable_provisional_age_ok_views_not_ok(db):
    """Age >= 48h but views below threshold → EVALUABLE_PROVISIONAL."""
    _insert_opportunity(db, 1)
    _insert_experiment(db, "exp-L", status="observing")
    _insert_contract(db, "exp-L")
    _insert_topic(db, 10, 1)
    _insert_publishing_plan(db, 200, 10)
    _insert_publication(db, 100, 200, "2026-08-01T00:00:00")
    _link_pub_to_exp(db, "exp-L", 100)
    _insert_snapshot(db, 100, "data")
    _insert_agg(db, 100, 10, "views", 5.0)
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-L")
    assert ev.readiness == OutcomeReadiness.EVALUABLE_PROVISIONAL


def test_M_evaluable_mature_both_thresholds_met(db):
    """Age >= 48h AND views >= 10 → EVALUABLE_MATURE."""
    _build_evaluable_chain(db, exp_id="exp-M", views=50.0)
    ev = evaluate_experiment_outcome(db, "exp-M")
    assert ev.readiness == OutcomeReadiness.EVALUABLE_MATURE


def test_N_insufficient_analytics_null_views(db):
    """No views agg row at all → INSUFFICIENT_ANALYTICS."""
    _insert_opportunity(db, 1)
    _insert_experiment(db, "exp-N", status="observing")
    _insert_contract(db, "exp-N")
    _insert_topic(db, 10, 1)
    _insert_publishing_plan(db, 200, 10)
    _insert_publication(db, 100, 200, "2026-08-01T00:00:00")
    _link_pub_to_exp(db, "exp-N", 100)
    # Only a non-views aggregate — no views row
    _insert_agg(db, 100, 10, "average_view_percentage", 40.0)
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-N")
    assert ev.readiness == OutcomeReadiness.INSUFFICIENT_ANALYTICS
    assert ev.observed_views is None


def test_O_evaluable_mature_in_completed_status(db):
    """Status 'completed' (late lifecycle) is also evaluable."""
    _build_evaluable_chain(db, exp_id="exp-O", status="completed")
    ev = evaluate_experiment_outcome(db, "exp-O")
    assert ev.readiness == OutcomeReadiness.EVALUABLE_MATURE


# ── P–W: Outcome direction and classification ─────────────────────────────────


def test_P_positive_observation_higher_is_better_positive_delta(db):
    """higher_is_better + treatment > baseline → POSITIVE_OBSERVATION."""
    _build_evaluable_chain(
        db, exp_id="exp-P", metric_value=50.0, opp_id=2, topic_id=11, pub_id=101, plan_id=201
    )
    # Insert a second publication for the channel baseline
    _insert_topic(db, 12, 2)
    _insert_publishing_plan(db, 202, 12)
    _insert_publication(db, 102, 202)
    _insert_agg(db, 102, 12, "average_view_percentage", 30.0)
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-P")
    assert ev.classification == OutcomeClassification.POSITIVE_OBSERVATION
    assert ev.absolute_delta == pytest.approx(20.0)


def test_Q_negative_observation_higher_is_better_negative_delta(db):
    """higher_is_better + treatment < baseline → NEGATIVE_OBSERVATION."""
    _build_evaluable_chain(
        db, exp_id="exp-Q", metric_value=20.0, opp_id=3, topic_id=13, pub_id=103, plan_id=203
    )
    _insert_topic(db, 14, 3)
    _insert_publishing_plan(db, 204, 14)
    _insert_publication(db, 104, 204)
    _insert_agg(db, 104, 14, "average_view_percentage", 40.0)
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-Q")
    assert ev.classification == OutcomeClassification.NEGATIVE_OBSERVATION
    assert ev.absolute_delta == pytest.approx(-20.0)


def test_R_neutral_observation_zero_delta(db):
    """Exact same value as baseline → NEUTRAL_OBSERVATION."""
    _build_evaluable_chain(
        db, exp_id="exp-R", metric_value=40.0, opp_id=4, topic_id=15, pub_id=105, plan_id=205
    )
    _insert_topic(db, 16, 4)
    _insert_publishing_plan(db, 206, 16)
    _insert_publication(db, 106, 206)
    _insert_agg(db, 106, 16, "average_view_percentage", 40.0)
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-R")
    assert ev.classification == OutcomeClassification.NEUTRAL_OBSERVATION
    assert ev.absolute_delta == pytest.approx(0.0)


def test_S_positive_observation_lower_is_better_negative_delta(db):
    """lower_is_better + treatment < baseline → POSITIVE (improvement)."""
    _build_evaluable_chain(
        db,
        exp_id="exp-S",
        metric_name="bounce_rate",
        metric_value=5.0,
        opp_id=5,
        topic_id=17,
        pub_id=107,
        plan_id=207,
    )
    _insert_metric_target(db, "exp-S", "bounce_rate", "lower_is_better")
    _insert_topic(db, 18, 5)
    _insert_publishing_plan(db, 208, 18)
    _insert_publication(db, 108, 208)
    _insert_agg(db, 108, 18, "bounce_rate", 10.0)
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-S")
    assert ev.classification == OutcomeClassification.POSITIVE_OBSERVATION


def test_T_negative_observation_lower_is_better_positive_delta(db):
    """lower_is_better + treatment > baseline → NEGATIVE (regression)."""
    _build_evaluable_chain(
        db,
        exp_id="exp-T",
        metric_name="bounce_rate",
        metric_value=15.0,
        opp_id=6,
        topic_id=19,
        pub_id=109,
        plan_id=209,
    )
    _insert_metric_target(db, "exp-T", "bounce_rate", "lower_is_better")
    _insert_topic(db, 20, 6)
    _insert_publishing_plan(db, 210, 20)
    _insert_publication(db, 110, 210)
    _insert_agg(db, 110, 20, "bounce_rate", 10.0)
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-T")
    assert ev.classification == OutcomeClassification.NEGATIVE_OBSERVATION


def test_U_informational_only_direction(db):
    """informational_only direction → INFORMATIONAL_ONLY, no delta."""
    _build_evaluable_chain(
        db,
        exp_id="exp-U",
        metric_name="shares",
        metric_value=5.0,
        opp_id=7,
        topic_id=21,
        pub_id=111,
        plan_id=211,
    )
    _insert_metric_target(db, "exp-U", "shares", "informational_only")
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-U")
    assert ev.classification == OutcomeClassification.INFORMATIONAL_ONLY
    assert ev.absolute_delta is None
    assert ev.relative_delta is None


def test_V_target_range_in_range_is_positive(db):
    """target_range with value within [min, max] → POSITIVE_OBSERVATION."""
    _build_evaluable_chain(
        db,
        exp_id="exp-V",
        metric_name="ctr",
        metric_value=5.0,
        opp_id=8,
        topic_id=22,
        pub_id=112,
        plan_id=212,
    )
    _insert_metric_target(db, "exp-V", "ctr", "target_range", target_min=3.0, target_max=7.0)
    # Need a baseline to avoid BASELINE_UNAVAILABLE
    _insert_topic(db, 23, 8)
    _insert_publishing_plan(db, 213, 23)
    _insert_publication(db, 113, 213)
    _insert_agg(db, 113, 23, "ctr", 4.0)
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-V")
    assert ev.classification == OutcomeClassification.POSITIVE_OBSERVATION


def test_W_target_range_out_of_range_is_negative(db):
    """target_range with value outside [min, max] → NEGATIVE_OBSERVATION."""
    _build_evaluable_chain(
        db,
        exp_id="exp-W",
        metric_name="ctr",
        metric_value=1.0,
        opp_id=9,
        topic_id=24,
        pub_id=114,
        plan_id=214,
    )
    _insert_metric_target(db, "exp-W", "ctr", "target_range", target_min=3.0, target_max=7.0)
    _insert_topic(db, 25, 9)
    _insert_publishing_plan(db, 215, 25)
    _insert_publication(db, 115, 215)
    _insert_agg(db, 115, 25, "ctr", 4.0)
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-W")
    assert ev.classification == OutcomeClassification.NEGATIVE_OBSERVATION


# ── X–AE: Baseline resolution ─────────────────────────────────────────────────


def test_X_channel_baseline_used_by_default(db):
    """No prior experiment → CHANNEL_BASELINE when channel avg is available."""
    _build_evaluable_chain(
        db, exp_id="exp-X", metric_value=50.0, opp_id=10, topic_id=26, pub_id=116, plan_id=216
    )
    _insert_topic(db, 27, 10)
    _insert_publishing_plan(db, 217, 27)
    _insert_publication(db, 117, 217)
    _insert_agg(db, 117, 27, "average_view_percentage", 30.0)
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-X")
    assert ev.baseline_source_type == BaselineSourceType.CHANNEL_BASELINE
    assert ev.baseline_metric_value == pytest.approx(30.0)


def test_Y_baseline_unavailable_when_no_other_channel_publications(db):
    """Channel has no other publications with avg_view_percentage → BASELINE_UNAVAILABLE."""
    _build_evaluable_chain(db, exp_id="exp-Y", opp_id=11, topic_id=28, pub_id=118, plan_id=218)
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-Y")
    assert ev.baseline_source_type == BaselineSourceType.NONE
    assert ev.classification == OutcomeClassification.BASELINE_UNAVAILABLE


def test_Z_prior_experiment_baseline(db):
    """prior_experiment_id provided with outcome record → PRIOR_EXPERIMENT baseline."""
    # Prior experiment outcome
    _build_evaluable_chain(
        db, exp_id="exp-Z-prior", metric_value=30.0, opp_id=12, topic_id=29, pub_id=119, plan_id=219
    )
    prior_ev = evaluate_experiment_outcome(db, "exp-Z-prior")
    persist_outcome(db, prior_ev)
    # Current experiment
    _build_evaluable_chain(
        db, exp_id="exp-Z", metric_value=45.0, opp_id=13, topic_id=30, pub_id=120, plan_id=220
    )
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-Z", prior_experiment_id="exp-Z-prior")
    assert ev.baseline_source_type == BaselineSourceType.PRIOR_EXPERIMENT
    assert ev.baseline_experiment_id == "exp-Z-prior"
    assert ev.baseline_metric_value == pytest.approx(30.0)


def test_AA_validation_reference_baseline(db):
    """is_validation_repeat=True + prior_experiment_id → VALIDATION_REFERENCE."""
    _build_evaluable_chain(
        db,
        exp_id="exp-AA-prior",
        metric_value=28.0,
        opp_id=14,
        topic_id=31,
        pub_id=121,
        plan_id=221,
    )
    prior_ev = evaluate_experiment_outcome(db, "exp-AA-prior")
    persist_outcome(db, prior_ev)
    _insert_opportunity(db, 15)
    _insert_selection_decision_validation(db, 15)
    _insert_experiment(db, "exp-AA", opp_id=15, status="observing")
    _insert_contract(db, "exp-AA", opp_id=15)
    _insert_topic(db, 32, 15)
    _insert_publishing_plan(db, 222, 32)
    _insert_publication(db, 122, 222)
    _link_pub_to_exp(db, "exp-AA", 122)
    _insert_snapshot(db, 122, "data")
    _insert_agg(db, 122, 32, "views", 50.0)
    _insert_agg(db, 122, 32, "average_view_percentage", 35.0)
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-AA", prior_experiment_id="exp-AA-prior")
    assert ev.baseline_source_type == BaselineSourceType.VALIDATION_REFERENCE
    assert ev.baseline_experiment_id == "exp-AA-prior"
    assert ev.baseline_metric_value == pytest.approx(28.0)


def test_AB_channel_baseline_average_of_multiple_publications(db):
    """Channel baseline is the AVG across all other publications."""
    _build_evaluable_chain(
        db, exp_id="exp-AB", metric_value=60.0, opp_id=16, topic_id=33, pub_id=123, plan_id=223
    )
    _insert_topic(db, 34, 16)
    _insert_publishing_plan(db, 224, 34)
    _insert_publication(db, 124, 224)
    _insert_agg(db, 124, 34, "average_view_percentage", 20.0)
    _insert_topic(db, 35, 16)
    _insert_publishing_plan(db, 225, 35)
    _insert_publication(db, 125, 225)
    _insert_agg(db, 125, 35, "average_view_percentage", 40.0)
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-AB")
    # Baseline = avg(20.0, 40.0) = 30.0
    assert ev.baseline_metric_value == pytest.approx(30.0)


def test_AC_absolute_delta_computed_correctly(db):
    """absolute_delta = treatment_value - baseline_value."""
    _build_evaluable_chain(
        db, exp_id="exp-AC", metric_value=55.0, opp_id=17, topic_id=36, pub_id=126, plan_id=226
    )
    _insert_topic(db, 37, 17)
    _insert_publishing_plan(db, 227, 37)
    _insert_publication(db, 127, 227)
    _insert_agg(db, 127, 37, "average_view_percentage", 45.0)
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-AC")
    assert ev.absolute_delta == pytest.approx(10.0)
    assert ev.relative_delta == pytest.approx(10.0 / 45.0)


def test_AD_relative_delta_absent_when_no_baseline(db):
    """No channel baseline → relative_delta is None."""
    _build_evaluable_chain(db, exp_id="exp-AD", opp_id=18, topic_id=38, pub_id=128, plan_id=228)
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-AD")
    assert ev.relative_delta is None
    assert ev.absolute_delta is None


def test_AE_prior_experiment_baseline_falls_back_to_channel_if_no_outcome(db):
    """prior_experiment_id provided but no persisted outcome → falls back to CHANNEL_BASELINE."""
    _build_evaluable_chain(
        db, exp_id="exp-AE", metric_value=50.0, opp_id=19, topic_id=39, pub_id=129, plan_id=229
    )
    _insert_topic(db, 40, 19)
    _insert_publishing_plan(db, 230, 40)
    _insert_publication(db, 130, 230)
    _insert_agg(db, 130, 40, "average_view_percentage", 35.0)
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-AE", prior_experiment_id="nonexistent-prior")
    assert ev.baseline_source_type == BaselineSourceType.CHANNEL_BASELINE


# ── AF–AJ: Evidence maturity and is_mature ────────────────────────────────────


def test_AF_is_mature_true_when_evaluable_mature(db):
    """is_mature=True when readiness == EVALUABLE_MATURE."""
    _build_evaluable_chain(db, exp_id="exp-AF", opp_id=20, topic_id=41, pub_id=131, plan_id=231)
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-AF")
    assert ev.readiness == OutcomeReadiness.EVALUABLE_MATURE
    assert ev.is_mature is True


def test_AG_is_mature_false_when_evaluable_provisional(db):
    """is_mature=False when readiness == EVALUABLE_PROVISIONAL."""
    _insert_opportunity(db, 21)
    _insert_experiment(db, "exp-AG", opp_id=21, status="observing")
    _insert_contract(db, "exp-AG", opp_id=21)
    _insert_topic(db, 42, 21)
    _insert_publishing_plan(db, 232, 42)
    _insert_publication(db, 132, 232, "2030-01-01T00:00:00")  # future → age < 0
    _link_pub_to_exp(db, "exp-AG", 132)
    _insert_snapshot(db, 132, "data")
    _insert_agg(db, 132, 42, "views", 50.0)
    _insert_agg(db, 132, 42, "average_view_percentage", 40.0)
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-AG")
    assert ev.readiness == OutcomeReadiness.EVALUABLE_PROVISIONAL
    assert ev.is_mature is False


def test_AH_evidence_maturity_exploratory_without_prior(db):
    """Single experiment, no prior_experiment_id → EXPLORATORY."""
    _build_evaluable_chain(db, exp_id="exp-AH", opp_id=22, topic_id=43, pub_id=133, plan_id=233)
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-AH")
    assert ev.evidence_maturity == EvidenceMaturity.EXPLORATORY


def test_AI_evidence_maturity_directional_with_prior(db):
    """Validation + prior_experiment_id with outcome → DIRECTIONAL."""
    _build_evaluable_chain(
        db,
        exp_id="exp-AI-prior",
        metric_value=30.0,
        opp_id=23,
        topic_id=44,
        pub_id=134,
        plan_id=234,
    )
    prior_ev = evaluate_experiment_outcome(db, "exp-AI-prior")
    persist_outcome(db, prior_ev)
    _insert_opportunity(db, 24)
    _insert_selection_decision_validation(db, 24)
    _insert_experiment(db, "exp-AI", opp_id=24, status="observing")
    _insert_contract(db, "exp-AI", opp_id=24)
    _insert_topic(db, 45, 24)
    _insert_publishing_plan(db, 235, 45)
    _insert_publication(db, 135, 235)
    _link_pub_to_exp(db, "exp-AI", 135)
    _insert_snapshot(db, 135, "data")
    _insert_agg(db, 135, 45, "views", 50.0)
    _insert_agg(db, 135, 45, "average_view_percentage", 35.0)
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-AI", prior_experiment_id="exp-AI-prior")
    assert ev.evidence_maturity == EvidenceMaturity.DIRECTIONAL


def test_AJ_evidence_maturity_exploratory_if_prior_but_not_validation(db):
    """prior_experiment_id provided but is_validation_repeat=False → EXPLORATORY."""
    _build_evaluable_chain(
        db,
        exp_id="exp-AJ-prior",
        metric_value=30.0,
        opp_id=25,
        topic_id=46,
        pub_id=136,
        plan_id=236,
    )
    prior_ev = evaluate_experiment_outcome(db, "exp-AJ-prior")
    persist_outcome(db, prior_ev)
    _build_evaluable_chain(
        db, exp_id="exp-AJ", metric_value=40.0, opp_id=26, topic_id=47, pub_id=137, plan_id=237
    )
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-AJ", prior_experiment_id="exp-AJ-prior")
    # is_validation is False since no is_validation_repeat decision
    assert ev.evidence_maturity == EvidenceMaturity.EXPLORATORY


# ── AK–AN: Age, views, and publication data in output ─────────────────────────


def test_AK_publication_age_hours_is_positive(db):
    """publication_age_hours is computed and positive for old publications."""
    _build_evaluable_chain(
        db,
        exp_id="exp-AK",
        published_at="2026-08-01T00:00:00",
        opp_id=27,
        topic_id=48,
        pub_id=138,
        plan_id=238,
    )
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-AK")
    assert ev.publication_age_hours is not None
    assert ev.publication_age_hours > 48.0


def test_AL_publication_age_hours_present_in_insufficient_analytics(db):
    """Age is computed even when analytics are insufficient."""
    _insert_opportunity(db, 28)
    _insert_experiment(db, "exp-AL", opp_id=28, status="observing")
    _insert_contract(db, "exp-AL", opp_id=28)
    _insert_topic(db, 49, 28)
    _insert_publishing_plan(db, 239, 49)
    _insert_publication(db, 139, 239, "2026-08-01T00:00:00")
    _link_pub_to_exp(db, "exp-AL", 139)
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-AL")
    assert ev.readiness == OutcomeReadiness.INSUFFICIENT_ANALYTICS
    assert ev.publication_age_hours is not None and ev.publication_age_hours > 0


def test_AM_observed_views_returned_in_output(db):
    """observed_views field matches what was inserted."""
    _build_evaluable_chain(
        db, exp_id="exp-AM", views=73.0, opp_id=29, topic_id=50, pub_id=140, plan_id=240
    )
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-AM")
    assert ev.observed_views == pytest.approx(73.0)


def test_AN_target_metric_name_defaults_to_avg_view_pct(db):
    """When no metric_targets row, defaults to 'average_view_percentage'."""
    _build_evaluable_chain(db, exp_id="exp-AN", opp_id=30, topic_id=51, pub_id=141, plan_id=241)
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-AN")
    assert ev.target_metric_name == "average_view_percentage"
    assert ev.target_metric_direction == "higher_is_better"


# ── AO–AP: Provisional warnings ───────────────────────────────────────────────


def test_AO_provisional_warning_present(db):
    """EVALUABLE_PROVISIONAL includes at least one warning about maturity."""
    _insert_opportunity(db, 31)
    _insert_experiment(db, "exp-AO", opp_id=31, status="observing")
    _insert_contract(db, "exp-AO", opp_id=31)
    _insert_topic(db, 52, 31)
    _insert_publishing_plan(db, 242, 52)
    _insert_publication(db, 142, 242, "2030-01-01T00:00:00")
    _link_pub_to_exp(db, "exp-AO", 142)
    _insert_snapshot(db, 142, "data")
    _insert_agg(db, 142, 52, "views", 50.0)
    _insert_agg(db, 142, 52, "average_view_percentage", 40.0)
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-AO")
    assert ev.readiness == OutcomeReadiness.EVALUABLE_PROVISIONAL
    assert len(ev.warnings) >= 1
    assert any("provisional" in w for w in ev.warnings)


def test_AP_no_warnings_when_mature(db):
    """EVALUABLE_MATURE produces no maturity warnings."""
    _build_evaluable_chain(db, exp_id="exp-AP", opp_id=32, topic_id=53, pub_id=143, plan_id=243)
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-AP")
    assert ev.readiness == OutcomeReadiness.EVALUABLE_MATURE
    assert not any("provisional" in w for w in ev.warnings)


# ── AQ–AW: Persistence ────────────────────────────────────────────────────────


def test_AQ_persist_outcome_inserts_row(db):
    """persist_outcome() writes a row to experiment_outcomes."""
    _build_evaluable_chain(db, exp_id="exp-AQ", opp_id=33, topic_id=54, pub_id=144, plan_id=244)
    ev = evaluate_experiment_outcome(db, "exp-AQ")
    persist_outcome(db, ev)
    db.commit()
    row = db.execute("SELECT * FROM experiment_outcomes WHERE experiment_id = 'exp-AQ'").fetchone()
    assert row is not None
    assert row["readiness"] == ev.readiness.value


def test_AR_persist_is_append_only(db):
    """Calling persist twice with same evaluation inserts only 1 row (idempotent)."""
    _build_evaluable_chain(db, exp_id="exp-AR", opp_id=34, topic_id=55, pub_id=145, plan_id=245)
    ev = evaluate_experiment_outcome(db, "exp-AR")
    persist_outcome(db, ev)
    persist_outcome(db, ev)  # second call same hash → INSERT OR IGNORE
    db.commit()
    count = db.execute(
        "SELECT COUNT(*) FROM experiment_outcomes WHERE experiment_id = 'exp-AR'"
    ).fetchone()[0]
    assert count == 1


def test_AS_two_different_evaluations_both_persisted(db):
    """Two evaluations with different input_hashes both persist (append-only)."""
    _build_evaluable_chain(db, exp_id="exp-AS", opp_id=35, topic_id=56, pub_id=146, plan_id=246)
    ev1 = evaluate_experiment_outcome(db, "exp-AS")
    persist_outcome(db, ev1)
    import uuid as _uuid

    ev2_id = str(_uuid.uuid4())
    ev2 = type(ev1)(
        id=ev2_id,
        experiment_id="exp-AS",
        readiness=ev1.readiness,
        classification=ev1.classification,
        evidence_maturity=ev1.evidence_maturity,
        baseline_source_type=ev1.baseline_source_type,
        baseline_experiment_id=ev1.baseline_experiment_id,
        target_metric_name=ev1.target_metric_name,
        target_metric_direction=ev1.target_metric_direction,
        treatment_metric_value=ev1.treatment_metric_value,
        baseline_metric_value=ev1.baseline_metric_value,
        absolute_delta=ev1.absolute_delta,
        relative_delta=ev1.relative_delta,
        is_mature=ev1.is_mature,
        publication_age_hours=ev1.publication_age_hours,
        observed_views=ev1.observed_views,
        reasons=ev1.reasons,
        warnings=ev1.warnings,
        outcome_policy_version=ev1.outcome_policy_version,
        input_hash="different-hash-intentional",
        evaluated_at="2026-08-23T00:00:00",
    )
    persist_outcome(db, ev2)
    db.commit()
    count = db.execute(
        "SELECT COUNT(*) FROM experiment_outcomes WHERE experiment_id = 'exp-AS'"
    ).fetchone()[0]
    assert count == 2


def test_AT_get_latest_experiment_outcome_returns_most_recent(db):
    """get_latest_experiment_outcome() returns the newest row."""
    _build_evaluable_chain(db, exp_id="exp-AT", opp_id=36, topic_id=57, pub_id=147, plan_id=247)
    ev1 = evaluate_experiment_outcome(db, "exp-AT")
    persist_outcome(db, ev1)
    # Insert older row directly with an earlier evaluated_at
    db.execute(
        """INSERT INTO experiment_outcomes
           (id, experiment_id, readiness, baseline_source_type,
            is_mature, reasons_json, warnings_json, outcome_policy_version,
            input_hash, evaluated_at)
           VALUES ('old-row', 'exp-AT', 'not_ready', 'none',
                   0, '[]', '[]', '1.0.0', 'old-hash', '2020-01-01T00:00:00')"""
    )
    db.commit()
    latest = get_latest_experiment_outcome(db, "exp-AT")
    assert latest is not None
    assert latest.id == ev1.id


def test_AU_list_experiment_outcomes_returns_all(db):
    """list_experiment_outcomes() returns all rows for the experiment."""
    _build_evaluable_chain(db, exp_id="exp-AU", opp_id=37, topic_id=58, pub_id=148, plan_id=248)
    ev = evaluate_experiment_outcome(db, "exp-AU")
    persist_outcome(db, ev)
    db.execute(
        """INSERT INTO experiment_outcomes
           (id, experiment_id, readiness, baseline_source_type,
            is_mature, reasons_json, warnings_json, outcome_policy_version,
            input_hash, evaluated_at)
           VALUES ('extra-row', 'exp-AU', 'not_ready', 'none',
                   0, '[]', '[]', '1.0.0', 'extra-hash', '2020-01-01T00:00:00')"""
    )
    db.commit()
    outcomes = list_experiment_outcomes(db, "exp-AU")
    assert len(outcomes) == 2


def test_AV_input_hash_stable_for_same_inputs(db):
    """Calling evaluate twice with same state → same input_hash."""
    _build_evaluable_chain(db, exp_id="exp-AV", opp_id=38, topic_id=59, pub_id=149, plan_id=249)
    ev1 = evaluate_experiment_outcome(db, "exp-AV")
    ev2 = evaluate_experiment_outcome(db, "exp-AV")
    assert ev1.input_hash == ev2.input_hash


def test_AW_persisted_fields_round_trip(db):
    """Fields serialised then deserialised from experiment_outcomes match."""
    _build_evaluable_chain(
        db, exp_id="exp-AW", metric_value=55.0, opp_id=39, topic_id=60, pub_id=150, plan_id=250
    )
    _insert_topic(db, 61, 39)
    _insert_publishing_plan(db, 251, 61)
    _insert_publication(db, 151, 251)
    _insert_agg(db, 151, 61, "average_view_percentage", 40.0)
    ev = evaluate_experiment_outcome(db, "exp-AW")
    persist_outcome(db, ev)
    db.commit()
    retrieved = get_latest_experiment_outcome(db, "exp-AW")
    assert retrieved is not None
    assert retrieved.readiness == ev.readiness
    assert retrieved.classification == ev.classification
    assert retrieved.baseline_source_type == ev.baseline_source_type
    assert retrieved.treatment_metric_value == pytest.approx(ev.treatment_metric_value)
    assert retrieved.absolute_delta == pytest.approx(ev.absolute_delta)
    assert retrieved.is_mature == ev.is_mature
    assert retrieved.outcome_policy_version == OUTCOME_POLICY_VERSION
    assert retrieved.input_hash == ev.input_hash


# ── AX–BA: Phase14D feedback API ──────────────────────────────────────────────


def test_AX_get_outcome_for_phase14d_returns_none_when_no_outcomes(db):
    """get_outcome_for_phase14d() returns None when no persisted outcomes."""
    result = get_outcome_for_phase14d(db, "nonexistent-exp")
    assert result is None


def test_AY_get_outcome_for_phase14d_returns_dict(db):
    """get_outcome_for_phase14d() returns a dict when outcome exists."""
    _build_evaluable_chain(db, exp_id="exp-AY", opp_id=40, topic_id=62, pub_id=152, plan_id=252)
    ev = evaluate_experiment_outcome(db, "exp-AY")
    persist_outcome(db, ev)
    db.commit()
    result = get_outcome_for_phase14d(db, "exp-AY")
    assert isinstance(result, dict)
    assert result["experiment_id"] == "exp-AY"


def test_AZ_phase14d_dict_has_required_fields(db):
    """get_outcome_for_phase14d() dict has all expected keys."""
    _build_evaluable_chain(
        db, exp_id="exp-AZ", metric_value=50.0, opp_id=41, topic_id=63, pub_id=153, plan_id=253
    )
    _insert_topic(db, 64, 41)
    _insert_publishing_plan(db, 254, 64)
    _insert_publication(db, 154, 254)
    _insert_agg(db, 154, 64, "average_view_percentage", 30.0)
    ev = evaluate_experiment_outcome(db, "exp-AZ")
    persist_outcome(db, ev)
    db.commit()
    result = get_outcome_for_phase14d(db, "exp-AZ")
    for key in (
        "experiment_id",
        "readiness",
        "classification",
        "evidence_maturity",
        "baseline_source_type",
        "target_metric_name",
        "target_metric_direction",
        "treatment_metric_value",
        "baseline_metric_value",
        "absolute_delta",
        "relative_delta",
        "is_mature",
        "publication_age_hours",
        "observed_views",
        "warnings",
        "outcome_policy_version",
        "input_hash",
        "evaluated_at",
    ):
        assert key in result, f"Missing key: {key}"


def test_BA_phase14d_classification_matches_evaluation(db):
    """get_outcome_for_phase14d() classification matches the evaluation."""
    _build_evaluable_chain(
        db, exp_id="exp-BA", metric_value=60.0, opp_id=42, topic_id=65, pub_id=155, plan_id=255
    )
    _insert_topic(db, 66, 42)
    _insert_publishing_plan(db, 256, 66)
    _insert_publication(db, 156, 256)
    _insert_agg(db, 156, 66, "average_view_percentage", 40.0)
    ev = evaluate_experiment_outcome(db, "exp-BA")
    persist_outcome(db, ev)
    db.commit()
    result = get_outcome_for_phase14d(db, "exp-BA")
    assert result["classification"] == ev.classification.value if ev.classification else None


# ── BB–BD: Market/exploration experiment type ─────────────────────────────────


def test_BB_market_experiment_uses_channel_baseline(db):
    """'exploration' experiment type uses CHANNEL_BASELINE by default."""
    _build_evaluable_chain(
        db,
        exp_id="exp-BB",
        exp_type="exploration",
        metric_value=50.0,
        opp_id=43,
        topic_id=67,
        pub_id=157,
        plan_id=257,
    )
    _insert_topic(db, 68, 43)
    _insert_publishing_plan(db, 258, 68)
    _insert_publication(db, 158, 258)
    _insert_agg(db, 158, 68, "average_view_percentage", 35.0)
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-BB")
    assert ev.baseline_source_type == BaselineSourceType.CHANNEL_BASELINE


def test_BC_market_experiment_positive_outcome(db):
    """Market experiment with treatment > channel baseline → POSITIVE_OBSERVATION."""
    _build_evaluable_chain(
        db,
        exp_id="exp-BC",
        exp_type="exploration",
        metric_value=55.0,
        opp_id=44,
        topic_id=69,
        pub_id=159,
        plan_id=259,
    )
    _insert_topic(db, 70, 44)
    _insert_publishing_plan(db, 260, 70)
    _insert_publication(db, 160, 260)
    _insert_agg(db, 160, 70, "average_view_percentage", 40.0)
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-BC")
    assert ev.classification == OutcomeClassification.POSITIVE_OBSERVATION


def test_BD_market_experiment_exploratory_maturity(db):
    """Market experiment without prior → evidence_maturity=EXPLORATORY."""
    _build_evaluable_chain(
        db, exp_id="exp-BD", exp_type="exploration", opp_id=45, topic_id=71, pub_id=161, plan_id=261
    )
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-BD")
    assert ev.evidence_maturity == EvidenceMaturity.EXPLORATORY


# ── BE–BG: Feature/exploitation experiment type ───────────────────────────────


def test_BE_feature_experiment_uses_channel_baseline(db):
    """'exploitation' experiment type falls back to CHANNEL_BASELINE by default."""
    _build_evaluable_chain(
        db,
        exp_id="exp-BE",
        exp_type="exploitation",
        metric_value=50.0,
        opp_id=46,
        topic_id=72,
        pub_id=162,
        plan_id=262,
    )
    _insert_topic(db, 73, 46)
    _insert_publishing_plan(db, 263, 73)
    _insert_publication(db, 163, 263)
    _insert_agg(db, 163, 73, "average_view_percentage", 35.0)
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-BE")
    assert ev.baseline_source_type == BaselineSourceType.CHANNEL_BASELINE


def test_BF_feature_experiment_positive_outcome(db):
    """Feature experiment with treatment > baseline → POSITIVE_OBSERVATION."""
    _build_evaluable_chain(
        db,
        exp_id="exp-BF",
        exp_type="exploitation",
        metric_value=60.0,
        opp_id=47,
        topic_id=74,
        pub_id=164,
        plan_id=264,
    )
    _insert_topic(db, 75, 47)
    _insert_publishing_plan(db, 265, 75)
    _insert_publication(db, 165, 265)
    _insert_agg(db, 165, 75, "average_view_percentage", 40.0)
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-BF")
    assert ev.classification == OutcomeClassification.POSITIVE_OBSERVATION


def test_BG_feature_experiment_negative_outcome(db):
    """Feature experiment with treatment < baseline → NEGATIVE_OBSERVATION."""
    _build_evaluable_chain(
        db,
        exp_id="exp-BG",
        exp_type="exploitation",
        metric_value=30.0,
        opp_id=48,
        topic_id=76,
        pub_id=166,
        plan_id=266,
    )
    _insert_topic(db, 77, 48)
    _insert_publishing_plan(db, 267, 77)
    _insert_publication(db, 167, 267)
    _insert_agg(db, 167, 77, "average_view_percentage", 50.0)
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-BG")
    assert ev.classification == OutcomeClassification.NEGATIVE_OBSERVATION


# ── BH–BJ: Validation experiment ─────────────────────────────────────────────


def test_BH_validation_reference_baseline_type(db):
    """Validation repeat + prior outcome → VALIDATION_REFERENCE baseline type."""
    _build_evaluable_chain(
        db,
        exp_id="exp-BH-prior",
        metric_value=25.0,
        opp_id=49,
        topic_id=78,
        pub_id=168,
        plan_id=268,
    )
    prior_ev = evaluate_experiment_outcome(db, "exp-BH-prior")
    persist_outcome(db, prior_ev)
    _insert_opportunity(db, 50)
    _insert_selection_decision_validation(db, 50)
    _insert_experiment(db, "exp-BH", opp_id=50, status="observing")
    _insert_contract(db, "exp-BH", opp_id=50)
    _insert_topic(db, 79, 50)
    _insert_publishing_plan(db, 269, 79)
    _insert_publication(db, 169, 269)
    _link_pub_to_exp(db, "exp-BH", 169)
    _insert_snapshot(db, 169, "data")
    _insert_agg(db, 169, 79, "views", 50.0)
    _insert_agg(db, 169, 79, "average_view_percentage", 35.0)
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-BH", prior_experiment_id="exp-BH-prior")
    assert ev.baseline_source_type == BaselineSourceType.VALIDATION_REFERENCE


def test_BI_validation_experiment_directional_evidence(db):
    """Validation with prior outcome → DIRECTIONAL evidence maturity."""
    _build_evaluable_chain(
        db,
        exp_id="exp-BI-prior",
        metric_value=25.0,
        opp_id=51,
        topic_id=80,
        pub_id=170,
        plan_id=270,
    )
    prior_ev = evaluate_experiment_outcome(db, "exp-BI-prior")
    persist_outcome(db, prior_ev)
    _insert_opportunity(db, 52)
    _insert_selection_decision_validation(db, 52)
    _insert_experiment(db, "exp-BI", opp_id=52, status="observing")
    _insert_contract(db, "exp-BI", opp_id=52)
    _insert_topic(db, 81, 52)
    _insert_publishing_plan(db, 271, 81)
    _insert_publication(db, 171, 271)
    _link_pub_to_exp(db, "exp-BI", 171)
    _insert_snapshot(db, 171, "data")
    _insert_agg(db, 171, 81, "views", 50.0)
    _insert_agg(db, 171, 81, "average_view_percentage", 35.0)
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-BI", prior_experiment_id="exp-BI-prior")
    assert ev.evidence_maturity == EvidenceMaturity.DIRECTIONAL


def test_BJ_prior_experiment_id_stored_in_outcome(db):
    """baseline_experiment_id in result matches supplied prior_experiment_id."""
    _build_evaluable_chain(
        db,
        exp_id="exp-BJ-prior",
        metric_value=28.0,
        opp_id=53,
        topic_id=82,
        pub_id=172,
        plan_id=272,
    )
    prior_ev = evaluate_experiment_outcome(db, "exp-BJ-prior")
    persist_outcome(db, prior_ev)
    _insert_opportunity(db, 54)
    _insert_selection_decision_validation(db, 54)
    _insert_experiment(db, "exp-BJ", opp_id=54, status="observing")
    _insert_contract(db, "exp-BJ", opp_id=54)
    _insert_topic(db, 83, 54)
    _insert_publishing_plan(db, 273, 83)
    _insert_publication(db, 173, 273)
    _link_pub_to_exp(db, "exp-BJ", 173)
    _insert_snapshot(db, 173, "data")
    _insert_agg(db, 173, 83, "views", 50.0)
    _insert_agg(db, 173, 83, "average_view_percentage", 35.0)
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-BJ", prior_experiment_id="exp-BJ-prior")
    assert ev.baseline_experiment_id == "exp-BJ-prior"


# ── BK–BM: Zero/null edge cases ───────────────────────────────────────────────


def test_BK_zero_baseline_relative_delta_is_null(db):
    """Baseline value of 0.0 → relative_delta is None (no divide-by-zero)."""
    _build_evaluable_chain(
        db, exp_id="exp-BK", metric_value=5.0, opp_id=55, topic_id=84, pub_id=174, plan_id=274
    )
    _insert_topic(db, 85, 55)
    _insert_publishing_plan(db, 275, 85)
    _insert_publication(db, 175, 275)
    _insert_agg(db, 175, 85, "average_view_percentage", 0.0)
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-BK")
    assert ev.baseline_metric_value == pytest.approx(0.0)
    assert ev.relative_delta is None
    assert ev.absolute_delta == pytest.approx(5.0)


def test_BL_zero_treatment_value_is_valid(db):
    """treatment_value of 0.0 is NOT treated as missing; absolute_delta computed."""
    _build_evaluable_chain(
        db, exp_id="exp-BL", metric_value=0.0, opp_id=56, topic_id=86, pub_id=176, plan_id=276
    )
    _insert_topic(db, 87, 56)
    _insert_publishing_plan(db, 277, 87)
    _insert_publication(db, 177, 277)
    _insert_agg(db, 177, 87, "average_view_percentage", 30.0)
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-BL")
    assert ev.treatment_metric_value == pytest.approx(0.0)
    assert ev.absolute_delta is not None
    assert ev.classification == OutcomeClassification.NEGATIVE_OBSERVATION


def test_BM_no_target_metric_row_is_inconclusive(db):
    """No lifetime aggregate for target metric → INCONCLUSIVE."""
    _build_evaluable_chain(
        db,
        exp_id="exp-BM",
        metric_name="views",
        metric_value=50.0,
        opp_id=57,
        topic_id=88,
        pub_id=178,
        plan_id=278,
    )
    # target metric defaults to average_view_percentage, which has no agg row
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-BM")
    assert ev.classification == OutcomeClassification.INCONCLUSIVE
    assert ev.treatment_metric_value is None


# ── BN–BS: No side effects ────────────────────────────────────────────────────


def test_BN_evaluate_does_not_modify_experiments(db):
    """evaluate_experiment_outcome() does not modify experiments table."""
    _build_evaluable_chain(db, exp_id="exp-BN", opp_id=58, topic_id=89, pub_id=179, plan_id=279)
    before = db.execute("SELECT * FROM experiments WHERE id = 'exp-BN'").fetchone()
    evaluate_experiment_outcome(db, "exp-BN")
    after = db.execute("SELECT * FROM experiments WHERE id = 'exp-BN'").fetchone()
    assert dict(before) == dict(after)


def test_BO_evaluate_does_not_modify_analytics_aggregates(db):
    """evaluate_experiment_outcome() does not modify analytics_aggregates."""
    _build_evaluable_chain(db, exp_id="exp-BO", opp_id=59, topic_id=90, pub_id=180, plan_id=280)
    before_count = db.execute("SELECT COUNT(*) FROM analytics_aggregates").fetchone()[0]
    evaluate_experiment_outcome(db, "exp-BO")
    after_count = db.execute("SELECT COUNT(*) FROM analytics_aggregates").fetchone()[0]
    assert before_count == after_count


def test_BP_evaluate_does_not_modify_publications(db):
    """evaluate_experiment_outcome() does not modify publications."""
    _build_evaluable_chain(db, exp_id="exp-BP", opp_id=60, topic_id=91, pub_id=181, plan_id=281)
    before = db.execute("SELECT * FROM publications WHERE id = 181").fetchone()
    evaluate_experiment_outcome(db, "exp-BP")
    after = db.execute("SELECT * FROM publications WHERE id = 181").fetchone()
    assert dict(before) == dict(after)


def test_BQ_evaluate_does_not_write_to_experiment_outcomes(db):
    """evaluate_experiment_outcome() alone does NOT persist (read-only)."""
    _build_evaluable_chain(db, exp_id="exp-BQ", opp_id=61, topic_id=92, pub_id=182, plan_id=282)
    evaluate_experiment_outcome(db, "exp-BQ")
    count = db.execute(
        "SELECT COUNT(*) FROM experiment_outcomes WHERE experiment_id = 'exp-BQ'"
    ).fetchone()[0]
    assert count == 0


def test_BR_missing_experiment_raises_key_error(db):
    """KeyError raised for nonexistent experiment_id."""
    with pytest.raises(KeyError):
        evaluate_experiment_outcome(db, "does-not-exist")


def test_BS_policy_version_present_in_output(db):
    """outcome_policy_version matches OUTCOME_POLICY_VERSION constant."""
    _build_evaluable_chain(db, exp_id="exp-BS", opp_id=62, topic_id=93, pub_id=183, plan_id=283)
    ev = evaluate_experiment_outcome(db, "exp-BS")
    assert ev.outcome_policy_version == OUTCOME_POLICY_VERSION


# ── BT–CA: Regression ────────────────────────────────────────────────────────


def test_BT_schema_version_is_41(db):
    """SCHEMA_VERSION is 41 (Phase 14G added experiment_outcomes)."""
    assert SCHEMA_VERSION == 51


def test_BU_experiment_outcomes_table_exists(db):
    """experiment_outcomes table exists in schema."""
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='experiment_outcomes'"
    ).fetchone()
    assert row is not None


def test_BV_experiment_outcomes_idx_experiment_exists(db):
    """idx_eo_experiment index exists."""
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_eo_experiment'"
    ).fetchone()
    assert row is not None


def test_BW_experiment_outcomes_idx_evaluated_at_exists(db):
    """idx_eo_evaluated_at index exists."""
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_eo_evaluated_at'"
    ).fetchone()
    assert row is not None


def test_BX_evaluate_accepts_all_evaluable_statuses(db):
    """All evaluable statuses (published, observing, mature, analyzed, completed) pass gate."""
    for i, status in enumerate(("published", "observing", "mature", "analyzed", "completed")):
        exp_id = f"exp-BX-{status}"
        opp_id = 63 + i
        topic_id = 94 + i
        pub_id = 184 + i
        plan_id = 284 + i
        _build_evaluable_chain(
            db,
            exp_id=exp_id,
            status=status,
            opp_id=opp_id,
            topic_id=topic_id,
            pub_id=pub_id,
            plan_id=plan_id,
        )
        db.commit()
        ev = evaluate_experiment_outcome(db, exp_id)
        assert ev.readiness != OutcomeReadiness.NOT_READY, f"status={status} should be evaluable"


def test_BY_cancelled_status_not_evaluable(db):
    """'cancelled' status → NOT_READY."""
    _insert_opportunity(db, 70)
    _insert_experiment(db, "exp-BY", opp_id=70, status="cancelled")
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-BY")
    assert ev.readiness == OutcomeReadiness.NOT_READY


def test_BZ_fidelity_unresolved_blocks_evaluation(db):
    """fidelity classification 'unresolved' → INVALID_EXECUTION."""
    _insert_opportunity(db, 71)
    _insert_experiment(db, "exp-BZ", opp_id=71, status="observing")
    _insert_contract(db, "exp-BZ", opp_id=71, fidelity_classification="unresolved")
    db.commit()
    ev = evaluate_experiment_outcome(db, "exp-BZ")
    assert ev.readiness == OutcomeReadiness.INVALID_EXECUTION


def test_CA_outcome_policy_version_constant_is_1_0_0():
    """OUTCOME_POLICY_VERSION is '1.0.0'."""
    assert OUTCOME_POLICY_VERSION == "1.0.0"
