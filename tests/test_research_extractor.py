"""Tests for Phase 4.2 claim extraction orchestrator (extractor.py).

All tests use the FakeProvider — no live LLM calls, no live HTTP.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

import pytest

from app.ai.fake import FakeProvider
from app.core.models import SourceKind, Topic
from app.core.repository import create_topic
from app.research.errors import ClaimExtractionError
from app.research.extractor import extract_claims
from app.research.models import (
    ClaimExtractionRunStatus,
    ExtractionStatus,
    FetchStatus,
)
from app.research.repository import (
    create_source_content,
    get_claim_extraction_run,
    get_or_create_source,
    list_active_evidence_for_topic,
    list_claim_extraction_runs,
    list_claims,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _make_source_content(db: sqlite3.Connection, *, raw_text: str = "Sky is blue.") -> object:
    topic = create_topic(db, Topic(title="Test Topic"))
    src, _ = get_or_create_source(db, topic.id, SourceKind.url, "http://example.com")
    return create_source_content(
        db,
        source_id=src.id,
        fetch_status=FetchStatus.ok,
        extraction_status=ExtractionStatus.ok,
        fetched_at=_now(),
        raw_text=raw_text,
        normalized_text_hash="nhash",
        retrieval_hash="rhash",
        word_count=3,
        extraction_method="html_parser",
    )


def _fake_claim_output(claims: list[dict]) -> str:
    return json.dumps({"claims": claims})


def _claim_dict(
    text: str = "The sky is blue.",
    claim_type: str = "factual",
    supporting_quote: str | None = "sky is blue",
) -> dict:
    return {
        "claim_text": text,
        "claim_type": claim_type,
        "supporting_quote": supporting_quote,
    }


# ---------------------------------------------------------------------------
# extract_claims: basic happy path
# ---------------------------------------------------------------------------


class TestExtractClaimsHappyPath:
    def test_returns_completed_run(self, db: sqlite3.Connection):
        sc = _make_source_content(db)
        provider = FakeProvider(output=_fake_claim_output([_claim_dict()]))
        run = extract_claims(db, sc, provider=provider)
        assert run.status == ClaimExtractionRunStatus.completed
        assert run.accepted_claim_count == 1

    def test_claims_persisted(self, db: sqlite3.Connection):
        sc = _make_source_content(db)
        provider = FakeProvider(output=_fake_claim_output([_claim_dict()]))
        run = extract_claims(db, sc, provider=provider)
        claims = list_claims(db, run.id, include_unsupported=True)
        assert len(claims) == 1
        assert claims[0].claim_text == "The sky is blue."

    def test_exact_quote_classified_correctly(self, db: sqlite3.Connection):
        raw = "The sky is blue and clear."
        sc = _make_source_content(db, raw_text=raw)
        provider = FakeProvider(
            output=_fake_claim_output([_claim_dict(supporting_quote="sky is blue")])
        )
        run = extract_claims(db, sc, provider=provider)
        claims = list_claims(db, run.id, include_unsupported=True)
        assert claims[0].quote_support_status.value == "exact"
        assert claims[0].quote_start is not None

    def test_no_quote_classified_as_no_quote(self, db: sqlite3.Connection):
        sc = _make_source_content(db)
        provider = FakeProvider(output=_fake_claim_output([_claim_dict(supporting_quote=None)]))
        run = extract_claims(db, sc, provider=provider)
        claims = list_claims(db, run.id, include_unsupported=True)
        assert claims[0].quote_support_status.value == "no_quote"

    def test_zero_claims_still_completes(self, db: sqlite3.Connection):
        sc = _make_source_content(db)
        provider = FakeProvider(output=_fake_claim_output([]))
        run = extract_claims(db, sc, provider=provider)
        assert run.status == ClaimExtractionRunStatus.completed
        assert run.accepted_claim_count == 0

    def test_run_call_recorded_for_each_chunk(self, db: sqlite3.Connection):
        sc = _make_source_content(db)
        provider = FakeProvider(output=_fake_claim_output([_claim_dict()]))
        run = extract_claims(db, sc, provider=provider)
        sql = (
            "SELECT COUNT(*) as n FROM claim_extraction_run_calls WHERE claim_extraction_run_id = ?"
        )
        row = db.execute(sql, (run.id,)).fetchone()
        assert row["n"] >= 1

    def test_ai_call_recorded(self, db: sqlite3.Connection):
        sc = _make_source_content(db)
        provider = FakeProvider(output=_fake_claim_output([_claim_dict()]))
        extract_claims(db, sc, provider=provider)
        row = db.execute(
            "SELECT COUNT(*) as n FROM ai_calls WHERE prompt_name = 'claim-extraction'",
        ).fetchone()
        assert row["n"] >= 1


# ---------------------------------------------------------------------------
# Idempotency (no replace)
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_second_call_returns_existing_run(self, db: sqlite3.Connection):
        sc = _make_source_content(db)
        provider = FakeProvider(output=_fake_claim_output([_claim_dict()]))
        run1 = extract_claims(db, sc, provider=provider)
        run2 = extract_claims(db, sc, provider=provider)
        assert run1.id == run2.id

    def test_second_call_does_not_create_new_run(self, db: sqlite3.Connection):
        sc = _make_source_content(db)
        provider = FakeProvider(output=_fake_claim_output([_claim_dict()]))
        extract_claims(db, sc, provider=provider)
        extract_claims(db, sc, provider=provider)
        runs = list_claim_extraction_runs(db, sc.id, include_superseded=True)
        assert len(runs) == 1


# ---------------------------------------------------------------------------
# Replace / supersession
# ---------------------------------------------------------------------------


class TestSupersession:
    def test_replace_creates_new_run(self, db: sqlite3.Connection):
        sc = _make_source_content(db)
        provider = FakeProvider(output=_fake_claim_output([_claim_dict()]))
        run1 = extract_claims(db, sc, provider=provider)

        # To force a new hash, change provider name by using a custom subclass.
        class OtherProvider(FakeProvider):
            name = "other"

        out = _fake_claim_output([_claim_dict()])
        run2 = extract_claims(db, sc, provider=OtherProvider(output=out))
        assert run1.id != run2.id

    def test_replace_flag_supersedes_prior_completed_run(self, db: sqlite3.Connection):
        sc = _make_source_content(db)
        provider = FakeProvider(output=_fake_claim_output([_claim_dict()]))
        run1 = extract_claims(db, sc, provider=provider)
        run2 = extract_claims(db, sc, provider=provider, replace=True)
        run1_after = get_claim_extraction_run(db, run1.id)
        assert run1_after.superseded_at is not None
        assert run1_after.superseded_by_run_id == run2.id


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


class TestErrorPaths:
    def test_raises_on_no_raw_text(self, db: sqlite3.Connection):
        topic = create_topic(db, Topic(title="T"))
        src, _ = get_or_create_source(db, topic.id, SourceKind.url, "http://x.com")
        sc = create_source_content(
            db,
            source_id=src.id,
            fetch_status=FetchStatus.failed,
            extraction_status=ExtractionStatus.failed,
            fetched_at=_now(),
        )
        with pytest.raises(ClaimExtractionError, match="no raw_text"):
            extract_claims(db, sc, provider=FakeProvider())

    def test_invalid_json_output_still_records_call(self, db: sqlite3.Connection):
        # Per-chunk failures are caught; the run completes as partial or failed.
        # The AI call record must still be written.
        sc = _make_source_content(db)
        provider = FakeProvider(output="NOT JSON")
        run = extract_claims(db, sc, provider=provider)
        assert run.status in (
            ClaimExtractionRunStatus.partial,
            ClaimExtractionRunStatus.failed,
        )
        row = db.execute("SELECT COUNT(*) as n FROM ai_calls").fetchone()
        assert row["n"] >= 1


# ---------------------------------------------------------------------------
# Active evidence integration
# ---------------------------------------------------------------------------


class TestActiveEvidence:
    def test_active_evidence_appears_after_extraction(self, db: sqlite3.Connection):
        topic = create_topic(db, Topic(title="T"))
        src, _ = get_or_create_source(db, topic.id, SourceKind.url, "http://x.com")
        sc = create_source_content(
            db,
            source_id=src.id,
            fetch_status=FetchStatus.ok,
            extraction_status=ExtractionStatus.ok,
            fetched_at=_now(),
            raw_text="The sky is blue.",
            normalized_text_hash="h",
            extraction_method="html_parser",
        )
        provider = FakeProvider(
            output=_fake_claim_output([_claim_dict(supporting_quote="sky is blue")])
        )
        extract_claims(db, sc, provider=provider)
        evidence = list_active_evidence_for_topic(db, topic.id)
        assert len(evidence) >= 1
        assert evidence[0].claim_text == "The sky is blue."
        assert evidence[0].quote_support_status.value in ("exact", "normalized")

    def test_no_quote_claims_excluded_from_active_evidence(self, db: sqlite3.Connection):
        topic = create_topic(db, Topic(title="T"))
        src, _ = get_or_create_source(db, topic.id, SourceKind.url, "http://x.com")
        sc = create_source_content(
            db,
            source_id=src.id,
            fetch_status=FetchStatus.ok,
            extraction_status=ExtractionStatus.ok,
            fetched_at=_now(),
            raw_text="The sky is blue.",
            normalized_text_hash="h",
            extraction_method="html_parser",
        )
        provider = FakeProvider(output=_fake_claim_output([_claim_dict(supporting_quote=None)]))
        extract_claims(db, sc, provider=provider)
        evidence = list_active_evidence_for_topic(db, topic.id)
        assert evidence == []
