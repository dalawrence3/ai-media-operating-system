"""Tests for Phase 11 hashing — determinism, uniqueness, and collision resistance."""

from __future__ import annotations

from app.learning.hashing import (
    LearningRunHashInput,
    RecommendationHashInput,
    ReviewEventHashInput,
    compute_learning_run_hash,
    compute_recommendation_hash,
    compute_review_event_hash,
)


class TestLearningRunHash:
    def _make_input(self, **overrides) -> LearningRunHashInput:
        defaults = dict(
            topic_id=1,
            publication_id=10,
            engine_version="1.0.0",
            schema_version="1.0.0",
            algorithm_version="learning-v1",
            snapshot_ids_sorted=[1, 2, 3],
        )
        defaults.update(overrides)
        return LearningRunHashInput(**defaults)

    def test_deterministic(self):
        inp = self._make_input()
        assert compute_learning_run_hash(inp) == compute_learning_run_hash(inp)

    def test_returns_hex_string(self):
        result = compute_learning_run_hash(self._make_input())
        assert isinstance(result, str)
        assert len(result) == 64
        int(result, 16)  # must be valid hex

    def test_different_topic_ids_produce_different_hashes(self):
        h1 = compute_learning_run_hash(self._make_input(topic_id=1))
        h2 = compute_learning_run_hash(self._make_input(topic_id=2))
        assert h1 != h2

    def test_different_publication_ids_produce_different_hashes(self):
        h1 = compute_learning_run_hash(self._make_input(publication_id=10))
        h2 = compute_learning_run_hash(self._make_input(publication_id=20))
        assert h1 != h2

    def test_none_publication_id_distinct_from_int(self):
        h1 = compute_learning_run_hash(self._make_input(publication_id=None))
        h2 = compute_learning_run_hash(self._make_input(publication_id=10))
        assert h1 != h2

    def test_snapshot_id_order_is_normalized(self):
        h1 = compute_learning_run_hash(self._make_input(snapshot_ids_sorted=[1, 2, 3]))
        h2 = compute_learning_run_hash(self._make_input(snapshot_ids_sorted=[3, 2, 1]))
        assert h1 == h2

    def test_different_snapshot_ids_produce_different_hashes(self):
        h1 = compute_learning_run_hash(self._make_input(snapshot_ids_sorted=[1, 2]))
        h2 = compute_learning_run_hash(self._make_input(snapshot_ids_sorted=[1, 3]))
        assert h1 != h2

    def test_engine_version_change_changes_hash(self):
        h1 = compute_learning_run_hash(self._make_input(engine_version="1.0.0"))
        h2 = compute_learning_run_hash(self._make_input(engine_version="2.0.0"))
        assert h1 != h2


class TestRecommendationHash:
    def _make_input(self, **overrides) -> RecommendationHashInput:
        defaults = dict(
            learning_run_id=1,
            topic_id=1,
            publication_id=10,
            domain="scripts",
            subsystem="hook_effectiveness",
            measure="ctr",
            engine_version="1.0.0",
            schema_version="1.0.0",
            algorithm_version="learning-v1",
            evidence_classification="observational",
            recommendation_strength="exploratory",
            evidence_snapshot_ids_sorted=[1, 2],
        )
        defaults.update(overrides)
        return RecommendationHashInput(**defaults)

    def test_deterministic(self):
        inp = self._make_input()
        assert compute_recommendation_hash(inp) == compute_recommendation_hash(inp)

    def test_returns_64_char_hex(self):
        result = compute_recommendation_hash(self._make_input())
        assert len(result) == 64

    def test_domain_change_changes_hash(self):
        h1 = compute_recommendation_hash(self._make_input(domain="scripts"))
        h2 = compute_recommendation_hash(self._make_input(domain="publishing"))
        assert h1 != h2

    def test_subsystem_change_changes_hash(self):
        h1 = compute_recommendation_hash(self._make_input(subsystem="hook_effectiveness"))
        h2 = compute_recommendation_hash(self._make_input(subsystem="title_effectiveness"))
        assert h1 != h2

    def test_evidence_snapshot_order_normalized(self):
        h1 = compute_recommendation_hash(self._make_input(evidence_snapshot_ids_sorted=[1, 2]))
        h2 = compute_recommendation_hash(self._make_input(evidence_snapshot_ids_sorted=[2, 1]))
        assert h1 == h2

    def test_run_id_change_changes_hash(self):
        h1 = compute_recommendation_hash(self._make_input(learning_run_id=1))
        h2 = compute_recommendation_hash(self._make_input(learning_run_id=2))
        assert h1 != h2

    def test_evidence_classification_change_changes_hash(self):
        h1 = compute_recommendation_hash(self._make_input(evidence_classification="observational"))
        h2 = compute_recommendation_hash(
            self._make_input(evidence_classification="controlled_experiment")
        )
        assert h1 != h2


class TestReviewEventHash:
    def _make_input(self, **overrides) -> ReviewEventHashInput:
        defaults = dict(
            recommendation_id=1,
            topic_id=1,
            event_type="accepted",
            reviewer="operator",
        )
        defaults.update(overrides)
        return ReviewEventHashInput(**defaults)

    def test_deterministic(self):
        inp = self._make_input()
        assert compute_review_event_hash(inp) == compute_review_event_hash(inp)

    def test_returns_64_char_hex(self):
        assert len(compute_review_event_hash(self._make_input())) == 64

    def test_event_type_change_changes_hash(self):
        h1 = compute_review_event_hash(self._make_input(event_type="accepted"))
        h2 = compute_review_event_hash(self._make_input(event_type="rejected"))
        assert h1 != h2

    def test_reviewer_change_changes_hash(self):
        h1 = compute_review_event_hash(self._make_input(reviewer="alice"))
        h2 = compute_review_event_hash(self._make_input(reviewer="bob"))
        assert h1 != h2

    def test_recommendation_id_change_changes_hash(self):
        h1 = compute_review_event_hash(self._make_input(recommendation_id=1))
        h2 = compute_review_event_hash(self._make_input(recommendation_id=2))
        assert h1 != h2
