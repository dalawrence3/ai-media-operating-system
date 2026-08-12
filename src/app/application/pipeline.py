"""Pipeline controller — coordinates the canonical media lifecycle.

The controller manages state transitions and prerequisite checking.
It does NOT invoke engine business logic directly; it calls through
existing public engine boundaries.

Canonical stage order:
  research → script_generation → production_plan → narration → captions
  → visual_intelligence → rendering → publishing → analytics → learning
"""

from __future__ import annotations

import uuid
from typing import Any

from app.application import state as pipeline_state
from app.application.commands import (
    PIPELINE_STAGES,
    REVIEW_REQUIRED_STAGES,
    STAGE_PREREQUISITES,
    AdvancePipelineStageCommand,
    ExecutePipelineStageCommand,
    FailPipelineStageCommand,
    StartPipelineCommand,
)
from app.application.configuration import snapshot_policy_refs
from app.application.contracts import PipelineView
from app.application.errors import (
    ChannelNotFoundError,
    ChannelPausedError,
    CrossWorkspaceAccessError,
    ExecutorDisabledError,
    ExecutorNotFoundError,
    InvalidStageError,
    PipelineAlreadyExistsError,
    StagePrerequisiteError,
    WorkspaceNotFoundError,
    WorkspacePausedError,
)
from app.control_plane import repository as cp_repo
from app.control_plane.event_bus import dispatch_event
from app.control_plane.models import ControlEventDraft  # noqa: F401 — kept for type clarity


def _emit_event(
    conn: Any,
    event_type: str,
    workspace_id: str,
    actor: str,
    payload: dict[str, Any],
    *,
    correlation_id: str | None = None,
) -> None:
    draft = ControlEventDraft(
        id=str(uuid.uuid4()),
        event_type=event_type,
        workspace_id=workspace_id,
        actor=actor,
        payload=payload,
        correlation_id=correlation_id,
    )
    event = cp_repo.create_event(conn, draft)
    dispatch_event(conn, event)


def validate_start_command(conn: Any, cmd: StartPipelineCommand) -> None:
    """Raise an appropriate error if the command cannot proceed."""
    # Workspace exists and is not paused.
    try:
        ws = cp_repo.get_workspace(conn, cmd.workspace_id)
    except Exception as exc:
        raise WorkspaceNotFoundError(cmd.workspace_id) from exc
    if ws.status != "active":
        raise WorkspacePausedError(cmd.workspace_id)

    # Channel exists and belongs to the workspace.
    try:
        ch = cp_repo.get_channel(conn, cmd.channel_id)
    except Exception as exc:
        raise ChannelNotFoundError(cmd.channel_id) from exc
    if ch.workspace_id != cmd.workspace_id:
        raise CrossWorkspaceAccessError("Channel", cmd.channel_id, cmd.workspace_id)
    if ch.status == "paused":
        raise ChannelPausedError(cmd.channel_id)

    # topic_id must belong to the workspace — guards against cross-workspace guessing.
    if cmd.topic_id is not None:
        topic_row = conn.execute(
            "SELECT workspace_id FROM topics WHERE id = ?",
            (cmd.topic_id,),
        ).fetchone()
        if topic_row is None:
            raise ValueError(f"Topic {cmd.topic_id} not found")
        if topic_row["workspace_id"] != cmd.workspace_id:
            raise CrossWorkspaceAccessError("Topic", str(cmd.topic_id), cmd.workspace_id)

    # Stage names are valid.
    valid_stages = set(PIPELINE_STAGES)
    if cmd.start_stage not in valid_stages:
        raise InvalidStageError(cmd.start_stage)
    if cmd.end_stage not in valid_stages:
        raise InvalidStageError(cmd.end_stage)
    start_idx = PIPELINE_STAGES.index(cmd.start_stage)
    end_idx = PIPELINE_STAGES.index(cmd.end_stage)
    if start_idx > end_idx:
        raise InvalidStageError(
            f"start_stage '{cmd.start_stage}' is after end_stage '{cmd.end_stage}'"
        )


def start_pipeline(conn: Any, cmd: StartPipelineCommand) -> PipelineView:
    """Validate, create, and record the pipeline execution. Returns PipelineView."""
    # Idempotency: return existing if key already used.
    existing = pipeline_state.get_pipeline_by_idempotency_key(conn, cmd.idempotency_key)
    if existing is not None:
        raise PipelineAlreadyExistsError(cmd.idempotency_key, existing.id)

    validate_start_command(conn, cmd)

    # Policy snapshot for audit.
    policy_snap = snapshot_policy_refs(
        conn,
        cmd.workspace_id,
        channel_id=cmd.channel_id,
        platform_account_id=cmd.platform_account_id,
    )

    correlation_id = cmd.correlation_id or str(uuid.uuid4())

    # Determine the stage slice to run.
    start_idx = PIPELINE_STAGES.index(cmd.start_stage)
    end_idx = PIPELINE_STAGES.index(cmd.end_stage)
    stages_to_run = PIPELINE_STAGES[start_idx : end_idx + 1]

    pipeline_id = pipeline_state.create_pipeline(
        conn,
        workspace_id=cmd.workspace_id,
        channel_id=cmd.channel_id,
        topic_id=cmd.topic_id,
        actor=cmd.actor,
        idempotency_key=cmd.idempotency_key,
        start_stage=cmd.start_stage,
        end_stage=cmd.end_stage,
        correlation_id=correlation_id,
        platform_account_id=cmd.platform_account_id,
        experiment_id=cmd.experiment_id,
        policy_snapshot=policy_snap,
    )
    pipeline_state.update_pipeline_status(conn, pipeline_id, "running")

    pipeline_state.create_stage_entries(conn, pipeline_id, stages_to_run, first=cmd.start_stage)

    _emit_event(
        conn,
        "pipeline.started",
        cmd.workspace_id,
        cmd.actor,
        {
            "pipeline_id": pipeline_id,
            "start_stage": cmd.start_stage,
            "end_stage": cmd.end_stage,
            "topic_id": cmd.topic_id,
        },
        correlation_id=correlation_id,
    )

    return pipeline_state.get_pipeline(conn, pipeline_id)


def advance_pipeline(conn: Any, cmd: AdvancePipelineStageCommand) -> PipelineView:
    """Mark a stage completed, transition to next or complete the pipeline."""
    pv = pipeline_state.get_pipeline(conn, cmd.pipeline_id)
    _assert_workspace_owns_pipeline(pv, cmd.workspace_id)

    if cmd.stage not in PIPELINE_STAGES:
        raise InvalidStageError(cmd.stage)

    _check_prerequisites(pv, cmd.stage)

    pipeline_state.advance_stage(
        conn,
        cmd.pipeline_id,
        cmd.stage,
        artifact_id=cmd.artifact_id,
        artifact_type=cmd.artifact_type,
    )

    # If stage requires review, park there until approved.
    if cmd.stage in REVIEW_REQUIRED_STAGES:
        pipeline_state.set_stage_waiting_for_review(conn, cmd.pipeline_id, cmd.stage)
        _emit_event(
            conn,
            "pipeline.waiting_for_review",
            cmd.workspace_id,
            cmd.actor,
            {"pipeline_id": cmd.pipeline_id, "stage": cmd.stage},
            correlation_id=pv.correlation_id,
        )
        return pipeline_state.get_pipeline(conn, cmd.pipeline_id)

    # Advance to next stage or complete.
    next_stage = _next_stage(pv, cmd.stage)
    if next_stage is None:
        pipeline_state.update_pipeline_status(conn, cmd.pipeline_id, "completed")
        _emit_event(
            conn,
            "pipeline.completed",
            cmd.workspace_id,
            cmd.actor,
            {"pipeline_id": cmd.pipeline_id},
            correlation_id=pv.correlation_id,
        )
    else:
        pipeline_state.update_pipeline_status(
            conn, cmd.pipeline_id, "running", current_stage=next_stage
        )
        _emit_event(
            conn,
            "pipeline.stage_started",
            cmd.workspace_id,
            cmd.actor,
            {"pipeline_id": cmd.pipeline_id, "stage": next_stage},
            correlation_id=pv.correlation_id,
        )

    return pipeline_state.get_pipeline(conn, cmd.pipeline_id)


def fail_pipeline(conn: Any, cmd: FailPipelineStageCommand) -> PipelineView:
    pv = pipeline_state.get_pipeline(conn, cmd.pipeline_id)
    _assert_workspace_owns_pipeline(pv, cmd.workspace_id)
    pipeline_state.fail_stage(conn, cmd.pipeline_id, cmd.stage, error_message=cmd.error_message)
    _emit_event(
        conn,
        "pipeline.stage_failed",
        cmd.workspace_id,
        cmd.actor,
        {"pipeline_id": cmd.pipeline_id, "stage": cmd.stage, "error": cmd.error_message},
        correlation_id=pv.correlation_id,
    )
    return pipeline_state.get_pipeline(conn, cmd.pipeline_id)


def pause_pipeline(conn: Any, pipeline_id: str, workspace_id: str, actor: str) -> PipelineView:
    pv = pipeline_state.get_pipeline(conn, pipeline_id)
    _assert_workspace_owns_pipeline(pv, workspace_id)
    pipeline_state.update_pipeline_status(conn, pipeline_id, "paused")
    _emit_event(
        conn,
        "pipeline.paused",
        workspace_id,
        actor,
        {"pipeline_id": pipeline_id},
        correlation_id=pv.correlation_id,
    )
    return pipeline_state.get_pipeline(conn, pipeline_id)


def resume_pipeline(conn: Any, pipeline_id: str, workspace_id: str, actor: str) -> PipelineView:
    pv = pipeline_state.get_pipeline(conn, pipeline_id)
    _assert_workspace_owns_pipeline(pv, workspace_id)
    pipeline_state.update_pipeline_status(conn, pipeline_id, "running")
    _emit_event(
        conn,
        "pipeline.resumed",
        workspace_id,
        actor,
        {"pipeline_id": pipeline_id},
        correlation_id=pv.correlation_id,
    )
    return pipeline_state.get_pipeline(conn, pipeline_id)


def cancel_pipeline(
    conn: Any, pipeline_id: str, workspace_id: str, actor: str, reason: str = ""
) -> PipelineView:
    pv = pipeline_state.get_pipeline(conn, pipeline_id)
    _assert_workspace_owns_pipeline(pv, workspace_id)
    pipeline_state.update_pipeline_status(
        conn, pipeline_id, "cancelled", error_message=reason or None
    )
    _emit_event(
        conn,
        "pipeline.cancelled",
        workspace_id,
        actor,
        {"pipeline_id": pipeline_id, "reason": reason},
        correlation_id=pv.correlation_id,
    )
    return pipeline_state.get_pipeline(conn, pipeline_id)


def execute_pipeline_stage(
    conn: Any,
    cmd: ExecutePipelineStageCommand,
    *,
    executor_registry: Any | None = None,
) -> PipelineView:
    """Invoke the registered executor for a pipeline stage.

    18-step execution flow:
     1  Load pipeline and verify workspace scope
     2  Verify legal stage name
     3  Verify prerequisites
     4  Verify stage is in running status (not waiting_for_review, completed, etc.)
     5  Resolve executor from registry (fails closed on unknown/disabled stage)
     6  Build StageExecutionRequest with current pipeline context
     7  Collect prerequisite artifact references from prior completed stages
     8  Invoke executor — executor returns typed StageExecutionResult
     9  Persist artifact reference on stage row
    10  Transition stage status based on result (completed/waiting_for_review/failed/blocked)
    11  Update pipeline status
    12  Emit immutable CP event
    13  Return updated PipelineView

    The executor itself handles engine invocation and idempotency internally.
    Blocked/deferred executors return status='blocked' without side effects.
    """
    from app.application.executor import (
        StageExecutionRequest,
        get_default_executor_registry,
    )

    pv = pipeline_state.get_pipeline(conn, cmd.pipeline_id)
    _assert_workspace_owns_pipeline(pv, cmd.workspace_id)

    if cmd.stage not in PIPELINE_STAGES:
        raise InvalidStageError(cmd.stage)

    _check_prerequisites(pv, cmd.stage)

    # Verify stage is currently in a runnable state.
    stage_view = next((s for s in pv.stages if s.stage == cmd.stage), None)
    if stage_view is None:
        raise InvalidStageError(f"Stage '{cmd.stage}' not in this pipeline slice")

    # Resolve executor — fails closed if not registered or disabled.
    registry = executor_registry or get_default_executor_registry()
    try:
        executor = registry.get(cmd.stage)
    except ExecutorNotFoundError:
        _emit_event(
            conn,
            "pipeline.executor_not_found",
            cmd.workspace_id,
            cmd.actor,
            {"pipeline_id": cmd.pipeline_id, "stage": cmd.stage},
            correlation_id=pv.correlation_id,
        )
        raise
    except ExecutorDisabledError:
        _emit_event(
            conn,
            "pipeline.executor_disabled",
            cmd.workspace_id,
            cmd.actor,
            {"pipeline_id": cmd.pipeline_id, "stage": cmd.stage},
            correlation_id=pv.correlation_id,
        )
        raise

    # Collect prerequisite artifact IDs from completed prior stages.
    prereq_artifact_ids: dict[str, str] = {
        s.stage: s.artifact_id
        for s in pv.stages
        if s.status == "completed" and s.artifact_id is not None
    }
    # Merge caller-provided overrides (e.g. tests supplying exact IDs).
    if cmd.prerequisite_artifact_ids:
        prereq_artifact_ids.update(cmd.prerequisite_artifact_ids)

    req = StageExecutionRequest(
        pipeline_execution_id=cmd.pipeline_id,
        workspace_id=cmd.workspace_id,
        channel_id=pv.channel_id,
        platform_account_id=pv.platform_account_id,
        topic_id=pv.topic_id,
        stage=cmd.stage,
        attempt_number=stage_view.attempt_number,
        correlation_id=pv.correlation_id,
        causation_event_id=None,
        actor=cmd.actor,
        experiment_id=pv.experiment_id,
        idempotency_key=f"{cmd.pipeline_id}:{cmd.stage}:{stage_view.attempt_number}",
        prerequisite_artifact_ids=prereq_artifact_ids,
        effective_config=cmd.effective_config or {},
    )

    result = executor.execute(conn, req)

    # Persist result and transition stage state.
    if result.status == "completed":
        pipeline_state.advance_stage(
            conn,
            cmd.pipeline_id,
            cmd.stage,
            artifact_id=result.artifact_id,
            artifact_type=result.artifact_type,
        )
        next_stage = _next_stage(pv, cmd.stage)
        if next_stage is None:
            pipeline_state.update_pipeline_status(conn, cmd.pipeline_id, "completed")
            _emit_event(
                conn,
                "pipeline.completed",
                cmd.workspace_id,
                cmd.actor,
                {"pipeline_id": cmd.pipeline_id},
                correlation_id=pv.correlation_id,
            )
        else:
            pipeline_state.update_pipeline_status(
                conn, cmd.pipeline_id, "running", current_stage=next_stage
            )
            _emit_event(
                conn,
                "pipeline.stage_started",
                cmd.workspace_id,
                cmd.actor,
                {"pipeline_id": cmd.pipeline_id, "stage": next_stage},
                correlation_id=pv.correlation_id,
            )

    elif result.status == "waiting_for_review":
        pipeline_state.advance_stage(
            conn,
            cmd.pipeline_id,
            cmd.stage,
            artifact_id=result.artifact_id,
            artifact_type=result.artifact_type,
        )
        pipeline_state.set_stage_waiting_for_review(conn, cmd.pipeline_id, cmd.stage)
        _emit_event(
            conn,
            "pipeline.waiting_for_review",
            cmd.workspace_id,
            cmd.actor,
            {
                "pipeline_id": cmd.pipeline_id,
                "stage": cmd.stage,
                "artifact_type": result.artifact_type,
                "artifact_id": result.artifact_id,
            },
            correlation_id=pv.correlation_id,
        )

    elif result.status == "failed":
        pipeline_state.fail_stage(
            conn,
            cmd.pipeline_id,
            cmd.stage,
            error_message=result.error_message or "executor reported failure",
        )
        _emit_event(
            conn,
            "pipeline.stage_failed",
            cmd.workspace_id,
            cmd.actor,
            {
                "pipeline_id": cmd.pipeline_id,
                "stage": cmd.stage,
                "error_category": result.error_category,
                "error": result.error_message,
            },
            correlation_id=pv.correlation_id,
        )

    else:  # blocked
        pipeline_state.update_pipeline_status(
            conn,
            cmd.pipeline_id,
            "blocked",
            blocked_reason=result.error_message or "executor blocked",
        )
        _emit_event(
            conn,
            "pipeline.stage_blocked",
            cmd.workspace_id,
            cmd.actor,
            {
                "pipeline_id": cmd.pipeline_id,
                "stage": cmd.stage,
                "error_category": result.error_category,
                "reason": result.error_message,
            },
            correlation_id=pv.correlation_id,
        )

    return pipeline_state.get_pipeline(conn, cmd.pipeline_id)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _assert_workspace_owns_pipeline(pv: PipelineView, workspace_id: str) -> None:
    if pv.workspace_id != workspace_id:
        raise CrossWorkspaceAccessError("Pipeline", pv.id, workspace_id)


def _check_prerequisites(pv: PipelineView, stage: str) -> None:
    prereqs = STAGE_PREREQUISITES.get(stage, [])
    if not prereqs:
        return
    pipeline_stage_names = {s.stage for s in pv.stages}
    # Only require prerequisites that are actually part of this pipeline slice.
    required = [p for p in prereqs if p in pipeline_stage_names]
    if not required:
        return
    completed = {s.stage for s in pv.stages if s.status == "completed"}
    missing = [p for p in required if p not in completed]
    if missing:
        raise StagePrerequisiteError(stage, missing)


def _next_stage(pv: PipelineView, completed_stage: str) -> str | None:
    """Return the next stage in the pipeline slice, or None if done."""
    stage_names = [s.stage for s in pv.stages]
    try:
        idx = stage_names.index(completed_stage)
    except ValueError:
        return None
    if idx + 1 < len(stage_names):
        return stage_names[idx + 1]
    return None
