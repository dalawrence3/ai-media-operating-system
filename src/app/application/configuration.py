"""Effective configuration hierarchy resolution.

Resolves configuration deterministically from:
  system defaults → organization → workspace → channel → platform account
  → runtime overrides

No secrets are placed in resolved configuration snapshots.
"""

from __future__ import annotations

import json
from typing import Any

from app.control_plane import repository as cp_repo
from app.control_plane import services as cp_services
from app.control_plane.constants import AUTOMATION_MANUAL

# System defaults — the lowest-priority layer.
SYSTEM_DEFAULTS: dict[str, Any] = {
    "max_concurrent_operations": 5,
    "max_concurrent_per_channel": 2,
    "max_concurrent_per_account": 1,
    "pipeline_timeout_seconds": 3600,
    "retry_max_attempts": 3,
    "retry_backoff_seconds": 60,
    "cost_warn_threshold_usd": 10.0,
    "cost_block_threshold_usd": 100.0,
    "default_automation_level": AUTOMATION_MANUAL,
    "default_timezone": "UTC",
    "scheduler_lookahead_seconds": 3600,
    "diagnostics_history_limit": 100,
    "review_required_stages": [
        "script_generation",
        "production_plan",
        "narration",
        "captions",
        "visual_intelligence",
        "rendering",
    ],
}


def resolve_config(
    conn: Any,
    workspace_id: str,
    *,
    channel_id: str | None = None,
    platform_account_id: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the effective configuration for a given scope.

    Layers are merged in priority order (last writer wins for scalar keys;
    list keys from the narrowest scope win outright).
    """
    result: dict[str, Any] = dict(SYSTEM_DEFAULTS)

    # Workspace strategy profile may carry concurrency/timeout overrides.
    try:
        strategy = cp_services.get_channel_strategy(conn, channel_id) if channel_id else None
        if strategy and strategy.config_json:
            strategy_cfg = (
                json.loads(strategy.config_json)
                if isinstance(strategy.config_json, str)
                else strategy.config_json
            )
            result.update({k: v for k, v in strategy_cfg.items() if not _is_secret_key(k)})
    except Exception:
        pass

    # Effective automation level from CP policy hierarchy.
    try:
        level = cp_services.get_effective_policy(
            conn, workspace_id, channel_id, platform_account_id
        )
        result["effective_automation_level"] = level
    except Exception:
        result["effective_automation_level"] = AUTOMATION_MANUAL

    # Workspace status (paused flag).
    try:
        ws = cp_repo.get_workspace(conn, workspace_id)
        result["workspace_status"] = ws.status
        result["workspace_paused"] = ws.status == "paused"
    except Exception:
        result["workspace_paused"] = False

    # Channel status.
    if channel_id:
        try:
            ch = cp_repo.get_channel(conn, channel_id)
            result["channel_status"] = ch.status
            result["channel_paused"] = ch.status == "paused"
        except Exception:
            result["channel_paused"] = False

    # Runtime overrides (highest priority, secrets filtered).
    if overrides:
        result.update({k: v for k, v in overrides.items() if not _is_secret_key(k)})

    return result


def snapshot_policy_refs(
    conn: Any,
    workspace_id: str,
    *,
    channel_id: str | None = None,
    platform_account_id: str | None = None,
) -> dict[str, str | None]:
    """Return a lightweight ID-only snapshot of the policy references in effect.

    Stored alongside pipeline executions for audit purposes.
    No secret material is included.
    """
    snapshot: dict[str, str | None] = {
        "workspace_id": workspace_id,
        "channel_id": channel_id,
        "platform_account_id": platform_account_id,
    }
    try:
        level = cp_services.get_effective_policy(
            conn, workspace_id, channel_id, platform_account_id
        )
        snapshot["effective_automation_level"] = level
    except Exception:
        snapshot["effective_automation_level"] = AUTOMATION_MANUAL
    return snapshot


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    secret_markers = (
        "secret",
        "token",
        "password",
        "credential",
        "api_key",
        "apikey",
        "private",
        "cert",
        "auth_header",
    )
    return any(m in lowered for m in secret_markers)
