"""Tests for Phase 11 recommendation generators — deterministic, no network."""

from __future__ import annotations

import json
import pathlib
import tempfile
from unittest.mock import MagicMock

import pytest

from app.core.database import open_db
from app.learning.constants import (
    DOMAIN_ANALYTICS,
    DOMAIN_NARRATION,
    DOMAIN_PUBLISHING,
    DOMAIN_SCRIPTS,
    DOMAIN_TOPICS,
)
from app.learning.recommendations import (
    generate_all_recommendations,
    generate_ctr_recommendations,
    generate_engagement_recommendations,
    generate_retention_recommendations,
    generate_shares_recommendations,
    generate_subscriber_recommendations,
)


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as d:
        conn = open_db(pathlib.Path(d) / "test.db")
        conn.execute("INSERT INTO topics (title, angle) VALUES ('Test', 'test')")
        conn.commit()
        yield conn


def _insert_aggregate(conn, publication_id: int, metric_name: str, value: float,
                       snapshot_ids: list[int] | None = None) -> None:
    conn.execute(
        """
        INSERT INTO analytics_aggregates
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
            f"hash_{metric_name}_{value}",
        ),
    )
    conn.commit()


def _make_handoff(publication_id: int = 1, topic_id: int = 1):
    h = MagicMock()
    h.publication_id = publication_id
    h.topic_id = topic_id
    h.experiment_id = None
    h.script_id = 2
    h.narration_run_id = 3
    h.caption_run_id = 4
    h.scene_manifest_id = 5
    h.render_manifest_id = 6
    h.publishing_plan_id = 7
    h.publishing_job_id = 8
    h.production_plan_id = 10
    return h


class TestCTRGenerator:
    def test_no_ctr_data_returns_empty(self, db):
        drafts = generate_ctr_recommendations(db, _make_handoff(), learning_run_id=1)
        assert drafts == []

    def test_low_ctr_produces_two_recommendations(self, db):
        _insert_aggregate(db, 1, "ctr", 0.01)  # below 2% threshold
        drafts = generate_ctr_recommendations(db, _make_handoff(), learning_run_id=1)
        assert len(drafts) == 2
        domains = {d.domain for d in drafts}
        assert DOMAIN_PUBLISHING in domains
        assert DOMAIN_SCRIPTS in domains

    def test_high_ctr_produces_positive_reinforcement(self, db):
        _insert_aggregate(db, 1, "ctr", 0.06)  # above 5% threshold
        drafts = generate_ctr_recommendations(db, _make_handoff(), learning_run_id=1)
        assert len(drafts) == 1
        assert drafts[0].domain == DOMAIN_ANALYTICS

    def test_medium_ctr_produces_no_recommendations(self, db):
        _insert_aggregate(db, 1, "ctr", 0.03)  # between 2% and 5%
        drafts = generate_ctr_recommendations(db, _make_handoff(), learning_run_id=1)
        assert drafts == []

    def test_drafts_have_evidence(self, db):
        _insert_aggregate(db, 1, "ctr", 0.01)
        drafts = generate_ctr_recommendations(db, _make_handoff(), learning_run_id=1)
        for draft in drafts:
            assert len(draft.evidence) > 0

    def test_drafts_have_input_hash(self, db):
        _insert_aggregate(db, 1, "ctr", 0.01)
        drafts = generate_ctr_recommendations(db, _make_handoff(), learning_run_id=1)
        for draft in drafts:
            assert draft.input_hash
            assert len(draft.input_hash) == 64

    def test_deterministic_same_inputs(self, db):
        _insert_aggregate(db, 1, "ctr", 0.01)
        handoff = _make_handoff()
        d1 = generate_ctr_recommendations(db, handoff, learning_run_id=1)
        d2 = generate_ctr_recommendations(db, handoff, learning_run_id=1)
        assert [d.input_hash for d in d1] == [d.input_hash for d in d2]


class TestRetentionGenerator:
    def test_no_data_returns_empty(self, db):
        drafts = generate_retention_recommendations(db, _make_handoff(), learning_run_id=1)
        assert drafts == []

    def test_low_retention_produces_recommendations(self, db):
        _insert_aggregate(db, 1, "average_view_duration", 10.0)  # below 20s
        drafts = generate_retention_recommendations(db, _make_handoff(), learning_run_id=1)
        assert len(drafts) == 2
        domains = {d.domain for d in drafts}
        assert DOMAIN_SCRIPTS in domains
        assert DOMAIN_NARRATION in domains

    def test_high_retention_produces_positive_recommendation(self, db):
        _insert_aggregate(db, 1, "average_view_duration", 50.0)  # above 45s
        drafts = generate_retention_recommendations(db, _make_handoff(), learning_run_id=1)
        assert len(drafts) == 1
        assert drafts[0].domain == DOMAIN_ANALYTICS

    def test_medium_retention_produces_no_recommendation(self, db):
        _insert_aggregate(db, 1, "average_view_duration", 30.0)  # between 20 and 45
        drafts = generate_retention_recommendations(db, _make_handoff(), learning_run_id=1)
        assert drafts == []


class TestEngagementGenerator:
    def test_no_data_returns_empty(self, db):
        drafts = generate_engagement_recommendations(db, _make_handoff(), learning_run_id=1)
        assert drafts == []

    def test_missing_views_returns_empty(self, db):
        _insert_aggregate(db, 1, "likes", 5.0)
        drafts = generate_engagement_recommendations(db, _make_handoff(), learning_run_id=1)
        assert drafts == []

    def test_low_engagement_produces_recommendation(self, db):
        _insert_aggregate(db, 1, "views", 10000.0)
        _insert_aggregate(db, 1, "likes", 50.0)  # 0.5%, below 1%
        drafts = generate_engagement_recommendations(db, _make_handoff(), learning_run_id=1)
        assert len(drafts) == 1

    def test_high_engagement_produces_no_recommendation(self, db):
        _insert_aggregate(db, 1, "views", 1000.0)
        _insert_aggregate(db, 1, "likes", 50.0)  # 5%, above 1%
        drafts = generate_engagement_recommendations(db, _make_handoff(), learning_run_id=1)
        assert drafts == []


class TestSubscriberGenerator:
    def test_no_data_returns_empty(self, db):
        drafts = generate_subscriber_recommendations(db, _make_handoff(), learning_run_id=1)
        assert drafts == []

    def test_net_loss_produces_recommendation(self, db):
        _insert_aggregate(db, 1, "subscribers_gained", 5.0)
        _insert_aggregate(db, 1, "subscribers_lost", 10.0)  # net -5
        drafts = generate_subscriber_recommendations(db, _make_handoff(), learning_run_id=1)
        assert len(drafts) == 1
        assert drafts[0].domain == DOMAIN_TOPICS

    def test_strong_growth_produces_positive_recommendation(self, db):
        _insert_aggregate(db, 1, "subscribers_gained", 20.0)
        _insert_aggregate(db, 1, "subscribers_lost", 2.0)  # net +18
        drafts = generate_subscriber_recommendations(db, _make_handoff(), learning_run_id=1)
        assert len(drafts) == 1
        assert drafts[0].domain == DOMAIN_TOPICS

    def test_small_net_gain_produces_no_recommendation(self, db):
        _insert_aggregate(db, 1, "subscribers_gained", 5.0)
        _insert_aggregate(db, 1, "subscribers_lost", 3.0)  # net +2, below threshold of 10
        drafts = generate_subscriber_recommendations(db, _make_handoff(), learning_run_id=1)
        assert drafts == []


class TestSharesGenerator:
    def test_no_data_returns_empty(self, db):
        drafts = generate_shares_recommendations(db, _make_handoff(), learning_run_id=1)
        assert drafts == []

    def test_high_share_rate_produces_recommendation(self, db):
        _insert_aggregate(db, 1, "views", 1000.0)
        _insert_aggregate(db, 1, "shares", 20.0)  # 2%, above 0.5%
        drafts = generate_shares_recommendations(db, _make_handoff(), learning_run_id=1)
        assert len(drafts) == 1

    def test_low_share_rate_produces_no_recommendation(self, db):
        _insert_aggregate(db, 1, "views", 1000.0)
        _insert_aggregate(db, 1, "shares", 2.0)  # 0.2%, below 0.5%
        drafts = generate_shares_recommendations(db, _make_handoff(), learning_run_id=1)
        assert drafts == []


class TestGenerateAll:
    def test_no_data_returns_empty_drafts(self, db):
        result = generate_all_recommendations(db, _make_handoff(), learning_run_id=1)
        assert result.drafts == []

    def test_no_data_all_generators_succeeded(self, db):
        result = generate_all_recommendations(db, _make_handoff(), learning_run_id=1)
        assert len(result.generator_results) == 6
        assert all(gr.status == "succeeded" for gr in result.generator_results)

    def test_multiple_signals_produce_multiple_recommendations(self, db):
        _insert_aggregate(db, 1, "ctr", 0.01)
        _insert_aggregate(db, 1, "average_view_duration", 10.0)
        _insert_aggregate(db, 1, "views", 1000.0)
        _insert_aggregate(db, 1, "likes", 5.0)
        result = generate_all_recommendations(db, _make_handoff(), learning_run_id=1)
        assert len(result.drafts) >= 4  # ctr×2, retention×2, engagement×1

    def test_all_drafts_have_required_fields(self, db):
        _insert_aggregate(db, 1, "ctr", 0.01)
        result = generate_all_recommendations(db, _make_handoff(), learning_run_id=1)
        for draft in result.drafts:
            assert draft.domain
            assert draft.subsystem
            assert draft.title
            assert draft.explanation
            assert draft.expected_improvement
            assert draft.input_hash
            assert 0.0 <= draft.confidence_score <= 1.0
            assert draft.evidence_classification == "observational"
            assert draft.recommendation_strength in {"exploratory", "actionable"}

    def test_generator_exception_recorded_not_propagated(self, db):
        """A broken generator must be recorded as failed, not raise."""
        from unittest.mock import patch

        def bad_gen(conn, handoff, learning_run_id):
            raise RuntimeError("Simulated generator failure")

        with patch("app.learning.recommendations.generate_ctr_recommendations", bad_gen):
            result = generate_all_recommendations(db, _make_handoff(), learning_run_id=1)

        ctr_result = next(
            gr for gr in result.generator_results if gr.generator_name == "ctr"
        )
        assert ctr_result.status == "failed"
        assert "Simulated generator failure" in ctr_result.error_message

    def test_generator_failure_does_not_affect_other_generators(self, db):
        """Remaining generators continue when one fails."""
        from unittest.mock import patch
        _insert_aggregate(db, 1, "average_view_duration", 10.0)  # for retention generator

        def bad_gen(conn, handoff, learning_run_id):
            raise RuntimeError("CTR generator failed")

        with patch("app.learning.recommendations.generate_ctr_recommendations", bad_gen):
            result = generate_all_recommendations(db, _make_handoff(), learning_run_id=1)

        succeeded = [gr for gr in result.generator_results if gr.status == "succeeded"]
        assert len(succeeded) == 5  # all except CTR
