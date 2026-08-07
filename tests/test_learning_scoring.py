"""Tests for Phase 11 confidence scoring."""

from __future__ import annotations

import pytest

from app.learning.constants import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
)
from app.learning.models import EvidenceItem
from app.learning.scoring import (
    _consistency_score,
    _effect_score,
    _volume_score,
    compute_confidence,
    compute_confidence_from_evidence,
    meets_minimum_confidence,
)


def _make_ev(metric_name="ctr", snapshot_ids=None, period_key="lifetime") -> EvidenceItem:
    return EvidenceItem(
        metric_name=metric_name,
        observed_value=0.01,
        comparison_value=0.02,
        period_type="lifetime",
        period_key=period_key,
        snapshot_ids=snapshot_ids or [1],
        interpretation="test",
    )


class TestVolumeScore:
    def test_zero_snapshots_returns_zero(self):
        assert _volume_score(0) == 0.0

    def test_one_snapshot_is_positive(self):
        assert _volume_score(1) > 0.0

    def test_more_snapshots_higher_score(self):
        assert _volume_score(2) > _volume_score(1)
        assert _volume_score(5) > _volume_score(2)

    def test_many_snapshots_does_not_exceed_one(self):
        assert _volume_score(1000) <= 1.0

    def test_score_capped_at_one(self):
        assert _volume_score(100) == 1.0


class TestEffectScore:
    def test_at_threshold_returns_zero_below(self):
        assert _effect_score(0.02, 0.02, "below") == 0.0

    def test_below_threshold_returns_positive(self):
        assert _effect_score(0.01, 0.02, "below") > 0.0

    def test_zero_threshold_returns_half(self):
        assert _effect_score(0.0, 0.0, "below") == 0.5

    def test_above_direction(self):
        assert _effect_score(0.1, 0.05, "above") > 0.0

    def test_deeply_below_threshold_approaches_one(self):
        score = _effect_score(0.001, 1.0, "below")
        assert score > 0.9

    def test_effect_score_capped_at_one(self):
        assert _effect_score(-100.0, 1.0, "below") == 1.0


class TestConsistencyScore:
    def test_empty_evidence_returns_zero(self):
        assert _consistency_score([]) == 0.0

    def test_single_evidence_returns_zero(self):
        # One period cannot demonstrate consistency — must be 0.0, not a neutral 0.5.
        assert _consistency_score([_make_ev()]) == 0.0

    def test_multiple_evidence_with_different_periods_increases_score(self):
        ev1 = _make_ev(period_key="2026-W31")
        ev2 = _make_ev(period_key="2026-W32")
        score = _consistency_score([ev1, ev2])
        assert score > 0.0

    def test_duplicate_period_keys_reduces_uniqueness_score(self):
        ev1 = _make_ev(period_key="lifetime")
        ev2 = _make_ev(period_key="lifetime")
        score = _consistency_score([ev1, ev2])
        assert score < 0.9


class TestComputeConfidence:
    def test_returns_tuple_score_and_label(self):
        ev = _make_ev(snapshot_ids=[1, 2, 3])
        score, label = compute_confidence([ev], 0.01, 0.02, "below")
        assert isinstance(score, float)
        assert label in {CONFIDENCE_LOW, CONFIDENCE_MEDIUM, CONFIDENCE_HIGH}

    def test_score_in_range(self):
        ev = _make_ev(snapshot_ids=[1])
        score, _ = compute_confidence([ev], 0.01, 0.02, "below")
        assert 0.0 <= score <= 1.0

    def test_more_snapshots_higher_score(self):
        ev_few = _make_ev(snapshot_ids=[1])
        ev_many = _make_ev(snapshot_ids=list(range(10)))
        score_few, _ = compute_confidence([ev_few], 0.01, 0.02, "below")
        score_many, _ = compute_confidence([ev_many], 0.01, 0.02, "below")
        assert score_many > score_few

    def test_deterministic(self):
        ev = _make_ev(snapshot_ids=[1, 2])
        r1 = compute_confidence([ev], 0.01, 0.02, "below")
        r2 = compute_confidence([ev], 0.01, 0.02, "below")
        assert r1 == r2

    def test_empty_evidence_returns_low(self):
        score, label = compute_confidence([], 0.01, 0.02, "below")
        assert label == CONFIDENCE_LOW


class TestComputeConfidenceFromEvidence:
    def test_returns_valid_tuple(self):
        ev = _make_ev(snapshot_ids=[1, 2])
        score, label = compute_confidence_from_evidence([ev])
        assert 0.0 <= score <= 1.0
        assert label in {CONFIDENCE_LOW, CONFIDENCE_MEDIUM, CONFIDENCE_HIGH}

    def test_empty_evidence_returns_low(self):
        _, label = compute_confidence_from_evidence([])
        assert label == CONFIDENCE_LOW


class TestMeetsMinimumConfidence:
    def test_low_always_passes(self):
        assert meets_minimum_confidence(0, "low") is True

    def test_medium_requires_at_least_two(self):
        assert meets_minimum_confidence(1, "medium") is False
        assert meets_minimum_confidence(2, "medium") is True

    def test_high_requires_at_least_five(self):
        assert meets_minimum_confidence(4, "high") is False
        assert meets_minimum_confidence(5, "high") is True


# ── Pathological-input tests ──────────────────────────────────────────────────


class TestComputeConfidencePathological:
    def test_zero_evidence_returns_zero_score(self):
        score, label = compute_confidence([], 0.01, 0.02, "below")
        assert score == 0.0
        assert label == CONFIDENCE_LOW

    def test_single_observation_cannot_reach_high(self):
        ev = _make_ev(snapshot_ids=[1])
        score, label = compute_confidence([ev], 0.0, 1.0, "below")
        assert label != CONFIDENCE_HIGH, (
            f"Single-period observation must not reach HIGH confidence; got score={score}"
        )

    def test_100_duplicated_copies_of_one_snapshot_do_not_inflate(self):
        # All 100 items reference the same snapshot ID — volume must be 1.
        ev = _make_ev(snapshot_ids=[42])
        evidence = [ev] * 100
        score, _ = compute_confidence(evidence, 0.0, 1.0, "below")
        # Compare to genuine 100-snapshot score
        ev_many = _make_ev(snapshot_ids=list(range(100)))
        score_real, _ = compute_confidence([ev_many], 0.0, 1.0, "below")
        assert score < score_real, (
            "Duplicated snapshot IDs must not produce the same confidence as 100 unique IDs"
        )

    def test_two_independent_periods_scores_higher_than_one(self):
        ev1 = _make_ev(snapshot_ids=[1], period_key="2026-W31")
        ev2 = _make_ev(snapshot_ids=[2], period_key="2026-W32")
        score_two, _ = compute_confidence([ev1, ev2], 0.0, 1.0, "below")
        score_one, _ = compute_confidence([ev1], 0.0, 1.0, "below")
        assert score_two > score_one

    def test_duplicate_period_keys_not_double_counted(self):
        ev1 = _make_ev(snapshot_ids=[1], period_key="lifetime")
        ev2 = _make_ev(snapshot_ids=[2], period_key="lifetime")
        score_dup, _ = compute_confidence([ev1, ev2], 0.0, 1.0, "below")
        ev_diff = _make_ev(snapshot_ids=[2], period_key="2026-W32")
        score_div, _ = compute_confidence([ev1, ev_diff], 0.0, 1.0, "below")
        assert score_dup < score_div, (
            "Two items with the same period_key must score lower than two independent periods"
        )

    def test_very_large_effect_magnitude_capped_at_one(self):
        ev = _make_ev(snapshot_ids=[1])
        score, _ = compute_confidence([ev], -1e9, 1.0, "below")
        assert score <= 1.0

    def test_zero_threshold_does_not_raise(self):
        ev = _make_ev(snapshot_ids=[1])
        score, label = compute_confidence([ev], 0.0, 0.0, "below")
        assert 0.0 <= score <= 1.0
        assert label in {CONFIDENCE_LOW, CONFIDENCE_MEDIUM, CONFIDENCE_HIGH}

    def test_score_bounded_between_zero_and_one(self):
        for snap_count in [0, 1, 2, 5, 100]:
            ev = _make_ev(snapshot_ids=list(range(snap_count)) or [1])
            score, _ = compute_confidence([ev], 0.0, 1.0, "below")
            assert 0.0 <= score <= 1.0, f"Score {score} out of bounds for snap_count={snap_count}"

    def test_empty_evidence_from_evidence_variant_returns_zero(self):
        score, label = compute_confidence_from_evidence([])
        assert score == 0.0
        assert label == CONFIDENCE_LOW

    @pytest.mark.parametrize("direction", ["below", "above"])
    def test_direction_agnostic_bounds(self, direction):
        ev = _make_ev(snapshot_ids=[1, 2, 3])
        score, _ = compute_confidence([ev], 0.5, 0.5, direction)
        assert 0.0 <= score <= 1.0


class TestConsistencyScorePathological:
    def test_100_items_same_period_key(self):
        ev = _make_ev(period_key="lifetime")
        score = _consistency_score([ev] * 100)
        # All same period: score = 0.3 + 0.7 * (1/100) = 0.307
        assert score < 0.5

    def test_two_unique_periods(self):
        ev1 = _make_ev(period_key="a")
        ev2 = _make_ev(period_key="b")
        score = _consistency_score([ev1, ev2])
        # 2 unique / 2 total = 1.0; score = 0.3 + 0.7 = 1.0
        assert score == 1.0
