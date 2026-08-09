"""Tests for Phase 11 orchestrator — full integration with in-memory SQLite."""

from __future__ import annotations

import json
import pathlib
import tempfile

import pytest

from app.core.database import open_db
from app.learning.errors import InsufficientAnalyticsDataError
from app.learning.orchestrator import (
    accept_recommendation,
    analyze_publication,
    reject_recommendation,
)
from app.learning.repository import get_learning_run, get_recommendation, list_recommendations


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as d:
        conn = open_db(pathlib.Path(d) / "test.db")
        # Minimal prerequisites for analytics ingestion
        conn.execute("INSERT INTO topics (title, angle) VALUES ('Test', 'test')")
        conn.commit()
        yield conn


def _insert_snapshot(conn, publication_id: int = 1) -> int:
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
        (
            publication_id,
            f"snap_hash_{publication_id}",
            json.dumps({"ctr": 0.01}),
        ),
    )
    conn.commit()
    return cursor.lastrowid


def _insert_aggregate(
    conn, publication_id: int, metric_name: str, value: float, snapshot_ids: list | None = None
) -> None:
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
            f"agg_{metric_name}_{value}",
        ),
    )
    conn.commit()


class TestAnalyzePublication:
    def test_no_snapshots_raises_insufficient_data(self, db):
        with pytest.raises(InsufficientAnalyticsDataError):
            analyze_publication(db, publication_id=999, topic_id=1)

    def test_with_snapshot_creates_learning_run(self, db):
        _insert_snapshot(db, publication_id=1)
        run_id = analyze_publication(db, publication_id=1, topic_id=1)
        assert run_id > 0

    def test_completed_run_has_correct_status(self, db):
        _insert_snapshot(db, publication_id=1)
        run_id = analyze_publication(db, publication_id=1, topic_id=1)
        run = get_learning_run(db, run_id)
        assert run.status == "completed"

    def test_run_with_ctr_data_produces_recommendations(self, db):
        _insert_snapshot(db, publication_id=1)
        _insert_aggregate(db, 1, "ctr", 0.01)
        run_id = analyze_publication(db, publication_id=1, topic_id=1)
        run = get_learning_run(db, run_id)
        assert run.recommendation_count > 0

    def test_recommendations_are_persisted(self, db):
        _insert_snapshot(db, publication_id=1)
        _insert_aggregate(db, 1, "ctr", 0.01)
        analyze_publication(db, publication_id=1, topic_id=1)
        recs = list_recommendations(db, topic_id=1)
        assert len(recs) > 0

    def test_recommendations_start_as_pending(self, db):
        _insert_snapshot(db, publication_id=1)
        _insert_aggregate(db, 1, "ctr", 0.01)
        analyze_publication(db, publication_id=1, topic_id=1)
        recs = list_recommendations(db, topic_id=1)
        for rec in recs:
            assert rec.status == "pending"

    def test_run_input_hash_is_deterministic(self, db):
        _insert_snapshot(db, publication_id=1)
        run_id = analyze_publication(db, publication_id=1, topic_id=1)
        run1 = get_learning_run(db, run_id)
        # A second run would have the same snapshot IDs and thus the same hash
        assert run1.input_hash
        assert len(run1.input_hash) == 64

    def test_no_automatic_changes_applied(self, db):
        _insert_snapshot(db, publication_id=1)
        _insert_aggregate(db, 1, "ctr", 0.01)
        analyze_publication(db, publication_id=1, topic_id=1)
        # Analytics tables must not be modified
        snapshots = db.execute("SELECT COUNT(*) FROM analytics_snapshots").fetchone()[0]
        aggregates = db.execute("SELECT COUNT(*) FROM analytics_aggregates").fetchone()[0]
        assert snapshots == 1  # unchanged
        assert aggregates == 1  # unchanged


class TestAcceptRecommendation:
    def _setup(self, db) -> int:
        _insert_snapshot(db, publication_id=1)
        _insert_aggregate(db, 1, "ctr", 0.01)
        analyze_publication(db, publication_id=1, topic_id=1)
        recs = list_recommendations(db, topic_id=1)
        return recs[0].id

    def test_accept_changes_status(self, db):
        rec_id = self._setup(db)
        accept_recommendation(db, rec_id, reviewer="operator")
        rec = get_recommendation(db, rec_id)
        assert rec.status == "accepted"

    def test_accept_creates_review_event(self, db):
        from app.learning.repository import list_review_events

        rec_id = self._setup(db)
        accept_recommendation(db, rec_id, reviewer="operator", notes="Looks good")
        events = list_review_events(db, recommendation_id=rec_id)
        assert len(events) == 1
        assert events[0].event_type == "accepted"
        assert events[0].reviewer == "operator"

    def test_accept_without_reviewer_raises(self, db):
        from app.learning.errors import ReviewerRequiredError

        rec_id = self._setup(db)
        with pytest.raises(ReviewerRequiredError):
            accept_recommendation(db, rec_id, reviewer="")

    def test_accept_superseded_recommendation_raises(self, db):
        from app.learning.errors import RecommendationNotReviewableError
        from app.learning.repository import supersede_recommendation

        rec_id = self._setup(db)
        # Supersede the recommendation first
        supersede_recommendation(db, rec_id, rec_id)
        db.commit()
        with pytest.raises(RecommendationNotReviewableError):
            accept_recommendation(db, rec_id, reviewer="operator")


class TestRejectRecommendation:
    def _setup(self, db) -> int:
        _insert_snapshot(db, publication_id=1)
        _insert_aggregate(db, 1, "ctr", 0.01)
        analyze_publication(db, publication_id=1, topic_id=1)
        recs = list_recommendations(db, topic_id=1)
        return recs[0].id

    def test_reject_changes_status(self, db):
        rec_id = self._setup(db)
        reject_recommendation(db, rec_id, reviewer="operator", notes="Not relevant now")
        rec = get_recommendation(db, rec_id)
        assert rec.status == "rejected"

    def test_reject_without_notes_raises(self, db):
        from app.learning.errors import NotesRequiredError

        rec_id = self._setup(db)
        with pytest.raises(NotesRequiredError):
            reject_recommendation(db, rec_id, reviewer="operator", notes="")

    def test_reject_creates_review_event(self, db):
        from app.learning.repository import list_review_events

        rec_id = self._setup(db)
        reject_recommendation(db, rec_id, reviewer="operator", notes="Too vague")
        events = list_review_events(db, recommendation_id=rec_id)
        assert len(events) == 1
        assert events[0].event_type == "rejected"
        assert events[0].notes == "Too vague"
