"""Tests for Phase 11 learning constants."""

from __future__ import annotations

from app.learning.constants import (
    CONFIDENCE_HIGH,
    CONFIDENCE_HIGH_THRESHOLD,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    CONFIDENCE_MEDIUM_THRESHOLD,
    DOMAIN_SUBSYSTEMS,
    LEARNING_ALGORITHM_VERSION,
    LEARNING_ENGINE_VERSION,
    LEARNING_SCHEMA_VERSION,
    RECOMMENDATION_DOMAINS,
    RECOMMENDATION_SUBSYSTEMS,
    STATUS_ACCEPTED,
    STATUS_PENDING,
    STATUS_REJECTED,
    STATUS_SUPERSEDED,
    confidence_label,
)


class TestVersionConstants:
    def test_engine_version_is_string(self):
        assert isinstance(LEARNING_ENGINE_VERSION, str)
        assert LEARNING_ENGINE_VERSION

    def test_schema_version_is_string(self):
        assert isinstance(LEARNING_SCHEMA_VERSION, str)

    def test_algorithm_version_is_string(self):
        assert isinstance(LEARNING_ALGORITHM_VERSION, str)


class TestDomains:
    def test_domains_is_frozenset(self):
        assert isinstance(RECOMMENDATION_DOMAINS, frozenset)

    def test_all_expected_domains_present(self):
        expected = {
            "topics",
            "research",
            "scripts",
            "narration",
            "captions",
            "scenes",
            "media",
            "publishing",
            "analytics",
        }
        assert expected == RECOMMENDATION_DOMAINS

    def test_domain_subsystems_all_present(self):
        for domain in RECOMMENDATION_DOMAINS:
            assert domain in DOMAIN_SUBSYSTEMS
            assert len(DOMAIN_SUBSYSTEMS[domain]) > 0

    def test_all_subsystems_covered_by_domain_map(self):
        all_mapped = set()
        for subsystems in DOMAIN_SUBSYSTEMS.values():
            all_mapped |= subsystems
        assert all_mapped == RECOMMENDATION_SUBSYSTEMS


class TestSubsystems:
    def test_subsystems_is_frozenset(self):
        assert isinstance(RECOMMENDATION_SUBSYSTEMS, frozenset)

    def test_subsystems_not_empty(self):
        assert len(RECOMMENDATION_SUBSYSTEMS) > 0

    def test_key_subsystems_present(self):
        from app.learning.constants import (
            SUBSYSTEM_HOOK_EFFECTIVENESS,
            SUBSYSTEM_NARRATION_PACE,
            SUBSYSTEM_TITLE_EFFECTIVENESS,
            SUBSYSTEM_TOPIC_SELECTION,
        )

        assert SUBSYSTEM_TOPIC_SELECTION in RECOMMENDATION_SUBSYSTEMS
        assert SUBSYSTEM_HOOK_EFFECTIVENESS in RECOMMENDATION_SUBSYSTEMS
        assert SUBSYSTEM_NARRATION_PACE in RECOMMENDATION_SUBSYSTEMS
        assert SUBSYSTEM_TITLE_EFFECTIVENESS in RECOMMENDATION_SUBSYSTEMS


class TestConfidenceLabel:
    def test_zero_is_low(self):
        assert confidence_label(0.0) == CONFIDENCE_LOW

    def test_below_medium_threshold_is_low(self):
        assert confidence_label(CONFIDENCE_MEDIUM_THRESHOLD - 0.001) == CONFIDENCE_LOW

    def test_at_medium_threshold_is_medium(self):
        assert confidence_label(CONFIDENCE_MEDIUM_THRESHOLD) == CONFIDENCE_MEDIUM

    def test_between_thresholds_is_medium(self):
        mid = (CONFIDENCE_MEDIUM_THRESHOLD + CONFIDENCE_HIGH_THRESHOLD) / 2
        assert confidence_label(mid) == CONFIDENCE_MEDIUM

    def test_at_high_threshold_is_high(self):
        assert confidence_label(CONFIDENCE_HIGH_THRESHOLD) == CONFIDENCE_HIGH

    def test_one_is_high(self):
        assert confidence_label(1.0) == CONFIDENCE_HIGH


class TestStatuses:
    def test_statuses_are_strings(self):
        for s in (STATUS_PENDING, STATUS_ACCEPTED, STATUS_REJECTED, STATUS_SUPERSEDED):
            assert isinstance(s, str)

    def test_statuses_are_distinct(self):
        statuses = [STATUS_PENDING, STATUS_ACCEPTED, STATUS_REJECTED, STATUS_SUPERSEDED]
        assert len(set(statuses)) == len(statuses)


class TestThresholds:
    def test_ctr_thresholds_ordered(self):
        from app.learning.constants import CTR_HIGH_THRESHOLD, CTR_LOW_THRESHOLD

        assert CTR_LOW_THRESHOLD < CTR_HIGH_THRESHOLD

    def test_retention_thresholds_ordered(self):
        from app.learning.constants import (
            RETENTION_HIGH_THRESHOLD_S,
            RETENTION_LOW_THRESHOLD_S,
        )

        assert RETENTION_LOW_THRESHOLD_S < RETENTION_HIGH_THRESHOLD_S

    def test_engagement_threshold_positive(self):
        from app.learning.constants import ENGAGEMENT_LOW_THRESHOLD

        assert ENGAGEMENT_LOW_THRESHOLD > 0

    def test_min_snapshot_counts_ordered(self):
        from app.learning.constants import (
            MIN_SNAPSHOTS_HIGH_CONFIDENCE,
            MIN_SNAPSHOTS_MEDIUM_CONFIDENCE,
        )

        assert MIN_SNAPSHOTS_MEDIUM_CONFIDENCE < MIN_SNAPSHOTS_HIGH_CONFIDENCE
