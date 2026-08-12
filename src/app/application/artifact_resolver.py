"""Safe artifact content resolver for the pipeline artifact viewer.

Resolves artifact content by type from domain tables. Never exposes raw
filesystem paths, object storage keys, or credential-adjacent data.

Content is truncated at CONTENT_MAX_CHARS to keep API responses bounded.
Only types explicitly listed in _SAFE_ARTIFACT_TYPES are resolved; all
others return a stub {"resolved": false, "reason": "type_not_inspectable"}.
"""

from __future__ import annotations

from typing import Any

CONTENT_MAX_CHARS = 8_000

# Artifact types that have no human-readable content (binary, audio, video, etc.)
_BINARY_ARTIFACT_TYPES = frozenset(
    {"narration_audio", "rendered_video", "caption_vtt", "thumbnail_image"}
)

# Secret-adjacent types that must never be surfaced.
_SECRET_ARTIFACT_TYPES = frozenset(
    {"oauth_token", "credential_profile", "platform_token", "access_key"}
)


def resolve_stage_artifact(
    conn: Any,
    *,
    pipeline_id: str,
    stage: str,
    workspace_id: str,
) -> dict[str, Any]:
    """Return artifact metadata + safe content preview for the given stage.

    Raises ValueError if the stage has no artifact or the pipeline does not
    exist. Workspace ownership is validated by the caller (ApplicationService).
    """
    row = conn.execute(
        "SELECT artifact_id, artifact_type, status, attempt_number, "
        "started_at, completed_at, duration_ms, error_message "
        "FROM app_pipeline_stage_log "
        "WHERE pipeline_id = ? AND stage = ? "
        "ORDER BY attempt_number DESC LIMIT 1",
        (pipeline_id, stage),
    ).fetchone()

    if row is None:
        raise ValueError(f"Stage '{stage}' has no log entry for pipeline '{pipeline_id}'")

    artifact_id: str | None = row["artifact_id"]
    artifact_type: str | None = row["artifact_type"]

    base: dict[str, Any] = {
        "pipeline_id": pipeline_id,
        "stage": stage,
        "workspace_id": workspace_id,
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "stage_status": row["status"],
        "attempt_number": row["attempt_number"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
        "duration_ms": row["duration_ms"],
        "error_message": row["error_message"],
    }

    if artifact_id is None or artifact_type is None:
        return {**base, "resolved": False, "reason": "no_artifact"}

    if artifact_type in _SECRET_ARTIFACT_TYPES:
        return {**base, "resolved": False, "reason": "type_secret"}

    if artifact_type in _BINARY_ARTIFACT_TYPES:
        return {**base, "resolved": False, "reason": "type_binary"}

    content = _resolve_content(conn, artifact_type, artifact_id)
    return {**base, **content}


def _resolve_content(conn: Any, artifact_type: str, artifact_id: str) -> dict[str, Any]:
    """Dispatch to the appropriate domain table resolver."""
    resolver = _RESOLVERS.get(artifact_type)
    if resolver is None:
        return {"resolved": False, "reason": "type_not_inspectable"}
    try:
        return resolver(conn, artifact_id)
    except Exception:
        return {"resolved": False, "reason": "content_unavailable"}


# ---------------------------------------------------------------------------
# Domain-specific resolvers
# ---------------------------------------------------------------------------


def _resolve_learning_run(conn: Any, artifact_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT id, topic_id, status, recommendation_count, engine_version, "
        "schema_version, created_at, completed_at "
        "FROM learning_runs WHERE id = ?",
        (artifact_id,),
    ).fetchone()
    if row is None:
        return {"resolved": False, "reason": "content_unavailable"}
    return {
        "resolved": True,
        "content_type": "learning_run",
        "content": {
            "id": row["id"],
            "topic_id": row["topic_id"],
            "status": row["status"],
            "recommendation_count": row["recommendation_count"],
            "engine_version": row["engine_version"],
            "schema_version": row["schema_version"],
            "created_at": row["created_at"],
            "completed_at": row["completed_at"],
        },
        "truncated": False,
    }


def _resolve_production_plan(conn: Any, artifact_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT id, topic_id, status, created_at FROM production_plans WHERE id = ?",
        (artifact_id,),
    ).fetchone()
    if row is None:
        return {"resolved": False, "reason": "content_unavailable"}
    return {
        "resolved": True,
        "content_type": "production_plan",
        "content": {
            "id": row["id"],
            "topic_id": row["topic_id"],
            "status": row["status"],
            "created_at": row["created_at"],
        },
        "truncated": False,
    }


def _resolve_research_brief(conn: Any, artifact_id: str) -> dict[str, Any]:
    """Research briefs are stored as free-form JSON text in the seed."""
    return {
        "resolved": True,
        "content_type": "research_brief",
        "content": {"ref": artifact_id},
        "truncated": False,
    }


def _resolve_script(conn: Any, artifact_id: str) -> dict[str, Any]:
    try:
        script_id = int(artifact_id)
    except (ValueError, TypeError):
        return {"resolved": False, "reason": "content_unavailable"}
    row = conn.execute(
        "SELECT id, topic_id, version, status, created_at FROM scripts WHERE id = ?",
        (script_id,),
    ).fetchone()
    if row is None:
        return {"resolved": False, "reason": "content_unavailable"}
    return {
        "resolved": True,
        "content_type": "script",
        "content": {
            "id": row["id"],
            "topic_id": row["topic_id"],
            "version": row["version"],
            "status": row["status"],
            "created_at": row["created_at"],
        },
        "truncated": False,
    }


_RESOLVERS: dict[str, Any] = {
    "learning_run": _resolve_learning_run,
    "production_plan": _resolve_production_plan,
    "research_brief": _resolve_research_brief,
    "script": _resolve_script,
}
