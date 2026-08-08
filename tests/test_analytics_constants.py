"""Tests for analytics constants module."""

from __future__ import annotations

from app.analytics.constants import (
    AGG_LAST,
    AGG_SUM,
    ANALYTICS_ENGINE_VERSION,
    CANONICAL_METRICS,
    METRIC_AGGREGATION_OP,
    METRIC_CTR,
    METRIC_KIND,
    METRIC_KIND_ADDITIVE,
    METRIC_KIND_GAUGE,
    METRIC_KIND_MONETARY,
    METRIC_KIND_RATIO,
    METRIC_LIKES,
    METRIC_REVENUE_ESTIMATE,
    METRIC_VIEWS,
    METRIC_WATCH_TIME_SECONDS,
    PERIOD_TYPES,
    REVIEW_SEVERITIES,
    SEVERITY_REQUIRES_NOTES,
)


class TestCanonicalMetrics:
    def test_views_in_canonical(self):
        assert METRIC_VIEWS in CANONICAL_METRICS

    def test_watch_time_in_canonical(self):
        assert METRIC_WATCH_TIME_SECONDS in CANONICAL_METRICS

    def test_ctr_in_canonical(self):
        assert METRIC_CTR in CANONICAL_METRICS

    def test_all_canonical_are_strings(self):
        for m in CANONICAL_METRICS:
            assert isinstance(m, str)

    def test_canonical_is_nonempty(self):
        assert len(CANONICAL_METRICS) > 10

    def test_estimated_minutes_watched_removed(self):
        assert "estimated_minutes_watched" not in CANONICAL_METRICS

    def test_revenue_currency_removed(self):
        assert "revenue_currency" not in CANONICAL_METRICS


class TestPeriodTypes:
    def test_all_four_periods_present(self):
        assert {"daily", "weekly", "monthly", "lifetime"} == PERIOD_TYPES

    def test_is_frozenset(self):
        assert isinstance(PERIOD_TYPES, frozenset)


class TestSeverities:
    def test_standard_severities_present(self):
        assert {"info", "warning", "error", "critical", "other"} == REVIEW_SEVERITIES

    def test_other_requires_notes(self):
        assert "other" in SEVERITY_REQUIRES_NOTES

    def test_info_does_not_require_notes(self):
        assert "info" not in SEVERITY_REQUIRES_NOTES


class TestAggregationOps:
    def test_views_sums(self):
        assert METRIC_AGGREGATION_OP[METRIC_VIEWS] == AGG_SUM

    def test_ctr_uses_last(self):
        assert METRIC_AGGREGATION_OP[METRIC_CTR] == AGG_LAST

    def test_likes_uses_last(self):
        assert METRIC_AGGREGATION_OP[METRIC_LIKES] == AGG_LAST

    def test_revenue_estimate_sums(self):
        assert METRIC_AGGREGATION_OP[METRIC_REVENUE_ESTIMATE] == AGG_SUM

    def test_all_canonical_have_op(self):
        for name in CANONICAL_METRICS:
            assert name in METRIC_AGGREGATION_OP, f"{name} missing aggregation op"

    def test_engine_version_is_string(self):
        assert isinstance(ANALYTICS_ENGINE_VERSION, str)
        assert len(ANALYTICS_ENGINE_VERSION) > 0

    def test_only_sum_and_last_ops_used(self):
        valid_ops = {AGG_SUM, AGG_LAST}
        for name, op in METRIC_AGGREGATION_OP.items():
            assert op in valid_ops, f"{name} uses unexpected op {op!r}"


class TestMetricKind:
    def test_views_is_additive(self):
        assert METRIC_KIND[METRIC_VIEWS] == METRIC_KIND_ADDITIVE

    def test_ctr_is_ratio(self):
        assert METRIC_KIND[METRIC_CTR] == METRIC_KIND_RATIO

    def test_likes_is_gauge(self):
        assert METRIC_KIND[METRIC_LIKES] == METRIC_KIND_GAUGE

    def test_revenue_estimate_is_monetary(self):
        assert METRIC_KIND[METRIC_REVENUE_ESTIMATE] == METRIC_KIND_MONETARY

    def test_all_canonical_have_kind(self):
        for name in CANONICAL_METRICS:
            assert name in METRIC_KIND, f"{name} missing metric kind"

    def test_all_kinds_are_known_values(self):
        known = {METRIC_KIND_ADDITIVE, METRIC_KIND_GAUGE, METRIC_KIND_RATIO, METRIC_KIND_MONETARY}
        for name, kind in METRIC_KIND.items():
            assert kind in known, f"{name} has unexpected kind {kind!r}"
