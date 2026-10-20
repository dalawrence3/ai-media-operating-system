"""Phase 7C.2, §2/§3/§8 — exploration-cell domain compatibility.

Generic by construction: classify_domain_compatibility() takes a domain
seed string and free text — no opportunity/domain pair is ever
hardcoded, and every test case here is phrased generically (any language
pair, any finance topic) rather than special-casing "Spanish" or
"budgeting" as literal production rules.
"""

from __future__ import annotations

from app.intelligence.exploration.domain_compatibility import (
    ADJACENT,
    COMPATIBLE,
    INCOMPATIBLE,
    classify_domain_compatibility,
)


class TestCompatibleLanguageLearningTopics:
    """§8.A — cell=language_learning, opportunity=vocabulary/pronunciation/
    language-skill topic => compatible."""

    def test_vocabulary_topic_is_compatible(self):
        result = classify_domain_compatibility(
            "language_learning", "everyday vocabulary and pronunciation for beginners"
        )
        assert result.classification == COMPATIBLE

    def test_grammar_topic_is_compatible(self):
        result = classify_domain_compatibility(
            "language_learning", "common grammar mistakes when learning a new language"
        )
        assert result.classification == COMPATIBLE


class TestIncompatibleLanguageLearningPairing:
    """§8.B — cell=language_learning, opportunity=science of cooking food
    => incompatible. This is the EXACT real regression from Phase 7C.1."""

    def test_science_of_cooking_food_is_incompatible(self):
        result = classify_domain_compatibility(
            "language_learning", "science of cooking food chemical reactions heat"
        )
        assert result.classification == INCOMPATIBLE
        assert result.matched_keywords == ()


class TestCompatiblePersonalFinanceTopics:
    """§8.C — cell=personal_finance, opportunity=budgeting/debt/
    compound-interest education => compatible."""

    def test_budgeting_topic_is_compatible(self):
        result = classify_domain_compatibility(
            "personal_finance", "budgeting tips to pay off debt faster"
        )
        assert result.classification == COMPATIBLE

    def test_compound_interest_topic_is_compatible(self):
        result = classify_domain_compatibility(
            "personal_finance", "how compound interest grows your savings over time"
        )
        assert result.classification == COMPATIBLE


class TestIncompatiblePersonalFinancePairing:
    """§8.D — cell=personal_finance, opportunity=astrophysics =>
    incompatible."""

    def test_astrophysics_is_incompatible(self):
        result = classify_domain_compatibility(
            "personal_finance", "astrophysics black holes and the edge of the universe"
        )
        assert result.classification == INCOMPATIBLE


class TestAdjacentButDefensibleTopics:
    """§8.E — a topic with genuinely high but partial semantic overlap can
    pass (adjacent-but-valid), not an automatic block."""

    def test_single_strong_signal_is_adjacent_not_incompatible(self):
        result = classify_domain_compatibility(
            "communication", "why speaking in front of a crowd terrifies so many people"
        )
        assert result.classification == ADJACENT
        assert result.eligible_for_linkage is True

    def test_adjacent_is_still_eligible_for_linkage(self):
        result = classify_domain_compatibility("science", "the chemistry of everyday cooking")
        assert result.classification in (ADJACENT, COMPATIBLE)
        assert result.eligible_for_linkage is True


class TestIncompatibleIsNotEligibleForLinkage:
    def test_incompatible_is_never_eligible(self):
        result = classify_domain_compatibility("history", "modern smartphone app design trends")
        assert result.classification == INCOMPATIBLE
        assert result.eligible_for_linkage is False


class TestGenericAcrossAllDomainSeeds:
    """No domain is special-cased in production code — prove every seed
    domain has a real, non-empty keyword basis and behaves consistently."""

    def test_every_domain_seed_matches_its_own_clearly_on_topic_text(self):
        from app.intelligence.exploration.taxonomy import DOMAIN_SEEDS

        on_topic_text = {
            "language_learning": "learning vocabulary and grammar in a new language",
            "personal_finance": "budgeting savings and investing for retirement",
            "psychology": "cognitive bias and how the brain processes emotion",
            "business_career": "career negotiation tips for your next job interview",
            "history": "the ancient civilization and empire that shaped the era",
            "science": "the chemistry experiment behind this scientific reaction",
            "technology_ai": "how artificial intelligence algorithms power this software",
            "geography_culture": "the culture and tradition of countries around the world",
            "logic_math": "a math puzzle involving algebra and probability",
            "everyday_knowledge": "a practical everyday household hack",
            "communication": "public speaking and persuasive writing techniques",
            "health_wellness": "nutrition fitness and wellness for better sleep",
        }
        for domain in DOMAIN_SEEDS:
            text = on_topic_text[domain]
            result = classify_domain_compatibility(domain, text)
            assert result.classification == COMPATIBLE, (
                f"{domain} failed to match its own clearly on-topic text: {text!r}"
            )

    def test_unknown_future_domain_falls_back_to_name_derived_keywords(self):
        """A domain never seen before (not in the curated table) must
        still work generically — never crash, never silently pass/fail
        everything."""
        result = classify_domain_compatibility(
            "underwater_basket_weaving", "a tutorial on underwater basket weaving techniques"
        )
        assert result.classification == COMPATIBLE

    def test_unknown_future_domain_correctly_rejects_unrelated_text(self):
        result = classify_domain_compatibility(
            "underwater_basket_weaving", "quantum computing and cryptography explained"
        )
        assert result.classification == INCOMPATIBLE


class TestResultCarriesEvidence:
    def test_matched_keywords_are_reported(self):
        result = classify_domain_compatibility(
            "personal_finance", "a guide to budgeting and managing debt"
        )
        assert "budgeting" in result.matched_keywords
        assert "debt" in result.matched_keywords

    def test_domain_is_carried_through(self):
        result = classify_domain_compatibility("science", "irrelevant text about nothing")
        assert result.domain == "science"
