"""Deterministic validation pipeline for Phase 5 generated scripts.

validate_script() runs all 12 steps in order. No Script or citation row may
be written before this function returns successfully.

Steps:
  1-3   Structural validation (done earlier by LLMGeneratedScript + from_llm)
  4     Citation-marker parsing with exact regex
  5     Marker/list bidirectional equivalence per section
  6-8   Claim-ID existence and active-evidence membership
  9     Section citation sanity (no orphaned citations)
  10    Evidence-review date warnings
  11    Local word-count and duration calculation
  12    Deterministic body rendering

Marker syntax: [claim:N] where N is a positive integer with no leading zeros.
Malformed claim-like patterns (e.g. [claim:0], [claim:01], [claim:abc]) are
rejected as errors rather than treated as plain text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.content.constants import (
    SCRIPT_DURATION_TOLERANCE_S,
    SHORT_FORM_DEFAULT_DURATION_S,
)
from app.content.errors import ScriptValidationError
from app.content.renderer import compute_duration_s, count_words, render_body
from app.content.schemas import GeneratedScript, ScriptCitation
from app.research.models import EvidenceClaim

# Detects any bracket that looks like a claim marker (for malformed detection)
_CLAIM_LIKE_RE = re.compile(r"\[claim:([^\]]*)\]")
# The only valid marker form
_VALID_MARKER_RE = re.compile(r"\[claim:([1-9]\d*)\]")


@dataclass
class ValidationResult:
    """Output of a successful validate_script() call."""

    script: GeneratedScript
    word_count: int
    duration_s: int
    body: str
    pending_citations: list[ScriptCitation] = field(default_factory=list)


def _parse_section_markers(section_index: int, section_type: str, text: str) -> list[int]:
    """Return marker IDs in first-left-to-right order, rejecting malformed patterns.

    Raises ScriptValidationError for any [claim:...] that is not a valid marker.
    Returns a list of unique IDs in the order of their first appearance.
    """
    for m in _CLAIM_LIKE_RE.finditer(text):
        inner = m.group(1)
        if not re.fullmatch(r"[1-9]\d*", inner):
            raise ScriptValidationError(
                f"Section {section_index} ({section_type}): malformed citation marker "
                f"[claim:{inner}] — markers must be positive integers with no leading zeros"
            )

    seen: dict[int, int] = {}
    for m in _VALID_MARKER_RE.finditer(text):
        cid = int(m.group(1))
        if cid not in seen:
            seen[cid] = m.start()
    return sorted(seen.keys(), key=lambda k: seen[k])


def validate_script(
    script: GeneratedScript,
    evidence: list[EvidenceClaim],
    *,
    allow_no_evidence: bool = False,
    target_duration_s: int = SHORT_FORM_DEFAULT_DURATION_S,
) -> ValidationResult:
    """Run all validation steps and return a ValidationResult.

    Raises:
        ScriptValidationError: Any structural, citation, or evidence violation.
    """
    warnings: list[str] = []
    pending_citations: list[ScriptCitation] = []

    evidence_by_id = {c.claim_id: c for c in evidence}
    zero_evidence = len(evidence) == 0

    # Steps 4–9: per-section citation validation
    for section in script.sections:
        marker_ids = _parse_section_markers(
            section.section_index, section.section_type, section.text
        )
        marker_set = set(marker_ids)
        declared_set = set(section.cited_claim_ids)

        # Invariant 11: zero-evidence scripts may not contain any markers or declared IDs
        if zero_evidence and (marker_ids or section.cited_claim_ids):
            raise ScriptValidationError(
                "Zero-evidence script must contain no citation markers or cited_claim_ids"
            )

        # Invariant 6: bidirectional equivalence
        missing_from_declared = marker_set - declared_set
        if missing_from_declared:
            raise ScriptValidationError(
                f"Section {section.section_index} ({section.section_type}): "
                f"marker IDs not declared in cited_claim_ids: {sorted(missing_from_declared)}"
            )
        missing_from_markers = declared_set - marker_set
        if missing_from_markers:
            raise ScriptValidationError(
                f"Section {section.section_index} ({section.section_type}): "
                f"cited_claim_ids declared but not found as markers: {sorted(missing_from_markers)}"
            )

        # Invariants 6–8: claim IDs must exist in active evidence for the topic
        for cid in marker_ids:
            if cid not in evidence_by_id:
                raise ScriptValidationError(
                    f"Section {section.section_index}: cited claim ID {cid} "
                    f"is not in the active evidence for this topic"
                )

        # Step 8: build ordered citation rows (script_id=0 placeholder)
        for order, cid in enumerate(marker_ids):
            pending_citations.append(
                ScriptCitation(
                    script_id=0,
                    claim_id=cid,
                    section_index=section.section_index,
                    citation_order=order,
                )
            )

    # Step 10: evidence-review warnings
    cited_ids = {c.claim_id for c in pending_citations}
    for cid in sorted(cited_ids):
        claim = evidence_by_id.get(cid)
        if claim and claim.requires_date_review:
            warnings.append(
                f"Claim {cid} requires date review — citation may be time-sensitive"
            )

    # Step 11: local word count and duration
    word_count = count_words(script)
    duration_s = compute_duration_s(word_count)
    deviation = abs(duration_s - target_duration_s)
    if deviation > SCRIPT_DURATION_TOLERANCE_S:
        warnings.append(
            f"Computed duration {duration_s}s deviates {deviation}s from "
            f"target {target_duration_s}s (tolerance {SCRIPT_DURATION_TOLERANCE_S}s)"
        )

    # Step 12: deterministic rendering (on script enriched with warnings)
    final_script = script.model_copy(update={"warnings": warnings})
    body = render_body(final_script)

    return ValidationResult(
        script=final_script,
        word_count=word_count,
        duration_s=duration_s,
        body=body,
        pending_citations=pending_citations,
    )
