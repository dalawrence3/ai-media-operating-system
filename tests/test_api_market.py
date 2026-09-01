"""Backend API tests for market intelligence routes (Phase 17D).

Covers:
- GET /workspaces/{ws}/market/opportunities — cp_channel_id scoping, score
  fields, canonical cluster label, evidence count
- GET /workspaces/{ws}/market/experiments — cp_channel_id scoping
- GET /workspaces/{ws}/market/strategy-briefs — cp_channel_id scoping,
  linked-experiment lookup
- GET /workspaces/{ws}/channels/{channel_id}/cross-publication — channel
  baselines + feature observations, honest empty state

Isolation contract under test: cp_channel_id must resolve through the
identity bridge (app.intelligence.channel_bridge) before any market data is
returned; an unbootstrapped or unrelated channel must see an empty list,
never another channel's data.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.auth.tokens import create_access_token
from app.control_plane import repository as cp_repo
from app.control_plane.models import ChannelDraft, WorkspaceDraft
from app.core.config import reset_config
from app.core.database import open_db

_SECRET = "test-secret-market-32-bytes-ok!!"
NOW = "2026-01-01T00:00:00"


def _uid() -> str:
    return str(uuid.uuid4())


def _jwt(workspace_id: str, role: str) -> str:
    return create_access_token(
        1,
        "test@test.com",
        {workspace_id: role},
        secret_key=_SECRET,
        expire_seconds=3600,
    )


@pytest.fixture(autouse=True)
def _reset_config():
    reset_config()
    yield
    reset_config()


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "market_test.db"


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
def prod_client(db_path: Path, monkeypatch):
    monkeypatch.setenv("ACE_ENV", "production")
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


def _create_cp_channel(conn, workspace_id: str) -> str:
    ch = cp_repo.create_channel(
        conn,
        ChannelDraft(
            id=_uid(),
            workspace_id=workspace_id,
            name="Test Channel",
            slug=f"test-channel-{_uid()[:8]}",
            actor="cli",
        ),
    )
    conn.commit()
    return ch.id


def _bootstrap_intelligence_channel(conn, cp_channel_id: str) -> int:
    from app.intelligence.channel_bridge import bootstrap_intelligence_channel

    ch = bootstrap_intelligence_channel(conn, cp_channel_id, channel_name="Test Channel")
    conn.commit()
    return ch.id


def _seed_opportunity_with_score(
    conn,
    *,
    channel_id: int,
    opportunity_id: int = 1,
    canonical_cluster_id: int | None = 1,
    composite_score: float = 0.7,
    confidence: float = 0.6,
    score_competition: float | None = 0.2,
) -> None:
    """Seed a minimal opportunity + latest score, with FK checks off.

    Mirrors the real schema directly (see opportunities/opportunity_scores/
    market_canonical_clusters) rather than composing the full scoring engine,
    which needs a scoring policy + channel profile + evidence pipeline this
    test does not need to exercise.
    """
    conn.execute("PRAGMA foreign_keys = OFF")
    if canonical_cluster_id is not None:
        conn.execute(
            "INSERT OR IGNORE INTO market_canonical_clusters "
            "(id, platform, provider, canonical_label, normalized_label, "
            " semantic_fingerprint, identity_version, created_at, updated_at) "
            "VALUES (?, 'youtube', 'youtube_data_api', 'Test Cluster', 'test cluster', "
            " 'fp-1', '1', ?, ?)",
            (canonical_cluster_id, NOW, NOW),
        )
    conn.execute(
        "INSERT INTO discovery_runs "
        "(id, channel_id, profile_version_id, adapter_name, status, started_at) "
        "VALUES (?, ?, 1, 'market_intelligence', 'completed', ?)",
        (opportunity_id, channel_id, NOW),
    )
    conn.execute(
        "INSERT INTO opportunities "
        "(id, channel_id, discovery_run_id, normalized_topic, raw_topic, title, "
        " current_lifecycle_state, canonical_cluster_id, created_at, updated_at) "
        "VALUES (?, ?, ?, 'test topic', 'Test Topic', 'Test Topic', 'new', ?, ?, ?)",
        (opportunity_id, channel_id, opportunity_id, canonical_cluster_id, NOW, NOW),
    )
    conn.execute(
        "INSERT INTO opportunity_scores "
        "(opportunity_id, scoring_policy_id, channel_profile_version_id, "
        " composite_score, confidence, score_competition, status_competition, "
        " eff_weight_trend_strength, eff_weight_audience_demand, eff_weight_competition, "
        " eff_weight_evergreen_value, eff_weight_audience_fit, eff_weight_content_novelty, "
        " input_hash, scored_at) "
        "VALUES (?, 1, 1, ?, ?, ?, 'present', 0, 0, 0, 0, 0, 0, 'hash-score', ?)",
        (opportunity_id, composite_score, confidence, score_competition, NOW),
    )
    conn.execute(
        "INSERT INTO opportunity_observations "
        "(id, opportunity_id, discovery_run_id, adapter_name, collected_at) "
        "VALUES (?, ?, ?, 'market_intelligence', ?)",
        (opportunity_id, opportunity_id, opportunity_id, NOW),
    )
    conn.execute(
        "INSERT INTO opportunity_source_evidence "
        "(observation_id, opportunity_id, evidence_type, evidence_value, "
        "source_label, collected_at) "
        "VALUES (?, ?, 'market_demand_score', 0.5, 'market_intelligence', ?)",
        (opportunity_id, opportunity_id, NOW),
    )
    conn.commit()


def _seed_experiment(
    conn, *, channel_id: int, opportunity_id: int, experiment_id: str = "exp-1"
) -> None:
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT INTO experiments "
        "(id, channel_id, opportunity_id, experiment_type, status, hypothesis, "
        "created_at, updated_at) "
        "VALUES (?, ?, ?, 'exploration', 'draft', 'Test hypothesis', ?, ?)",
        (experiment_id, channel_id, opportunity_id, NOW, NOW),
    )
    conn.commit()


def _seed_strategy_brief(
    conn, *, channel_id: int, opportunity_id: int, brief_id: str = "brief-1"
) -> None:
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT INTO experiment_planning_runs (id, channel_id, status, input_hash, created_at) "
        "VALUES ('plan-1', ?, 'completed', 'hash-plan', ?)",
        (channel_id, NOW),
    )
    conn.execute(
        "INSERT INTO experiment_selection_decisions "
        "(id, planning_run_id, candidate_score_id, opportunity_id, selected, rank_in_pool, "
        " pool_type, selection_reason, is_validation_repeat, created_at) "
        "VALUES (1, 'plan-1', 1, ?, 1, 1, 'exploration', 'top candidate', 0, ?)",
        (opportunity_id, NOW),
    )
    conn.execute(
        "INSERT INTO experiment_strategy_briefs "
        "(id, channel_id, planning_run_id, selection_decision_id, opportunity_id, "
        " canonical_cluster_id, brief_planning_intent, experiment_type, "
        " market_theme, canonical_topic, strategic_reason, information_gain_reason, "
        " hypothesis, target_metric, target_direction, treatment_factors_json, "
        " controlled_factors_json, content_constraints_json, confounding_risk, "
        " policy_version, eligibility_classification, score_decomposition_json, "
        " brief_hash, status, created_at) "
        "VALUES (?, ?, 'plan-1', 1, ?, 1, 'market_exploration', 'exploration', "
        " 'test topic', 'test topic', 'No prior experiments; baseline needed.', "
        " 'Untested cluster: high information gain.', 'Test hypothesis', "
        " 'average_view_percentage', 'higher_is_better', '[]', '[]', '{}', 'low', "
        " '1.0', 'eligible', '{}', 'hash-brief', 'pending_approval', ?)",
        (brief_id, channel_id, opportunity_id, NOW),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# /market/opportunities
# ---------------------------------------------------------------------------


def test_opportunities_empty_for_unbootstrapped_channel(dev_client, db_conn, workspace):
    cp_channel_id = _uid()
    res = dev_client.get(
        f"/api/v1/workspaces/{workspace.id}/market/opportunities",
        params={"cp_channel_id": cp_channel_id},
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 200
    assert res.json() == []


def test_opportunities_scoped_to_cp_channel_id(dev_client, db_conn, workspace):
    cp_channel_id = _create_cp_channel(db_conn, workspace.id)
    intel_channel_id = _bootstrap_intelligence_channel(db_conn, cp_channel_id)
    _seed_opportunity_with_score(db_conn, channel_id=intel_channel_id, score_competition=0.2)

    res = dev_client.get(
        f"/api/v1/workspaces/{workspace.id}/market/opportunities",
        params={"cp_channel_id": cp_channel_id},
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 1
    opp = data[0]
    assert opp["title"] == "Test Topic"
    assert opp["canonical_label"] == "Test Cluster"
    assert opp["composite_score"] == 0.7
    assert opp["confidence"] == 0.6
    assert opp["score_competition"] == 0.2
    assert opp["status_competition"] == "present"
    assert opp["evidence_count"] == 1
    # Absent score is null, never coerced to 0 — trend_strength was never seeded.
    assert opp["score_trend_strength"] is None
    assert opp["status_trend_strength"] == "absent"


def test_opportunities_does_not_leak_across_channels(dev_client, db_conn, workspace):
    cp_channel_a = _create_cp_channel(db_conn, workspace.id)
    cp_channel_b = _create_cp_channel(db_conn, workspace.id)
    intel_a = _bootstrap_intelligence_channel(db_conn, cp_channel_a)
    intel_b = _bootstrap_intelligence_channel(db_conn, cp_channel_b)
    _seed_opportunity_with_score(db_conn, channel_id=intel_a, opportunity_id=1)
    _seed_opportunity_with_score(
        db_conn, channel_id=intel_b, opportunity_id=2, canonical_cluster_id=2
    )

    res = dev_client.get(
        f"/api/v1/workspaces/{workspace.id}/market/opportunities",
        params={"cp_channel_id": cp_channel_a},
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    data = res.json()
    assert len(data) == 1
    assert data[0]["channel_id"] == intel_a


def test_opportunities_401_without_auth(prod_client, workspace):
    res = prod_client.get(
        f"/api/v1/workspaces/{workspace.id}/market/opportunities",
        params={"cp_channel_id": _uid()},
    )
    assert res.status_code == 401


# ---------------------------------------------------------------------------
# /market/experiments
# ---------------------------------------------------------------------------


def test_experiments_empty_for_unbootstrapped_channel(dev_client, db_conn, workspace):
    res = dev_client.get(
        f"/api/v1/workspaces/{workspace.id}/market/experiments",
        params={"cp_channel_id": _uid()},
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 200
    assert res.json() == []


def test_experiments_scoped_to_cp_channel_id(dev_client, db_conn, workspace):
    cp_channel_id = _create_cp_channel(db_conn, workspace.id)
    intel_channel_id = _bootstrap_intelligence_channel(db_conn, cp_channel_id)
    _seed_opportunity_with_score(db_conn, channel_id=intel_channel_id)
    _seed_experiment(db_conn, channel_id=intel_channel_id, opportunity_id=1)

    res = dev_client.get(
        f"/api/v1/workspaces/{workspace.id}/market/experiments",
        params={"cp_channel_id": cp_channel_id},
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 1
    assert data[0]["hypothesis"] == "Test hypothesis"
    assert data[0]["status"] == "draft"


# ---------------------------------------------------------------------------
# /market/strategy-briefs
# ---------------------------------------------------------------------------


def test_strategy_briefs_empty_for_unbootstrapped_channel(dev_client, db_conn, workspace):
    res = dev_client.get(
        f"/api/v1/workspaces/{workspace.id}/market/strategy-briefs",
        params={"cp_channel_id": _uid()},
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 200
    assert res.json() == []


def test_strategy_briefs_scoped_with_linked_experiment(dev_client, db_conn, workspace):
    cp_channel_id = _create_cp_channel(db_conn, workspace.id)
    intel_channel_id = _bootstrap_intelligence_channel(db_conn, cp_channel_id)
    _seed_opportunity_with_score(db_conn, channel_id=intel_channel_id)
    _seed_experiment(
        db_conn, channel_id=intel_channel_id, opportunity_id=1, experiment_id="exp-linked"
    )
    _seed_strategy_brief(db_conn, channel_id=intel_channel_id, opportunity_id=1)

    res = dev_client.get(
        f"/api/v1/workspaces/{workspace.id}/market/strategy-briefs",
        params={"cp_channel_id": cp_channel_id},
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 1
    brief = data[0]
    assert brief["strategic_reason"] == "No prior experiments; baseline needed."
    assert brief["status"] == "pending_approval"
    assert brief["confounding_risk"] == "low"
    assert brief["linked_experiment"] is not None
    assert brief["linked_experiment"]["id"] == "exp-linked"


def test_strategy_briefs_filters_by_status(dev_client, db_conn, workspace):
    cp_channel_id = _create_cp_channel(db_conn, workspace.id)
    intel_channel_id = _bootstrap_intelligence_channel(db_conn, cp_channel_id)
    _seed_opportunity_with_score(db_conn, channel_id=intel_channel_id)
    _seed_strategy_brief(db_conn, channel_id=intel_channel_id, opportunity_id=1)

    res = dev_client.get(
        f"/api/v1/workspaces/{workspace.id}/market/strategy-briefs",
        params={"cp_channel_id": cp_channel_id, "status": "approved"},
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 200
    assert res.json() == []


# ---------------------------------------------------------------------------
# /channels/{id}/cross-publication
# ---------------------------------------------------------------------------


def test_cross_publication_empty_when_never_run(dev_client, db_conn, workspace):
    cp_channel_id = _uid()
    res = dev_client.get(
        f"/api/v1/workspaces/{workspace.id}/channels/{cp_channel_id}/cross-publication",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data == {"channel_id": cp_channel_id, "baselines": [], "feature_observations": []}


def test_cross_publication_returns_persisted_baselines_and_observations(
    dev_client, db_conn, workspace
):
    from app.learning.cross_publication import (
        _compute_stats,
        _upsert_channel_baseline,
        _upsert_feature_observation,
    )

    cp_channel_id = _uid()
    stats = _compute_stats([100.0, 200.0])
    _upsert_channel_baseline(
        db_conn,
        channel_id=cp_channel_id,
        workspace_id=workspace.id,
        metric_name="views",
        period_type="lifetime",
        stats=stats,
        source_publication_ids=[1, 2],
        source_snapshot_ids=[1, 2],
        input_hash="hash-baseline",
    )
    _upsert_feature_observation(
        db_conn,
        channel_id=cp_channel_id,
        workspace_id=workspace.id,
        feature_name="scene_count",
        bucket="6-9",
        metric_name="views",
        period_type="lifetime",
        stats=stats,
        baseline_mean=stats["mean"],
        baseline_median=stats["median"],
        source_publication_ids=[1, 2],
        source_snapshot_ids=[1, 2],
        input_hash="hash-observation",
    )
    db_conn.commit()

    res = dev_client.get(
        f"/api/v1/workspaces/{workspace.id}/channels/{cp_channel_id}/cross-publication",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 200
    data = res.json()
    assert len(data["baselines"]) == 1
    assert data["baselines"][0]["metric_name"] == "views"
    assert data["baselines"][0]["sample_maturity"] == "exploratory"  # n=2
    assert len(data["feature_observations"]) == 1
    assert data["feature_observations"][0]["feature_name"] == "scene_count"
    assert data["feature_observations"][0]["observation_type"] == "association"


def test_cross_publication_isolated_by_channel(dev_client, db_conn, workspace):
    from app.learning.cross_publication import _compute_stats, _upsert_channel_baseline

    channel_a = _uid()
    channel_b = _uid()
    _upsert_channel_baseline(
        db_conn,
        channel_id=channel_a,
        workspace_id=workspace.id,
        metric_name="views",
        period_type="lifetime",
        stats=_compute_stats([50.0]),
        source_publication_ids=[1],
        source_snapshot_ids=[1],
        input_hash="hash-a",
    )
    db_conn.commit()

    res = dev_client.get(
        f"/api/v1/workspaces/{workspace.id}/channels/{channel_b}/cross-publication",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.json()["baselines"] == []
