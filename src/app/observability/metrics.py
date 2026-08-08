"""Prometheus metrics registry for the AI Content Engine.

All application metrics are registered here and exported via /metrics.
Workers and API handlers import specific counters/histograms from METRICS.

Usage:
  from app.observability.metrics import METRICS
  METRICS["http_requests_total"].labels(method="GET", path="/health", status=200).inc()
"""

from __future__ import annotations

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Histogram,
    Info,
)

_registry = CollectorRegistry(auto_describe=True)


def get_registry() -> CollectorRegistry:
    return _registry


# ── HTTP metrics ──────────────────────────────────────────────────────────

_http_requests_total = Counter(
    "ace_http_requests_total",
    "Total HTTP requests by method, path, and status code",
    ["method", "path", "status"],
    registry=_registry,
)

_http_request_duration_seconds = Histogram(
    "ace_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
    registry=_registry,
)

# ── Pipeline metrics ──────────────────────────────────────────────────────

_pipeline_stages_total = Counter(
    "ace_pipeline_stages_total",
    "Total pipeline stage executions by stage and status",
    ["stage", "status"],
    registry=_registry,
)

_pipeline_stages_blocked_total = Counter(
    "ace_pipeline_stages_blocked_total",
    "Pipeline stages blocked by ProviderBoundary (Class B/C gate)",
    ["stage", "stage_class"],
    registry=_registry,
)

# ── Job queue metrics ─────────────────────────────────────────────────────

_jobs_enqueued_total = Counter(
    "ace_jobs_enqueued_total",
    "Total jobs enqueued to RQ by queue name",
    ["queue"],
    registry=_registry,
)

_jobs_completed_total = Counter(
    "ace_jobs_completed_total",
    "Total jobs completed by queue and status",
    ["queue", "status"],
    registry=_registry,
)

# ── Auth metrics ──────────────────────────────────────────────────────────

_auth_login_total = Counter(
    "ace_auth_login_total",
    "Total login attempts by outcome",
    ["outcome"],  # success | failure | disabled
    registry=_registry,
)

_auth_token_refresh_total = Counter(
    "ace_auth_token_refresh_total",
    "Total token refresh attempts by outcome",
    ["outcome"],
    registry=_registry,
)

# ── System info ───────────────────────────────────────────────────────────

_build_info = Info(
    "ace_build",
    "Build information",
    registry=_registry,
)
_build_info.info({"version": "15.0.0", "phase": "15"})

# ── Public registry (dict for easy access) ────────────────────────────────

METRICS: dict = {
    "http_requests_total": _http_requests_total,
    "http_request_duration_seconds": _http_request_duration_seconds,
    "pipeline_stages_total": _pipeline_stages_total,
    "pipeline_stages_blocked_total": _pipeline_stages_blocked_total,
    "jobs_enqueued_total": _jobs_enqueued_total,
    "jobs_completed_total": _jobs_completed_total,
    "auth_login_total": _auth_login_total,
    "auth_token_refresh_total": _auth_token_refresh_total,
}
