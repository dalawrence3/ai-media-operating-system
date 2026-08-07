"""Tests for Phase 11 repository layer — append-only, no UPDATE/DELETE on recommendations."""

from __future__ import annotations

import pathlib
import tempfile

import pytest

from app.core.database import open_db
from app.learning.errors import LearningRunNotFoundError, RecommendationNotFoundError
from app.learning.models import EvidenceItem, RecommendationDraft, ReviewEventDraft
from app.learning.repository import (
    complete_learning_run,
    create_learning_run,
    create_recommendation,
    create_review_event,
    fail_learning_run,
    get_learning_run,
    get_recommendation,
    list_learning_runs,
    list_recommendations,
    list_review_events,
    supersede_recommendation,
    update_recommendation_status,
)


@pytest.fixture
def conn():
    with tempfile.TemporaryDirectory() as d:
        db = open_db(pathlib.Path(d) / "test.db")
        # Insert a topic so FK constraints pass
        db.execute(
            "INSERT INTO topics (title, angle) VALUES ('Test Topic', 'test angle')"
        )
        db.commit()
        yield db


def _make_draft(run_id: int, topic_id: int = 1, **overrides) -> RecommendationDraft:
    ev = EvidenceItem(
        metric_name="ctr",
        observed_value=0.01,
        comparison_value=0.02,
        period_type="lifetime",
        period_key="lifetime",
        snapshot_ids=[1, 2],
        interpretation="test",
    )
    defaults = dict(
        learning_run_id=run_id,
        topic_id=topic_id,
        publication_id=None,
        domain="scripts",
        subsystem="hook_effectiveness",
        measure="ctr",
        title="Test recommendation",
        explanation="Because CTR is low",
        expected_improvement="CTR will improve",
        evidence=[ev],
        confidence="low",
        confidence_score=0.25,
        affected_subsystem="script",
        subsystem_entity_type="script",
        subsystem_entity_id=2,
        engine_version="1.0.0",
        schema_version="1.0.0",
        input_hash="abc123",
    )
    defaults.update(overrides)
    return RecommendationDraft(**defaults)


class TestLearningRunCRUD:
    def test_create_run_returns_id(self, conn):
        run_id = create_learning_run(conn, topic_id=1, publication_id=None, input_hash="h1")
        assert isinstance(run_id, int)
        assert run_id > 0

    def test_get_run_after_create(self, conn):
        run_id = create_learning_run(conn, topic_id=1, publication_id=10, input_hash="h1")
        run = get_learning_run(conn, run_id)
        assert run.id == run_id
        assert run.topic_id == 1
        assert run.publication_id == 10
        assert run.status == "running"
        assert run.recommendation_count == 0

    def test_get_run_not_found_raises(self, conn):
        with pytest.raises(LearningRunNotFoundError):
            get_learning_run(conn, 99999)

    def test_complete_run_updates_counts(self, conn):
        run_id = create_learning_run(conn, topic_id=1, publication_id=None, input_hash="h1")
        complete_learning_run(conn, run_id, publication_count=1, recommendation_count=5)
        run = get_learning_run(conn, run_id)
        assert run.status == "completed"
        assert run.recommendation_count == 5
        assert run.completed_at is not None

    def test_fail_run_records_error(self, conn):
        run_id = create_learning_run(conn, topic_id=1, publication_id=None, input_hash="h1")
        fail_learning_run(conn, run_id, "Something went wrong")
        run = get_learning_run(conn, run_id)
        assert run.status == "failed"
        assert "Something went wrong" in run.error

    def test_list_runs_empty_when_none(self, conn):
        result = list_learning_runs(conn, topic_id=1)
        assert result == []

    def test_list_runs_returns_all_for_topic(self, conn):
        run_id1 = create_learning_run(conn, topic_id=1, publication_id=None, input_hash="h1")
        run_id2 = create_learning_run(conn, topic_id=1, publication_id=None, input_hash="h2")
        runs = list_learning_runs(conn, topic_id=1)
        assert len(runs) == 2
        ids = {r.id for r in runs}
        assert run_id1 in ids
        assert run_id2 in ids

    def test_list_runs_without_topic_filter(self, conn):
        create_learning_run(conn, topic_id=1, publication_id=None, input_hash="h1")
        runs = list_learning_runs(conn)
        assert len(runs) == 1


class TestRecommendationCRUD:
    def test_create_recommendation_returns_id(self, conn):
        run_id = create_learning_run(conn, topic_id=1, publication_id=None, input_hash="h1")
        draft = _make_draft(run_id)
        rec_id = create_recommendation(conn, draft)
        conn.commit()
        assert isinstance(rec_id, int)
        assert rec_id > 0

    def test_get_recommendation_after_create(self, conn):
        run_id = create_learning_run(conn, topic_id=1, publication_id=None, input_hash="h1")
        draft = _make_draft(run_id)
        rec_id = create_recommendation(conn, draft)
        conn.commit()
        rec = get_recommendation(conn, rec_id)
        assert rec.id == rec_id
        assert rec.domain == "scripts"
        assert rec.subsystem == "hook_effectiveness"
        assert rec.status == "pending"

    def test_get_recommendation_not_found_raises(self, conn):
        with pytest.raises(RecommendationNotFoundError):
            get_recommendation(conn, 99999)

    def test_evidence_roundtripped_through_json(self, conn):
        run_id = create_learning_run(conn, topic_id=1, publication_id=None, input_hash="h1")
        draft = _make_draft(run_id)
        rec_id = create_recommendation(conn, draft)
        conn.commit()
        rec = get_recommendation(conn, rec_id)
        assert len(rec.evidence) == 1
        ev = rec.evidence[0]
        assert ev.metric_name == "ctr"
        assert ev.snapshot_ids == [1, 2]

    def test_confidence_score_stored(self, conn):
        run_id = create_learning_run(conn, topic_id=1, publication_id=None, input_hash="h1")
        draft = _make_draft(run_id, confidence_score=0.75, confidence="high")
        rec_id = create_recommendation(conn, draft)
        conn.commit()
        rec = get_recommendation(conn, rec_id)
        assert rec.confidence == "high"
        assert abs(rec.confidence_score - 0.75) < 0.001

    def test_publication_id_none_allowed(self, conn):
        run_id = create_learning_run(conn, topic_id=1, publication_id=None, input_hash="h1")
        draft = _make_draft(run_id, publication_id=None)
        rec_id = create_recommendation(conn, draft)
        conn.commit()
        rec = get_recommendation(conn, rec_id)
        assert rec.publication_id is None

    def test_update_recommendation_status(self, conn):
        run_id = create_learning_run(conn, topic_id=1, publication_id=None, input_hash="h1")
        draft = _make_draft(run_id)
        rec_id = create_recommendation(conn, draft)
        conn.commit()
        update_recommendation_status(conn, rec_id, "accepted")
        conn.commit()
        rec = get_recommendation(conn, rec_id)
        assert rec.status == "accepted"

    def test_supersede_recommendation(self, conn):
        run_id = create_learning_run(conn, topic_id=1, publication_id=None, input_hash="h1")
        old_id = create_recommendation(conn, _make_draft(run_id, input_hash="old"))
        new_id = create_recommendation(conn, _make_draft(run_id, input_hash="new"))
        conn.commit()
        supersede_recommendation(conn, old_id, new_id)
        conn.commit()
        old_rec = get_recommendation(conn, old_id)
        assert old_rec.status == "superseded"
        assert old_rec.superseded_by_id == new_id
        assert old_rec.superseded_at is not None

    def test_list_recommendations_filter_by_status(self, conn):
        run_id = create_learning_run(conn, topic_id=1, publication_id=None, input_hash="h1")
        create_recommendation(conn, _make_draft(run_id, input_hash="a"))
        rec2_id = create_recommendation(conn, _make_draft(run_id, input_hash="b"))
        conn.commit()
        update_recommendation_status(conn, rec2_id, "accepted")
        conn.commit()
        pending = list_recommendations(conn, status="pending")
        accepted = list_recommendations(conn, status="accepted")
        assert len(pending) == 1
        assert len(accepted) == 1

    def test_evidence_classification_defaults_to_observational(self, conn):
        run_id = create_learning_run(conn, topic_id=1, publication_id=None, input_hash="h1")
        draft = _make_draft(run_id)
        rec_id = create_recommendation(conn, draft)
        conn.commit()
        rec = get_recommendation(conn, rec_id)
        assert rec.evidence_classification == "observational"

    def test_evidence_classification_stored_explicitly(self, conn):
        run_id = create_learning_run(conn, topic_id=1, publication_id=None, input_hash="h1")
        draft = _make_draft(run_id, evidence_classification="controlled_experiment")
        rec_id = create_recommendation(conn, draft)
        conn.commit()
        rec = get_recommendation(conn, rec_id)
        assert rec.evidence_classification == "controlled_experiment"

    def test_list_recommendations_filter_by_domain(self, conn):
        run_id = create_learning_run(conn, topic_id=1, publication_id=None, input_hash="h1")
        create_recommendation(conn, _make_draft(run_id, domain="scripts", input_hash="a"))
        create_recommendation(conn, _make_draft(run_id, domain="publishing", input_hash="b",
                                                subsystem="title_effectiveness"))
        conn.commit()
        scripts = list_recommendations(conn, domain="scripts")
        publishing = list_recommendations(conn, domain="publishing")
        assert len(scripts) == 1
        assert len(publishing) == 1


class TestReviewEventCRUD:
    def test_create_and_retrieve_review_event(self, conn):
        run_id = create_learning_run(conn, topic_id=1, publication_id=None, input_hash="h1")
        rec_id = create_recommendation(conn, _make_draft(run_id))
        conn.commit()

        draft = ReviewEventDraft(
            recommendation_id=rec_id,
            topic_id=1,
            event_type="accepted",
            reviewer="operator",
            notes="Looks good",
            expected_outcome="CTR will improve",
            input_hash="ev_hash_1",
        )
        ev_id = create_review_event(conn, draft)
        conn.commit()

        events = list_review_events(conn, recommendation_id=rec_id)
        assert len(events) == 1
        ev = events[0]
        assert ev.id == ev_id
        assert ev.event_type == "accepted"
        assert ev.reviewer == "operator"

    def test_multiple_review_events_for_same_recommendation(self, conn):
        run_id = create_learning_run(conn, topic_id=1, publication_id=None, input_hash="h1")
        rec_id = create_recommendation(conn, _make_draft(run_id))
        conn.commit()

        for i in range(3):
            draft = ReviewEventDraft(
                recommendation_id=rec_id,
                topic_id=1,
                event_type="noted",
                reviewer=f"user{i}",
                input_hash=f"h{i}",
            )
            create_review_event(conn, draft)
        conn.commit()

        events = list_review_events(conn, recommendation_id=rec_id)
        assert len(events) == 3

    def test_list_events_by_topic_id(self, conn):
        run_id = create_learning_run(conn, topic_id=1, publication_id=None, input_hash="h1")
        rec_id = create_recommendation(conn, _make_draft(run_id))
        conn.commit()

        draft = ReviewEventDraft(
            recommendation_id=rec_id,
            topic_id=1,
            event_type="noted",
            input_hash="h",
        )
        create_review_event(conn, draft)
        conn.commit()

        events = list_review_events(conn, topic_id=1)
        assert len(events) == 1

    def test_empty_list_when_no_events(self, conn):
        events = list_review_events(conn)
        assert events == []
