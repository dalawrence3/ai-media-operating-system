"""Schedule routes — list, create, pause, resume, delete."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from app.api.deps import get_app_service, get_dev_actor
from app.application.commands import (
    CreateScheduleCommand,
    DeleteScheduleCommand,
    PauseScheduleCommand,
    ResumeScheduleCommand,
)
from app.application.queries import ListSchedulesQuery
from app.application.services import ApplicationService

router = APIRouter(prefix="/workspaces/{workspace_id}/schedules", tags=["schedules"])


@router.get("")
def list_schedules(
    workspace_id: str,
    is_active: bool | None = None,
    svc: ApplicationService = Depends(get_app_service),
) -> list[dict[str, Any]]:
    items = svc.list_schedules(ListSchedulesQuery(workspace_id=workspace_id, is_active=is_active))
    return [i.model_dump() for i in items]


@router.post("")
def create_schedule(
    workspace_id: str,
    body: dict[str, Any] = Body(...),
    actor: str = Depends(get_dev_actor),
    svc: ApplicationService = Depends(get_app_service),
) -> dict[str, Any]:
    try:
        view = svc.create_schedule(
            CreateScheduleCommand(
                workspace_id=workspace_id,
                channel_id=body.get("channel_id"),
                name=body["name"],
                operation_type=body["operation_type"],
                schedule_type=body["schedule_type"],
                schedule_config=body.get("schedule_config", {}),
                timezone=body.get("timezone", "UTC"),
                actor=actor,
            )
        )
        return view.model_dump()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{schedule_id}/pause")
def pause_schedule(
    workspace_id: str,
    schedule_id: str,
    actor: str = Depends(get_dev_actor),
    svc: ApplicationService = Depends(get_app_service),
) -> dict[str, Any]:
    try:
        view = svc.pause_schedule(
            PauseScheduleCommand(
                workspace_id=workspace_id, schedule_id=schedule_id, actor=actor
            )
        )
        return view.model_dump()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{schedule_id}/resume")
def resume_schedule(
    workspace_id: str,
    schedule_id: str,
    actor: str = Depends(get_dev_actor),
    svc: ApplicationService = Depends(get_app_service),
) -> dict[str, Any]:
    try:
        view = svc.resume_schedule(
            ResumeScheduleCommand(
                workspace_id=workspace_id, schedule_id=schedule_id, actor=actor
            )
        )
        return view.model_dump()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{schedule_id}")
def delete_schedule(
    workspace_id: str,
    schedule_id: str,
    actor: str = Depends(get_dev_actor),
    svc: ApplicationService = Depends(get_app_service),
) -> dict[str, Any]:
    try:
        svc.delete_schedule(
            DeleteScheduleCommand(
                workspace_id=workspace_id, schedule_id=schedule_id, actor=actor
            )
        )
        return {"deleted": True}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
