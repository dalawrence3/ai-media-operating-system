"""Tests for M15.9 observability (logging, metrics, health, middleware)."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.observability.health import SECURITY_HEADERS, liveness, readiness
from app.observability.logging_config import (
    _redact_sensitive,
    configure_logging,
    get_logger,
)
from app.observability.metrics import METRICS, get_registry
from app.observability.middleware import _normalise_path

# ── Sensitive field redaction ─────────────────────────────────────────────


def test_redact_removes_password():
    event = {"event": "login", "password": "super-secret", "email": "a@b.com"}
    result = _redact_sensitive(None, "info", event)
    assert result["password"] == "<redacted>"
    assert result["email"] == "a@b.com"


def test_redact_removes_access_token():
    event = {"event": "auth", "access_token": "eyJhbG..."}
    result = _redact_sensitive(None, "info", event)
    assert result["access_token"] == "<redacted>"


def test_redact_removes_refresh_token():
    event = {"event": "refresh", "refresh_token": "abc123hex"}
    result = _redact_sensitive(None, "info", event)
    assert result["refresh_token"] == "<redacted>"


def test_redact_removes_api_key():
    event = {"event": "call", "api_key": "sk-prod-key"}
    result = _redact_sensitive(None, "info", event)
    assert result["api_key"] == "<redacted>"


def test_redact_removes_authorization_header():
    event = {"event": "req", "authorization": "Bearer eyJ..."}
    result = _redact_sensitive(None, "info", event)
    assert result["authorization"] == "<redacted>"


def test_redact_removes_secret_key():
    event = {"event": "startup", "secret_key": "aaaa...aaaa"}
    result = _redact_sensitive(None, "info", event)
    assert result["secret_key"] == "<redacted>"


def test_redact_removes_password_hash():
    event = {"event": "check", "password_hash": "$argon2id$..."}
    result = _redact_sensitive(None, "info", event)
    assert result["password_hash"] == "<redacted>"


def test_redact_preserves_safe_fields():
    event = {
        "event": "pipeline.stage",
        "pipeline_id": "pipe-1",
        "stage": "research",
        "workspace_id": "ws-1",
    }
    result = _redact_sensitive(None, "info", event)
    assert result["pipeline_id"] == "pipe-1"
    assert result["stage"] == "research"
    assert result["workspace_id"] == "ws-1"


def test_redact_case_insensitive():
    event = {"PASSWORD": "secret", "Api_Key": "key"}
    result = _redact_sensitive(None, "info", event)
    assert result["PASSWORD"] == "<redacted>"
    assert result["Api_Key"] == "<redacted>"


# ── configure_logging ─────────────────────────────────────────────────────


def test_configure_logging_does_not_raise():
    configure_logging(log_level="WARNING", json_logs=False)


def test_get_logger_returns_logger():
    log = get_logger("test.module")
    assert log is not None


# ── Health checks ──────────────────────────────────────────────────────────


def test_liveness_returns_ok():
    result = liveness()
    assert result["status"] == "ok"
    assert "version" in result
    assert "timestamp" in result


def test_readiness_no_connections():
    result, is_ready = readiness()
    assert result["status"] in ("ready", "degraded")
    assert "checks" in result
    assert "database" in result["checks"]
    assert "redis" in result["checks"]


def test_readiness_with_healthy_db():
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchone.return_value = (1,)
    result, is_ready = readiness(db_conn=mock_conn)
    assert result["checks"]["database"] == "ok"


def test_readiness_with_failing_db():
    mock_conn = MagicMock()
    mock_conn.execute.side_effect = Exception("connection refused")
    result, is_ready = readiness(db_conn=mock_conn)
    assert "error" in result["checks"]["database"]
    assert is_ready is False


def test_readiness_with_healthy_redis():
    mock_redis = MagicMock()
    mock_redis.ping.return_value = True
    result, is_ready = readiness(redis_conn=mock_redis)
    assert result["checks"]["redis"] == "ok"


def test_readiness_with_failing_redis():
    mock_redis = MagicMock()
    mock_redis.ping.side_effect = Exception("timeout")
    result, is_ready = readiness(redis_conn=mock_redis)
    assert "error" in result["checks"]["redis"]
    assert is_ready is False


def test_readiness_all_healthy():
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchone.return_value = (1,)
    mock_redis = MagicMock()
    mock_redis.ping.return_value = True
    result, is_ready = readiness(db_conn=mock_conn, redis_conn=mock_redis)
    assert result["status"] == "ready"
    assert is_ready is True


# ── Security headers ──────────────────────────────────────────────────────


def test_security_headers_present():
    assert "X-Content-Type-Options" in SECURITY_HEADERS
    assert SECURITY_HEADERS["X-Content-Type-Options"] == "nosniff"
    assert "X-Frame-Options" in SECURITY_HEADERS
    assert SECURITY_HEADERS["X-Frame-Options"] == "DENY"
    assert "Referrer-Policy" in SECURITY_HEADERS
    assert "Cache-Control" in SECURITY_HEADERS


# ── Path normalisation ────────────────────────────────────────────────────


def test_normalise_path_collapses_uuid():
    path = "/api/v1/pipelines/123e4567-e89b-12d3-a456-426614174000/stages"
    assert _normalise_path(path) == "/api/v1/pipelines/{id}/stages"


def test_normalise_path_collapses_integer_id():
    path = "/api/v1/users/42/tokens"
    assert _normalise_path(path) == "/api/v1/users/{id}/tokens"


def test_normalise_path_leaves_safe_paths():
    assert _normalise_path("/api/v1/workspaces") == "/api/v1/workspaces"
    assert _normalise_path("/health") == "/health"


# ── Prometheus metrics ────────────────────────────────────────────────────


def test_metrics_dict_has_expected_keys():
    assert "http_requests_total" in METRICS
    assert "http_request_duration_seconds" in METRICS
    assert "pipeline_stages_total" in METRICS
    assert "auth_login_total" in METRICS


def test_registry_is_not_default():
    from prometheus_client import REGISTRY

    assert get_registry() is not REGISTRY  # isolated registry, not global


def test_counter_increments():
    before = _counter_value("ace_auth_login_total", {"outcome": "success"})
    METRICS["auth_login_total"].labels(outcome="success").inc()
    after = _counter_value("ace_auth_login_total", {"outcome": "success"})
    assert after == before + 1.0


def _counter_value(metric_name: str, labels: dict) -> float:
    # prometheus_client strips _total from Counter names in metric.name
    base_name = metric_name.removesuffix("_total")
    registry = get_registry()
    for metric in registry.collect():
        if metric.name == base_name:
            for sample in metric.samples:
                if sample.labels == labels and sample.name == metric_name:
                    return sample.value
    return 0.0
