"""Pipeline routes — list, status, stage operations, history, diagnostics."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from app.api.deps import get_app_service, get_dev_actor
from app.application.commands import (
    AdvancePipelineStageCommand,
    CancelPipelineCommand,
    ExecutePipelineStageCommand,
    FailPipelineStageCommand,
    PausePipelineCommand,
    RecoverPipelineCommand,
    ResumePipelineCommand,
    StartPipelineCommand,
)
from app.application.queries import (
    GetDiagnosticsQuery,
    GetPipelineStatusQuery,
    ListPipelinesQuery,
)
from app.application.services import ApplicationService

router = APIRouter(prefix="/workspaces/{workspace_id}/pipelines", tags=["pipelines"])


@router.get("")
def list_pipelines(
    workspace_id: str,
    status: str | None = None,
    channel_id: str | None = None,
    limit: int = 50,
    svc: ApplicationService = Depends(get_app_service),
) -> list[dict[str, Any]]:
    items = svc.list_pipelines(
        ListPipelinesQuery(
            workspace_id=workspace_id,
            status=status,
            channel_id=channel_id,
            limit=limit,
        )
    )
    return [i.model_dump() for i in items]


@router.get("/{pipeline_id}")
def get_pipeline(
    workspace_id: str,
    pipeline_id: str,
    svc: ApplicationService = Depends(get_app_service),
) -> dict[str, Any]:
    try:
        view = svc.get_pipeline_status(
            GetPipelineStatusQuery(workspace_id=workspace_id, pipeline_id=pipeline_id)
        )
        return view.model_dump()
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("")
def start_pipeline(
    workspace_id: str,
    body: dict[str, Any] = Body(...),
    actor: str = Depends(get_dev_actor),
    svc: ApplicationService = Depends(get_app_service),
) -> dict[str, Any]:
    try:
        view = svc.start_pipeline(
            StartPipelineCommand(
                workspace_id=workspace_id,
                channel_id=body.get("channel_id", ""),
                platform_account_id=body.get("platform_account_id"),
                topic_id=body.get("topic_id"),
                end_stage=body.get("end_stage", "learning"),
                experiment_id=body.get("experiment_id"),
                actor=actor,
                idempotency_key=body.get("idempotency_key", ""),
                correlation_id=body.get("correlation_id"),
            )
        )
        return view.model_dump()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{pipeline_id}/pause")
def pause_pipeline(
    workspace_id: str,
    pipeline_id: str,
    actor: str = Depends(get_dev_actor),
    svc: ApplicationService = Depends(get_app_service),
) -> dict[str, Any]:
    try:
        view = svc.pause_pipeline(
            PausePipelineCommand(
                workspace_id=workspace_id, pipeline_id=pipeline_id, actor=actor
            )
        )
        return view.model_dump()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{pipeline_id}/resume")
def resume_pipeline(
    workspace_id: str,
    pipeline_id: str,
    actor: str = Depends(get_dev_actor),
    svc: ApplicationService = Depends(get_app_service),
) -> dict[str, Any]:
    try:
        view = svc.resume_pipeline(
            ResumePipelineCommand(
                workspace_id=workspace_id, pipeline_id=pipeline_id, actor=actor
            )
        )
        return view.model_dump()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{pipeline_id}/cancel")
def cancel_pipeline(
    workspace_id: str,
    pipeline_id: str,
    actor: str = Depends(get_dev_actor),
    svc: ApplicationService = Depends(get_app_service),
) -> dict[str, Any]:
    try:
        view = svc.cancel_pipeline(
            CancelPipelineCommand(
                workspace_id=workspace_id, pipeline_id=pipeline_id, actor=actor
            )
        )
        return view.model_dump()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{pipeline_id}/advance")
def advance_stage(
    workspace_id: str,
    pipeline_id: str,
    body: dict[str, Any] = Body(...),
    actor: str = Depends(get_dev_actor),
    svc: ApplicationService = Depends(get_app_service),
) -> dict[str, Any]:
    try:
        view = svc.advance_pipeline_stage(
            AdvancePipelineStageCommand(
                workspace_id=workspace_id,
                pipeline_id=pipeline_id,
                stage=body["stage"],
                artifact_id=body.get("artifact_id"),
                artifact_type=body.get("artifact_type"),
                actor=actor,
            )
        )
        return view.model_dump()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{pipeline_id}/execute-stage")
def execute_stage(
    workspace_id: str,
    pipeline_id: str,
    body: dict[str, Any] = Body(...),
    actor: str = Depends(get_dev_actor),
    svc: ApplicationService = Depends(get_app_service),
) -> dict[str, Any]:
    try:
        view = svc.execute_pipeline_stage(
            ExecutePipelineStageCommand(
                workspace_id=workspace_id,
                pipeline_id=pipeline_id,
                stage=body["stage"],
                actor=actor,
                idempotency_key=body.get("idempotency_key", ""),
                correlation_id=body.get("correlation_id"),
            )
        )
        return view.model_dump()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{pipeline_id}/fail-stage")
def fail_stage(
    workspace_id: str,
    pipeline_id: str,
    body: dict[str, Any] = Body(...),
    actor: str = Depends(get_dev_actor),
    svc: ApplicationService = Depends(get_app_service),
) -> dict[str, Any]:
    try:
        view = svc.fail_pipeline_stage(
            FailPipelineStageCommand(
                workspace_id=workspace_id,
                pipeline_id=pipeline_id,
                stage=body["stage"],
                error_message=body.get("error_message", ""),
                actor=actor,
            )
        )
        return view.model_dump()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{pipeline_id}/recover")
def recover_pipeline(
    workspace_id: str,
    pipeline_id: str,
    actor: str = Depends(get_dev_actor),
    svc: ApplicationService = Depends(get_app_service),
) -> dict[str, Any]:
    try:
        view = svc.recover_pipeline(
            RecoverPipelineCommand(
                workspace_id=workspace_id, pipeline_id=pipeline_id, actor=actor
            )
        )
        return view.model_dump()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{pipeline_id}/stages/{stage}/history")
def get_stage_history(
    workspace_id: str,
    pipeline_id: str,
    stage: str,
    svc: ApplicationService = Depends(get_app_service),
) -> list[dict[str, Any]]:
    rows = svc.get_stage_history(workspace_id=workspace_id, pipeline_id=pipeline_id, stage=stage)
    return [dict(r) for r in rows]


@router.get("/{pipeline_id}/diagnostics/{stage}")
def get_pipeline_diagnostics(
    workspace_id: str,
    pipeline_id: str,
    stage: str,
    svc: ApplicationService = Depends(get_app_service),
) -> dict[str, Any]:
    try:
        report = svc.get_diagnostics(
            GetDiagnosticsQuery(
                workspace_id=workspace_id, subject="pipeline_stage", subject_id=pipeline_id
            )
        )
        return report.model_dump()
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
