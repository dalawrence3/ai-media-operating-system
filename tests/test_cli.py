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


# ---------------------------------------------------------------------------
# topics promote (M3.4)
# ---------------------------------------------------------------------------


@pytest.fixture()
def _scored_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """
    Set up a DB with one channel, one opportunity, and one score, then point
    CLI env vars at it. Yields (db_path, opportunity_id).
    """

    from app.core.config import reset_config as _reset
    from app.core.database import open_db as _open_db
    from app.intelligence.models import (
        AdapterName,
        DiscoveryRun,
        FactorStatus,
        MissingDataPolicy,
        Opportunity,
        OpportunityScore,
        RunStatus,
        ScoringPolicy,
    )
    from app.intelligence.repository import (
        activate_scoring_policy,
        create_channel_full,
        create_discovery_run,
        create_opportunity,
        create_opportunity_score,
        create_scoring_policy,
    )

    db_path = tmp_path / "test.db"
    monkeypatch.setenv("ACE_DB_PATH", str(db_path))
    _reset()

    db = _open_db(db_path)
    channel, profile, _, _ = create_channel_full(
        db, channel_name="Finance", primary_niche="personal finance"
    )
    run = create_discovery_run(
        db,
        DiscoveryRun(
            channel_id=channel.id,
            profile_version_id=profile.id,
            adapter_name=AdapterName.manual,
            status=RunStatus.completed,
        ),
    )
    db.commit()
    opp = create_opportunity(
        db,
        Opportunity(
            channel_id=channel.id,
            discovery_run_id=run.id,
            normalized_topic="index fund basics",
            raw_topic="index fund basics",
        ),
    )
    db.commit()

    policy = create_scoring_policy(
        db,
        ScoringPolicy(
            channel_id=channel.id,
            version=1,
            label="default",
            missing_competition=MissingDataPolicy.reweight_available,
        ),
    )
    activate_scoring_policy(db, policy.id)
    db.commit()

    create_opportunity_score(
        db,
        OpportunityScore(
            opportunity_id=opp.id,
            scoring_policy_id=policy.id,
            channel_profile_version_id=profile.id,
            composite_score=0.72,
            confidence=0.61,
            status_trend_strength=FactorStatus.present,
            status_audience_demand=FactorStatus.present,
            status_competition=FactorStatus.present,
            status_evergreen_value=FactorStatus.present,
            status_audience_fit=FactorStatus.present,
            status_content_novelty=FactorStatus.present,
            eff_weight_trend_strength=0.05,
            eff_weight_audience_demand=0.20,
            eff_weight_competition=0.15,
            eff_weight_evergreen_value=0.20,
            eff_weight_audience_fit=0.30,
            eff_weight_content_novelty=0.10,
            input_hash="abc",
            scorer_version="1.0",
        ),
    )
    db.commit()
    db.close()

    yield db_path, opp.id
    _reset()


@pytest.fixture()
def _unscored_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """DB with a channel and opportunity but no score."""
    from app.core.config import reset_config as _reset
    from app.core.database import open_db as _open_db
    from app.intelligence.models import AdapterName, DiscoveryRun, Opportunity, RunStatus
    from app.intelligence.repository import (
        create_channel_full,
        create_discovery_run,
        create_opportunity,
    )

    db_path = tmp_path / "test.db"
    monkeypatch.setenv("ACE_DB_PATH", str(db_path))
    _reset()

    db = _open_db(db_path)
    channel, profile, _, _ = create_channel_full(
        db, channel_name="Finance", primary_niche="personal finance"
    )
    run = create_discovery_run(
        db,
        DiscoveryRun(
            channel_id=channel.id,
            profile_version_id=profile.id,
            adapter_name=AdapterName.manual,
            status=RunStatus.completed,
        ),
    )
    db.commit()
    opp = create_opportunity(
        db,
        Opportunity(
            channel_id=channel.id,
            discovery_run_id=run.id,
            normalized_topic="index fund basics",
            raw_topic="index fund basics",
        ),
    )
    db.commit()
    db.close()

    yield db_path, opp.id
    _reset()


def test_topics_promote_success(_scored_db) -> None:
    _, opp_id = _scored_db
    result = runner.invoke(app, ["topics", "promote", str(opp_id)])
    assert result.exit_code == 0, result.output
    assert f"Promoted opportunity [{opp_id}]" in result.output
    assert "Topic [" in result.output


def test_topics_promote_shows_in_topics_list(_scored_db) -> None:
    _, opp_id = _scored_db
    runner.invoke(app, ["topics", "promote", str(opp_id)])
    result = runner.invoke(app, ["topics", "list"])
    assert result.exit_code == 0
    assert "index fund basics" in result.output


def test_topics_promote_shows_score_in_output(_scored_db) -> None:
    _, opp_id = _scored_db
    result = runner.invoke(app, ["topics", "promote", str(opp_id)])
    assert result.exit_code == 0, result.output
    assert "Score:" in result.output
    assert "Confidence:" in result.output


def test_topics_promote_idempotent(_scored_db) -> None:
    _, opp_id = _scored_db
    runner.invoke(app, ["topics", "promote", str(opp_id)])
    result = runner.invoke(app, ["topics", "promote", str(opp_id)])
    assert result.exit_code == 0, result.output
    assert "already promoted" in result.output

    result2 = runner.invoke(app, ["topics", "list"])
    topic_lines = [ln for ln in result2.output.splitlines() if ln.strip().startswith("[")]
    assert len(topic_lines) == 1


def test_topics_promote_missing_opportunity_fails() -> None:
    result = runner.invoke(app, ["topics", "promote", "9999"])
    assert result.exit_code != 0


def test_topics_promote_no_score_fails(_unscored_db) -> None:
    _, opp_id = _unscored_db
    result = runner.invoke(app, ["topics", "promote", str(opp_id)])
    assert result.exit_code != 0
    assert "no score" in result.output.lower()


def test_topics_promote_allow_unscored_warns_and_succeeds(_unscored_db) -> None:
    _, opp_id = _unscored_db
    result = runner.invoke(app, ["topics", "promote", str(opp_id), "--allow-unscored"])
    assert result.exit_code == 0, result.output
    assert "Warning" in result.output
    assert f"Promoted opportunity [{opp_id}]" in result.output


def test_topics_promote_angle_override(_scored_db) -> None:
    _, opp_id = _scored_db
    result = runner.invoke(app, ["topics", "promote", str(opp_id), "--angle", "custom angle"])
    assert result.exit_code == 0, result.output
    result2 = runner.invoke(app, ["topics", "list"])
    assert "custom angle" in result2.output


def test_topics_promote_operator_option(_scored_db) -> None:
    _, opp_id = _scored_db
    result = runner.invoke(app, ["topics", "promote", str(opp_id), "--operator", "alice"])
    assert result.exit_code == 0, result.output
