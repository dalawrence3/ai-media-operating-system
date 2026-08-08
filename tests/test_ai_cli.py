"""Tests for Phase 2 CLI commands: ace ai prompts list/show, ace ai demo."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from app.cli import app
from app.core.config import reset_config

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point every CLI call at a fresh temp database and reset config cache."""
    monkeypatch.setenv("ACE_DB_PATH", str(tmp_path / "test.db"))
    reset_config()
    yield
    reset_config()


# ── ace ai prompts list ────────────────────────────────────────────────────────


def test_prompts_list_shows_demo_echo() -> None:
    result = runner.invoke(app, ["ai", "prompts", "list"])
    assert result.exit_code == 0
    assert "demo-echo" in result.output


def test_prompts_list_includes_version() -> None:
    result = runner.invoke(app, ["ai", "prompts", "list"])
    assert result.exit_code == 0
    assert "v1" in result.output


# ── ace ai prompts show ────────────────────────────────────────────────────────


def test_prompts_show_demo_echo() -> None:
    result = runner.invoke(app, ["ai", "prompts", "show", "demo-echo", "1"])
    assert result.exit_code == 0
    assert "demo-echo" in result.output
    assert "Version" in result.output
    assert "Description" in result.output
    assert "System" in result.output
    assert "User template" in result.output


def test_prompts_show_default_version() -> None:
    result = runner.invoke(app, ["ai", "prompts", "show", "demo-echo"])
    assert result.exit_code == 0
    assert "demo-echo" in result.output


def test_prompts_show_missing_prompt() -> None:
    result = runner.invoke(app, ["ai", "prompts", "show", "nonexistent", "99"])
    assert result.exit_code == 1


# ── ace ai demo ───────────────────────────────────────────────────────────────


def test_demo_succeeds_with_fake_provider() -> None:
    result = runner.invoke(app, ["ai", "demo", "--text", "hello"])
    assert result.exit_code == 0, result.output
    assert "Provider" in result.output
    assert "fake" in result.output


def test_demo_records_usage(tmp_path: Path) -> None:
    result = runner.invoke(app, ["ai", "demo", "--text", "record me"])
    assert result.exit_code == 0

    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM ai_calls LIMIT 1").fetchone()
    conn.close()
    assert row is not None
    assert row["status"] == "success"


def test_demo_shows_parsed_result() -> None:
    result = runner.invoke(app, ["ai", "demo", "--text", "echo this"])
    assert result.exit_code == 0
    assert "echo" in result.output.lower()


def test_demo_shows_usage_id() -> None:
    result = runner.invoke(app, ["ai", "demo"])
    assert result.exit_code == 0
    assert "Usage id" in result.output


def test_demo_live_without_key_fails_safely(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACE_ANTHROPIC_API_KEY", "")
    monkeypatch.delenv("ACE_DRY_RUN", raising=False)  # dry_run bypasses the key check
    reset_config()
    result = runner.invoke(app, ["ai", "demo", "--live"])
    assert result.exit_code != 0
    assert "ACE_ANTHROPIC_API_KEY" in result.output or "not set" in result.output


def test_demo_default_text() -> None:
    result = runner.invoke(app, ["ai", "demo"])
    assert result.exit_code == 0
    assert "Phase 2" in result.output or "Provider" in result.output
