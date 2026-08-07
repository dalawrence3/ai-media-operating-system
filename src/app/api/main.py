"""FastAPI application entry point.

Architecture boundary:
    Browser → FastAPI → ApplicationService → Application/Control Plane → Engines

This module only wires routes and configures the server.  No business logic.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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

# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="AI Media Operating System — Studio API",
    description=(
        "Thin HTTP transport layer over ApplicationService. "
        "All business logic lives in the application/control-plane layer."
    ),
    version="14.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

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
# Health check (no auth required)
# ---------------------------------------------------------------------------

@app.get("/api/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok", "version": "14.0.0"}


@app.get("/api/meta", tags=["meta"])
def meta() -> dict[str, str]:
    return {
        "status": "ok",
        "api_version": "14.0.0",
        "auth_mode": "dev",  # Phase 15 changes this to "jwt"
        "note": "DEV AUTH ACTIVE — not for production use",
    }
