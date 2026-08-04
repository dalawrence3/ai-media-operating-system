"""Phase 4.2 claim extraction orchestrator.

Calls the AI provider once per chunk, classifies quote support locally,
applies date-review risk signals, and persists all results atomically.
No live LLM calls are made in tests — callers supply the provider.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.ai.provider import AIRequest, AIResponse
from app.ai.registry import PromptRegistry
from app.ai.usage import record_ai_call
from app.core.logging import get_logger
from app.research.chunking import (
    Chunk,
    chunk_text,
    compute_input_hash,
    deduplicate_claims,
)
from app.research.claim_risk import compute_requires_date_review
from app.research.claim_support import classify_quote_support, derive_page_number
from app.research.constants import (
    CLAIM_EXTRACTION_PROMPT_NAME,
    CLAIM_EXTRACTION_PROMPT_VERSION,
    EXTRACTION_ALGO_VERSION,
)
from app.research.errors import ClaimExtractionError
from app.research.models import (
    ClaimExtractionRun,
    ClaimExtractionRunCallStatus,
    ClaimExtractionRunStatus,
    ClaimType,
    SourceContent,
)
from app.research.repository import (
    create_claim_extraction_run,
    create_claim_extraction_run_call,
    finalize_claim_extraction_run,
    get_latest_completed_run,
    update_claim_extraction_run_call,
)
from app.research.schemas import ClaimExtractionOutput

if TYPE_CHECKING:
    from app.ai.provider import AIProvider
    from app.core.config import Config

logger = get_logger(__name__)

_DEFAULT_TEMPERATURE = 0.3
_DEFAULT_MAX_TOKENS = 2048


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _build_provider(cfg: Config) -> AIProvider:
    """Return the configured AI provider. Never called in tests."""
    if cfg.dry_run or cfg.ai_provider == "fake":
        from app.ai.fake import FakeProvider

        return FakeProvider()
    from app.ai.claude import ClaudeProvider

    return ClaudeProvider(
        api_key=cfg.anthropic_api_key,
        model=cfg.ai_model,
        timeout=cfg.ai_timeout,
        max_retries=cfg.ai_max_retries,
    )


def _process_chunk(
    conn: sqlite3.Connection,
    *,
    chunk: Chunk,
    run_id: int,
    source: SourceContent,
    request: AIRequest,
    provider: AIProvider,
    temperature: float,
    max_tokens: int,
) -> list[dict] | None:
    """Call the AI for one chunk, record the call, return claim dicts or None on failure."""
    started_at = _now()
    run_call = create_claim_extraction_run_call(
        conn,
        claim_extraction_run_id=run_id,
        chunk_index=chunk.index,
        chunk_hash=chunk.chunk_hash,
        input_char_start=chunk.char_start,
        input_char_end=chunk.char_end,
        started_at=started_at,
    )

    response: AIResponse | None = None
    error_msg: str | None = None
    call_status = ClaimExtractionRunCallStatus.failed
    claims_out: list[dict] | None = None

    try:
        response = provider.complete(request)
        # Mark as completed tentatively; may revert below on parse error.
        call_status = ClaimExtractionRunCallStatus.completed
        claims_out = []

        parsed = response.parsed
        if parsed is None:
            # Parse raw_text as JSON fallback
            try:
                data = json.loads(response.raw_text)
                parsed = ClaimExtractionOutput.model_validate(data)
            except (json.JSONDecodeError, Exception) as exc:
                raise ClaimExtractionError(f"Could not parse LLM output: {exc}") from exc

        extracted = parsed.claims if isinstance(parsed, ClaimExtractionOutput) else []

        for ec in extracted:
            support_status, q_start, q_end = classify_quote_support(
                ec.supporting_quote, source.raw_text or ""
            )
            page_num = derive_page_number(
                source.raw_text or "", q_start, source.extraction_method
            )
            claim_type = ClaimType(ec.claim_type)
            requires_review = compute_requires_date_review(ec.claim_text, claim_type, source)
            claims_out.append(
                dict(
                    chunk_index=chunk.index,
                    claim_text=ec.claim_text,
                    claim_type=ec.claim_type,
                    supporting_quote=ec.supporting_quote,
                    quote_support_status=support_status.value,
                    quote_start=q_start,
                    quote_end=q_end,
                    page_number=page_num,
                    requires_date_review=requires_review,
                )
            )

    except Exception as exc:
        error_msg = str(exc)
        call_status = ClaimExtractionRunCallStatus.failed
        logger.warning("Chunk %d extraction failed: %s", chunk.index, exc)

    finally:
        completed_at = _now()
        ai_call_id = record_ai_call(
            conn,
            request,
            response,
            status="success" if call_status == ClaimExtractionRunCallStatus.completed else "failed",
            error_message=error_msg,
            started_at=started_at,
            completed_at=completed_at,
        )
        update_claim_extraction_run_call(
            conn,
            call_id=run_call.id,
            ai_call_id=ai_call_id,
            status=call_status,
            accepted_claim_count=(
                len(claims_out)
                if call_status == ClaimExtractionRunCallStatus.completed and claims_out is not None
                else None
            ),
            error_message=error_msg,
            completed_at=completed_at,
        )

    return claims_out


def extract_claims(
    conn: sqlite3.Connection,
    source: SourceContent,
    *,
    provider: AIProvider,
    prompt_name: str = CLAIM_EXTRACTION_PROMPT_NAME,
    prompt_version: str = CLAIM_EXTRACTION_PROMPT_VERSION,
    temperature: float = _DEFAULT_TEMPERATURE,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    replace: bool = False,
    registry: PromptRegistry | None = None,
) -> ClaimExtractionRun:
    """Orchestrate claim extraction for a single SourceContent.

    Returns the completed (or partial) ClaimExtractionRun.
    Raises ClaimExtractionError if source has no extractable text.

    When *replace* is False and an identical completed run already exists,
    the existing run is returned immediately (idempotent).
    When *replace* is True, the new run supersedes the prior completed run.
    """
    if not source.raw_text:
        raise ClaimExtractionError(
            f"source_content_id={source.id} has no raw_text — cannot extract claims"
        )

    chunks, was_truncated = chunk_text(source.raw_text)
    if not chunks:
        raise ClaimExtractionError(
            f"source_content_id={source.id} raw_text produced no chunks"
        )

    registry = registry or PromptRegistry()
    prompt = registry.get(prompt_name, prompt_version)

    # Build a single representative hash for the whole content run.
    import unicodedata

    nfc_hash = __import__("hashlib").sha256(
        unicodedata.normalize("NFC", source.raw_text).encode()
    ).hexdigest()

    input_hash = compute_input_hash(
        normalized_text_hash=nfc_hash,
        prompt_name=prompt_name,
        prompt_version=prompt_version,
        provider=provider.name,
        model=getattr(provider, "_model", provider.name),
        temperature=temperature,
        max_tokens=max_tokens,
    )

    assert source.id is not None
    existing = get_latest_completed_run(conn, source.id, input_hash)
    if existing is not None and not replace:
        logger.info(
            "Returning existing completed run id=%d for source_content_id=%d",
            existing.id,
            source.id,
        )
        return existing

    prior_run_id: int | None = existing.id if (existing and replace) else None

    run = create_claim_extraction_run(
        conn,
        source_content_id=source.id,
        input_hash=input_hash,
        total_chunk_count=len(chunks),
        was_truncated=was_truncated,
        prompt_name=prompt_name,
        prompt_version=prompt_version,
        model=getattr(provider, "_model", provider.name),
        provider=provider.name,
        extraction_algo_version=EXTRACTION_ALGO_VERSION,
        started_at=_now(),
    )

    all_claims: list[dict] = []
    failed_chunks = 0
    completed_chunks = 0

    for chunk in chunks:
        chunk_user = prompt.format_user(chunk_text=chunk.text)
        request = AIRequest(
            system=prompt.system,
            user=chunk_user,
            model=getattr(provider, "_model", provider.name),
            temperature=temperature,
            max_tokens=max_tokens,
            response_schema=ClaimExtractionOutput,
            prompt_name=prompt_name,
            prompt_version=prompt_version,
        )

        chunk_claims = _process_chunk(
            conn,
            chunk=chunk,
            run_id=run.id,
            source=source,
            request=request,
            provider=provider,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        if chunk_claims is not None:
            all_claims.extend(chunk_claims)
            completed_chunks += 1
        else:
            failed_chunks += 1

    deduped = deduplicate_claims(all_claims)

    if completed_chunks == 0:
        final_status = ClaimExtractionRunStatus.failed
    elif failed_chunks > 0:
        final_status = ClaimExtractionRunStatus.partial
    else:
        final_status = ClaimExtractionRunStatus.completed

    finalize_claim_extraction_run(
        conn,
        run_id=run.id,
        status=final_status,
        completed_chunk_count=completed_chunks,
        failed_chunk_count=failed_chunks,
        accepted_claim_count=len(deduped),
        completed_at=_now(),
        claims=deduped,
        supersede_run_id=prior_run_id,
    )

    from app.research.repository import get_claim_extraction_run

    final_run = get_claim_extraction_run(conn, run.id)
    assert final_run is not None
    return final_run
