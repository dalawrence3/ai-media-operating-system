"""Deterministic hashing and ordering for Phase 5 script generation.

All ordering and hashing functions use the single canonical evidence ordering so
that the prompt context, evidence hash, and reproducibility checks are always
derived from the same sorted sequence.

Canonical evidence ordering (all ascending unless noted):
  1. quality_score DESC, NULL last
  2. requires_date_review ASC  (False < True)
  3. claim_type ASC
  4. source_id ASC
  5. claim_id ASC
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.research.models import EvidenceClaim

# ---------------------------------------------------------------------------
# Evidence ordering
# ---------------------------------------------------------------------------


def sort_evidence(claims: list[EvidenceClaim]) -> list[EvidenceClaim]:
    """Return a new list sorted by the canonical five-key ordering."""
    return sorted(
        claims,
        key=lambda c: (
            -(c.quality_score if c.quality_score is not None else float("-inf")),
            c.requires_date_review,
            c.claim_type,
            c.source_id,
            c.claim_id,
        ),
    )


# ---------------------------------------------------------------------------
# Canonical JSON helper
# ---------------------------------------------------------------------------


def _canonical_json(obj: Any) -> str:
    """Produce compact, sorted-key JSON suitable for hashing."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


# ---------------------------------------------------------------------------
# Evidence hash
# ---------------------------------------------------------------------------


def _evidence_payload(claim: EvidenceClaim) -> dict[str, Any]:
    return {
        "claim_id": claim.claim_id,
        "claim_text": claim.claim_text,
        "claim_type": str(claim.claim_type),
        "source_id": claim.source_id,
        "source_content_id": claim.source_content_id,
        "extraction_run_id": claim.extraction_run_id,
        "requires_date_review": claim.requires_date_review,
        "quality_score": claim.quality_score,
        "supporting_quote": claim.supporting_quote,
        "quote_support_status": str(claim.quote_support_status),
    }


def compute_evidence_hash(claims: list[EvidenceClaim]) -> str:
    """SHA-256 of the canonical-sorted evidence payload.

    Always calls sort_evidence() internally; callers must not pre-sort.
    Returns the hex digest.
    """
    sorted_claims = sort_evidence(claims)
    payload = [_evidence_payload(c) for c in sorted_claims]
    return hashlib.sha256(_canonical_json(payload).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Prompt hash
# ---------------------------------------------------------------------------


def compute_prompt_hash(
    name: str, version: str, system: str, user_template: str
) -> str:
    """SHA-256 of the canonical prompt identity (name, version, system, user_template)."""
    payload = {
        "name": name,
        "version": version,
        "system": system,
        "user_template": user_template,
    }
    return hashlib.sha256(_canonical_json(payload).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Script input hash
# ---------------------------------------------------------------------------


def compute_script_input_hash(
    *,
    evidence_hash: str,
    prompt_hash: str,
    schema_version: str,
    renderer_version: str,
    citation_version: str,
    generation_version: str,
    model: str,
    temperature: float,
    max_tokens: int,
    tone: str,
    audience: str,
    target_duration_s: int,
) -> str:
    """SHA-256 covering all behaviour-affecting versions and settings.

    Evidence and prompt are represented by their own hashes so this hash
    is independent of their raw content while still changing whenever
    either changes.
    """
    payload = {
        "evidence_hash": evidence_hash,
        "prompt_hash": prompt_hash,
        "schema_version": schema_version,
        "renderer_version": renderer_version,
        "citation_version": citation_version,
        "generation_version": generation_version,
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "tone": tone,
        "audience": audience,
        "target_duration_s": target_duration_s,
    }
    return hashlib.sha256(_canonical_json(payload).encode()).hexdigest()
