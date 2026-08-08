"""Tests for the readiness probe endpoint (/api/ready).

Verifies: all healthy → 200, DB failure → 503, Redis failure → 503,
both failing → 503, and that exceptions are caught safely.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.config import reset_config


@pytest.fixture(autouse=True)
def _reset_config():
    reset_config()
    yield
    reset_config()


@pytest.fixture()
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ACE_ENV", "development")
    monkeypatch.setenv("ACE_SECRET_KEY", "a" * 32)
    monkeypatch.setenv("ACE_DB_PATH", str(tmp_path / "ready_test.db"))
    reset_config()
    from app.api.main import app

    with TestClient(app) as c:
        yield c


class TestReadinessProbe:
    def test_healthy_db_returns_200(self, client):
        """With a real SQLite DB the check should pass."""
        # The default open_db(db_path) succeeds for the temp path.
        mock_redis = MagicMock()
        mock_redis.ping.return_value = True
        with patch("redis.Redis.from_url", return_value=mock_redis):
            r = client.get("/api/ready")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ready"
        assert body["checks"]["database"] == "ok"
        assert body["checks"]["redis"] == "ok"

    def test_redis_failure_returns_503(self, client):
        """Redis ping failure → service not ready → 503."""
        mock_redis = MagicMock()
        mock_redis.ping.side_effect = ConnectionError("Redis not available")
        with patch("redis.Redis.from_url", return_value=mock_redis):
            r = client.get("/api/ready")
        assert r.status_code == 503
        body = r.json()
        assert body["status"] == "degraded"
        assert "error" in body["checks"]["redis"]

    def test_db_failure_returns_503(self, client):
        """DB connection failure → service not ready → 503."""
        mock_redis = MagicMock()
        mock_redis.ping.return_value = True
        with (
            patch("redis.Redis.from_url", return_value=mock_redis),
            patch("app.api.main.open_db", side_effect=Exception("DB unavailable")),
        ):
            r = client.get("/api/ready")
        assert r.status_code == 503
        body = r.json()
        assert body["status"] == "degraded"
        assert "error" in body["checks"]["database"]

    def test_both_failing_returns_503(self, client):
        """Both DB and Redis failing → 503."""
        mock_redis = MagicMock()
        mock_redis.ping.side_effect = ConnectionError("no redis")
        with (
            patch("redis.Redis.from_url", return_value=mock_redis),
            patch("app.api.main.open_db", side_effect=Exception("no db")),
        ):
            r = client.get("/api/ready")
        assert r.status_code == 503

    def test_unexpected_exception_returns_503_not_500(self, client):
        """Unexpected exception in readiness() must not crash to 500."""
        with (
            patch("app.api.main.readiness", side_effect=RuntimeError("unexpected")),
            patch("redis.Redis.from_url", side_effect=Exception),
            patch("app.api.main.open_db", side_effect=Exception),
        ):
            r = client.get("/api/ready")
        assert r.status_code == 503

    def test_liveness_always_200(self, client):
        """/health is always 200 regardless of DB/Redis state."""
        with (
            patch("app.api.main.open_db", side_effect=Exception("no db")),
        ):
            r = client.get("/health")
        assert r.status_code == 200

    def test_readiness_response_has_timestamp(self, client):
        mock_redis = MagicMock()
        mock_redis.ping.return_value = True
        with patch("redis.Redis.from_url", return_value=mock_redis):
            r = client.get("/api/ready")
        assert "timestamp" in r.json()
