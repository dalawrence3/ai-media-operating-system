"""Tests for Phase 5 renderer and duration computation (Stage 4)."""

from __future__ import annotations

from app.content.constants import (
    SCRIPT_WORDS_PER_MINUTE,
    SHORT_FORM_MAX_DURATION_S,
    SHORT_FORM_MIN_DURATION_S,
)
from app.content.renderer import (
    compute_duration_s,
    count_words,
    render_body,
    strip_markers,
)
from app.content.schemas import GeneratedScript, LLMGeneratedScript

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_script(
    title: str = "Test Script",
    hook: str = "Hook text.",
    body: str | None = "Body text.",
) -> GeneratedScript:
    sections = [{"section_type": "hook", "text": hook, "cited_claim_ids": []}]
    if body is not None:
        sections.append({"section_type": "body", "text": body, "cited_claim_ids": []})
    llm = LLMGeneratedScript(title=title, sections=sections)
    return GeneratedScript.from_llm(llm)


# ---------------------------------------------------------------------------
# render_body
# ---------------------------------------------------------------------------


class TestRenderBody:
    def test_title_first(self):
        script = _make_script(title="My Script")
        body = render_body(script)
        assert body.startswith("# My Script")

    def test_blank_line_after_title(self):
        script = _make_script()
        lines = render_body(script).split("\n")
        assert lines[1] == ""

    def test_section_label_hook(self):
        script = _make_script()
        body = render_body(script)
        assert "[HOOK]" in body

    def test_section_label_body(self):
        script = _make_script()
        body = render_body(script)
        assert "[BODY]" in body

    def test_section_text_included(self):
        script = _make_script(hook="This is the hook.", body="This is the body.")
        body = render_body(script)
        assert "This is the hook." in body
        assert "This is the body." in body

    def test_blank_line_between_sections(self):
        script = _make_script(hook="Hook.", body="Body.")
        lines = render_body(script).split("\n")
        # Blank line before [BODY]
        body_idx = lines.index("[BODY]")
        assert lines[body_idx - 1] == ""

    def test_markers_preserved(self):
        llm = LLMGeneratedScript(
            title="T",
            sections=[
                {"section_type": "hook", "text": "Intro [claim:1] more.", "cited_claim_ids": [1]},
            ],
        )
        script = GeneratedScript.from_llm(llm)
        body = render_body(script)
        assert "[claim:1]" in body

    def test_warnings_excluded(self):
        script = _make_script()
        script_with_warnings = script.model_copy(
            update={"warnings": ["Duration 5s below minimum 15s"]}
        )
        body = render_body(script_with_warnings)
        assert "Duration" not in body
        assert "warning" not in body.lower()

    def test_deterministic(self):
        script = _make_script()
        assert render_body(script) == render_body(script)

    def test_exact_format(self):
        """Verify the complete rendered format."""
        llm = LLMGeneratedScript(
            title="My Title",
            sections=[
                {"section_type": "hook", "text": "Hook here.", "cited_claim_ids": []},
                {"section_type": "body", "text": "Body here.", "cited_claim_ids": []},
            ],
        )
        script = GeneratedScript.from_llm(llm)
        expected = "# My Title\n\n[HOOK]\nHook here.\n\n[BODY]\nBody here."
        assert render_body(script) == expected

    def test_all_section_types_have_labels(self):
        for stype in ("hook", "intro", "body", "transition", "conclusion", "cta"):
            llm = LLMGeneratedScript(
                title="T",
                sections=[
                    {"section_type": "hook", "text": "H", "cited_claim_ids": []},
                    {"section_type": stype, "text": "S", "cited_claim_ids": []},
                ]
                if stype != "hook"
                else [
                    {"section_type": "hook", "text": "H", "cited_claim_ids": []},
                ],
            )
            script = GeneratedScript.from_llm(llm)
            body = render_body(script)
            assert f"[{stype.upper()}]" in body


# ---------------------------------------------------------------------------
# strip_markers
# ---------------------------------------------------------------------------


class TestStripMarkers:
    def test_no_markers(self):
        assert strip_markers("plain text") == "plain text"

    def test_single_marker(self):
        assert strip_markers("Fact [claim:1] follows.") == "Fact  follows."

    def test_multiple_markers(self):
        assert strip_markers("[claim:1] a [claim:2] b") == " a  b"

    def test_leading_zero_not_stripped(self):
        # [claim:01] is NOT a valid marker; should be left as-is
        text = "[claim:01] text"
        assert strip_markers(text) == "[claim:01] text"

    def test_zero_not_stripped(self):
        assert strip_markers("[claim:0] text") == "[claim:0] text"


# ---------------------------------------------------------------------------
# count_words
# ---------------------------------------------------------------------------


class TestCountWords:
    def test_simple(self):
        script = _make_script(hook="one two three", body=None)
        assert count_words(script) == 3

    def test_markers_excluded_from_count(self):
        llm = LLMGeneratedScript(
            title="T",
            sections=[
                {"section_type": "hook", "text": "word [claim:1] word", "cited_claim_ids": [1]}
            ],
        )
        script = GeneratedScript.from_llm(llm)
        assert count_words(script) == 2

    def test_multi_section(self):
        script = _make_script(hook="one two", body="three four five")
        assert count_words(script) == 5


# ---------------------------------------------------------------------------
# compute_duration_s
# ---------------------------------------------------------------------------


class TestComputeDurationS:
    def test_min_clamp(self):
        assert compute_duration_s(0) == SHORT_FORM_MIN_DURATION_S

    def test_max_clamp(self):
        huge = 10_000
        assert compute_duration_s(huge) == SHORT_FORM_MAX_DURATION_S

    def test_normal_range(self):
        # 150 wpm * 1 min = 150 words → 60 seconds
        result = compute_duration_s(150)
        assert result == 60

    def test_rounds_up(self):
        # 1 word → ceil(1/150 * 60) = ceil(0.4) = 1, but clamped to 15
        result = compute_duration_s(1)
        assert result == SHORT_FORM_MIN_DURATION_S

    def test_75_words(self):
        # 75/150 * 60 = 30 seconds
        result = compute_duration_s(75)
        assert result == 30

    def test_bounds_inclusive(self):
        min_words = SHORT_FORM_MIN_DURATION_S * SCRIPT_WORDS_PER_MINUTE // 60
        max_words = SHORT_FORM_MAX_DURATION_S * SCRIPT_WORDS_PER_MINUTE // 60
        assert compute_duration_s(min_words) >= SHORT_FORM_MIN_DURATION_S
        assert compute_duration_s(max_words) <= SHORT_FORM_MAX_DURATION_S
