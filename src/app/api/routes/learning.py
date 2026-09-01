"""Learning routes — workspace-scoped recommendations and accept/reject."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.api.deps import get_db, require_workspace_permission, workspace_topic_ids
from app.api.jwt_auth import CurrentUser, get_current_user
from app.learning import repository as learning_repo
from app.learning.orchestrator import accept_recommendation, reject_recommendation

router = APIRouter(prefix="/workspaces/{workspace_id}/recommendations", tags=["learning"])


@router.get("")
def list_recommendations(
    workspace_id: str,
    status: str | None = None,
    domain: str | None = None,
    limit: int = 100,
    publication_id: int | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    conn: Any = Depends(get_db),
) -> list[dict[str, Any]]:
    require_workspace_permission(current_user, workspace_id, "recommendations:view")

    if publication_id is not None:
        # Publication-scoped query: verify ownership then bypass topic_id routing.
        row = conn.execute(
            "SELECT id FROM publications WHERE id = ? AND workspace_id = ?",
            (publication_id, workspace_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Publication not found")
        results = learning_repo.list_recommendations(
            conn,
            publication_id=publication_id,
            domain=domain,
            status=status,
            limit=limit,
        )
        return [r.model_dump() for r in results]

    topic_ids = workspace_topic_ids(conn, workspace_id)
    if not topic_ids:
        return []
    results = []
    for tid in topic_ids:
        results.extend(
            learning_repo.list_recommendations(
                conn,
                topic_id=tid,
                domain=domain,
                status=status,
                limit=limit,
            )
        )
    results.sort(key=lambda r: r.created_at, reverse=True)
    return [r.model_dump() for r in results[:limit]]


@router.post("/{recommendation_id}/accept")
def accept_rec(
    workspace_id: str,
    recommendation_id: int,
    body: dict[str, Any] = Body(default={}),
    current_user: CurrentUser = Depends(get_current_user),
    conn: Any = Depends(get_db),
) -> dict[str, Any]:
    require_workspace_permission(current_user, workspace_id, "recommendations:review")
    topic_ids = workspace_topic_ids(conn, workspace_id)
    try:
        rec = learning_repo.get_recommendation(conn, recommendation_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if rec.topic_id not in topic_ids:
        raise HTTPException(status_code=404, detail="Recommendation not found in this workspace")
    try:
        event = accept_recommendation(
            conn,
            recommendation_id,
            reviewer=current_user.actor,
            notes=body.get("notes", ""),
            expected_outcome=body.get("expected_outcome", ""),
        )
        return event.model_dump()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{recommendation_id}/reject")
def reject_rec(
    workspace_id: str,
    recommendation_id: int,
    body: dict[str, Any] = Body(...),
    current_user: CurrentUser = Depends(get_current_user),
    conn: Any = Depends(get_db),
) -> dict[str, Any]:
    require_workspace_permission(current_user, workspace_id, "recommendations:review")
    topic_ids = workspace_topic_ids(conn, workspace_id)
    try:
        rec = learning_repo.get_recommendation(conn, recommendation_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if rec.topic_id not in topic_ids:
        raise HTTPException(status_code=404, detail="Recommendation not found in this workspace")
    notes = body.get("notes", "")
    if not notes:
        raise HTTPException(status_code=400, detail="Notes are required for rejection")
    try:
        event = reject_recommendation(
            conn,
            recommendation_id,
            reviewer=current_user.actor,
            notes=notes,
            expected_outcome=body.get("expected_outcome", ""),
        )
        return event.model_dump()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
