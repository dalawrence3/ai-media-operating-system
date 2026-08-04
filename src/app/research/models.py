"""Pydantic models for Phase 4.1 source acquisition and Phase 4.2 claim extraction."""

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


# ---------------------------------------------------------------------------
# Phase 4.2 — claim extraction models
# ---------------------------------------------------------------------------


class ClaimType(StrEnum):
    factual = "factual"
    statistical = "statistical"
    attribution = "attribution"
    definition = "definition"


class ClaimSupportStatus(StrEnum):
    exact = "exact"
    normalized = "normalized"
    unsupported = "unsupported"
    no_quote = "no_quote"


class ClaimExtractionRunStatus(StrEnum):
    running = "running"
    completed = "completed"
    partial = "partial"
    failed = "failed"


class ClaimExtractionRunCallStatus(StrEnum):
    running = "running"
    completed = "completed"
    failed = "failed"


def _parse_dt(v: Any) -> Any:
    if isinstance(v, str):
        return datetime.fromisoformat(v)
    return v


class ClaimExtractionRun(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    source_content_id: int
    status: ClaimExtractionRunStatus
    input_hash: str
    total_chunk_count: int
    completed_chunk_count: int = 0
    failed_chunk_count: int = 0
    accepted_claim_count: int | None = None
    was_truncated: bool = False
    prompt_name: str = ""
    prompt_version: str = ""
    model: str
    provider: str
    extraction_algo_version: str
    error_message: str | None = None
    superseded_at: str | None = None
    superseded_by_run_id: int | None = None
    started_at: str
    completed_at: str | None = None
    created_at: datetime | None = None

    @field_validator("created_at", mode="before")
    @classmethod
    def _parse_created_at(cls, v: Any) -> Any:
        return _parse_dt(v)


class ClaimExtractionRunCall(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    claim_extraction_run_id: int
    ai_call_id: int | None = None
    chunk_index: int
    chunk_hash: str
    input_char_start: int
    input_char_end: int
    status: ClaimExtractionRunCallStatus
    retry_count: int = 0
    accepted_claim_count: int | None = None
    error_message: str | None = None
    started_at: str
    completed_at: str | None = None
    created_at: datetime | None = None

    @field_validator("created_at", mode="before")
    @classmethod
    def _parse_created_at(cls, v: Any) -> Any:
        return _parse_dt(v)


class Claim(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    extraction_run_id: int
    chunk_index: int
    claim_text: str
    claim_type: ClaimType
    supporting_quote: str | None = None
    quote_support_status: ClaimSupportStatus
    quote_start: int | None = None
    quote_end: int | None = None
    page_number: int | None = None
    requires_date_review: bool = False
    created_at: datetime | None = None

    @field_validator("created_at", mode="before")
    @classmethod
    def _parse_created_at(cls, v: Any) -> Any:
        return _parse_dt(v)


class EvidenceClaim(BaseModel):
    """Read-only evidence record assembled for Phase 5 consumption."""

    model_config = ConfigDict(frozen=True)

    claim_id: int
    claim_text: str
    claim_type: ClaimType
    supporting_quote: str | None
    quote_support_status: ClaimSupportStatus
    quote_start: int | None
    quote_end: int | None
    page_number: int | None
    chunk_index: int
    requires_date_review: bool
    source_id: int
    source_content_id: int
    extraction_run_id: int
    source_title: str | None
    canonical_url: str | None
    author: str | None
    published_at: str | None
    quality_score: float | None
    extraction_status: str
    suspected_truncation: bool
    prompt_name: str
    prompt_version: str
    model: str
