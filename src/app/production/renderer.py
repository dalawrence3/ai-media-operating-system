"""Deterministic production plan builder for Phase 6 M6.1.

build_production_plan() is a pure function: given the same ApprovedScript
it always produces the same ProductionPlanDraft.

Hard invariant:
    segment.narration_text == strip_markers(script_section.text)

No other transformation is applied to section text.  Citation markers are
the only permitted change.  Word counts and durations are computed locally
from the resulting narration text using SCRIPT_WORDS_PER_MINUTE.
"""

from __future__ import annotations

import json
import math

from app.content.constants import SCRIPT_WORDS_PER_MINUTE
from app.content.renderer import strip_markers
from app.content.schemas import ApprovedScript
from app.production.constants import (
    PRODUCTION_DURATION_VERSION,
    PRODUCTION_PLAN_RENDERER_VERSION,
    PRODUCTION_PLAN_SCHEMA_VERSION,
)
from app.production.hashing import (
    compute_production_plan_input_hash,
    compute_script_body_hash,
)
from app.production.models import ProductionPlanDraft, ProductionSegmentDraft


def _count_words(text: str) -> int:
    """Count words in plain text by splitting on whitespace."""
    stripped = text.strip()
    if not stripped:
        return 0
    return len(stripped.split())


def _segment_duration_s(word_count: int) -> int:
    """Convert word count to seconds using SCRIPT_WORDS_PER_MINUTE (unclamped; minimum 1s)."""
    if word_count == 0:
        return 0
    return max(1, math.ceil(word_count / SCRIPT_WORDS_PER_MINUTE * 60))


def build_production_plan(approved_script: ApprovedScript) -> ProductionPlanDraft:
    """Deterministically convert an ApprovedScript into a ProductionPlanDraft.

    Pure function: same input always produces the same output.
    No DB access. No external calls.
    """
    body_json_str = approved_script.body_json.model_dump_json()
    script_body_hash = compute_script_body_hash(body_json_str)

    input_hash = compute_production_plan_input_hash(
        script_id=approved_script.script_id,
        script_version=approved_script.version,
        script_body_hash=script_body_hash,
        plan_schema_version=PRODUCTION_PLAN_SCHEMA_VERSION,
        renderer_version=PRODUCTION_PLAN_RENDERER_VERSION,
        duration_algorithm_version=PRODUCTION_DURATION_VERSION,
        script_format=approved_script.format,
        evidence_hash=approved_script.evidence_hash or "",
        requires_evidence_review=approved_script.requires_evidence_review,
    )

    segments: list[ProductionSegmentDraft] = []
    for segment_index, section in enumerate(approved_script.body_json.sections):
        narration_text = strip_markers(section.text)
        word_count = _count_words(narration_text)
        duration_s = _segment_duration_s(word_count)

        segments.append(
            ProductionSegmentDraft(
                segment_index=segment_index,
                section_index=section.section_index,
                section_type=section.section_type,
                narration_text=narration_text,
                estimated_duration_s=duration_s,
                estimated_word_count=word_count,
                citation_claim_ids=list(section.cited_claim_ids),
            )
        )

    total_duration_s = sum(s.estimated_duration_s for s in segments)
    total_word_count = sum(s.estimated_word_count for s in segments)

    return ProductionPlanDraft(
        topic_id=approved_script.topic_id,
        script_id=approved_script.script_id,
        script_version=approved_script.version,
        input_hash=input_hash,
        script_body_hash=script_body_hash,
        plan_schema_version=PRODUCTION_PLAN_SCHEMA_VERSION,
        renderer_version=PRODUCTION_PLAN_RENDERER_VERSION,
        duration_algorithm_version=PRODUCTION_DURATION_VERSION,
        title=approved_script.body_json.title,
        format=approved_script.format,
        total_estimated_duration_s=total_duration_s,
        total_word_count=total_word_count,
        warnings=list(approved_script.warnings),
        requires_evidence_review=approved_script.requires_evidence_review,
        evidence_hash=approved_script.evidence_hash or "",
        generation_run_id=approved_script.generation_run_id,
        experiment_id=None,
        segments=segments,
    )


def plan_draft_to_json_summary(draft: ProductionPlanDraft) -> str:
    """Return a compact JSON summary of the draft suitable for dry-run CLI display."""
    return json.dumps(
        {
            "topic_id": draft.topic_id,
            "script_id": draft.script_id,
            "script_version": draft.script_version,
            "title": draft.title,
            "format": draft.format,
            "total_estimated_duration_s": draft.total_estimated_duration_s,
            "total_word_count": draft.total_word_count,
            "segment_count": len(draft.segments),
            "requires_evidence_review": draft.requires_evidence_review,
            "warnings": draft.warnings,
            "input_hash": draft.input_hash,
        },
        indent=2,
    )
