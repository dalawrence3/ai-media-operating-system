"""Deterministic paragraph-aware text chunking for Phase 4.2 claim extraction.

Invariant: chunk.text == raw_text[chunk.char_start:chunk.char_end] for every chunk.
The algorithm computes character positions in the original text and slices
accordingly — it never strips, rejoins, normalises, or modifies characters.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass

from app.research.constants import (
    CHUNK_ALGO_VERSION,
    CHUNK_CLAIMS_MAX,
    CHUNK_MAX_CHARS,
    CHUNK_MAX_COUNT,
    CLAIM_SCHEMA_VERSION,
    CLAIMS_RUN_MAX,
    EXTRACTION_ALGO_VERSION,
    QUOTE_VALIDATION_VERSION,
)

# Paragraph separator: two or more consecutive newlines.
_PARA_SEP = re.compile(r"\n{2,}")
# Sentence boundary: end-of-sentence punctuation followed by whitespace.
_SENT_BOUND = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Chunk:
    index: int
    text: str
    char_start: int
    char_end: int
    chunk_hash: str


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def chunk_text(raw_text: str) -> tuple[list[Chunk], bool]:
    """Split *raw_text* into deterministic chunks.

    Returns (chunks, was_truncated).  *was_truncated* is True when the text
    exceeded ``CHUNK_MAX_CHARS * CHUNK_MAX_COUNT`` and the tail was dropped.

    Every chunk satisfies: chunk.text == raw_text[chunk.char_start:chunk.char_end]
    """
    max_total = CHUNK_MAX_CHARS * CHUNK_MAX_COUNT
    was_truncated = len(raw_text) > max_total
    effective = raw_text[:max_total]

    # Build paragraph spans: each span is (start, end) in *effective* such that
    # effective[start:end] is a paragraph including its trailing separator.
    para_spans = _build_paragraph_spans(effective)

    # Accumulate spans into chunks, splitting oversized paragraphs when needed.
    raw_chunks: list[tuple[int, int]] = []  # (start, end) pairs in *effective*
    for p_start, p_end in para_spans:
        para_len = p_end - p_start
        if para_len <= CHUNK_MAX_CHARS:
            _accumulate(raw_chunks, p_start, p_end)
        else:
            # Split the oversized paragraph at sentence boundaries.
            for s_start, s_end in _split_paragraph(effective, p_start, p_end):
                _accumulate(raw_chunks, s_start, s_end)

    chunks = [
        Chunk(
            index=i,
            text=effective[start:end],
            char_start=start,
            char_end=end,
            chunk_hash=_sha256(effective[start:end]),
        )
        for i, (start, end) in enumerate(raw_chunks)
    ]
    return chunks, was_truncated


def _build_paragraph_spans(text: str) -> list[tuple[int, int]]:
    """Return (start, end) spans where text[start:end] is a paragraph + trailing separator."""
    spans: list[tuple[int, int]] = []
    pos = 0
    for m in _PARA_SEP.finditer(text):
        # Include the separator at the END of the preceding paragraph span.
        end = m.end()
        if end > pos:
            spans.append((pos, end))
        pos = end
    # Trailing paragraph with no separator.
    if pos < len(text):
        spans.append((pos, len(text)))
    return spans


def _accumulate(chunks: list[tuple[int, int]], start: int, end: int) -> None:
    """Append (start, end) to *chunks*, merging with the last entry when it fits."""
    if not chunks:
        chunks.append((start, end))
        return
    last_start, last_end = chunks[-1]
    if (last_end - last_start) + (end - start) <= CHUNK_MAX_CHARS:
        chunks[-1] = (last_start, end)
    else:
        chunks.append((start, end))


def _split_paragraph(text: str, p_start: int, p_end: int) -> list[tuple[int, int]]:
    """Split the paragraph text[p_start:p_end] at sentence boundaries.

    Returns a list of (start, end) spans in *text*.  If a sentence itself
    exceeds CHUNK_MAX_CHARS, it is hard-cut at that boundary.
    """
    # Find sentence boundaries within the paragraph (relative positions).
    para = text[p_start:p_end]
    # Collect absolute positions of sentence splits.
    split_points = [p_start]
    for m in _SENT_BOUND.finditer(para):
        split_points.append(p_start + m.end())
    split_points.append(p_end)

    spans: list[tuple[int, int]] = []
    for i in range(len(split_points) - 1):
        seg_start = split_points[i]
        seg_end = split_points[i + 1]
        seg_len = seg_end - seg_start
        if seg_len <= CHUNK_MAX_CHARS:
            _accumulate(spans, seg_start, seg_end)
        else:
            # Hard-cut oversized sentence at CHUNK_MAX_CHARS boundaries.
            pos = seg_start
            while pos < seg_end:
                cut = min(pos + CHUNK_MAX_CHARS, seg_end)
                _accumulate(spans, pos, cut)
                pos = cut
    return spans


# ---------------------------------------------------------------------------
# Input hash
# ---------------------------------------------------------------------------


def compute_input_hash(
    *,
    normalized_text_hash: str,
    prompt_name: str,
    prompt_version: str,
    provider: str,
    model: str,
    temperature: float,
    max_tokens: int,
) -> str:
    """Return a canonical SHA-256 hash of all extraction configuration parameters.

    Changing any of these values produces a different hash, triggering a new run.
    All algorithm-version constants are included automatically.
    """
    payload = json.dumps(
        {
            "normalized_text_hash": normalized_text_hash,
            "prompt_name": prompt_name,
            "prompt_version": prompt_version,
            "extraction_algo_version": EXTRACTION_ALGO_VERSION,
            "chunk_algo_version": CHUNK_ALGO_VERSION,
            "chunk_max_chars": CHUNK_MAX_CHARS,
            "chunk_max_count": CHUNK_MAX_COUNT,
            "chunk_claims_max": CHUNK_CLAIMS_MAX,
            "claims_run_max": CLAIMS_RUN_MAX,
            "schema_version": CLAIM_SCHEMA_VERSION,
            "provider": provider,
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "quote_validation_version": QUOTE_VALIDATION_VERSION,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Claim deduplication
# ---------------------------------------------------------------------------


def deduplicate_claims(
    claims: list[dict],
) -> list[dict]:
    """Remove semantically duplicate claims across chunks.

    Uses NFC-normalised, whitespace-collapsed claim_text as the dedup key.
    First occurrence (lowest chunk_index order) is retained.
    Result is capped at CLAIMS_RUN_MAX.
    """
    seen: dict[str, bool] = {}
    result: list[dict] = []
    for c in claims:
        key = _dedup_key(c["claim_text"])
        if key not in seen:
            seen[key] = True
            result.append(c)
            if len(result) >= CLAIMS_RUN_MAX:
                break
    return result


def _dedup_key(text: str) -> str:
    normalised = unicodedata.normalize("NFC", text)
    return re.sub(r"\s+", " ", normalised).strip().lower()
