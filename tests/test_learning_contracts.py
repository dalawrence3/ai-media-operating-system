"""Phase 11 production-readiness contract tests.

Covers:
- Causal language enforcement (observational recommendations must not claim causation)
- Recommendation strength classification (exploratory vs actionable)
- Evidence classification determinism (experiment_id alone ≠ controlled_experiment)
- Partial run semantics (some generators fail)
- Supersession behaviour (active recommendation superseded by updated evidence)
- ReviewedOptimizationHandoff hydration and immutability
"""

from __future__ import annotations

import json
import pathlib
import re
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from app.core.database import open_db
from app.learning.constants import (
    EVIDENCE_OBSERVATIONAL,
    GENERATOR_CTR,
    RUN_STATUS_COMPLETED,
    RUN_STATUS_FAILED,
    RUN_STATUS_PARTIAL,
    STRENGTH_ACTIONABLE,
    STRENGTH_EXPLORATORY,
)
from app.learning.orchestrator import (
    accept_recommendation,
    analyze_publication,
    build_reviewed_handoff,
    reject_recommendation,
)
from app.learning.recommendations import (
    _classify_evidence,
    _classify_strength,
    generate_all_recommendations,
    generate_ctr_recommendations,
)
from app.learning.repository import (
    get_learning_run,
    get_recommendation,
    list_generator_results,
    list_recommendations,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as d:
        conn = open_db(pathlib.Path(d) / "test.db")
        conn.execute("INSERT INTO topics (title, angle) VALUES ('Test', 'test')")
        conn.commit()
        yield conn


_snap_counter = 0


def _insert_snapshot(conn, publication_id: int = 1) -> int:
    global _snap_counter
    _snap_counter += 1
    cursor = conn.execute(
        """
        INSERT INTO analytics_snapshots
            (publication_id, publishing_plan_id, publishing_job_id,
             render_manifest_id, scene_manifest_id, production_plan_id,
             script_id, topic_id, narration_run_id, caption_run_id,
             provider, provider_version, adapter_version,
             engine_version, analytics_schema_version, db_schema_version,
             input_hash, raw_metrics_json, ingested_at, created_at)
        VALUES (?,1,1,1,1,1,2,1,3,4,'fake','1.0.0','1.0.0',
                '1.0.0','1.0.0',17,
                ?,?, strftime('%Y-%m-%dT%H:%M:%S','now'),
                strftime('%Y-%m-%dT%H:%M:%S','now'))
        """,
        (publication_id, f"snap_hash_{publication_id}_{_snap_counter}", json.dumps({})),
    )
    conn.commit()
    return cursor.lastrowid


def _insert_aggregate(
    conn,
    publication_id: int,
    metric_name: str,
    value: float,
    snapshot_ids: list[int] | None = None,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO analytics_aggregates
            (publication_id, topic_id, provider, period_type, period_key,
             metric_name, metric_value, snapshot_count, calculation_method,
             currency_code, source_snapshot_ids_json, input_hash, created_at)
        VALUES (?,1,'fake','lifetime','lifetime',?,?,1,'sum',NULL,?,?,
                strftime('%Y-%m-%dT%H:%M:%S','now'))
        """,
        (
            publication_id,
            metric_name,
            value,
            json.dumps(snapshot_ids or [1]),
            f"hash_{metric_name}_{value}_{snapshot_ids}",
        ),
    )
    conn.commit()


def _make_handoff(publication_id: int = 1, topic_id: int = 1, experiment_id=None):
    h = MagicMock()
    h.publication_id = publication_id
    h.topic_id = topic_id
    h.experiment_id = experiment_id
    h.script_id = 2
    h.narration_run_id = 3
    h.caption_run_id = 4
    h.scene_manifest_id = 5
    h.render_manifest_id = 6
    h.publishing_plan_id = 7
    h.publishing_job_id = 8
    h.production_plan_id = 10
    return h


# ── Causal-language enforcement ───────────────────────────────────────────────

# Prohibited causal patterns in observational recommendation text.
# Controlled-experiment wording is only valid when evidence_classification
# = "controlled_experiment", which Phase 11 does not currently produce.
_CAUSAL_PATTERNS = re.compile(
    r"\b("
    r"cause[sd]?"
    r"|increases?"
    r"|decreases?"
    r"|improve[sd]?"
    r"|reduce[sd]?"
    r"|leads? to"
    r"|results? in"
    r"|because of"
    r")\b",
    re.IGNORECASE,
)


def _emit_all_drafts(db):
    """Emit all possible recommendation text by exercising all generators."""
    _insert_aggregate(db, 1, "ctr", 0.01, snapshot_ids=[1, 2, 3])  # low CTR × 2
    _insert_aggregate(db, 1, "ctr", 0.06, snapshot_ids=[1, 2, 3])  # duplicate, last wins
    # Re-insert to get both low and high CTR exercised in separate calls
    return _make_handoff()


class TestCausalLanguageEnforcement:
    def _collect_text(self, drafts) -> list[tuple[str, str]]:
        """Return (field_name, text) pairs for all recommendation text fields."""
        pairs = []
        for d in drafts:
            pairs.append(("explanation", d.explanation))
            pairs.append(("expected_improvement", d.expected_improvement))
        return pairs

    def test_low_ctr_no_causal_wording(self, db):
        _insert_aggregate(db, 1, "ctr", 0.01)
        drafts = generate_ctr_recommendations(db, _make_handoff(), learning_run_id=1)
        for field, text in self._collect_text(drafts):
            m = _CAUSAL_PATTERNS.search(text)
            assert m is None, f"Causal term {m.group()!r} in observational CTR {field!r}: {text!r}"

    def test_high_ctr_no_causal_wording(self, db):
        _insert_aggregate(db, 1, "ctr", 0.06)
        drafts = generate_ctr_recommendations(db, _make_handoff(), learning_run_id=1)
        for field, text in self._collect_text(drafts):
            m = _CAUSAL_PATTERNS.search(text)
            assert m is None, f"Causal term {m.group()!r} in observational CTR {field!r}: {text!r}"

    def test_all_generators_no_causal_wording(self, db):
        _insert_aggregate(db, 1, "ctr", 0.01)
        _insert_aggregate(db, 1, "average_view_duration", 10.0)
        _insert_aggregate(db, 1, "views", 1000.0)
        _insert_aggregate(db, 1, "likes", 5.0)
        _insert_aggregate(db, 1, "subscribers_gained", 2.0)
        _insert_aggregate(db, 1, "subscribers_lost", 8.0)
        _insert_aggregate(db, 1, "shares", 10.0)
        _insert_aggregate(db, 1, "watch_time_seconds", 5000.0)

        result = generate_all_recommendations(db, _make_handoff(), learning_run_id=1)
        for draft in result.drafts:
            for field in ("explanation", "expected_improvement"):
                text = getattr(draft, field)
                m = _CAUSAL_PATTERNS.search(text)
                assert m is None, (
                    f"Causal term {m.group()!r} found in {field!r} "
                    f"of observational recommendation {draft.title!r}: {text!r}"
                )


# ── Evidence classification ────────────────────────────────────────────────────


class TestEvidenceClassification:
    def test_classify_evidence_returns_observational(self, db):
        handoff = _make_handoff()
        assert _classify_evidence(handoff) == EVIDENCE_OBSERVATIONAL

    def test_experiment_id_alone_does_not_produce_controlled_experiment(self, db):
        # experiment_id set ≠ controlled_experiment classification
        handoff = _make_handoff(experiment_id="exp-abc-123")
        classification = _classify_evidence(handoff)
        assert classification == EVIDENCE_OBSERVATIONAL, (
            "experiment_id alone must not produce 'controlled_experiment'; "
            "Phase 11 has no validated treatment/control semantics."
        )

    def test_generated_drafts_all_observational(self, db):
        _insert_aggregate(db, 1, "ctr", 0.01)
        handoff = _make_handoff(experiment_id="exp-xyz")
        drafts = generate_ctr_recommendations(db, handoff, learning_run_id=1)
        for d in drafts:
            assert d.evidence_classification == EVIDENCE_OBSERVATIONAL, (
                f"Draft has non-observational classification: {d.evidence_classification!r}"
            )


# ── Recommendation strength ───────────────────────────────────────────────────


class TestRecommendationStrength:
    def _make_ev(self, snapshot_ids: list[int]):
        from app.learning.models import EvidenceItem

        return EvidenceItem(
            metric_name="ctr",
            observed_value=0.01,
            comparison_value=0.02,
            period_type="lifetime",
            period_key="lifetime",
            snapshot_ids=snapshot_ids,
            interpretation="test",
        )

    def test_single_snapshot_is_exploratory(self):
        ev = self._make_ev([1])
        # With 1 unique snapshot, volume is low, consistency 0 → score < 0.4
        from app.learning.scoring import compute_confidence

        score, _ = compute_confidence([ev], 0.01, 0.02, "below")
        strength = _classify_strength(score, [ev])
        assert strength == STRENGTH_EXPLORATORY

    def test_sufficient_snapshots_and_effect_can_be_actionable(self):
        ev = self._make_ev(list(range(5)))  # 5 unique snapshots
        from app.learning.scoring import compute_confidence

        # CTR well below threshold → large effect
        score, _ = compute_confidence([ev], 0.001, 0.02, "below")
        strength = _classify_strength(score, [ev])
        assert strength == STRENGTH_ACTIONABLE

    def test_zero_evidence_is_exploratory(self):
        strength = _classify_strength(0.0, [])
        assert strength == STRENGTH_EXPLORATORY

    def test_drafts_have_strength_field(self, db):
        _insert_aggregate(db, 1, "ctr", 0.01)
        drafts = generate_ctr_recommendations(db, _make_handoff(), learning_run_id=1)
        for d in drafts:
            assert d.recommendation_strength in {STRENGTH_EXPLORATORY, STRENGTH_ACTIONABLE}

    def test_single_snapshot_draft_is_exploratory(self, db):
        _insert_aggregate(db, 1, "ctr", 0.01, snapshot_ids=[99])
        drafts = generate_ctr_recommendations(db, _make_handoff(), learning_run_id=1)
        for d in drafts:
            assert d.recommendation_strength == STRENGTH_EXPLORATORY


# ── Partial run semantics ─────────────────────────────────────────────────────


class TestPartialRunSemantics:
    def test_all_generators_fail_produces_failed_run(self, db):
        _insert_snapshot(db)
        # Insert sufficient data so maturity checks pass and the patched bad
        # generators actually get called (without this they'd be SKIPPED first).
        _insert_aggregate(db, 1, "ctr", 0.03)
        _insert_aggregate(db, 1, "views", 50.0)
        _insert_aggregate(db, 1, "average_view_duration", 5.0)
        _insert_aggregate(db, 1, "likes", 2.0)
        _insert_aggregate(db, 1, "subscribers_gained", 1.0)
        _insert_aggregate(db, 1, "subscribers_lost", 3.0)
        _insert_aggregate(db, 1, "shares", 0.0)
        _insert_aggregate(db, 1, "watch_time_seconds", 250.0)

        def bad_gen(conn, handoff, run_id):
            raise RuntimeError("all broken")

        with (
            patch("app.learning.recommendations.generate_ctr_recommendations", bad_gen),
            patch("app.learning.recommendations.generate_retention_recommendations", bad_gen),
            patch("app.learning.recommendations.generate_engagement_recommendations", bad_gen),
            patch("app.learning.recommendations.generate_watch_time_recommendations", bad_gen),
            patch("app.learning.recommendations.generate_subscriber_recommendations", bad_gen),
            patch("app.learning.recommendations.generate_shares_recommendations", bad_gen),
        ):
            run_id = analyze_publication(db, publication_id=1, topic_id=1)

        run = get_learning_run(db, run_id)
        assert run.status == RUN_STATUS_FAILED

    def test_one_generator_fails_produces_partial_run(self, db):
        _insert_snapshot(db)
        # CTR aggregate ensures maturity passes for CTR generator (required_metrics check).
        # Views aggregate ensures retention and others pass their maturity check.
        _insert_aggregate(db, 1, "ctr", 0.03)
        _insert_aggregate(db, 1, "views", 50.0)
        _insert_aggregate(db, 1, "average_view_duration", 10.0)

        def bad_ctr(conn, handoff, run_id):
            raise RuntimeError("CTR broken")

        with patch("app.learning.recommendations.generate_ctr_recommendations", bad_ctr):
            run_id = analyze_publication(db, publication_id=1, topic_id=1)

        run = get_learning_run(db, run_id)
        assert run.status == RUN_STATUS_PARTIAL

    def test_partial_run_persists_successful_recommendations(self, db):
        _insert_snapshot(db)
        # Enough data for maturity to pass for CTR and for retention
        _insert_aggregate(db, 1, "ctr", 0.03)
        _insert_aggregate(db, 1, "views", 50.0)
        _insert_aggregate(db, 1, "average_view_duration", 10.0)

        def bad_ctr(conn, handoff, run_id):
            raise RuntimeError("CTR broken")

        with patch("app.learning.recommendations.generate_ctr_recommendations", bad_ctr):
            analyze_publication(db, publication_id=1, topic_id=1)

        recs = list_recommendations(db, topic_id=1)
        # Retention generator produced recommendations despite CTR failure
        assert len(recs) > 0

    def test_generator_results_persisted_for_run(self, db):
        _insert_snapshot(db)
        run_id = analyze_publication(db, publication_id=1, topic_id=1)
        gen_results = list_generator_results(db, run_id)
        assert len(gen_results) == 6
        # With no analytics data, generators are SKIPPED (insufficient evidence).
        # All results must be either succeeded or skipped — never failed.
        assert all(gr.status in ("succeeded", "skipped") for gr in gen_results)

    def test_partial_run_records_failure(self, db):
        _insert_snapshot(db)
        # Insert CTR aggregate so the maturity check passes and the patched
        # bad_ctr generator gets called (without this, maturity skips it first).
        _insert_aggregate(db, 1, "ctr", 0.03)

        def bad_ctr(conn, handoff, run_id):
            raise RuntimeError("CTR broken")

        with patch("app.learning.recommendations.generate_ctr_recommendations", bad_ctr):
            run_id = analyze_publication(db, publication_id=1, topic_id=1)

        gen_results = list_generator_results(db, run_id)
        ctr_result = next(gr for gr in gen_results if gr.generator_name == GENERATOR_CTR)
        assert ctr_result.status == "failed"
        assert "CTR broken" in ctr_result.error_message

    def test_successful_run_status_is_completed(self, db):
        _insert_snapshot(db)
        run_id = analyze_publication(db, publication_id=1, topic_id=1)
        run = get_learning_run(db, run_id)
        assert run.status == RUN_STATUS_COMPLETED


# ── Supersession behaviour ────────────────────────────────────────────────────


class TestSupersession:
    def test_second_run_same_evidence_does_not_duplicate(self, db):
        _insert_snapshot(db)
        _insert_aggregate(db, 1, "ctr", 0.01, snapshot_ids=[1])
        analyze_publication(db, publication_id=1, topic_id=1)
        analyze_publication(db, publication_id=1, topic_id=1)
        # Same snapshot IDs → same hash → idempotent, no duplicate rows
        recs = list_recommendations(db, topic_id=1)
        pending = [r for r in recs if r.status == "pending"]
        # Should have the same number as a single run
        ctr_pending = [r for r in pending if r.measure == "ctr"]
        assert len(ctr_pending) <= 2  # 2 CTR recommendations at most

    def test_updated_evidence_supersedes_prior_recommendation(self, db):
        _insert_snapshot(db, publication_id=1)
        _insert_aggregate(db, 1, "ctr", 0.01, snapshot_ids=[1])
        run1 = analyze_publication(db, publication_id=1, topic_id=1)

        # Add a second snapshot and re-insert aggregate with new snapshot_ids
        _insert_snapshot(db, publication_id=1)
        _insert_aggregate(db, 1, "ctr", 0.01, snapshot_ids=[1, 2])

        run2 = analyze_publication(db, publication_id=1, topic_id=1)
        assert run1 != run2

        all_recs = list_recommendations(db, topic_id=1)
        superseded_recs = [r for r in all_recs if r.status == "superseded"]
        assert len(superseded_recs) > 0, "Prior recommendation should be superseded"

    def test_superseded_recommendation_history_preserved(self, db):
        _insert_snapshot(db, publication_id=1)
        _insert_aggregate(db, 1, "ctr", 0.01, snapshot_ids=[1])
        analyze_publication(db, publication_id=1, topic_id=1)

        _insert_snapshot(db, publication_id=1)
        _insert_aggregate(db, 1, "ctr", 0.01, snapshot_ids=[1, 2])
        analyze_publication(db, publication_id=1, topic_id=1)

        all_recs = list_recommendations(db, topic_id=1)
        superseded = [r for r in all_recs if r.status == "superseded"]
        for r in superseded:
            assert r.superseded_at is not None
            assert r.superseded_by_id is not None

    def test_rejected_recommendation_not_confused_with_superseded(self, db):
        _insert_snapshot(db, publication_id=1)
        _insert_aggregate(db, 1, "ctr", 0.01, snapshot_ids=[1])
        analyze_publication(db, publication_id=1, topic_id=1)
        recs = list_recommendations(db, topic_id=1, status="pending")
        rec_id = recs[0].id
        reject_recommendation(db, rec_id, reviewer="op", notes="not relevant")

        rec = get_recommendation(db, rec_id)
        assert rec.status == "rejected"
        assert rec.superseded_at is None
        assert rec.superseded_by_id is None

    def test_failed_generator_does_not_supersede_prior_recommendation(self, db):
        _insert_snapshot(db, publication_id=1)
        _insert_aggregate(db, 1, "ctr", 0.01, snapshot_ids=[1])
        run1 = analyze_publication(db, publication_id=1, topic_id=1)

        # Second run with CTR generator failing
        _insert_snapshot(db, publication_id=1)

        def bad_ctr(conn, handoff, run_id):
            raise RuntimeError("CTR broken")

        with patch("app.learning.recommendations.generate_ctr_recommendations", bad_ctr):
            analyze_publication(db, publication_id=1, topic_id=1)

        # Prior CTR recommendation must still be active (not superseded)
        all_recs = list_recommendations(db, topic_id=1)
        run1_recs = [r for r in all_recs if r.learning_run_id == run1]
        ctr_run1 = [r for r in run1_recs if r.measure == "ctr"]
        for r in ctr_run1:
            assert r.status != "superseded", (
                "Failed CTR generator must not supersede prior CTR recommendation"
            )


# ── ReviewedOptimizationHandoff ───────────────────────────────────────────────


class TestReviewedOptimizationHandoff:
    def test_build_handoff_returns_frozen_model(self, db):
        _insert_snapshot(db)
        _insert_aggregate(db, 1, "ctr", 0.01)
        run_id = analyze_publication(db, publication_id=1, topic_id=1)
        handoff = build_reviewed_handoff(db, run_id)
        # Attempt mutation of frozen model
        with pytest.raises(ValidationError):
            handoff.learning_run_id = 999

    def test_build_handoff_has_correct_run_id(self, db):
        _insert_snapshot(db)
        run_id = analyze_publication(db, publication_id=1, topic_id=1)
        handoff = build_reviewed_handoff(db, run_id)
        assert handoff.learning_run_id == run_id

    def test_build_handoff_accepted_recommendations_populated(self, db):
        _insert_snapshot(db)
        _insert_aggregate(db, 1, "ctr", 0.01)
        run_id = analyze_publication(db, publication_id=1, topic_id=1)
        recs = list_recommendations(db, topic_id=1)
        accept_recommendation(db, recs[0].id, reviewer="operator")
        handoff = build_reviewed_handoff(db, run_id)
        assert len(handoff.accepted) == 1
        assert handoff.accepted[0].status == "accepted"

    def test_build_handoff_rejected_recommendations_populated(self, db):
        _insert_snapshot(db)
        _insert_aggregate(db, 1, "ctr", 0.01)
        run_id = analyze_publication(db, publication_id=1, topic_id=1)
        recs = list_recommendations(db, topic_id=1)
        reject_recommendation(db, recs[0].id, reviewer="operator", notes="not now")
        handoff = build_reviewed_handoff(db, run_id)
        assert len(handoff.rejected) == 1
        assert handoff.rejected[0].status == "rejected"

    def test_build_handoff_review_events_included(self, db):
        _insert_snapshot(db)
        _insert_aggregate(db, 1, "ctr", 0.01)
        run_id = analyze_publication(db, publication_id=1, topic_id=1)
        recs = list_recommendations(db, topic_id=1)
        accept_recommendation(db, recs[0].id, reviewer="operator", notes="good")
        handoff = build_reviewed_handoff(db, run_id)
        item = handoff.accepted[0]
        assert len(item.review_events) == 1
        assert item.review_events[0].reviewer == "operator"

    def test_build_handoff_generator_results_included(self, db):
        _insert_snapshot(db)
        run_id = analyze_publication(db, publication_id=1, topic_id=1)
        handoff = build_reviewed_handoff(db, run_id)
        assert len(handoff.generator_results) == 6

    def test_build_handoff_exposes_evidence_classification(self, db):
        _insert_snapshot(db)
        _insert_aggregate(db, 1, "ctr", 0.01)
        run_id = analyze_publication(db, publication_id=1, topic_id=1)
        handoff = build_reviewed_handoff(db, run_id)
        for bucket in (handoff.pending, handoff.accepted, handoff.rejected):
            for item in bucket:
                assert item.evidence_classification == EVIDENCE_OBSERVATIONAL

    def test_build_handoff_exposes_recommendation_strength(self, db):
        _insert_snapshot(db)
        _insert_aggregate(db, 1, "ctr", 0.01)
        run_id = analyze_publication(db, publication_id=1, topic_id=1)
        handoff = build_reviewed_handoff(db, run_id)
        for bucket in (handoff.pending, handoff.accepted, handoff.rejected):
            for item in bucket:
                assert item.recommendation_strength in {STRENGTH_EXPLORATORY, STRENGTH_ACTIONABLE}

    def test_handoff_run_status_matches_learning_run(self, db):
        _insert_snapshot(db)
        run_id = analyze_publication(db, publication_id=1, topic_id=1)
        run = get_learning_run(db, run_id)
        handoff = build_reviewed_handoff(db, run_id)
        assert handoff.run_status == run.status
