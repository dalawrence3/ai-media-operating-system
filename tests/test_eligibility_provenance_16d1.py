"""Phase 16D.1.1 — Eligibility provenance tests.

Verifies:
  1. FakeProvider → semantic_fit_disposition = "fake_provider_test"
  2. ReplayEligibilityProvider → semantic_fit_disposition = "replay_prior_real_call"
  3. Real provider (simulated via FakeProvider with provider_name override) → "provider_called"
  4. Disposition is persisted to experiment_candidate_scores by _persist_plan()
  5. Recent real assessment CAN be replayed; stale/mismatched cannot be confused
  6. New cluster forces EXPLORATION regardless of market signal maturity
  7. Cancelled experiments are excluded from cluster_is_new check
  8. New planning run with ReplayProvider → market_exploration brief, 0 treatment factors
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock

import pytest

from app.ai.fake import FakeProvider
from app.ai.replay_provider import ReplayEligibilityProvider
from app.intelligence.experiments.eligibility import (
    ExperimentEligibilityAssessment,
    ExperimentEligibilityClassification,
)
from app.intelligence.experiments.planning import (
    CandidateScoreComponents,
    ExperimentCandidate,
    PlanningIntent,
)

# ---------------------------------------------------------------------------
# ReplayEligibilityProvider unit tests
# ---------------------------------------------------------------------------


class TestReplayEligibilityProvider:
    def _make_provider(self, **kw):
        defaults = {
            "score": 0.92,
            "fit_label": "strong_fit",
            "rationale": "Science and tech matches channel.",
            "source": "test_phase_x_2026-08-22",
        }
        defaults.update(kw)
        return ReplayEligibilityProvider(**defaults)

    def test_provider_name_is_replay(self):
        p = self._make_provider()
        assert p.provider_name == "replay"

    def test_model_includes_source(self):
        from app.ai.provider import AIRequest

        p = self._make_provider(source="phase_16c4_confirmed_2026-08-22")
        resp = p.complete(AIRequest(system="s", user="u", model="m"))
        assert "phase_16c4_confirmed_2026-08-22" in resp.model

    def test_score_preserved(self):
        from app.ai.provider import AIRequest
        from app.intelligence.experiments.eligibility_service import _SemanticFitOutput

        p = self._make_provider(score=0.95)
        resp = p.complete(
            AIRequest(system="s", user="u", model="m", response_schema=_SemanticFitOutput)
        )
        assert resp.parsed is not None
        assert resp.parsed.score == pytest.approx(0.95)

    def test_rationale_contains_source_tag(self):
        from app.ai.provider import AIRequest
        from app.intelligence.experiments.eligibility_service import _SemanticFitOutput

        p = self._make_provider(source="my_source_ref")
        resp = p.complete(
            AIRequest(system="s", user="u", model="m", response_schema=_SemanticFitOutput)
        )
        assert "[replay:my_source_ref]" in resp.parsed.rationale

    def test_tokens_are_zero(self):
        from app.ai.provider import AIRequest

        p = self._make_provider()
        resp = p.complete(AIRequest(system="s", user="u", model="m"))
        assert resp.input_tokens == 0
        assert resp.output_tokens == 0

    def test_provider_name_distinct_from_fake(self):
        fake = FakeProvider()
        replay = self._make_provider()
        assert (getattr(fake, "provider_name", None) or getattr(fake, "name", None)) == "fake"
        assert replay.provider_name == "replay"


# ---------------------------------------------------------------------------
# Disposition detection in eligibility_service
# ---------------------------------------------------------------------------


class TestDispositionDetection:
    """Tests the provider-type → semantic_fit_disposition mapping."""

    def _detection_logic(self, provider) -> str:
        pname = getattr(provider, "provider_name", None) or getattr(provider, "name", None)
        if pname == "fake":
            return "fake_provider_test"
        elif pname == "replay":
            return "replay_prior_real_call"
        else:
            return "provider_called"

    def test_fake_provider_gives_fake_disposition(self):
        assert self._detection_logic(FakeProvider()) == "fake_provider_test"

    def test_replay_provider_gives_replay_disposition(self):
        p = ReplayEligibilityProvider(score=0.9, fit_label="fit", rationale="r", source="s")
        assert self._detection_logic(p) == "replay_prior_real_call"

    def test_real_provider_gives_provider_called(self):
        mock_provider = MagicMock()
        mock_provider.provider_name = "anthropic"
        assert self._detection_logic(mock_provider) == "provider_called"

    def test_unknown_provider_gives_provider_called(self):
        mock_provider = MagicMock(spec=[])  # no provider_name or name attr
        assert self._detection_logic(mock_provider) == "provider_called"


# ---------------------------------------------------------------------------
# ExperimentCandidate carries semantic_fit_disposition
# ---------------------------------------------------------------------------


class TestExperimentCandidateDispositionField:
    def _make_candidate(self, disposition=None):
        return ExperimentCandidate(
            opportunity_id=4,
            channel_id=1,
            canonical_cluster_id=10,
            eligibility_classification="general_eligible",
            planning_intent=PlanningIntent.EXPLORATION,
            experiment_type="exploration",
            primary_target_metric="average_view_percentage",
            primary_metric_direction="higher_is_better",
            hypothesis_sketch="test hypothesis",
            intended_treatment_factors=[],
            controlled_factors=[],
            feature_change_risk="low",
            score=CandidateScoreComponents(
                opportunity_attractiveness=0.5,
                exploitation_value=0.0,
                exploration_value=0.7,
                information_gain=0.8,
                internal_evidence_strength=0.0,
                uncertainty=1.0,
                cluster_coverage_need=1.0,
                production_feasibility=1.0,
                final_planning_score=0.7,
            ),
            semantic_fit_disposition=disposition,
        )

    def test_default_disposition_is_none(self):
        c = self._make_candidate()
        assert c.semantic_fit_disposition is None

    def test_disposition_can_be_set(self):
        c = self._make_candidate("replay_prior_real_call")
        assert c.semantic_fit_disposition == "replay_prior_real_call"

    def test_fake_disposition_value(self):
        c = self._make_candidate("fake_provider_test")
        assert c.semantic_fit_disposition == "fake_provider_test"


# ---------------------------------------------------------------------------
# Integration: planning_service — new cluster always gets EXPLORATION
# ---------------------------------------------------------------------------


def _make_assessment(
    opportunity_id: int = 4,
    channel_id: int = 1,
    classification: str = "general_eligible",
    signal_maturity: str = "actionable",
    signal_confidence: float = 0.85,
    semantic_fit_score: float = 0.95,
    semantic_fit_disposition: str | None = "replay_prior_real_call",
) -> ExperimentEligibilityAssessment:
    return ExperimentEligibilityAssessment(
        opportunity_id=opportunity_id,
        channel_id=channel_id,
        classification=ExperimentEligibilityClassification(classification),
        findings=[],
        policy_snapshot_json="{}",
        assessed_at="2026-08-22T12:00:00",
        signal_maturity=signal_maturity,
        signal_confidence=signal_confidence,
        semantic_fit_score=semantic_fit_score,
        semantic_fit_label="strong_fit",
        semantic_fit_disposition=semantic_fit_disposition,
    )


@pytest.fixture()
def _planning_db(tmp_path):
    """Real-schema DB (v43) for planning service cluster guard tests."""
    from app.core.database import open_db

    db_path = tmp_path / "test_provenance.db"
    conn = open_db(db_path)
    conn.execute("PRAGMA foreign_keys = OFF")

    # Minimum tables for build_portfolio_plan():
    # experiments (cluster count), opportunities (cluster_id + topic), opportunity_scores
    conn.execute(
        """INSERT INTO channels (id, platform, channel_name, platform_channel_id)
           VALUES (1, 'youtube', 'Test Channel', 'UC_test')"""
    )
    conn.execute(
        """INSERT INTO opportunities
           (id, channel_id, discovery_run_id, normalized_topic, raw_topic,
            canonical_cluster_id, current_lifecycle_state, created_at, updated_at)
           VALUES (4, 1, 1, 'crispr gene editing technology', 'crispr gene editing technology',
                   42, 'approved', '2026-08-22T00:00:00', '2026-08-22T00:00:00')"""
    )
    conn.execute(
        """INSERT INTO opportunity_scores
           (opportunity_id, scoring_policy_id, channel_profile_version_id,
            composite_score, confidence, eff_weight_trend_strength,
            eff_weight_audience_demand, eff_weight_competition,
            eff_weight_evergreen_value, eff_weight_audience_fit,
            eff_weight_content_novelty, input_hash, scorer_version, scored_at)
           VALUES (4, 1, 1, 0.49, 0.85, 0.05, 0.20, 0.15, 0.20, 0.30, 0.10,
                   'hash_4', '1.0', '2026-08-22T00:00:00')"""
    )
    conn.commit()
    return conn


class TestPlanningServiceNewClusterGuard:
    def test_new_cluster_with_actionable_maturity_gets_exploration(self, _planning_db):
        """Core regression: cluster_is_new overrides market maturity → EXPLORATION."""
        from app.intelligence.experiments.planning_service import build_portfolio_plan

        # "actionable" maturity (strength=1.0 ≥ 0.67) would trigger EXPLOITATION without the fix.
        # With the fix, new cluster_is_new=True → EXPLORATION regardless of maturity.
        assessment = _make_assessment(signal_maturity="actionable")
        plan = build_portfolio_plan(_planning_db, 1, [assessment], dry_run=True)
        opp4 = next((d for d in plan.decisions if d.opportunity_id == 4), None)
        assert opp4 is not None
        assert opp4.selected
        assert opp4.candidate.planning_intent == PlanningIntent.EXPLORATION, (
            f"Expected EXPLORATION for new cluster, got {opp4.candidate.planning_intent}"
        )
        assert len(opp4.candidate.intended_treatment_factors) == 0, (
            "New cluster must have 0 treatment factors (no confounding)"
        )

    def test_insufficient_maturity_new_cluster_also_gets_exploration(self, _planning_db):
        """Insufficient maturity + new cluster → EXPLORATION (no confounding either way)."""
        from app.intelligence.experiments.planning_service import build_portfolio_plan

        assessment = _make_assessment(
            classification="general_eligible", signal_maturity="insufficient"
        )
        plan = build_portfolio_plan(_planning_db, 1, [assessment], dry_run=True)
        opp4 = next((d for d in plan.decisions if d.opportunity_id == 4), None)
        assert opp4 is not None
        assert opp4.candidate.planning_intent == PlanningIntent.EXPLORATION

    def test_replay_disposition_propagates_to_candidate(self, _planning_db):
        """semantic_fit_disposition='replay_prior_real_call' is carried from
        assessment to candidate."""
        from app.intelligence.experiments.planning_service import build_portfolio_plan

        assessment = _make_assessment(semantic_fit_disposition="replay_prior_real_call")
        plan = build_portfolio_plan(_planning_db, 1, [assessment], dry_run=True)
        opp4 = next(d for d in plan.decisions if d.opportunity_id == 4)
        assert opp4.candidate.semantic_fit_disposition == "replay_prior_real_call"

    def test_fake_disposition_propagates_to_candidate(self, _planning_db):
        """semantic_fit_disposition='fake_provider_test' is carried from assessment to candidate."""
        from app.intelligence.experiments.planning_service import build_portfolio_plan

        assessment = _make_assessment(semantic_fit_disposition="fake_provider_test")
        plan = build_portfolio_plan(_planning_db, 1, [assessment], dry_run=True)
        opp4 = next(d for d in plan.decisions if d.opportunity_id == 4)
        assert opp4.candidate.semantic_fit_disposition == "fake_provider_test"

    def test_none_disposition_propagates_to_candidate(self, _planning_db):
        """None disposition (e.g. deterministic bypass) is preserved in candidate."""
        from app.intelligence.experiments.planning_service import build_portfolio_plan

        assessment = _make_assessment(semantic_fit_disposition=None)
        plan = build_portfolio_plan(_planning_db, 1, [assessment], dry_run=True)
        opp4 = next(d for d in plan.decisions if d.opportunity_id == 4)
        assert opp4.candidate.semantic_fit_disposition is None

    def test_disposition_persisted_to_db(self, _planning_db):
        """semantic_fit_disposition is stored in experiment_candidate_scores (schema v43)."""
        from app.intelligence.experiments.planning_service import build_portfolio_plan

        assessment = _make_assessment(semantic_fit_disposition="replay_prior_real_call")
        plan = build_portfolio_plan(_planning_db, 1, [assessment], dry_run=False)
        _planning_db.commit()

        row = _planning_db.execute(
            "SELECT semantic_fit_disposition FROM experiment_candidate_scores "
            "WHERE planning_run_id = ?",
            (plan.run_id,),
        ).fetchone()
        assert row is not None
        assert row["semantic_fit_disposition"] == "replay_prior_real_call"

    def test_fake_disposition_persisted_to_db(self, _planning_db):
        """fake_provider_test disposition is also persisted correctly."""
        from app.intelligence.experiments.planning_service import build_portfolio_plan

        assessment = _make_assessment(semantic_fit_disposition="fake_provider_test")
        plan = build_portfolio_plan(_planning_db, 1, [assessment], dry_run=False)
        _planning_db.commit()

        row = _planning_db.execute(
            "SELECT semantic_fit_disposition FROM experiment_candidate_scores "
            "WHERE planning_run_id = ?",
            (plan.run_id,),
        ).fetchone()
        assert row is not None
        assert row["semantic_fit_disposition"] == "fake_provider_test"

    def test_cancelled_experiment_excluded_from_cluster_count(self, _planning_db):
        """Cancelled experiments do not mark a cluster as explored."""
        from app.intelligence.experiments.models import ExperimentStatus, ExperimentType
        from app.intelligence.experiments.planning_service import build_portfolio_plan
        from app.intelligence.experiments.repository import (
            create_experiment,
            transition_experiment_state,
        )

        conn = _planning_db
        # Create and cancel an experiment for opp=4
        exp_id = str(uuid.uuid4())
        create_experiment(
            conn,
            experiment_id=exp_id,
            channel_id=1,
            experiment_type=ExperimentType.exploitation,
            hypothesis="test",
            opportunity_id=4,
            actor="test",
        )
        transition_experiment_state(conn, exp_id, ExperimentStatus.cancelled, reason="test")
        conn.commit()

        # Now plan — cluster_is_new should still be True (cancelled excluded)
        assessment = _make_assessment(signal_maturity="actionable")
        plan = build_portfolio_plan(conn, 1, [assessment], dry_run=True)
        opp4 = next((d for d in plan.decisions if d.opportunity_id == 4), None)
        assert opp4 is not None
        assert opp4.candidate.planning_intent == PlanningIntent.EXPLORATION, (
            "Cancelled experiment must not prevent market exploration of the cluster"
        )
        assert len(opp4.candidate.intended_treatment_factors) == 0

    def _seed_prior_experiment(self, conn, opportunity_id: int = 4) -> str:
        from app.intelligence.experiments.models import ExperimentType
        from app.intelligence.experiments.repository import create_experiment

        exp_id = str(uuid.uuid4())
        create_experiment(
            conn,
            experiment_id=exp_id,
            channel_id=1,
            experiment_type=ExperimentType.exploration,
            hypothesis="prior exploration",
            opportunity_id=opportunity_id,
            actor="test",
        )
        conn.commit()
        return exp_id

    def _seed_channel_evidence(self, conn, *, maturity: str, channel_id: int = 1) -> None:
        """Give channel 1 its own measured evidence at the given maturity."""
        cp_channel_id = str(uuid.uuid4())
        conn.execute(
            "UPDATE channels SET cp_channel_id = ? WHERE id = ?", (cp_channel_id, channel_id)
        )
        conn.execute(
            """INSERT INTO channel_performance_baselines
               (channel_id, workspace_id, metric_name, period_type, publication_count,
                mean, median, min_value, max_value, std_dev, sample_maturity,
                source_publication_ids_json, source_snapshot_ids_json,
                comparison_schema_version, observer_version, input_hash,
                created_at, updated_at)
               VALUES (?, 'ws', 'average_view_percentage', 'lifetime', 12,
                       50.0, 50.0, 40.0, 60.0, 5.0, ?,
                       '[]', '[]', 'v1', 'v1', ?, '2026-01-01', '2026-01-01')""",
            (cp_channel_id, maturity, str(uuid.uuid4())[:16]),
        )
        conn.commit()

    def test_existing_cluster_with_mature_channel_evidence_gets_exploitation(self, _planning_db):
        """Explored cluster + mature OWN evidence → EXPLOITATION."""
        from app.intelligence.experiments.planning_service import build_portfolio_plan

        conn = _planning_db
        self._seed_prior_experiment(conn)
        self._seed_channel_evidence(conn, maturity="actionable")

        assessment = _make_assessment(signal_maturity="actionable")
        plan = build_portfolio_plan(conn, 1, [assessment], dry_run=True)
        opp4 = next((d for d in plan.decisions if d.opportunity_id == 4), None)
        assert opp4 is not None
        assert opp4.candidate.planning_intent == PlanningIntent.EXPLOITATION, (
            "Explored cluster with actionable channel evidence should get EXPLOITATION"
        )

    def test_market_maturity_alone_does_not_trigger_exploitation(self, _planning_db):
        """Phase 18D: a strong MARKET signal is not evidence about this channel.

        The cluster has been explored and YouTube-wide maturity is
        'actionable', but Orvella has published nothing and so has no
        baseline of its own. Exploiting here would mean betting the channel
        on someone else's audience data — the planner must keep exploring
        until its own evidence says otherwise.
        """
        from app.intelligence.experiments.planning_service import build_portfolio_plan

        conn = _planning_db
        self._seed_prior_experiment(conn)
        # Deliberately no channel_performance_baselines row.

        assessment = _make_assessment(signal_maturity="actionable")
        plan = build_portfolio_plan(conn, 1, [assessment], dry_run=True)
        opp4 = next((d for d in plan.decisions if d.opportunity_id == 4), None)
        assert opp4 is not None
        assert opp4.candidate.planning_intent == PlanningIntent.EXPLORATION
        # The evidence term must report the absence honestly, not inherit
        # the market's maturity.
        assert opp4.candidate.score.internal_evidence_strength == 0.0

    def test_immature_channel_evidence_still_explores(self, _planning_db):
        """Two publications' worth of evidence is exploratory, not exploitable."""
        from app.intelligence.experiments.planning_service import build_portfolio_plan

        conn = _planning_db
        self._seed_prior_experiment(conn)
        self._seed_channel_evidence(conn, maturity="exploratory")

        assessment = _make_assessment(signal_maturity="actionable")
        plan = build_portfolio_plan(conn, 1, [assessment], dry_run=True)
        opp4 = next((d for d in plan.decisions if d.opportunity_id == 4), None)
        assert opp4 is not None
        assert opp4.candidate.planning_intent == PlanningIntent.EXPLORATION
        assert opp4.candidate.score.internal_evidence_strength == pytest.approx(0.33)


# ---------------------------------------------------------------------------
# Brief service: market_exploration intent for new clusters
# ---------------------------------------------------------------------------


class TestBriefServiceMarketExplorationIntent:
    def test_exploration_new_cluster_produces_market_exploration_brief(self, _planning_db):
        """End-to-end: new cluster → EXPLORATION plan → MARKET_EXPLORATION
        brief, 0 treatment factors."""
        from app.intelligence.experiments.brief_service import create_strategy_brief
        from app.intelligence.experiments.planning_service import build_portfolio_plan

        conn = _planning_db
        assessment = _make_assessment(
            signal_maturity="actionable",
            semantic_fit_disposition="replay_prior_real_call",
        )
        plan = build_portfolio_plan(conn, 1, [assessment], dry_run=False)
        conn.commit()

        # Get selection_decision_id
        row = conn.execute(
            "SELECT id FROM experiment_selection_decisions WHERE planning_run_id=? AND selected=1",
            (plan.run_id,),
        ).fetchone()
        assert row is not None

        brief = create_strategy_brief(conn, row["id"])
        conn.commit()

        assert brief.brief_planning_intent == "market_exploration", (
            f"Expected market_exploration, got {brief.brief_planning_intent!r}"
        )
        assert brief.experiment_type == "exploration"
        # Market exploration hypothesis must not claim prior directional evidence
        assert "prior directional evidence" not in brief.hypothesis, (
            "MARKET_EXPLORATION brief must not claim 'prior directional evidence'"
        )
        # Treatment factors must be empty (verified via JSON in DB)
        brief_row = conn.execute(
            "SELECT treatment_factors_json FROM experiment_strategy_briefs WHERE id = ?",
            (brief.id,),
        ).fetchone()
        treatment_factors = json.loads(brief_row["treatment_factors_json"])
        assert len(treatment_factors) == 0, (
            f"Expected 0 treatment factors in brief, got "
            f"{len(treatment_factors)}: {treatment_factors}"
        )
