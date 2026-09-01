"""Analytics routes — workspace-scoped aggregate and snapshot reads."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.analytics import repository as analytics_repo
from app.api.deps import get_db, require_workspace_permission, workspace_topic_ids
from app.api.jwt_auth import CurrentUser, get_current_user

router = APIRouter(prefix="/workspaces/{workspace_id}/analytics", tags=["analytics"])


@router.get("/aggregates")
def list_aggregates(
    workspace_id: str,
    metric_name: str | None = None,
    period_type: str | None = None,
    publication_id: int | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    conn: Any = Depends(get_db),
) -> list[dict[str, Any]]:
    require_workspace_permission(current_user, workspace_id, "analytics:view")

    if publication_id is not None:
        # Publication-scoped query: verify ownership then bypass topic_id routing.
        # Handles the case where a publication's workspace differs from the pipeline
        # execution workspace (workspace_topic_ids would return empty for the pub's workspace).
        row = conn.execute(
            "SELECT id FROM publications WHERE id = ? AND workspace_id = ?",
            (publication_id, workspace_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Publication not found")
        results = analytics_repo.list_aggregates(
            conn,
            publication_id=publication_id,
            period_type=period_type,
            metric_name=metric_name,
        )
        results.sort(key=lambda r: (r.period_key, r.metric_name), reverse=True)
        return [r.model_dump() for r in results]

    topic_ids = workspace_topic_ids(conn, workspace_id)
    if not topic_ids:
        return []
    results = []
    for tid in topic_ids:
        results.extend(
            analytics_repo.list_aggregates(
                conn,
                topic_id=tid,
                period_type=period_type,
                metric_name=metric_name,
            )
        )
    results.sort(key=lambda r: (r.period_key, r.metric_name), reverse=True)
    return [r.model_dump() for r in results]


@router.get("/snapshots")
def list_snapshots(
    workspace_id: str,
    limit: int = 50,
    current_user: CurrentUser = Depends(get_current_user),
    conn: Any = Depends(get_db),
) -> list[dict[str, Any]]:
    require_workspace_permission(current_user, workspace_id, "analytics:view")
    topic_ids = workspace_topic_ids(conn, workspace_id)
    if not topic_ids:
        return []
    results: list = []
    for tid in topic_ids:
        results.extend(analytics_repo.list_snapshots(conn, topic_id=tid, limit=limit))
    results.sort(key=lambda r: r.id, reverse=True)
    return [r.model_dump() for r in results[:limit]]
