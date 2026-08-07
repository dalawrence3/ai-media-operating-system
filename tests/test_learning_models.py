"""Tests for Phase 11 domain models."""

from __future__ import annotations

import json

from app.learning.models import EvidenceItem, RecommendationDraft, ReviewEventDraft


class TestEvidenceItem:
    def _make(self, **kw) -> EvidenceItem:
        defaults = dict(
            metric_name="ctr",
            observed_value=0.015,
            comparison_value=0.02,
            period_type="lifetime",
            period_key="lifetime",
            snapshot_ids=[1, 2],
            interpretation="CTR is below threshold",
        )
        defaults.update(kw)
        return EvidenceItem(**defaults)

    def test_to_dict_roundtrip(self):
        ev = self._make()
        d = ev.to_dict()
        ev2 = EvidenceItem.from_dict(d)
        assert ev2.metric_name == ev.metric_name
        assert ev2.observed_value == ev.observed_value
        assert ev2.comparison_value == ev.comparison_value
        assert ev2.snapshot_ids == ev.snapshot_ids

    def test_comparison_value_can_be_none(self):
        ev = self._make(comparison_value=None)
        d = ev.to_dict()
        ev2 = EvidenceItem.from_dict(d)
        assert ev2.comparison_value is None

    def test_to_dict_is_json_serializable(self):
        ev = self._make()
        payload = json.dumps(ev.to_dict())
        assert isinstance(payload, str)

    def test_from_dict_with_missing_optional_fields(self):
        d = {
            "metric_name": "views",
            "observed_value": 100.0,
            "comparison_value": None,
            "period_type": "lifetime",
            "period_key": "lifetime",
            "interpretation": "test",
        }
        ev = EvidenceItem.from_dict(d)
        assert ev.snapshot_ids == []

    def test_snapshot_ids_preserved(self):
        ev = self._make(snapshot_ids=[10, 20, 30])
        assert EvidenceItem.from_dict(ev.to_dict()).snapshot_ids == [10, 20, 30]


class TestRecommendationDraft:
    def test_default_evidence_is_empty_list(self):
        draft = RecommendationDraft(
            learning_run_id=1,
            topic_id=1,
            publication_id=1,
            domain="scripts",
            subsystem="hook_effectiveness",
            measure="ctr",
            title="Test",
            explanation="Test explanation",
            expected_improvement="Test improvement",
        )
        assert draft.evidence == []
        assert draft.confidence == "low"
        assert draft.confidence_score == 0.0

    def test_evidence_field_accepts_items(self):
        ev = EvidenceItem(
            metric_name="ctr",
            observed_value=0.01,
            comparison_value=0.02,
            period_type="lifetime",
            period_key="lifetime",
            snapshot_ids=[1],
            interpretation="low",
        )
        draft = RecommendationDraft(
            learning_run_id=1,
            topic_id=1,
            publication_id=1,
            domain="scripts",
            subsystem="hook_effectiveness",
            measure="ctr",
            title="T",
            explanation="E",
            expected_improvement="I",
            evidence=[ev],
        )
        assert len(draft.evidence) == 1


class TestReviewEventDraft:
    def test_defaults(self):
        draft = ReviewEventDraft(
            recommendation_id=1,
            topic_id=1,
            event_type="accepted",
        )
        assert draft.reviewer == ""
        assert draft.notes == ""
        assert draft.expected_outcome == ""
        assert draft.input_hash == ""
