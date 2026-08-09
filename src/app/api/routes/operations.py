"""Operations routes — list, retry, cancel."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_actor, get_app_service
from app.application.commands import CancelOperationCommand, RetryOperationCommand
from app.application.queries import ListOperationsQuery
from app.application.services import ApplicationService

router = APIRouter(prefix="/workspaces/{workspace_id}/operations", tags=["operations"])


@router.get("")
def list_operations(
    workspace_id: str,
    status: str | None = None,
    channel_id: str | None = None,
    limit: int = 50,
    svc: ApplicationService = Depends(get_app_service),
) -> list[dict[str, Any]]:
    items = svc.list_operations(
        ListOperationsQuery(
            workspace_id=workspace_id,
            status=status,
            channel_id=channel_id,
            limit=limit,
        )
    )
    return [i.model_dump() for i in items]


@router.post("/{operation_id}/retry")
def retry_operation(
    workspace_id: str,
    operation_id: str,
    actor: str = Depends(get_actor),
    svc: ApplicationService = Depends(get_app_service),
) -> dict[str, Any]:
    try:
        return svc.retry_operation(
            RetryOperationCommand(workspace_id=workspace_id, operation_id=operation_id, actor=actor)
        )
    except PermissionError:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{operation_id}/cancel")
def cancel_operation(
    workspace_id: str,
    operation_id: str,
    actor: str = Depends(get_actor),
    svc: ApplicationService = Depends(get_app_service),
) -> dict[str, Any]:
    try:
        return svc.cancel_operation(
            CancelOperationCommand(
                workspace_id=workspace_id, operation_id=operation_id, actor=actor
            )
        )
    except PermissionError:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
