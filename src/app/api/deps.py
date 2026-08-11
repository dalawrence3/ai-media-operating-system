"""FastAPI dependency injection — DB connection, ApplicationService, and auth.

Auth mode selection:
  Production / staging (ACE_ENV != "development"):
    All protected routes require a valid JWT Bearer token.
    X-Dev-Actor is ignored.

  Development (ACE_ENV=development AND ACE_DEV_AUTH=enabled):
    Dev actor header is accepted for local frontend iteration.
    Bearer token also accepted (enables JWT testing in dev).

ApplicationService is the sole backend boundary; this module must not import
any repository, engine, or sqlite3 directly.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from fastapi import Depends, HTTPException

from app.api.jwt_auth import CurrentUser, get_current_user, make_jwt_auth_hook
from app.application.composition import build_application_service
from app.application.services import ApplicationService
from app.auth.rbac import has_permission
from app.core.config import Config
from app.core.config import get_config as _cfg
from app.core.database import open_db


def get_config() -> Config:
    return _cfg()


def get_db(config: Config = Depends(get_config)) -> Generator[Any]:
    """Open a DB connection for the duration of one HTTP request."""
    conn = open_db(config.db_path)
    try:
        yield conn
    finally:
        conn.close()


async def get_actor(
    current_user: CurrentUser = Depends(get_current_user),
) -> str:
    """Return the actor string for the authenticated request."""
    return current_user.actor


def require_workspace_permission(
    user: CurrentUser,
    workspace_id: str,
    action: str,
) -> None:
    """Raise HTTP 403 if the caller lacks the required action in workspace_id.

    Dev users (is_dev=True) are granted all permissions unconditionally.
    In production JWT mode, workspace membership + role are checked against
    the RBAC permission matrix.
    """
    if user.is_dev:
        return
    if not has_permission(action, workspace_id, user.workspace_roles):
        raise HTTPException(
            status_code=403,
            detail=f"Action '{action}' requires workspace membership with sufficient role",
        )


def workspace_topic_ids(conn: Any, workspace_id: str) -> list[int]:
    """Return distinct topic_ids linked to workspace_id via pipeline executions."""
    rows = conn.execute(
        "SELECT DISTINCT topic_id FROM app_pipeline_executions WHERE workspace_id = ?",
        (workspace_id,),
    ).fetchall()
    return [r[0] for r in rows if r[0] is not None]


def get_app_service(
    conn: Any = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> ApplicationService:
    """Build ApplicationService with an auth hook derived from the request identity.

    Production: JWT claims enforced — workspace membership + RBAC.
    Development: all commands permitted (mirrors allow_all_auth_hook).
    """
    auth_hook = make_jwt_auth_hook(current_user)
    return build_application_service(conn, auth_hook=auth_hook)
