"""Phase 5 structured-output and domain schemas.

LLM output models (LLMScriptSection, LLMGeneratedScript) use extra='forbid'
so unexpected fields surface as validation errors rather than being silently
dropped.  Section indexes are system-assigned; the LLM never supplies them.
Word count and duration are computed locally; the LLM never supplies them.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# LLM output models (provider boundary — what the model returns)
# ---------------------------------------------------------------------------

_SECTION_TYPES = Literal["hook", "intro", "body", "transition", "conclusion", "cta"]


class LLMScriptSection(BaseModel):
    """One section as returned by the LLM.  No section_index — system-assigned."""

    model_config = ConfigDict(extra="forbid")

    section_type: _SECTION_TYPES
    text: str = Field(min_length=1, max_length=2000)
    cited_claim_ids: list[int] = Field(default_factory=list, max_length=20)

    @field_validator("cited_claim_ids")
    @classmethod
    def _no_duplicates(cls, v: list[int]) -> list[int]:
        if len(v) != len(set(v)):
            raise ValueError("cited_claim_ids must not contain duplicate IDs")
        return v


class LLMGeneratedScript(BaseModel):
    """Top-level structured output from the LLM."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=80)
    sections: list[LLMScriptSection] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def _require_hook(self) -> LLMGeneratedScript:
        hook_count = sum(1 for s in self.sections if s.section_type == "hook")
        if hook_count != 1:
            raise ValueError(f"Script must contain exactly one hook section; found {hook_count}")
        cta_count = sum(1 for s in self.sections if s.section_type == "cta")
        if cta_count > 1:
            raise ValueError(f"Script may contain at most one cta section; found {cta_count}")
        return self


# ---------------------------------------------------------------------------
# Internal domain models (after system enrichment)
# ---------------------------------------------------------------------------


class ScriptSection(BaseModel):
    """Script section with system-assigned index."""

    model_config = ConfigDict(from_attributes=True)

    section_index: int
    section_type: _SECTION_TYPES
    text: str
    cited_claim_ids: list[int]


class GeneratedScript(BaseModel):
    """Canonical internal representation stored in scripts.body_json."""

    model_config = ConfigDict(from_attributes=True)

    title: str
    sections: list[ScriptSection]
    warnings: list[str] = Field(default_factory=list)

    @classmethod
    def from_llm(cls, llm: LLMGeneratedScript) -> GeneratedScript:
        """Assign section indexes from list position."""
        sections = [
            ScriptSection(
                section_index=i,
                section_type=s.section_type,
                text=s.text,
                cited_claim_ids=list(s.cited_claim_ids),
            )
            for i, s in enumerate(llm.sections)
        ]
        return cls(title=llm.title, sections=sections)


# ---------------------------------------------------------------------------
# Citation model
# ---------------------------------------------------------------------------


class ScriptCitation(BaseModel):
    """One citation row: one claim in one section at one position."""

    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    script_id: int
    claim_id: int
    section_index: int
    citation_order: int
    created_at: datetime | None = None

    @field_validator("created_at", mode="before")
    @classmethod
    def _parse_dt(cls, v: Any) -> Any:
        if isinstance(v, str):
            return datetime.fromisoformat(v)
        return v


# ---------------------------------------------------------------------------
# Phase 6 handoff model
# ---------------------------------------------------------------------------


class ApprovedScript(BaseModel):
    """Structured handoff object for Phase 6.  Never constructed from manual scripts."""

    model_config = ConfigDict(frozen=True)

    script_id: int
    topic_id: int
    version: int
    body_json: GeneratedScript
    body: str
    format: str
    computed_duration_s: int
    computed_word_count: int
    status: Literal["approved"]
    citations: list[ScriptCitation]
    warnings: list[str]
    requires_evidence_review: bool
    generation_run_id: int | None
    model: str | None
    prompt_name: str | None
    prompt_hash: str | None
    evidence_hash: str | None
    approved_at: str | None

    @classmethod
    def from_row_and_run(
        cls,
        script_row: Any,
        body_json: GeneratedScript,
        body: str,
        citations: list[ScriptCitation],
        run_row: Any | None,
    ) -> ApprovedScript:
        warnings: list[str] = []
        requires_evidence_review = False
        generation_run_id = None
        model = None
        prompt_name = None
        prompt_hash = None
        evidence_hash = None
        computed_duration_s = 0
        computed_word_count = 0

        if run_row is not None:
            warnings_raw = run_row["warnings_json"]
            warnings = json.loads(warnings_raw) if warnings_raw else []
            requires_evidence_review = bool(run_row["requires_evidence_review"])
            generation_run_id = run_row["id"]
            model = run_row["model"]
            prompt_name = run_row["prompt_name"]
            prompt_hash = run_row["prompt_hash"]
            evidence_hash = run_row["evidence_hash"]
            computed_duration_s = run_row["computed_duration_s"] or 0
            computed_word_count = run_row["computed_word_count"] or 0

        return cls(
            script_id=script_row["id"],
            topic_id=script_row["topic_id"],
            version=script_row["version"],
            body_json=body_json,
            body=body,
            format=script_row["format"],
            computed_duration_s=computed_duration_s,
            computed_word_count=computed_word_count,
            status="approved",
            citations=citations,
            warnings=warnings,
            requires_evidence_review=requires_evidence_review,
            generation_run_id=generation_run_id,
            model=model,
            prompt_name=prompt_name,
            prompt_hash=prompt_hash,
            evidence_hash=evidence_hash,
            approved_at=script_row["approved_at"],
        )
