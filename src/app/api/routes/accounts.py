"""Platform account routes — account detail, policy."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_app_service, get_db
from app.application.queries import GetAccountSummaryQuery
from app.application.services import ApplicationService
from app.control_plane import services as cp

router = APIRouter(prefix="/workspaces/{workspace_id}/accounts", tags=["accounts"])


@router.get("/{account_id}")
def get_account_summary(
    workspace_id: str,
    account_id: str,
    svc: ApplicationService = Depends(get_app_service),
) -> dict[str, Any]:
    try:
        view = svc.get_account_summary(
            GetAccountSummaryQuery(workspace_id=workspace_id, account_id=account_id)
        )
        return view.model_dump()
    except PermissionError:
        raise
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{account_id}/policy")
def get_account_policy(
    workspace_id: str,
    account_id: str,
    db: Any = Depends(get_db),
) -> dict[str, Any]:
    level = cp.get_effective_policy(db, workspace_id, platform_account_id=account_id)
    return {"effective_automation_level": level}
