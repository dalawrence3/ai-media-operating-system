"""Job control — create and track CP operations with idempotency."""

from __future__ import annotations

import uuid
from typing import Any

from app.control_plane import repository as repo
from app.control_plane.hashing import OperationHashInput, compute_operation_idempotency_key
from app.control_plane.models import OperationExecution, OperationExecutionDraft


def start_operation(
    conn: Any,
    *,
    operation_type: str,
    workspace_id: str,
    actor: str,
    input_data: dict[str, Any] | None = None,
    channel_id: str | None = None,
    platform_account_id: str | None = None,
    correlation_id: str | None = None,
    source_event_id: str | None = None,
    idempotency_key: str | None = None,
) -> OperationExecution:
    if idempotency_key is None:
        idempotency_key = compute_operation_idempotency_key(
            OperationHashInput(
                operation_type=operation_type,
                workspace_id=workspace_id,
                actor=actor,
                input_data=input_data,
            )
        )

    existing = repo.get_operation_by_idempotency_key(conn, idempotency_key)
    if existing:
        return existing

    draft = OperationExecutionDraft(
        id=str(uuid.uuid4()),
        operation_type=operation_type,
        workspace_id=workspace_id,
        idempotency_key=idempotency_key,
        actor=actor,
        channel_id=channel_id,
        platform_account_id=platform_account_id,
        correlation_id=correlation_id,
        source_event_id=source_event_id,
        input_data=input_data,
    )
    return repo.create_operation_execution(conn, draft)


def complete_operation(
    conn: Any,
    operation_id: str,
    output_data: dict[str, Any] | None = None,
) -> OperationExecution:
    return repo.update_operation_status(conn, operation_id, "completed", output_data=output_data)


def fail_operation(conn: Any, operation_id: str, error_message: str) -> OperationExecution:
    return repo.update_operation_status(conn, operation_id, "failed", error_message=error_message)


def supersede_operation(conn: Any, operation_id: str) -> OperationExecution:
    return repo.update_operation_status(conn, operation_id, "superseded")
