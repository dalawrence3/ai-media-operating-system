"""Tests for deterministic date-review risk signals (Phase 4.2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.research.claim_risk import compute_requires_date_review
from app.research.constants import STAT_REVIEW_THRESHOLD_DAYS, TIME_SENSITIVE_REVIEW_DAYS
from app.research.models import ClaimType, ExtractionStatus, FetchStatus, SourceContent


def _sc(
    *,
    published_at: str | None = None,
    suspected_truncation: bool = False,
    age_days: int | None = None,
) -> SourceContent:
    """Build a minimal SourceContent for testing."""
    if age_days is not None:
        pub = (datetime.now(UTC) - timedelta(days=age_days)).strftime("%Y-%m-%d")
        published_at = pub
    return SourceContent(
        source_id=1,
        fetch_status=FetchStatus.ok,
        extraction_status=ExtractionStatus.ok,
        fetched_at=datetime.now(UTC),
        suspected_truncation=suspected_truncation,
        published_at=published_at,
    )


class TestStatisticalClaimRules:
    def test_rule1_statistical_no_published_at(self):
        sc = _sc(published_at=None)
        assert compute_requires_date_review("Unemployment was 5%.", ClaimType.statistical, sc)

    def test_rule2_statistical_old_source(self):
        sc = _sc(age_days=STAT_REVIEW_THRESHOLD_DAYS + 1)
        assert compute_requires_date_review("GDP grew by 3%.", ClaimType.statistical, sc)

    def test_rule2_statistical_recent_source_not_flagged(self):
        sc = _sc(age_days=30)
        assert not compute_requires_date_review("GDP grew by 3%.", ClaimType.statistical, sc)

    def test_rule2_exactly_at_threshold_not_flagged(self):
        sc = _sc(age_days=STAT_REVIEW_THRESHOLD_DAYS)
        assert not compute_requires_date_review("GDP grew by 3%.", ClaimType.statistical, sc)

    def test_rule3_statistical_suspected_truncation(self):
        sc = _sc(age_days=30, suspected_truncation=True)
        assert compute_requires_date_review("Revenue was $1M.", ClaimType.statistical, sc)

    def test_rule3_non_statistical_truncation_not_flagged(self):
        sc = _sc(age_days=30, suspected_truncation=True)
        assert not compute_requires_date_review(
            "The company was founded in London.", ClaimType.factual, sc
        )


class TestNonStatisticalRules:
    def test_non_statistical_no_time_language_not_flagged(self):
        sc = _sc(age_days=1000)
        assert not compute_requires_date_review(
            "The Eiffel Tower is in Paris.", ClaimType.factual, sc
        )

    def test_rule4_time_sensitive_wording_old_source(self):
        sc = _sc(age_days=TIME_SENSITIVE_REVIEW_DAYS + 1)
        assert compute_requires_date_review(
            "The current CEO is Alice Smith.", ClaimType.factual, sc
        )

    def test_rule4_time_sensitive_wording_recent_source_not_flagged(self):
        sc = _sc(age_days=10)
        assert not compute_requires_date_review(
            "The current CEO is Alice Smith.", ClaimType.factual, sc
        )

    def test_rule4_today_wording(self):
        sc = _sc(age_days=TIME_SENSITIVE_REVIEW_DAYS + 1)
        result = compute_requires_date_review(
            "Today the market opened higher.", ClaimType.factual, sc
        )
        assert result

    def test_rule4_recently_wording(self):
        sc = _sc(age_days=TIME_SENSITIVE_REVIEW_DAYS + 1)
        assert compute_requires_date_review(
            "The company recently announced layoffs.", ClaimType.attribution, sc
        )

    def test_rule4_no_date_time_sensitive(self):
        sc = _sc(published_at=None)
        assert compute_requires_date_review(
            "The latest figures show growth.", ClaimType.factual, sc
        )


class TestHistoricalYearSuppression:
    def test_historical_year_suppresses_rule4(self):
        # Rule 4 is suppressed when an explicit historical year is present.
        sc = _sc(age_days=TIME_SENSITIVE_REVIEW_DAYS + 1)
        result = compute_requires_date_review(
            "Currently in 2015 the rate was 3%.", ClaimType.factual, sc
        )
        assert not result

    def test_historical_year_does_not_suppress_rule1(self):
        # Rule 1 (statistical + no date) is independent of claim wording.
        sc = _sc(published_at=None)
        result = compute_requires_date_review(
            "In 2015, unemployment was 5%.", ClaimType.statistical, sc
        )
        assert result  # rule 1 still fires

    def test_historical_year_does_not_suppress_rule2(self):
        sc = _sc(age_days=STAT_REVIEW_THRESHOLD_DAYS + 1)
        result = compute_requires_date_review(
            "In 2015, the rate was 3%.", ClaimType.statistical, sc
        )
        assert result  # rule 2 still fires

    def test_historical_year_does_not_suppress_rule3(self):
        sc = _sc(age_days=30, suspected_truncation=True)
        result = compute_requires_date_review(
            "In 2015, GDP grew 3%.", ClaimType.statistical, sc
        )
        assert result  # rule 3 still fires

    def test_no_historical_year_rule4_applies(self):
        sc = _sc(age_days=TIME_SENSITIVE_REVIEW_DAYS + 1)
        result = compute_requires_date_review(
            "Currently the rate is 5%.", ClaimType.statistical, sc
        )
        # Rule 4 not suppressed (no historical year); rule 2 also fires (old + statistical).
        assert result

    def test_year_1990_suppresses_rule4(self):
        sc = _sc(age_days=TIME_SENSITIVE_REVIEW_DAYS + 1)
        result = compute_requires_date_review(
            "Currently in 1990 this was true.", ClaimType.factual, sc
        )
        assert not result
