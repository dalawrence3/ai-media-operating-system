"""Pydantic models for Phase 4.1 source acquisition and quality scoring."""

from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


class FetchStatus(StrEnum):
    ok = "ok"
    failed = "failed"


class ExtractionStatus(StrEnum):
    ok = "ok"
    partial = "partial"
    failed = "failed"


class DomainType(StrEnum):
    academic = "academic"
    news = "news"
    government = "government"
    blog = "blog"
    forum = "forum"
    unknown = "unknown"


class ExtractionMethod(StrEnum):
    html_parser = "html_parser"
    pdf = "pdf"
    plaintext = "plaintext"
    markdown = "markdown"


class SourceContent(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    source_id: int
    fetch_status: FetchStatus
    extraction_status: ExtractionStatus
    http_status: int | None = None
    canonical_url: str | None = None
    mime_type: str | None = None
    fetched_at: datetime
    raw_text: str | None = None
    retrieval_hash: str | None = None
    normalized_text_hash: str | None = None
    hash_algorithm: str = "sha256-nfc-v1"
    word_count: int | None = None
    title: str | None = None
    author: str | None = None
    published_at: str | None = None
    domain_type: DomainType | None = None
    extraction_method: ExtractionMethod | None = None
    extraction_error: str | None = None
    suspected_truncation: bool = False
    quality_score: float | None = None
    quality_factors_json: str | None = None
    quality_scorer_version: str | None = None
    created_at: datetime | None = None

    @field_validator("fetched_at", mode="before")
    @classmethod
    def parse_fetched_at(cls, v: Any) -> Any:
        if isinstance(v, str):
            return datetime.fromisoformat(v)
        return v

    @field_validator("created_at", mode="before")
    @classmethod
    def parse_created_at(cls, v: Any) -> Any:
        if isinstance(v, str):
            return datetime.fromisoformat(v)
        return v

    @property
    def quality_factors(self) -> dict[str, Any] | None:
        if self.quality_factors_json is None:
            return None
        return json.loads(self.quality_factors_json)  # type: ignore[no-any-return]

    @property
    def is_successful(self) -> bool:
        return self.fetch_status == FetchStatus.ok and self.extraction_status in (
            ExtractionStatus.ok,
            ExtractionStatus.partial,
        )
