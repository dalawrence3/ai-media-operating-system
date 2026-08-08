"""Automation policy resolution and enforcement."""

from __future__ import annotations

import uuid
from typing import Any

from app.control_plane import repository as repo
from app.control_plane.constants import (
    AUTOMATION_AUTONOMOUS,
    AUTOMATION_LEVELS,
    AUTOMATION_MANUAL,
    AUTOMATION_SUPERVISED,
    POLICY_SCOPE_CHANNEL,
    POLICY_SCOPE_PLATFORM_ACCOUNT,
    POLICY_SCOPE_WORKSPACE,
)
from app.control_plane.errors import InvalidAutomationLevelError, PolicyViolationError
from app.control_plane.models import AutomationPolicy, AutomationPolicyDraft


def _level_rank(level: str) -> int:
    return {AUTOMATION_MANUAL: 0, AUTOMATION_SUPERVISED: 1, AUTOMATION_AUTONOMOUS: 2}[level]


def set_policy(
    conn: Any,
    *,
    scope: str,
    scope_id: str,
    automation_level: str,
    allowed_actions: list[str],
    actor: str,
) -> AutomationPolicy:
    if automation_level not in AUTOMATION_LEVELS:
        raise InvalidAutomationLevelError(automation_level)
    draft = AutomationPolicyDraft(
        id=str(uuid.uuid4()),
        scope=scope,
        scope_id=scope_id,
        automation_level=automation_level,
        allowed_actions=allowed_actions,
        actor=actor,
    )
    return repo.create_automation_policy(conn, draft)


def resolve_effective_level(
    conn: Any,
    workspace_id: str,
    channel_id: str | None = None,
    platform_account_id: str | None = None,
) -> str:
    """Return the most restrictive automation level across workspace/channel/account hierarchy."""
    levels: list[str] = []

    ws_policy = repo.get_active_policy_for_scope(conn, POLICY_SCOPE_WORKSPACE, workspace_id)
    if ws_policy:
        levels.append(ws_policy.automation_level)

    if channel_id:
        ch_policy = repo.get_active_policy_for_scope(conn, POLICY_SCOPE_CHANNEL, channel_id)
        if ch_policy:
            levels.append(ch_policy.automation_level)

    if platform_account_id:
        acc_policy = repo.get_active_policy_for_scope(
            conn, POLICY_SCOPE_PLATFORM_ACCOUNT, platform_account_id
        )
        if acc_policy:
            levels.append(acc_policy.automation_level)

    if not levels:
        return AUTOMATION_MANUAL

    return min(levels, key=_level_rank)


def assert_action_permitted(
    conn: Any,
    action: str,
    workspace_id: str,
    channel_id: str | None = None,
    platform_account_id: str | None = None,
) -> None:
    """Raise PolicyViolationError if action is not permitted under the effective policy."""
    effective_level = resolve_effective_level(conn, workspace_id, channel_id, platform_account_id)
    if effective_level == AUTOMATION_MANUAL:
        raise PolicyViolationError(AUTOMATION_SUPERVISED, AUTOMATION_MANUAL)

    if effective_level == AUTOMATION_SUPERVISED:
        ws_policy = repo.get_active_policy_for_scope(conn, POLICY_SCOPE_WORKSPACE, workspace_id)
        if ws_policy and action not in ws_policy.allowed_actions:
            raise PolicyViolationError(AUTOMATION_AUTONOMOUS, AUTOMATION_SUPERVISED)
