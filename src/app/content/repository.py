"""Phase 5 content repository — script generation runs, citations, and Phase 6 handoff.

All DB writes that span multiple tables use a SAVEPOINT so that a failure
in any step rolls back the entire unit of work.  No auto-committing helper
(e.g. record_ai_call) may be called inside a SAVEPOINT.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from app.content.errors import MalformedBodyJsonError, UnstructuredApprovedScriptError
from app.content.models import ScriptGenerationRun, ScriptGenerationRunStatus
from app.content.schemas import (
    ApprovedScript,
    GeneratedScript,
    ScriptCitation,
)
from app.core.logging import get_logger

logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _row_to_run(row: sqlite3.Row) -> ScriptGenerationRun:
    return ScriptGenerationRun(
        id=row["id"],
        topic_id=row["topic_id"],
        script_id=row["script_id"],
        status=ScriptGenerationRunStatus(row["status"]),
        input_hash=row["input_hash"],
        evidence_hash=row["evidence_hash"],
        prompt_hash=row["prompt_hash"],
        prompt_name=row["prompt_name"],
        prompt_version=row["prompt_version"],
        model=row["model"],
        temperature=row["temperature"],
        max_tokens=row["max_tokens"],
        tone=row["tone"],
        audience=row["audience"],
        target_duration_s=row["target_duration_s"],
        computed_word_count=row["computed_word_count"],
        computed_duration_s=row["computed_duration_s"],
        warnings_json=row["warnings_json"],
        requires_evidence_review=bool(row["requires_evidence_review"]),
        ai_call_id=row["ai_call_id"],
        error_message=row["error_message"],
        superseded_at=row["superseded_at"],
        superseded_by_run_id=row["superseded_by_run_id"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        created_at=row["created_at"],
    )


def _row_to_citation(row: sqlite3.Row) -> ScriptCitation:
    return ScriptCitation(
        id=row["id"],
        script_id=row["script_id"],
        claim_id=row["claim_id"],
        section_index=row["section_index"],
        citation_order=row["citation_order"],
        created_at=row["created_at"],
    )


# ---------------------------------------------------------------------------
# Generation-run creation and retrieval
# ---------------------------------------------------------------------------


def create_generation_run(
    conn: sqlite3.Connection,
    run: ScriptGenerationRun,
) -> ScriptGenerationRun:
    """Insert a new 'running' generation run and return it with its assigned id."""
    now = _now()
    cur = conn.execute(
        """
        INSERT INTO script_generation_runs
            (topic_id, script_id, status, input_hash, evidence_hash, prompt_hash,
             prompt_name, prompt_version, model, temperature, max_tokens,
             tone, audience, target_duration_s, started_at, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            run.topic_id,
            run.script_id,
            run.status.value,
            run.input_hash,
            run.evidence_hash,
            run.prompt_hash,
            run.prompt_name,
            run.prompt_version,
            run.model,
            run.temperature,
            run.max_tokens,
            run.tone,
            run.audience,
            run.target_duration_s,
            run.started_at or now,
            now,
        ),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM script_generation_runs WHERE id=?", (cur.lastrowid,)
    ).fetchone()
    return _row_to_run(row)


def get_generation_run(
    conn: sqlite3.Connection, run_id: int
) -> ScriptGenerationRun | None:
    row = conn.execute(
        "SELECT * FROM script_generation_runs WHERE id=?", (run_id,)
    ).fetchone()
    return _row_to_run(row) if row else None


def list_generation_runs(
    conn: sqlite3.Connection, topic_id: int
) -> list[ScriptGenerationRun]:
    rows = conn.execute(
        "SELECT * FROM script_generation_runs WHERE topic_id=? ORDER BY created_at DESC, id DESC",
        (topic_id,),
    ).fetchall()
    return [_row_to_run(r) for r in rows]


def find_completed_run_by_input_hash(
    conn: sqlite3.Connection, topic_id: int, input_hash: str
) -> ScriptGenerationRun | None:
    """Idempotency lookup: return an existing completed run with matching input_hash."""
    row = conn.execute(
        """
        SELECT * FROM script_generation_runs
         WHERE topic_id=?
           AND input_hash=?
           AND status='completed'
           AND superseded_at IS NULL
         ORDER BY created_at DESC, id DESC
         LIMIT 1
        """,
        (topic_id, input_hash),
    ).fetchone()
    return _row_to_run(row) if row else None


# ---------------------------------------------------------------------------
# Atomic finalization
# ---------------------------------------------------------------------------


def finalize_generation_run(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    topic_id: int,
    script_body: str,
    script_body_json: str,
    script_version: int,
    computed_word_count: int,
    computed_duration_s: int,
    warnings: list[str],
    requires_evidence_review: bool,
    pending_citations: list[ScriptCitation],
    ai_call_id: int | None,
    replace_run_id: int | None = None,
) -> tuple[int, int]:
    """Atomically insert Script + citations, complete the run, optionally supersede prior run.

    Returns (script_id, run_id).

    SAVEPOINT order (invariants 19, 22–23):
      1. INSERT script
      2. INSERT citations
      3. UPDATE run to completed
      4. If replace_run_id given: supersede prior run (non-destructive)
      5. RELEASE — commit once

    On any failure: ROLLBACK + RELEASE, leaving prior approved Scripts untouched.
    No auto-committing call (record_ai_call) may be made inside this SAVEPOINT.
    """
    now = _now()
    warnings_json = json.dumps(warnings)

    conn.execute("SAVEPOINT finalize_run")
    try:
        # 1. Insert script
        cur = conn.execute(
            """
            INSERT INTO scripts
                (topic_id, version, body, body_json, format, status, created_at, updated_at)
            VALUES (?,?,?,?,'short','draft',?,?)
            """,
            (topic_id, script_version, script_body, script_body_json, now, now),
        )
        script_id = cur.lastrowid

        # 2. Insert citations
        for cit in pending_citations:
            conn.execute(
                """
                INSERT INTO script_citations
                    (script_id, claim_id, section_index, citation_order, created_at)
                VALUES (?,?,?,?,?)
                """,
                (script_id, cit.claim_id, cit.section_index, cit.citation_order, now),
            )

        # 3. Complete the run
        conn.execute(
            """
            UPDATE script_generation_runs
               SET status='completed',
                   script_id=?,
                   computed_word_count=?,
                   computed_duration_s=?,
                   warnings_json=?,
                   requires_evidence_review=?,
                   ai_call_id=?,
                   completed_at=?
             WHERE id=?
            """,
            (
                script_id,
                computed_word_count,
                computed_duration_s,
                warnings_json,
                int(requires_evidence_review),
                ai_call_id,
                now,
                run_id,
            ),
        )

        # 4. Supersede prior run (non-destructive — does not touch approved Scripts)
        if replace_run_id is not None:
            conn.execute(
                """
                UPDATE script_generation_runs
                   SET superseded_at=?, superseded_by_run_id=?
                 WHERE id=?
                """,
                (now, run_id, replace_run_id),
            )

        conn.execute("RELEASE finalize_run")
    except Exception:
        conn.execute("ROLLBACK TO finalize_run")
        conn.execute("RELEASE finalize_run")
        raise

    conn.commit()
    return script_id, run_id  # type: ignore[return-value]


def fail_generation_run(
    conn: sqlite3.Connection,
    run_id: int,
    error_message: str,
    ai_call_id: int | None = None,
) -> None:
    """Mark a run as failed. Leaves all Scripts and approved Scripts untouched (invariant 23)."""
    now = _now()
    conn.execute(
        """
        UPDATE script_generation_runs
           SET status='failed', error_message=?, ai_call_id=?, completed_at=?
         WHERE id=?
        """,
        (error_message, ai_call_id, now, run_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Citation retrieval
# ---------------------------------------------------------------------------


def list_citations(conn: sqlite3.Connection, script_id: int) -> list[ScriptCitation]:
    """Return citations ordered by section_index, then citation_order."""
    rows = conn.execute(
        """
        SELECT * FROM script_citations
         WHERE script_id=?
         ORDER BY section_index, citation_order, id
        """,
        (script_id,),
    ).fetchall()
    return [_row_to_citation(r) for r in rows]


# ---------------------------------------------------------------------------
# Phase 6 handoff (invariants 24–25)
# ---------------------------------------------------------------------------


def get_active_approved_generated_script(
    conn: sqlite3.Connection, topic_id: int
) -> ApprovedScript | None:
    """Return a structured ApprovedScript for Phase 6, or None if no approved script exists.

    Query 1: find active approved Script.
    Query 2: find the most recent completed generation run linked to that script.

    Raises:
        UnstructuredApprovedScriptError: if the active approved Script has body_json=NULL
            (i.e. it was created manually via `ace scripts add`).
        MalformedBodyJsonError: if body_json is non-NULL but cannot be parsed.
    """
    script_row = conn.execute(
        """
        SELECT * FROM scripts
         WHERE topic_id=?
           AND status='approved'
           AND superseded_at IS NULL
         ORDER BY approved_at DESC, id DESC
         LIMIT 1
        """,
        (topic_id,),
    ).fetchone()

    if script_row is None:
        return None

    if script_row["body_json"] is None:
        raise UnstructuredApprovedScriptError(
            f"Script {script_row['id']} for topic {topic_id} has body_json=NULL "
            f"(manual script) — Phase 6 requires a generated script with body_json"
        )

    try:
        body_json_obj = GeneratedScript.model_validate_json(script_row["body_json"])
    except Exception as exc:
        raise MalformedBodyJsonError(
            f"Script {script_row['id']} body_json cannot be parsed: {exc}"
        ) from exc

    run_row = conn.execute(
        """
        SELECT * FROM script_generation_runs
         WHERE script_id=?
           AND status='completed'
         ORDER BY created_at DESC, id DESC
         LIMIT 1
        """,
        (script_row["id"],),
    ).fetchone()

    citations = list_citations(conn, script_row["id"])

    return ApprovedScript.from_row_and_run(
        script_row=script_row,
        body_json=body_json_obj,
        body=script_row["body"],
        citations=citations,
        run_row=run_row,
    )
