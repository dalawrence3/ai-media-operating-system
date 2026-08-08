"""Tests for ace discover CLI commands."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from app.cli import app
from app.core.config import reset_config

runner = CliRunner()


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("ACE_DB_PATH", str(db_path))
    reset_config()
    yield db_path
    reset_config()


def _add_channel(name="Test", niche="finance"):
    return runner.invoke(app, ["channels", "add", "--name", name, "--niche", niche])


# ---------------------------------------------------------------------------
# discover run
# ---------------------------------------------------------------------------


def test_discover_run_manual_single_topic(isolated_db) -> None:
    _add_channel()
    result = runner.invoke(
        app, ["discover", "run", "--channel", "1", "--topic", "personal finance tips"]
    )
    assert result.exit_code == 0, result.output
    assert "completed" in result.output
    assert "New opportunities:  1" in result.output or "New opportunities: 1" in result.output


def test_discover_run_manual_multiple_topics(isolated_db) -> None:
    _add_channel()
    result = runner.invoke(
        app,
        [
            "discover",
            "run",
            "--channel",
            "1",
            "--topic",
            "finance tips",
            "--topic",
            "budgeting basics",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "2" in result.output  # 2 new opportunities


def test_discover_run_no_topics_fails(isolated_db) -> None:
    _add_channel()
    result = runner.invoke(app, ["discover", "run", "--channel", "1"])
    assert result.exit_code == 1


def test_discover_run_missing_channel_fails(isolated_db) -> None:
    result = runner.invoke(app, ["discover", "run", "--channel", "999", "--topic", "finance"])
    assert result.exit_code == 1


def test_discover_run_shows_dedup_count(isolated_db) -> None:
    _add_channel()
    runner.invoke(app, ["discover", "run", "--channel", "1", "--topic", "finance tips"])
    result = runner.invoke(app, ["discover", "run", "--channel", "1", "--topic", "finance tips"])
    assert result.exit_code == 0, result.output
    assert "Deduplicated" in result.output


def test_discover_run_dedup_on_identical_normalized_topics(isolated_db) -> None:
    """Two topics that normalize identically → first is new, second is deduplicated."""
    _add_channel()
    result = runner.invoke(
        app,
        [
            "discover",
            "run",
            "--channel",
            "1",
            "--topic",
            "Save Money",
            "--topic",
            "Save, Money!",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Deduplicated" in result.output


# ---------------------------------------------------------------------------
# discover list
# ---------------------------------------------------------------------------


def test_discover_list_empty(isolated_db) -> None:
    _add_channel()
    result = runner.invoke(app, ["discover", "list", "--channel", "1"])
    assert result.exit_code == 0, result.output
    assert "No opportunities" in result.output


def test_discover_list_shows_opportunities(isolated_db) -> None:
    _add_channel()
    runner.invoke(app, ["discover", "run", "--channel", "1", "--topic", "finance tips"])
    result = runner.invoke(app, ["discover", "list", "--channel", "1"])
    assert result.exit_code == 0, result.output
    assert "finance tips" in result.output


def test_discover_list_filter_by_status(isolated_db) -> None:
    _add_channel()
    runner.invoke(app, ["discover", "run", "--channel", "1", "--topic", "finance tips"])
    result_new = runner.invoke(app, ["discover", "list", "--channel", "1", "--status", "new"])
    assert result_new.exit_code == 0
    assert "finance tips" in result_new.output
    result_approved = runner.invoke(
        app, ["discover", "list", "--channel", "1", "--status", "approved"]
    )
    assert result_approved.exit_code == 0
    assert "No opportunities" in result_approved.output


def test_discover_list_missing_channel_fails(isolated_db) -> None:
    result = runner.invoke(app, ["discover", "list", "--channel", "999"])
    assert result.exit_code == 1


def test_discover_list_invalid_status_fails(isolated_db) -> None:
    _add_channel()
    result = runner.invoke(app, ["discover", "list", "--channel", "1", "--status", "nonsense"])
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# discover show
# ---------------------------------------------------------------------------


def test_discover_show_success(isolated_db) -> None:
    _add_channel()
    runner.invoke(app, ["discover", "run", "--channel", "1", "--topic", "finance tips"])
    result = runner.invoke(app, ["discover", "show", "1"])
    assert result.exit_code == 0, result.output
    assert "finance tips" in result.output
    assert "Observations" in result.output
    assert "State history" in result.output


def test_discover_show_includes_evidence(isolated_db) -> None:
    _add_channel()
    runner.invoke(app, ["discover", "run", "--channel", "1", "--topic", "finance tips"])
    result = runner.invoke(app, ["discover", "show", "1"])
    assert result.exit_code == 0, result.output
    assert "manual_demand_note" in result.output


def test_discover_show_missing_opportunity_fails(isolated_db) -> None:
    result = runner.invoke(app, ["discover", "show", "9999"])
    assert result.exit_code == 1


def test_discover_show_dedup_annotation(isolated_db) -> None:
    """Second run on same topic: dedup observation should show similarity in show output."""
    _add_channel()
    runner.invoke(app, ["discover", "run", "--channel", "1", "--topic", "finance tips"])
    runner.invoke(app, ["discover", "run", "--channel", "1", "--topic", "finance tips"])
    result = runner.invoke(app, ["discover", "show", "1"])
    assert result.exit_code == 0, result.output
    assert "dedup" in result.output.lower() or "similarity" in result.output.lower()
