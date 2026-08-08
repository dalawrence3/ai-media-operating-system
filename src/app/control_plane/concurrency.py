"""Concurrency limit enforcement for CP operations.

Counts active operations per workspace/channel/account against a configurable
limit. No distributed locks required — enforced at the SQLite query boundary.
"""

from __future__ import annotations

from typing import Any

from app.control_plane.errors import ControlPlaneError

DEFAULT_MAX_CONCURRENT_OPERATIONS = 10


class ConcurrencyLimitExceededError(ControlPlaneError):
    def __init__(
        self, scope: str, scope_id: str, operation_type: str, current: int, limit: int
    ) -> None:
        super().__init__(
            f"Concurrency limit exceeded for {scope} {scope_id}: "
            f"{current} active '{operation_type}' operations (limit {limit})"
        )
        self.scope = scope
        self.scope_id = scope_id
        self.operation_type = operation_type
        self.current = current
        self.limit = limit


def count_active_operations(
    conn: Any,
    workspace_id: str,
    operation_type: str | None = None,
    channel_id: str | None = None,
    platform_account_id: str | None = None,
) -> int:
    query = (
        "SELECT COUNT(*) FROM cp_operation_executions "
        "WHERE workspace_id = ? AND status IN ('pending', 'running')"
    )
    params: list[Any] = [workspace_id]
    if operation_type:
        query += " AND operation_type = ?"
        params.append(operation_type)
    if channel_id:
        query += " AND channel_id = ?"
        params.append(channel_id)
    if platform_account_id:
        query += " AND platform_account_id = ?"
        params.append(platform_account_id)
    row = conn.execute(query, params).fetchone()
    return int(row[0]) if row else 0


def check_concurrency_limit(
    conn: Any,
    workspace_id: str,
    operation_type: str = "any",
    limit: int = DEFAULT_MAX_CONCURRENT_OPERATIONS,
    channel_id: str | None = None,
    platform_account_id: str | None = None,
) -> None:
    """Raise ConcurrencyLimitExceededError if the active operation count is at or above limit."""
    actual_op_type = None if operation_type == "any" else operation_type
    current = count_active_operations(
        conn,
        workspace_id,
        operation_type=actual_op_type,
        channel_id=channel_id,
        platform_account_id=platform_account_id,
    )
    if current >= limit:
        if platform_account_id:
            scope = "platform_account"
        elif channel_id:
            scope = "channel"
        else:
            scope = "workspace"
        scope_id = platform_account_id or channel_id or workspace_id
        raise ConcurrencyLimitExceededError(scope, scope_id, operation_type, current, limit)
