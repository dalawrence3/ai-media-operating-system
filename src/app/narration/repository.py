"""Phase 6 M6.2: Narration repository — all DB reads and writes.

All multi-table writes use a SAVEPOINT so that any failure leaves the
database in a consistent state.  record_tts_call() auto-commits and must
be called OUTSIDE any SAVEPOINT (same pattern as record_ai_call()).

SAVEPOINTs used:
  create_vp          — create_voice_profile (single-table, guards is_default)
  approve_nr         — approve_narration_run
  reject_nr          — reject_narration_run
  finalize_nsa       — finalize_narration_segment_asset
  reject_nsa         — reject_narration_segment_asset
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from app.narration.constants import (
    NARRATION_ALGORITHM_VERSION,
    NARRATION_REJECTION_REASON_CODE_REQUIRING_NOTES,
    NARRATION_REJECTION_REASON_CODES,
    NARRATION_SCHEMA_VERSION,
    NARRATION_SEVERITY_MAX,
    NARRATION_SEVERITY_MIN,
)
from app.narration.errors import (
    DuplicateNarrationInputHashError,
    IllegalNarrationTransitionError,
    IllegalSegmentTransitionError,
    InvalidNarrationReasonCodeError,
    InvalidNarrationSeverityError,
    NoNarrationRunError,
    NoSegmentAssetError,
    NoVoiceProfileError,
    PendingSegmentsError,
    RejectedSegmentsError,
)
from app.narration.models import (
    ApprovedNarrationRun,
    ApprovedNarrationSegment,
    NarrationReviewEvent,
    NarrationRun,
    NarrationRunDraft,
    NarrationSegmentAsset,
    NarrationSegmentAssetDraft,
    VoiceProfile,
    VoiceProfileCreate,
)

_NOW = lambda: datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")  # noqa: E731


# ── Internal helpers ──────────────────────────────────────────────────────────


def _validate_severity(severity: int | None) -> None:
    """Raise InvalidNarrationSeverityError for out-of-range severity values."""
    if severity is None:
        return
    valid = (
        isinstance(severity, int)
        and NARRATION_SEVERITY_MIN <= severity <= NARRATION_SEVERITY_MAX
    )
    if not valid:
        raise InvalidNarrationSeverityError(
            f"Severity must be an integer between {NARRATION_SEVERITY_MIN} and "
            f"{NARRATION_SEVERITY_MAX}, got {severity!r}"
        )


def _get_plan_script_topic(conn: sqlite3.Connection, plan_id: int) -> tuple[int, int]:
    """Return (script_id, topic_id) for a production plan."""
    row = conn.execute(
        "SELECT script_id, topic_id FROM production_plans WHERE id = ?", (plan_id,)
    ).fetchone()
    return row[0], row[1]


def _get_voice_context(conn: sqlite3.Connection, voice_profile_id: int) -> tuple[str, str, str]:
    """Return (provider, model, voice_id) for a voice profile."""
    row = conn.execute(
        "SELECT provider, model, voice_id FROM voice_profiles WHERE id = ?",
        (voice_profile_id,),
    ).fetchone()
    return row[0], row[1], row[2]


def _get_run_plan_experiment(conn: sqlite3.Connection, run_id: int) -> tuple[int, str | None]:
    """Return (plan_id, experiment_id) for a narration run."""
    row = conn.execute(
        "SELECT plan_id, experiment_id FROM narration_runs WHERE id = ?", (run_id,)
    ).fetchone()
    return row[0], row[1]


# ── Voice profiles ────────────────────────────────────────────────────────────


def create_voice_profile(conn: sqlite3.Connection, vpc: VoiceProfileCreate) -> VoiceProfile:
    now = _NOW()
    conn.execute("SAVEPOINT create_vp")
    try:
        if vpc.is_default:
            if vpc.channel_id is not None:
                conn.execute(
                    "UPDATE voice_profiles SET is_default = 0, updated_at = ? "
                    "WHERE channel_id = ? AND is_default = 1",
                    (now, vpc.channel_id),
                )
            else:
                conn.execute(
                    "UPDATE voice_profiles SET is_default = 0, updated_at = ? "
                    "WHERE channel_id IS NULL AND is_default = 1",
                    (now,),
                )
        cur = conn.execute(
            """
            INSERT INTO voice_profiles
              (channel_id, provider, model, voice_id, name, language,
               speaking_rate, style, stability, similarity_boost,
               settings_json, version, is_default, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                vpc.channel_id, vpc.provider, vpc.model, vpc.voice_id, vpc.name,
                vpc.language, vpc.speaking_rate, vpc.style, vpc.stability,
                vpc.similarity_boost, vpc.settings_json, vpc.version,
                1 if vpc.is_default else 0, now, now,
            ),
        )
        vp_id = cur.lastrowid
        conn.execute("RELEASE create_vp")
    except Exception:
        conn.execute("ROLLBACK TO create_vp")
        conn.execute("RELEASE create_vp")
        raise
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM voice_profiles WHERE id = ?", (vp_id,)).fetchone()
    return VoiceProfile.from_row(row)


def get_voice_profile(
    conn: sqlite3.Connection, profile_id: int
) -> VoiceProfile | None:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM voice_profiles WHERE id = ?", (profile_id,)
    ).fetchone()
    return VoiceProfile.from_row(row) if row else None


def require_voice_profile(conn: sqlite3.Connection, profile_id: int) -> VoiceProfile:
    vp = get_voice_profile(conn, profile_id)
    if vp is None:
        raise NoVoiceProfileError(f"Voice profile {profile_id} not found")
    return vp


def list_voice_profiles(
    conn: sqlite3.Connection,
    *,
    channel_id: int | None = None,
    provider: str | None = None,
) -> list[VoiceProfile]:
    conn.row_factory = sqlite3.Row
    query = "SELECT * FROM voice_profiles WHERE 1=1"
    params: list = []
    if channel_id is not None:
        query += " AND channel_id = ?"
        params.append(channel_id)
    if provider is not None:
        query += " AND provider = ?"
        params.append(provider)
    query += " ORDER BY id"
    rows = conn.execute(query, params).fetchall()
    return [VoiceProfile.from_row(r) for r in rows]


# ── Narration runs ────────────────────────────────────────────────────────────


def create_narration_run(
    conn: sqlite3.Connection, draft: NarrationRunDraft
) -> NarrationRun:
    now = _NOW()
    try:
        cur = conn.execute(
            """
            INSERT INTO narration_runs
              (plan_id, plan_input_hash, voice_profile_id, voice_profile_version,
               language, speaking_rate, style, stability, similarity_boost,
               settings_json, output_format, sample_rate_hz, input_hash,
               status, experiment_id, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?, ?, ?)
            """,
            (
                draft.plan_id, draft.plan_input_hash, draft.voice_profile_id,
                draft.voice_profile_version, draft.language, draft.speaking_rate,
                draft.style, draft.stability, draft.similarity_boost,
                draft.settings_json, draft.output_format, draft.sample_rate_hz,
                _build_run_input_hash(draft),
                draft.experiment_id, draft.notes, now, now,
            ),
        )
    except sqlite3.IntegrityError as exc:
        if "UNIQUE" in str(exc):
            raise DuplicateNarrationInputHashError(
                f"Narration run with same input_hash already exists for plan {draft.plan_id}"
            ) from exc
        raise
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM narration_runs WHERE id = ?", (cur.lastrowid,)
    ).fetchone()
    return NarrationRun.from_row(row)


def _build_run_input_hash(draft: NarrationRunDraft) -> str:
    from app.narration.hashing import compute_narration_run_input_hash, compute_settings_hash

    return compute_narration_run_input_hash(
        plan_id=draft.plan_id,
        plan_input_hash=draft.plan_input_hash,
        voice_profile_id=draft.voice_profile_id,
        voice_profile_version=draft.voice_profile_version,
        language=draft.language,
        speaking_rate=draft.speaking_rate,
        style=draft.style,
        stability=draft.stability,
        similarity_boost=draft.similarity_boost,
        settings_json_hash=compute_settings_hash(draft.settings_json),
        output_format=draft.output_format,
        sample_rate_hz=draft.sample_rate_hz,
        narration_schema_version=NARRATION_SCHEMA_VERSION,
        narration_algorithm_version=NARRATION_ALGORITHM_VERSION,
    )


def get_narration_run(
    conn: sqlite3.Connection, run_id: int
) -> NarrationRun | None:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM narration_runs WHERE id = ?", (run_id,)
    ).fetchone()
    return NarrationRun.from_row(row) if row else None


def require_narration_run(conn: sqlite3.Connection, run_id: int) -> NarrationRun:
    run = get_narration_run(conn, run_id)
    if run is None:
        raise NoNarrationRunError(f"Narration run {run_id} not found")
    return run


def find_narration_run_by_input_hash(
    conn: sqlite3.Connection, plan_id: int, input_hash: str
) -> NarrationRun | None:
    """Return a completed or approved run matching the given input hash, if any."""
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM narration_runs WHERE plan_id = ? AND input_hash = ? "
        "AND status IN ('completed', 'approved')",
        (plan_id, input_hash),
    ).fetchone()
    return NarrationRun.from_row(row) if row else None


def get_active_approved_narration_run(
    conn: sqlite3.Connection, plan_id: int, *, experiment_id: str | None = None
) -> NarrationRun | None:
    conn.row_factory = sqlite3.Row
    if experiment_id is None:
        row = conn.execute(
            "SELECT * FROM narration_runs WHERE plan_id = ? AND status = 'approved' "
            "AND superseded_at IS NULL AND experiment_id IS NULL",
            (plan_id,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM narration_runs WHERE plan_id = ? AND status = 'approved' "
            "AND superseded_at IS NULL AND experiment_id = ?",
            (plan_id, experiment_id),
        ).fetchone()
    return NarrationRun.from_row(row) if row else None


def list_narration_runs(
    conn: sqlite3.Connection, plan_id: int
) -> list[NarrationRun]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM narration_runs WHERE plan_id = ? ORDER BY id", (plan_id,)
    ).fetchall()
    return [NarrationRun.from_row(r) for r in rows]


def complete_narration_run(conn: sqlite3.Connection, run_id: int) -> NarrationRun:
    now = _NOW()
    run = require_narration_run(conn, run_id)
    if run.status != "running":
        raise IllegalNarrationTransitionError(
            f"Cannot complete run {run_id} with status '{run.status}'"
        )
    conn.execute(
        "UPDATE narration_runs SET status='completed', completed_at=?, updated_at=? WHERE id=?",
        (now, now, run_id),
    )
    return require_narration_run(conn, run_id)


def fail_narration_run(
    conn: sqlite3.Connection, run_id: int, *, error_message: str
) -> NarrationRun:
    now = _NOW()
    run = require_narration_run(conn, run_id)
    if run.status != "running":
        raise IllegalNarrationTransitionError(
            f"Cannot fail run {run_id} with status '{run.status}'"
        )
    conn.execute(
        "UPDATE narration_runs SET status='failed', error_message=?, updated_at=? WHERE id=?",
        (error_message, now, run_id),
    )
    return require_narration_run(conn, run_id)


def restart_failed_narration_run(conn: sqlite3.Connection, run_id: int) -> NarrationRun:
    now = _NOW()
    run = require_narration_run(conn, run_id)
    if run.status != "failed":
        raise IllegalNarrationTransitionError(
            f"Cannot restart run {run_id} with status '{run.status}'"
        )
    conn.execute(
        "UPDATE narration_runs SET status='running', error_message=NULL, updated_at=? WHERE id=?",
        (now, run_id),
    )
    return require_narration_run(conn, run_id)


def approve_narration_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    actor: str | None = None,
    notes: str | None = None,
) -> NarrationRun:
    now = _NOW()
    conn.execute("SAVEPOINT approve_nr")
    try:
        run = require_narration_run(conn, run_id)
        if run.status != "completed":
            raise IllegalNarrationTransitionError(
                f"Cannot approve run {run_id}: status is '{run.status}' (must be 'completed')"
            )
        _validate_run_for_approval(conn, run)
        script_id, topic_id = _get_plan_script_topic(conn, run.plan_id)
        vp_provider, vp_model, vp_voice_id = _get_voice_context(conn, run.voice_profile_id)
        # Supersede the prior active approved run: retain status='approved', set superseded_at.
        if run.experiment_id is None:
            conn.execute(
                "UPDATE narration_runs "
                "SET superseded_at=?, superseded_by_run_id=?, updated_at=? "
                "WHERE plan_id=? AND status='approved' AND superseded_at IS NULL "
                "AND experiment_id IS NULL AND id!=?",
                (now, run_id, now, run.plan_id, run_id),
            )
        else:
            conn.execute(
                "UPDATE narration_runs "
                "SET superseded_at=?, superseded_by_run_id=?, updated_at=? "
                "WHERE plan_id=? AND status='approved' AND superseded_at IS NULL "
                "AND experiment_id=? AND id!=?",
                (now, run_id, now, run.plan_id, run.experiment_id, run_id),
            )
        conn.execute(
            "UPDATE narration_runs SET status='approved', approved_at=?, updated_at=? WHERE id=?",
            (now, now, run_id),
        )
        conn.execute(
            "INSERT INTO narration_review_events "
            "(run_id, plan_id, script_id, topic_id, voice_profile_id, provider, model, "
            "voice_id, experiment_id, event_type, notes, actor, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'run_approved', ?, ?, ?)",
            (run_id, run.plan_id, script_id, topic_id, run.voice_profile_id,
             vp_provider, vp_model, vp_voice_id, run.experiment_id, notes, actor, now),
        )
        conn.execute("RELEASE approve_nr")
    except Exception:
        conn.execute("ROLLBACK TO approve_nr")
        conn.execute("RELEASE approve_nr")
        raise
    return require_narration_run(conn, run_id)


def _validate_run_for_approval(conn: sqlite3.Connection, run: NarrationRun) -> None:
    rejected_count = conn.execute(
        "SELECT COUNT(*) FROM narration_segment_assets "
        "WHERE run_id = ? AND status = 'rejected' AND superseded_at IS NULL",
        (run.id,),
    ).fetchone()[0]
    if rejected_count > 0:
        raise RejectedSegmentsError(
            f"Run {run.id} has {rejected_count} unresolved rejected segment(s)"
        )

    expected = {
        row[0]
        for row in conn.execute(
            "SELECT id FROM production_segments WHERE plan_id = ?", (run.plan_id,)
        ).fetchall()
    }
    covered = {
        row[0]
        for row in conn.execute(
            "SELECT segment_id FROM narration_segment_assets "
            "WHERE run_id = ? AND status = 'synthesized' AND superseded_at IS NULL",
            (run.id,),
        ).fetchall()
    }
    missing = expected - covered
    if missing:
        raise PendingSegmentsError(
            f"Run {run.id} is missing synthesized assets for segment(s): {sorted(missing)}"
        )


def reject_narration_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    reason_code: str | None = None,
    notes: str | None = None,
    severity: int | None = None,
    actor: str | None = None,
    expected_correction: str | None = None,
) -> NarrationRun:
    _validate_severity(severity)
    now = _NOW()
    conn.execute("SAVEPOINT reject_nr")
    try:
        run = require_narration_run(conn, run_id)
        if run.status not in ("completed", "approved"):
            raise IllegalNarrationTransitionError(
                f"Cannot reject run {run_id} with status '{run.status}'"
            )
        script_id, topic_id = _get_plan_script_topic(conn, run.plan_id)
        vp_provider, vp_model, vp_voice_id = _get_voice_context(conn, run.voice_profile_id)
        conn.execute(
            "UPDATE narration_runs SET status='rejected', rejected_at=?, updated_at=? WHERE id=?",
            (now, now, run_id),
        )
        conn.execute(
            "INSERT INTO narration_review_events "
            "(run_id, plan_id, script_id, topic_id, voice_profile_id, provider, model, "
            "voice_id, experiment_id, event_type, reason_code, severity, "
            "expected_correction, notes, actor, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'run_rejected', ?, ?, ?, ?, ?, ?)",
            (run_id, run.plan_id, script_id, topic_id, run.voice_profile_id,
             vp_provider, vp_model, vp_voice_id, run.experiment_id,
             reason_code, severity, expected_correction, notes, actor, now),
        )
        conn.execute("RELEASE reject_nr")
    except Exception:
        conn.execute("ROLLBACK TO reject_nr")
        conn.execute("RELEASE reject_nr")
        raise
    return require_narration_run(conn, run_id)


# ── Narration segment assets ──────────────────────────────────────────────────


def create_narration_segment_asset(
    conn: sqlite3.Connection, draft: NarrationSegmentAssetDraft
) -> NarrationSegmentAsset:
    now = _NOW()
    cur = conn.execute(
        """
        INSERT INTO narration_segment_assets
          (run_id, segment_id, narration_text_hash, provider, model, voice_id,
           voice_profile_id, voice_profile_version, language, speaking_rate,
           style, stability, similarity_boost, settings_json_hash, output_format,
           sample_rate_hz, input_hash, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
        """,
        (
            draft.run_id, draft.segment_id, draft.narration_text_hash,
            draft.provider, draft.model, draft.voice_id,
            draft.voice_profile_id, draft.voice_profile_version, draft.language,
            draft.speaking_rate, draft.style, draft.stability, draft.similarity_boost,
            draft.settings_json_hash, draft.output_format, draft.sample_rate_hz,
            draft.input_hash, now, now,
        ),
    )
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM narration_segment_assets WHERE id = ?", (cur.lastrowid,)
    ).fetchone()
    return NarrationSegmentAsset.from_row(row)


def get_narration_segment_asset(
    conn: sqlite3.Connection, asset_id: int
) -> NarrationSegmentAsset | None:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM narration_segment_assets WHERE id = ?", (asset_id,)
    ).fetchone()
    return NarrationSegmentAsset.from_row(row) if row else None


def require_narration_segment_asset(
    conn: sqlite3.Connection, asset_id: int
) -> NarrationSegmentAsset:
    asset = get_narration_segment_asset(conn, asset_id)
    if asset is None:
        raise NoSegmentAssetError(f"Segment asset {asset_id} not found")
    return asset


def get_active_segment_asset(
    conn: sqlite3.Connection, run_id: int, segment_id: int
) -> NarrationSegmentAsset | None:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM narration_segment_assets "
        "WHERE run_id = ? AND segment_id = ? "
        "AND status != 'rejected' AND superseded_at IS NULL",
        (run_id, segment_id),
    ).fetchone()
    return NarrationSegmentAsset.from_row(row) if row else None


def get_segment_assets_for_run(
    conn: sqlite3.Connection, run_id: int
) -> list[NarrationSegmentAsset]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM narration_segment_assets WHERE run_id = ? ORDER BY segment_id",
        (run_id,),
    ).fetchall()
    return [NarrationSegmentAsset.from_row(r) for r in rows]


def finalize_narration_segment_asset(
    conn: sqlite3.Connection,
    asset_id: int,
    *,
    audio_path: str,
    audio_sha256: str,
    duration_seconds: float,
    characters_billed: int,
    cost_usd: float,
    actor: str | None = None,
) -> NarrationSegmentAsset:
    now = _NOW()
    conn.execute("SAVEPOINT finalize_nsa")
    try:
        asset = require_narration_segment_asset(conn, asset_id)
        if asset.status != "pending":
            raise IllegalSegmentTransitionError(
                f"Cannot finalize segment asset {asset_id} with status '{asset.status}'"
            )
        superseded = conn.execute(
            "SELECT id FROM narration_segment_assets "
            "WHERE run_id = ? AND segment_id = ? AND status = 'rejected' AND superseded_at IS NULL",
            (asset.run_id, asset.segment_id),
        ).fetchone()
        if superseded:
            rejected_asset_id = superseded[0]
            plan_id, experiment_id = _get_run_plan_experiment(conn, asset.run_id)
            script_id, topic_id = _get_plan_script_topic(conn, plan_id)
            conn.execute(
                "UPDATE narration_segment_assets "
                "SET superseded_at = ?, updated_at = ? WHERE id = ?",
                (now, now, rejected_asset_id),
            )
            conn.execute(
                "INSERT INTO narration_review_events "
                "(run_id, plan_id, script_id, topic_id, voice_profile_id, provider, model, "
                "voice_id, experiment_id, segment_id, asset_id, replacement_asset_id, "
                "event_type, actor, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'segment_regenerated', ?, ?)",
                (asset.run_id, plan_id, script_id, topic_id, asset.voice_profile_id,
                 asset.provider, asset.model, asset.voice_id,
                 experiment_id, asset.segment_id, rejected_asset_id, asset_id,
                 actor, now),
            )
        conn.execute(
            "UPDATE narration_segment_assets SET "
            "status='synthesized', audio_path=?, audio_sha256=?, "
            "duration_seconds=?, characters_billed=?, cost_usd=?, "
            "updated_at=? WHERE id=?",
            (audio_path, audio_sha256, duration_seconds,
             characters_billed, cost_usd, now, asset_id),
        )
        conn.execute("RELEASE finalize_nsa")
    except Exception:
        conn.execute("ROLLBACK TO finalize_nsa")
        conn.execute("RELEASE finalize_nsa")
        raise
    return require_narration_segment_asset(conn, asset_id)


def reject_narration_segment_asset(
    conn: sqlite3.Connection,
    asset_id: int,
    *,
    reason_code: str,
    notes: str | None = None,
    severity: int | None = None,
    actor: str | None = None,
    expected_correction: str | None = None,
) -> NarrationSegmentAsset:
    if reason_code not in NARRATION_REJECTION_REASON_CODES:
        raise InvalidNarrationReasonCodeError(f"Unknown reason code: {reason_code!r}")
    if reason_code == NARRATION_REJECTION_REASON_CODE_REQUIRING_NOTES and not notes:
        raise InvalidNarrationReasonCodeError(
            f"Reason code {reason_code!r} requires notes to be provided"
        )
    _validate_severity(severity)
    now = _NOW()
    conn.execute("SAVEPOINT reject_nsa")
    try:
        asset = require_narration_segment_asset(conn, asset_id)
        if asset.status != "synthesized":
            raise IllegalSegmentTransitionError(
                f"Cannot reject segment asset {asset_id} with status '{asset.status}'"
            )
        plan_id, experiment_id = _get_run_plan_experiment(conn, asset.run_id)
        script_id, topic_id = _get_plan_script_topic(conn, plan_id)
        conn.execute(
            "UPDATE narration_segment_assets SET status='rejected', updated_at=? WHERE id=?",
            (now, asset_id),
        )
        conn.execute(
            "INSERT INTO narration_review_events "
            "(run_id, plan_id, script_id, topic_id, voice_profile_id, provider, model, "
            "voice_id, experiment_id, segment_id, asset_id, event_type, "
            "reason_code, severity, expected_correction, notes, actor, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'segment_rejected', "
            "?, ?, ?, ?, ?, ?)",
            (asset.run_id, plan_id, script_id, topic_id, asset.voice_profile_id,
             asset.provider, asset.model, asset.voice_id,
             experiment_id, asset.segment_id, asset_id,
             reason_code, severity, expected_correction, notes, actor, now),
        )
        conn.execute("RELEASE reject_nsa")
    except Exception:
        conn.execute("ROLLBACK TO reject_nsa")
        conn.execute("RELEASE reject_nsa")
        raise
    return require_narration_segment_asset(conn, asset_id)


# ── TTS calls (auto-commits — must be called OUTSIDE any SAVEPOINT) ───────────


def record_tts_call(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    segment_id: int | None,
    provider: str,
    model: str,
    voice_id: str,
    input_characters: int,
    characters_billed: int,
    output_format: str,
    sample_rate_hz: int,
    duration_seconds: float | None,
    cost_usd: float,
    success: bool,
    error_message: str | None = None,
    latency_ms: int | None = None,
    request_id: str | None = None,
    provider_metadata_json: str | None = None,
) -> int:
    now = _NOW()
    cur = conn.execute(
        """
        INSERT INTO tts_calls
          (run_id, segment_id, provider, model, voice_id, input_characters,
           characters_billed, output_format, sample_rate_hz, duration_seconds,
           cost_usd, success, error_message, latency_ms, request_id,
           provider_metadata_json, narration_schema_version, narration_algorithm_version,
           called_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id, segment_id, provider, model, voice_id, input_characters,
            characters_billed, output_format, sample_rate_hz, duration_seconds,
            cost_usd, 1 if success else 0, error_message, latency_ms, request_id,
            provider_metadata_json, NARRATION_SCHEMA_VERSION, NARRATION_ALGORITHM_VERSION,
            now,
        ),
    )
    conn.commit()
    return cur.lastrowid


# ── Review events ─────────────────────────────────────────────────────────────


def list_narration_review_events(
    conn: sqlite3.Connection, run_id: int
) -> list[NarrationReviewEvent]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM narration_review_events WHERE run_id = ? ORDER BY created_at, id",
        (run_id,),
    ).fetchall()
    return [NarrationReviewEvent.from_row(r) for r in rows]


# ── Caption handoff ───────────────────────────────────────────────────────────


def get_approved_narration_run_full(
    conn: sqlite3.Connection,
    plan_id: int,
    *,
    experiment_id: str | None = None,
) -> ApprovedNarrationRun | None:
    """Return the fully-loaded approved narration run for the caption pipeline.

    Returns None when no active approved run exists.  Segments are ordered
    by production_segments.segment_index ascending.
    """
    run = get_active_approved_narration_run(conn, plan_id, experiment_id=experiment_id)
    if run is None:
        return None

    script_id, topic_id = _get_plan_script_topic(conn, plan_id)
    provider, model, voice_id = _get_voice_context(conn, run.voice_profile_id)

    rows = conn.execute(
        """
        SELECT
            nsa.id          AS asset_id,
            nsa.segment_id  AS segment_id,
            ps.narration_text AS narration_text,
            nsa.narration_text_hash AS narration_text_hash,
            nsa.audio_sha256 AS audio_sha256,
            nsa.audio_path   AS audio_path,
            nsa.duration_seconds AS duration_seconds,
            nsa.provider     AS provider,
            nsa.model        AS model,
            nsa.voice_id     AS voice_id
        FROM narration_segment_assets nsa
        JOIN production_segments ps ON ps.id = nsa.segment_id
        WHERE nsa.run_id = ?
          AND nsa.status = 'synthesized'
          AND nsa.superseded_at IS NULL
        ORDER BY ps.segment_index ASC
        """,
        (run.id,),
    ).fetchall()

    segments = tuple(
        ApprovedNarrationSegment(
            asset_id=r[0],
            segment_id=r[1],
            narration_text=r[2],
            narration_text_hash=r[3],
            audio_sha256=r[4],
            audio_path=r[5],
            duration_ms=round(r[6] * 1000) if r[6] is not None else 0,
            provider=r[7],
            model=r[8],
            voice_id=r[9],
        )
        for r in rows
    )

    return ApprovedNarrationRun(
        run_id=run.id,
        plan_id=plan_id,
        script_id=script_id,
        topic_id=topic_id,
        experiment_id=run.experiment_id,
        input_hash=run.input_hash,
        voice_profile_id=run.voice_profile_id,
        provider=provider,
        model=model,
        voice_id=voice_id,
        language=run.language,
        segments=segments,
    )
