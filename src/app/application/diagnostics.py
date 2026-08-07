"""Explainable diagnostic services.

Produces human-readable DiagnosticReport for operators trying to understand
why a pipeline is blocked, why a command was denied, etc.

No AI remediation in Phase 13.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.application.contracts import DiagnosticFinding, DiagnosticReport
from app.application.errors import PipelineNotFoundError
from app.application.versioning import APPLICATION_CONTRACT_VERSION
from app.control_plane import repository as cp_repo
from app.control_plane import services as cp_services
from app.control_plane.constants import AUTOMATION_MANUAL
from app.control_plane.costs import check_budget
from app.control_plane.errors import BudgetExceededError


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def explain_pipeline(conn: Any, pipeline_id: str, workspace_id: str) -> DiagnosticReport:
    """Explain why a pipeline is in its current state."""
    from app.application import state as pipeline_state

    findings: list[DiagnosticFinding] = []

    try:
        pv = pipeline_state.get_pipeline(conn, pipeline_id)
    except PipelineNotFoundError:
        return DiagnosticReport(
            subject="pipeline",
            subject_id=pipeline_id,
            workspace_id=workspace_id,
            status="error",
            findings=[
                DiagnosticFinding(
                    category="not_found",
                    severity="error",
                    message=f"Pipeline '{pipeline_id}' not found",
                    detail={},
                )
            ],
            generated_at=_now_iso(),
            contract_version=APPLICATION_CONTRACT_VERSION,
        )

    if pv.workspace_id != workspace_id:
        return DiagnosticReport(
            subject="pipeline",
            subject_id=pipeline_id,
            workspace_id=workspace_id,
            status="error",
            findings=[
                DiagnosticFinding(
                    category="isolation_violation",
                    severity="error",
                    message="Pipeline does not belong to this workspace",
                    detail={"pipeline_workspace": pv.workspace_id},
                )
            ],
            generated_at=_now_iso(),
            contract_version=APPLICATION_CONTRACT_VERSION,
        )

    if pv.status == "failed":
        failed_stages = [s for s in pv.stages if s.status == "failed"]
        for s in failed_stages:
            findings.append(
                DiagnosticFinding(
                    category="stage_failed",
                    severity="error",
                    message=f"Stage '{s.stage}' failed: {s.error_message}",
                    detail={"stage": s.stage, "error": s.error_message},
                )
            )

    if pv.status == "waiting_for_review":
        review_stages = [s for s in pv.stages if s.status == "waiting_for_review"]
        for s in review_stages:
            findings.append(
                DiagnosticFinding(
                    category="waiting_for_review",
                    severity="info",
                    message=f"Stage '{s.stage}' is waiting for human review",
                    detail={"stage": s.stage},
                )
            )

    if pv.blocked_reason:
        findings.append(
            DiagnosticFinding(
                category="blocked",
                severity="warn",
                message=f"Pipeline blocked: {pv.blocked_reason}",
                detail={"reason": pv.blocked_reason},
            )
        )

    if pv.status == "paused":
        findings.append(
            DiagnosticFinding(
                category="paused",
                severity="info",
                message="Pipeline is paused by operator",
                detail={},
            )
        )

    overall = "ok" if not findings else ("error" if pv.status == "failed" else "warn")
    return DiagnosticReport(
        subject="pipeline",
        subject_id=pipeline_id,
        workspace_id=workspace_id,
        status=overall,
        findings=findings,
        generated_at=_now_iso(),
        contract_version=APPLICATION_CONTRACT_VERSION,
    )


def explain_workspace_health(conn: Any, workspace_id: str) -> DiagnosticReport:
    """Explain the current health of a workspace."""
    findings: list[DiagnosticFinding] = []

    # Workspace status.
    try:
        ws = cp_repo.get_workspace(conn, workspace_id)
        if ws.status != "active":
            findings.append(
                DiagnosticFinding(
                    category="workspace_not_active",
                    severity="warn",
                    message=f"Workspace status is '{ws.status}' — operations will not proceed",
                    detail={"workspace_id": workspace_id, "status": ws.status},
                )
            )
    except Exception as exc:
        findings.append(
            DiagnosticFinding(
                category="workspace_not_found",
                severity="error",
                message=str(exc),
                detail={},
            )
        )

    # Automation level.
    try:
        level = cp_services.get_effective_policy(conn, workspace_id)
        if level == AUTOMATION_MANUAL:
            findings.append(
                DiagnosticFinding(
                    category="automation_level",
                    severity="info",
                    message=(
                        "Effective automation level is MANUAL"
                        " — all stages require explicit operator action"
                    ),
                    detail={"effective_level": level},
                )
            )
    except Exception:
        pass

    # Budget.
    try:
        result = check_budget(conn, workspace_id)
        if result.get("warnings"):
            findings.append(
                DiagnosticFinding(
                    category="budget_warn",
                    severity="warn",
                    message="Budget warning threshold reached",
                    detail=result,
                )
            )
    except BudgetExceededError as exc:
        findings.append(
            DiagnosticFinding(
                category="budget_blocked",
                severity="error",
                message=f"Budget limit exceeded — new operations are blocked: {exc}",
                detail={},
            )
        )
    except Exception:
        pass

    overall = "ok"
    if any(f.severity == "error" for f in findings):
        overall = "error"
    elif any(f.severity == "warn" for f in findings):
        overall = "warn"

    return DiagnosticReport(
        subject="workspace",
        subject_id=workspace_id,
        workspace_id=workspace_id,
        status=overall,
        findings=findings,
        generated_at=_now_iso(),
        contract_version=APPLICATION_CONTRACT_VERSION,
    )


def explain_effective_policy(
    conn: Any,
    workspace_id: str,
    *,
    channel_id: str | None = None,
    platform_account_id: str | None = None,
) -> DiagnosticReport:
    """Explain the effective automation policy for a given scope."""
    findings: list[DiagnosticFinding] = []

    try:
        level = cp_services.get_effective_policy(
            conn, workspace_id, channel_id, platform_account_id
        )
        findings.append(
            DiagnosticFinding(
                category="effective_policy",
                severity="info",
                message=f"Effective automation level: {level.upper()}",
                detail={
                    "workspace_id": workspace_id,
                    "channel_id": channel_id,
                    "platform_account_id": platform_account_id,
                    "effective_level": level,
                },
            )
        )
    except Exception as exc:
        findings.append(
            DiagnosticFinding(
                category="policy_error",
                severity="error",
                message=str(exc),
                detail={},
            )
        )

    subject_id = platform_account_id or channel_id or workspace_id
    return DiagnosticReport(
        subject="policy",
        subject_id=subject_id,
        workspace_id=workspace_id,
        status="ok",
        findings=findings,
        generated_at=_now_iso(),
        contract_version=APPLICATION_CONTRACT_VERSION,
    )


def explain_budget_status(conn: Any, workspace_id: str) -> DiagnosticReport:
    """Explain the current budget status for a workspace."""
    findings: list[DiagnosticFinding] = []
    status = "ok"

    try:
        result = check_budget(conn, workspace_id)
        for warning in result.get("warnings", []):
            findings.append(
                DiagnosticFinding(
                    category="budget_warning",
                    severity="warn",
                    message=warning,
                    detail=result,
                )
            )
        if findings:
            status = "warn"
    except BudgetExceededError as exc:
        status = "error"
        findings.append(
            DiagnosticFinding(
                category="budget_blocked",
                severity="error",
                message=f"Budget limit exceeded; new work is blocked: {exc}",
                detail={},
            )
        )
    except Exception as exc:
        findings.append(
            DiagnosticFinding(
                category="budget_check_error",
                severity="warn",
                message=f"Budget check failed: {exc}",
                detail={},
            )
        )

    return DiagnosticReport(
        subject="budget",
        subject_id=workspace_id,
        workspace_id=workspace_id,
        status=status,
        findings=findings,
        generated_at=_now_iso(),
        contract_version=APPLICATION_CONTRACT_VERSION,
    )


def explain_executor_status(
    conn: Any,  # noqa: ARG001
    workspace_id: str,
    stage: str,
    executor_registry: Any | None = None,
) -> DiagnosticReport:
    """Structured explanation of executor availability for a given stage.

    Categories emitted:
      executor_unavailable  — stage not registered in the registry
      executor_disabled     — stage registered but currently disabled
      executor_ok           — stage registered and enabled
      external_provider_required — stage is class-C (live HTTP/platform provider)
      provider_required     — stage is class-B (AI/TTS provider not yet injected)
    """
    from app.application.errors import ExecutorDisabledError, ExecutorNotFoundError
    from app.application.executor import (
        ExternalProviderRequiredExecutor,
        ProviderRequiredExecutor,
        get_default_executor_registry,
    )

    findings: list[DiagnosticFinding] = []
    status = "ok"
    registry = executor_registry or get_default_executor_registry()

    try:
        executor = registry.get(stage)
    except ExecutorNotFoundError:
        status = "error"
        findings.append(
            DiagnosticFinding(
                category="executor_unavailable",
                severity="error",
                message=f"No executor registered for stage '{stage}'",
                detail={"stage": stage},
            )
        )
        return DiagnosticReport(
            subject="executor",
            subject_id=stage,
            workspace_id=workspace_id,
            status=status,
            findings=findings,
            generated_at=_now_iso(),
            contract_version=APPLICATION_CONTRACT_VERSION,
        )
    except ExecutorDisabledError:
        status = "blocked"
        findings.append(
            DiagnosticFinding(
                category="executor_disabled",
                severity="warn",
                message=f"Executor for stage '{stage}' is registered but disabled",
                detail={"stage": stage},
            )
        )
        return DiagnosticReport(
            subject="executor",
            subject_id=stage,
            workspace_id=workspace_id,
            status=status,
            findings=findings,
            generated_at=_now_iso(),
            contract_version=APPLICATION_CONTRACT_VERSION,
        )

    if isinstance(executor, ExternalProviderRequiredExecutor):
        status = "blocked"
        findings.append(
            DiagnosticFinding(
                category="external_provider_required",
                severity="warn",
                message=(
                    f"Stage '{stage}' requires a live external provider "
                    "(Phase 15 cloud infrastructure). Execution will return blocked."
                ),
                detail={"stage": stage, "executor_key": executor.executor_key},
            )
        )
    elif isinstance(executor, ProviderRequiredExecutor):
        status = "blocked"
        findings.append(
            DiagnosticFinding(
                category="provider_required",
                severity="warn",
                message=(
                    f"Stage '{stage}' requires an AI/TTS provider injected via "
                    "effective_config (Phase 14 studio layer). Execution will return blocked."
                ),
                detail={"stage": stage, "executor_key": executor.executor_key},
            )
        )
    else:
        findings.append(
            DiagnosticFinding(
                category="executor_ok",
                severity="ok",
                message=(
                    f"Stage '{stage}' has an executable executor "
                    f"({type(executor).__name__} v{executor.executor_version})"
                ),
                detail={
                    "stage": stage,
                    "executor_key": executor.executor_key,
                    "executor_version": executor.executor_version,
                },
            )
        )

    return DiagnosticReport(
        subject="executor",
        subject_id=stage,
        workspace_id=workspace_id,
        status=status,
        findings=findings,
        generated_at=_now_iso(),
        contract_version=APPLICATION_CONTRACT_VERSION,
    )
