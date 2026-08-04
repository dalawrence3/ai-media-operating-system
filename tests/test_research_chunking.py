"""Tests for deterministic paragraph-aware text chunking (Phase 4.2)."""

from __future__ import annotations

import hashlib

from app.research.chunking import (
    CHUNK_MAX_CHARS,
    CHUNK_MAX_COUNT,
    Chunk,
    chunk_text,
    compute_input_hash,
    deduplicate_claims,
)
from app.research.constants import CLAIMS_RUN_MAX

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _raw_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _verify_invariant(raw_text: str, chunks: list[Chunk]) -> None:
    """Assert chunk.text == raw_text[start:end] for every chunk."""
    for c in chunks:
        assert c.text == raw_text[c.char_start : c.char_end], (
            f"Chunk {c.index} text mismatch: "
            f"expected {raw_text[c.char_start:c.char_end]!r}, got {c.text!r}"
        )


# ---------------------------------------------------------------------------
# Invariant: every chunk is an exact raw-text slice
# ---------------------------------------------------------------------------


class TestOffsetInvariant:
    def test_single_chunk_invariant(self):
        raw = "Hello world. This is a test."
        chunks, _ = chunk_text(raw)
        _verify_invariant(raw, chunks)

    def test_multi_chunk_invariant(self):
        para = "word " * 500
        raw = (para + "\n\n") * 3 + para
        chunks, _ = chunk_text(raw)
        assert len(chunks) > 1
        _verify_invariant(raw, chunks)

    def test_crlf_preserved(self):
        raw = "Para one.\r\nLine two.\r\n\r\nPara two."
        chunks, _ = chunk_text(raw)
        _verify_invariant(raw, chunks)
        # CRLF must appear verbatim in chunk text
        full = "".join(c.text for c in chunks)
        assert "\r\n" in full

    def test_repeated_paragraphs_correct_offsets(self):
        para = "The quick brown fox.\n\n"
        raw = para * 4
        chunks, _ = chunk_text(raw)
        _verify_invariant(raw, chunks)

    def test_pdf_page_separators_preserved(self):
        raw = "--- Page 1 ---\nContent on page one.\n\n--- Page 2 ---\nContent on page two."
        chunks, _ = chunk_text(raw)
        _verify_invariant(raw, chunks)
        combined = "".join(c.text for c in chunks)
        assert "--- Page 1 ---" in combined
        assert "--- Page 2 ---" in combined

    def test_repeated_whitespace_preserved(self):
        raw = "Word  with   double   spaces.\n\nSecond paragraph."
        chunks, _ = chunk_text(raw)
        _verify_invariant(raw, chunks)
        assert "  " in chunks[0].text  # original whitespace retained

    def test_oversized_paragraph_splitting_preserves_characters(self):
        # Build a paragraph that exceeds CHUNK_MAX_CHARS
        sentence = "This is a sentence. " * 400  # ~8000 chars
        raw = sentence
        chunks, _ = chunk_text(raw)
        _verify_invariant(raw, chunks)
        # All original characters appear somewhere
        reconstructed = "".join(c.text for c in chunks)
        assert reconstructed == raw[: CHUNK_MAX_CHARS * CHUNK_MAX_COUNT]


# ---------------------------------------------------------------------------
# Chunk count and truncation
# ---------------------------------------------------------------------------


class TestChunkCount:
    def test_single_short_text(self):
        raw = "Short text."
        chunks, was_truncated = chunk_text(raw)
        assert len(chunks) == 1
        assert not was_truncated

    def test_multiple_chunks_from_paragraphs(self):
        # Many small paragraphs forced into separate chunks by size
        para = "x " * (CHUNK_MAX_CHARS // 2 + 10)
        raw = (para + "\n\n") * 6
        chunks, was_truncated = chunk_text(raw)
        assert len(chunks) > 1

    def test_max_chunk_count_enforced(self):
        # Text longer than CHUNK_MAX_CHARS * CHUNK_MAX_COUNT must be truncated
        raw = "a" * (CHUNK_MAX_CHARS * CHUNK_MAX_COUNT + 1)
        chunks, was_truncated = chunk_text(raw)
        assert was_truncated
        assert len(chunks) <= CHUNK_MAX_COUNT

    def test_no_truncation_when_within_limit(self):
        raw = "a" * (CHUNK_MAX_CHARS * CHUNK_MAX_COUNT)
        _, was_truncated = chunk_text(raw)
        assert not was_truncated

    def test_empty_text_returns_empty(self):
        chunks, was_truncated = chunk_text("")
        assert chunks == []
        assert not was_truncated

    def test_whitespace_only(self):
        chunks, _ = chunk_text("   \n\n   ")
        _verify_invariant("   \n\n   ", chunks)

    def test_chunk_indexes_sequential(self):
        raw = ("para " * 300 + "\n\n") * 5
        chunks, _ = chunk_text(raw)
        for i, c in enumerate(chunks):
            assert c.index == i

    def test_chunk_spans_in_source_order(self):
        raw = ("para " * 300 + "\n\n") * 5
        chunks, _ = chunk_text(raw)
        for i in range(len(chunks) - 1):
            assert chunks[i].char_end <= chunks[i + 1].char_start


# ---------------------------------------------------------------------------
# Chunk hashes
# ---------------------------------------------------------------------------


class TestChunkHash:
    def test_hash_matches_text(self):
        raw = "Hello world."
        chunks, _ = chunk_text(raw)
        for c in chunks:
            assert c.chunk_hash == _raw_sha256(c.text)

    def test_same_text_same_hash(self):
        raw = "Deterministic text."
        chunks1, _ = chunk_text(raw)
        chunks2, _ = chunk_text(raw)
        assert [c.chunk_hash for c in chunks1] == [c.chunk_hash for c in chunks2]

    def test_different_text_different_hash(self):
        chunks1, _ = chunk_text("Text A.")
        chunks2, _ = chunk_text("Text B.")
        assert chunks1[0].chunk_hash != chunks2[0].chunk_hash


# ---------------------------------------------------------------------------
# Input hash
# ---------------------------------------------------------------------------


class TestComputeInputHash:
    _BASE = dict(
        normalized_text_hash="abc123",
        prompt_name="claim-extraction",
        prompt_version="1",
        provider="fake",
        model="claude-sonnet-5",
        temperature=0.1,
        max_tokens=2048,
    )

    def test_deterministic(self):
        h1 = compute_input_hash(**self._BASE)
        h2 = compute_input_hash(**self._BASE)
        assert h1 == h2

    def test_changes_on_model(self):
        h1 = compute_input_hash(**self._BASE)
        h2 = compute_input_hash(**{**self._BASE, "model": "different-model"})
        assert h1 != h2

    def test_changes_on_temperature(self):
        h1 = compute_input_hash(**self._BASE)
        h2 = compute_input_hash(**{**self._BASE, "temperature": 0.9})
        assert h1 != h2

    def test_changes_on_max_tokens(self):
        h1 = compute_input_hash(**self._BASE)
        h2 = compute_input_hash(**{**self._BASE, "max_tokens": 512})
        assert h1 != h2

    def test_changes_on_normalized_text_hash(self):
        h1 = compute_input_hash(**self._BASE)
        h2 = compute_input_hash(**{**self._BASE, "normalized_text_hash": "xyz"})
        assert h1 != h2

    def test_changes_on_prompt_version(self):
        h1 = compute_input_hash(**self._BASE)
        h2 = compute_input_hash(**{**self._BASE, "prompt_version": "2"})
        assert h1 != h2

    def test_returns_hex_string(self):
        h = compute_input_hash(**self._BASE)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


class TestDeduplicateClaims:
    def _claim(self, text: str, chunk: int = 0) -> dict:
        return {"claim_text": text, "chunk_index": chunk, "claim_type": "factual"}

    def test_removes_exact_duplicates(self):
        claims = [self._claim("Same text.", 0), self._claim("Same text.", 1)]
        result = deduplicate_claims(claims)
        assert len(result) == 1
        assert result[0]["chunk_index"] == 0

    def test_removes_case_normalised_duplicates(self):
        claims = [self._claim("Water boils at 100°C.", 0), self._claim("water boils at 100°C.", 1)]
        result = deduplicate_claims(claims)
        assert len(result) == 1

    def test_removes_whitespace_normalised_duplicates(self):
        claims = [self._claim("The  quick brown fox.", 0), self._claim("The quick brown fox.", 1)]
        result = deduplicate_claims(claims)
        assert len(result) == 1

    def test_retains_distinct_claims(self):
        claims = [self._claim("Claim A."), self._claim("Claim B.")]
        assert len(deduplicate_claims(claims)) == 2

    def test_caps_at_run_max(self):
        claims = [self._claim(f"Unique claim number {i}.", i) for i in range(CLAIMS_RUN_MAX + 5)]
        result = deduplicate_claims(claims)
        assert len(result) == CLAIMS_RUN_MAX
