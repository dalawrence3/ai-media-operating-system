"""Phase 5 domain models for script generation runs."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


class ScriptGenerationRunStatus(StrEnum):
    running = "running"
    completed = "completed"
    failed = "failed"


class ScriptGenerationRun(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    topic_id: int
    script_id: int | None = None
    status: ScriptGenerationRunStatus
    input_hash: str
    evidence_hash: str
    prompt_hash: str
    prompt_name: str
    prompt_version: str
    model: str
    temperature: float
    max_tokens: int
    tone: str
    audience: str
    target_duration_s: int
    computed_word_count: int | None = None
    computed_duration_s: int | None = None
    warnings_json: str | None = None
    requires_evidence_review: bool = False
    ai_call_id: int | None = None
    error_message: str | None = None
    superseded_at: str | None = None
    superseded_by_run_id: int | None = None
    started_at: str
    completed_at: str | None = None
    created_at: datetime | None = None

    @field_validator("created_at", mode="before")
    @classmethod
    def _parse_created_at(cls, v: Any) -> Any:
        if isinstance(v, str):
            return datetime.fromisoformat(v)
        return v
