"""Phase 7C.1, Part B — format suitability heuristic.

Generic by construction: format_suitability() takes only (format,
audience_value) — no domain parameter exists to hardcode against, so
"language_learning -> quiz" or "finance -> micro_lesson" style routing is
structurally impossible here, not just avoided by convention.
"""

from __future__ import annotations

from app.intelligence.exploration.format_suitability import (
    DEFAULT_SUITABILITY,
    format_suitability,
)


class TestRecallOrientedContentFavorsQuiz:
    def test_immediate_payoff_scores_quiz_highest(self):
        quiz = format_suitability("interactive_recall_quiz", "immediate_payoff")
        lesson = format_suitability("practical_micro_lesson", "immediate_payoff")
        explainer = format_suitability("curiosity_explainer", "immediate_payoff")
        assert quiz > lesson
        assert quiz > explainer


class TestPracticalScenarioFavorsMicroLesson:
    def test_practical_utility_scores_lesson_highest(self):
        quiz = format_suitability("interactive_recall_quiz", "practical_utility")
        lesson = format_suitability("practical_micro_lesson", "practical_utility")
        explainer = format_suitability("curiosity_explainer", "practical_utility")
        assert lesson > quiz
        assert lesson > explainer


class TestCuriosityRevealFavorsExplainer:
    def test_curiosity_scores_explainer_highest(self):
        quiz = format_suitability("interactive_recall_quiz", "curiosity")
        lesson = format_suitability("practical_micro_lesson", "curiosity")
        explainer = format_suitability("curiosity_explainer", "curiosity")
        assert explainer > quiz
        assert explainer > lesson

    def test_surprise_scores_explainer_highest(self):
        quiz = format_suitability("interactive_recall_quiz", "surprise")
        lesson = format_suitability("practical_micro_lesson", "surprise")
        explainer = format_suitability("curiosity_explainer", "surprise")
        assert explainer > quiz
        assert explainer > lesson


class TestNoDomainParameterExists:
    """The function signature itself has no domain argument — the only way
    a caller could smuggle in a domain-specific rule would be a second
    lookup keyed on domain, which this module never builds."""

    def test_signature_takes_only_format_and_audience_value(self):
        import inspect

        params = list(inspect.signature(format_suitability).parameters)
        assert params == ["format_name", "audience_value"]


class TestUnsupportedFormatsAndUnknownValuesAreNeutral:
    def test_unsupported_format_returns_default_no_crash(self):
        assert format_suitability("myth_bust", "curiosity") == DEFAULT_SUITABILITY

    def test_unknown_audience_value_returns_default(self):
        assert format_suitability("interactive_recall_quiz", "made_up_value") == (
            DEFAULT_SUITABILITY
        )

    def test_every_score_is_a_valid_probability(self):
        from app.intelligence.exploration.format_suitability import _SUITABILITY_TABLE

        for fmt_scores in _SUITABILITY_TABLE.values():
            for score in fmt_scores.values():
                assert 0.0 <= score <= 1.0
