"""Repository layer for Phase 4.1/4.2 source-content and claim persistence."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from app.core.logging import get_logger
from app.core.models import Source, SourceKind
from app.research.models import (
    Claim,
    ClaimExtractionRun,
    ClaimExtractionRunCall,
    ClaimExtractionRunCallStatus,
    ClaimExtractionRunStatus,
    ClaimSupportStatus,
    EvidenceClaim,
    ExtractionStatus,
    FetchStatus,
    SourceContent,
)

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


def get_source_content(conn: sqlite3.Connection, content_id: int) -> SourceContent | None:
    row = conn.execute("SELECT * FROM source_contents WHERE id = ?", (content_id,)).fetchone()
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


# ---------------------------------------------------------------------------
# Phase 4.2 — claim extraction run persistence
# ---------------------------------------------------------------------------


def _row_to_run(row: sqlite3.Row) -> ClaimExtractionRun:
    return ClaimExtractionRun(
        id=row["id"],
        source_content_id=row["source_content_id"],
        status=ClaimExtractionRunStatus(row["status"]),
        input_hash=row["input_hash"],
        total_chunk_count=row["total_chunk_count"],
        completed_chunk_count=row["completed_chunk_count"],
        failed_chunk_count=row["failed_chunk_count"],
        accepted_claim_count=row["accepted_claim_count"],
        was_truncated=bool(row["was_truncated"]),
        prompt_name=row["prompt_name"],
        prompt_version=row["prompt_version"],
        model=row["model"],
        provider=row["provider"],
        extraction_algo_version=row["extraction_algo_version"],
        error_message=row["error_message"],
        superseded_at=row["superseded_at"],
        superseded_by_run_id=row["superseded_by_run_id"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        created_at=row["created_at"],
    )


def _row_to_run_call(row: sqlite3.Row) -> ClaimExtractionRunCall:
    return ClaimExtractionRunCall(
        id=row["id"],
        claim_extraction_run_id=row["claim_extraction_run_id"],
        ai_call_id=row["ai_call_id"],
        chunk_index=row["chunk_index"],
        chunk_hash=row["chunk_hash"],
        input_char_start=row["input_char_start"],
        input_char_end=row["input_char_end"],
        status=ClaimExtractionRunCallStatus(row["status"]),
        retry_count=row["retry_count"],
        accepted_claim_count=row["accepted_claim_count"],
        error_message=row["error_message"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        created_at=row["created_at"],
    )


def _row_to_claim(row: sqlite3.Row) -> Claim:
    from app.research.models import ClaimType

    return Claim(
        id=row["id"],
        extraction_run_id=row["extraction_run_id"],
        chunk_index=row["chunk_index"],
        claim_text=row["claim_text"],
        claim_type=ClaimType(row["claim_type"]),
        supporting_quote=row["supporting_quote"],
        quote_support_status=ClaimSupportStatus(row["quote_support_status"]),
        quote_start=row["quote_start"],
        quote_end=row["quote_end"],
        page_number=row["page_number"],
        requires_date_review=bool(row["requires_date_review"]),
        created_at=row["created_at"],
    )


def create_claim_extraction_run(
    conn: sqlite3.Connection,
    *,
    source_content_id: int,
    input_hash: str,
    total_chunk_count: int,
    was_truncated: bool,
    prompt_name: str,
    prompt_version: str,
    model: str,
    provider: str,
    extraction_algo_version: str,
    started_at: str,
) -> ClaimExtractionRun:
    """Insert a new run with status='running' inside a SAVEPOINT."""
    conn.execute("SAVEPOINT sp_run_insert")
    try:
        cur = conn.execute(
            """
            INSERT INTO claim_extraction_runs (
                source_content_id, status, input_hash,
                total_chunk_count, was_truncated,
                prompt_name, prompt_version, model, provider,
                extraction_algo_version, started_at
            ) VALUES (?, 'running', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_content_id,
                input_hash,
                total_chunk_count,
                1 if was_truncated else 0,
                prompt_name,
                prompt_version,
                model,
                provider,
                extraction_algo_version,
                started_at,
            ),
        )
        conn.execute("RELEASE SAVEPOINT sp_run_insert")
        conn.commit()
        run = get_claim_extraction_run(conn, cur.lastrowid)  # type: ignore[arg-type]
        assert run is not None
        logger.debug(
            "Created claim_extraction_run id=%d source_content_id=%d",
            run.id,
            source_content_id,
        )
        return run
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT sp_run_insert")
        conn.execute("RELEASE SAVEPOINT sp_run_insert")
        raise


def get_claim_extraction_run(conn: sqlite3.Connection, run_id: int) -> ClaimExtractionRun | None:
    row = conn.execute("SELECT * FROM claim_extraction_runs WHERE id = ?", (run_id,)).fetchone()
    return _row_to_run(row) if row else None


def list_claim_extraction_runs(
    conn: sqlite3.Connection,
    source_content_id: int,
    *,
    include_superseded: bool = False,
) -> list[ClaimExtractionRun]:
    """Return runs for *source_content_id*, newest first."""
    if include_superseded:
        rows = conn.execute(
            """
            SELECT * FROM claim_extraction_runs
            WHERE source_content_id = ?
            ORDER BY id DESC
            """,
            (source_content_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM claim_extraction_runs
            WHERE source_content_id = ? AND superseded_at IS NULL
            ORDER BY id DESC
            """,
            (source_content_id,),
        ).fetchall()
    return [_row_to_run(r) for r in rows]


def get_latest_completed_run(
    conn: sqlite3.Connection,
    source_content_id: int,
    input_hash: str,
) -> ClaimExtractionRun | None:
    """Return the active completed run matching *input_hash*, if any."""
    row = conn.execute(
        """
        SELECT * FROM claim_extraction_runs
        WHERE source_content_id = ?
          AND status = 'completed'
          AND superseded_at IS NULL
          AND input_hash = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (source_content_id, input_hash),
    ).fetchone()
    return _row_to_run(row) if row else None


def create_claim_extraction_run_call(
    conn: sqlite3.Connection,
    *,
    claim_extraction_run_id: int,
    chunk_index: int,
    chunk_hash: str,
    input_char_start: int,
    input_char_end: int,
    started_at: str,
) -> ClaimExtractionRunCall:
    """Insert a new chunk call with status='running' inside a SAVEPOINT."""
    conn.execute("SAVEPOINT sp_call_insert")
    try:
        cur = conn.execute(
            """
            INSERT INTO claim_extraction_run_calls (
                claim_extraction_run_id, chunk_index, chunk_hash,
                input_char_start, input_char_end,
                status, started_at
            ) VALUES (?, ?, ?, ?, ?, 'running', ?)
            """,
            (
                claim_extraction_run_id,
                chunk_index,
                chunk_hash,
                input_char_start,
                input_char_end,
                started_at,
            ),
        )
        conn.execute("RELEASE SAVEPOINT sp_call_insert")
        conn.commit()
        call = get_claim_extraction_run_call(conn, cur.lastrowid)  # type: ignore[arg-type]
        assert call is not None
        return call
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT sp_call_insert")
        conn.execute("RELEASE SAVEPOINT sp_call_insert")
        raise


def get_claim_extraction_run_call(
    conn: sqlite3.Connection, call_id: int
) -> ClaimExtractionRunCall | None:
    row = conn.execute(
        "SELECT * FROM claim_extraction_run_calls WHERE id = ?", (call_id,)
    ).fetchone()
    return _row_to_run_call(row) if row else None


def update_claim_extraction_run_call(
    conn: sqlite3.Connection,
    *,
    call_id: int,
    ai_call_id: int | None,
    status: ClaimExtractionRunCallStatus,
    accepted_claim_count: int | None,
    error_message: str | None,
    completed_at: str,
    retry_count: int = 0,
) -> None:
    """Update a chunk call row with its outcome. Plain UPDATE, auto-commits."""
    conn.execute(
        """
        UPDATE claim_extraction_run_calls SET
            ai_call_id = ?,
            status = ?,
            accepted_claim_count = ?,
            error_message = ?,
            completed_at = ?,
            retry_count = ?
        WHERE id = ?
        """,
        (
            ai_call_id,
            status.value,
            accepted_claim_count,
            error_message,
            completed_at,
            retry_count,
            call_id,
        ),
    )
    conn.commit()


def finalize_claim_extraction_run(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    status: ClaimExtractionRunStatus,
    completed_chunk_count: int,
    failed_chunk_count: int,
    accepted_claim_count: int,
    completed_at: str,
    claims: list[dict],
    error_message: str | None = None,
    supersede_run_id: int | None = None,
) -> None:
    """Atomically persist all claims, update run status, and optionally supersede a prior run.

    One SAVEPOINT wraps all INSERTs + the optional supersession UPDATE + the run
    status UPDATE.  On success: RELEASE + conn.commit().
    On failure: ROLLBACK + RELEASE, then a *separate* transaction marks the run failed.

    *claims* is a list of dicts with keys matching the `claims` table columns
    (excluding id, extraction_run_id, created_at).
    """
    conn.execute("SAVEPOINT sp_finalize")
    try:
        for c in claims:
            conn.execute(
                """
                INSERT INTO claims (
                    extraction_run_id, chunk_index,
                    claim_text, claim_type,
                    supporting_quote, quote_support_status,
                    quote_start, quote_end, page_number,
                    requires_date_review
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    c["chunk_index"],
                    c["claim_text"],
                    c["claim_type"],
                    c.get("supporting_quote"),
                    c["quote_support_status"],
                    c.get("quote_start"),
                    c.get("quote_end"),
                    c.get("page_number"),
                    1 if c.get("requires_date_review") else 0,
                ),
            )

        if supersede_run_id is not None:
            conn.execute(
                """
                UPDATE claim_extraction_runs
                SET superseded_at = ?, superseded_by_run_id = ?
                WHERE id = ?
                """,
                (completed_at, run_id, supersede_run_id),
            )

        conn.execute(
            """
            UPDATE claim_extraction_runs SET
                status = ?,
                completed_chunk_count = ?,
                failed_chunk_count = ?,
                accepted_claim_count = ?,
                completed_at = ?,
                error_message = ?
            WHERE id = ?
            """,
            (
                status.value,
                completed_chunk_count,
                failed_chunk_count,
                accepted_claim_count,
                completed_at,
                error_message,
                run_id,
            ),
        )

        conn.execute("RELEASE SAVEPOINT sp_finalize")
        conn.commit()
        logger.debug(
            "Finalized claim_extraction_run id=%d status=%s claims=%d",
            run_id,
            status.value,
            accepted_claim_count,
        )

    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT sp_finalize")
        conn.execute("RELEASE SAVEPOINT sp_finalize")
        # Mark the run failed in a separate transaction.
        try:
            conn.execute(
                """
                UPDATE claim_extraction_runs
                SET status = 'failed', completed_at = ?, error_message = ?
                WHERE id = ?
                """,
                (completed_at, "finalization failed", run_id),
            )
            conn.commit()
        except Exception:
            logger.warning("Could not mark run %d as failed after finalization error", run_id)
        raise


# ---------------------------------------------------------------------------
# Phase 4.2 — claim reads
# ---------------------------------------------------------------------------


def list_claims(
    conn: sqlite3.Connection,
    extraction_run_id: int,
    *,
    include_unsupported: bool = False,
) -> list[Claim]:
    """Return claims for a run, optionally filtering out unsupported/no_quote."""
    if include_unsupported:
        rows = conn.execute(
            "SELECT * FROM claims WHERE extraction_run_id = ? ORDER BY chunk_index, id",
            (extraction_run_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM claims
            WHERE extraction_run_id = ?
              AND quote_support_status IN ('exact', 'normalized')
            ORDER BY chunk_index, id
            """,
            (extraction_run_id,),
        ).fetchall()
    return [_row_to_claim(r) for r in rows]


def list_active_evidence_for_topic(
    conn: sqlite3.Connection,
    topic_id: int,
) -> list[EvidenceClaim]:
    """Return active evidence claims for all sources under *topic_id*.

    Active = run status='completed' AND superseded_at IS NULL
             AND quote_support_status IN ('exact', 'normalized').
    Joined to source_contents and claim_extraction_runs for full provenance.
    """
    from app.research.models import ClaimType

    rows = conn.execute(
        """
        SELECT
            c.id                        AS claim_id,
            c.claim_text,
            c.claim_type,
            c.supporting_quote,
            c.quote_support_status,
            c.quote_start,
            c.quote_end,
            c.page_number,
            c.chunk_index,
            c.requires_date_review,
            sc.source_id,
            sc.id                       AS source_content_id,
            r.id                        AS extraction_run_id,
            sc.title                    AS source_title,
            sc.canonical_url,
            sc.author,
            sc.published_at,
            sc.quality_score,
            sc.extraction_status,
            sc.suspected_truncation,
            r.prompt_name,
            r.prompt_version,
            r.model
        FROM claims c
        JOIN claim_extraction_runs r
            ON r.id = c.extraction_run_id
        JOIN source_contents sc
            ON sc.id = r.source_content_id
        JOIN sources s
            ON s.id = sc.source_id
        WHERE s.topic_id = ?
          AND r.status = 'completed'
          AND r.superseded_at IS NULL
          AND c.quote_support_status IN ('exact', 'normalized')
        ORDER BY s.id, sc.id, c.chunk_index, c.id
        """,
        (topic_id,),
    ).fetchall()

    return [
        EvidenceClaim(
            claim_id=r["claim_id"],
            claim_text=r["claim_text"],
            claim_type=ClaimType(r["claim_type"]),
            supporting_quote=r["supporting_quote"],
            quote_support_status=ClaimSupportStatus(r["quote_support_status"]),
            quote_start=r["quote_start"],
            quote_end=r["quote_end"],
            page_number=r["page_number"],
            chunk_index=r["chunk_index"],
            requires_date_review=bool(r["requires_date_review"]),
            source_id=r["source_id"],
            source_content_id=r["source_content_id"],
            extraction_run_id=r["extraction_run_id"],
            source_title=r["source_title"],
            canonical_url=r["canonical_url"],
            author=r["author"],
            published_at=r["published_at"],
            quality_score=r["quality_score"],
            extraction_status=r["extraction_status"],
            suspected_truncation=bool(r["suspected_truncation"]),
            prompt_name=r["prompt_name"],
            prompt_version=r["prompt_version"],
            model=r["model"],
        )
        for r in rows
    ]
