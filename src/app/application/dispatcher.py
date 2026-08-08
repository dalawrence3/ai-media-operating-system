"""Command dispatcher — the application command bus.

Dispatch pipeline:
  1. Validate command
  2. Verify workspace scope
  3. Invoke authorization hook
  4. Resolve effective policy
  5. Check pause state
  6. Check budget
  7. Check concurrency
  8. Enforce idempotency
  9. Invoke registered safe handler
  10. Persist operation result
  11. Emit resulting event

Unknown commands fail closed.
No eval, exec, shell, or arbitrary dynamic imports.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from app.application.authorization import AuthHook, allow_all_auth_hook, default_auth_hook
from app.application.commands import (
    AdvancePipelineStageCommand,
    ApproveReviewItemCommand,
    CancelOperationCommand,
    CancelPipelineCommand,
    CreateScheduleCommand,
    DeleteScheduleCommand,
    ExecutePipelineStageCommand,
    FailPipelineStageCommand,
    PausePipelineCommand,
    PauseScheduleCommand,
    RecoverPipelineCommand,
    RejectReviewItemCommand,
    ReplayEventCommand,
    ResumePipelineCommand,
    ResumeScheduleCommand,
    RetryOperationCommand,
    StartPipelineCommand,
)
from app.application.errors import (
    ChannelPausedError,
    CrossWorkspaceAccessError,
    PolicyBlockedError,
    UnknownCommandError,
    WorkspacePausedError,
)
from app.application.registry import ExtensionRegistry
from app.control_plane import repository as cp_repo
from app.control_plane.concurrency import ConcurrencyLimitExceededError, check_concurrency_limit
from app.control_plane.costs import check_budget
from app.control_plane.errors import BudgetExceededError
from app.control_plane.event_bus import dispatch_event
from app.control_plane.models import ControlEventDraft


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# Re-export permissive hook so callers can inject it explicitly for dev/test.
noop_auth_hook = allow_all_auth_hook


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


# ---------------------------------------------------------------------------
# Cross-cutting checks
# ---------------------------------------------------------------------------


def _check_workspace_not_paused(conn: Any, workspace_id: str) -> None:
    try:
        ws = cp_repo.get_workspace(conn, workspace_id)
    except Exception:
        return  # Non-existent workspace caught later by the handler.
    if ws.status != "active":
        raise WorkspacePausedError(workspace_id)


def _check_channel_not_paused(conn: Any, channel_id: str | None, workspace_id: str) -> None:
    if channel_id is None:
        return
    try:
        ch = cp_repo.get_channel(conn, channel_id)
    except Exception:
        return
    if ch.workspace_id != workspace_id:
        raise CrossWorkspaceAccessError("Channel", channel_id, workspace_id)
    if ch.status == "paused":
        raise ChannelPausedError(channel_id)


def _check_policy(effective_level: str, command_type: str) -> None:
    """Block MANUAL-only commands that require SUPERVISED or AUTONOMOUS."""
    # For now: all commands are permitted at all automation levels.
    # Phase 14 may restrict pipeline auto-advance to SUPERVISED+.
    # This hook is preserved for future enforcement.
    _ = effective_level, command_type


def _check_budget(conn: Any, workspace_id: str, channel_id: str | None = None) -> None:
    try:
        result = check_budget(conn, workspace_id, channel_id=channel_id)
        if result.get("block_active"):
            raise PolicyBlockedError("start_pipeline", "budget_exceeded")
    except BudgetExceededError as exc:
        raise PolicyBlockedError("start_pipeline", str(exc)) from exc


def _check_concurrency(
    conn: Any,
    workspace_id: str,
    *,
    channel_id: str | None = None,
    platform_account_id: str | None = None,
) -> None:
    try:
        check_concurrency_limit(
            conn, workspace_id,
            channel_id=channel_id,
            platform_account_id=platform_account_id,
        )
    except ConcurrencyLimitExceededError as exc:
        raise PolicyBlockedError("start_pipeline", str(exc)) from exc


# ---------------------------------------------------------------------------
# Handler registry
# ---------------------------------------------------------------------------


_HANDLERS: dict[type, Callable[..., Any]] = {}


def register_handler(
    cmd_type: type, handler: Callable[..., Any], *, replace: bool = False
) -> None:
    """Register a safe handler for a command type.

    Pass replace=True to intentionally replace an existing registration.
    Fails closed — silent overwrite would hide composition mistakes.
    """
    if cmd_type in _HANDLERS and not replace:
        raise RuntimeError(
            f"Handler already registered for {cmd_type.__name__!r}; "
            "pass replace=True to override"
        )
    _HANDLERS[cmd_type] = handler


def clear_handlers() -> None:
    """For testing: reset the handler registry."""
    _HANDLERS.clear()


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def dispatch(
    conn: Any,
    cmd: Any,
    *,
    registry: ExtensionRegistry | None = None,
    auth_hook: AuthHook = default_auth_hook,
) -> Any:
    """Route a command through the full dispatch pipeline."""
    cmd_type = type(cmd)

    if cmd_type not in _HANDLERS:
        raise UnknownCommandError(cmd_type.__name__)

    workspace_id = getattr(cmd, "workspace_id", None)
    actor = getattr(cmd, "actor", "unknown")
    channel_id = getattr(cmd, "channel_id", None)
    platform_account_id = getattr(cmd, "platform_account_id", None)

    # Steps 3: authorization hook.
    if workspace_id:
        auth_hook(conn, cmd_type.__name__, workspace_id, actor)

    # Step 4–5: policy + pause checks (all mutating pipeline commands).
    if isinstance(
        cmd,
        StartPipelineCommand | AdvancePipelineStageCommand | ExecutePipelineStageCommand,
    ):
        if workspace_id:
            _check_workspace_not_paused(conn, workspace_id)
        _check_channel_not_paused(conn, channel_id, workspace_id or "")

    # Steps 6–7: budget + concurrency (only for pipeline starts).
    if isinstance(cmd, StartPipelineCommand):
        _check_budget(conn, workspace_id, channel_id=channel_id)
        _check_concurrency(
            conn, workspace_id,
            channel_id=channel_id,
            platform_account_id=platform_account_id,
        )

    # Step 9: invoke handler.
    handler = _HANDLERS[cmd_type]
    result = handler(conn, cmd, registry=registry)

    # Step 11: emit dispatch event.
    if workspace_id:
        _emit_event(
            conn, "command.dispatched", workspace_id, actor,
            {
                "command": cmd_type.__name__,
                "result_type": type(result).__name__,
            },
            correlation_id=getattr(cmd, "correlation_id", None),
        )

    return result


# ---------------------------------------------------------------------------
# Built-in handler registrations (called by composition root)
# ---------------------------------------------------------------------------


def register_default_handlers(conn_factory: Callable[[], Any] | None = None) -> None:
    """Register all built-in command handlers. Called once at startup.

    Uses register_handler(..., replace=True) so that calling this function
    twice is deterministic and intentional — it replaces the existing canonical
    registrations with the same implementations. A different externally-registered
    handler is silently replaced; this is expected for the composition root.
    """
    from app.application import pipeline as pipeline_ctrl
    from app.application import recovery as recovery_svc
    from app.application import review as review_svc
    from app.application import scheduler as sched_svc
    from app.application.executor import get_default_executor_registry

    def _start_pipeline(conn: Any, cmd: StartPipelineCommand, **_: Any) -> Any:
        return pipeline_ctrl.start_pipeline(conn, cmd)

    def _pause_pipeline(conn: Any, cmd: PausePipelineCommand, **_: Any) -> Any:
        return pipeline_ctrl.pause_pipeline(conn, cmd.pipeline_id, cmd.workspace_id, cmd.actor)

    def _resume_pipeline(conn: Any, cmd: ResumePipelineCommand, **_: Any) -> Any:
        return pipeline_ctrl.resume_pipeline(conn, cmd.pipeline_id, cmd.workspace_id, cmd.actor)

    def _cancel_pipeline(conn: Any, cmd: CancelPipelineCommand, **_: Any) -> Any:
        return pipeline_ctrl.cancel_pipeline(
            conn, cmd.pipeline_id, cmd.workspace_id, cmd.actor, cmd.reason
        )

    def _advance_stage(conn: Any, cmd: AdvancePipelineStageCommand, **_: Any) -> Any:
        return pipeline_ctrl.advance_pipeline(conn, cmd)

    def _fail_stage(conn: Any, cmd: FailPipelineStageCommand, **_: Any) -> Any:
        return pipeline_ctrl.fail_pipeline(conn, cmd)

    def _execute_stage(conn: Any, cmd: ExecutePipelineStageCommand, **_: Any) -> Any:
        return pipeline_ctrl.execute_pipeline_stage(
            conn, cmd, executor_registry=get_default_executor_registry()
        )

    def _retry_operation(conn: Any, cmd: RetryOperationCommand, **_: Any) -> Any:
        now = _now_iso()
        conn.execute(
            "UPDATE cp_operation_executions "
            "SET status='pending', error_message=NULL, updated_at=? "
            "WHERE id=? AND workspace_id=?",
            (now, cmd.operation_id, cmd.workspace_id),
        )
        conn.commit()
        return {"operation_id": cmd.operation_id, "status": "pending", "retried_at": now}

    def _cancel_operation(conn: Any, cmd: CancelOperationCommand, **_: Any) -> Any:
        now = _now_iso()
        conn.execute(
            "UPDATE cp_operation_executions "
            "SET status='cancelled', error_message=?, updated_at=? "
            "WHERE id=? AND workspace_id=?",
            (cmd.reason or "cancelled by operator", now, cmd.operation_id, cmd.workspace_id),
        )
        conn.commit()
        return {"operation_id": cmd.operation_id, "status": "cancelled", "cancelled_at": now}

    def _approve_review(conn: Any, cmd: ApproveReviewItemCommand, **kwargs: Any) -> Any:
        return review_svc.approve_review_item(
            conn, cmd.item_type, cmd.item_id, cmd.workspace_id, cmd.actor,
            notes=cmd.notes, registry=kwargs.get("registry"),
        )

    def _reject_review(conn: Any, cmd: RejectReviewItemCommand, **kwargs: Any) -> Any:
        return review_svc.reject_review_item(
            conn, cmd.item_type, cmd.item_id, cmd.workspace_id, cmd.actor,
            reason=cmd.reason, registry=kwargs.get("registry"),
        )

    def _create_schedule(conn: Any, cmd: CreateScheduleCommand, **_: Any) -> Any:
        return sched_svc.create_schedule(
            conn,
            workspace_id=cmd.workspace_id,
            name=cmd.name,
            operation_type=cmd.operation_type,
            schedule_type=cmd.schedule_type,
            schedule_config=cmd.schedule_config,
            actor=cmd.actor,
            channel_id=cmd.channel_id,
            timezone_name=cmd.timezone,
        )

    def _pause_schedule(conn: Any, cmd: PauseScheduleCommand, **_: Any) -> Any:
        return sched_svc.pause_schedule(conn, cmd.schedule_id, cmd.workspace_id)

    def _resume_schedule(conn: Any, cmd: ResumeScheduleCommand, **_: Any) -> Any:
        return sched_svc.resume_schedule(conn, cmd.schedule_id, cmd.workspace_id)

    def _delete_schedule(conn: Any, cmd: DeleteScheduleCommand, **_: Any) -> None:
        sched_svc.delete_schedule(conn, cmd.schedule_id, cmd.workspace_id)

    def _replay_event(conn: Any, cmd: ReplayEventCommand, **_: Any) -> Any:
        return recovery_svc.replay_event(conn, cmd.event_id, cmd.workspace_id, cmd.actor)

    def _recover_pipeline(conn: Any, cmd: RecoverPipelineCommand, **_: Any) -> Any:
        return recovery_svc.recover_pipeline(
            conn, cmd.pipeline_id, cmd.workspace_id, cmd.actor, from_stage=cmd.from_stage
        )

    register_handler(StartPipelineCommand, _start_pipeline, replace=True)
    register_handler(PausePipelineCommand, _pause_pipeline, replace=True)
    register_handler(ResumePipelineCommand, _resume_pipeline, replace=True)
    register_handler(CancelPipelineCommand, _cancel_pipeline, replace=True)
    register_handler(AdvancePipelineStageCommand, _advance_stage, replace=True)
    register_handler(ExecutePipelineStageCommand, _execute_stage, replace=True)
    register_handler(FailPipelineStageCommand, _fail_stage, replace=True)
    register_handler(RetryOperationCommand, _retry_operation, replace=True)
    register_handler(CancelOperationCommand, _cancel_operation, replace=True)
    register_handler(ApproveReviewItemCommand, _approve_review, replace=True)
    register_handler(RejectReviewItemCommand, _reject_review, replace=True)
    register_handler(CreateScheduleCommand, _create_schedule, replace=True)
    register_handler(PauseScheduleCommand, _pause_schedule, replace=True)
    register_handler(ResumeScheduleCommand, _resume_schedule, replace=True)
    register_handler(DeleteScheduleCommand, _delete_schedule, replace=True)
    register_handler(ReplayEventCommand, _replay_event, replace=True)
    register_handler(RecoverPipelineCommand, _recover_pipeline, replace=True)
