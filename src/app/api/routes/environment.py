"""Environment readiness endpoint — reports the status of all required env vars,
capabilities, and data prerequisites for pilot operations.
No live calls are made; this is purely a local state read."""

from __future__ import annotations

import os
import shutil
from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import get_config, get_db, require_workspace_permission
from app.api.jwt_auth import CurrentUser, get_current_user
from app.core.config import Config

router = APIRouter(prefix="/workspaces/{workspace_id}/environment", tags=["environment"])


def _check(ok: bool, detail: str = "") -> dict[str, Any]:
    return {"ok": ok, "detail": detail}


@router.get("")
def get_environment_readiness(
    workspace_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    conn: Any = Depends(get_db),
    cfg: Config = Depends(get_config),
) -> dict[str, Any]:
    require_workspace_permission(current_user, workspace_id, "analytics:view")

    # ── Provider env vars ────────────────────────────────────────────────────
    checks: dict[str, dict[str, Any]] = {}

    checks["youtube_api_key"] = _check(
        bool(cfg.youtube_data_api_key),
        "ACE_YOUTUBE_API_KEY" if not cfg.youtube_data_api_key else "set",
    )

    client_secrets_path = os.environ.get("YOUTUBE_CLIENT_SECRETS_PATH", "")
    checks["youtube_client_secrets"] = _check(
        bool(client_secrets_path) and os.path.isfile(client_secrets_path),
        client_secrets_path or "YOUTUBE_CLIENT_SECRETS_PATH not set",
    )

    ai_provider = os.environ.get("ACE_AI_PROVIDER", cfg.ai_provider or "fake")
    anthropic_key = os.environ.get("ACE_ANTHROPIC_API_KEY", "")
    ai_is_fake = ai_provider == "fake" or cfg.dry_run
    if ai_is_fake:
        # Fake provider: can run non-AI steps; content generation is blocked.
        checks["ai_provider"] = _check(
            True, "test-provider-only (ACE_AI_PROVIDER=fake — content generation unavailable)"
        )
    else:
        checks["ai_provider"] = _check(
            bool(anthropic_key),
            f"{ai_provider} — ACE_ANTHROPIC_API_KEY {'set' if anthropic_key else 'not set'}",
        )

    checks["ffmpeg"] = _check(
        shutil.which("ffmpeg") is not None,
        shutil.which("ffmpeg") or "ffmpeg not found in PATH",
    )

    checks["dev_auth"] = _check(
        cfg.dev_auth_enabled,
        "enabled (ACE_ENV=development)" if cfg.dev_auth_enabled else "disabled (production mode)",
    )

    # ── OAuth credential profiles (token files) ──────────────────────────────
    token_row = conn.execute(
        "SELECT COUNT(*) FROM cp_credential_profiles WHERE workspace_id = ? AND status = 'active'",
        (workspace_id,),
    ).fetchone()
    token_count: int = token_row[0] if token_row else 0
    checks["oauth_tokens"] = _check(token_count > 0, f"{token_count} active credential profile(s)")

    # ── Workspace data prerequisites ─────────────────────────────────────────
    # cp_channels is the workspace-scoped channel table (channels is the legacy table).
    channel_count: int = conn.execute(
        "SELECT COUNT(*) FROM cp_channels WHERE workspace_id = ?",
        (workspace_id,),
    ).fetchone()[0]
    checks["channels"] = _check(channel_count > 0, f"{channel_count} channel(s)")

    pub_count: int = conn.execute(
        "SELECT COUNT(*) FROM publications WHERE workspace_id = ?",
        (workspace_id,),
    ).fetchone()[0]
    checks["publications"] = _check(pub_count > 0, f"{pub_count} publication(s)")

    snap_count: int = conn.execute(
        "SELECT COUNT(*) FROM analytics_snapshots ans "
        "JOIN publications p ON p.id = ans.publication_id "
        "WHERE p.workspace_id = ?",
        (workspace_id,),
    ).fetchone()[0]
    checks["analytics_snapshots"] = _check(snap_count > 0, f"{snap_count} snapshot(s)")

    agg_count: int = conn.execute(
        "SELECT COUNT(*) FROM analytics_aggregates aa "
        "JOIN publications p ON p.id = aa.publication_id "
        "WHERE p.workspace_id = ?",
        (workspace_id,),
    ).fetchone()[0]
    checks["analytics_aggregates"] = _check(agg_count > 0, f"{agg_count} aggregate row(s)")

    rec_count: int = conn.execute(
        "SELECT COUNT(*) FROM optimization_recommendations WHERE publication_id IN "
        "(SELECT id FROM publications WHERE workspace_id = ?)",
        (workspace_id,),
    ).fetchone()[0]
    checks["recommendations"] = _check(rec_count > 0, f"{rec_count} recommendation(s)")

    cluster_count: int = conn.execute(
        "SELECT COUNT(*) FROM market_canonical_clusters",
    ).fetchone()[0]
    checks["market_clusters"] = _check(cluster_count > 0, f"{cluster_count} canonical cluster(s)")

    # ── Gates (features that require ALL prerequisites) ──────────────────────
    analytics_ready = all(
        checks[k]["ok"] for k in ["youtube_api_key", "oauth_tokens", "analytics_snapshots"]
    )
    # pilot_ready requires a real AI provider — fake blocks content generation
    ai_provider_real = not ai_is_fake and checks["ai_provider"]["ok"]
    pilot_ready = analytics_ready and checks["publications"]["ok"] and ai_provider_real

    overall_ok = all(c["ok"] for c in checks.values())

    # ── Derived fields ────────────────────────────────────────────────────────
    ai_provider_mode = (
        "test-provider-only"
        if ai_is_fake
        else ("ready" if checks["ai_provider"]["ok"] else "missing-key")
    )
    warnings: list[str] = []
    if ai_is_fake:
        warnings.append(
            "AI provider is 'fake' — analytics ingest and video serving are available "
            "but content generation (script, narration, render) requires ACE_AI_PROVIDER=anthropic "
            "and ACE_ANTHROPIC_API_KEY."
        )

    return {
        "workspace_id": workspace_id,
        "ok": overall_ok,
        "pilot_ready": pilot_ready,
        "analytics_ready": analytics_ready,
        "ai_provider_mode": ai_provider_mode,
        "warnings": warnings,
        "checks": checks,
    }
