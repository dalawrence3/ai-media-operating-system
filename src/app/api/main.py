"""FastAPI application entry point.

Architecture boundary:
    Browser → FastAPI → ApplicationService → Application/Control Plane → Engines

This module only wires routes and configures the server.  No business logic.
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import asynccontextmanager

import redis as _redis
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.limiter import limiter
from app.api.routes import (
    accounts,
    auth,
    channels,
    diagnostics,
    oauth,
    operations,
    pipelines,
    reviews,
    schedules,
    workspaces,
)
from app.core.config import get_config
from app.core.database import open_db
from app.observability.health import liveness, readiness
from app.observability.logging_config import configure_logging
from app.observability.metrics import get_registry
from app.observability.middleware import (
    MetricsMiddleware,
    RequestIDMiddleware,
    SecurityHeadersMiddleware,
)

# Configure structured JSON logging at startup.
_json_logs = os.environ.get("ACE_LOG_FORMAT", "json").lower() != "console"
configure_logging(
    log_level=os.environ.get("ACE_LOG_LEVEL", "INFO"),
    json_logs=_json_logs,
)


# ---------------------------------------------------------------------------
# Production startup validation (runs on first request to the app server, not
# at import time — so tests importing this module are not affected).
# ---------------------------------------------------------------------------


def _validate_production_config() -> None:
    """Abort startup when required env vars are missing in production.

    Only runs when ACE_ENV is not "development".  Exits with code 1 so the
    container restarts with a clear error rather than serving unauthenticated.
    """
    cfg = get_config()
    if cfg.ace_env == "development":
        return

    errors: list[str] = []
    if not cfg.secret_key:
        errors.append("ACE_SECRET_KEY is not set (required for JWT signing)")
    elif len(cfg.secret_key.encode()) < 32:
        errors.append("ACE_SECRET_KEY must be at least 32 bytes")

    if errors:
        for err in errors:
            print(f"FATAL startup error: {err}", file=sys.stderr)
        sys.exit(1)


@asynccontextmanager
async def lifespan(application: FastAPI):
    _validate_production_config()
    yield


# ---------------------------------------------------------------------------
# CORS — environment-aware; localhost origins only in development
# ---------------------------------------------------------------------------

cfg = get_config()

_CORS_ORIGINS: list[str] = []

if cfg.ace_env == "development":
    _CORS_ORIGINS += [
        "http://localhost:5173",  # Vite dev server default
        "http://localhost:4173",  # Vite preview default
    ]

# Operator-configured origins are always permitted (e.g. the production SPA origin).
_CORS_ORIGINS += [o.strip() for o in cfg.cors_origins if o.strip()]


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="AI Media Operating System — Studio API",
    description=(
        "Thin HTTP transport layer over ApplicationService. "
        "All business logic lives in the application/control-plane layer."
    ),
    version="15.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.exception_handler(PermissionError)
async def _permission_error_handler(request: Request, exc: PermissionError) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": str(exc)})


# ---------------------------------------------------------------------------
# Observability middleware (order matters: outermost = last added)
# ---------------------------------------------------------------------------

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(MetricsMiddleware)
app.add_middleware(RequestIDMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

_PREFIX = "/api/v1"

app.include_router(auth.router, prefix=_PREFIX)
app.include_router(workspaces.router, prefix=_PREFIX)
app.include_router(channels.router, prefix=_PREFIX)
app.include_router(accounts.router, prefix=_PREFIX)
app.include_router(oauth.router, prefix=_PREFIX)
app.include_router(pipelines.router, prefix=_PREFIX)
app.include_router(reviews.router, prefix=_PREFIX)
app.include_router(operations.router, prefix=_PREFIX)
app.include_router(schedules.router, prefix=_PREFIX)
app.include_router(diagnostics.router, prefix=_PREFIX)


# ---------------------------------------------------------------------------
# Health, readiness, and metrics endpoints (unauthenticated)
# ---------------------------------------------------------------------------


@app.get("/health", tags=["meta"], include_in_schema=False)
@app.get("/api/health", tags=["meta"])
def health() -> dict:
    """Liveness probe — returns 200 if the process is running."""
    return liveness()


@app.get("/ready", tags=["meta"], include_in_schema=False)
@app.get("/api/ready", tags=["meta"])
def ready() -> Response:
    """Readiness probe — returns 200 when DB and Redis are reachable, 503 otherwise."""
    _cfg = get_config()
    db_conn = None
    db_error: str | None = None
    redis_conn = None
    redis_error: str | None = None

    try:
        db_conn = open_db(_cfg.db_path)
    except Exception as exc:
        db_error = type(exc).__name__

    try:
        redis_conn = _redis.Redis.from_url(_cfg.redis_url, socket_connect_timeout=2)
    except Exception as exc:
        redis_error = type(exc).__name__

    # If we failed to connect to a dependency, inject a "failing" stub so that
    # readiness() correctly marks the check as an error (not "unconfigured").
    if db_conn is None and db_error:

        class _FailedConn:
            def execute(self, *a, **kw):
                raise ConnectionError(f"DB connection failed: {db_error}")

            def close(self):
                pass

        db_conn = _FailedConn()

    if redis_conn is None and redis_error:

        class _FailedRedis:
            def ping(self):
                raise ConnectionError(f"Redis connection failed: {redis_error}")

        redis_conn = _FailedRedis()

    try:
        result, is_ready = readiness(db_conn=db_conn, redis_conn=redis_conn)
    except Exception:
        result, is_ready = {"status": "error", "checks": {}}, False
    finally:
        if db_conn is not None:
            try:
                db_conn.close()
            except Exception:
                pass

    return Response(
        content=json.dumps(result),
        status_code=200 if is_ready else 503,
        media_type="application/json",
    )


@app.get("/metrics", tags=["meta"], include_in_schema=False)
def metrics() -> Response:
    """Prometheus metrics endpoint (scrape target for monitoring)."""
    registry = get_registry()
    return Response(
        content=generate_latest(registry),
        media_type=CONTENT_TYPE_LATEST,
    )


@app.get("/api/meta", tags=["meta"])
def meta() -> dict:
    _cfg = get_config()
    auth_mode = "dev" if _cfg.dev_auth_enabled else "jwt"
    return {
        "status": "ok",
        "api_version": "15.0.0",
        "auth_mode": auth_mode,
        "environment": _cfg.ace_env,
    }
