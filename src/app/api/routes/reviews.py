"""Review routes — unified review queue + approve/reject."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from app.api.deps import get_actor, get_app_service
from app.application.commands import ApproveReviewItemCommand, RejectReviewItemCommand
from app.application.services import ApplicationService

router = APIRouter(prefix="/workspaces/{workspace_id}/reviews", tags=["reviews"])


@router.post("/{item_type}/{item_id}/approve")
def approve_review_item(
    workspace_id: str,
    item_type: str,
    item_id: str,
    body: dict[str, Any] = Body(default={}),
    actor: str = Depends(get_actor),
    svc: ApplicationService = Depends(get_app_service),
) -> dict[str, Any]:
    try:
        return svc.approve_review_item(
            ApproveReviewItemCommand(
                workspace_id=workspace_id,
                item_type=item_type,
                item_id=item_id,
                actor=actor,
                notes=body.get("notes"),
            )
        )
    except PermissionError:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{item_type}/{item_id}/reject")
def reject_review_item(
    workspace_id: str,
    item_type: str,
    item_id: str,
    body: dict[str, Any] = Body(...),
    actor: str = Depends(get_actor),
    svc: ApplicationService = Depends(get_app_service),
) -> dict[str, Any]:
    try:
        return svc.reject_review_item(
            RejectReviewItemCommand(
                workspace_id=workspace_id,
                item_type=item_type,
                item_id=item_id,
                actor=actor,
                reason=body.get("reason", ""),
                notes=body.get("notes"),
            )
        )
    except PermissionError:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
