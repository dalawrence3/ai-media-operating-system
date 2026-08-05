"""Pydantic models representing the core domain entities."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TopicStatus(StrEnum):
    active = "active"
    archived = "archived"


class SourceKind(StrEnum):
    url = "url"
    file = "file"
    note = "note"


class ScriptStatus(StrEnum):
    draft = "draft"
    approved = "approved"
    rejected = "rejected"


class RunStatus(StrEnum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class Topic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    title: str = Field(min_length=1, max_length=200)
    angle: str = Field(default="", max_length=500)
    status: TopicStatus = TopicStatus.active
    promoted_opportunity_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("title", mode="before")
    @classmethod
    def strip_title(cls, v: Any) -> Any:
        return v.strip() if isinstance(v, str) else v


class Source(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    topic_id: int
    kind: SourceKind
    reference: str = Field(min_length=1, max_length=2000)
    notes: str = Field(default="", max_length=2000)
    created_at: datetime | None = None


class Script(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    topic_id: int
    version: int = Field(ge=1)
    body: str = Field(min_length=1)
    status: ScriptStatus = ScriptStatus.draft
    created_at: datetime | None = None
    updated_at: datetime | None = None
    # Phase 5 additions — NULL for manual scripts (backward-compatible defaults)
    body_json: str | None = None
    format: str = "short"
    approved_at: str | None = None
    superseded_at: str | None = None


class Run(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    topic_id: int
    script_id: int | None = None
    status: RunStatus = RunStatus.pending
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    created_at: datetime | None = None
