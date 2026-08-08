"""Tests for Phase 5 validation pipeline (Stage 5)."""

from __future__ import annotations

import pytest

from app.content.constants import (
    SHORT_FORM_DEFAULT_DURATION_S,
    SHORT_FORM_MAX_DURATION_S,
    SHORT_FORM_MIN_DURATION_S,
)
from app.content.errors import ScriptValidationError
from app.content.schemas import GeneratedScript, LLMGeneratedScript
from app.content.validator import ValidationResult, validate_script
from app.research.models import ClaimSupportStatus, ClaimType, EvidenceClaim

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _evidence(
    claim_id: int,
    source_id: int = 1,
    requires_date_review: bool = False,
    quality_score: float | None = 0.8,
) -> EvidenceClaim:
    return EvidenceClaim(
        claim_id=claim_id,
        claim_text=f"Claim {claim_id} text.",
        claim_type=ClaimType.factual,
        supporting_quote="Some quote.",
        quote_support_status=ClaimSupportStatus.exact,
        quote_start=0,
        quote_end=10,
        page_number=None,
        chunk_index=0,
        requires_date_review=requires_date_review,
        source_id=source_id,
        source_content_id=claim_id,
        extraction_run_id=1,
        source_title=f"Source {source_id}",
        canonical_url=None,
        author=None,
        published_at=None,
        quality_score=quality_score,
        extraction_status="ok",
        suspected_truncation=False,
        prompt_name="test",
        prompt_version="1",
        model="fake",
    )


def _script_from_sections(sections: list[dict]) -> GeneratedScript:
    llm = LLMGeneratedScript(title="Test Script", sections=sections)
    return GeneratedScript.from_llm(llm)


def _simple_script(
    hook_text: str = "Hook text.",
    hook_cited: list[int] | None = None,
) -> GeneratedScript:
    return _script_from_sections([
        {
            "section_type": "hook",
            "text": hook_text,
            "cited_claim_ids": hook_cited or [],
        }
    ])


# ---------------------------------------------------------------------------
# Happy-path validation
# ---------------------------------------------------------------------------


class TestValidateScriptSuccess:
    def test_no_citations(self):
        script = _simple_script()
        result = validate_script(script, evidence=[])
        # No evidence, no markers → OK with allow_no_evidence default since evidence is empty
        # Wait — zero_evidence is True here, but there are no markers, so it passes
        assert isinstance(result, ValidationResult)
        assert result.word_count > 0
        assert result.body.startswith("# Test Script")

    def test_no_citations_with_evidence(self):
        script = _simple_script(hook_text="Hook text.")
        result = validate_script(script, evidence=[_evidence(1)])
        assert result.pending_citations == []

    def test_single_citation(self):
        script = _simple_script(
            hook_text="Fact [claim:1] stated.",
            hook_cited=[1],
        )
        result = validate_script(script, evidence=[_evidence(1)])
        assert len(result.pending_citations) == 1
        assert result.pending_citations[0].claim_id == 1
        assert result.pending_citations[0].citation_order == 0

    def test_multiple_citations_order_preserved(self):
        script = _simple_script(
            hook_text="First [claim:3] then [claim:1] then [claim:2].",
            hook_cited=[3, 1, 2],
        )
        evidence = [_evidence(1), _evidence(2), _evidence(3)]
        result = validate_script(script, evidence=evidence)
        orders = [(c.claim_id, c.citation_order) for c in result.pending_citations]
        assert orders == [(3, 0), (1, 1), (2, 2)]

    def test_returns_body(self):
        script = _simple_script()
        result = validate_script(script, evidence=[])
        assert "[HOOK]" in result.body

    def test_returns_word_count(self):
        script = _simple_script(hook_text="one two three")
        result = validate_script(script, evidence=[])
        assert result.word_count == 3

    def test_duration_clamped_to_min(self):
        # Very short text → clamped to min
        script = _simple_script(hook_text="short")
        result = validate_script(script, evidence=[])
        assert result.duration_s == SHORT_FORM_MIN_DURATION_S

    def test_warnings_in_result_script(self):
        script = _simple_script(hook_text="short")
        result = validate_script(
            script, evidence=[], target_duration_s=SHORT_FORM_DEFAULT_DURATION_S
        )
        # Duration deviation from 60s target will exceed tolerance
        assert result.script.warnings is not None

    def test_cross_section_citations(self):
        llm = LLMGeneratedScript(
            title="T",
            sections=[
                {"section_type": "hook", "text": "Hook [claim:1].", "cited_claim_ids": [1]},
                {"section_type": "body", "text": "Body [claim:2].", "cited_claim_ids": [2]},
            ],
        )
        script = GeneratedScript.from_llm(llm)
        evidence = [_evidence(1), _evidence(2)]
        result = validate_script(script, evidence=evidence)
        assert len(result.pending_citations) == 2
        sections = {c.section_index for c in result.pending_citations}
        assert sections == {0, 1}


# ---------------------------------------------------------------------------
# Malformed marker detection
# ---------------------------------------------------------------------------


class TestMalformedMarkers:
    def test_leading_zero_rejected(self):
        script = _simple_script(hook_text="Fact [claim:01] here.", hook_cited=[])
        with pytest.raises(ScriptValidationError, match="malformed"):
            validate_script(script, evidence=[])

    def test_zero_rejected(self):
        script = _simple_script(hook_text="Fact [claim:0] here.", hook_cited=[])
        with pytest.raises(ScriptValidationError, match="malformed"):
            validate_script(script, evidence=[])

    def test_text_id_rejected(self):
        script = _simple_script(hook_text="Fact [claim:abc] here.", hook_cited=[])
        with pytest.raises(ScriptValidationError, match="malformed"):
            validate_script(script, evidence=[])

    def test_empty_id_rejected(self):
        script = _simple_script(hook_text="Fact [claim:] here.", hook_cited=[])
        with pytest.raises(ScriptValidationError, match="malformed"):
            validate_script(script, evidence=[])

    def test_valid_marker_not_rejected(self):
        script = _simple_script(hook_text="Fact [claim:1] here.", hook_cited=[1])
        # Should not raise
        validate_script(script, evidence=[_evidence(1)])

    def test_large_valid_id(self):
        script = _simple_script(hook_text="Fact [claim:999] here.", hook_cited=[999])
        validate_script(script, evidence=[_evidence(999)])


# ---------------------------------------------------------------------------
# Marker/list bidirectional equivalence (Invariant 6)
# ---------------------------------------------------------------------------


class TestMarkerListEquivalence:
    def test_marker_not_declared_fails(self):
        # Marker [claim:1] in text but cited_claim_ids=[]
        script = _simple_script(hook_text="Fact [claim:1] here.", hook_cited=[])
        with pytest.raises(ScriptValidationError, match="not declared"):
            validate_script(script, evidence=[_evidence(1)])

    def test_declared_not_in_text_fails(self):
        # cited_claim_ids=[1] but no marker in text
        script = _simple_script(hook_text="Plain text.", hook_cited=[1])
        with pytest.raises(ScriptValidationError, match="not found as markers"):
            validate_script(script, evidence=[_evidence(1)])

    def test_both_missing_fails(self):
        # Different IDs: marker uses 1, declared has 2
        script = _simple_script(hook_text="Text [claim:1].", hook_cited=[2])
        with pytest.raises(ScriptValidationError):
            validate_script(script, evidence=[_evidence(1), _evidence(2)])

    def test_exact_equivalence_passes(self):
        script = _simple_script(hook_text="Text [claim:1] and [claim:2].", hook_cited=[1, 2])
        validate_script(script, evidence=[_evidence(1), _evidence(2)])


# ---------------------------------------------------------------------------
# Claim-ID existence (Invariants 6–8)
# ---------------------------------------------------------------------------


class TestClaimIdExistence:
    def test_unknown_claim_id_fails(self):
        script = _simple_script(hook_text="Fact [claim:99].", hook_cited=[99])
        with pytest.raises(ScriptValidationError, match="not in the active evidence"):
            validate_script(script, evidence=[_evidence(1)])

    def test_known_claim_id_passes(self):
        script = _simple_script(hook_text="Fact [claim:1].", hook_cited=[1])
        validate_script(script, evidence=[_evidence(1)])

    def test_partially_missing_fails(self):
        script = _simple_script(
            hook_text="Fact [claim:1] and [claim:5].",
            hook_cited=[1, 5],
        )
        with pytest.raises(ScriptValidationError):
            validate_script(script, evidence=[_evidence(1)])


# ---------------------------------------------------------------------------
# Zero-evidence mode (Invariants 10–11)
# ---------------------------------------------------------------------------


class TestZeroEvidence:
    def test_no_markers_no_evidence_ok(self):
        script = _simple_script(hook_text="No citations here.")
        result = validate_script(script, evidence=[], allow_no_evidence=True)
        assert result.pending_citations == []

    def test_markers_with_zero_evidence_fails(self):
        script = _simple_script(hook_text="Fact [claim:1].", hook_cited=[1])
        with pytest.raises(ScriptValidationError, match="Zero-evidence"):
            validate_script(script, evidence=[], allow_no_evidence=True)

    def test_declared_ids_with_zero_evidence_fails(self):
        script = _simple_script(hook_text="Plain.", hook_cited=[1])
        with pytest.raises(ScriptValidationError):
            validate_script(script, evidence=[], allow_no_evidence=True)


# ---------------------------------------------------------------------------
# Evidence-review warnings (Step 10)
# ---------------------------------------------------------------------------


class TestEvidenceReviewWarnings:
    def test_requires_date_review_produces_warning(self):
        script = _simple_script(hook_text="Fact [claim:1].", hook_cited=[1])
        ev = _evidence(1, requires_date_review=True)
        result = validate_script(script, evidence=[ev])
        assert any("requires date review" in w for w in result.script.warnings)

    def test_no_review_needed_no_warning(self):
        script = _simple_script(hook_text="Fact [claim:1].", hook_cited=[1])
        ev = _evidence(1, requires_date_review=False)
        result = validate_script(script, evidence=[ev])
        assert not any("date review" in w for w in result.script.warnings)


# ---------------------------------------------------------------------------
# Duration warnings (Invariant 17)
# ---------------------------------------------------------------------------


class TestDurationWarnings:
    def test_deviation_within_tolerance_no_warning(self):
        # Target 60s, generate ~60s of content (150 words)
        words = " ".join(["word"] * 150)
        script = _simple_script(hook_text=words)
        result = validate_script(script, evidence=[], target_duration_s=60)
        duration_warnings = [w for w in result.script.warnings if "deviates" in w]
        assert len(duration_warnings) == 0

    def test_deviation_exceeds_tolerance_is_warning_not_error(self):
        # 5 words → ~2s computed, 60s target → >10s deviation
        script = _simple_script(hook_text="one two three four five")
        result = validate_script(script, evidence=[], target_duration_s=60)
        assert any("deviates" in w for w in result.script.warnings)

    def test_duration_hard_bounds_never_violated(self):
        # 300 words ≈ 120s unclamped → clamped to 90s
        words = " ".join(["word"] * 300)
        llm = LLMGeneratedScript(
            title="T",
            sections=[
                {"section_type": "hook", "text": words, "cited_claim_ids": []},
            ],
        )
        script = GeneratedScript.from_llm(llm)
        result = validate_script(script, evidence=[])
        assert result.duration_s == SHORT_FORM_MAX_DURATION_S

    def test_duration_min_clamped(self):
        script = _simple_script(hook_text="short")
        result = validate_script(script, evidence=[])
        assert result.duration_s == SHORT_FORM_MIN_DURATION_S


# ---------------------------------------------------------------------------
# Citation order (Invariant 8)
# ---------------------------------------------------------------------------


class TestCitationOrder:
    def test_first_appearance_order(self):
        # [claim:3] appears before [claim:1] in text
        script = _simple_script(
            hook_text="[claim:3] then [claim:1].",
            hook_cited=[3, 1],
        )
        evidence = [_evidence(1), _evidence(3)]
        result = validate_script(script, evidence=evidence)
        by_order = sorted(result.pending_citations, key=lambda c: c.citation_order)
        assert by_order[0].claim_id == 3
        assert by_order[1].claim_id == 1

    def test_repeated_marker_counted_once(self):
        # [claim:1] appears twice but only one citation row
        script = _simple_script(
            hook_text="[claim:1] and again [claim:1].",
            hook_cited=[1],
        )
        result = validate_script(script, evidence=[_evidence(1)])
        assert len(result.pending_citations) == 1
        assert result.pending_citations[0].citation_order == 0

    def test_section_indexes_in_citations(self):
        llm = LLMGeneratedScript(
            title="T",
            sections=[
                {"section_type": "hook", "text": "H [claim:1].", "cited_claim_ids": [1]},
                {"section_type": "body", "text": "B [claim:2].", "cited_claim_ids": [2]},
            ],
        )
        script = GeneratedScript.from_llm(llm)
        result = validate_script(script, evidence=[_evidence(1), _evidence(2)])
        idx_map = {c.claim_id: c.section_index for c in result.pending_citations}
        assert idx_map[1] == 0
        assert idx_map[2] == 1
