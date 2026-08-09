"""Phase 6 M6.3A: Caption repository — all DB reads and writes.

All multi-table writes use a SAVEPOINT so that any failure leaves the
database in a consistent state.

SAVEPOINTs used:
  create_cr         — create_caption_run (single-table, guards UNIQUE constraint)
  persist_cues      — persist_caption_cues (bulk cue insert)
  complete_cr       — complete_caption_run (status + exports)
  fail_cr           — fail_caption_run
  approve_cr        — approve_caption_run (supersedes prior, inserts event)
  reject_cr         — reject_caption_run
  record_cre        — record_caption_review_event (insert-only)
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from app.captions.constants import (
    CAPTION_REJECTION_REASON_CODE_REQUIRING_NOTES,
    CAPTION_REJECTION_REASON_CODES,
    CAPTION_SEVERITY_MAX,
    CAPTION_SEVERITY_MIN,
)
from app.captions.errors import (
    CueRejectionBlocksApprovalError,
    DuplicateCaptionInputHashError,
    IllegalCaptionTransitionError,
    IncompleteCaptionRunError,
    InvalidCaptionReasonCodeError,
    InvalidCaptionSeverityError,
    NoCaptionRunError,
    SupersededCaptionRunError,
)
from app.captions.models import (
    CaptionCue,
    CaptionCueDraft,
    CaptionReviewEvent,
    CaptionRun,
    CaptionRunDraft,
)

_NOW = lambda: datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")  # noqa: E731


# ── Internal helpers ──────────────────────────────────────────────────────────


def _validate_severity(severity: int | None) -> None:
    if severity is None:
        return
    if not (isinstance(severity, int) and CAPTION_SEVERITY_MIN <= severity <= CAPTION_SEVERITY_MAX):
        raise InvalidCaptionSeverityError(
            f"Severity must be {CAPTION_SEVERITY_MIN}–{CAPTION_SEVERITY_MAX}, got {severity!r}"
        )


def _get_narration_run_context(
    conn: sqlite3.Connection, narration_run_id: int
) -> tuple[int, int, int, str | None, int, str, str, str]:
    """Return (plan_id, script_id, topic_id, experiment_id, voice_profile_id,
    provider, model, voice_id) for a narration run."""
    row = conn.execute(
        """
        SELECT nr.plan_id, pp.script_id, pp.topic_id, nr.experiment_id,
               nr.voice_profile_id, vp.provider, vp.model, vp.voice_id
        FROM narration_runs nr
        JOIN production_plans pp ON pp.id = nr.plan_id
        JOIN voice_profiles vp ON vp.id = nr.voice_profile_id
        WHERE nr.id = ?
        """,
        (narration_run_id,),
    ).fetchone()
    return row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7]


# ── Caption runs ──────────────────────────────────────────────────────────────


def create_caption_run(conn: sqlite3.Connection, draft: CaptionRunDraft) -> CaptionRun:
    now = _NOW()
    conn.execute("SAVEPOINT create_cr")
    try:
        cur = conn.execute(
            """
            INSERT INTO caption_runs
              (narration_run_id, plan_id, script_id, topic_id, experiment_id,
               input_hash, caption_schema_version, segmentation_version,
               timing_algorithm_version, style_version, exporter_version, language,
               status, total_cue_count, total_duration_ms,
               created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', 0, 0, ?, ?)
            """,
            (
                draft.narration_run_id,
                draft.plan_id,
                draft.script_id,
                draft.topic_id,
                draft.experiment_id,
                draft.input_hash,
                draft.caption_schema_version,
                draft.segmentation_version,
                draft.timing_algorithm_version,
                draft.style_version,
                draft.exporter_version,
                draft.language,
                now,
                now,
            ),
        )
        conn.execute("RELEASE create_cr")
    except sqlite3.IntegrityError as exc:
        conn.execute("ROLLBACK TO create_cr")
        conn.execute("RELEASE create_cr")
        if "UNIQUE" in str(exc):
            raise DuplicateCaptionInputHashError(
                f"Caption run with same input_hash already exists for narration run "
                f"{draft.narration_run_id}"
            ) from exc
        raise
    except Exception:
        conn.execute("ROLLBACK TO create_cr")
        conn.execute("RELEASE create_cr")
        raise
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM caption_runs WHERE id = ?", (cur.lastrowid,)).fetchone()
    return CaptionRun.from_row(row)


def get_caption_run(conn: sqlite3.Connection, run_id: int) -> CaptionRun | None:
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM caption_runs WHERE id = ?", (run_id,)).fetchone()
    return CaptionRun.from_row(row) if row else None


def require_caption_run(conn: sqlite3.Connection, run_id: int) -> CaptionRun:
    run = get_caption_run(conn, run_id)
    if run is None:
        raise NoCaptionRunError(f"Caption run {run_id} not found")
    return run


def find_caption_run_by_input_hash(
    conn: sqlite3.Connection, narration_run_id: int, input_hash: str
) -> CaptionRun | None:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM caption_runs WHERE narration_run_id = ? AND input_hash = ?",
        (narration_run_id, input_hash),
    ).fetchone()
    return CaptionRun.from_row(row) if row else None


def get_active_approved_caption_run(
    conn: sqlite3.Connection, narration_run_id: int, *, experiment_id: str | None = None
) -> CaptionRun | None:
    conn.row_factory = sqlite3.Row
    if experiment_id is None:
        row = conn.execute(
            "SELECT * FROM caption_runs WHERE narration_run_id = ? "
            "AND status = 'approved' AND superseded_at IS NULL AND experiment_id IS NULL",
            (narration_run_id,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM caption_runs WHERE narration_run_id = ? "
            "AND status = 'approved' AND superseded_at IS NULL AND experiment_id = ?",
            (narration_run_id, experiment_id),
        ).fetchone()
    return CaptionRun.from_row(row) if row else None


def list_caption_runs(conn: sqlite3.Connection, narration_run_id: int) -> list[CaptionRun]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM caption_runs WHERE narration_run_id = ? ORDER BY id DESC",
        (narration_run_id,),
    ).fetchall()
    return [CaptionRun.from_row(r) for r in rows]


def list_caption_runs_by_plan(conn: sqlite3.Connection, plan_id: int) -> list[CaptionRun]:
    """Return all caption runs for a plan (across all narration runs), newest first."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM caption_runs WHERE plan_id = ? ORDER BY id DESC",
        (plan_id,),
    ).fetchall()
    return [CaptionRun.from_row(r) for r in rows]


def update_caption_run_export_metadata(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    srt_path: str | None = None,
    vtt_path: str | None = None,
    json_path: str | None = None,
    srt_sha256: str | None = None,
    vtt_sha256: str | None = None,
    json_sha256: str | None = None,
) -> CaptionRun:
    """Update export path/hash metadata on a completed or approved caption run.

    Used by the export command to repair or regenerate export metadata.
    Never alters cues, status, or provenance.
    """
    now = _NOW()
    conn.execute("SAVEPOINT update_cr_exports")
    try:
        run = require_caption_run(conn, run_id)
        if run.status not in ("completed", "approved"):
            raise IllegalCaptionTransitionError(
                f"Cannot update exports for caption run {run_id}: "
                f"status is '{run.status}' (must be 'completed' or 'approved')"
            )
        sets = []
        values: list = []
        if srt_path is not None:
            sets.append("srt_path=?")
            values.append(srt_path)
        if vtt_path is not None:
            sets.append("vtt_path=?")
            values.append(vtt_path)
        if json_path is not None:
            sets.append("json_path=?")
            values.append(json_path)
        if srt_sha256 is not None:
            sets.append("srt_sha256=?")
            values.append(srt_sha256)
        if vtt_sha256 is not None:
            sets.append("vtt_sha256=?")
            values.append(vtt_sha256)
        if json_sha256 is not None:
            sets.append("json_sha256=?")
            values.append(json_sha256)
        if sets:
            sets.append("updated_at=?")
            values.append(now)
            values.append(run_id)
            conn.execute(
                f"UPDATE caption_runs SET {', '.join(sets)} WHERE id=?",  # noqa: S608
                values,
            )
        conn.execute("RELEASE update_cr_exports")
    except Exception:
        conn.execute("ROLLBACK TO update_cr_exports")
        conn.execute("RELEASE update_cr_exports")
        raise
    return require_caption_run(conn, run_id)


# ── Caption cues ──────────────────────────────────────────────────────────────


def persist_caption_cues(
    conn: sqlite3.Connection, run_id: int, cues: list[CaptionCueDraft]
) -> list[CaptionCue]:
    """Insert all cues for a run in a single SAVEPOINT.  Immutable once written."""
    now = _NOW()
    conn.execute("SAVEPOINT persist_cues")
    try:
        ids = []
        for cue in cues:
            cur = conn.execute(
                """
                INSERT INTO caption_cues
                  (run_id, segment_id, narration_asset_id, narration_text_hash, audio_sha256,
                   cue_index, segment_cue_index, text, start_ms, end_ms,
                   line_count, char_count, timing_source, warnings_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    cue.segment_id,
                    cue.narration_asset_id,
                    cue.narration_text_hash,
                    cue.audio_sha256,
                    cue.cue_index,
                    cue.segment_cue_index,
                    cue.text,
                    cue.start_ms,
                    cue.end_ms,
                    cue.line_count,
                    cue.char_count,
                    cue.timing_source,
                    json.dumps(cue.warnings, separators=(",", ":")),
                    now,
                ),
            )
            ids.append(cur.lastrowid)
        conn.execute("RELEASE persist_cues")
    except Exception:
        conn.execute("ROLLBACK TO persist_cues")
        conn.execute("RELEASE persist_cues")
        raise
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        f"SELECT * FROM caption_cues WHERE id IN ({','.join('?' * len(ids))}) ORDER BY cue_index",
        ids,
    ).fetchall()
    return [CaptionCue.from_row(r) for r in rows]


def get_caption_cues(conn: sqlite3.Connection, run_id: int) -> list[CaptionCue]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM caption_cues WHERE run_id = ? ORDER BY cue_index", (run_id,)
    ).fetchall()
    return [CaptionCue.from_row(r) for r in rows]


# ── Lifecycle transitions ─────────────────────────────────────────────────────


def complete_caption_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    total_cue_count: int,
    total_duration_ms: int,
    srt_path: str,
    vtt_path: str,
    json_path: str,
    srt_sha256: str,
    vtt_sha256: str,
    json_sha256: str,
) -> CaptionRun:
    now = _NOW()
    conn.execute("SAVEPOINT complete_cr")
    try:
        run = require_caption_run(conn, run_id)
        if run.status != "running":
            raise IllegalCaptionTransitionError(
                f"Cannot complete caption run {run_id}: status is '{run.status}'"
                " (must be 'running')"
            )
        conn.execute(
            """
            UPDATE caption_runs SET
              status='completed', total_cue_count=?, total_duration_ms=?,
              srt_path=?, vtt_path=?, json_path=?,
              srt_sha256=?, vtt_sha256=?, json_sha256=?,
              updated_at=?
            WHERE id=?
            """,
            (
                total_cue_count,
                total_duration_ms,
                srt_path,
                vtt_path,
                json_path,
                srt_sha256,
                vtt_sha256,
                json_sha256,
                now,
                run_id,
            ),
        )
        conn.execute("RELEASE complete_cr")
    except Exception:
        conn.execute("ROLLBACK TO complete_cr")
        conn.execute("RELEASE complete_cr")
        raise
    return require_caption_run(conn, run_id)


def fail_caption_run(conn: sqlite3.Connection, run_id: int, *, failure_reason: str) -> CaptionRun:
    now = _NOW()
    conn.execute("SAVEPOINT fail_cr")
    try:
        run = require_caption_run(conn, run_id)
        if run.status != "running":
            raise IllegalCaptionTransitionError(
                f"Cannot fail caption run {run_id}: status is '{run.status}' (must be 'running')"
            )
        conn.execute(
            "UPDATE caption_runs SET status='failed', failure_reason=?, updated_at=? WHERE id=?",
            (failure_reason, now, run_id),
        )
        conn.execute("RELEASE fail_cr")
    except Exception:
        conn.execute("ROLLBACK TO fail_cr")
        conn.execute("RELEASE fail_cr")
        raise
    return require_caption_run(conn, run_id)


def approve_caption_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    actor: str | None = None,
    notes: str | None = None,
) -> CaptionRun:
    now = _NOW()
    conn.execute("SAVEPOINT approve_cr")
    try:
        run = require_caption_run(conn, run_id)
        if run.status != "completed":
            raise IllegalCaptionTransitionError(
                f"Cannot approve caption run {run_id}: status is '{run.status}'"
                " (must be 'completed')"
            )
        if run.superseded_at is not None:
            raise SupersededCaptionRunError(
                f"Caption run {run_id} has been superseded and cannot be approved"
            )
        # Check for unresolved cue rejections
        rejected_count = conn.execute(
            "SELECT COUNT(*) FROM caption_review_events "
            "WHERE run_id = ? AND event_type = 'cue_rejected'",
            (run_id,),
        ).fetchone()[0]
        if rejected_count > 0:
            raise CueRejectionBlocksApprovalError(
                f"Caption run {run_id} has {rejected_count} cue rejection(s) — "
                "resolve them before approval"
            )
        if run.total_cue_count == 0:
            raise IncompleteCaptionRunError(
                f"Caption run {run_id} has zero cues and cannot be approved"
            )
        (
            plan_id,
            script_id,
            topic_id,
            experiment_id,
            voice_profile_id,
            provider,
            model,
            voice_id,
        ) = _get_narration_run_context(conn, run.narration_run_id)
        # Supersede the prior active approved run
        if run.experiment_id is None:
            conn.execute(
                "UPDATE caption_runs SET superseded_at=?, superseded_by_run_id=?, updated_at=? "
                "WHERE narration_run_id=? AND status='approved' "
                "AND superseded_at IS NULL AND experiment_id IS NULL AND id!=?",
                (now, run_id, now, run.narration_run_id, run_id),
            )
        else:
            conn.execute(
                "UPDATE caption_runs SET superseded_at=?, superseded_by_run_id=?, updated_at=? "
                "WHERE narration_run_id=? AND status='approved' "
                "AND superseded_at IS NULL AND experiment_id=? AND id!=?",
                (now, run_id, now, run.narration_run_id, run.experiment_id, run_id),
            )
        conn.execute(
            "UPDATE caption_runs SET status='approved', approved_at=?, updated_at=? WHERE id=?",
            (now, now, run_id),
        )
        conn.execute(
            """
            INSERT INTO caption_review_events
              (run_id, narration_run_id, plan_id, script_id, topic_id, voice_profile_id,
               provider, model, voice_id, experiment_id,
               caption_schema_version, segmentation_version, timing_algorithm_version,
               style_version, exporter_version,
               event_type, notes, actor, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'run_approved', ?, ?, ?)
            """,
            (
                run_id,
                run.narration_run_id,
                plan_id,
                script_id,
                topic_id,
                voice_profile_id,
                provider,
                model,
                voice_id,
                run.experiment_id,
                run.caption_schema_version,
                run.segmentation_version,
                run.timing_algorithm_version,
                run.style_version,
                run.exporter_version,
                notes,
                actor,
                now,
            ),
        )
        conn.execute("RELEASE approve_cr")
    except Exception:
        conn.execute("ROLLBACK TO approve_cr")
        conn.execute("RELEASE approve_cr")
        raise
    return require_caption_run(conn, run_id)


def reject_caption_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    reason_code: str | None = None,
    notes: str | None = None,
    severity: int | None = None,
    actor: str | None = None,
    expected_correction: str | None = None,
) -> CaptionRun:
    _validate_severity(severity)
    now = _NOW()
    conn.execute("SAVEPOINT reject_cr")
    try:
        run = require_caption_run(conn, run_id)
        if run.status not in ("completed", "approved"):
            raise IllegalCaptionTransitionError(
                f"Cannot reject caption run {run_id}: status is '{run.status}'"
            )
        (
            plan_id,
            script_id,
            topic_id,
            experiment_id,
            voice_profile_id,
            provider,
            model,
            voice_id,
        ) = _get_narration_run_context(conn, run.narration_run_id)
        conn.execute(
            "UPDATE caption_runs SET status='rejected', rejected_at=?, updated_at=? WHERE id=?",
            (now, now, run_id),
        )
        conn.execute(
            """
            INSERT INTO caption_review_events
              (run_id, narration_run_id, plan_id, script_id, topic_id, voice_profile_id,
               provider, model, voice_id, experiment_id,
               caption_schema_version, segmentation_version, timing_algorithm_version,
               style_version, exporter_version,
               event_type, reason_code, severity, expected_correction, notes, actor, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'run_rejected',
                    ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                run.narration_run_id,
                plan_id,
                script_id,
                topic_id,
                voice_profile_id,
                provider,
                model,
                voice_id,
                run.experiment_id,
                run.caption_schema_version,
                run.segmentation_version,
                run.timing_algorithm_version,
                run.style_version,
                run.exporter_version,
                reason_code,
                severity,
                expected_correction,
                notes,
                actor,
                now,
            ),
        )
        conn.execute("RELEASE reject_cr")
    except Exception:
        conn.execute("ROLLBACK TO reject_cr")
        conn.execute("RELEASE reject_cr")
        raise
    return require_caption_run(conn, run_id)


# ── Review events (insert-only) ───────────────────────────────────────────────


def record_cue_rejection(
    conn: sqlite3.Connection,
    run_id: int,
    cue_id: int,
    *,
    reason_code: str,
    severity: int | None = None,
    notes: str | None = None,
    actor: str | None = None,
    expected_correction: str | None = None,
) -> CaptionReviewEvent:
    if reason_code not in CAPTION_REJECTION_REASON_CODES:
        raise InvalidCaptionReasonCodeError(f"Unknown caption reason code: {reason_code!r}")
    if reason_code == CAPTION_REJECTION_REASON_CODE_REQUIRING_NOTES and not notes:
        raise InvalidCaptionReasonCodeError(f"Reason code {reason_code!r} requires notes")
    _validate_severity(severity)
    now = _NOW()
    conn.execute("SAVEPOINT record_cre")
    try:
        run = require_caption_run(conn, run_id)
        cue_row = conn.execute(
            "SELECT segment_id, narration_asset_id FROM caption_cues WHERE id = ? AND run_id = ?",
            (cue_id, run_id),
        ).fetchone()
        if cue_row is None:
            raise NoCaptionRunError(f"Caption cue {cue_id} not found in run {run_id}")
        segment_id, asset_id = cue_row[0], cue_row[1]
        (
            plan_id,
            script_id,
            topic_id,
            experiment_id,
            voice_profile_id,
            provider,
            model,
            voice_id,
        ) = _get_narration_run_context(conn, run.narration_run_id)
        cur = conn.execute(
            """
            INSERT INTO caption_review_events
              (run_id, cue_id, segment_id, narration_asset_id, narration_run_id,
               plan_id, script_id, topic_id, voice_profile_id,
               provider, model, voice_id, experiment_id,
               caption_schema_version, segmentation_version, timing_algorithm_version,
               style_version, exporter_version,
               event_type, reason_code, severity, expected_correction, notes, actor, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    'cue_rejected', ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                cue_id,
                segment_id,
                asset_id,
                run.narration_run_id,
                plan_id,
                script_id,
                topic_id,
                voice_profile_id,
                provider,
                model,
                voice_id,
                run.experiment_id,
                run.caption_schema_version,
                run.segmentation_version,
                run.timing_algorithm_version,
                run.style_version,
                run.exporter_version,
                reason_code,
                severity,
                expected_correction,
                notes,
                actor,
                now,
            ),
        )
        conn.execute("RELEASE record_cre")
    except Exception:
        conn.execute("ROLLBACK TO record_cre")
        conn.execute("RELEASE record_cre")
        raise
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM caption_review_events WHERE id = ?", (cur.lastrowid,)
    ).fetchone()
    return CaptionReviewEvent.from_row(row)


def list_caption_review_events(conn: sqlite3.Connection, run_id: int) -> list[CaptionReviewEvent]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM caption_review_events WHERE run_id = ? ORDER BY created_at, id",
        (run_id,),
    ).fetchall()
    return [CaptionReviewEvent.from_row(r) for r in rows]
