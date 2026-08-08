"""Tests for the ace channels CLI commands."""

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


# ---------------------------------------------------------------------------
# channels add
# ---------------------------------------------------------------------------


def test_channels_add_success(isolated_db) -> None:
    result = runner.invoke(
        app, ["channels", "add", "--name", "Finance Tips", "--niche", "personal finance"]
    )
    assert result.exit_code == 0, result.output
    assert "Created channel" in result.output
    assert "Finance Tips" in result.output
    assert "personal finance" in result.output


def test_channels_add_defaults_mode_manual(isolated_db) -> None:
    result = runner.invoke(app, ["channels", "add", "--name", "Test", "--niche", "tech"])
    assert result.exit_code == 0, result.output
    assert "manual" in result.output


def test_channels_add_defaults_stage_validation(isolated_db) -> None:
    result = runner.invoke(app, ["channels", "add", "--name", "Test", "--niche", "tech"])
    assert result.exit_code == 0, result.output
    assert "validation" in result.output


def test_channels_add_missing_name_fails(isolated_db) -> None:
    result = runner.invoke(app, ["channels", "add", "--niche", "tech"])
    assert result.exit_code != 0


def test_channels_add_missing_niche_fails(isolated_db) -> None:
    result = runner.invoke(app, ["channels", "add", "--name", "Test"])
    assert result.exit_code != 0


def test_channels_add_custom_stage(isolated_db) -> None:
    result = runner.invoke(
        app, ["channels", "add", "--name", "Test", "--niche", "tech", "--stage", "growth"]
    )
    assert result.exit_code == 0, result.output
    assert "growth" in result.output


def test_channels_add_with_operator(isolated_db) -> None:
    result = runner.invoke(
        app,
        ["channels", "add", "--name", "Test", "--niche", "tech", "--operator", "alice"],
    )
    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# channels list
# ---------------------------------------------------------------------------


def test_channels_list_empty(isolated_db) -> None:
    result = runner.invoke(app, ["channels", "list"])
    assert result.exit_code == 0, result.output
    assert "No channels found" in result.output


def test_channels_list_shows_channels(isolated_db) -> None:
    runner.invoke(app, ["channels", "add", "--name", "Alpha", "--niche", "finance"])
    runner.invoke(app, ["channels", "add", "--name", "Beta", "--niche", "tech"])
    result = runner.invoke(app, ["channels", "list"])
    assert result.exit_code == 0, result.output
    assert "Alpha" in result.output
    assert "Beta" in result.output


# ---------------------------------------------------------------------------
# channels show
# ---------------------------------------------------------------------------


def test_channels_show_success(isolated_db) -> None:
    runner.invoke(app, ["channels", "add", "--name", "Finance Tips", "--niche", "investing"])
    result = runner.invoke(app, ["channels", "show", "1"])
    assert result.exit_code == 0, result.output
    assert "Finance Tips" in result.output
    assert "investing" in result.output


def test_channels_show_missing_channel(isolated_db) -> None:
    result = runner.invoke(app, ["channels", "show", "999"])
    assert result.exit_code == 1


def test_channels_show_includes_capacity(isolated_db) -> None:
    runner.invoke(app, ["channels", "add", "--name", "X", "--niche", "y"])
    result = runner.invoke(app, ["channels", "show", "1"])
    assert result.exit_code == 0, result.output
    assert "Capacity" in result.output
    assert "ceilings" in result.output


def test_channels_show_includes_strategy(isolated_db) -> None:
    runner.invoke(app, ["channels", "add", "--name", "X", "--niche", "y"])
    result = runner.invoke(app, ["channels", "show", "1"])
    assert result.exit_code == 0, result.output
    assert "strategy" in result.output.lower()
    assert "pre" in result.output


# ---------------------------------------------------------------------------
# channels versions
# ---------------------------------------------------------------------------


def test_channels_versions_shows_history(isolated_db) -> None:
    runner.invoke(app, ["channels", "add", "--name", "X", "--niche", "y"])
    result = runner.invoke(app, ["channels", "versions", "1"])
    assert result.exit_code == 0, result.output
    assert "v1" in result.output
    assert "active" in result.output


def test_channels_versions_missing_channel(isolated_db) -> None:
    result = runner.invoke(app, ["channels", "versions", "999"])
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# channels new-version
# ---------------------------------------------------------------------------


def test_channels_new_version_creates_draft(isolated_db) -> None:
    runner.invoke(app, ["channels", "add", "--name", "X", "--niche", "budgeting"])
    result = runner.invoke(app, ["channels", "new-version", "1", "--niche", "investing"])
    assert result.exit_code == 0, result.output
    assert "v2" in result.output
    assert "investing" in result.output
    assert "draft" in result.output.lower()
    assert "Not yet active" in result.output


def test_channels_new_version_does_not_change_active_profile(isolated_db) -> None:
    """After creating a draft, show must still reflect the old active profile."""
    runner.invoke(app, ["channels", "add", "--name", "X", "--niche", "budgeting"])
    runner.invoke(app, ["channels", "new-version", "1", "--niche", "investing"])
    result = runner.invoke(app, ["channels", "show", "1"])
    assert result.exit_code == 0, result.output
    assert "budgeting" in result.output  # old active niche still shown


def test_channels_new_version_carries_forward_niche_when_omitted(isolated_db) -> None:
    """After creating a draft with --stage only and activating it, show must reflect both values."""
    runner.invoke(app, ["channels", "add", "--name", "X", "--niche", "budgeting"])
    runner.invoke(app, ["channels", "new-version", "1", "--stage", "growth"])
    runner.invoke(app, ["channels", "activate-version", "1", "2"])
    result = runner.invoke(app, ["channels", "show", "1"])
    assert result.exit_code == 0, result.output
    assert "budgeting" in result.output
    assert "growth" in result.output


def test_channels_new_version_missing_channel(isolated_db) -> None:
    result = runner.invoke(app, ["channels", "new-version", "999"])
    assert result.exit_code == 1


def test_channels_versions_after_new_version_shows_both(isolated_db) -> None:
    runner.invoke(app, ["channels", "add", "--name", "X", "--niche", "y"])
    runner.invoke(app, ["channels", "new-version", "1", "--niche", "z"])
    result = runner.invoke(app, ["channels", "versions", "1"])
    assert result.exit_code == 0, result.output
    assert "v1" in result.output
    assert "v2" in result.output
    # v1 is still active, v2 is a draft — no superseded versions yet
    assert "active" in result.output
    assert "draft" in result.output


# ---------------------------------------------------------------------------
# channels activate-version
# ---------------------------------------------------------------------------


def test_channels_activate_version_success(isolated_db) -> None:
    runner.invoke(app, ["channels", "add", "--name", "X", "--niche", "y"])
    runner.invoke(app, ["channels", "new-version", "1", "--niche", "z"])
    result = runner.invoke(
        app, ["channels", "activate-version", "1", "2", "--operator", "alice", "--reason", "pivot"]
    )
    assert result.exit_code == 0, result.output
    assert "v2" in result.output
    assert "activated" in result.output.lower()


def test_channels_activate_version_updates_show(isolated_db) -> None:
    """After activation, show must reflect the new active profile."""
    runner.invoke(app, ["channels", "add", "--name", "X", "--niche", "old-niche"])
    runner.invoke(app, ["channels", "new-version", "1", "--niche", "new-niche"])
    runner.invoke(app, ["channels", "activate-version", "1", "2"])
    result = runner.invoke(app, ["channels", "show", "1"])
    assert result.exit_code == 0, result.output
    assert "new-niche" in result.output


def test_channels_activate_version_rejects_already_active(isolated_db) -> None:
    runner.invoke(app, ["channels", "add", "--name", "X", "--niche", "y"])
    result = runner.invoke(app, ["channels", "activate-version", "1", "1"])
    assert result.exit_code == 1


def test_channels_activate_version_missing_channel(isolated_db) -> None:
    result = runner.invoke(app, ["channels", "activate-version", "999", "1"])
    assert result.exit_code == 1


def test_channels_activate_version_missing_version(isolated_db) -> None:
    runner.invoke(app, ["channels", "add", "--name", "X", "--niche", "y"])
    result = runner.invoke(app, ["channels", "activate-version", "1", "99"])
    assert result.exit_code == 1


def test_channels_versions_after_activation_shows_superseded(isolated_db) -> None:
    runner.invoke(app, ["channels", "add", "--name", "X", "--niche", "y"])
    runner.invoke(app, ["channels", "new-version", "1", "--niche", "z"])
    runner.invoke(app, ["channels", "activate-version", "1", "2"])
    result = runner.invoke(app, ["channels", "versions", "1"])
    assert result.exit_code == 0, result.output
    assert "superseded" in result.output
    assert "active" in result.output


# ---------------------------------------------------------------------------
# channels new-strategy
# ---------------------------------------------------------------------------


def test_channels_new_strategy_creates_draft(isolated_db) -> None:
    runner.invoke(app, ["channels", "add", "--name", "X", "--niche", "y"])
    weights = '{"ad_revenue": 0.7, "contribution_margin": 0.3}'
    result = runner.invoke(
        app,
        ["channels", "new-strategy", "1", "--weights", weights, "--status", "active"],
    )
    assert result.exit_code == 0, result.output
    assert "v2" in result.output
    # The monetization status (from --status active) appears in output
    assert "active" in result.output
    assert "draft" in result.output.lower() or "Not yet active" in result.output


def test_channels_new_strategy_does_not_change_active_strategy(isolated_db) -> None:
    """After creating a strategy draft, show must still reflect the original active strategy."""
    runner.invoke(app, ["channels", "add", "--name", "X", "--niche", "y"])
    weights = '{"ad_revenue": 1.0}'
    runner.invoke(app, ["channels", "new-strategy", "1", "--weights", weights])
    result = runner.invoke(app, ["channels", "show", "1"])
    assert result.exit_code == 0, result.output
    # Original strategy is still active (pre-monetization weights)
    assert "pre" in result.output


def test_channels_new_strategy_invalid_json(isolated_db) -> None:
    runner.invoke(app, ["channels", "add", "--name", "X", "--niche", "y"])
    result = runner.invoke(app, ["channels", "new-strategy", "1", "--weights", "{bad json}"])
    assert result.exit_code == 1


def test_channels_new_strategy_invalid_weights(isolated_db) -> None:
    runner.invoke(app, ["channels", "add", "--name", "X", "--niche", "y"])
    # Weights sum to 0.5, not 1.0
    result = runner.invoke(
        app,
        ["channels", "new-strategy", "1", "--weights", '{"ad_revenue": 0.5}'],
    )
    assert result.exit_code == 1


def test_channels_new_strategy_missing_channel(isolated_db) -> None:
    result = runner.invoke(
        app, ["channels", "new-strategy", "999", "--weights", '{"ad_revenue": 1.0}']
    )
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# channels activate-strategy
# ---------------------------------------------------------------------------


def test_channels_activate_strategy_success(isolated_db) -> None:
    runner.invoke(app, ["channels", "add", "--name", "X", "--niche", "y"])
    weights = '{"ad_revenue": 1.0}'
    runner.invoke(app, ["channels", "new-strategy", "1", "--weights", weights])
    result = runner.invoke(
        app,
        ["channels", "activate-strategy", "1", "2", "--operator", "bob", "--reason", "upgrade"],
    )
    assert result.exit_code == 0, result.output
    assert "v2" in result.output
    assert "activated" in result.output.lower()


def test_channels_activate_strategy_updates_show(isolated_db) -> None:
    """After activation, show must reflect the new active strategy."""
    runner.invoke(app, ["channels", "add", "--name", "X", "--niche", "y"])
    weights = '{"ad_revenue": 1.0}'
    runner.invoke(
        app, ["channels", "new-strategy", "1", "--weights", weights, "--status", "active"]
    )
    runner.invoke(app, ["channels", "activate-strategy", "1", "2"])
    result = runner.invoke(app, ["channels", "show", "1"])
    assert result.exit_code == 0, result.output
    assert "active" in result.output
    assert "ad_revenue" in result.output


def test_channels_activate_strategy_rejects_already_active(isolated_db) -> None:
    runner.invoke(app, ["channels", "add", "--name", "X", "--niche", "y"])
    result = runner.invoke(app, ["channels", "activate-strategy", "1", "1"])
    assert result.exit_code == 1


def test_channels_activate_strategy_missing_channel(isolated_db) -> None:
    result = runner.invoke(app, ["channels", "activate-strategy", "999", "1"])
    assert result.exit_code == 1


def test_channels_activate_strategy_missing_version(isolated_db) -> None:
    runner.invoke(app, ["channels", "add", "--name", "X", "--niche", "y"])
    result = runner.invoke(app, ["channels", "activate-strategy", "1", "99"])
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# channels set-mode
# ---------------------------------------------------------------------------


def test_channels_set_mode_manual_allowed(isolated_db) -> None:
    runner.invoke(app, ["channels", "add", "--name", "X", "--niche", "y"])
    result = runner.invoke(app, ["channels", "set-mode", "1", "manual"])
    assert result.exit_code == 0, result.output


def test_channels_set_mode_supervised_rejected_phase3(isolated_db) -> None:
    runner.invoke(app, ["channels", "add", "--name", "X", "--niche", "y"])
    result = runner.invoke(app, ["channels", "set-mode", "1", "supervised"])
    assert result.exit_code == 1
    assert "reserved" in result.output or "reserved" in (
        result.stderr if hasattr(result, "stderr") else ""
    )


def test_channels_set_mode_autonomous_rejected_phase3(isolated_db) -> None:
    runner.invoke(app, ["channels", "add", "--name", "X", "--niche", "y"])
    result = runner.invoke(app, ["channels", "set-mode", "1", "autonomous"])
    assert result.exit_code == 1


def test_channels_set_mode_missing_channel(isolated_db) -> None:
    result = runner.invoke(app, ["channels", "set-mode", "999", "manual"])
    assert result.exit_code == 1


def test_channels_set_mode_already_manual_is_noop(isolated_db) -> None:
    runner.invoke(app, ["channels", "add", "--name", "X", "--niche", "y"])
    result = runner.invoke(app, ["channels", "set-mode", "1", "manual"])
    assert result.exit_code == 0
    assert "already" in result.output


# ---------------------------------------------------------------------------
# channels capacity
# ---------------------------------------------------------------------------


def test_channels_capacity_shows_defaults(isolated_db) -> None:
    runner.invoke(app, ["channels", "add", "--name", "X", "--niche", "y"])
    result = runner.invoke(app, ["channels", "capacity", "1"])
    assert result.exit_code == 0, result.output
    assert "ceilings" in result.output or "not quotas" in result.output.lower()
    assert "2" in result.output  # long_form_slots_per_week default
    assert "4" in result.output  # short_slots_per_week default


def test_channels_capacity_missing_channel(isolated_db) -> None:
    result = runner.invoke(app, ["channels", "capacity", "999"])
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# channels set-capacity
# ---------------------------------------------------------------------------


def test_channels_set_capacity_updates_slots(isolated_db) -> None:
    runner.invoke(app, ["channels", "add", "--name", "X", "--niche", "y"])
    result = runner.invoke(
        app, ["channels", "set-capacity", "1", "--long-form-slots", "3", "--short-slots", "6"]
    )
    assert result.exit_code == 0, result.output
    assert "3" in result.output


def test_channels_set_capacity_missing_channel(isolated_db) -> None:
    result = runner.invoke(app, ["channels", "set-capacity", "999", "--long-form-slots", "3"])
    assert result.exit_code == 1
