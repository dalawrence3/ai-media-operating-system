"""Tests for environment-aware CORS configuration.

Verifies: production does not include localhost origins, development does,
and operator-configured ACE_CORS_ORIGINS are always included.
"""

from __future__ import annotations

import pytest

from app.core.config import reset_config


@pytest.fixture(autouse=True)
def _reset_config():
    reset_config()
    yield
    reset_config()


def _get_cors_origins_from_app(monkeypatch, env: str, cors_origins: str = "") -> list[str]:
    """Import main and extract the CORS origins list it assembled."""
    monkeypatch.setenv("ACE_ENV", env)
    monkeypatch.setenv("ACE_SECRET_KEY", "a" * 32)
    if cors_origins:
        monkeypatch.setenv("ACE_CORS_ORIGINS", cors_origins)
    reset_config()

    # Re-import to pick up new env state.
    import importlib

    import app.api.main as _main

    importlib.reload(_main)
    return list(_main._CORS_ORIGINS)


class TestCORSProduction:
    def test_localhost_not_in_production_origins(self, monkeypatch):
        origins = _get_cors_origins_from_app(monkeypatch, "production")
        assert not any("localhost" in o for o in origins), (
            f"localhost must not appear in production CORS origins: {origins}"
        )

    def test_empty_origins_allowed_in_production(self, monkeypatch):
        """Production with no ACE_CORS_ORIGINS → empty list (strict)."""
        origins = _get_cors_origins_from_app(monkeypatch, "production")
        assert origins == []

    def test_configured_origin_accepted_in_production(self, monkeypatch):
        origins = _get_cors_origins_from_app(
            monkeypatch, "production", "https://studio.example.com"
        )
        assert "https://studio.example.com" in origins
        assert not any("localhost" in o for o in origins)

    def test_multiple_configured_origins_accepted(self, monkeypatch):
        origins = _get_cors_origins_from_app(
            monkeypatch,
            "production",
            "https://app.example.com,https://beta.example.com",
        )
        assert "https://app.example.com" in origins
        assert "https://beta.example.com" in origins


class TestCORSDevelopment:
    def test_localhost_5173_in_development(self, monkeypatch):
        origins = _get_cors_origins_from_app(monkeypatch, "development")
        assert "http://localhost:5173" in origins

    def test_localhost_4173_in_development(self, monkeypatch):
        origins = _get_cors_origins_from_app(monkeypatch, "development")
        assert "http://localhost:4173" in origins

    def test_configured_origin_also_included_in_development(self, monkeypatch):
        origins = _get_cors_origins_from_app(
            monkeypatch, "development", "https://staging.example.com"
        )
        assert "http://localhost:5173" in origins
        assert "https://staging.example.com" in origins
