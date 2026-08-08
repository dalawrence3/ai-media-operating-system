"""Production JWT authentication dependency for FastAPI.

Extracts and verifies the Bearer token from the Authorization header, resolves
the actor identity, and produces a per-request auth_hook for ApplicationService.

Mode selection (checked at request time, not import time):
  Production / staging (ACE_ENV != "development"):
    Bearer token required.  X-Dev-Actor is rejected.
  Development (ACE_ENV=development AND ACE_DEV_AUTH=enabled):
    X-Dev-Actor accepted for local iteration.  Bearer token also accepted.

Security invariants:
  - Missing or malformed Authorization header → 401 (production mode).
  - Expired token → 401.
  - Invalid signature → 401.
  - Valid token but no workspace membership → 403.
  - Valid token but insufficient role for command → 403.
  - System actors (system:*) are always permitted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi import Header, HTTPException

from app.auth.tokens import TokenExpiredError, TokenInvalidError, decode_access_token

# ---------------------------------------------------------------------------
# Command → RBAC permission mapping
# ---------------------------------------------------------------------------

# Maps ApplicationService command type names to the RBAC action string in
# _PERMISSION_MATRIX.  Unknown commands default to "pipeline:run" (operator).
_COMMAND_PERMISSION: dict[str, str] = {
    # Pipeline lifecycle
    "StartPipelineCommand": "pipeline:create",
    "PausePipelineCommand": "pipeline:cancel",
    "ResumePipelineCommand": "pipeline:run",
    "CancelPipelineCommand": "pipeline:cancel",
    "AdvancePipelineStageCommand": "pipeline:run",
    "ExecutePipelineStageCommand": "pipeline:run",
    "FailPipelineStageCommand": "pipeline:run",
    "RecoverPipelineCommand": "pipeline:run",
    # Operations
    "RetryOperationCommand": "pipeline:run",
    "CancelOperationCommand": "pipeline:cancel",
    # Review
    "ApproveReviewItemCommand": "publish:approve",
    "RejectReviewItemCommand": "publish:reject",
    # Schedules
    "CreateScheduleCommand": "schedule:create",
    "PauseScheduleCommand": "schedule:create",
    "ResumeScheduleCommand": "schedule:create",
    "DeleteScheduleCommand": "schedule:delete",
    # Events / recovery
    "ReplayEventCommand": "pipeline:run",
}

_DEFAULT_COMMAND_ACTION = "pipeline:run"  # fallback — requires operator


# ---------------------------------------------------------------------------
# CurrentUser identity carrier
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CurrentUser:
    """Verified identity extracted from a JWT access token (or dev stub)."""

    user_id: int
    email: str
    workspace_roles: dict[str, str] = field(default_factory=dict)
    # True only in development mode when JWT is not required.
    _is_dev: bool = field(default=False, compare=False)
    # Actor string override (used for dev:studio-user etc.)
    _actor_override: str = field(default="", compare=False)

    @property
    def actor(self) -> str:
        if self._actor_override:
            return self._actor_override
        return f"user:{self.user_id}"

    @property
    def is_dev(self) -> bool:
        return self._is_dev


# ---------------------------------------------------------------------------
# FastAPI dependency — handles both production and development modes
# ---------------------------------------------------------------------------


def get_current_user(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_dev_actor: str | None = Header(default=None, alias="X-Dev-Actor"),
) -> CurrentUser:
    """FastAPI dependency: extract and verify identity.

    Production (ACE_ENV != "development"):
      - Authorization: Bearer <token> required.
      - X-Dev-Actor is silently ignored.
      - Returns CurrentUser from verified JWT claims.
      - Raises 401 on missing/invalid/expired tokens.

    Development (ACE_ENV=development AND ACE_DEV_AUTH=enabled):
      - Bearer token used if provided (enables testing JWT in dev).
      - Falls back to X-Dev-Actor header (default "dev:studio-user").
      - Returns a synthetic CurrentUser with is_dev=True.
    """
    from app.core.config import get_config

    cfg = get_config()

    # ── Development mode ────────────────────────────────────────────────────
    if cfg.dev_auth_enabled:
        # Bearer token takes precedence even in dev (enables JWT testing).
        if authorization and authorization.startswith("Bearer "):
            return _decode_bearer(authorization[7:], cfg.secret_key)
        # Fall back to X-Dev-Actor.
        actor = x_dev_actor or "dev:studio-user"
        return CurrentUser(
            user_id=0,
            email="dev@local",
            workspace_roles={},
            _is_dev=True,
            _actor_override=actor,
        )

    # ── Production mode — JWT required ─────────────────────────────────────
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Authentication required. Provide: Authorization: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Invalid Authorization header format. Expected: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not cfg.secret_key:
        raise HTTPException(
            status_code=503,
            detail="Authentication not configured on this server",
        )

    return _decode_bearer(authorization[7:], cfg.secret_key)


def _decode_bearer(raw_token: str, secret_key: str) -> CurrentUser:
    """Decode and validate a raw JWT, returning CurrentUser or raising 401."""
    try:
        claims = decode_access_token(raw_token, secret_key=secret_key)
    except TokenExpiredError as exc:
        raise HTTPException(
            status_code=401,
            detail="Access token has expired",
            headers={"WWW-Authenticate": "Bearer error=\"invalid_token\""},
        ) from exc
    except TokenInvalidError as exc:
        raise HTTPException(
            status_code=401,
            detail="Invalid access token",
            headers={"WWW-Authenticate": "Bearer error=\"invalid_token\""},
        ) from exc

    return CurrentUser(
        user_id=int(claims["sub"]),
        email=claims.get("email", ""),
        workspace_roles=claims.get("roles", {}),
    )


# ---------------------------------------------------------------------------
# ApplicationService auth hook factory
# ---------------------------------------------------------------------------


def make_jwt_auth_hook(user: CurrentUser):
    """Return an auth_hook for ApplicationService that enforces RBAC.

    Dev users (is_dev=True) are granted all permissions unconditionally —
    this mirrors the behaviour of allow_all_auth_hook but without importing it
    from the application layer, keeping the dependency direction clean.

    Production users must have workspace membership and sufficient role.
    """
    if user.is_dev:
        # Development: permit all, matching allow_all_auth_hook behaviour.
        def _dev_hook(conn: Any, command_type: str, workspace_id: str, actor: str) -> None:
            return

        return _dev_hook

    # Production: enforce workspace membership + RBAC.
    from app.auth.rbac import _PERMISSION_MATRIX, _role_rank  # noqa: PLC2701

    workspace_roles = user.workspace_roles

    def auth_hook(conn: Any, command_type: str, workspace_id: str, actor: str) -> None:
        # System actors are always trusted.
        if actor.startswith("system:"):
            return

        # Workspace membership check.
        role = workspace_roles.get(workspace_id)
        if not role:
            raise PermissionError(
                f"User {user.email!r} is not a member of workspace {workspace_id!r}"
            )

        # Resolve the minimum required role for this command.
        action = _COMMAND_PERMISSION.get(command_type, _DEFAULT_COMMAND_ACTION)
        required_role = _PERMISSION_MATRIX.get(action)
        if required_role is None:
            raise PermissionError(
                f"Command {command_type!r} is not permitted (no permission mapping)"
            )

        if _role_rank(role) > _role_rank(required_role):
            raise PermissionError(
                f"Action {action!r} requires role '{required_role}' or higher; "
                f"caller has '{role}' in workspace {workspace_id!r}"
            )

    return auth_hook
