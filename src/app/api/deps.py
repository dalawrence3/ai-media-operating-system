"""FastAPI dependency injection — provides ApplicationService and CP service access.

Each request gets its own connection from the SQLite database.  ApplicationService
is the sole backend boundary; this module must not import any repository, engine,
or sqlite3 directly.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from fastapi import Depends

from app.api.dev_auth import dev_auth_hook, get_dev_actor
from app.application.composition import build_application_service
from app.application.services import ApplicationService
from app.core.config import Config
from app.core.database import open_db


def get_config() -> Config:
    return Config()


def get_db(config: Config = Depends(get_config)) -> Generator[Any]:
    """Open a sqlite3 connection for the duration of one HTTP request."""
    conn = open_db(config.db_path)
    try:
        yield conn
    finally:
        conn.close()


def get_app_service(
    conn: Any = Depends(get_db),
) -> ApplicationService:
    """Build an ApplicationService for this request.

    Injects the dev_auth_hook so the frontend can call mutating commands.
    Phase 15 replaces this with a real JWT verifier.
    """
    return build_application_service(conn, auth_hook=dev_auth_hook)


# Re-export get_dev_actor so routes can import from one place.
__all__ = ["get_db", "get_app_service", "get_dev_actor", "get_config"]
