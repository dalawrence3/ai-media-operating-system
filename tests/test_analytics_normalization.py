"""Tests for analytics normalization utilities."""

from __future__ import annotations

import pytest

from app.analytics.errors import NormalizationError, UnknownMetricError
from app.analytics.normalization import (
    filter_none_metrics,
    merge_normalized,
    safe_float,
    validate_canonical_metrics,
)


class TestValidateCanonicalMetrics:
    def test_valid_metrics_pass(self):
        validate_canonical_metrics({"views": 100.0, "likes": 50.0})

    def test_unknown_metric_raises(self):
        with pytest.raises(UnknownMetricError):
            validate_canonical_metrics({"unknownField": 1.0})

    def test_empty_dict_passes(self):
        validate_canonical_metrics({})

    def test_mixed_valid_invalid_raises(self):
        with pytest.raises(UnknownMetricError):
            validate_canonical_metrics({"views": 1.0, "notAMetric": 2.0})


class TestSafeFloat:
    def test_int_value(self):
        assert safe_float(42, "f") == 42.0

    def test_float_value(self):
        assert safe_float(3.14, "f") == pytest.approx(3.14)

    def test_none_returns_none(self):
        assert safe_float(None, "f") is None

    def test_string_number(self):
        assert safe_float("99.5", "f") == pytest.approx(99.5)

    def test_invalid_string_raises(self):
        with pytest.raises(NormalizationError):
            safe_float("notanumber", "bad_field")

    def test_non_numeric_type_raises(self):
        with pytest.raises(NormalizationError):
            safe_float([], "arr_field")


class TestMergeNormalized:
    def test_empty(self):
        assert merge_normalized() == {}

    def test_single_dict(self):
        assert merge_normalized({"views": 1.0}) == {"views": 1.0}

    def test_last_writer_wins(self):
        result = merge_normalized({"views": 1.0}, {"views": 2.0})
        assert result["views"] == 2.0

    def test_merges_distinct_keys(self):
        result = merge_normalized({"views": 1.0}, {"likes": 5.0})
        assert result == {"views": 1.0, "likes": 5.0}


class TestFilterNoneMetrics:
    def test_removes_none_values(self):
        result = filter_none_metrics({"views": 10.0, "likes": None})
        assert result == {"views": 10.0}
        assert "likes" not in result

    def test_keeps_zero(self):
        result = filter_none_metrics({"views": 0.0})
        assert result == {"views": 0.0}

    def test_empty_input(self):
        assert filter_none_metrics({}) == {}
