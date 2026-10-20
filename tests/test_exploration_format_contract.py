"""Phase 7B, §5/§18 — format contract registry."""

from __future__ import annotations

from app.intelligence.exploration.format_contract import (
    DEFAULT_FORMAT,
    QUIZ_MAX_ITEMS,
    QUIZ_MIN_ITEMS,
    get_format_spec,
    known_format_specs,
    resolve_format_spec,
)


class TestKnownSpecs:
    def test_default_format_has_a_spec(self):
        assert get_format_spec(DEFAULT_FORMAT) is not None

    def test_quiz_and_lesson_have_specs(self):
        assert get_format_spec("interactive_recall_quiz") is not None
        assert get_format_spec("practical_micro_lesson") is not None

    def test_unspecified_seed_format_returns_none(self):
        """§14/§17: every other seed format is valid to select as a cell
        even without production support yet — None, not an error."""
        assert get_format_spec("myth_bust") is None

    def test_known_format_specs_lists_all_three(self):
        assert set(known_format_specs()) == {
            DEFAULT_FORMAT,
            "interactive_recall_quiz",
            "practical_micro_lesson",
        }


class TestResolveFormatSpecNeverReturnsNone:
    def test_known_format_resolves_to_itself(self):
        assert resolve_format_spec("interactive_recall_quiz").name == "interactive_recall_quiz"

    def test_unknown_format_falls_back_to_default(self):
        assert resolve_format_spec("myth_bust").name == DEFAULT_FORMAT

    def test_none_falls_back_to_default(self):
        assert resolve_format_spec(None).name == DEFAULT_FORMAT

    def test_empty_string_falls_back_to_default(self):
        assert resolve_format_spec("").name == DEFAULT_FORMAT


class TestFormatSpecsCarryPromptRouting:
    def test_each_spec_names_a_distinct_prompt(self):
        specs = [get_format_spec(f) for f in known_format_specs()]
        prompt_names = {s.prompt_name for s in specs if s is not None}
        assert len(prompt_names) == 3  # one dedicated prompt per format

    def test_quiz_spec_sections_start_with_hook_end_with_cta(self):
        spec = get_format_spec("interactive_recall_quiz")
        assert spec.sections[0] == "hook"
        assert spec.sections[-1] == "cta"

    def test_lesson_spec_includes_required_structure(self):
        spec = get_format_spec("practical_micro_lesson")
        for required in ("hook", "problem", "explanation", "example", "takeaway"):
            assert required in spec.sections


class TestQuizBoundsFitTheSharedSectionCountCap:
    """§6/§9 — QUIZ_MAX_ITEMS must fit content.schemas.LLMGeneratedScript's
    shared max_length=12 sections cap: hook(1) + cta(1) + rounds*2 +
    (rounds-1) next_challenge <= 12."""

    def test_max_items_fits_within_twelve_sections(self):
        rounds = QUIZ_MAX_ITEMS
        total = 1 + 1 + rounds * 2 + max(0, rounds - 1)
        assert total <= 12

    def test_min_items_is_at_least_two(self):
        assert QUIZ_MIN_ITEMS >= 2
