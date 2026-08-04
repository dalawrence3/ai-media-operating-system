"""Tests for Phase 4.1 research repository operations."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from app.core.models import SourceKind, Topic
from app.core.repository import create_topic, get_source
from app.research.models import ExtractionStatus, FetchStatus
from app.research.repository import (
    create_source_content,
    get_latest_source_content,
    get_or_create_source,
    list_source_contents,
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
