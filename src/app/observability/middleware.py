"""ASGI middleware: request ID correlation, security headers, metrics.

RequestIDMiddleware:
  - Reads X-Request-ID from incoming request (if present) or generates a UUID.
  - Binds request_id to structlog contextvars so all log lines for this request
    carry the same correlation ID.
  - Adds X-Request-ID to the response.

SecurityHeadersMiddleware:
  - Appends security headers to every response.
  - Headers: X-Content-Type-Options, X-Frame-Options, Referrer-Policy, etc.

MetricsMiddleware:
  - Increments ace_http_requests_total and records ace_http_request_duration_seconds.
  - Path is normalised to avoid high-cardinality label explosion (UUIDs → {id}).
"""

from __future__ import annotations

import re
import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.observability.health import SECURITY_HEADERS
from app.observability.metrics import METRICS

logger = structlog.get_logger(__name__)

# Collapse UUIDs and numeric IDs in path to {id} to reduce cardinality.
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_INT_RE = re.compile(r"/\d+(?=/|$)")


def _normalise_path(path: str) -> str:
    path = _UUID_RE.sub("{id}", path)
    path = _INT_RE.sub("/{id}", path)
    return path


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach a correlation ID to every request and response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to every HTTP response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers[header] = value
        return response


class MetricsMiddleware(BaseHTTPMiddleware):
    """Record Prometheus HTTP request count and latency."""

    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - start

        path = _normalise_path(request.url.path)
        method = request.method
        status = str(response.status_code)

        METRICS["http_requests_total"].labels(method=method, path=path, status=status).inc()
        METRICS["http_request_duration_seconds"].labels(method=method, path=path).observe(duration)

        return response
