"""Tests for Phase 4.1/4.2 research repository operations."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.core.models import SourceKind, Topic
from app.core.repository import create_topic, get_source
from app.research.models import (
    ClaimExtractionRunCallStatus,
    ClaimExtractionRunStatus,
    ExtractionStatus,
    FetchStatus,
)
from app.research.repository import (
    create_claim_extraction_run,
    create_claim_extraction_run_call,
    create_source_content,
    finalize_claim_extraction_run,
    get_claim_extraction_run,
    get_claim_extraction_run_call,
    get_latest_completed_run,
    get_latest_source_content,
    get_or_create_source,
    list_active_evidence_for_topic,
    list_claim_extraction_runs,
    list_claims,
    list_source_contents,
    update_claim_extraction_run_call,
)


def _make_topic(conn: sqlite3.Connection, title: str = "Test Topic") -> Topic:
    return create_topic(conn, Topic(title=title))


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _create_ok_content(conn: sqlite3.Connection, source_id: int, **kwargs) -> object:
    defaults = dict(
        source_id=source_id,
        fetch_status=FetchStatus.ok,
        extraction_status=ExtractionStatus.ok,
        fetched_at=_now(),
        raw_text="hello world content",
        retrieval_hash="abc123",
        normalized_text_hash="def456",
        word_count=3,
    )
    defaults.update(kwargs)
    return create_source_content(conn, **defaults)


class TestGetOrCreateSource:
    def test_creates_new_source(self, db: sqlite3.Connection):
        topic = _make_topic(db)
        src, created = get_or_create_source(db, topic.id, SourceKind.url, "http://example.com")
        assert created is True
        assert src.id is not None
        assert src.topic_id == topic.id
        assert src.kind == SourceKind.url
        assert src.reference == "http://example.com"

    def test_returns_existing_source(self, db: sqlite3.Connection):
        topic = _make_topic(db)
        src1, _ = get_or_create_source(db, topic.id, SourceKind.url, "http://example.com")
        src2, created = get_or_create_source(db, topic.id, SourceKind.url, "http://example.com")
        assert created is False
        assert src1.id == src2.id

    def test_different_url_creates_new_source(self, db: sqlite3.Connection):
        topic = _make_topic(db)
        src1, _ = get_or_create_source(db, topic.id, SourceKind.url, "http://a.com")
        src2, _ = get_or_create_source(db, topic.id, SourceKind.url, "http://b.com")
        assert src1.id != src2.id

    def test_different_kind_creates_new_source(self, db: sqlite3.Connection):
        topic = _make_topic(db)
        src1, _ = get_or_create_source(db, topic.id, SourceKind.url, "same_ref")
        src2, _ = get_or_create_source(db, topic.id, SourceKind.file, "same_ref")
        assert src1.id != src2.id

    def test_source_is_visible_via_get_source(self, db: sqlite3.Connection):
        topic = _make_topic(db)
        src, _ = get_or_create_source(db, topic.id, SourceKind.url, "http://x.com")
        fetched = get_source(db, src.id)
        assert fetched is not None
        assert fetched.id == src.id


class TestCreateSourceContent:
    def test_creates_ok_row(self, db: sqlite3.Connection):
        topic = _make_topic(db)
        src, _ = get_or_create_source(db, topic.id, SourceKind.url, "http://x.com")
        sc = _create_ok_content(db, src.id)
        assert sc.id is not None
        assert sc.fetch_status == FetchStatus.ok
        assert sc.extraction_status == ExtractionStatus.ok
        assert sc.raw_text == "hello world content"

    def test_creates_failed_fetch_row(self, db: sqlite3.Connection):
        topic = _make_topic(db)
        src, _ = get_or_create_source(db, topic.id, SourceKind.url, "http://x.com")
        sc = create_source_content(
            db,
            source_id=src.id,
            fetch_status=FetchStatus.failed,
            extraction_status=ExtractionStatus.failed,
            fetched_at=_now(),
            http_status=404,
            extraction_error="HTTP 404",
        )
        assert sc.fetch_status == FetchStatus.failed
        assert sc.http_status == 404
        assert sc.raw_text is None

    def test_creates_partial_extraction_row(self, db: sqlite3.Connection):
        topic = _make_topic(db)
        src, _ = get_or_create_source(db, topic.id, SourceKind.url, "http://x.com")
        sc = create_source_content(
            db,
            source_id=src.id,
            fetch_status=FetchStatus.ok,
            extraction_status=ExtractionStatus.partial,
            fetched_at=_now(),
            raw_text="partial content",
            extraction_error="Some pages failed",
        )
        assert sc.extraction_status == ExtractionStatus.partial
        assert sc.raw_text == "partial content"

    def test_suspected_truncation_persisted(self, db: sqlite3.Connection):
        topic = _make_topic(db)
        src, _ = get_or_create_source(db, topic.id, SourceKind.url, "http://x.com")
        sc = _create_ok_content(db, src.id, suspected_truncation=True)
        assert sc.suspected_truncation is True

    def test_quality_fields_persisted(self, db: sqlite3.Connection):
        topic = _make_topic(db)
        src, _ = get_or_create_source(db, topic.id, SourceKind.url, "http://x.com")
        sc = _create_ok_content(
            db,
            src.id,
            quality_score=0.72,
            quality_factors_json='{"recency": 0.9}',
            quality_scorer_version="quality-v1",
        )
        assert sc.quality_score == pytest.approx(0.72)
        assert sc.quality_scorer_version == "quality-v1"


class TestGetLatestSourceContent:
    def test_returns_none_when_no_content(self, db: sqlite3.Connection):
        topic = _make_topic(db)
        src, _ = get_or_create_source(db, topic.id, SourceKind.url, "http://x.com")
        assert get_latest_source_content(db, src.id) is None

    def test_returns_most_recent_ok_row(self, db: sqlite3.Connection):
        topic = _make_topic(db)
        src, _ = get_or_create_source(db, topic.id, SourceKind.url, "http://x.com")
        _create_ok_content(db, src.id, normalized_text_hash="hash1")
        sc2 = _create_ok_content(db, src.id, normalized_text_hash="hash2")
        latest = get_latest_source_content(db, src.id)
        assert latest is not None
        assert latest.id == sc2.id

    def test_skips_failed_rows_when_require_successful(self, db: sqlite3.Connection):
        topic = _make_topic(db)
        src, _ = get_or_create_source(db, topic.id, SourceKind.url, "http://x.com")
        _create_ok_content(db, src.id)
        create_source_content(
            db,
            source_id=src.id,
            fetch_status=FetchStatus.failed,
            extraction_status=ExtractionStatus.failed,
            fetched_at=_now(),
            extraction_error="timeout",
        )
        latest = get_latest_source_content(db, src.id, require_successful=True)
        assert latest is not None
        assert latest.fetch_status == FetchStatus.ok

    def test_returns_failed_row_when_not_require_successful(self, db: sqlite3.Connection):
        topic = _make_topic(db)
        src, _ = get_or_create_source(db, topic.id, SourceKind.url, "http://x.com")
        create_source_content(
            db,
            source_id=src.id,
            fetch_status=FetchStatus.failed,
            extraction_status=ExtractionStatus.failed,
            fetched_at=_now(),
            extraction_error="timeout",
        )
        latest = get_latest_source_content(db, src.id, require_successful=False)
        assert latest is not None
        assert latest.fetch_status == FetchStatus.failed


class TestListSourceContents:
    def test_returns_empty_list(self, db: sqlite3.Connection):
        topic = _make_topic(db)
        src, _ = get_or_create_source(db, topic.id, SourceKind.url, "http://x.com")
        assert list_source_contents(db, src.id) == []

    def test_returns_all_rows_newest_first(self, db: sqlite3.Connection):
        topic = _make_topic(db)
        src, _ = get_or_create_source(db, topic.id, SourceKind.url, "http://x.com")
        sc1 = _create_ok_content(db, src.id)
        sc2 = _create_ok_content(db, src.id)
        rows = list_source_contents(db, src.id)
        assert len(rows) == 2
        assert rows[0].id == sc2.id
        assert rows[1].id == sc1.id


# ---------------------------------------------------------------------------
# Helpers for Phase 4.2 tests
# ---------------------------------------------------------------------------

_RUN_DEFAULTS = dict(
    input_hash="hash-abc",
    total_chunk_count=3,
    was_truncated=False,
    prompt_name="claim-extraction",
    prompt_version="1",
    model="claude-sonnet-5",
    provider="anthropic",
    extraction_algo_version="4.2-v1",
    started_at="2024-01-01T00:00:00",
)

_CALL_DEFAULTS = dict(
    chunk_index=0,
    chunk_hash="chunkhash",
    input_char_start=0,
    input_char_end=100,
    started_at="2024-01-01T00:00:01",
)

_CLAIM = dict(
    chunk_index=0,
    claim_text="The sky is blue.",
    claim_type="factual",
    supporting_quote="sky is blue",
    quote_support_status="exact",
    quote_start=4,
    quote_end=15,
    page_number=None,
    requires_date_review=False,
)

_CLAIM_UNSUPPORTED = dict(
    chunk_index=1,
    claim_text="Water is wet and known to all.",
    claim_type="factual",
    supporting_quote=None,
    quote_support_status="no_quote",
    quote_start=None,
    quote_end=None,
    page_number=None,
    requires_date_review=False,
)


def _sc_and_run(db: sqlite3.Connection) -> tuple:
    """Return (source_content, run) with defaults."""
    topic = _make_topic(db)
    src, _ = get_or_create_source(db, topic.id, SourceKind.url, "http://x.com")
    sc = _create_ok_content(db, src.id)
    run = create_claim_extraction_run(db, source_content_id=sc.id, **_RUN_DEFAULTS)
    return sc, run


# ---------------------------------------------------------------------------
# Phase 4.2 — ClaimExtractionRun
# ---------------------------------------------------------------------------


class TestCreateClaimExtractionRun:
    def test_creates_run_with_running_status(self, db: sqlite3.Connection):
        _, run = _sc_and_run(db)
        assert run.id is not None
        assert run.status == ClaimExtractionRunStatus.running
        assert run.total_chunk_count == 3
        assert run.was_truncated is False

    def test_get_returns_run(self, db: sqlite3.Connection):
        _, run = _sc_and_run(db)
        fetched = get_claim_extraction_run(db, run.id)
        assert fetched is not None
        assert fetched.id == run.id
        assert fetched.input_hash == "hash-abc"

    def test_get_returns_none_for_missing(self, db: sqlite3.Connection):
        assert get_claim_extraction_run(db, 9999) is None

    def test_list_runs_returns_run(self, db: sqlite3.Connection):
        sc, run = _sc_and_run(db)
        runs = list_claim_extraction_runs(db, sc.id)
        assert len(runs) == 1
        assert runs[0].id == run.id

    def test_list_runs_returns_newest_first(self, db: sqlite3.Connection):
        topic = _make_topic(db)
        src, _ = get_or_create_source(db, topic.id, SourceKind.url, "http://z.com")
        sc = _create_ok_content(db, src.id)
        r1 = create_claim_extraction_run(db, source_content_id=sc.id, **_RUN_DEFAULTS)
        r2 = create_claim_extraction_run(
            db, source_content_id=sc.id, **{**_RUN_DEFAULTS, "input_hash": "hash-xyz"}
        )
        runs = list_claim_extraction_runs(db, sc.id)
        assert runs[0].id == r2.id
        assert runs[1].id == r1.id


# ---------------------------------------------------------------------------
# Phase 4.2 — ClaimExtractionRunCall
# ---------------------------------------------------------------------------


class TestClaimExtractionRunCall:
    def test_creates_call_with_running_status(self, db: sqlite3.Connection):
        _, run = _sc_and_run(db)
        call = create_claim_extraction_run_call(
            db, claim_extraction_run_id=run.id, **_CALL_DEFAULTS
        )
        assert call.id is not None
        assert call.status == ClaimExtractionRunCallStatus.running
        assert call.chunk_index == 0
        assert call.retry_count == 0

    def test_unique_constraint_on_run_chunk(self, db: sqlite3.Connection):
        _, run = _sc_and_run(db)
        create_claim_extraction_run_call(db, claim_extraction_run_id=run.id, **_CALL_DEFAULTS)
        import sqlite3 as _sqlite3

        with pytest.raises(_sqlite3.IntegrityError):
            create_claim_extraction_run_call(
                db, claim_extraction_run_id=run.id, **_CALL_DEFAULTS
            )

    def test_update_call_outcome(self, db: sqlite3.Connection):
        _, run = _sc_and_run(db)
        call = create_claim_extraction_run_call(
            db, claim_extraction_run_id=run.id, **_CALL_DEFAULTS
        )
        # ai_call_id=None because no ai_calls row exists; FK allows NULL.
        update_claim_extraction_run_call(
            db,
            call_id=call.id,
            ai_call_id=None,
            status=ClaimExtractionRunCallStatus.completed,
            accepted_claim_count=2,
            error_message=None,
            completed_at="2024-01-01T00:01:00",
            retry_count=0,
        )
        fetched = get_claim_extraction_run_call(db, call.id)
        assert fetched.status == ClaimExtractionRunCallStatus.completed
        assert fetched.ai_call_id is None
        assert fetched.accepted_claim_count == 2


# ---------------------------------------------------------------------------
# Phase 4.2 — finalize_claim_extraction_run
# ---------------------------------------------------------------------------


class TestFinalizeClaimExtractionRun:
    def test_completes_run_and_inserts_claims(self, db: sqlite3.Connection):
        sc, run = _sc_and_run(db)
        finalize_claim_extraction_run(
            db,
            run_id=run.id,
            status=ClaimExtractionRunStatus.completed,
            completed_chunk_count=3,
            failed_chunk_count=0,
            accepted_claim_count=1,
            completed_at="2024-01-01T01:00:00",
            claims=[_CLAIM],
        )
        updated = get_claim_extraction_run(db, run.id)
        assert updated.status == ClaimExtractionRunStatus.completed
        assert updated.accepted_claim_count == 1

    def test_claims_inserted_correctly(self, db: sqlite3.Connection):
        _, run = _sc_and_run(db)
        finalize_claim_extraction_run(
            db,
            run_id=run.id,
            status=ClaimExtractionRunStatus.completed,
            completed_chunk_count=1,
            failed_chunk_count=0,
            accepted_claim_count=1,
            completed_at="2024-01-01T01:00:00",
            claims=[_CLAIM],
        )
        claims = list_claims(db, run.id, include_unsupported=True)
        assert len(claims) == 1
        assert claims[0].claim_text == "The sky is blue."
        assert claims[0].quote_start == 4
        assert claims[0].quote_end == 15

    def test_supersession_marks_prior_run(self, db: sqlite3.Connection):
        sc, run1 = _sc_and_run(db)
        # Finalize run1 as completed first.
        finalize_claim_extraction_run(
            db,
            run_id=run1.id,
            status=ClaimExtractionRunStatus.completed,
            completed_chunk_count=1,
            failed_chunk_count=0,
            accepted_claim_count=1,
            completed_at="2024-01-01T01:00:00",
            claims=[_CLAIM],
        )
        # Create run2 and finalize it, superseding run1.
        run2 = create_claim_extraction_run(
            db, source_content_id=sc.id, **{**_RUN_DEFAULTS, "input_hash": "hash-2"}
        )
        finalize_claim_extraction_run(
            db,
            run_id=run2.id,
            status=ClaimExtractionRunStatus.completed,
            completed_chunk_count=1,
            failed_chunk_count=0,
            accepted_claim_count=1,
            completed_at="2024-01-01T02:00:00",
            claims=[_CLAIM],
            supersede_run_id=run1.id,
        )
        run1_after = get_claim_extraction_run(db, run1.id)
        assert run1_after.superseded_at is not None
        assert run1_after.superseded_by_run_id == run2.id
        # run1 status is still 'completed' (supersession is via columns, not status).
        assert run1_after.status == ClaimExtractionRunStatus.completed

    def test_failure_injection_marks_run_failed(self, db: sqlite3.Connection):
        _, run = _sc_and_run(db)
        # Pass a claim with an invalid claim_type to trigger a DB constraint error.
        bad_claim = {**_CLAIM, "claim_type": "INVALID_TYPE"}
        with pytest.raises(sqlite3.IntegrityError):
            finalize_claim_extraction_run(
                db,
                run_id=run.id,
                status=ClaimExtractionRunStatus.completed,
                completed_chunk_count=1,
                failed_chunk_count=0,
                accepted_claim_count=1,
                completed_at="2024-01-01T01:00:00",
                claims=[bad_claim],
            )
        # The run should have been marked failed.
        updated = get_claim_extraction_run(db, run.id)
        assert updated.status == ClaimExtractionRunStatus.failed

    def test_no_claims_allowed(self, db: sqlite3.Connection):
        _, run = _sc_and_run(db)
        finalize_claim_extraction_run(
            db,
            run_id=run.id,
            status=ClaimExtractionRunStatus.failed,
            completed_chunk_count=0,
            failed_chunk_count=3,
            accepted_claim_count=0,
            completed_at="2024-01-01T01:00:00",
            claims=[],
            error_message="All chunks failed",
        )
        updated = get_claim_extraction_run(db, run.id)
        assert updated.status == ClaimExtractionRunStatus.failed
        assert updated.error_message == "All chunks failed"


# ---------------------------------------------------------------------------
# Phase 4.2 — get_latest_completed_run
# ---------------------------------------------------------------------------


class TestGetLatestCompletedRun:
    def test_returns_none_when_no_completed(self, db: sqlite3.Connection):
        sc, _ = _sc_and_run(db)
        result = get_latest_completed_run(db, sc.id, "hash-abc")
        assert result is None

    def test_returns_completed_run(self, db: sqlite3.Connection):
        sc, run = _sc_and_run(db)
        finalize_claim_extraction_run(
            db,
            run_id=run.id,
            status=ClaimExtractionRunStatus.completed,
            completed_chunk_count=1,
            failed_chunk_count=0,
            accepted_claim_count=1,
            completed_at="2024-01-01T01:00:00",
            claims=[_CLAIM],
        )
        result = get_latest_completed_run(db, sc.id, "hash-abc")
        assert result is not None
        assert result.id == run.id

    def test_returns_none_when_hash_mismatch(self, db: sqlite3.Connection):
        sc, run = _sc_and_run(db)
        finalize_claim_extraction_run(
            db,
            run_id=run.id,
            status=ClaimExtractionRunStatus.completed,
            completed_chunk_count=1,
            failed_chunk_count=0,
            accepted_claim_count=1,
            completed_at="2024-01-01T01:00:00",
            claims=[_CLAIM],
        )
        result = get_latest_completed_run(db, sc.id, "different-hash")
        assert result is None

    def test_returns_none_when_superseded(self, db: sqlite3.Connection):
        sc, run1 = _sc_and_run(db)
        finalize_claim_extraction_run(
            db,
            run_id=run1.id,
            status=ClaimExtractionRunStatus.completed,
            completed_chunk_count=1,
            failed_chunk_count=0,
            accepted_claim_count=1,
            completed_at="2024-01-01T01:00:00",
            claims=[_CLAIM],
        )
        run2 = create_claim_extraction_run(
            db, source_content_id=sc.id, **{**_RUN_DEFAULTS, "input_hash": "hash-2"}
        )
        finalize_claim_extraction_run(
            db,
            run_id=run2.id,
            status=ClaimExtractionRunStatus.completed,
            completed_chunk_count=1,
            failed_chunk_count=0,
            accepted_claim_count=1,
            completed_at="2024-01-01T02:00:00",
            claims=[_CLAIM],
            supersede_run_id=run1.id,
        )
        result = get_latest_completed_run(db, sc.id, "hash-abc")
        assert result is None


# ---------------------------------------------------------------------------
# Phase 4.2 — list_claims
# ---------------------------------------------------------------------------


class TestListClaims:
    def _finalize_with_claims(self, db: sqlite3.Connection, run_id: int) -> None:
        finalize_claim_extraction_run(
            db,
            run_id=run_id,
            status=ClaimExtractionRunStatus.completed,
            completed_chunk_count=2,
            failed_chunk_count=0,
            accepted_claim_count=2,
            completed_at="2024-01-01T01:00:00",
            claims=[_CLAIM, _CLAIM_UNSUPPORTED],
        )

    def test_excludes_unsupported_by_default(self, db: sqlite3.Connection):
        _, run = _sc_and_run(db)
        self._finalize_with_claims(db, run.id)
        claims = list_claims(db, run.id)
        assert all(c.quote_support_status.value in ("exact", "normalized") for c in claims)

    def test_includes_unsupported_when_flag_set(self, db: sqlite3.Connection):
        _, run = _sc_and_run(db)
        self._finalize_with_claims(db, run.id)
        claims = list_claims(db, run.id, include_unsupported=True)
        statuses = {c.quote_support_status.value for c in claims}
        assert "no_quote" in statuses

    def test_empty_for_run_with_no_claims(self, db: sqlite3.Connection):
        _, run = _sc_and_run(db)
        finalize_claim_extraction_run(
            db,
            run_id=run.id,
            status=ClaimExtractionRunStatus.failed,
            completed_chunk_count=0,
            failed_chunk_count=1,
            accepted_claim_count=0,
            completed_at="2024-01-01T01:00:00",
            claims=[],
        )
        assert list_claims(db, run.id, include_unsupported=True) == []


# ---------------------------------------------------------------------------
# Phase 4.2 — list_active_evidence_for_topic
# ---------------------------------------------------------------------------


class TestListActiveEvidenceForTopic:
    def test_returns_active_evidence(self, db: sqlite3.Connection):
        topic = _make_topic(db)
        src, _ = get_or_create_source(db, topic.id, SourceKind.url, "http://x.com")
        sc = _create_ok_content(db, src.id)
        run = create_claim_extraction_run(db, source_content_id=sc.id, **_RUN_DEFAULTS)
        finalize_claim_extraction_run(
            db,
            run_id=run.id,
            status=ClaimExtractionRunStatus.completed,
            completed_chunk_count=1,
            failed_chunk_count=0,
            accepted_claim_count=1,
            completed_at="2024-01-01T01:00:00",
            claims=[_CLAIM],
        )
        evidence = list_active_evidence_for_topic(db, topic.id)
        assert len(evidence) == 1
        ev = evidence[0]
        assert ev.claim_text == "The sky is blue."
        assert ev.source_id == src.id
        assert ev.source_content_id == sc.id
        assert ev.extraction_run_id == run.id

    def test_excludes_no_quote_and_unsupported(self, db: sqlite3.Connection):
        topic = _make_topic(db)
        src, _ = get_or_create_source(db, topic.id, SourceKind.url, "http://x.com")
        sc = _create_ok_content(db, src.id)
        run = create_claim_extraction_run(db, source_content_id=sc.id, **_RUN_DEFAULTS)
        finalize_claim_extraction_run(
            db,
            run_id=run.id,
            status=ClaimExtractionRunStatus.completed,
            completed_chunk_count=1,
            failed_chunk_count=0,
            accepted_claim_count=1,
            completed_at="2024-01-01T01:00:00",
            claims=[_CLAIM_UNSUPPORTED],
        )
        assert list_active_evidence_for_topic(db, topic.id) == []

    def test_excludes_superseded_runs(self, db: sqlite3.Connection):
        topic = _make_topic(db)
        src, _ = get_or_create_source(db, topic.id, SourceKind.url, "http://x.com")
        sc = _create_ok_content(db, src.id)
        run1 = create_claim_extraction_run(db, source_content_id=sc.id, **_RUN_DEFAULTS)
        finalize_claim_extraction_run(
            db,
            run_id=run1.id,
            status=ClaimExtractionRunStatus.completed,
            completed_chunk_count=1,
            failed_chunk_count=0,
            accepted_claim_count=1,
            completed_at="2024-01-01T01:00:00",
            claims=[_CLAIM],
        )
        run2 = create_claim_extraction_run(
            db, source_content_id=sc.id, **{**_RUN_DEFAULTS, "input_hash": "hash-2"}
        )
        finalize_claim_extraction_run(
            db,
            run_id=run2.id,
            status=ClaimExtractionRunStatus.completed,
            completed_chunk_count=1,
            failed_chunk_count=0,
            accepted_claim_count=1,
            completed_at="2024-01-01T02:00:00",
            claims=[_CLAIM],
            supersede_run_id=run1.id,
        )
        evidence = list_active_evidence_for_topic(db, topic.id)
        # Only run2's claims should appear; run1 is superseded.
        assert all(ev.extraction_run_id == run2.id for ev in evidence)

    def test_returns_empty_for_other_topic(self, db: sqlite3.Connection):
        topic1 = _make_topic(db, "Topic A")
        topic2 = _make_topic(db, "Topic B")
        src, _ = get_or_create_source(db, topic1.id, SourceKind.url, "http://x.com")
        sc = _create_ok_content(db, src.id)
        run = create_claim_extraction_run(db, source_content_id=sc.id, **_RUN_DEFAULTS)
        finalize_claim_extraction_run(
            db,
            run_id=run.id,
            status=ClaimExtractionRunStatus.completed,
            completed_chunk_count=1,
            failed_chunk_count=0,
            accepted_claim_count=1,
            completed_at="2024-01-01T01:00:00",
            claims=[_CLAIM],
        )
        assert list_active_evidence_for_topic(db, topic2.id) == []

    def test_evidence_claim_is_frozen(self, db: sqlite3.Connection):
        topic = _make_topic(db)
        src, _ = get_or_create_source(db, topic.id, SourceKind.url, "http://x.com")
        sc = _create_ok_content(db, src.id)
        run = create_claim_extraction_run(db, source_content_id=sc.id, **_RUN_DEFAULTS)
        finalize_claim_extraction_run(
            db,
            run_id=run.id,
            status=ClaimExtractionRunStatus.completed,
            completed_chunk_count=1,
            failed_chunk_count=0,
            accepted_claim_count=1,
            completed_at="2024-01-01T01:00:00",
            claims=[_CLAIM],
        )
        evidence = list_active_evidence_for_topic(db, topic.id)
        ev = evidence[0]
        with pytest.raises((TypeError, ValidationError)):
            ev.claim_text = "mutated"  # type: ignore[misc]
