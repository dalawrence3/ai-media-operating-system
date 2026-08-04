"""Repository layer for Phase 4.1 source-content persistence."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from app.core.logging import get_logger
from app.core.models import Source, SourceKind
from app.research.models import ExtractionStatus, FetchStatus, SourceContent

logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _row_to_source_content(row: sqlite3.Row) -> SourceContent:
    return SourceContent(
        id=row["id"],
        source_id=row["source_id"],
        fetch_status=FetchStatus(row["fetch_status"]),
        extraction_status=ExtractionStatus(row["extraction_status"]),
        http_status=row["http_status"],
        canonical_url=row["canonical_url"],
        mime_type=row["mime_type"],
        fetched_at=row["fetched_at"],
        raw_text=row["raw_text"],
        retrieval_hash=row["retrieval_hash"],
        normalized_text_hash=row["normalized_text_hash"],
        hash_algorithm=row["hash_algorithm"],
        word_count=row["word_count"],
        title=row["title"],
        author=row["author"],
        published_at=row["published_at"],
        domain_type=row["domain_type"],
        extraction_method=row["extraction_method"],
        extraction_error=row["extraction_error"],
        suspected_truncation=bool(row["suspected_truncation"]),
        quality_score=row["quality_score"],
        quality_factors_json=row["quality_factors_json"],
        quality_scorer_version=row["quality_scorer_version"],
        created_at=row["created_at"],
    )


# ---------------------------------------------------------------------------
# Source identity
# ---------------------------------------------------------------------------


def get_or_create_source(
    conn: sqlite3.Connection,
    topic_id: int,
    kind: SourceKind,
    reference: str,
) -> tuple[Source, bool]:
    """Return the existing Source or create a new one.

    Returns (source, created) where created is True if a new row was inserted.
    Uses a SAVEPOINT so callers can nest inside outer savepoints.
    """
    from app.core.repository import get_source

    conn.execute("SAVEPOINT sp_source_upsert")
    try:
        row = conn.execute(
            "SELECT * FROM sources WHERE topic_id = ? AND kind = ? AND reference = ?",
            (topic_id, kind.value, reference),
        ).fetchone()

        if row:
            conn.execute("RELEASE SAVEPOINT sp_source_upsert")
            from app.core.models import SourceKind as SK
            src = Source(
                id=row["id"],
                topic_id=row["topic_id"],
                kind=SK(row["kind"]),
                reference=row["reference"],
                notes=row["notes"],
                created_at=row["created_at"],
            )
            logger.debug("Reusing source id=%d topic_id=%d", src.id, topic_id)
            return src, False

        # create_source commits internally; that's fine — the SAVEPOINT is already released
        # below after we exit this block.
        # Actually: create_source calls conn.commit() which commits the outer transaction
        # but SAVEPOINTs are still in effect with conn.commit() in SQLite WAL.
        # To stay within SAVEPOINT semantics, insert directly.
        now = _now()
        cur = conn.execute(
            "INSERT INTO sources (topic_id, kind, reference, notes, created_at)"
            " VALUES (?, ?, ?, '', ?)",
            (topic_id, kind.value, reference, now),
        )
        conn.execute("RELEASE SAVEPOINT sp_source_upsert")
        conn.commit()
        src = get_source(conn, cur.lastrowid)  # type: ignore[arg-type]
        assert src is not None
        logger.debug("Created source id=%d topic_id=%d kind=%s", src.id, topic_id, kind.value)
        return src, True

    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT sp_source_upsert")
        conn.execute("RELEASE SAVEPOINT sp_source_upsert")
        raise


# ---------------------------------------------------------------------------
# SourceContent persistence
# ---------------------------------------------------------------------------


def create_source_content(
    conn: sqlite3.Connection,
    *,
    source_id: int,
    fetch_status: FetchStatus,
    extraction_status: ExtractionStatus,
    fetched_at: str,
    http_status: int | None = None,
    canonical_url: str | None = None,
    mime_type: str | None = None,
    raw_text: str | None = None,
    retrieval_hash: str | None = None,
    normalized_text_hash: str | None = None,
    hash_algorithm: str = "sha256-nfc-v1",
    word_count: int | None = None,
    title: str | None = None,
    author: str | None = None,
    published_at: str | None = None,
    domain_type: str | None = None,
    extraction_method: str | None = None,
    extraction_error: str | None = None,
    suspected_truncation: bool = False,
    quality_score: float | None = None,
    quality_factors_json: str | None = None,
    quality_scorer_version: str | None = None,
) -> SourceContent:
    """Insert one SourceContent row inside a SAVEPOINT and return it."""
    conn.execute("SAVEPOINT sp_content_insert")
    try:
        cur = conn.execute(
            """
            INSERT INTO source_contents (
                source_id, fetch_status, extraction_status,
                http_status, canonical_url, mime_type,
                fetched_at,
                raw_text, retrieval_hash, normalized_text_hash, hash_algorithm,
                word_count, title, author, published_at,
                domain_type, extraction_method,
                extraction_error, suspected_truncation,
                quality_score, quality_factors_json, quality_scorer_version
            ) VALUES (
                ?,?,?,  ?,?,?,  ?,  ?,?,?,?,  ?,?,?,?,  ?,?,  ?,?,  ?,?,?
            )
            """,
            (
                source_id,
                fetch_status.value,
                extraction_status.value,
                http_status,
                canonical_url,
                mime_type,
                fetched_at,
                raw_text,
                retrieval_hash,
                normalized_text_hash,
                hash_algorithm,
                word_count,
                title,
                author,
                published_at,
                domain_type,
                extraction_method,
                extraction_error,
                1 if suspected_truncation else 0,
                quality_score,
                quality_factors_json,
                quality_scorer_version,
            ),
        )
        conn.execute("RELEASE SAVEPOINT sp_content_insert")
        conn.commit()
        sc = get_source_content(conn, cur.lastrowid)  # type: ignore[arg-type]
        assert sc is not None
        logger.debug(
            "Created source_contents id=%d source_id=%d fetch=%s extraction=%s",
            sc.id,
            source_id,
            fetch_status.value,
            extraction_status.value,
        )
        return sc
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT sp_content_insert")
        conn.execute("RELEASE SAVEPOINT sp_content_insert")
        raise


def get_source_content(
    conn: sqlite3.Connection, content_id: int
) -> SourceContent | None:
    row = conn.execute(
        "SELECT * FROM source_contents WHERE id = ?", (content_id,)
    ).fetchone()
    return _row_to_source_content(row) if row else None


def get_latest_source_content(
    conn: sqlite3.Connection,
    source_id: int,
    *,
    require_successful: bool = True,
) -> SourceContent | None:
    """Return the most recent SourceContent for *source_id*.

    When *require_successful* is True (default), only rows with fetch_status='ok'
    and extraction_status in ('ok','partial') are considered.
    """
    if require_successful:
        row = conn.execute(
            """
            SELECT * FROM source_contents
            WHERE source_id = ?
              AND fetch_status = 'ok'
              AND extraction_status IN ('ok', 'partial')
            ORDER BY fetched_at DESC, id DESC
            LIMIT 1
            """,
            (source_id,),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT * FROM source_contents
            WHERE source_id = ?
            ORDER BY fetched_at DESC, id DESC
            LIMIT 1
            """,
            (source_id,),
        ).fetchone()
    return _row_to_source_content(row) if row else None


def list_source_contents(
    conn: sqlite3.Connection,
    source_id: int,
) -> list[SourceContent]:
    """Return all SourceContent rows for *source_id*, newest first."""
    rows = conn.execute(
        """
        SELECT * FROM source_contents
        WHERE source_id = ?
        ORDER BY fetched_at DESC, id DESC
        """,
        (source_id,),
    ).fetchall()
    return [_row_to_source_content(r) for r in rows]
