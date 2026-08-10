"""Channel routes — list, create, detail, accounts, strategy, policy."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from app.api.deps import get_actor, get_app_service, get_db
from app.application.commands import CreateChannelCommand, CreatePlatformAccountCommand
from app.application.queries import GetChannelSummaryQuery
from app.application.services import ApplicationService
from app.control_plane import services as cp

router = APIRouter(prefix="/workspaces/{workspace_id}/channels", tags=["channels"])


@router.get("")
def list_channels(
    workspace_id: str,
    db: Any = Depends(get_db),
) -> list[dict[str, Any]]:
    channels = cp.list_channels(db, workspace_id)
    return [c.model_dump() for c in channels]


@router.post("")
def create_channel(
    workspace_id: str,
    body: dict[str, Any] = Body(...),
    actor: str = Depends(get_actor),
    svc: ApplicationService = Depends(get_app_service),
) -> dict[str, Any]:
    try:
        ch = svc.create_channel(
            CreateChannelCommand(
                workspace_id=workspace_id,
                name=body["name"],
                slug=body["slug"],
                actor=actor,
                description=body.get("description"),
            )
        )
        return ch.model_dump()
    except PermissionError:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{channel_id}")
def get_channel_summary(
    workspace_id: str,
    channel_id: str,
    svc: ApplicationService = Depends(get_app_service),
) -> dict[str, Any]:
    try:
        view = svc.get_channel_summary(
            GetChannelSummaryQuery(workspace_id=workspace_id, channel_id=channel_id)
        )
        return view.model_dump()
    except PermissionError:
        raise
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{channel_id}/accounts")
def list_channel_accounts(
    workspace_id: str,
    channel_id: str,
    db: Any = Depends(get_db),
) -> list[dict[str, Any]]:
    accounts = cp.list_platform_accounts(db, channel_id)
    return [a.model_dump() for a in accounts]


@router.post("/{channel_id}/accounts")
def create_platform_account(
    workspace_id: str,
    channel_id: str,
    body: dict[str, Any] = Body(...),
    actor: str = Depends(get_actor),
    svc: ApplicationService = Depends(get_app_service),
) -> dict[str, Any]:
    try:
        acct = svc.create_platform_account(
            CreatePlatformAccountCommand(
                workspace_id=workspace_id,
                channel_id=channel_id,
                platform_id=body["platform_id"],
                external_account_id=body["external_account_id"],
                display_name=body["display_name"],
                actor=actor,
            )
        )
        return acct.model_dump()
    except PermissionError:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{channel_id}/strategy")
def get_channel_strategy(
    workspace_id: str,
    channel_id: str,
    db: Any = Depends(get_db),
) -> dict[str, Any]:
    strategy = cp.get_channel_strategy(db, channel_id)
    if strategy is None:
        return {"status": "unavailable", "message": "No active strategy profile assigned"}
    return strategy.model_dump()


@router.get("/{channel_id}/policy")
def get_effective_policy(
    workspace_id: str,
    channel_id: str,
    db: Any = Depends(get_db),
) -> dict[str, Any]:
    level = cp.get_effective_policy(db, workspace_id, channel_id=channel_id)
    return {"effective_automation_level": level}
