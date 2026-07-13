"""Tests for the CLI — diagnostic commands and core entity subcommands."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from app import __version__
from app.cli import app
from app.core.config import reset_config

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point every CLI call at a fresh temp database."""
    monkeypatch.setenv("ACE_DB_PATH", str(tmp_path / "test.db"))
    reset_config()
    yield
    reset_config()


# ---------------------------------------------------------------------------
# Diagnostic commands
# ---------------------------------------------------------------------------


def test_version_command() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_doctor_command() -> None:
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "Status: OK" in result.output


def test_no_args_shows_help() -> None:
    result = runner.invoke(app, [])
    assert "Usage" in result.output or result.exit_code in (0, 2)


# ---------------------------------------------------------------------------
# Topic commands
# ---------------------------------------------------------------------------


def test_topics_add_and_list() -> None:
    result = runner.invoke(app, ["topics", "add", "Black holes"])
    assert result.exit_code == 0
    assert "Created topic" in result.output

    result = runner.invoke(app, ["topics", "list"])
    assert result.exit_code == 0
    assert "Black holes" in result.output


def test_topics_add_with_angle() -> None:
    result = runner.invoke(app, ["topics", "add", "Mars", "--angle", "water evidence"])
    assert result.exit_code == 0


def test_topics_list_empty() -> None:
    result = runner.invoke(app, ["topics", "list"])
    assert result.exit_code == 0
    assert "No topics" in result.output


def test_topics_archive() -> None:
    runner.invoke(app, ["topics", "add", "T1"])
    result = runner.invoke(app, ["topics", "archive", "1"])
    assert result.exit_code == 0
    assert "archived" in result.output


def test_topics_archive_missing() -> None:
    result = runner.invoke(app, ["topics", "archive", "999"])
    assert result.exit_code != 0


def test_topics_delete_with_yes() -> None:
    runner.invoke(app, ["topics", "add", "T1"])
    result = runner.invoke(app, ["topics", "delete", "1", "--yes"])
    assert result.exit_code == 0
    assert "deleted" in result.output


def test_topics_delete_missing() -> None:
    result = runner.invoke(app, ["topics", "delete", "999", "--yes"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Source commands
# ---------------------------------------------------------------------------


def test_sources_add_and_list() -> None:
    runner.invoke(app, ["topics", "add", "T1"])
    result = runner.invoke(app, ["sources", "add", "1", "url", "https://example.com"])
    assert result.exit_code == 0
    assert "Created source" in result.output

    result = runner.invoke(app, ["sources", "list", "1"])
    assert result.exit_code == 0
    assert "https://example.com" in result.output


def test_sources_list_empty() -> None:
    runner.invoke(app, ["topics", "add", "T1"])
    result = runner.invoke(app, ["sources", "list", "1"])
    assert "No sources" in result.output


def test_sources_delete_with_yes() -> None:
    runner.invoke(app, ["topics", "add", "T1"])
    runner.invoke(app, ["sources", "add", "1", "note", "a note"])
    result = runner.invoke(app, ["sources", "delete", "1", "--yes"])
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Script commands
# ---------------------------------------------------------------------------


def test_scripts_add_and_list() -> None:
    runner.invoke(app, ["topics", "add", "T1"])
    result = runner.invoke(app, ["scripts", "add", "1", "Line one. Line two."])
    assert result.exit_code == 0
    assert "version=1" in result.output

    result = runner.invoke(app, ["scripts", "list", "1"])
    assert result.exit_code == 0
    assert "v1" in result.output


def test_scripts_auto_increment_version() -> None:
    runner.invoke(app, ["topics", "add", "T1"])
    runner.invoke(app, ["scripts", "add", "1", "v1 body"])
    result = runner.invoke(app, ["scripts", "add", "1", "v2 body"])
    assert "version=2" in result.output


def test_scripts_approve() -> None:
    runner.invoke(app, ["topics", "add", "T1"])
    runner.invoke(app, ["scripts", "add", "1", "body"])
    result = runner.invoke(app, ["scripts", "approve", "1"])
    assert result.exit_code == 0
    assert "approved" in result.output


def test_scripts_reject() -> None:
    runner.invoke(app, ["topics", "add", "T1"])
    runner.invoke(app, ["scripts", "add", "1", "body"])
    result = runner.invoke(app, ["scripts", "reject", "1"])
    assert result.exit_code == 0
    assert "rejected" in result.output


# ---------------------------------------------------------------------------
# Run commands
# ---------------------------------------------------------------------------


def test_runs_create_and_list() -> None:
    runner.invoke(app, ["topics", "add", "T1"])
    result = runner.invoke(app, ["runs", "create", "1"])
    assert result.exit_code == 0
    assert "Created run" in result.output

    result = runner.invoke(app, ["runs", "list", "1"])
    assert result.exit_code == 0
    assert "pending" in result.output


def test_runs_update_status() -> None:
    runner.invoke(app, ["topics", "add", "T1"])
    runner.invoke(app, ["runs", "create", "1"])
    result = runner.invoke(app, ["runs", "update-status", "1", "running"])
    assert result.exit_code == 0
    assert "running" in result.output


def test_runs_update_status_invalid() -> None:
    runner.invoke(app, ["topics", "add", "T1"])
    runner.invoke(app, ["runs", "create", "1"])
    result = runner.invoke(app, ["runs", "update-status", "1", "bogus"])
    assert result.exit_code != 0
