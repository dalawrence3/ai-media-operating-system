"""Tests for the ace intelligence CLI commands (scoring & policy management)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from app.cli import app
from app.intelligence.models import (
    AdapterName,
    DiscoveryRun,
    MissingDataPolicy,
    Opportunity,
    OpportunityObservation,
    RunStatus,
    ScoringPolicy,
    SourceQualityTier,
)
from app.intelligence.repository import (
    activate_scoring_policy,
    create_channel_full,
    create_discovery_run,
    create_observation,
    create_opportunity,
    create_scoring_policy,
    get_scoring_policy,
    list_scoring_policies,
)

runner = CliRunner()


@pytest.fixture(autouse=True)
def _reset_config():
    """Reset the config singleton before and after each test so ACE_DB_PATH is re-read."""
    from app.core.config import reset_config
    reset_config()
    yield
    reset_config()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_channel(db: sqlite3.Connection, name: str = "CLI Test Channel"):
    return create_channel_full(db, channel_name=name, primary_niche="personal finance")


def _make_run(db: sqlite3.Connection, channel_id: int, profile_version_id: int) -> DiscoveryRun:
    return create_discovery_run(db, DiscoveryRun(
        channel_id=channel_id,
        profile_version_id=profile_version_id,
        adapter_name=AdapterName.manual,
        status=RunStatus.running,
    ))


def _make_policy(db: sqlite3.Connection, channel_id: int, activate: bool = True) -> ScoringPolicy:
    existing = list_scoring_policies(db, channel_id)
    next_version = max((p.version for p in existing), default=0) + 1
    policy = create_scoring_policy(db, ScoringPolicy(
        channel_id=channel_id,
        version=next_version,
        label="test policy",
        missing_competition=MissingDataPolicy.reweight_available,
    ))
    if activate:
        activate_scoring_policy(db, policy.id)
        db.commit()
    return get_scoring_policy(db, policy.id)


def _make_opp(db: sqlite3.Connection, channel_id: int, profile_version_id: int) -> Opportunity:
    run = _make_run(db, channel_id, profile_version_id)
    opp = create_opportunity(db, Opportunity(
        channel_id=channel_id,
        raw_topic="index fund basics",
        normalized_topic="index fund basics",
        discovery_run_id=run.id,
    ))
    db.commit()
    return opp


def _invoke(*args, db_path: str | None = None, env: dict | None = None):
    extra_env = {"ACE_DB_PATH": db_path} if db_path else {}
    if env:
        extra_env.update(env)
    return runner.invoke(app, list(args), env=extra_env, catch_exceptions=False)


# ---------------------------------------------------------------------------
# policy list
# ---------------------------------------------------------------------------


def test_policy_list_empty(tmp_path: Path) -> None:
    from app.core.database import open_db
    db = open_db(tmp_path / "test.db")
    channel, _, _, _ = _make_channel(db)
    db.commit()
    db.close()

    result = _invoke(
        "intelligence", "policy", "list",
        "--channel", str(channel.id),
        db_path=str(tmp_path / "test.db"),
    )
    assert result.exit_code == 0
    assert "No scoring policies" in result.output


def test_policy_list_shows_policies(tmp_path: Path) -> None:
    from app.core.database import open_db
    db = open_db(tmp_path / "test.db")
    channel, _, _, _ = _make_channel(db)
    _make_policy(db, channel.id, activate=True)
    db.commit()
    db.close()

    result = _invoke(
        "intelligence", "policy", "list",
        "--channel", str(channel.id),
        db_path=str(tmp_path / "test.db"),
    )
    assert result.exit_code == 0
    assert "test policy" in result.output
    assert "DEFAULT" in result.output


# ---------------------------------------------------------------------------
# policy show
# ---------------------------------------------------------------------------


def test_policy_show(tmp_path: Path) -> None:
    from app.core.database import open_db
    db = open_db(tmp_path / "test.db")
    channel, _, _, _ = _make_channel(db)
    policy = _make_policy(db, channel.id)
    db.commit()
    db.close()

    result = _invoke(
        "intelligence", "policy", "show", str(policy.id),
        db_path=str(tmp_path / "test.db"),
    )
    assert result.exit_code == 0
    assert "test policy" in result.output
    assert "weight" in result.output.lower()


# ---------------------------------------------------------------------------
# policy create
# ---------------------------------------------------------------------------


def test_policy_create(tmp_path: Path) -> None:
    from app.core.database import open_db
    db = open_db(tmp_path / "test.db")
    channel, _, _, _ = _make_channel(db)
    db.commit()
    db.close()

    result = _invoke(
        "intelligence", "policy", "create",
        "--channel", str(channel.id),
        "--label", "new policy",
        db_path=str(tmp_path / "test.db"),
    )
    assert result.exit_code == 0
    assert "draft" in result.output.lower() or "Created" in result.output


# ---------------------------------------------------------------------------
# policy activate (guard + confirm)
# ---------------------------------------------------------------------------


def test_policy_activate_guard_without_confirm(tmp_path: Path) -> None:
    from app.core.database import open_db
    db = open_db(tmp_path / "test.db")
    channel, _, _, _ = _make_channel(db)
    policy = create_scoring_policy(db, ScoringPolicy(
        channel_id=channel.id, version=1, label="p1",
        missing_competition=MissingDataPolicy.reweight_available,
    ))
    db.commit()
    db.close()

    result = _invoke(
        "intelligence", "policy", "activate", str(policy.id),
        db_path=str(tmp_path / "test.db"),
    )
    # Should print informational message and exit 0 (guard, not error)
    assert result.exit_code == 0
    assert "--confirm" in result.output


def test_policy_activate_with_confirm(tmp_path: Path) -> None:
    from app.core.database import open_db
    db = open_db(tmp_path / "test.db")
    channel, _, _, _ = _make_channel(db)
    policy = create_scoring_policy(db, ScoringPolicy(
        channel_id=channel.id, version=1, label="p1",
        missing_competition=MissingDataPolicy.reweight_available,
    ))
    db.commit()
    db.close()

    result = _invoke(
        "intelligence", "policy", "activate", str(policy.id), "--confirm",
        db_path=str(tmp_path / "test.db"),
    )
    assert result.exit_code == 0
    assert "activated" in result.output.lower()


# ---------------------------------------------------------------------------
# policy clone
# ---------------------------------------------------------------------------


def test_policy_clone(tmp_path: Path) -> None:
    from app.core.database import open_db
    db = open_db(tmp_path / "test.db")
    channel, _, _, _ = _make_channel(db)
    policy = _make_policy(db, channel.id)
    db.commit()
    db.close()

    result = _invoke(
        "intelligence", "policy", "clone", str(policy.id),
        "--label", "cloned policy",
        db_path=str(tmp_path / "test.db"),
    )
    assert result.exit_code == 0
    assert "cloned" in result.output.lower() or "Cloned" in result.output


# ---------------------------------------------------------------------------
# policy update
# ---------------------------------------------------------------------------


def test_policy_update_label(tmp_path: Path) -> None:
    from app.core.database import open_db
    db = open_db(tmp_path / "test.db")
    channel, _, _, _ = _make_channel(db)
    policy = create_scoring_policy(db, ScoringPolicy(
        channel_id=channel.id, version=1, label="old",
        missing_competition=MissingDataPolicy.reweight_available,
    ))
    db.commit()
    db.close()

    result = _invoke(
        "intelligence", "policy", "update", str(policy.id),
        "--label", "updated label",
        db_path=str(tmp_path / "test.db"),
    )
    assert result.exit_code == 0
    assert "updated" in result.output.lower() or "updated label" in result.output


# ---------------------------------------------------------------------------
# policy archive
# ---------------------------------------------------------------------------


def test_policy_archive_non_default(tmp_path: Path) -> None:
    from app.core.database import open_db
    db = open_db(tmp_path / "test.db")
    channel, _, _, _ = _make_channel(db)
    p1 = _make_policy(db, channel.id)
    p2 = create_scoring_policy(db, ScoringPolicy(
        channel_id=channel.id, version=2, label="p2",
        missing_competition=MissingDataPolicy.reweight_available,
    ))
    activate_scoring_policy(db, p2.id)
    activate_scoring_policy(db, p1.id)  # p1 is now default again
    db.commit()
    db.close()

    result = _invoke(
        "intelligence", "policy", "archive", str(p2.id),
        db_path=str(tmp_path / "test.db"),
    )
    assert result.exit_code == 0
    assert "archived" in result.output.lower()


# ---------------------------------------------------------------------------
# intelligence score
# ---------------------------------------------------------------------------


def test_intelligence_score_missing_opportunity_exits_1(tmp_path: Path) -> None:
    from app.core.database import open_db
    db = open_db(tmp_path / "test.db")
    channel, _, _, _ = _make_channel(db)
    _make_policy(db, channel.id)
    db.commit()
    db.close()

    result = runner.invoke(
        app,
        ["intelligence", "score", "--opportunity", "9999"],
        env={"ACE_DB_PATH": str(tmp_path / "test.db")},
        catch_exceptions=False,
    )
    assert result.exit_code != 0


def test_intelligence_score_outputs_composite(tmp_path: Path) -> None:
    from app.core.database import open_db
    db = open_db(tmp_path / "test.db")
    channel, profile, _, _ = _make_channel(db)
    _make_policy(db, channel.id)
    opp = _make_opp(db, channel.id, profile.id)
    run = _make_run(db, channel.id, profile.id)
    create_observation(db, OpportunityObservation(
        opportunity_id=opp.id,
        discovery_run_id=run.id,
        adapter_name=AdapterName.youtube_data_api,
        source_quality_tier=SourceQualityTier.medium,
        signal_age_days=20.0,
    ))
    db.commit()
    db.close()

    result = _invoke(
        "intelligence", "score", "--opportunity", str(opp.id),
        db_path=str(tmp_path / "test.db"),
    )
    assert result.exit_code == 0
    assert "Composite Score" in result.output
    assert "Confidence" in result.output
