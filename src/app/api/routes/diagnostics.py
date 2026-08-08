"""Diagnostics + observability routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_app_service
from app.application.queries import GetDiagnosticsQuery
from app.application.services import ApplicationService

router = APIRouter(prefix="/workspaces/{workspace_id}/diagnostics", tags=["diagnostics"])


@router.get("/{subject}/{subject_id}")
def get_diagnostics(
    workspace_id: str,
    subject: str,
    subject_id: str,
    svc: ApplicationService = Depends(get_app_service),
) -> dict[str, Any]:
    try:
        report = svc.get_diagnostics(
            GetDiagnosticsQuery(
                workspace_id=workspace_id, subject=subject, subject_id=subject_id
            )
        )
        return report.model_dump()
    except PermissionError:
        raise
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/observability")
def get_observability(
    workspace_id: str,
    svc: ApplicationService = Depends(get_app_service),
) -> dict[str, Any]:
    return svc.observability_summary()
