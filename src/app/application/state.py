"""Pipeline execution state — read/write for app_pipeline_executions and
app_pipeline_stage_log.

All functions accept a raw sqlite3 connection; callers are responsible for
connection lifecycle.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from app.application.contracts import PipelineStageView, PipelineView
from app.application.errors import PipelineNotFoundError
from app.application.versioning import APPLICATION_CONTRACT_VERSION


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def create_pipeline(
    conn: Any,
    *,
    workspace_id: str,
    channel_id: str | None,
    topic_id: int | None,
    actor: str,
    idempotency_key: str,
    start_stage: str,
    end_stage: str,
    correlation_id: str,
    platform_account_id: str | None = None,
    experiment_id: str | None = None,
    policy_snapshot: dict[str, Any] | None = None,
) -> str:
    """Insert a new pipeline execution row. Returns the new pipeline ID."""
    pipeline_id = str(uuid.uuid4())
    now = _now_iso()
    conn.execute(
        "INSERT INTO app_pipeline_executions "
        "(id, workspace_id, channel_id, platform_account_id, topic_id, "
        "correlation_id, idempotency_key, status, current_stage, end_stage, "
        "experiment_id, actor, policy_snapshot_json, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            pipeline_id,
            workspace_id,
            channel_id,
            platform_account_id,
            topic_id,
            correlation_id,
            idempotency_key,
            "pending",
            start_stage,
            end_stage,
            experiment_id,
            actor,
            json.dumps(policy_snapshot) if policy_snapshot else None,
            now,
            now,
        ),
    )
    conn.commit()
    return pipeline_id


def create_stage_entries(
    conn: Any, pipeline_id: str, stages: list[str], *, first: str
) -> None:
    """Insert initial stage log rows — first stage is 'running', rest 'pending'."""
    now = _now_iso()
    for stage in stages:
        status = "running" if stage == first else "pending"
        started_at = now if stage == first else None
        conn.execute(
            "INSERT OR IGNORE INTO app_pipeline_stage_log "
            "(id, pipeline_id, stage, attempt_number, status, started_at, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), pipeline_id, stage, 1, status, started_at, now),
        )
    conn.commit()


def advance_stage(
    conn: Any,
    pipeline_id: str,
    stage: str,
    *,
    artifact_id: str | None = None,
    artifact_type: str | None = None,
    duration_ms: int | None = None,
) -> None:
    now = _now_iso()
    conn.execute(
        "UPDATE app_pipeline_stage_log "
        "SET status='completed', artifact_id=?, artifact_type=?, "
        "duration_ms=?, completed_at=? "
        "WHERE pipeline_id=? AND stage=? AND attempt_number=("
        "SELECT MAX(attempt_number) FROM app_pipeline_stage_log "
        "WHERE pipeline_id=? AND stage=?)",
        (artifact_id, artifact_type, duration_ms, now, pipeline_id, stage, pipeline_id, stage),
    )
    conn.commit()


def fail_stage(
    conn: Any, pipeline_id: str, stage: str, *, error_message: str
) -> None:
    now = _now_iso()
    conn.execute(
        "UPDATE app_pipeline_stage_log "
        "SET status='failed', error_message=?, completed_at=? "
        "WHERE pipeline_id=? AND stage=? AND attempt_number=("
        "SELECT MAX(attempt_number) FROM app_pipeline_stage_log "
        "WHERE pipeline_id=? AND stage=?)",
        (error_message, now, pipeline_id, stage, pipeline_id, stage),
    )
    _update_pipeline(conn, pipeline_id, status="failed", error_message=error_message)


def set_stage_waiting_for_review(conn: Any, pipeline_id: str, stage: str) -> None:
    conn.execute(
        "UPDATE app_pipeline_stage_log SET status='waiting_for_review' "
        "WHERE pipeline_id=? AND stage=? AND attempt_number=("
        "SELECT MAX(attempt_number) FROM app_pipeline_stage_log "
        "WHERE pipeline_id=? AND stage=?)",
        (pipeline_id, stage, pipeline_id, stage),
    )
    _update_pipeline(conn, pipeline_id, status="waiting_for_review")


def _update_pipeline(
    conn: Any,
    pipeline_id: str,
    *,
    status: str | None = None,
    current_stage: str | None = None,
    error_message: str | None = None,
    blocked_reason: str | None = None,
) -> None:
    now = _now_iso()
    sets = ["updated_at=?"]
    params: list[Any] = [now]
    if status is not None:
        sets.append("status=?")
        params.append(status)
    if current_stage is not None:
        sets.append("current_stage=?")
        params.append(current_stage)
    if error_message is not None:
        sets.append("error_message=?")
        params.append(error_message)
    if blocked_reason is not None:
        sets.append("blocked_reason=?")
        params.append(blocked_reason)
    params.append(pipeline_id)
    conn.execute(
        f"UPDATE app_pipeline_executions SET {', '.join(sets)} WHERE id=?",
        params,
    )
    conn.commit()


def update_pipeline_status(
    conn: Any,
    pipeline_id: str,
    status: str,
    *,
    current_stage: str | None = None,
    error_message: str | None = None,
    blocked_reason: str | None = None,
) -> None:
    _update_pipeline(
        conn, pipeline_id,
        status=status,
        current_stage=current_stage,
        error_message=error_message,
        blocked_reason=blocked_reason,
    )


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def get_pipeline(conn: Any, pipeline_id: str) -> PipelineView:
    row = conn.execute(
        "SELECT * FROM app_pipeline_executions WHERE id=?", (pipeline_id,)
    ).fetchone()
    if row is None:
        raise PipelineNotFoundError(pipeline_id)
    stages = _load_stages(conn, pipeline_id)
    return _row_to_view(row, stages)


def get_pipeline_by_idempotency_key(
    conn: Any, idempotency_key: str
) -> PipelineView | None:
    row = conn.execute(
        "SELECT * FROM app_pipeline_executions WHERE idempotency_key=?",
        (idempotency_key,),
    ).fetchone()
    if row is None:
        return None
    stages = _load_stages(conn, row["id"])
    return _row_to_view(row, stages)


def list_pipelines(
    conn: Any,
    workspace_id: str,
    *,
    status: str | None = None,
    channel_id: str | None = None,
    limit: int = 50,
) -> list[PipelineView]:
    sql = (
        "SELECT * FROM app_pipeline_executions WHERE workspace_id=?"
    )
    params: list[Any] = [workspace_id]
    if status is not None:
        sql += " AND status=?"
        params.append(status)
    if channel_id is not None:
        sql += " AND channel_id=?"
        params.append(channel_id)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    result = []
    for row in rows:
        stages = _load_stages(conn, row["id"])
        result.append(_row_to_view(row, stages))
    return result


def _row_to_stage_view(r: Any) -> PipelineStageView:
    return PipelineStageView(
        stage=r["stage"],
        attempt_number=r["attempt_number"],
        status=r["status"],
        artifact_id=r["artifact_id"],
        artifact_type=r["artifact_type"],
        error_message=r["error_message"],
        duration_ms=r["duration_ms"],
        started_at=r["started_at"],
        completed_at=r["completed_at"],
    )


def _load_stages(conn: Any, pipeline_id: str) -> list[PipelineStageView]:
    """Return the latest attempt for each stage, ordered by canonical stage position."""
    from app.application.commands import PIPELINE_STAGES

    rows = conn.execute(
        "SELECT * FROM app_pipeline_stage_log "
        "WHERE pipeline_id=? ORDER BY attempt_number DESC",
        (pipeline_id,),
    ).fetchall()
    seen: set[str] = set()
    latest: list = []
    for r in rows:
        if r["stage"] not in seen:
            seen.add(r["stage"])
            latest.append(r)
    latest.sort(
        key=lambda r: PIPELINE_STAGES.index(r["stage"]) if r["stage"] in PIPELINE_STAGES else 999
    )
    return [_row_to_stage_view(r) for r in latest]


def get_stage_history(conn: Any, pipeline_id: str, stage: str) -> list[PipelineStageView]:
    """Return all attempt rows for a stage in order (oldest first)."""
    rows = conn.execute(
        "SELECT * FROM app_pipeline_stage_log "
        "WHERE pipeline_id=? AND stage=? ORDER BY attempt_number",
        (pipeline_id, stage),
    ).fetchall()
    return [_row_to_stage_view(r) for r in rows]


def _row_to_view(row: Any, stages: list[PipelineStageView]) -> PipelineView:
    return PipelineView(
        id=row["id"],
        workspace_id=row["workspace_id"],
        channel_id=row["channel_id"],
        platform_account_id=row["platform_account_id"],
        topic_id=row["topic_id"],
        correlation_id=row["correlation_id"],
        idempotency_key=row["idempotency_key"],
        status=row["status"],
        current_stage=row["current_stage"],
        end_stage=row["end_stage"],
        experiment_id=row["experiment_id"],
        actor=row["actor"],
        error_message=row["error_message"],
        blocked_reason=row["blocked_reason"],
        stages=stages,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        contract_version=APPLICATION_CONTRACT_VERSION,
    )
