"""Health and readiness check logic.

/health  — liveness: returns 200 if the process is running.
/ready   — readiness: returns 200 only when DB and Redis are reachable.

Both are unauthenticated (required for load balancer / k8s probes).
Readiness check failures return 503.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def liveness() -> dict:
    """Liveness probe: always succeeds if the process is up."""
    return {
        "status": "ok",
        "version": "15.0.0",
        "timestamp": datetime.now(UTC).isoformat(),
    }


def readiness(db_conn: Any = None, redis_conn: Any = None) -> tuple[dict, bool]:
    """Readiness probe: check DB and Redis connectivity.

    Returns (result_dict, is_ready). is_ready=False means the service should
    return HTTP 503 to signal it is not ready to accept traffic.
    """
    checks: dict[str, str] = {}
    ready = True

    # Database ping
    if db_conn is not None:
        try:
            db_conn.execute("SELECT 1").fetchone()
            checks["database"] = "ok"
        except Exception as exc:
            checks["database"] = f"error: {type(exc).__name__}"
            ready = False
    else:
        checks["database"] = "unconfigured"

    # Redis ping
    if redis_conn is not None:
        try:
            redis_conn.ping()
            checks["redis"] = "ok"
        except Exception as exc:
            checks["redis"] = f"error: {type(exc).__name__}"
            ready = False
    else:
        checks["redis"] = "unconfigured"

    return {
        "status": "ready" if ready else "degraded",
        "checks": checks,
        "timestamp": datetime.now(UTC).isoformat(),
    }, ready


def _make_security_headers() -> dict[str, str]:
    """Return security response headers for all API responses."""
    return {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "X-XSS-Protection": "1; mode=block",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Cache-Control": "no-store",
        "Content-Security-Policy": "default-src 'none'",
    }


SECURITY_HEADERS: dict[str, str] = _make_security_headers()
