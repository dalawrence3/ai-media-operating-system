"""Public application contracts — structured response models for Phase 14 consumers.

No sqlite.Row here. All fields are plain Python types.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class WorkspaceView(BaseModel, frozen=True):
    id: str
    name: str
    slug: str
    status: str
    organization_id: str | None
    channel_count: int
    active_pipeline_count: int
    paused: bool
    automation_level: str


class ChannelView(BaseModel, frozen=True):
    id: str
    workspace_id: str
    name: str
    slug: str
    status: str
    paused: bool
    account_count: int
    automation_level: str
    active_pipeline_count: int


class AccountView(BaseModel, frozen=True):
    id: str
    channel_id: str
    platform_key: str
    display_name: str
    status: str
    external_account_id: str
    automation_level: str


class PipelineStageView(BaseModel, frozen=True):
    stage: str
    attempt_number: int
    status: str
    artifact_id: str | None
    artifact_type: str | None
    error_message: str | None
    duration_ms: int | None
    started_at: str | None
    completed_at: str | None


class PipelineView(BaseModel, frozen=True):
    id: str
    workspace_id: str
    channel_id: str | None
    platform_account_id: str | None
    topic_id: int | None
    correlation_id: str
    idempotency_key: str
    status: str
    current_stage: str | None
    end_stage: str
    experiment_id: str | None
    actor: str
    error_message: str | None
    blocked_reason: str | None
    stages: list[PipelineStageView]
    created_at: str
    updated_at: str
    contract_version: str


class OperationView(BaseModel, frozen=True):
    id: str
    workspace_id: str
    channel_id: str | None
    platform_account_id: str | None
    operation_type: str
    status: str
    idempotency_key: str
    actor: str
    correlation_id: str | None
    error_message: str | None
    created_at: str
    updated_at: str


class ScheduleView(BaseModel, frozen=True):
    id: str
    workspace_id: str
    channel_id: str | None
    name: str
    operation_type: str
    schedule_type: str
    schedule_config: dict[str, Any]
    timezone: str
    is_active: bool
    last_run_at: str | None
    next_run_at: str | None
    actor: str
    created_at: str
    updated_at: str


class ReviewItemView(BaseModel, frozen=True):
    item_type: str
    item_id: str
    workspace_id: str
    channel_id: str | None
    description: str
    status: str
    created_at: str
    metadata: dict[str, Any]


class ExceptionView(BaseModel, frozen=True):
    exception_type: str
    entity_id: str
    workspace_id: str
    description: str
    severity: str
    occurred_at: str
    metadata: dict[str, Any]


class HealthView(BaseModel, frozen=True):
    workspace_id: str
    overall_status: str
    event_bus_ok: bool
    dead_letter_count: int
    stuck_operation_count: int
    active_pipeline_count: int
    budget_status: str
    details: dict[str, Any]


class CostView(BaseModel, frozen=True):
    workspace_id: str
    period: str
    total_usd: float
    channel_breakdown: list[dict[str, Any]]
    budget_policies: list[dict[str, Any]]
    warning_active: bool
    block_active: bool


class AuditView(BaseModel, frozen=True):
    event_type: str
    actor: str
    timestamp: str
    correlation_id: str | None
    description: str
    metadata: dict[str, Any]


class DiagnosticFinding(BaseModel, frozen=True):
    category: str
    severity: str
    message: str
    detail: dict[str, Any]


class DiagnosticReport(BaseModel, frozen=True):
    subject: str
    subject_id: str
    workspace_id: str
    status: str
    findings: list[DiagnosticFinding]
    generated_at: str
    contract_version: str
