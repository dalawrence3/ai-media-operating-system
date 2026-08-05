"""Phase 5 script generation orchestrator.

generate_script() is the single entry point for creating a new script from evidence.
It enforces all Phase 5 invariants around evidence guards, idempotency, provider calls,
validation, persistence, and supersession.

Key ordering constraints:
  - record_ai_call() is called OUTSIDE any SAVEPOINT (it auto-commits).
  - The provider call is made OUTSIDE DB transactions so a slow LLM cannot hold locks.
  - finalize_generation_run() wraps all DB writes in a single SAVEPOINT.
  - On failure: fail_generation_run() marks the run failed without touching approved Scripts.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from app.ai.provider import AIProvider, AIRequest
from app.ai.registry import PromptRegistry
from app.ai.usage import record_ai_call
from app.content.constants import (
    DEFAULT_AUDIENCE,
    DEFAULT_MAX_TOKENS,
    DEFAULT_TEMPERATURE,
    DEFAULT_TONE,
    SCRIPT_CITATION_VERSION,
    SCRIPT_GENERATION_PROMPT_NAME,
    SCRIPT_GENERATION_PROMPT_VERSION,
    SCRIPT_GENERATION_VERSION,
    SCRIPT_RENDERER_VERSION,
    SCRIPT_SCHEMA_VERSION,
    SHORT_FORM_DEFAULT_DURATION_S,
    SHORT_FORM_MAX_WORDS,
    SHORT_FORM_MIN_WORDS,
)
from app.content.errors import NoActiveEvidenceError, ScriptGenerationError
from app.content.hashing import (
    compute_evidence_hash,
    compute_prompt_hash,
    compute_script_input_hash,
    sort_evidence,
)
from app.content.models import ScriptGenerationRun, ScriptGenerationRunStatus
from app.content.repository import (
    create_generation_run,
    fail_generation_run,
    finalize_generation_run,
    find_completed_run_by_input_hash,
    get_generation_run,
)
from app.content.schemas import GeneratedScript, LLMGeneratedScript
from app.content.validator import validate_script
from app.core.logging import get_logger
from app.core.models import Topic
from app.core.repository import next_script_version
from app.research.models import EvidenceClaim

logger = get_logger(__name__)

_registry = PromptRegistry()


@dataclass
class GenerationResult:
    """Returned by generate_script() on success."""

    script_id: int
    run_id: int
    topic_id: int
    word_count: int
    duration_s: int
    warnings: list[str]
    was_idempotent: bool


def _format_evidence_context(claims: list[EvidenceClaim]) -> str:
    """Render sorted evidence as numbered context lines for the prompt."""
    sorted_claims = sort_evidence(claims)
    if not sorted_claims:
        return "(No evidence supplied — zero-evidence mode)"
    lines: list[str] = []
    for c in sorted_claims:
        source_label = c.source_title or f"Source {c.source_id}"
        lines.append(
            f"Claim ID {c.claim_id} [{c.claim_type}] (source: {source_label}): {c.claim_text}"
        )
        if c.requires_date_review:
            lines.append("  ⚠ Note: this claim may be time-sensitive — cite with care.")
    return "\n".join(lines)


def generate_script(
    conn: sqlite3.Connection,
    provider: AIProvider,
    topic: Topic,
    evidence: list[EvidenceClaim],
    *,
    allow_no_evidence: bool = False,
    replace_run_id: int | None = None,
    target_duration_s: int = SHORT_FORM_DEFAULT_DURATION_S,
    tone: str = DEFAULT_TONE,
    audience: str = DEFAULT_AUDIENCE,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    prompt_name: str = SCRIPT_GENERATION_PROMPT_NAME,
    prompt_version: str = SCRIPT_GENERATION_PROMPT_VERSION,
) -> GenerationResult:
    """Generate a new script for *topic* from *evidence*.

    Args:
        conn:              Open SQLite connection.
        provider:          AI provider to use (FakeProvider in tests).
        topic:             Topic to generate for (must have id set).
        evidence:          Active evidence claims from list_active_evidence_for_topic().
        allow_no_evidence: If False, raises NoActiveEvidenceError when evidence is empty.
        replace_run_id:    If set, supersede this prior generation run on success.
        target_duration_s: Target script duration (seconds); deviation >10s is a warning.
        tone:              Script tone descriptor for the prompt.
        audience:          Audience description for the prompt.
        temperature:       LLM temperature.
        max_tokens:        LLM max output tokens.
        prompt_name:       Prompt registry name.
        prompt_version:    Prompt registry version.

    Returns:
        GenerationResult on success.

    Raises:
        NoActiveEvidenceError:   evidence is empty and allow_no_evidence is False.
        ScriptGenerationError:   validation or provider failures.
    """
    if topic.id is None:
        raise ScriptGenerationError("Topic must have an id")

    # Invariant 10: zero-evidence guard before run creation
    if not evidence and not allow_no_evidence:
        raise NoActiveEvidenceError(
            f"No active evidence for topic {topic.id} — "
            "use --allow-no-evidence to generate without evidence"
        )

    # Load prompt (raises PromptNotFoundError if missing)
    prompt = _registry.get(prompt_name, prompt_version)

    # Hashing (invariants 12–14)
    evidence_hash = compute_evidence_hash(evidence)
    prompt_hash = compute_prompt_hash(
        prompt.name, prompt.version, prompt.system, prompt.user_template
    )
    input_hash = compute_script_input_hash(
        evidence_hash=evidence_hash,
        prompt_hash=prompt_hash,
        schema_version=SCRIPT_SCHEMA_VERSION,
        renderer_version=SCRIPT_RENDERER_VERSION,
        citation_version=SCRIPT_CITATION_VERSION,
        generation_version=SCRIPT_GENERATION_VERSION,
        model=provider.name,
        temperature=temperature,
        max_tokens=max_tokens,
        tone=tone,
        audience=audience,
        target_duration_s=target_duration_s,
    )

    # Idempotency: if an identical completed run already exists, return it
    existing = find_completed_run_by_input_hash(conn, topic.id, input_hash)
    if existing is not None and existing.script_id is not None:
        run_obj = get_generation_run(conn, existing.id)
        warnings = json.loads(run_obj.warnings_json) if run_obj and run_obj.warnings_json else []
        return GenerationResult(
            script_id=existing.script_id,
            run_id=existing.id,
            topic_id=topic.id,
            word_count=existing.computed_word_count or 0,
            duration_s=existing.computed_duration_s or 0,
            warnings=warnings,
            was_idempotent=True,
        )

    # Create a running generation-run row
    run_model = ScriptGenerationRun(
        topic_id=topic.id,
        status=ScriptGenerationRunStatus.running,
        input_hash=input_hash,
        evidence_hash=evidence_hash,
        prompt_hash=prompt_hash,
        prompt_name=prompt_name,
        prompt_version=prompt_version,
        model=provider.name,
        temperature=temperature,
        max_tokens=max_tokens,
        tone=tone,
        audience=audience,
        target_duration_s=target_duration_s,
        started_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S"),
    )
    run = create_generation_run(conn, run_model)
    run_id = run.id
    ai_call_id: int | None = None

    try:
        # Format prompt (using sorted evidence for canonical ordering — invariant 4)
        target_words = round(target_duration_s * 150 / 60)
        user_text = prompt.format_user(
            topic_title=topic.title,
            topic_angle=topic.angle,
            evidence_context=_format_evidence_context(evidence),
            target_duration_s=str(target_duration_s),
            target_words=str(target_words),
            tone=tone,
            audience_desc=audience or "general audience",
            min_words=str(SHORT_FORM_MIN_WORDS),
            max_words=str(SHORT_FORM_MAX_WORDS),
        )

        request = AIRequest(
            system=prompt.system.format(
                target_duration_s=target_duration_s,
                target_words=target_words,
                tone=tone,
                audience_desc=audience or "general audience",
                min_words=SHORT_FORM_MIN_WORDS,
                max_words=SHORT_FORM_MAX_WORDS,
            ),
            user=user_text,
            model=provider.name,
            temperature=temperature,
            max_tokens=max_tokens,
            prompt_name=prompt_name,
            prompt_version=prompt_version,
        )

        # Provider call OUTSIDE DB transactions (invariant: no lock held during LLM call)
        response = provider.complete(request)

        # record_ai_call auto-commits — must be OUTSIDE any SAVEPOINT
        ai_call_id = record_ai_call(
            conn,
            request,
            response,
            status="success",
            started_at=run.started_at,
        )

        # Parse structured output
        try:
            data = json.loads(response.raw_text)
        except json.JSONDecodeError as exc:
            raise ScriptGenerationError(
                f"Provider returned non-JSON response: {exc}"
            ) from exc

        llm_script = LLMGeneratedScript.model_validate(data)
        script = GeneratedScript.from_llm(llm_script)

        # Validate (invariants 4–17)
        result = validate_script(
            script,
            evidence,
            allow_no_evidence=allow_no_evidence,
            target_duration_s=target_duration_s,
        )

        # Determine next script version
        script_version = next_script_version(conn, topic.id)

        requires_evidence_review = any(
            e.requires_date_review
            for e in evidence
            if e.claim_id in {c.claim_id for c in result.pending_citations}
        )

        # Atomic persistence (invariant: no auto-commit inside SAVEPOINT)
        script_id, _ = finalize_generation_run(
            conn,
            run_id=run_id,  # type: ignore[arg-type]
            topic_id=topic.id,
            script_body=result.body,
            script_body_json=result.script.model_dump_json(),
            script_version=script_version,
            computed_word_count=result.word_count,
            computed_duration_s=result.duration_s,
            warnings=list(result.script.warnings),
            requires_evidence_review=requires_evidence_review,
            pending_citations=result.pending_citations,
            ai_call_id=ai_call_id,
            replace_run_id=replace_run_id,
        )

        logger.info(
            "Script generated: topic_id=%d script_id=%d run_id=%d words=%d duration=%ds",
            topic.id,
            script_id,
            run_id,
            result.word_count,
            result.duration_s,
        )

        return GenerationResult(
            script_id=script_id,
            run_id=run_id,  # type: ignore[arg-type]
            topic_id=topic.id,
            word_count=result.word_count,
            duration_s=result.duration_s,
            warnings=list(result.script.warnings),
            was_idempotent=False,
        )

    except Exception as exc:
        fail_generation_run(
            conn,
            run_id,  # type: ignore[arg-type]
            error_message=str(exc),
            ai_call_id=ai_call_id,
        )
        raise
