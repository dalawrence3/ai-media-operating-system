"""Workflow definition and run management."""

from __future__ import annotations

import uuid
from typing import Any

from app.control_plane import repository as repo
from app.control_plane.constants import (
    WORKFLOW_ACTION_TYPES,
    WORKFLOW_CONDITION_OPERATORS,
    WORKFLOW_STATUS_ACTIVE,
    WORKFLOW_STATUS_DRAFT,
)
from app.control_plane.errors import (
    InvalidWorkflowActionError,
    InvalidWorkflowConditionError,
)
from app.control_plane.models import Workflow, WorkflowDraft, WorkflowRun


def _validate_conditions(conditions: list[dict[str, Any]]) -> None:
    for cond in conditions:
        op = cond.get("operator")
        if op not in WORKFLOW_CONDITION_OPERATORS:
            raise InvalidWorkflowConditionError(
                f"Unsupported condition operator: {op!r}"
            )
        if "field" not in cond:
            raise InvalidWorkflowConditionError("Condition missing 'field'")


def _validate_actions(actions: list[dict[str, Any]]) -> None:
    for act in actions:
        action_type = act.get("action_type")
        if action_type not in WORKFLOW_ACTION_TYPES:
            raise InvalidWorkflowActionError(
                f"Unsupported action type: {action_type!r}"
            )


def create_workflow(
    conn: Any,
    *,
    workspace_id: str,
    name: str,
    trigger_event_type: str,
    conditions: list[dict[str, Any]],
    actions: list[dict[str, Any]],
    actor: str,
) -> Workflow:
    _validate_conditions(conditions)
    _validate_actions(actions)
    draft = WorkflowDraft(
        id=str(uuid.uuid4()),
        workspace_id=workspace_id,
        name=name,
        trigger_event_type=trigger_event_type,
        conditions=conditions,
        actions=actions,
        actor=actor,
        status=WORKFLOW_STATUS_DRAFT,
    )
    return repo.create_workflow(conn, draft)


def activate_workflow(conn: Any, workflow_id: str, actor: str) -> Workflow:
    return repo.update_workflow_status(conn, workflow_id, WORKFLOW_STATUS_ACTIVE, actor)


def pause_workflow(conn: Any, workflow_id: str, actor: str) -> Workflow:
    return repo.update_workflow_status(conn, workflow_id, "paused", actor)


def start_workflow_run(
    conn: Any, workflow_id: str, trigger_event_id: str
) -> WorkflowRun:
    return repo.create_workflow_run(conn, str(uuid.uuid4()), workflow_id, trigger_event_id)
