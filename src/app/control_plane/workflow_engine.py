"""Structured workflow engine — condition evaluation and action dispatch (no eval)."""

from __future__ import annotations

from typing import Any

from app.control_plane import repository as repo
from app.control_plane.constants import (
    ACTION_NOTIFY,
    ACTION_PAUSE_ACCOUNT,
    ACTION_QUEUE_REVIEW,
    ACTION_RESUME_ACCOUNT,
    ACTION_TRIGGER_WORKFLOW,
    ACTION_UPDATE_POLICY,
    CONDITION_OP_BOOLEAN,
    CONDITION_OP_EQUALS,
    CONDITION_OP_EXISTS,
    CONDITION_OP_GREATER_THAN,
    CONDITION_OP_IN,
    CONDITION_OP_LESS_THAN,
    CONDITION_OP_NOT_EQUALS,
    CONDITION_OP_NOT_IN,
)
from app.control_plane.models import (
    ControlEvent,
    Workflow,
    WorkflowEvaluationResult,
    WorkflowRun,
    WorkflowRunResult,
)


def _get_nested(payload: dict[str, Any], field: str) -> Any:
    parts = field.split(".")
    current: Any = payload
    for part in parts:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _evaluate_condition(condition: dict[str, Any], payload: dict[str, Any]) -> bool:
    field = condition["field"]
    op = condition["operator"]
    expected = condition.get("value")
    actual = _get_nested(payload, field)

    if op == CONDITION_OP_EXISTS:
        return actual is not None
    if op == CONDITION_OP_BOOLEAN:
        return bool(actual)
    if op == CONDITION_OP_EQUALS:
        return actual == expected
    if op == CONDITION_OP_NOT_EQUALS:
        return actual != expected
    if op == CONDITION_OP_GREATER_THAN:
        try:
            return float(actual) > float(expected)
        except (TypeError, ValueError):
            return False
    if op == CONDITION_OP_LESS_THAN:
        try:
            return float(actual) < float(expected)
        except (TypeError, ValueError):
            return False
    if op == CONDITION_OP_IN:
        return actual in (expected or [])
    if op == CONDITION_OP_NOT_IN:
        return actual not in (expected or [])
    return False


def evaluate_workflow(workflow: Workflow, event: ControlEvent) -> WorkflowEvaluationResult:
    payload = event.payload
    conditions_passed: list[str] = []
    conditions_failed: list[str] = []

    for cond in workflow.conditions:
        label = f"{cond.get('field')}.{cond.get('operator')}"
        if _evaluate_condition(cond, payload):
            conditions_passed.append(label)
        else:
            conditions_failed.append(label)

    matched = len(conditions_failed) == 0
    return WorkflowEvaluationResult(
        workflow_id=workflow.id,
        matched=matched,
        conditions_passed=conditions_passed,
        conditions_failed=conditions_failed,
        actions_to_execute=workflow.actions if matched else [],
    )


def execute_actions(
    conn: Any,
    actions: list[dict[str, Any]],
    event: ControlEvent,
) -> list[str]:
    executed: list[str] = []
    for action in actions:
        action_type = action.get("action_type")
        params = action.get("params", {})

        if action_type == ACTION_PAUSE_ACCOUNT:
            account_id = params.get("platform_account_id")
            if account_id:
                repo.update_platform_account_status(conn, account_id, "paused", actor=event.actor)
                executed.append(f"{ACTION_PAUSE_ACCOUNT}:{account_id}")

        elif action_type == ACTION_RESUME_ACCOUNT:
            account_id = params.get("platform_account_id")
            if account_id:
                repo.update_platform_account_status(
                    conn, account_id, "connected", actor=event.actor
                )
                executed.append(f"{ACTION_RESUME_ACCOUNT}:{account_id}")

        elif action_type == ACTION_NOTIFY:
            message = params.get("message", "")
            executed.append(f"{ACTION_NOTIFY}:{message[:40]}")

        elif action_type == ACTION_QUEUE_REVIEW:
            item_type = params.get("item_type", "workflow_exception")
            executed.append(f"{ACTION_QUEUE_REVIEW}:{item_type}")

        elif action_type == ACTION_UPDATE_POLICY:
            executed.append(f"{ACTION_UPDATE_POLICY}:deferred")

        elif action_type == ACTION_TRIGGER_WORKFLOW:
            target_workflow_id = params.get("workflow_id")
            if target_workflow_id:
                executed.append(f"{ACTION_TRIGGER_WORKFLOW}:{target_workflow_id}")

    return executed


def run_workflow(
    conn: Any,
    workflow: Workflow,
    event: ControlEvent,
    run: WorkflowRun,
) -> WorkflowRunResult:
    evaluation = evaluate_workflow(workflow, event)

    if not evaluation.matched:
        result = repo.complete_workflow_run(conn, run.id, success=True, result={"matched": False})
        return WorkflowRunResult(
            run_id=result.id,
            workflow_id=workflow.id,
            success=True,
            actions_executed=[],
        )

    try:
        executed = execute_actions(conn, evaluation.actions_to_execute, event)
        result = repo.complete_workflow_run(
            conn,
            run.id,
            success=True,
            result={"matched": True, "actions_executed": executed},
        )
        return WorkflowRunResult(
            run_id=result.id,
            workflow_id=workflow.id,
            success=True,
            actions_executed=executed,
        )
    except Exception as exc:
        repo.complete_workflow_run(conn, run.id, success=False, error_message=str(exc))
        return WorkflowRunResult(
            run_id=run.id,
            workflow_id=workflow.id,
            success=False,
            actions_executed=[],
            error_message=str(exc),
        )
