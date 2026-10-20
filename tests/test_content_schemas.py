"""Tests for Phase 5 schema models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.content.schemas import (
    GeneratedScript,
    LLMGeneratedScript,
    LLMScriptSection,
)


def _section(
    section_type: str = "body",
    text: str = "Some text.",
    cited: list[int] | None = None,
) -> dict:
    return {"section_type": section_type, "text": text, "cited_claim_ids": cited or []}


def _valid_llm() -> dict:
    return {
        "title": "My Script",
        "sections": [
            _section("hook", "Hook text."),
            _section("body", "Body text. [claim:1]", [1]),
        ],
    }


class TestLLMScriptSection:
    def test_valid(self):
        s = LLMScriptSection(**_section("body", "text", [1, 2]))
        assert s.section_type == "body"
        assert s.cited_claim_ids == [1, 2]

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            LLMScriptSection(**_section(), extra_field="bad")

    def test_duplicate_cited_ids_rejected(self):
        with pytest.raises(ValidationError, match="duplicate"):
            LLMScriptSection(**_section("body", "t", [1, 1]))

    def test_empty_text_rejected(self):
        with pytest.raises(ValidationError):
            LLMScriptSection(section_type="body", text="", cited_claim_ids=[])

    def test_too_long_text_rejected(self):
        with pytest.raises(ValidationError):
            LLMScriptSection(section_type="body", text="x" * 2001, cited_claim_ids=[])

    def test_too_many_cited_ids_rejected(self):
        with pytest.raises(ValidationError):
            LLMScriptSection(section_type="body", text="t", cited_claim_ids=list(range(21)))

    def test_unknown_section_type_rejected(self):
        with pytest.raises(ValidationError):
            LLMScriptSection(section_type="unknown", text="t", cited_claim_ids=[])


class TestLLMScriptSectionLanguageCode:
    """Phase 7B.1, §1/§2 — per-section language override."""

    def test_defaults_to_none(self):
        s = LLMScriptSection(**_section("prompt", "Question?"))
        assert s.language_code is None

    def test_accepts_a_source_language_code(self):
        s = LLMScriptSection(**_section("prompt", "How do you say 'window'?"), language_code="en")
        assert s.language_code == "en"

    def test_accepts_a_target_language_code(self):
        s = LLMScriptSection(**_section("reveal", "La ventana."), language_code="es")
        assert s.language_code == "es"

    def test_not_restricted_to_any_particular_section_type(self):
        """Unlike think_duration_ms, an unused language override anywhere
        is harmless — no validator restricts which section_type may carry
        it (any two-language pair, any domain, per the spec's 'do not
        hardcode English/Spanish' instruction)."""
        for section_type in ("hook", "body", "cta", "problem", "takeaway"):
            s = LLMScriptSection(**_section(section_type, "t"), language_code="fr")
            assert s.language_code == "fr"


class TestLLMGeneratedScript:
    def test_valid(self):
        s = LLMGeneratedScript(**_valid_llm())
        assert s.title == "My Script"
        assert len(s.sections) == 2

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            LLMGeneratedScript(**_valid_llm(), estimated_duration_s=60)

    def test_no_word_count_field(self):
        with pytest.raises(ValidationError):
            LLMGeneratedScript(**_valid_llm(), estimated_word_count=100)

    def test_missing_hook_rejected(self):
        with pytest.raises(ValidationError, match="hook"):
            LLMGeneratedScript(title="T", sections=[_section("body", "text")])

    def test_two_hooks_rejected(self):
        with pytest.raises(ValidationError, match="hook"):
            LLMGeneratedScript(
                title="T",
                sections=[_section("hook", "h1"), _section("hook", "h2")],
            )

    def test_two_ctas_rejected(self):
        with pytest.raises(ValidationError, match="cta"):
            LLMGeneratedScript(
                title="T",
                sections=[
                    _section("hook", "h"),
                    _section("cta", "c1"),
                    _section("cta", "c2"),
                ],
            )

    def test_title_too_long_rejected(self):
        with pytest.raises(ValidationError):
            LLMGeneratedScript(title="x" * 81, sections=[_section("hook", "h")])

    def test_no_sections_rejected(self):
        with pytest.raises(ValidationError):
            LLMGeneratedScript(title="T", sections=[])

    def test_too_many_sections_rejected(self):
        sections = [_section("hook", "h")] + [_section("body", f"b{i}") for i in range(12)]
        with pytest.raises(ValidationError):
            LLMGeneratedScript(title="T", sections=sections)


class TestGeneratedScriptFromLLM:
    def test_assigns_section_indexes(self):
        llm = LLMGeneratedScript(**_valid_llm())
        gs = GeneratedScript.from_llm(llm)
        indexes = [s.section_index for s in gs.sections]
        assert indexes == [0, 1]

    def test_system_assigned_not_llm(self):
        # LLM output has no section_index field
        llm_data = _valid_llm()
        llm = LLMGeneratedScript(**llm_data)
        gs = GeneratedScript.from_llm(llm)
        assert all(isinstance(s.section_index, int) for s in gs.sections)

    def test_warnings_default_empty(self):
        llm = LLMGeneratedScript(**_valid_llm())
        gs = GeneratedScript.from_llm(llm)
        assert gs.warnings == []

    def test_section_type_preserved(self):
        llm = LLMGeneratedScript(**_valid_llm())
        gs = GeneratedScript.from_llm(llm)
        types = [s.section_type for s in gs.sections]
        assert types == ["hook", "body"]

    def test_language_code_propagates(self):
        """Phase 7B.1, §1/§2 — a section's language_code must survive the
        LLM-output -> internal ScriptSection conversion, same as
        think_duration_ms already does."""
        llm = LLMGeneratedScript(
            title="Quiz",
            sections=[
                LLMScriptSection(section_type="hook", text="Hook.", cited_claim_ids=[]),
                LLMScriptSection(
                    section_type="prompt",
                    text="How do you say 'window'?",
                    cited_claim_ids=[],
                    think_duration_ms=3000,
                    language_code="en",
                ),
                LLMScriptSection(
                    section_type="reveal",
                    text="La ventana.",
                    cited_claim_ids=[],
                    language_code="es",
                ),
            ],
        )
        gs = GeneratedScript.from_llm(llm)
        by_type = {s.section_type: s for s in gs.sections}
        assert by_type["hook"].language_code is None
        assert by_type["prompt"].language_code == "en"
        assert by_type["reveal"].language_code == "es"
