"""Role-Based Access Control (RBAC) for the AI Content Engine.

Role hierarchy (highest to lowest privilege):
  owner > admin > operator > reviewer > analyst

Permissions are deny-by-default — every action requires explicit role membership.
Workspace membership check is always required; no cross-workspace access.
"""

from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    OPERATOR = "operator"
    REVIEWER = "reviewer"
    ANALYST = "analyst"


# Ordered from highest to lowest privilege (used for ≥ comparisons).
_ROLE_ORDER: list[str] = [
    Role.OWNER,
    Role.ADMIN,
    Role.OPERATOR,
    Role.REVIEWER,
    Role.ANALYST,
]

# Permission matrix: action → minimum required role.
# Deny-by-default: any action not listed here is denied to all roles.
_PERMISSION_MATRIX: dict[str, str] = {
    # Workspace management
    "workspace:delete": Role.OWNER,
    "workspace:transfer": Role.OWNER,
    "workspace:manage_members": Role.ADMIN,
    "workspace:update_settings": Role.ADMIN,
    # Channel management
    "channel:create": Role.ADMIN,
    "channel:delete": Role.ADMIN,
    "channel:update": Role.OPERATOR,
    "channel:view": Role.ANALYST,
    # Pipeline operations
    "pipeline:create": Role.OPERATOR,
    "pipeline:run": Role.OPERATOR,
    "pipeline:cancel": Role.OPERATOR,
    "pipeline:view": Role.ANALYST,
    # Platform accounts (provider credentials)
    "platform_account:create": Role.ADMIN,
    "platform_account:delete": Role.ADMIN,
    "platform_account:view": Role.OPERATOR,
    # Publishing
    "publish:approve": Role.OPERATOR,
    "publish:reject": Role.REVIEWER,
    "publish:view": Role.ANALYST,
    # Analytics
    "analytics:view": Role.ANALYST,
    "analytics:export": Role.REVIEWER,
    # Storage
    "storage:upload": Role.OPERATOR,
    "storage:delete": Role.ADMIN,
    "storage:view": Role.ANALYST,
    # Schedule management
    "schedule:create": Role.OPERATOR,
    "schedule:delete": Role.ADMIN,
    "schedule:view": Role.ANALYST,
}


def _role_rank(role: str) -> int:
    """Return numeric rank; lower = more privileged. Returns len if unknown."""
    try:
        return _ROLE_ORDER.index(role)
    except ValueError:
        return len(_ROLE_ORDER)


def has_permission(
    action: str,
    workspace_id: str,
    workspace_roles: dict[str, str],
) -> bool:
    """Return True if the user's role in workspace_id grants `action`.

    workspace_roles — {workspace_id: role} from the decoded JWT claims.
    Deny-by-default: unknown action → False; no workspace membership → False.
    """
    role = workspace_roles.get(workspace_id)
    if not role:
        return False

    minimum_role = _PERMISSION_MATRIX.get(action)
    if minimum_role is None:
        return False

    return _role_rank(role) <= _role_rank(minimum_role)


def require_role(
    minimum_role: str | Role,
    workspace_id: str,
    workspace_roles: dict[str, str],
) -> None:
    """Raise PermissionError if the caller's role is below `minimum_role`.

    Use this for coarse gate-keeping at the service layer.
    """
    role = workspace_roles.get(workspace_id)
    if not role:
        raise PermissionError(
            f"User is not a member of workspace {workspace_id!r}"
        )
    required = str(minimum_role.value if isinstance(minimum_role, Role) else minimum_role)
    if _role_rank(role) > _role_rank(required):
        raise PermissionError(
            f"Action requires role '{required}' or higher; "
            f"caller has '{role}' in workspace {workspace_id!r}"
        )
