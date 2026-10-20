"""Phase 7B, §11/§13/§19/§20 — format-specific script validation."""

from __future__ import annotations

from app.content.schemas import GeneratedScript, ScriptSection
from app.intelligence.exploration.format_validation import (
    validate_format_script,
    validate_micro_lesson_script,
    validate_quiz_script,
)


def _section(
    index: int, section_type: str, text: str = "x", think_duration_ms: int = 0
) -> ScriptSection:
    return ScriptSection(
        section_index=index,
        section_type=section_type,
        text=text,
        cited_claim_ids=[],
        think_duration_ms=think_duration_ms,
    )


def _valid_quiz_script() -> GeneratedScript:
    return GeneratedScript(
        title="Quiz",
        sections=[
            _section(0, "hook"),
            _section(1, "prompt", think_duration_ms=3000),
            _section(2, "reveal"),
            _section(3, "next_challenge"),
            _section(4, "prompt", think_duration_ms=2500),
            _section(5, "reveal"),
            _section(6, "cta"),
        ],
    )


def _valid_lesson_script() -> GeneratedScript:
    return GeneratedScript(
        title="Lesson",
        sections=[
            _section(0, "hook"),
            _section(1, "problem"),
            _section(2, "explanation"),
            _section(3, "example"),
            _section(4, "takeaway"),
            _section(5, "cta"),
        ],
    )


class TestGenericRecallQuizSchema:
    def test_well_formed_quiz_passes(self):
        result = validate_quiz_script(_valid_quiz_script())
        assert result.valid
        assert result.violations == ()

    def test_english_to_spanish_quiz_is_representable_generically(self):
        """§18/§19 — no Spanish-specific code anywhere; a Spanish quiz is
        just ordinary prompt/reveal text content."""
        script = GeneratedScript(
            title="Spanish Vocab",
            sections=[
                _section(0, "hook", "Test your Spanish!"),
                _section(1, "prompt", "How do you say 'window' in Spanish?", 3000),
                _section(2, "reveal", "La ventana."),
                _section(3, "next_challenge", "One more!"),
                _section(4, "prompt", "How do you say 'thank you' in Spanish?", 2500),
                _section(5, "reveal", "Gracias."),
                _section(6, "cta", "Follow for more!"),
            ],
        )
        assert validate_quiz_script(script).valid

    def test_another_language_pair_is_equally_representable(self):
        """§8 — the mechanic must not be Spanish-specific; French works
        identically with zero code changes."""
        script = GeneratedScript(
            title="French Vocab",
            sections=[
                _section(0, "hook", "Test your French!"),
                _section(1, "prompt", "How do you say 'bread' in French?", 3000),
                _section(2, "reveal", "Le pain."),
                _section(3, "next_challenge", "One more!"),
                _section(4, "prompt", "How do you say 'cat' in French?", 2500),
                _section(5, "reveal", "Le chat."),
                _section(6, "cta", "Follow for more!"),
            ],
        )
        assert validate_quiz_script(script).valid

    def test_non_language_recall_quiz_works(self):
        """§6 — geography/history/science recall, not just vocabulary."""
        script = GeneratedScript(
            title="Geography Quiz",
            sections=[
                _section(0, "hook", "Can you name this capital?"),
                _section(1, "prompt", "What is the capital of Australia?", 3000),
                _section(2, "reveal", "Canberra — not Sydney!"),
                _section(3, "next_challenge", "One more!"),
                _section(4, "prompt", "What is the longest river in the world?", 2500),
                _section(5, "reveal", "The Nile."),
                _section(6, "cta", "Follow for more!"),
            ],
        )
        assert validate_quiz_script(script).valid


class TestThinkIntervalPreserved:
    def test_prompt_without_think_duration_is_rejected(self):
        script = GeneratedScript(
            title="Quiz",
            sections=[
                _section(0, "hook"),
                _section(1, "prompt", think_duration_ms=0),
                _section(2, "reveal"),
                _section(3, "cta"),
            ],
        )
        result = validate_quiz_script(script)
        assert not result.valid
        assert any("think_duration_ms" in v for v in result.violations)

    def test_non_prompt_section_with_think_duration_is_rejected(self):
        script = GeneratedScript(
            title="Quiz",
            sections=[
                _section(0, "hook"),
                _section(1, "prompt", think_duration_ms=3000),
                _section(2, "reveal", think_duration_ms=1000),
                _section(3, "cta"),
            ],
        )
        result = validate_quiz_script(script)
        assert not result.valid
        assert any("only 'prompt' sections" in v for v in result.violations)


class TestAnswerNotExposedBeforeReveal:
    def test_two_prompts_in_a_row_without_a_reveal_is_rejected(self):
        script = GeneratedScript(
            title="Quiz",
            sections=[
                _section(0, "hook"),
                _section(1, "prompt", think_duration_ms=3000),
                _section(2, "prompt", think_duration_ms=3000),
                _section(3, "reveal"),
                _section(4, "cta"),
            ],
        )
        result = validate_quiz_script(script)
        assert not result.valid
        assert any("not immediately followed by its own 'reveal'" in v for v in result.violations)

    def test_reveal_without_a_preceding_prompt_is_caught_via_count_mismatch(self):
        script = GeneratedScript(
            title="Quiz",
            sections=[
                _section(0, "hook"),
                _section(1, "reveal"),
                _section(2, "prompt", think_duration_ms=3000),
                _section(3, "cta"),
            ],
        )
        result = validate_quiz_script(script)
        assert not result.valid


class TestQuizItemCountBounds:
    def test_too_few_rounds_rejected(self):
        script = GeneratedScript(
            title="Quiz",
            sections=[
                _section(0, "hook"),
                _section(1, "prompt", think_duration_ms=3000),
                _section(2, "reveal"),
                _section(3, "cta"),
            ],
        )
        result = validate_quiz_script(script)
        assert not result.valid
        assert any("round(s); must be between" in v for v in result.violations)

    def test_too_many_rounds_rejected(self):
        sections = [_section(0, "hook")]
        idx = 1
        for _ in range(5):
            sections.append(_section(idx, "prompt", think_duration_ms=3000))
            sections.append(_section(idx + 1, "reveal"))
            idx += 2
        sections.append(_section(idx, "cta"))
        script = GeneratedScript(title="Quiz", sections=sections)
        result = validate_quiz_script(script)
        assert not result.valid


class TestMicroLessonRequiredStructure:
    def test_well_formed_lesson_passes(self):
        result = validate_micro_lesson_script(_valid_lesson_script())
        assert result.valid

    def test_finance_example_represented_generically(self):
        """§14/§18 — finance is just ordinary content, no special-casing."""
        script = GeneratedScript(
            title="Compound Interest",
            sections=[
                _section(0, "hook", "This one habit could double your savings."),
                _section(1, "problem", "Why does starting early matter so much?"),
                _section(2, "explanation", "Compound interest grows on top of itself."),
                _section(3, "example", "$1,000 at 7% becomes ~$2,000 in 10 years, untouched."),
                _section(4, "takeaway", "Start now — time matters more than amount."),
                _section(5, "cta", "Follow for more money tips!"),
            ],
        )
        assert validate_micro_lesson_script(script).valid

    def test_non_finance_practical_lesson_works(self):
        script = GeneratedScript(
            title="Salary Negotiation",
            sections=[
                _section(0, "hook", "You're probably leaving money on the table."),
                _section(1, "problem", "Why don't people negotiate offers?"),
                _section(2, "explanation", "Most first offers have room built in."),
                _section(3, "example", "Asking for 10% more got Maria an extra $6,000."),
                _section(4, "takeaway", "Always ask — the worst answer is 'no'."),
                _section(5, "cta", "Follow for more career tips!"),
            ],
        )
        assert validate_micro_lesson_script(script).valid

    def test_missing_example_is_rejected(self):
        script = GeneratedScript(
            title="Lesson",
            sections=[
                _section(0, "hook"),
                _section(1, "problem"),
                _section(2, "explanation"),
                _section(3, "takeaway"),
                _section(4, "cta"),
            ],
        )
        result = validate_micro_lesson_script(script)
        assert not result.valid
        assert any("example" in v for v in result.violations)

    def test_missing_takeaway_is_rejected(self):
        script = GeneratedScript(
            title="Lesson",
            sections=[
                _section(0, "hook"),
                _section(1, "problem"),
                _section(2, "explanation"),
                _section(3, "example"),
                _section(4, "cta"),
            ],
        )
        result = validate_micro_lesson_script(script)
        assert not result.valid
        assert any("takeaway" in v for v in result.violations)


class TestCurrentExplainerPathUnchanged:
    def test_default_format_never_fails_format_validation(self):
        """§19 — the current explainer path must remain unchanged: no
        dedicated validator exists for it, so it always passes here,
        regardless of content."""
        script = GeneratedScript(
            title="Explainer",
            sections=[
                _section(0, "hook"),
                _section(1, "body"),
                _section(2, "cta"),
            ],
        )
        assert validate_format_script(script, "curiosity_explainer").valid
        assert validate_format_script(script, None).valid
        assert validate_format_script(script, "some_unspecified_format").valid


class TestValidateFormatScriptRouting:
    def test_routes_to_quiz_validator(self):
        bad_quiz = GeneratedScript(
            title="Quiz",
            sections=[_section(0, "hook"), _section(1, "cta")],
        )
        result = validate_format_script(bad_quiz, "interactive_recall_quiz")
        assert not result.valid

    def test_routes_to_lesson_validator(self):
        bad_lesson = GeneratedScript(
            title="Lesson",
            sections=[_section(0, "hook"), _section(1, "cta")],
        )
        result = validate_format_script(bad_lesson, "practical_micro_lesson")
        assert not result.valid
