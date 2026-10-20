"""Phase 18H.A — Outcome-aware repetition guard, including the recent-repeat refinement.

Groups:
  A–H       — assess_prior_cluster_outcome() unit behavior (stability, cooldown,
              multiple-prior-experiment selection, UNAVAILABLE/INCONCLUSIVE cases)
  I–L       — _score_candidate() cluster_coverage_need penalty integration
              (stable-negative only; INCONCLUSIVE/UNAVAILABLE never get a score
              penalty, only the selection-gate block below)
  M–R       — _select_portfolio() / build_portfolio_plan() selection-gate
              integration: stable-negative block (M, N, R), stable-positive
              pass-through (O), recent-repeat refinement — INCONCLUSIVE
              recent/expired (P, P2), UNAVAILABLE recent/never-tested (Q, Q2)
  S         — exact Sept 1 CRISPR retest replay under the recent-repeat
              refinement (Phase 18H.A post-implementation audit regression)

Safety invariants (Phase 18H.A):
- No YouTube API calls
- No LLM calls
- No content generation
- No Experiment row creation via build_portfolio_plan (planning artifacts only)
- No modification to the live database — every test uses an isolated tmp_path DB
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from app.core.database import open_db
from app.intelligence.experiments.prior_outcome import (
    NEGATIVE_REPEAT_COVERAGE_PENALTY_WEIGHT,
    STABILITY_MIN_AGREEING_OBSERVATIONS,
    PriorOutcomeAssessment,
    assess_prior_cluster_outcome,
)

# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = open_db(tmp_path / "test_18ha.db")
    conn.execute("PRAGMA foreign_keys = OFF")
    yield conn
    conn.close()


# ── Helpers ──────────────────────────────────────────────────────────────────


def _insert_channel(conn, channel_id: int = 1) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO channels (id, platform, channel_name, platform_channel_id)"
        " VALUES (?, 'youtube', 'Test', ?)",
        (channel_id, f"UC{channel_id}h"),
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
            0.20,
            0.20,
            f"hash_{opp_id}",
            "1.0",
            "2026-08-21T12:00:00",
        ),
    )
    conn.commit()


def _insert_completed_experiment(
    conn,
    exp_id: str,
    opp_id: int,
    channel_id: int = 1,
    completed_at: str = "2026-08-30T11:00:00",
    matured_at: str | None = None,
    maturity_policy_json: str = "{}",
) -> str:
    conn.execute(
        """INSERT INTO experiments
           (id, channel_id, opportunity_id, experiment_type, status, hypothesis,
            input_hash, maturity_policy_json, completed_at, matured_at)
           VALUES (?, ?, ?, 'exploration', 'completed', 'prior hypothesis', ?, ?, ?, ?)""",
        (
            exp_id,
            channel_id,
            opp_id,
            f"hash-{exp_id}",
            maturity_policy_json,
            completed_at,
            matured_at or completed_at,
        ),
    )
    conn.commit()
    return exp_id


def _insert_outcome(
    conn,
    experiment_id: str,
    classification: str | None,
    *,
    is_mature: bool,
    evaluated_at: str,
    readiness: str = "evaluable_mature",
) -> None:
    conn.execute(
        """INSERT INTO experiment_outcomes
           (id, experiment_id, readiness, classification, baseline_source_type,
            is_mature, reasons_json, warnings_json, outcome_policy_version,
            input_hash, evaluated_at)
           VALUES (?, ?, ?, ?, 'channel_baseline', ?, '[]', '[]', '1.0.0', ?, ?)""",
        (
            str(uuid.uuid4()),
            experiment_id,
            readiness,
            classification,
            1 if is_mature else 0,
            f"ih-{uuid.uuid4()}",
            evaluated_at,
        ),
    )
    conn.commit()


# ── A–H: assess_prior_cluster_outcome() unit behavior ───────────────────────


def test_A_unavailable_when_no_completed_experiment(db):
    """A: no completed experiment in the cluster at all → UNAVAILABLE."""
    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=5)
    result = assess_prior_cluster_outcome(db, canonical_cluster_id=5, channel_id=1)
    assert result.assessment == PriorOutcomeAssessment.UNAVAILABLE
    assert result.prior_experiment_id is None


def test_B_unavailable_when_completed_but_no_mature_directional_reading(db):
    """B: experiment completed but every outcome row is baseline_unavailable → UNAVAILABLE."""
    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=5)
    exp_id = _insert_completed_experiment(db, "exp-B", 1)
    _insert_outcome(
        db, exp_id, "baseline_unavailable", is_mature=True, evaluated_at="2026-08-29T23:00:00"
    )
    result = assess_prior_cluster_outcome(db, canonical_cluster_id=5, channel_id=1)
    assert result.assessment == PriorOutcomeAssessment.UNAVAILABLE
    assert result.prior_experiment_id == exp_id


def test_C_single_mature_reading_is_inconclusive_not_positive_or_negative(db):
    """C: exactly one mature directional reading → INCONCLUSIVE (N=1 is not stability).

    This is the load-bearing case: the real Sept 1 CRISPR retest was authorized
    with exactly one mature positive_observation on record. A naive "use the
    latest label" rule would have called that POSITIVE (or, after the label
    flipped the next day, NEGATIVE, depending on when you asked). Neither is
    correct — the evidence was thin, and INCONCLUSIVE is the honest answer.
    """
    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=5)
    exp_id = _insert_completed_experiment(db, "exp-C", 1)
    _insert_outcome(
        db, exp_id, "positive_observation", is_mature=True, evaluated_at="2026-09-01T12:01:34"
    )
    result = assess_prior_cluster_outcome(db, canonical_cluster_id=5, channel_id=1)
    assert result.assessment == PriorOutcomeAssessment.INCONCLUSIVE
    assert result.usable_mature_observation_count == 1
    assert result.latest_classification == "positive_observation"
    assert not result.guard_active


def test_D_two_agreeing_negative_readings_is_stable_negative(db):
    """D: two consecutive mature negative_observation readings → NEGATIVE, guard active."""
    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=5)
    exp_id = _insert_completed_experiment(
        db, "exp-D", 1, completed_at="2026-08-30T11:00:00", matured_at="2026-08-30T11:00:00"
    )
    _insert_outcome(
        db, exp_id, "negative_observation", is_mature=True, evaluated_at="2026-09-02T00:02:04"
    )
    _insert_outcome(
        db, exp_id, "negative_observation", is_mature=True, evaluated_at="2026-09-03T00:02:41"
    )
    result = assess_prior_cluster_outcome(
        db, canonical_cluster_id=5, channel_id=1, as_of="2026-09-03T12:00:00"
    )
    assert result.assessment == PriorOutcomeAssessment.NEGATIVE
    assert result.guard_active is True
    assert result.usable_mature_observation_count == 2


def test_E_two_agreeing_positive_readings_is_stable_positive(db):
    """E: two consecutive mature positive_observation readings → POSITIVE, no guard."""
    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=5)
    exp_id = _insert_completed_experiment(db, "exp-E", 1)
    _insert_outcome(
        db, exp_id, "positive_observation", is_mature=True, evaluated_at="2026-08-19T00:00:00"
    )
    _insert_outcome(
        db, exp_id, "positive_observation", is_mature=True, evaluated_at="2026-08-26T00:00:00"
    )
    result = assess_prior_cluster_outcome(db, canonical_cluster_id=5, channel_id=1)
    assert result.assessment == PriorOutcomeAssessment.POSITIVE
    assert result.guard_active is False  # guard is only ever active for NEGATIVE


def test_F_disagreeing_readings_is_inconclusive_not_negative(db):
    """F: latest two mature readings disagree → INCONCLUSIVE, never falls back to 'latest label'."""
    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=5)
    exp_id = _insert_completed_experiment(db, "exp-F", 1)
    _insert_outcome(
        db, exp_id, "positive_observation", is_mature=True, evaluated_at="2026-09-01T12:01:34"
    )
    _insert_outcome(
        db, exp_id, "negative_observation", is_mature=True, evaluated_at="2026-09-02T00:02:04"
    )
    result = assess_prior_cluster_outcome(db, canonical_cluster_id=5, channel_id=1)
    assert result.assessment == PriorOutcomeAssessment.INCONCLUSIVE
    assert "disagree" in result.reason


def test_G_cooldown_expires_and_lifts_the_guard(db):
    """G: a stable negative outcome stops gating once its cooldown window elapses.

    Cooldown length is read from the blocking experiment's own
    MaturityPolicy.observation_window_hours (default 168h/7 days) — no new
    constant. matured_at anchors the window.
    """
    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=5)
    exp_id = _insert_completed_experiment(
        db,
        "exp-G",
        1,
        completed_at="2026-08-01T00:00:00",
        matured_at="2026-08-01T00:00:00",
        maturity_policy_json='{"observation_window_hours": 168}',
    )
    _insert_outcome(
        db, exp_id, "negative_observation", is_mature=True, evaluated_at="2026-08-01T01:00:00"
    )
    _insert_outcome(
        db, exp_id, "negative_observation", is_mature=True, evaluated_at="2026-08-02T00:00:00"
    )

    still_cooling = assess_prior_cluster_outcome(
        db, canonical_cluster_id=5, channel_id=1, as_of="2026-08-03T00:00:00"
    )
    assert still_cooling.assessment == PriorOutcomeAssessment.NEGATIVE
    assert still_cooling.guard_active is True

    expired = assess_prior_cluster_outcome(
        db,
        canonical_cluster_id=5,
        channel_id=1,
        as_of="2026-08-10T00:00:00",  # > 168h later
    )
    assert expired.assessment == PriorOutcomeAssessment.NEGATIVE
    assert expired.guard_active is False


def test_H_multiple_prior_experiments_uses_most_recent_completed_only(db):
    """H: with >1 completed experiment on the same opportunity/cluster (the real
    CRISPR shape: opportunities map 1:1 to a canonical cluster, so "multiple
    prior experiments in a cluster" means multiple experiments run over time on
    the same opportunity — exactly what happened with opportunity 4), only the
    most recent one's outcome history is consulted. A stale negative from an
    older experiment does not override a newer, stably positive one."""
    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=5)

    old_exp = _insert_completed_experiment(db, "exp-H-old", 1, completed_at="2026-08-01T00:00:00")
    _insert_outcome(
        db, old_exp, "negative_observation", is_mature=True, evaluated_at="2026-08-02T00:00:00"
    )
    _insert_outcome(
        db, old_exp, "negative_observation", is_mature=True, evaluated_at="2026-08-03T00:00:00"
    )

    new_exp = _insert_completed_experiment(db, "exp-H-new", 1, completed_at="2026-08-20T00:00:00")
    _insert_outcome(
        db, new_exp, "positive_observation", is_mature=True, evaluated_at="2026-08-21T00:00:00"
    )
    _insert_outcome(
        db, new_exp, "positive_observation", is_mature=True, evaluated_at="2026-08-22T00:00:00"
    )

    result = assess_prior_cluster_outcome(db, canonical_cluster_id=5, channel_id=1)
    assert result.prior_experiment_id == new_exp
    assert result.assessment == PriorOutcomeAssessment.POSITIVE


# ── I–L: _score_candidate() penalty integration ─────────────────────────────


def test_I_no_penalty_when_prior_outcome_is_none():
    """I: omitting prior_outcome entirely leaves cluster_coverage_need unchanged
    (backward compatibility with every pre-18H.A caller/test)."""
    from app.intelligence.experiments.planning import PlanningIntent, PlanningPolicy
    from app.intelligence.experiments.planning_service import _score_candidate

    assessment_score = _score_candidate(
        assessment=_bare_assessment(1),
        cluster_id=42,
        existing_cluster_counts={42: 1},
        feature_coverage={},
        policy=PlanningPolicy.v1(),
        planning_intent=PlanningIntent.EXPLORATION,
        composite_score=0.60,
    )
    assert abs(assessment_score.cluster_coverage_need - 0.5) < 1e-9  # 1/(1+1), unchanged


def test_J_no_penalty_for_positive_or_inconclusive_prior(db):
    """J: POSITIVE and INCONCLUSIVE prior outcomes get ordinary coverage decay only."""
    from app.intelligence.experiments.planning import PlanningIntent, PlanningPolicy
    from app.intelligence.experiments.planning_service import _score_candidate
    from app.intelligence.experiments.prior_outcome import PriorClusterOutcomeResult

    for assessment in (PriorOutcomeAssessment.POSITIVE, PriorOutcomeAssessment.INCONCLUSIVE):
        prior = PriorClusterOutcomeResult(assessment=assessment, reason="test")
        score = _score_candidate(
            assessment=_bare_assessment(1),
            cluster_id=42,
            existing_cluster_counts={42: 1},
            feature_coverage={},
            policy=PlanningPolicy.v1(),
            planning_intent=PlanningIntent.EXPLORATION,
            composite_score=0.60,
            prior_outcome=prior,
        )
        assert abs(score.cluster_coverage_need - 0.5) < 1e-9


def test_K_penalty_applied_for_stable_negative_within_cooldown():
    """K: a NEGATIVE, guard_active prior outcome extends the effective cluster count."""
    from app.intelligence.experiments.planning import PlanningIntent, PlanningPolicy
    from app.intelligence.experiments.planning_service import _score_candidate
    from app.intelligence.experiments.prior_outcome import PriorClusterOutcomeResult

    prior = PriorClusterOutcomeResult(
        assessment=PriorOutcomeAssessment.NEGATIVE, reason="test", guard_active=True
    )
    score = _score_candidate(
        assessment=_bare_assessment(1),
        cluster_id=42,
        existing_cluster_counts={42: 1},
        feature_coverage={},
        policy=PlanningPolicy.v1(),
        planning_intent=PlanningIntent.EXPLORATION,
        composite_score=0.60,
        prior_outcome=prior,
    )
    expected = 1.0 / (1 + 1 + NEGATIVE_REPEAT_COVERAGE_PENALTY_WEIGHT)  # 1/3
    assert abs(score.cluster_coverage_need - expected) < 1e-9


def test_L_no_penalty_for_negative_outside_cooldown():
    """L: NEGATIVE but guard_active=False (cooldown expired) → ordinary decay only."""
    from app.intelligence.experiments.planning import PlanningIntent, PlanningPolicy
    from app.intelligence.experiments.planning_service import _score_candidate
    from app.intelligence.experiments.prior_outcome import PriorClusterOutcomeResult

    prior = PriorClusterOutcomeResult(
        assessment=PriorOutcomeAssessment.NEGATIVE, reason="test", guard_active=False
    )
    score = _score_candidate(
        assessment=_bare_assessment(1),
        cluster_id=42,
        existing_cluster_counts={42: 1},
        feature_coverage={},
        policy=PlanningPolicy.v1(),
        planning_intent=PlanningIntent.EXPLORATION,
        composite_score=0.60,
        prior_outcome=prior,
    )
    assert abs(score.cluster_coverage_need - 0.5) < 1e-9


def _bare_assessment(opportunity_id: int):
    from app.intelligence.experiments.eligibility import (
        ExperimentEligibilityAssessment,
        ExperimentEligibilityClassification,
    )

    return ExperimentEligibilityAssessment(
        opportunity_id=opportunity_id,
        channel_id=1,
        classification=ExperimentEligibilityClassification.GENERAL_ELIGIBLE,
        findings=[],
        policy_snapshot_json="{}",
        assessed_at="2026-08-21T12:00:00",
        signal_maturity="directional",
        signal_confidence=0.70,
        has_active_conflict=False,
    )


# ── M–R: build_portfolio_plan() selection-gate integration ─────────────────


def test_M_stable_negative_repeat_is_not_selected(db):
    """M: a validation repeat over a stable negative prior is excluded from selection."""
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=5)
    _insert_opportunity_score(db, 1, composite=0.90)  # deliberately high — still must not win
    exp_id = _insert_completed_experiment(db, "exp-M", 1)
    _insert_outcome(
        db, exp_id, "negative_observation", is_mature=True, evaluated_at="2026-08-30T12:00:00"
    )
    _insert_outcome(
        db, exp_id, "negative_observation", is_mature=True, evaluated_at="2026-08-31T12:00:00"
    )

    # Phase 7B.1, §10 — fixed as_of shortly after the second reading, well
    # within the 168h default cooldown from the prior experiment's
    # 2026-08-30T11:00:00 maturity; without this the test drifted false as
    # real wall-clock time passed the cooldown window.
    plan = build_portfolio_plan(
        db, 1, [_bare_assessment(1)], dry_run=True, as_of="2026-08-31T13:00:00"
    )
    assert plan.selected_count == 0
    assert plan.deferred_count == 1
    decision = plan.decisions[0]
    assert decision.selected is False
    assert decision.deferral_reason == "prior_negative_outcome_requires_override"
    assert "negative" in decision.selection_reason
    assert exp_id in decision.selection_reason


def test_N_candidate_is_not_silently_dropped(db):
    """N: the blocked candidate still appears in plan.decisions with a full audit trail."""
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=5)
    exp_id = _insert_completed_experiment(db, "exp-N", 1)
    _insert_outcome(
        db, exp_id, "negative_observation", is_mature=True, evaluated_at="2026-08-30T12:00:00"
    )
    _insert_outcome(
        db, exp_id, "negative_observation", is_mature=True, evaluated_at="2026-08-31T12:00:00"
    )

    plan = build_portfolio_plan(db, 1, [_bare_assessment(1)], dry_run=True)
    assert plan.eligible_count == 1
    assert len(plan.decisions) == 1
    assert plan.decisions[0].opportunity_id == 1
    assert plan.decisions[0].is_validation_repeat is True


def test_O_stable_positive_repeat_still_gets_selected(db):
    """O: a validation repeat over a stable POSITIVE prior is not penalized or blocked."""
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=5)
    _insert_opportunity_score(db, 1, composite=0.70)
    exp_id = _insert_completed_experiment(db, "exp-O", 1)
    _insert_outcome(
        db, exp_id, "positive_observation", is_mature=True, evaluated_at="2026-08-30T12:00:00"
    )
    _insert_outcome(
        db, exp_id, "positive_observation", is_mature=True, evaluated_at="2026-08-31T12:00:00"
    )

    plan = build_portfolio_plan(db, 1, [_bare_assessment(1)], dry_run=True)
    assert plan.selected_count == 1
    decision = plan.decisions[0]
    assert decision.selected is True
    assert "prior_outcome=positive" in decision.selection_reason


def test_P_inconclusive_recent_repeat_is_blocked(db):
    """P (recent-repeat refinement): a validation repeat with only one mature
    reading (INCONCLUSIVE), still within the prior experiment's cooldown
    window, is now deferred rather than automatically repeated — this is
    exactly the gap that let the real Sept 1 CRISPR retest through under
    plain 18H.A (see test_crispr_replay below)."""
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=5)
    _insert_opportunity_score(db, 1, composite=0.70)
    exp_id = _insert_completed_experiment(
        db,
        "exp-P",
        1,
        completed_at="2026-08-30T11:00:00",
        matured_at="2026-08-30T11:00:00",
    )
    _insert_outcome(
        db, exp_id, "positive_observation", is_mature=True, evaluated_at="2026-08-30T12:00:00"
    )

    # Phase 7B.1, §10 — fixed as_of shortly after the single reading, well
    # within the 168h default cooldown from matured_at=2026-08-30T11:00:00.
    plan = build_portfolio_plan(
        db, 1, [_bare_assessment(1)], dry_run=True, as_of="2026-08-30T13:00:00"
    )
    assert plan.selected_count == 0
    decision = plan.decisions[0]
    assert decision.selected is False
    assert decision.deferral_reason == "recent_inconclusive_repeat_requires_wait"
    assert "inconclusive" in decision.selection_reason
    assert exp_id in decision.selection_reason


def test_P2_inconclusive_expired_repeat_is_selectable_again(db):
    """P2 (recent-repeat refinement, Part 6): the same single-reading INCONCLUSIVE
    case, but the prior experiment's cooldown window has long since elapsed —
    ordinary repeat behavior resumes, with the prior outcome still recorded
    in the selection reason for auditability."""
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=5)
    _insert_opportunity_score(db, 1, composite=0.70)
    exp_id = _insert_completed_experiment(
        db,
        "exp-P2",
        1,
        completed_at="2020-01-01T00:00:00",
        matured_at="2020-01-01T00:00:00",
    )
    _insert_outcome(
        db, exp_id, "positive_observation", is_mature=True, evaluated_at="2020-01-01T01:00:00"
    )

    plan = build_portfolio_plan(db, 1, [_bare_assessment(1)], dry_run=True)
    assert plan.selected_count == 1
    decision = plan.decisions[0]
    assert decision.selected is True
    assert decision.deferral_reason is None
    assert "prior_outcome=inconclusive" in decision.selection_reason


def test_Q_unavailable_recent_repeat_is_blocked(db):
    """Q (recent-repeat refinement, Part 7): a completed prior experiment with no
    usable outcome rows at all, still within its cooldown window, is treated
    the same as a recent inconclusive repeat — not meaningfully different
    from "we just tested this and don't know how it went yet.\""""
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=5)
    _insert_opportunity_score(db, 1, composite=0.70)
    exp_id = _insert_completed_experiment(
        db,
        "exp-Q",
        1,
        completed_at="2026-08-30T11:00:00",
        matured_at="2026-08-30T11:00:00",
    )  # no outcome rows at all

    # Phase 7B.1, §10 — fixed as_of shortly after maturity, well within the
    # 168h default cooldown from matured_at=2026-08-30T11:00:00.
    plan = build_portfolio_plan(
        db, 1, [_bare_assessment(1)], dry_run=True, as_of="2026-08-30T13:00:00"
    )
    assert plan.selected_count == 0
    decision = plan.decisions[0]
    assert decision.selected is False
    assert decision.deferral_reason == "recent_unobserved_repeat_requires_wait"
    assert exp_id in decision.selection_reason


def test_Q2_unavailable_never_tested_behaves_exactly_like_before_18ha(db):
    """Q2 (Part 8): no prior completed experiment in the cluster at all — a
    genuinely untested cluster — is unaffected by any repeat guard; ordinary
    first-exposure behavior, exactly as before 18H.A."""
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=5)
    _insert_opportunity_score(db, 1, composite=0.70)
    # No _insert_completed_experiment() call at all — cluster has never been tested.

    plan = build_portfolio_plan(db, 1, [_bare_assessment(1)], dry_run=True)
    assert plan.selected_count == 1
    assert plan.decisions[0].is_validation_repeat is False


def test_R_no_experiment_rows_created_by_the_guard(db):
    """R: the guard is purely advisory scoring/selection logic — it creates no rows
    in `experiments`, and build_portfolio_plan's own no-experiment-creation
    contract still holds with the guard active."""
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db)
    _insert_opportunity(db, 1, cluster_id=5)
    exp_id = _insert_completed_experiment(db, "exp-R", 1)
    _insert_outcome(
        db, exp_id, "negative_observation", is_mature=True, evaluated_at="2026-08-30T12:00:00"
    )
    _insert_outcome(
        db, exp_id, "negative_observation", is_mature=True, evaluated_at="2026-08-31T12:00:00"
    )

    before = db.execute("SELECT COUNT(*) FROM experiments").fetchone()[0]
    build_portfolio_plan(db, 1, [_bare_assessment(1)], dry_run=False)
    after = db.execute("SELECT COUNT(*) FROM experiments").fetchone()[0]
    assert after == before == 1  # only the prior experiment we inserted ourselves


# ── S: exact Sept 1 CRISPR retest replay ────────────────────────────────────


def test_S_exact_crispr_replay_sept1_decision(db):
    """S: reproduce the real Sept 1 2026 CRISPR retest decision from the Phase 18H
    audit under the recent-repeat refinement.

    Reconstructed ground truth (read-only DB audit):
    - opportunity 4 / canonical_cluster_id 4, "crispr gene editing technology"
    - prior experiment d0e57f27 (exploration, completed 2026-08-30T11:04:46)
    - its experiment_outcomes history, in order:
        2026-08-29T23:04:02  baseline_unavailable  (is_mature=1)
        2026-08-30T11:04:46  baseline_unavailable  (is_mature=1)
        2026-08-30T23:18:43  baseline_unavailable  (is_mature=1)
        2026-08-31T11:46:58  baseline_unavailable  (is_mature=1)
        2026-08-31T23:58:49  baseline_unavailable  (is_mature=1)
        2026-09-01T12:01:34  positive_observation  (is_mature=1)   <- last reading
                                                                       before the decision
        [ everything below happened AFTER the real 2026-09-01T14:04:53 decision —
          not inserted here, exactly as it was genuinely unknown to the planner
          at decision time — see the follow-up cycle at the end of this test ]
        2026-09-02T00:02:04  negative_observation
        2026-09-03T00:02:41  negative_observation
        2026-09-04T00:03:42  negative_observation
    - the other three approved opportunities were all blocked by in-flight
      experiments at decision time, so this replay (correctly) only supplies
      opportunity 4 as a candidate — eligible_count=1 in both the real run and
      here.

    At decision time the prior assessment is INCONCLUSIVE (one mature reading,
    need two to call it stable) and — new in the recent-repeat refinement —
    still within the prior experiment's cooldown window (168h from its
    2026-08-29T23:03:40 maturity; the decision is ~38h later). Under plain
    18H.A this proceeded unblocked (see the Phase 18H.A post-implementation
    audit); under the refinement it is now deferred instead of automatically
    repeated — closing exactly the gap that audit identified.
    """
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    _insert_channel(db, channel_id=1)
    _insert_opportunity(db, 4, channel_id=1, topic="crispr gene editing technology", cluster_id=4)
    _insert_opportunity_score(db, 4, composite=0.483647)

    exp_id = _insert_completed_experiment(
        db,
        "d0e57f27-c6aa-4f21-aeb9-ceac2500117e",
        4,
        channel_id=1,
        completed_at="2026-08-30T11:04:46",
        matured_at="2026-08-29T23:03:40",
        maturity_policy_json=(
            '{"minimum_age_hours": 48, "minimum_views": 10, '
            '"observation_window_hours": 168, "required_metrics": []}'
        ),
    )
    for ts in (
        "2026-08-29T23:04:02",
        "2026-08-30T11:04:46",
        "2026-08-30T23:18:43",
        "2026-08-31T11:46:58",
        "2026-08-31T23:58:49",
    ):
        _insert_outcome(db, exp_id, "baseline_unavailable", is_mature=True, evaluated_at=ts)
    _insert_outcome(
        db, exp_id, "positive_observation", is_mature=True, evaluated_at="2026-09-01T12:01:34"
    )
    # Deliberately NOT yet inserting the 2026-09-02..04 negative_observation rows
    # from the real history below — at the real decision instant (2026-09-01
    # 14:04:53) those readings did not exist yet. Inserting them now and relying
    # on `as_of` to hide them would only prove the as_of parameter works (already
    # covered by test_G); the DB state a live build_portfolio_plan call sees is
    # always "everything persisted so far", so the faithful replay is to only
    # have the rows that genuinely existed at this point in history.

    # Direct unit check of the assessment as of the real decision instant.
    at_decision = assess_prior_cluster_outcome(
        db, canonical_cluster_id=4, channel_id=1, as_of="2026-09-01T14:04:53"
    )
    assert at_decision.assessment == PriorOutcomeAssessment.INCONCLUSIVE
    assert at_decision.usable_mature_observation_count == 1
    assert at_decision.latest_classification == "positive_observation"
    assert at_decision.cooldown_active is True
    assert STABILITY_MIN_AGREEING_OBSERVATIONS == 2  # documents why 1 reading wasn't enough

    # Full planning replay: opportunity 4 is the only candidate, exactly as in
    # the real eligible_count=1 decision (the other three opportunities were
    # excluded from this assessment's inputs because they were blocked by
    # in-flight experiments — that gate is unchanged by Phase 18H.A and is
    # exercised by the existing eligibility_service tests, not here).
    plan = build_portfolio_plan(
        db, 1, [_bare_assessment(4)], dry_run=True, as_of="2026-09-01T14:04:53"
    )
    assert plan.eligible_count == 1
    assert plan.selected_count == 0
    decision = plan.decisions[0]
    assert decision.selected is False
    assert decision.is_validation_repeat is True
    assert decision.deferral_reason == "recent_inconclusive_repeat_requires_wait"
    assert "inconclusive" in decision.selection_reason
    assert exp_id in decision.selection_reason

    # plan.selected_count == 0 is exactly the condition
    # app.intelligence.autonomy.decision_cycle._run_locked_cycle checks
    # (`selected = next((d for d in plan.decisions if d.selected), None)`,
    # decision_cycle.py:292) to set DecisionOutcome.NO_ELIGIBLE_CANDIDATE — the
    # decision-cycle layer is not re-exercised here (it needs its own
    # channel/workspace/policy fixtures), but
    # tests/test_autonomy_decision_cycle_18a.py::
    # test_restart_recovery_resumes_in_flight_slot_not_a_new_one already proves
    # that a NO_ELIGIBLE_CANDIDATE cycle leaves its reserved slot in-flight and
    # a later cycle resumes — not re-reserves — that same slot once selection
    # succeeds. Nothing in this refinement touches that path.

    # Self-correction check: once the real world's subsequent negative readings
    # are recorded (as they were, in reality, on 09-02/09-03/09-04), a *later*
    # planning cycle on this same cluster — not a mutation of the decision
    # already made — would now see a stable negative and be blocked for the
    # original (stronger) reason instead. This demonstrates the guard reacts to
    # new evidence going forward; it does not retroactively change history.
    for ts in ("2026-09-02T00:02:04", "2026-09-03T00:02:41", "2026-09-04T00:03:42"):
        _insert_outcome(db, exp_id, "negative_observation", is_mature=True, evaluated_at=ts)
    # Fixed as_of shortly after the last negative reading, still well
    # within the 168h cooldown from matured_at=2026-08-29T23:03:40 (expires
    # 2026-09-05T23:03:40) — represents "a later cycle the same week",
    # exactly the fixture's own stated intent above.
    later_plan = build_portfolio_plan(
        db, 1, [_bare_assessment(4)], dry_run=True, as_of="2026-09-04T02:00:00"
    )
    later_decision = later_plan.decisions[0]
    assert later_decision.selected is False
    assert later_decision.deferral_reason == "prior_negative_outcome_requires_override"
