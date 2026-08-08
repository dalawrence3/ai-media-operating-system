"""Tests for deterministic source-quality scoring."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from app.research.models import DomainType, ExtractionStatus, FetchStatus, SourceContent
from app.research.quality import score_quality


def _make_sc(**kwargs) -> SourceContent:
    defaults = dict(
        source_id=1,
        fetch_status=FetchStatus.ok,
        extraction_status=ExtractionStatus.ok,
        fetched_at=datetime.now(UTC),
        raw_text="word " * 500,
        word_count=500,
        author="Jane Doe",
        published_at=(datetime.now(UTC) - timedelta(days=10)).strftime("%Y-%m-%d"),
        domain_type=DomainType.news,
        suspected_truncation=False,
        extraction_error=None,
    )
    defaults.update(kwargs)
    return SourceContent(**defaults)


class TestQualityScoring:
    def test_composite_score_in_range(self):
        sc = _make_sc()
        result = score_quality(sc)
        assert 0.0 <= result.score <= 1.0

    def test_recent_publication_gives_high_recency(self):
        sc = _make_sc(published_at=(datetime.now(UTC) - timedelta(days=5)).strftime("%Y-%m-%d"))
        result = score_quality(sc)
        assert result.factors["recency"] == 1.0

    def test_old_publication_gives_low_recency(self):
        sc = _make_sc(published_at=(datetime.now(UTC) - timedelta(days=800)).strftime("%Y-%m-%d"))
        result = score_quality(sc)
        assert result.factors["recency"] <= 0.15

    def test_missing_published_at_penalized(self):
        sc_known = _make_sc(
            published_at=(datetime.now(UTC) - timedelta(days=10)).strftime("%Y-%m-%d")
        )
        sc_unknown = _make_sc(published_at=None)
        assert (
            score_quality(sc_unknown).factors["recency"]
            < score_quality(sc_known).factors["recency"]
        )

    def test_government_domain_boosts_primary_source(self):
        sc = _make_sc(domain_type=DomainType.government)
        result = score_quality(sc)
        assert result.factors["primary_source"] == 1.0

    def test_academic_domain_boosts_primary_source(self):
        sc = _make_sc(domain_type=DomainType.academic)
        result = score_quality(sc)
        assert result.factors["primary_source"] == 1.0

    def test_unknown_domain_no_primary_source(self):
        sc = _make_sc(domain_type=DomainType.unknown)
        result = score_quality(sc)
        assert result.factors["primary_source"] == 0.0

    def test_has_author_boosts_score(self):
        sc_author = _make_sc(author="Jane")
        sc_no_author = _make_sc(author=None)
        assert score_quality(sc_author).factors["has_author"] == 1.0
        assert score_quality(sc_no_author).factors["has_author"] == 0.0

    def test_low_word_count_penalized(self):
        sc = _make_sc(word_count=50, raw_text="word " * 50)
        result = score_quality(sc)
        assert result.factors["word_count_score"] < 1.0

    def test_zero_word_count_penalized(self):
        sc = _make_sc(word_count=0, raw_text=None)
        result = score_quality(sc)
        assert result.factors["word_count_score"] == 0.0

    def test_adequate_word_count_full_score(self):
        sc = _make_sc(word_count=500)
        result = score_quality(sc)
        assert result.factors["word_count_score"] == 1.0

    def test_partial_extraction_penalizes_extraction_success(self):
        sc = _make_sc(extraction_status=ExtractionStatus.partial)
        result = score_quality(sc)
        assert result.factors["extraction_success"] == 0.5

    def test_failed_extraction_zero_extraction_success(self):
        sc = _make_sc(extraction_status=ExtractionStatus.failed)
        result = score_quality(sc)
        assert result.factors["extraction_success"] == 0.0

    def test_unknown_domain_type_penalizes_publication_identity(self):
        sc = _make_sc(domain_type=DomainType.unknown)
        result = score_quality(sc)
        assert result.factors["publication_identity"] == 0.0

    def test_known_domain_type_full_publication_identity(self):
        sc = _make_sc(domain_type=DomainType.news)
        result = score_quality(sc)
        assert result.factors["publication_identity"] == 1.0

    def test_suspected_truncation_penalized(self):
        sc_trunc = _make_sc(suspected_truncation=True)
        sc_ok = _make_sc(suspected_truncation=False)
        assert score_quality(sc_trunc).factors["not_truncated"] == 0.0
        assert score_quality(sc_ok).factors["not_truncated"] == 1.0

    def test_factors_json_is_valid_json(self):
        sc = _make_sc()
        result = score_quality(sc)
        parsed = json.loads(result.factors_json)
        assert isinstance(parsed, dict)
        assert len(parsed) > 0

    def test_scorer_version_set(self):
        from app.research.constants import QUALITY_SCORER_VERSION

        result = score_quality(_make_sc())
        assert result.scorer_version == QUALITY_SCORER_VERSION

    def test_composite_score_is_weighted_sum(self):
        """Verify the composite is within bounds for a known input."""
        sc = _make_sc(
            domain_type=DomainType.unknown,
            author=None,
            published_at=None,
            word_count=0,
            raw_text=None,
            extraction_status=ExtractionStatus.failed,
            suspected_truncation=True,
        )
        result = score_quality(sc)
        # All factors at minimum → composite should be very low
        assert result.score < 0.4

    def test_composite_score_rounds_to_4dp(self):
        sc = _make_sc()
        result = score_quality(sc)
        assert result.score == round(result.score, 4)
