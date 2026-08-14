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
import re

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


def _split_narration_into_chunks(text: str, max_duration_s: int) -> list[str]:
    """Split narration at sentence boundaries so each chunk fits within max_duration_s.

    Sentences that individually exceed the limit are kept as single chunks (no
    mid-sentence splits). Consecutive short sentences are greedy-grouped until
    the next sentence would push the chunk over the word limit.
    """
    max_words = max(1, round(max_duration_s * SCRIPT_WORDS_PER_MINUTE / 60))
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]
    if not sentences:
        return [text] if text.strip() else []

    chunks: list[str] = []
    current: list[str] = []
    current_words = 0

    for sentence in sentences:
        words = _count_words(sentence)
        if current and current_words + words > max_words:
            chunks.append(" ".join(current))
            current = [sentence]
            current_words = words
        else:
            current.append(sentence)
            current_words += words

    if current:
        chunks.append(" ".join(current))

    return chunks


def build_production_plan(
    approved_script: ApprovedScript,
    max_segment_duration_s: int | None = None,
) -> ProductionPlanDraft:
    """Deterministically convert an ApprovedScript into a ProductionPlanDraft.

    Pure function: same input always produces the same output.
    No DB access. No external calls.

    When max_segment_duration_s is set, sections whose estimated duration exceeds
    that limit are split at sentence boundaries into multiple shorter segments.
    All sub-segments from one section share the same section_index and section_type.
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
        max_segment_duration_s=max_segment_duration_s,
    )

    segments: list[ProductionSegmentDraft] = []
    segment_index = 0
    for section in approved_script.body_json.sections:
        narration_text = strip_markers(section.text)
        word_count = _count_words(narration_text)
        duration_s = _segment_duration_s(word_count)

        if max_segment_duration_s and duration_s > max_segment_duration_s:
            chunks = _split_narration_into_chunks(narration_text, max_segment_duration_s)
        else:
            chunks = [narration_text]

        for chunk_text in chunks:
            chunk_wc = _count_words(chunk_text)
            chunk_dur = _segment_duration_s(chunk_wc)
            segments.append(
                ProductionSegmentDraft(
                    segment_index=segment_index,
                    section_index=section.section_index,
                    section_type=section.section_type,
                    narration_text=chunk_text,
                    estimated_duration_s=chunk_dur,
                    estimated_word_count=chunk_wc,
                    citation_claim_ids=list(section.cited_claim_ids),
                )
            )
            segment_index += 1

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
