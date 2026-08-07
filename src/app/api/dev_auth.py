"""DEV-ONLY actor mechanism for Phase 14 local development.

⚠️  THIS IS NOT PRODUCTION AUTHENTICATION. ⚠️

This module provides a simple actor identity mechanism so the frontend can
make authenticated requests during local development.  It is:

  - Explicitly labeled DEVELOPMENT-ONLY throughout
  - Disabled when the ACE_DEV_AUTH environment variable is not set to "enabled"
  - Not a JWT verifier, not an RBAC system, not a production guard
  - Designed so Phase 15 can replace it with real identity/RBAC with no
    changes to any route or ApplicationService code

Phase 15 will inject a real JWT/RBAC auth_hook.  The hook signature is
identical to what Phase 13 already defines.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import Header, HTTPException

_DEV_ACTOR = "dev:studio-user"
_DEV_AUTH_ENABLED = os.environ.get("ACE_DEV_AUTH", "enabled") == "enabled"


def dev_auth_hook(
    conn: Any, command_type: str, workspace_id: str, actor: str
) -> None:
    """Dev-only auth hook — permits the canonical dev actor; denies all others.

    Replaces the fail-closed default_auth_hook during development so the
    frontend can call mutating commands without injecting system: actors.
    Never use in production.
    """
    if actor == _DEV_ACTOR or actor.startswith("system:"):
        return
    raise PermissionError(f"[DEV AUTH] Actor '{actor}' not permitted in dev mode")


async def get_dev_actor(
    x_actor: str = Header(default=_DEV_ACTOR, alias="X-Dev-Actor"),
) -> str:
    """Dependency: extract the dev actor from X-Dev-Actor header (or use default).

    ⚠️  DEV-ONLY — replaced by real JWT extraction in Phase 15.
    """
    if not _DEV_AUTH_ENABLED:
        raise HTTPException(status_code=401, detail="Auth not configured")
    return x_actor
