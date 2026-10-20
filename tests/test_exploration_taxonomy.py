"""Phase 7A, §2/§18 — domain/format/audience-value taxonomy.

Every check here pins the "seed, not closed universe" contract: an unseen
value is never rejected, only reported as non-seed.
"""

from __future__ import annotations

from app.intelligence.exploration.taxonomy import (
    AUDIENCE_VALUE_SEEDS,
    DOMAIN_SEEDS,
    FORMAT_SEEDS,
    is_known_audience_value,
    is_seed_domain,
    is_seed_format,
    normalize_domain,
    normalize_format,
)
from app.learning.topic_attributes import TOPIC_ATTRIBUTE_NAMES


class TestDomainClassification:
    def test_seed_domains_recognized(self):
        for domain in DOMAIN_SEEDS:
            assert is_seed_domain(domain)

    def test_case_and_whitespace_insensitive(self):
        assert is_seed_domain("  Language_Learning  ")
        assert is_seed_domain("language-learning")

    def test_unseen_domain_still_valid_not_rejected(self):
        assert is_seed_domain("underwater_basket_weaving") is False
        # normalize_domain never raises for a novel value.
        assert normalize_domain("underwater_basket_weaving") == "underwater_basket_weaving"

    def test_seed_list_includes_the_two_test_case_domains(self):
        assert "language_learning" in DOMAIN_SEEDS
        assert "personal_finance" in DOMAIN_SEEDS

    def test_seed_list_does_not_exclude_science(self):
        """Science must remain a valid domain — the fix is diversity, not a ban."""
        assert "science" in DOMAIN_SEEDS


class TestFormatClassification:
    def test_seed_formats_recognized(self):
        for fmt in FORMAT_SEEDS:
            assert is_seed_format(fmt)

    def test_unseen_format_still_valid_not_rejected(self):
        assert is_seed_format("interpretive_dance_explainer") is False
        assert normalize_format("interpretive_dance_explainer") == "interpretive_dance_explainer"

    def test_seed_list_includes_the_two_test_case_formats(self):
        assert "interactive_recall_quiz" in FORMAT_SEEDS
        assert "practical_micro_lesson" in FORMAT_SEEDS


class TestAudienceValueReusesPhase18HC:
    def test_audience_value_seeds_are_exactly_topic_attribute_names(self):
        """No duplicate taxonomy — audience_value IS the existing Phase
        18H.C attribute list, imported, never redeclared."""
        assert AUDIENCE_VALUE_SEEDS == TOPIC_ATTRIBUTE_NAMES

    def test_known_audience_values_recognized(self):
        for value in AUDIENCE_VALUE_SEEDS:
            assert is_known_audience_value(value)

    def test_unknown_audience_value_reported_not_rejected(self):
        assert is_known_audience_value("made_up_value") is False
