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

from fastapi import Depends

from app.api.jwt_auth import CurrentUser, get_current_user, make_jwt_auth_hook
from app.application.composition import build_application_service
from app.application.services import ApplicationService
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
