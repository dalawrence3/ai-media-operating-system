"""Unified review service — aggregates review items from all engines and the CP.

The service does NOT hold its own review history; it reads through existing
canonical engine tables. Approve/reject calls route through registered safe
handlers only.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.application.contracts import ReviewItemView
from app.application.errors import UnknownCommandError
from app.application.registry import ExtensionRegistry
from app.control_plane.reviews import build_review_queue


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def get_review_queue(conn: Any, workspace_id: str) -> list[ReviewItemView]:
    """Return all open review items for a workspace, aggregated from all engines."""
    cp_items = build_review_queue(conn, workspace_id)

    result: list[ReviewItemView] = []

    for item in cp_items:
        result.append(
            ReviewItemView(
                item_type=item.item_type,
                item_id=item.entity_id,
                workspace_id=workspace_id,
                channel_id=None,
                description=item.summary,
                status="open",
                created_at=_now_iso(),
                metadata={"severity": item.severity},
            )
        )

    # Application-layer: pipelines waiting for review.
    pipeline_rows = conn.execute(
        "SELECT id, channel_id, current_stage, created_at "
        "FROM app_pipeline_executions "
        "WHERE workspace_id=? AND status='waiting_for_review'",
        (workspace_id,),
    ).fetchall()
    for row in pipeline_rows:
        result.append(
            ReviewItemView(
                item_type="pipeline_review",
                item_id=row["id"],
                workspace_id=workspace_id,
                channel_id=row["channel_id"],
                description=(f"Pipeline waiting for review at stage '{row['current_stage']}'"),
                status="open",
                created_at=row["created_at"] or _now_iso(),
                metadata={"stage": row["current_stage"]},
            )
        )

    return result


def approve_review_item(
    conn: Any,
    item_type: str,
    item_id: str,
    workspace_id: str,
    actor: str,
    *,
    notes: str = "",
    registry: ExtensionRegistry | None = None,
) -> dict[str, Any]:
    """Route an approval through a registered safe handler."""
    handler_key = f"review.approve.{item_type}"
    if registry is not None and registry.has(handler_key):
        handler = registry.get_handler(handler_key)
        return handler(conn, item_id, workspace_id, actor, notes=notes)

    # Built-in: pipeline review approval — advance stage past waiting_for_review.
    if item_type == "pipeline_review":
        return _approve_pipeline_review(conn, item_id, workspace_id, actor)

    raise UnknownCommandError(f"review.approve.{item_type}")


def reject_review_item(
    conn: Any,
    item_type: str,
    item_id: str,
    workspace_id: str,
    actor: str,
    *,
    reason: str = "",
    registry: ExtensionRegistry | None = None,
) -> dict[str, Any]:
    """Route a rejection through a registered safe handler."""
    handler_key = f"review.reject.{item_type}"
    if registry is not None and registry.has(handler_key):
        handler = registry.get_handler(handler_key)
        return handler(conn, item_id, workspace_id, actor, reason=reason)

    if item_type == "pipeline_review":
        return _reject_pipeline_review(conn, item_id, workspace_id, actor, reason=reason)

    raise UnknownCommandError(f"review.reject.{item_type}")


def _approve_pipeline_review(
    conn: Any, pipeline_id: str, workspace_id: str, actor: str
) -> dict[str, Any]:
    from app.application import state as pipeline_state

    pv = pipeline_state.get_pipeline(conn, pipeline_id)
    if pv.workspace_id != workspace_id:
        from app.application.errors import CrossWorkspaceAccessError

        raise CrossWorkspaceAccessError("Pipeline", pipeline_id, workspace_id)

    # Find the stage currently waiting for review and mark it completed.
    waiting = [s for s in pv.stages if s.status == "waiting_for_review"]
    if not waiting:
        return {"status": "no_action", "pipeline_id": pipeline_id}

    waiting_stage = waiting[0]
    stage_name = waiting_stage.stage
    # Preserve the artifact_id/type that the executor stored when the stage
    # transitioned to waiting_for_review; without this they would be nulled out.
    pipeline_state.advance_stage(
        conn,
        pipeline_id,
        stage_name,
        artifact_id=waiting_stage.artifact_id,
        artifact_type=waiting_stage.artifact_type,
    )
    pipeline_state.update_pipeline_status(conn, pipeline_id, "running", current_stage=stage_name)

    return {
        "status": "approved",
        "pipeline_id": pipeline_id,
        "stage": stage_name,
        "actor": actor,
        "approved_at": _now_iso(),
    }


def _reject_pipeline_review(
    conn: Any, pipeline_id: str, workspace_id: str, actor: str, *, reason: str
) -> dict[str, Any]:
    from app.application import state as pipeline_state

    pv = pipeline_state.get_pipeline(conn, pipeline_id)
    if pv.workspace_id != workspace_id:
        from app.application.errors import CrossWorkspaceAccessError

        raise CrossWorkspaceAccessError("Pipeline", pipeline_id, workspace_id)

    waiting = [s for s in pv.stages if s.status == "waiting_for_review"]
    if not waiting:
        return {"status": "no_action", "pipeline_id": pipeline_id}

    stage_name = waiting[0].stage
    pipeline_state.fail_stage(conn, pipeline_id, stage_name, error_message=reason or "rejected")

    return {
        "status": "rejected",
        "pipeline_id": pipeline_id,
        "stage": stage_name,
        "actor": actor,
        "reason": reason,
        "rejected_at": _now_iso(),
    }
