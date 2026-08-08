"""Authorization contract for the application layer.

Phase 13 defines the authorization boundary but does not implement JWT/RBAC.

Default behaviour:
- System actors (actor starts with "system:") are always permitted.
- Non-system actors require an explicitly injected AuthHook.
- If no hook is provided the dispatcher fails closed with AuthorizationRequiredError.

Phase 14/15 will inject a real hook carrying user identity and JWT claims.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.application.errors import AuthorizationRequiredError

# All actors whose prefix marks them as trusted internal system callers.
SYSTEM_ACTOR_PREFIX = "system:"

# Canonical well-known system actors.
TRUSTED_SYSTEM_ACTORS: frozenset[str] = frozenset(
    {
        "system:pipeline",
        "system:control-plane",
        "system:scheduler",
        "system:recovery",
        "system:test",
    }
)

# Hook signature matches the existing dispatcher AuthHook.
# (conn, command_type, workspace_id, actor) -> None  — raises to deny.
AuthHook = Callable[[Any, str, str, str], None]


def is_system_actor(actor: str) -> bool:
    """Return True when actor is a trusted system caller."""
    return actor.startswith(SYSTEM_ACTOR_PREFIX)


def default_auth_hook(
    conn: Any, command_type: str, workspace_id: str, actor: str
) -> None:
    """Fail-closed authorization hook.

    System actors are always permitted.
    Non-system actors raise AuthorizationRequiredError — callers must inject
    an explicit hook (e.g. allow_all_auth_hook for dev/test, or a real JWT
    verifier for production).
    """
    if is_system_actor(actor):
        return
    raise AuthorizationRequiredError(actor, command_type)


def allow_all_auth_hook(
    conn: Any, command_type: str, workspace_id: str, actor: str
) -> None:
    """Permissive hook for development and test environments.

    Never inject this hook in production code paths.
    """
