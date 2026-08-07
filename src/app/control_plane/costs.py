"""Budget policy enforcement and spend checks."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.control_plane import repository as repo
from app.control_plane.constants import (
    BUDGET_ACTION_BLOCK,
    BUDGET_ACTION_PAUSE,
    BUDGET_ACTION_WARN,
    BUDGET_PERIOD_DAILY,
    BUDGET_PERIOD_WEEKLY,
    BUDGET_SCOPE_CHANNEL,
    BUDGET_SCOPE_PLATFORM_ACCOUNT,
    BUDGET_SCOPE_WORKSPACE,
    BUDGET_WARNING_THRESHOLD,
)
from app.control_plane.errors import BudgetExceededError
from app.control_plane.models import BudgetPolicy, BudgetPolicyDraft


def _period_start(period: str) -> datetime:
    now = datetime.now(UTC)
    if period == BUDGET_PERIOD_DAILY:
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == BUDGET_PERIOD_WEEKLY:
        start = now - timedelta(days=now.weekday())
        return start.replace(hour=0, minute=0, second=0, microsecond=0)
    # monthly
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def set_budget(
    conn: Any,
    *,
    scope: str,
    scope_id: str,
    period: str,
    limit_usd: float,
    actor: str,
    warning_threshold: float = BUDGET_WARNING_THRESHOLD,
    on_exceed_action: str = BUDGET_ACTION_WARN,
) -> BudgetPolicy:
    draft = BudgetPolicyDraft(
        id=str(uuid.uuid4()),
        scope=scope,
        scope_id=scope_id,
        period=period,
        limit_usd=limit_usd,
        actor=actor,
        warning_threshold=warning_threshold,
        on_exceed_action=on_exceed_action,
    )
    return repo.create_budget_policy(conn, draft)


def check_budget(
    conn: Any,
    workspace_id: str,
    channel_id: str | None = None,
    platform_account_id: str | None = None,
    projected_usd: float = 0.0,
) -> dict[str, Any]:
    """Check spend against active budgets.

    Returns a summary dict; raises BudgetExceededError on block.
    """
    warnings: list[str] = []
    checks = [
        (
            BUDGET_SCOPE_WORKSPACE,
            workspace_id,
            lambda s: repo.sum_cost_usd_by_workspace(conn, workspace_id, s),
        ),
    ]
    if channel_id:
        checks.append(
            (
                BUDGET_SCOPE_CHANNEL,
                channel_id,
                lambda s: repo.sum_cost_usd_by_channel(conn, channel_id, s),
            )
        )
    if platform_account_id:
        checks.append(
            (BUDGET_SCOPE_PLATFORM_ACCOUNT, platform_account_id,
             lambda s: repo.sum_cost_usd_by_account(conn, platform_account_id, s))
        )

    for scope, scope_id, spend_fn in checks:
        policy = repo.get_active_budget_for_scope(conn, scope, scope_id)
        if not policy:
            continue
        since = _period_start(policy.period)
        current = spend_fn(since) + projected_usd
        ratio = current / policy.limit_usd if policy.limit_usd > 0 else 0.0

        if ratio >= 1.0:
            if policy.on_exceed_action == BUDGET_ACTION_BLOCK:
                raise BudgetExceededError(scope, scope_id, policy.limit_usd, current)
            if policy.on_exceed_action == BUDGET_ACTION_PAUSE:
                warnings.append(f"budget_exceeded:{scope}:{scope_id}")
            else:
                warnings.append(f"budget_exceeded:{scope}:{scope_id}")
        elif ratio >= policy.warning_threshold:
            warnings.append(f"budget_warning:{scope}:{scope_id}:{ratio:.2f}")

    return {"ok": True, "warnings": warnings}
