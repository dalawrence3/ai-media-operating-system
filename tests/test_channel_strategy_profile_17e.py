"""Backend tests for Phase 17E — Channel Strategy Profile.

Covers:
- app.intelligence.experiments.strategy_policy: validation, effective-state
  computation (honest bootstrap/steady_state resolution from real channel
  maturity), and PlanningPolicy translation.
- GET/POST /workspaces/{ws}/channels/{id}/strategy(/history) — versioning,
  channel isolation, validation errors, no destructive overwrite.
- Planner integration: build_portfolio_plan falls back safely when no
  strategy exists, and consumes an active strategy's weights when one does.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.control_plane import repository as cp_repo
from app.control_plane.models import ChannelDraft, WorkspaceDraft
from app.core.config import reset_config
from app.core.database import open_db
from app.intelligence.experiments.strategy_policy import (
    MATURITY_RANK,
    compute_effective_strategy_state,
    default_bootstrap_strategy_config,
    load_policy_for_channel,
    strategy_config_to_planning_policy,
    validate_strategy_config,
)

_SECRET = "test-secret-strategy-32-bytes-ok!"
NOW = "2026-01-01T00:00:00"


def _uid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Pure-function tests — strategy_policy module
# ---------------------------------------------------------------------------


def test_default_bootstrap_config_is_valid():
    assert validate_strategy_config(default_bootstrap_strategy_config()) == []


def test_default_config_has_no_hardcoded_topics():
    cfg = default_bootstrap_strategy_config()
    serialized = str(cfg).lower()
    for banned in ("ai", "finance", "language learning", "science"):
        # Whole-word-ish check: these must not appear anywhere in the config,
        # not just as topic-family values.
        assert banned not in serialized
        assert banned not in [str(v).lower() for v in cfg.get("creative_dimensions", [])]


def test_validate_rejects_missing_regime():
    cfg = default_bootstrap_strategy_config()
    del cfg["bootstrap"]
    errors = validate_strategy_config(cfg)
    assert any("bootstrap" in e for e in errors)


def test_validate_rejects_out_of_range_weight():
    cfg = default_bootstrap_strategy_config()
    cfg["bootstrap"]["market_intelligence_weight"] = 1.5
    errors = validate_strategy_config(cfg)
    assert any("market_intelligence_weight" in e for e in errors)


def test_validate_rejects_bad_maturity_threshold():
    cfg = default_bootstrap_strategy_config()
    cfg["transition"]["maturity_threshold"] = "very_sure"
    errors = validate_strategy_config(cfg)
    assert any("maturity_threshold" in e for e in errors)


def test_validate_rejects_non_string_creative_dimensions():
    cfg = default_bootstrap_strategy_config()
    cfg["creative_dimensions"] = [1, 2, 3]
    errors = validate_strategy_config(cfg)
    assert any("creative_dimensions" in e for e in errors)


def test_maturity_rank_is_ordered():
    assert MATURITY_RANK["insufficient"] < MATURITY_RANK["exploratory"]
    assert MATURITY_RANK["exploratory"] < MATURITY_RANK["directional"]
    assert MATURITY_RANK["directional"] < MATURITY_RANK["actionable"]


def test_effective_state_is_bootstrap_when_no_baselines_exist(db_conn_bare):
    cfg = default_bootstrap_strategy_config()
    effective = compute_effective_strategy_state(db_conn_bare, "some-channel-id", cfg)
    assert effective["effective_regime"] == "bootstrap"
    assert effective["current_maturity"] == "insufficient"
    assert effective["market_intelligence_weight"] == cfg["bootstrap"]["market_intelligence_weight"]


def test_effective_state_transitions_to_steady_state_once_matured(db_conn_bare):
    from app.learning.cross_publication import _compute_stats, _upsert_channel_baseline

    channel_id = "matured-channel"
    # 5 publications → directional maturity (4-9 range).
    stats = _compute_stats([10.0, 20.0, 30.0, 40.0, 50.0])
    _upsert_channel_baseline(
        db_conn_bare,
        channel_id=channel_id,
        workspace_id=None,
        metric_name="average_view_percentage",
        period_type="lifetime",
        stats=stats,
        source_publication_ids=[1, 2, 3, 4, 5],
        source_snapshot_ids=[1, 2, 3, 4, 5],
        input_hash="hash-matured",
    )
    db_conn_bare.commit()

    cfg = default_bootstrap_strategy_config()
    effective = compute_effective_strategy_state(db_conn_bare, channel_id, cfg)
    assert effective["current_maturity"] == "directional"
    assert effective["effective_regime"] == "steady_state"
    assert (
        effective["market_intelligence_weight"] == cfg["steady_state"]["market_intelligence_weight"]
    )


def test_policy_translation_weights_sum_correctly():
    cfg = default_bootstrap_strategy_config()
    effective = {
        "market_intelligence_weight": 0.8,
        "channel_evidence_weight": 0.2,
        "exploration_share": 0.67,
    }
    policy = strategy_config_to_planning_policy(cfg, effective)
    total = (
        policy.w_exploitation_attractiveness
        + policy.w_exploitation_evidence
        + policy.w_exploitation_feasibility
    )
    assert abs(total - 1.0) < 1e-6
    assert policy.w_exploitation_attractiveness > policy.w_exploitation_evidence


def test_policy_translation_market_heavy_bootstrap_favors_attractiveness():
    cfg = default_bootstrap_strategy_config()
    bootstrap_policy = strategy_config_to_planning_policy(
        cfg,
        {
            "market_intelligence_weight": 0.8,
            "channel_evidence_weight": 0.2,
            "exploration_share": 0.67,
        },
    )
    steady_policy = strategy_config_to_planning_policy(
        cfg,
        {
            "market_intelligence_weight": 0.4,
            "channel_evidence_weight": 0.6,
            "exploration_share": 0.2,
        },
    )
    # Bootstrap favors market attractiveness more than steady-state does.
    assert (
        bootstrap_policy.w_exploitation_attractiveness > steady_policy.w_exploitation_attractiveness
    )
    # Steady-state favors channel evidence more than bootstrap does.
    assert steady_policy.w_exploitation_evidence > bootstrap_policy.w_exploitation_evidence
    # Steady-state allocates fewer exploration slots than bootstrap.
    assert steady_policy.max_exploration_slots <= bootstrap_policy.max_exploration_slots


def test_load_policy_for_channel_falls_back_when_unmapped(db_conn_bare):
    from app.intelligence.experiments.planning import PlanningPolicy

    policy = load_policy_for_channel(db_conn_bare, 999999)
    default = PlanningPolicy.v1()
    assert policy.max_exploration_slots == default.max_exploration_slots
    assert policy.w_exploitation_attractiveness == default.w_exploitation_attractiveness


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_config():
    reset_config()
    yield
    reset_config()


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "strategy_test.db"


@pytest.fixture()
def db_conn_bare(db_path: Path, monkeypatch):
    monkeypatch.setenv("ACE_DB_PATH", str(db_path))
    conn = open_db(db_path)
    yield conn
    conn.close()


@pytest.fixture()
def dev_client(db_path: Path, monkeypatch):
    monkeypatch.setenv("ACE_ENV", "development")
    monkeypatch.setenv("ACE_DEV_AUTH", "enabled")
    monkeypatch.setenv("ACE_SECRET_KEY", _SECRET)
    monkeypatch.setenv("ACE_DB_PATH", str(db_path))
    reset_config()
    from app.api.main import app

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture()
def db_conn(db_path: Path, monkeypatch):
    monkeypatch.setenv("ACE_DB_PATH", str(db_path))
    conn = open_db(db_path)
    yield conn
    conn.close()


@pytest.fixture()
def workspace(db_conn):
    ws = cp_repo.create_workspace(
        db_conn,
        WorkspaceDraft(id=_uid(), name="Test Workspace", slug=f"test-ws-{_uid()[:8]}", actor="cli"),
    )
    db_conn.commit()
    return ws


@pytest.fixture()
def channel(db_conn, workspace):
    ch = cp_repo.create_channel(
        db_conn,
        ChannelDraft(
            id=_uid(),
            workspace_id=workspace.id,
            name="Test Channel",
            slug="test-channel",
            actor="cli",
        ),
    )
    db_conn.commit()
    return ch


# ---------------------------------------------------------------------------
# API — GET strategy (empty + populated)
# ---------------------------------------------------------------------------


def test_get_strategy_unavailable_when_no_profile(dev_client, workspace, channel):
    res = dev_client.get(
        f"/api/v1/workspaces/{workspace.id}/channels/{channel.id}/strategy",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "unavailable"
    assert data["profile"] is None
    assert data["effective"] is None


def test_get_strategy_returns_profile_and_effective_state(dev_client, workspace, channel):
    cfg = default_bootstrap_strategy_config()
    create_res = dev_client.post(
        f"/api/v1/workspaces/{workspace.id}/channels/{channel.id}/strategy",
        json={"config": cfg},
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert create_res.status_code == 200

    res = dev_client.get(
        f"/api/v1/workspaces/{workspace.id}/channels/{channel.id}/strategy",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    data = res.json()
    assert data["status"] == "ok"
    assert data["profile"]["version"] == 1
    assert data["profile"]["is_active"] is True
    assert data["effective"]["effective_regime"] == "bootstrap"
    assert data["effective"]["current_maturity"] == "insufficient"


# ---------------------------------------------------------------------------
# API — POST strategy (create/version/validate)
# ---------------------------------------------------------------------------


def test_create_strategy_rejects_invalid_config(dev_client, workspace, channel):
    res = dev_client.post(
        f"/api/v1/workspaces/{workspace.id}/channels/{channel.id}/strategy",
        json={"config": {"bootstrap": {}}},
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 422
    assert "errors" in res.json()["detail"]


def test_create_strategy_rejects_missing_config_body(dev_client, workspace, channel):
    res = dev_client.post(
        f"/api/v1/workspaces/{workspace.id}/channels/{channel.id}/strategy",
        json={},
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 422


def test_creating_new_version_deactivates_previous_but_preserves_history(
    dev_client, db_conn, workspace, channel
):
    cfg = default_bootstrap_strategy_config()
    dev_client.post(
        f"/api/v1/workspaces/{workspace.id}/channels/{channel.id}/strategy",
        json={"config": cfg},
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    cfg2 = default_bootstrap_strategy_config()
    cfg2["bootstrap"]["target_publication_count"] = 20
    res2 = dev_client.post(
        f"/api/v1/workspaces/{workspace.id}/channels/{channel.id}/strategy",
        json={"config": cfg2},
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res2.status_code == 200
    assert res2.json()["version"] == 2
    assert res2.json()["is_active"] is True

    history_res = dev_client.get(
        f"/api/v1/workspaces/{workspace.id}/channels/{channel.id}/strategy/history",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    history = history_res.json()
    assert len(history) == 2  # both versions preserved — no destructive overwrite
    active_flags = {h["version"]: h["is_active"] for h in history}
    assert active_flags[1] is False
    assert active_flags[2] is True

    active_res = dev_client.get(
        f"/api/v1/workspaces/{workspace.id}/channels/{channel.id}/strategy",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert active_res.json()["profile"]["version"] == 2


def test_strategy_channel_isolation(dev_client, db_conn, workspace):
    channel_a = cp_repo.create_channel(
        db_conn,
        ChannelDraft(id=_uid(), workspace_id=workspace.id, name="A", slug="a", actor="cli"),
    )
    channel_b = cp_repo.create_channel(
        db_conn,
        ChannelDraft(id=_uid(), workspace_id=workspace.id, name="B", slug="b", actor="cli"),
    )
    db_conn.commit()

    dev_client.post(
        f"/api/v1/workspaces/{workspace.id}/channels/{channel_a.id}/strategy",
        json={"config": default_bootstrap_strategy_config()},
        headers={"X-Dev-Actor": "dev:studio-user"},
    )

    res_b = dev_client.get(
        f"/api/v1/workspaces/{workspace.id}/channels/{channel_b.id}/strategy",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res_b.json()["status"] == "unavailable"


# ---------------------------------------------------------------------------
# Planner integration
# ---------------------------------------------------------------------------


def test_build_portfolio_plan_falls_back_without_strategy(db_conn_bare):
    from app.intelligence.experiments.planning import PlanningPolicy
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    plan = build_portfolio_plan(db_conn_bare, 424242, [], dry_run=True)
    default = PlanningPolicy.v1()
    assert plan.eligible_count == 0
    # No candidates to plan, but the policy snapshot should match the safe default.
    import json as _json

    snapshot = _json.loads(plan.policy_snapshot_json)
    assert snapshot["max_exploration_slots"] == default.max_exploration_slots


def test_build_portfolio_plan_uses_active_strategy_end_to_end(db_conn_bare):
    """Full loop: create a channel + active strategy profile through the
    canonical control_plane API, then confirm build_portfolio_plan's policy
    snapshot reflects that strategy's weights rather than the v1 default —
    closing the exact gap identified in Phase 17E's inspection (planner
    policy was always call-time-defaulted, never wired to any per-channel
    config).
    """
    from app.control_plane import repository as cp_repo
    from app.control_plane import services as cp_services
    from app.control_plane.models import ChannelDraft, WorkspaceDraft
    from app.intelligence.channel_bridge import bootstrap_intelligence_channel
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    ws = cp_repo.create_workspace(
        db_conn_bare,
        WorkspaceDraft(id=_uid(), name="WS", slug=f"ws-{_uid()[:8]}", actor="cli"),
    )
    cp_channel = cp_repo.create_channel(
        db_conn_bare,
        ChannelDraft(id=_uid(), workspace_id=ws.id, name="C", slug="c", actor="cli"),
    )
    db_conn_bare.commit()
    intel_channel = bootstrap_intelligence_channel(db_conn_bare, cp_channel.id, channel_name="C")
    db_conn_bare.commit()

    cfg = default_bootstrap_strategy_config()
    cfg["bootstrap"]["market_intelligence_weight"] = 0.9
    cfg["bootstrap"]["channel_evidence_weight"] = 0.1
    cp_services.create_channel_strategy_version(db_conn_bare, cp_channel.id, cfg, actor="test")
    db_conn_bare.commit()

    plan = build_portfolio_plan(db_conn_bare, intel_channel.id, [], dry_run=True)
    import json as _json

    snapshot = _json.loads(plan.policy_snapshot_json)

    default_attractiveness = 0.50  # PlanningPolicy.v1() default
    assert snapshot["w_exploitation_attractiveness"] > default_attractiveness
    # A 90/10 market/channel split should push attractiveness weight well above default.
    assert snapshot["w_exploitation_attractiveness"] > 0.6
