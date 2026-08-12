"""Topic management routes.

Exposes workspace-scoped topic listing and creation. Topics are referenced by
integer ID in pipeline start commands; these routes let the frontend present
human-readable topic names rather than requiring raw ID entry.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from app.api.deps import get_actor, get_app_service, require_workspace_permission
from app.api.jwt_auth import get_current_user
from app.application import topics as topic_svc
from app.application.services import ApplicationService

router = APIRouter(prefix="/workspaces/{workspace_id}/topics", tags=["topics"])


@router.get("")
def list_topics(
    workspace_id: str,
    current_user=Depends(get_current_user),
    svc: ApplicationService = Depends(get_app_service),
) -> list[dict[str, Any]]:
    require_workspace_permission(current_user, workspace_id, "pipeline:view")
    topics = topic_svc.list_topics(svc._conn, workspace_id)
    return [t.model_dump() for t in topics]


@router.post("", status_code=201)
def create_topic(
    workspace_id: str,
    body: dict[str, Any] = Body(...),
    actor: str = Depends(get_actor),
    current_user=Depends(get_current_user),
    svc: ApplicationService = Depends(get_app_service),
) -> dict[str, Any]:
    require_workspace_permission(current_user, workspace_id, "pipeline:create")
    title = body.get("title", "")
    angle = body.get("angle", "")
    try:
        topic = topic_svc.create_topic(svc._conn, workspace_id, title, angle)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return topic.model_dump()
