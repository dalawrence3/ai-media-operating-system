"""Typed, versioned, actor-aware application commands.

Commands are requests to change system state. They are distinct from events
(immutable facts that something occurred).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

COMMAND_CONTRACT_VERSION = "13.0.0"

# Canonical pipeline stage ordering — Topic flows left to right.
PIPELINE_STAGES: list[str] = [
    "research",
    "script_generation",
    "production_plan",
    "narration",
    "captions",
    "visual_intelligence",
    "rendering",
    "publishing",
    "analytics",
    "learning",
]

# Stages that require human review before the next stage may proceed.
REVIEW_REQUIRED_STAGES: frozenset[str] = frozenset(
    {
        "script_generation",
        "production_plan",
        "narration",
        "captions",
        "visual_intelligence",
        "rendering",
    }
)

# Mapping: stage → stages that must be completed first.
STAGE_PREREQUISITES: dict[str, list[str]] = {
    "research": [],
    "script_generation": ["research"],
    "production_plan": ["script_generation"],
    "narration": ["production_plan"],
    "captions": ["narration"],
    "visual_intelligence": ["production_plan"],
    "rendering": ["narration", "captions", "visual_intelligence"],
    "publishing": ["rendering"],
    "analytics": ["publishing"],
    "learning": ["analytics"],
}

# Review-item types recognized by the unified review service.
REVIEW_ITEM_TYPES: frozenset[str] = frozenset(
    {
        "script",
        "production_plan",
        "narration_run",
        "caption_run",
        "scene_manifest",
        "render",
        "cp_review_item",
    }
)

# Schedule types.
SCHEDULE_TYPES: frozenset[str] = frozenset(
    {"cron", "interval", "once", "after_event"}
)


# ---------------------------------------------------------------------------
# Pipeline lifecycle commands
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StartPipelineCommand:
    workspace_id: str
    channel_id: str
    actor: str
    idempotency_key: str
    topic_id: int | None = None
    start_stage: str = "research"
    end_stage: str = "learning"
    platform_account_id: str | None = None
    experiment_id: str | None = None
    policy_overrides: dict[str, Any] | None = None
    correlation_id: str | None = None
    contract_version: str = COMMAND_CONTRACT_VERSION


@dataclass(frozen=True)
class PausePipelineCommand:
    pipeline_id: str
    workspace_id: str
    actor: str
    reason: str = ""
    contract_version: str = COMMAND_CONTRACT_VERSION


@dataclass(frozen=True)
class ResumePipelineCommand:
    pipeline_id: str
    workspace_id: str
    actor: str
    contract_version: str = COMMAND_CONTRACT_VERSION


@dataclass(frozen=True)
class CancelPipelineCommand:
    pipeline_id: str
    workspace_id: str
    actor: str
    reason: str = ""
    contract_version: str = COMMAND_CONTRACT_VERSION


@dataclass(frozen=True)
class AdvancePipelineStageCommand:
    pipeline_id: str
    workspace_id: str
    stage: str
    actor: str
    artifact_id: str | None = None
    artifact_type: str | None = None
    contract_version: str = COMMAND_CONTRACT_VERSION


@dataclass(frozen=True)
class ExecutePipelineStageCommand:
    """Invoke the registered executor for the current running stage.

    Unlike AdvancePipelineStageCommand (operator-driven manual advance),
    this triggers real engine execution through the StageExecutorRegistry.
    """

    pipeline_id: str
    workspace_id: str
    stage: str
    actor: str
    effective_config: dict[str, Any] | None = None
    prerequisite_artifact_ids: dict[str, str] | None = None
    contract_version: str = COMMAND_CONTRACT_VERSION


@dataclass(frozen=True)
class FailPipelineStageCommand:
    pipeline_id: str
    workspace_id: str
    stage: str
    error_message: str
    actor: str
    contract_version: str = COMMAND_CONTRACT_VERSION


# ---------------------------------------------------------------------------
# Operation lifecycle commands
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RetryOperationCommand:
    operation_id: str
    workspace_id: str
    actor: str
    contract_version: str = COMMAND_CONTRACT_VERSION


@dataclass(frozen=True)
class CancelOperationCommand:
    operation_id: str
    workspace_id: str
    actor: str
    reason: str = ""
    contract_version: str = COMMAND_CONTRACT_VERSION


# ---------------------------------------------------------------------------
# Review commands
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ApproveReviewItemCommand:
    item_type: str
    item_id: str
    workspace_id: str
    actor: str
    notes: str = ""
    contract_version: str = COMMAND_CONTRACT_VERSION


@dataclass(frozen=True)
class RejectReviewItemCommand:
    item_type: str
    item_id: str
    workspace_id: str
    actor: str
    reason: str = ""
    contract_version: str = COMMAND_CONTRACT_VERSION


# ---------------------------------------------------------------------------
# Schedule commands
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CreateScheduleCommand:
    workspace_id: str
    name: str
    operation_type: str
    schedule_type: str
    schedule_config: dict[str, Any]
    actor: str
    channel_id: str | None = None
    timezone: str = "UTC"
    contract_version: str = COMMAND_CONTRACT_VERSION


@dataclass(frozen=True)
class PauseScheduleCommand:
    schedule_id: str
    workspace_id: str
    actor: str
    contract_version: str = COMMAND_CONTRACT_VERSION


@dataclass(frozen=True)
class ResumeScheduleCommand:
    schedule_id: str
    workspace_id: str
    actor: str
    contract_version: str = COMMAND_CONTRACT_VERSION


@dataclass(frozen=True)
class DeleteScheduleCommand:
    schedule_id: str
    workspace_id: str
    actor: str
    contract_version: str = COMMAND_CONTRACT_VERSION


# ---------------------------------------------------------------------------
# Event / recovery commands
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReplayEventCommand:
    event_id: str
    workspace_id: str
    actor: str
    contract_version: str = COMMAND_CONTRACT_VERSION


@dataclass(frozen=True)
class RecoverPipelineCommand:
    pipeline_id: str
    workspace_id: str
    actor: str
    from_stage: str | None = None
    contract_version: str = COMMAND_CONTRACT_VERSION


# All concrete command types — used by dispatcher for closed-world validation.
ALL_COMMAND_TYPES: frozenset[type] = frozenset(
    {
        StartPipelineCommand,
        PausePipelineCommand,
        ResumePipelineCommand,
        CancelPipelineCommand,
        AdvancePipelineStageCommand,
        ExecutePipelineStageCommand,
        FailPipelineStageCommand,
        RetryOperationCommand,
        CancelOperationCommand,
        ApproveReviewItemCommand,
        RejectReviewItemCommand,
        CreateScheduleCommand,
        PauseScheduleCommand,
        ResumeScheduleCommand,
        DeleteScheduleCommand,
        ReplayEventCommand,
        RecoverPipelineCommand,
    }
)
