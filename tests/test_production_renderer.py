"""Tests for Phase 6 M6.1 production plan renderer.

Core invariants verified:
- build_production_plan() is a pure function (same input → identical output)
- segment_index is deterministic and gapless
- section_index and section_type match the source script exactly
- narration_text == strip_markers(section.text) with no other transformation
- word count is computed locally from narration_text
- duration is unclamped (min 1s) and computed locally
- totals equal the sum of segment values
- warnings, requires_evidence_review, evidence_hash, generation_run_id, script_id, version
  propagate unchanged
- citation_claim_ids preserves source order
- input_hash and script_body_hash are deterministic
- experiment_id is always None
"""

from __future__ import annotations

import math

from app.content.constants import SCRIPT_WORDS_PER_MINUTE
from app.content.renderer import strip_markers
from app.content.schemas import ApprovedScript, GeneratedScript, ScriptSection
from app.production.renderer import (
    _count_words,
    _segment_duration_s,
    build_production_plan,
    plan_draft_to_json_summary,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_section(
    section_index: int,
    section_type: str = "body",
    text: str = "Plain narration text.",
    cited_claim_ids: list[int] | None = None,
) -> ScriptSection:
    return ScriptSection(
        section_index=section_index,
        section_type=section_type,
        text=text,
        cited_claim_ids=cited_claim_ids or [],
    )


def _make_approved_script(
    sections: list[ScriptSection] | None = None,
    script_id: int = 1,
    topic_id: int = 10,
    version: int = 1,
    format: str = "short",
    warnings: list[str] | None = None,
    requires_evidence_review: bool = False,
    generation_run_id: int | None = 42,
    evidence_hash: str | None = "ev" * 32,
) -> ApprovedScript:
    if sections is None:
        sections = [_make_section(0, "hook", "Hook text."), _make_section(1, "cta", "CTA.")]
    body_json = GeneratedScript(title="Test Script", sections=sections)
    return ApprovedScript(
        script_id=script_id,
        topic_id=topic_id,
        version=version,
        body_json=body_json,
        body="",
        format=format,
        computed_duration_s=0,
        computed_word_count=0,
        status="approved",
        citations=[],
        warnings=warnings or [],
        requires_evidence_review=requires_evidence_review,
        generation_run_id=generation_run_id,
        model=None,
        prompt_name=None,
        prompt_hash=None,
        evidence_hash=evidence_hash,
        approved_at="2024-01-01T00:00:00",
    )


# ---------------------------------------------------------------------------
# _count_words
# ---------------------------------------------------------------------------


def test_count_words_basic() -> None:
    assert _count_words("Hello world") == 2


def test_count_words_empty() -> None:
    assert _count_words("") == 0


def test_count_words_whitespace_only() -> None:
    assert _count_words("   ") == 0


def test_count_words_single() -> None:
    assert _count_words("word") == 1


def test_count_words_multiple_spaces() -> None:
    assert _count_words("one  two   three") == 3


def test_count_words_leading_trailing() -> None:
    assert _count_words("  hello world  ") == 2


# ---------------------------------------------------------------------------
# _segment_duration_s
# ---------------------------------------------------------------------------


def test_segment_duration_zero_words_is_zero() -> None:
    assert _segment_duration_s(0) == 0


def test_segment_duration_min_one_second() -> None:
    assert _segment_duration_s(1) >= 1


def test_segment_duration_small_word_count_not_clamped_high() -> None:
    # 3 words: ceil(3/150*60) = ceil(1.2) = 2 — well below 15s (clamp we must NOT apply)
    result = _segment_duration_s(3)
    expected = max(1, math.ceil(3 / SCRIPT_WORDS_PER_MINUTE * 60))
    assert result == expected
    assert result < 15  # confirms no 15s lower-clamp


def test_segment_duration_large_word_count_not_clamped_low() -> None:
    # 300 words: ceil(300/150*60) = 120s — above 90s (clamp we must NOT apply)
    result = _segment_duration_s(300)
    expected = max(1, math.ceil(300 / SCRIPT_WORDS_PER_MINUTE * 60))
    assert result == expected
    assert result > 90  # confirms no 90s upper-clamp


def test_segment_duration_formula_matches_manual() -> None:
    for wc in [10, 25, 50, 75, 100, 150]:
        expected = max(1, math.ceil(wc / SCRIPT_WORDS_PER_MINUTE * 60))
        assert _segment_duration_s(wc) == expected


# ---------------------------------------------------------------------------
# build_production_plan — purity
# ---------------------------------------------------------------------------


def test_build_is_pure() -> None:
    script = _make_approved_script()
    draft1 = build_production_plan(script)
    draft2 = build_production_plan(script)
    assert draft1.input_hash == draft2.input_hash
    assert draft1.script_body_hash == draft2.script_body_hash
    assert len(draft1.segments) == len(draft2.segments)
    for s1, s2 in zip(draft1.segments, draft2.segments, strict=False):
        assert s1.narration_text == s2.narration_text
        assert s1.estimated_duration_s == s2.estimated_duration_s


# ---------------------------------------------------------------------------
# build_production_plan — segment index and structure
# ---------------------------------------------------------------------------


def test_segment_index_is_gapless_from_zero() -> None:
    sections = [
        _make_section(0, "hook", "Hook."),
        _make_section(1, "body", "Body text here."),
        _make_section(2, "cta", "CTA."),
    ]
    draft = build_production_plan(_make_approved_script(sections=sections))
    assert [s.segment_index for s in draft.segments] == [0, 1, 2]


def test_section_index_propagates() -> None:
    sections = [_make_section(0, "hook", "H."), _make_section(1, "body", "B.")]
    draft = build_production_plan(_make_approved_script(sections=sections))
    assert draft.segments[0].section_index == 0
    assert draft.segments[1].section_index == 1


def test_section_type_propagates() -> None:
    sections = [
        _make_section(0, "hook", "H."),
        _make_section(1, "body", "B."),
        _make_section(2, "cta", "C."),
    ]
    draft = build_production_plan(_make_approved_script(sections=sections))
    assert draft.segments[0].section_type == "hook"
    assert draft.segments[1].section_type == "body"
    assert draft.segments[2].section_type == "cta"


def test_segment_count_matches_section_count() -> None:
    sections = [_make_section(i, "body", f"Section {i}.") for i in range(5)]
    draft = build_production_plan(_make_approved_script(sections=sections))
    assert len(draft.segments) == 5


# ---------------------------------------------------------------------------
# build_production_plan — narration text invariant
# ---------------------------------------------------------------------------


def test_narration_text_equals_strip_markers() -> None:
    text_with_markers = "Claim one [claim:1] and claim two [claim:2] here."
    sections = [_make_section(0, "body", text_with_markers, [1, 2])]
    draft = build_production_plan(_make_approved_script(sections=sections))
    expected = strip_markers(text_with_markers)
    assert draft.segments[0].narration_text == expected


def test_narration_text_no_other_transformation() -> None:
    plain_text = "This is plain text with no markers at all."
    sections = [_make_section(0, "body", plain_text)]
    draft = build_production_plan(_make_approved_script(sections=sections))
    # strip_markers on plain text = plain text unchanged
    assert draft.segments[0].narration_text == plain_text


def test_narration_text_strips_all_markers() -> None:
    text = "[claim:10] Start [claim:20] middle end."
    sections = [_make_section(0, "hook", text, [10, 20])]
    draft = build_production_plan(_make_approved_script(sections=sections))
    assert "[claim:" not in draft.segments[0].narration_text


# ---------------------------------------------------------------------------
# build_production_plan — word count and duration
# ---------------------------------------------------------------------------


def test_word_count_computed_from_narration() -> None:
    text = "one [claim:1] two three"
    narration = strip_markers(text)  # "one  two three"
    expected_wc = _count_words(narration)
    sections = [_make_section(0, "body", text, [1])]
    draft = build_production_plan(_make_approved_script(sections=sections))
    assert draft.segments[0].estimated_word_count == expected_wc


def test_duration_computed_from_word_count() -> None:
    text = "alpha beta gamma delta epsilon"  # 5 words
    narration = strip_markers(text)
    wc = _count_words(narration)
    expected_dur = _segment_duration_s(wc)
    sections = [_make_section(0, "body", text)]
    draft = build_production_plan(_make_approved_script(sections=sections))
    assert draft.segments[0].estimated_duration_s == expected_dur


def test_total_duration_is_sum_of_segments() -> None:
    sections = [
        _make_section(0, "hook", "Short hook."),
        _make_section(1, "body", "Medium body section with more words."),
        _make_section(2, "cta", "Brief CTA."),
    ]
    draft = build_production_plan(_make_approved_script(sections=sections))
    assert draft.total_estimated_duration_s == sum(s.estimated_duration_s for s in draft.segments)


def test_total_word_count_is_sum_of_segments() -> None:
    sections = [
        _make_section(0, "hook", "one two three"),
        _make_section(1, "body", "four five six seven"),
        _make_section(2, "cta", "eight"),
    ]
    draft = build_production_plan(_make_approved_script(sections=sections))
    assert draft.total_word_count == sum(s.estimated_word_count for s in draft.segments)


# ---------------------------------------------------------------------------
# build_production_plan — field propagation
# ---------------------------------------------------------------------------


def test_script_id_propagates() -> None:
    draft = build_production_plan(_make_approved_script(script_id=99))
    assert draft.script_id == 99


def test_topic_id_propagates() -> None:
    draft = build_production_plan(_make_approved_script(topic_id=55))
    assert draft.topic_id == 55


def test_script_version_propagates() -> None:
    draft = build_production_plan(_make_approved_script(version=3))
    assert draft.script_version == 3


def test_format_propagates() -> None:
    draft = build_production_plan(_make_approved_script(format="long_form"))
    assert draft.format == "long_form"


def test_warnings_propagate_unchanged() -> None:
    warnings = ["stale evidence", "low confidence"]
    draft = build_production_plan(_make_approved_script(warnings=warnings))
    assert draft.warnings == warnings


def test_requires_evidence_review_true_propagates() -> None:
    draft = build_production_plan(_make_approved_script(requires_evidence_review=True))
    assert draft.requires_evidence_review is True


def test_requires_evidence_review_false_propagates() -> None:
    draft = build_production_plan(_make_approved_script(requires_evidence_review=False))
    assert draft.requires_evidence_review is False


def test_evidence_hash_propagates() -> None:
    ev_hash = "abcdef" * 10 + "ab"
    draft = build_production_plan(_make_approved_script(evidence_hash=ev_hash))
    assert draft.evidence_hash == ev_hash


def test_evidence_hash_none_becomes_empty_string() -> None:
    draft = build_production_plan(_make_approved_script(evidence_hash=None))
    assert draft.evidence_hash == ""


def test_generation_run_id_propagates() -> None:
    draft = build_production_plan(_make_approved_script(generation_run_id=77))
    assert draft.generation_run_id == 77


def test_generation_run_id_none_propagates() -> None:
    draft = build_production_plan(_make_approved_script(generation_run_id=None))
    assert draft.generation_run_id is None


def test_experiment_id_is_always_none() -> None:
    draft = build_production_plan(_make_approved_script())
    assert draft.experiment_id is None


def test_title_propagates_from_body_json() -> None:
    script = _make_approved_script()
    assert script.body_json.title == "Test Script"
    draft = build_production_plan(script)
    assert draft.title == "Test Script"


# ---------------------------------------------------------------------------
# build_production_plan — citation order
# ---------------------------------------------------------------------------


def test_citation_claim_ids_preserve_source_order() -> None:
    # cited_claim_ids in the section should appear in the same order in the segment
    sections = [_make_section(0, "body", "Text [claim:5] [claim:2] [claim:9].", [5, 2, 9])]
    draft = build_production_plan(_make_approved_script(sections=sections))
    assert draft.segments[0].citation_claim_ids == [5, 2, 9]


def test_citation_claim_ids_empty_when_none() -> None:
    sections = [_make_section(0, "body", "No citations here.")]
    draft = build_production_plan(_make_approved_script(sections=sections))
    assert draft.segments[0].citation_claim_ids == []


def test_citation_claim_ids_independent_per_segment() -> None:
    sections = [
        _make_section(0, "body", "Seg0 [claim:1].", [1]),
        _make_section(1, "body", "Seg1 [claim:2] [claim:3].", [2, 3]),
        _make_section(2, "cta", "Seg2 no cits."),
    ]
    draft = build_production_plan(_make_approved_script(sections=sections))
    assert draft.segments[0].citation_claim_ids == [1]
    assert draft.segments[1].citation_claim_ids == [2, 3]
    assert draft.segments[2].citation_claim_ids == []


# ---------------------------------------------------------------------------
# build_production_plan — hash determinism and input_hash propagation
# ---------------------------------------------------------------------------


def test_input_hash_is_64_hex_chars() -> None:
    draft = build_production_plan(_make_approved_script())
    assert len(draft.input_hash) == 64
    assert all(c in "0123456789abcdef" for c in draft.input_hash)


def test_script_body_hash_is_64_hex_chars() -> None:
    draft = build_production_plan(_make_approved_script())
    assert len(draft.script_body_hash) == 64
    assert all(c in "0123456789abcdef" for c in draft.script_body_hash)


def test_input_hash_changes_when_script_id_changes() -> None:
    d1 = build_production_plan(_make_approved_script(script_id=1))
    d2 = build_production_plan(_make_approved_script(script_id=2))
    assert d1.input_hash != d2.input_hash


def test_input_hash_changes_when_version_changes() -> None:
    d1 = build_production_plan(_make_approved_script(version=1))
    d2 = build_production_plan(_make_approved_script(version=2))
    assert d1.input_hash != d2.input_hash


def test_script_body_hash_changes_when_text_changes() -> None:
    sections_a = [_make_section(0, "body", "Version A text.")]
    sections_b = [_make_section(0, "body", "Version B text.")]
    d1 = build_production_plan(_make_approved_script(sections=sections_a))
    d2 = build_production_plan(_make_approved_script(sections=sections_b))
    assert d1.script_body_hash != d2.script_body_hash
    assert d1.input_hash != d2.input_hash


# ---------------------------------------------------------------------------
# plan_draft_to_json_summary
# ---------------------------------------------------------------------------


def test_plan_draft_to_json_summary_is_valid_json() -> None:
    import json
    draft = build_production_plan(_make_approved_script())
    summary = plan_draft_to_json_summary(draft)
    parsed = json.loads(summary)
    assert parsed["script_id"] == draft.script_id
    assert parsed["segment_count"] == len(draft.segments)
    assert parsed["input_hash"] == draft.input_hash


def test_plan_draft_to_json_summary_includes_required_keys() -> None:
    import json
    draft = build_production_plan(_make_approved_script())
    parsed = json.loads(plan_draft_to_json_summary(draft))
    required = {
        "topic_id", "script_id", "script_version", "title", "format",
        "total_estimated_duration_s", "total_word_count", "segment_count",
        "requires_evidence_review", "warnings", "input_hash",
    }
    assert required <= parsed.keys()
