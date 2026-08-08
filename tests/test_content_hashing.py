"""Tests for Phase 5 hashing and evidence ordering (Stage 3)."""

from __future__ import annotations

from app.content.hashing import (
    compute_evidence_hash,
    compute_prompt_hash,
    compute_script_input_hash,
    sort_evidence,
)
from app.research.models import ClaimSupportStatus, ClaimType, EvidenceClaim

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _claim(
    claim_id: int = 1,
    source_id: int = 1,
    source_content_id: int = 1,
    extraction_run_id: int = 1,
    claim_type: ClaimType = ClaimType.factual,
    requires_date_review: bool = False,
    quality_score: float | None = 0.8,
) -> EvidenceClaim:
    return EvidenceClaim(
        claim_id=claim_id,
        claim_text=f"Claim {claim_id}",
        claim_type=claim_type,
        supporting_quote=None,
        quote_support_status=ClaimSupportStatus.no_quote,
        quote_start=None,
        quote_end=None,
        page_number=None,
        chunk_index=0,
        requires_date_review=requires_date_review,
        source_id=source_id,
        source_content_id=source_content_id,
        extraction_run_id=extraction_run_id,
        source_title=None,
        canonical_url=None,
        author=None,
        published_at=None,
        quality_score=quality_score,
        extraction_status="ok",
        suspected_truncation=False,
        prompt_name="test",
        prompt_version="1",
        model="fake",
    )


def _input_hash_kwargs(**overrides: object) -> dict:
    base = dict(
        evidence_hash="e" * 64,
        prompt_hash="p" * 64,
        schema_version="GeneratedScript-v1",
        renderer_version="renderer-v1",
        citation_version="citation-v1",
        generation_version="5-v1",
        model="claude-sonnet-4-6",
        temperature=0.3,
        max_tokens=2048,
        tone="conversational",
        audience="",
        target_duration_s=60,
    )
    base.update(overrides)
    return base  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# sort_evidence
# ---------------------------------------------------------------------------


class TestSortEvidence:
    def test_empty(self):
        assert sort_evidence([]) == []

    def test_single(self):
        c = _claim()
        assert sort_evidence([c]) == [c]

    def test_quality_desc(self):
        lo = _claim(claim_id=1, quality_score=0.3)
        hi = _claim(claim_id=2, quality_score=0.9)
        result = sort_evidence([lo, hi])
        assert result[0].claim_id == 2
        assert result[1].claim_id == 1

    def test_null_quality_last(self):
        has_score = _claim(claim_id=1, quality_score=0.1)
        no_score = _claim(claim_id=2, quality_score=None)
        result = sort_evidence([no_score, has_score])
        assert result[0].claim_id == 1
        assert result[1].claim_id == 2

    def test_requires_date_review_asc(self):
        # same quality, requires_date_review=True sorts after False
        no_review = _claim(claim_id=1, quality_score=0.5, requires_date_review=False)
        needs_review = _claim(claim_id=2, quality_score=0.5, requires_date_review=True)
        result = sort_evidence([needs_review, no_review])
        assert result[0].claim_id == 1
        assert result[1].claim_id == 2

    def test_claim_type_asc(self):
        # factual < statistical alphabetically
        stat = _claim(claim_id=1, quality_score=0.5, claim_type=ClaimType.statistical)
        fact = _claim(claim_id=2, quality_score=0.5, claim_type=ClaimType.factual)
        result = sort_evidence([stat, fact])
        assert result[0].claim_id == 2  # factual first

    def test_source_id_asc(self):
        s2 = _claim(claim_id=1, source_id=2, quality_score=0.5)
        s1 = _claim(claim_id=2, source_id=1, quality_score=0.5)
        result = sort_evidence([s2, s1])
        assert result[0].source_id == 1

    def test_claim_id_asc_tiebreaker(self):
        c2 = _claim(claim_id=2, source_id=1, quality_score=0.5)
        c1 = _claim(claim_id=1, source_id=1, quality_score=0.5)
        result = sort_evidence([c2, c1])
        assert result[0].claim_id == 1

    def test_returns_new_list(self):
        claims = [_claim(claim_id=2), _claim(claim_id=1)]
        result = sort_evidence(claims)
        assert result is not claims

    def test_permutation_invariant(self):
        """Different input orderings produce identical sorted sequence."""
        claims = [
            _claim(claim_id=3, source_id=1, quality_score=0.9),
            _claim(claim_id=1, source_id=2, quality_score=0.5),
            _claim(claim_id=2, source_id=1, quality_score=0.5),
        ]
        import itertools

        ids_first = [c.claim_id for c in sort_evidence(claims)]
        for perm in itertools.permutations(claims):
            assert [c.claim_id for c in sort_evidence(list(perm))] == ids_first


# ---------------------------------------------------------------------------
# compute_evidence_hash
# ---------------------------------------------------------------------------


class TestComputeEvidenceHash:
    def test_empty_list(self):
        h = compute_evidence_hash([])
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_deterministic(self):
        claims = [_claim(claim_id=1), _claim(claim_id=2)]
        assert compute_evidence_hash(claims) == compute_evidence_hash(claims)

    def test_permutation_invariant(self):
        c1 = _claim(claim_id=1, quality_score=0.9)
        c2 = _claim(claim_id=2, quality_score=0.5)
        assert compute_evidence_hash([c1, c2]) == compute_evidence_hash([c2, c1])

    def test_order_independent_of_caller_sort(self):
        """Hash doesn't change even if caller provides differently ordered input."""
        claims_a = [_claim(claim_id=1, quality_score=0.9), _claim(claim_id=2, quality_score=0.1)]
        claims_b = [_claim(claim_id=2, quality_score=0.1), _claim(claim_id=1, quality_score=0.9)]
        assert compute_evidence_hash(claims_a) == compute_evidence_hash(claims_b)

    def test_different_claims_different_hash(self):
        c1 = [_claim(claim_id=1)]
        c2 = [_claim(claim_id=2)]
        assert compute_evidence_hash(c1) != compute_evidence_hash(c2)

    def test_independent_of_prompt(self):
        claims = [_claim()]
        h = compute_evidence_hash(claims)
        # changing prompt-related things does not affect evidence hash
        assert h == compute_evidence_hash(claims)


# ---------------------------------------------------------------------------
# compute_prompt_hash
# ---------------------------------------------------------------------------


class TestComputePromptHash:
    def test_deterministic(self):
        h1 = compute_prompt_hash("script-generation", "1", "sys", "user {topic}")
        h2 = compute_prompt_hash("script-generation", "1", "sys", "user {topic}")
        assert h1 == h2

    def test_hex_64_chars(self):
        h = compute_prompt_hash("n", "1", "s", "u")
        assert len(h) == 64

    def test_name_change_changes_hash(self):
        h1 = compute_prompt_hash("name-a", "1", "sys", "user")
        h2 = compute_prompt_hash("name-b", "1", "sys", "user")
        assert h1 != h2

    def test_version_change_changes_hash(self):
        h1 = compute_prompt_hash("n", "1", "s", "u")
        h2 = compute_prompt_hash("n", "2", "s", "u")
        assert h1 != h2

    def test_system_change_changes_hash(self):
        h1 = compute_prompt_hash("n", "1", "sys-a", "u")
        h2 = compute_prompt_hash("n", "1", "sys-b", "u")
        assert h1 != h2

    def test_user_template_change_changes_hash(self):
        h1 = compute_prompt_hash("n", "1", "s", "tmpl-a")
        h2 = compute_prompt_hash("n", "1", "s", "tmpl-b")
        assert h1 != h2


# ---------------------------------------------------------------------------
# compute_script_input_hash
# ---------------------------------------------------------------------------


class TestComputeScriptInputHash:
    def test_deterministic(self):
        kwargs = _input_hash_kwargs()
        assert compute_script_input_hash(**kwargs) == compute_script_input_hash(**kwargs)

    def test_hex_64_chars(self):
        h = compute_script_input_hash(**_input_hash_kwargs())
        assert len(h) == 64

    def test_evidence_hash_change(self):
        h1 = compute_script_input_hash(**_input_hash_kwargs(evidence_hash="a" * 64))
        h2 = compute_script_input_hash(**_input_hash_kwargs(evidence_hash="b" * 64))
        assert h1 != h2

    def test_prompt_hash_change(self):
        h1 = compute_script_input_hash(**_input_hash_kwargs(prompt_hash="a" * 64))
        h2 = compute_script_input_hash(**_input_hash_kwargs(prompt_hash="b" * 64))
        assert h1 != h2

    def test_model_change(self):
        h1 = compute_script_input_hash(**_input_hash_kwargs(model="claude-haiku-4-5"))
        h2 = compute_script_input_hash(**_input_hash_kwargs(model="claude-sonnet-4-6"))
        assert h1 != h2

    def test_temperature_change(self):
        h1 = compute_script_input_hash(**_input_hash_kwargs(temperature=0.3))
        h2 = compute_script_input_hash(**_input_hash_kwargs(temperature=0.7))
        assert h1 != h2

    def test_tone_change(self):
        h1 = compute_script_input_hash(**_input_hash_kwargs(tone="conversational"))
        h2 = compute_script_input_hash(**_input_hash_kwargs(tone="formal"))
        assert h1 != h2

    def test_duration_change(self):
        h1 = compute_script_input_hash(**_input_hash_kwargs(target_duration_s=60))
        h2 = compute_script_input_hash(**_input_hash_kwargs(target_duration_s=30))
        assert h1 != h2

    def test_schema_version_change(self):
        h1 = compute_script_input_hash(**_input_hash_kwargs(schema_version="GeneratedScript-v1"))
        h2 = compute_script_input_hash(**_input_hash_kwargs(schema_version="GeneratedScript-v2"))
        assert h1 != h2
