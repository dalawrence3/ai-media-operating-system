"""Workspace routes — list, summary, control-center, audit, config."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_app_service, get_db
from app.application.queries import (
    GetAuditTimelineQuery,
    GetCostSummaryQuery,
    GetEffectiveConfigQuery,
    GetExceptionQueueQuery,
    GetReviewQueueQuery,
    GetSystemHealthQuery,
    GetWorkspaceSummaryQuery,
)
from app.application.services import ApplicationService
from app.control_plane import services as cp

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.get("")
def list_workspaces(
    status: str | None = None,
    db: Any = Depends(get_db),
) -> list[dict[str, Any]]:
    workspaces = cp.list_workspaces(db, status=status)
    return [w.model_dump() for w in workspaces]


@router.get("/{workspace_id}")
def get_workspace_summary(
    workspace_id: str,
    svc: ApplicationService = Depends(get_app_service),
) -> dict[str, Any]:
    try:
        view = svc.get_workspace_summary(GetWorkspaceSummaryQuery(workspace_id=workspace_id))
        return view.model_dump()
    except PermissionError:
        raise
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{workspace_id}/control-center")
def get_control_center(
    workspace_id: str,
    db: Any = Depends(get_db),
) -> dict[str, Any]:
    try:
        return cp.workspace_control_center_status(db, workspace_id)
    except PermissionError:
        raise
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{workspace_id}/health")
def get_health(
    workspace_id: str,
    svc: ApplicationService = Depends(get_app_service),
) -> dict[str, Any]:
    try:
        view = svc.get_system_health(GetSystemHealthQuery(workspace_id=workspace_id))
        return view.model_dump()
    except PermissionError:
        raise
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{workspace_id}/review-queue")
def get_review_queue(
    workspace_id: str,
    svc: ApplicationService = Depends(get_app_service),
) -> list[dict[str, Any]]:
    items = svc.get_review_queue(GetReviewQueueQuery(workspace_id=workspace_id))
    return [i.model_dump() for i in items]


@router.get("/{workspace_id}/exceptions")
def get_exceptions(
    workspace_id: str,
    svc: ApplicationService = Depends(get_app_service),
) -> list[dict[str, Any]]:
    items = svc.get_exception_queue(GetExceptionQueueQuery(workspace_id=workspace_id))
    return [i.model_dump() for i in items]


@router.get("/{workspace_id}/costs")
def get_costs(
    workspace_id: str,
    period: str = "monthly",
    svc: ApplicationService = Depends(get_app_service),
) -> dict[str, Any]:
    try:
        view = svc.get_cost_summary(GetCostSummaryQuery(workspace_id=workspace_id, period=period))
        return view.model_dump()
    except PermissionError:
        raise
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{workspace_id}/audit")
def get_audit_timeline(
    workspace_id: str,
    limit: int = 50,
    svc: ApplicationService = Depends(get_app_service),
) -> list[dict[str, Any]]:
    items = svc.get_audit_timeline(GetAuditTimelineQuery(workspace_id=workspace_id, limit=limit))
    return [i.model_dump() for i in items]


@router.get("/{workspace_id}/config")
def get_effective_config(
    workspace_id: str,
    channel_id: str | None = None,
    platform_account_id: str | None = None,
    svc: ApplicationService = Depends(get_app_service),
) -> dict[str, Any]:
    return svc.get_effective_config(
        GetEffectiveConfigQuery(
            workspace_id=workspace_id,
            channel_id=channel_id,
            platform_account_id=platform_account_id,
        )
    )


@router.get("/{workspace_id}/experiments")
def list_experiments(
    workspace_id: str,
    db: Any = Depends(get_db),
) -> list[dict[str, Any]]:
    exps = cp.list_active_experiments(db, workspace_id)
    return [e.model_dump() for e in exps]


@router.get("/{workspace_id}/events")
def list_events(
    workspace_id: str,
    event_type: str | None = None,
    limit: int = 50,
    db: Any = Depends(get_db),
) -> list[dict[str, Any]]:
    events = cp.get_event_history(db, workspace_id, event_type=event_type, limit=limit)
    return [e.model_dump() for e in events]
