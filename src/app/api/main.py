"""FastAPI application entry point.

Architecture boundary:
    Browser → FastAPI → ApplicationService → Application/Control Plane → Engines

This module only wires routes and configures the server.  No business logic.
"""

from __future__ import annotations

import os

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.api.routes import (
    accounts,
    channels,
    diagnostics,
    operations,
    pipelines,
    reviews,
    schedules,
    workspaces,
)
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
)

# ---------------------------------------------------------------------------
# Observability middleware (order matters: outermost = last added)
# ---------------------------------------------------------------------------

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(MetricsMiddleware)
app.add_middleware(RequestIDMiddleware)

# ---------------------------------------------------------------------------
# CORS — allow the Vite dev server and any configured frontend origin
# ---------------------------------------------------------------------------

_CORS_ORIGINS = [
    "http://localhost:5173",  # Vite dev server default
    "http://localhost:4173",  # Vite preview default
    *os.environ.get("ACE_CORS_ORIGINS", "").split(","),
]
_CORS_ORIGINS = [o for o in _CORS_ORIGINS if o]

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

app.include_router(workspaces.router, prefix=_PREFIX)
app.include_router(channels.router, prefix=_PREFIX)
app.include_router(accounts.router, prefix=_PREFIX)
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
    import json

    result, is_ready = readiness()
    status_code = 200 if is_ready else 503
    return Response(
        content=json.dumps(result),
        status_code=status_code,
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
    return {
        "status": "ok",
        "api_version": "15.0.0",
        "auth_mode": "jwt",
    }
