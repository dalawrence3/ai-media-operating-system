"""Phase 16 — Control Plane API route tests.

Covers:
A. GET /analytics/aggregates?publication_id={id} — publication-scoped bypass
B. GET /analytics/aggregates?publication_id= wrong workspace → 404
C. GET /recommendations?publication_id={id} — publication-scoped bypass
D. GET /recommendations?publication_id= wrong workspace → 404
E. GET /market/clusters — empty list when no clusters exist
F. GET /market/opportunities — empty list when no opportunities
G. GET /market/signals — empty list when no signals
H. GET /environment — returns all expected keys
I. GET /environment — pilot_ready=False when publications missing
J. GET /environment — analytics_ready reported correctly
K. GET /analytics/aggregates with no publication_id still uses topic_id path
L. GET /recommendations with no publication_id still uses topic_id path
M. GET /market/experiments — empty when no intelligence experiments
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import reset_config
from app.core.database import open_db


def _uid() -> str:
    return str(uuid.uuid4())


@pytest.fixture(autouse=True)
def _reset_config():
    reset_config()
    yield
    reset_config()


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "p16_test.db"


@pytest.fixture()
def dev_client(db_path: Path, monkeypatch):
    monkeypatch.setenv("ACE_ENV", "development")
    monkeypatch.setenv("ACE_DEV_AUTH", "enabled")
    monkeypatch.setenv("ACE_DB_PATH", str(db_path))
    reset_config()
    open_db(db_path).close()

    from app.api.main import app

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


DEV_HEADERS = {"X-Dev-Actor": "dev:studio-user"}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_NOW = "2025-01-01T00:00:00"


def _seed_workspace(db_path: Path, ws_id: str = "test-ws") -> str:
    conn = open_db(db_path)
    conn.execute(
        "INSERT OR IGNORE INTO cp_workspaces "
        "(id, name, slug, status, actor, created_at, updated_at) "
        "VALUES (?, 'Test WS', ?, 'active', 'test', ?, ?)",
        (ws_id, f"slug-{ws_id[:8]}", _NOW, _NOW),
    )
    conn.commit()
    conn.close()
    return ws_id


def _seed_publication(db_path: Path, ws_id: str) -> int:
    """Seed a minimal publication with FK constraints off."""
    conn = open_db(db_path)
    conn.execute("PRAGMA foreign_keys = OFF")
    # topic
    conn.execute(
        "INSERT INTO topics (title, angle, status, created_at, updated_at) "
        "VALUES ('test topic', '', 'active', ?, ?)",
        (_NOW, _NOW),
    )
    topic_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    # render_manifest
    conn.execute(
        "INSERT INTO render_manifests "
        "(id, scene_manifest_id, narration_run_id, caption_run_id, topic_id, plan_id, "
        "script_id, input_hash, render_schema_version, compositor_version, "
        "total_scene_count, total_duration_ms, width, height, fps, caption_burn_in, "
        "status, created_at, updated_at) "
        "VALUES (1, 1, 1, 1, ?, 1, 1, ?, '1', '1', 5, 58607, 1080, 1920, 30, 0, 'approved', ?, ?)",
        (topic_id, str(uuid.uuid4()), _NOW, _NOW),
    )
    # publishing_plan
    conn.execute(
        "INSERT INTO publishing_plans "
        "(id, render_manifest_id, topic_id, production_plan_id, script_id, scene_manifest_id, "
        "narration_run_id, caption_run_id, input_hash, publishing_engine_version, "
        "metadata_version, provider, provider_version, title, description, tags_json, "
        "visibility, created_at, updated_at) "
        "VALUES (1, 1, ?, 1, 1, 1, 1, 1, ?, '1.0', '1', 'youtube', '1.0', "
        "'Test Plan', '', '[]', 'private', ?, ?)",
        (topic_id, str(uuid.uuid4()), _NOW, _NOW),
    )
    # publishing_job
    conn.execute(
        "INSERT INTO publishing_jobs "
        "(id, publishing_plan_id, attempt_number, provider, provider_version, status, "
        "retry_count, created_at, updated_at) "
        "VALUES (1, 1, 1, 'youtube', '1.0', 'completed', 0, ?, ?)",
        (_NOW, _NOW),
    )
    # publication
    conn.execute(
        "INSERT INTO publications "
        "(id, publishing_plan_id, publishing_job_id, provider, provider_version, "
        "provider_video_id, provider_url, visibility, status, "
        "publishing_engine_version, input_hash, output_sha256, published_at, "
        "workspace_id, created_at, updated_at) "
        "VALUES (1, 1, 1, 'youtube', '1.0', 'vid123', "
        "'https://www.youtube.com/watch?v=vid123', 'private', 'published', "
        "'1.0', ?, 'sha256-pub', ?, ?, ?, ?)",
        (str(uuid.uuid4()), _NOW, ws_id, _NOW, _NOW),
    )
    conn.commit()
    conn.close()
    return 1


def _seed_aggregate(db_path: Path, pub_id: int, topic_id: int = 1) -> None:
    conn = open_db(db_path)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT INTO analytics_aggregates "
        "(publication_id, topic_id, provider, period_type, period_key, "
        "metric_name, metric_value, snapshot_count, calculation_method, "
        "source_snapshot_ids_json, input_hash, created_at) "
        "VALUES (?, ?, 'youtube', 'lifetime', 'lifetime', 'views', 500, 1, 'sum', '[]', ?, ?)",
        (pub_id, topic_id, str(uuid.uuid4()), _NOW),
    )
    conn.commit()
    conn.close()


def _seed_recommendation(db_path: Path, pub_id: int) -> None:
    conn = open_db(db_path)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT INTO learning_runs "
        "(engine_version, schema_version, topic_id, input_hash, created_at) "
        "VALUES ('1.0.0', '1', 1, ?, ?)",
        (str(uuid.uuid4()), _NOW),
    )
    run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO optimization_recommendations "
        "(learning_run_id, topic_id, publication_id, domain, subsystem, measure, "
        "title, explanation, expected_improvement, confidence, confidence_score, "
        "recommendation_strength, engine_version, schema_version, input_hash, created_at) "
        "VALUES (?, 1, ?, 'topics', 'research', 'test_measure', 'Test rec', 'Because', "
        "'Better', 'medium', 0.6, 'actionable', '1.0.0', '1', ?, '2025-01-01T00:00:00')",
        (run_id, pub_id, str(uuid.uuid4())),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# A. analytics/aggregates with publication_id (publication-scoped bypass)
# ---------------------------------------------------------------------------


def test_A_aggregates_publication_scoped(db_path, dev_client):
    ws = _seed_workspace(db_path)
    pub_id = _seed_publication(db_path, ws)
    _seed_aggregate(db_path, pub_id)

    resp = dev_client.get(
        f"/api/v1/workspaces/{ws}/analytics/aggregates",
        params={"publication_id": pub_id},
        headers=DEV_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data) == 1
    assert data[0]["metric_name"] == "views"
    assert data[0]["publication_id"] == pub_id


# ---------------------------------------------------------------------------
# B. analytics/aggregates with publication_id — wrong workspace → 404
# ---------------------------------------------------------------------------


def test_B_aggregates_publication_wrong_workspace(db_path, dev_client):
    ws = _seed_workspace(db_path)
    pub_id = _seed_publication(db_path, ws)
    _seed_aggregate(db_path, pub_id)

    resp = dev_client.get(
        "/api/v1/workspaces/other-ws/analytics/aggregates",
        params={"publication_id": pub_id},
        headers=DEV_HEADERS,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# C. recommendations with publication_id (publication-scoped bypass)
# ---------------------------------------------------------------------------


def test_C_recommendations_publication_scoped(db_path, dev_client):
    ws = _seed_workspace(db_path)
    pub_id = _seed_publication(db_path, ws)
    _seed_recommendation(db_path, pub_id)

    resp = dev_client.get(
        f"/api/v1/workspaces/{ws}/recommendations",
        params={"publication_id": pub_id},
        headers=DEV_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data) == 1
    assert data[0]["publication_id"] == pub_id
    assert data[0]["title"] == "Test rec"


# ---------------------------------------------------------------------------
# D. recommendations with publication_id — wrong workspace → 404
# ---------------------------------------------------------------------------


def test_D_recommendations_publication_wrong_workspace(db_path, dev_client):
    ws = _seed_workspace(db_path)
    pub_id = _seed_publication(db_path, ws)
    _seed_recommendation(db_path, pub_id)

    resp = dev_client.get(
        "/api/v1/workspaces/other-ws/recommendations",
        params={"publication_id": pub_id},
        headers=DEV_HEADERS,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# E. market/clusters — empty when no clusters
# ---------------------------------------------------------------------------


def test_E_market_clusters_empty(db_path, dev_client):
    ws = _seed_workspace(db_path)
    resp = dev_client.get(
        f"/api/v1/workspaces/{ws}/market/clusters",
        headers=DEV_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# F. market/opportunities — empty when no opportunities
# ---------------------------------------------------------------------------


def test_F_market_opportunities_empty(db_path, dev_client):
    ws = _seed_workspace(db_path)
    resp = dev_client.get(
        f"/api/v1/workspaces/{ws}/market/opportunities",
        headers=DEV_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# G. market/signals — empty when no signals
# ---------------------------------------------------------------------------


def test_G_market_signals_empty(db_path, dev_client):
    ws = _seed_workspace(db_path)
    resp = dev_client.get(
        f"/api/v1/workspaces/{ws}/market/signals",
        headers=DEV_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# H. environment — returns all expected top-level keys
# ---------------------------------------------------------------------------


def test_H_environment_has_expected_keys(db_path, dev_client):
    ws = _seed_workspace(db_path)
    resp = dev_client.get(
        f"/api/v1/workspaces/{ws}/environment",
        headers=DEV_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "ok" in data
    assert "pilot_ready" in data
    assert "analytics_ready" in data
    assert "ai_provider_mode" in data
    assert "warnings" in data
    assert "checks" in data
    expected_checks = {
        "youtube_api_key",
        "youtube_client_secrets",
        "ai_provider",
        "ffmpeg",
        "dev_auth",
        "oauth_tokens",
        "channels",
        "publications",
        "analytics_snapshots",
        "analytics_aggregates",
        "recommendations",
        "market_clusters",
    }
    assert expected_checks.issubset(set(data["checks"].keys()))


# ---------------------------------------------------------------------------
# I. environment — pilot_ready=False when no publications exist
# ---------------------------------------------------------------------------


def test_I_environment_pilot_not_ready_without_publications(db_path, dev_client):
    ws = _seed_workspace(db_path)
    resp = dev_client.get(
        f"/api/v1/workspaces/{ws}/environment",
        headers=DEV_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["checks"]["publications"]["ok"] is False
    assert data["pilot_ready"] is False


# ---------------------------------------------------------------------------
# J. environment — publication count reflects seeded publication
# ---------------------------------------------------------------------------


def test_J_environment_publication_count(db_path, dev_client):
    ws = _seed_workspace(db_path)
    _seed_publication(db_path, ws)
    resp = dev_client.get(
        f"/api/v1/workspaces/{ws}/environment",
        headers=DEV_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["checks"]["publications"]["ok"] is True
    assert "1 publication" in data["checks"]["publications"]["detail"]


# ---------------------------------------------------------------------------
# K. analytics/aggregates without publication_id uses topic_id path (returns [])
# ---------------------------------------------------------------------------


def test_K_aggregates_no_topic_ids_returns_empty(db_path, dev_client):
    ws = _seed_workspace(db_path)
    pub_id = _seed_publication(db_path, ws)
    _seed_aggregate(db_path, pub_id)
    # No pipeline executions → workspace_topic_ids returns [] → aggregates returns []
    resp = dev_client.get(
        f"/api/v1/workspaces/{ws}/analytics/aggregates",
        headers=DEV_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# L. recommendations without publication_id uses topic_id path (returns [])
# ---------------------------------------------------------------------------


def test_L_recommendations_no_topic_ids_returns_empty(db_path, dev_client):
    ws = _seed_workspace(db_path)
    pub_id = _seed_publication(db_path, ws)
    _seed_recommendation(db_path, pub_id)
    resp = dev_client.get(
        f"/api/v1/workspaces/{ws}/recommendations",
        headers=DEV_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# M. market/experiments — 200 empty list when no experiments
# ---------------------------------------------------------------------------


def test_M_market_experiments_empty(db_path, dev_client):
    ws = _seed_workspace(db_path)
    resp = dev_client.get(
        f"/api/v1/workspaces/{ws}/market/experiments",
        headers=DEV_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# N. market/opportunities — composite_score + confidence exposed from scores table
# ---------------------------------------------------------------------------


def _seed_opportunity_with_score(db_path: Path, channel_id: int = 99) -> int:
    """Seed an opportunity + opportunity_score; returns the opportunity id."""
    conn = open_db(db_path)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT INTO opportunities "
        "(channel_id, discovery_run_id, normalized_topic, raw_topic, title, topic_summary, "
        "format_recommendation, strategic_role, current_lifecycle_state, created_at, updated_at) "
        "VALUES (?, 1, 'test topic', 'test topic', 'Test', '', 'undecided', "
        "'discovery', 'new', ?, ?)",
        (channel_id, _NOW, _NOW),
    )
    opp_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO opportunity_scores "
        "(opportunity_id, scoring_policy_id, channel_profile_version_id, "
        "composite_score, confidence, "
        "score_trend_strength, score_audience_demand, score_competition, "
        "score_evergreen_value, score_audience_fit, score_content_novelty, "
        "status_trend_strength, status_audience_demand, status_competition, "
        "status_evergreen_value, status_audience_fit, status_content_novelty, "
        "eff_weight_trend_strength, eff_weight_audience_demand, eff_weight_competition, "
        "eff_weight_evergreen_value, eff_weight_audience_fit, eff_weight_content_novelty, "
        "observation_ids_json, input_snapshot_json, input_hash, "
        "below_confidence_threshold, requires_research, scored_at, scorer_version) "
        "VALUES (?, 1, 1, 0.72, 0.85, NULL, 0.70, 0.12, 0.80, 0.65, 1.0, "
        "'absent', 'present', 'present', 'present', 'present', 'present', "
        "0.0, 0.21, 0.16, 0.21, 0.32, 0.11, "
        "'[]', '{}', 'testhash', 0, 0, ?, '1.0')",
        (opp_id, _NOW),
    )
    conn.commit()
    conn.close()
    return opp_id


def test_N_opportunities_exposes_composite_score(db_path, dev_client):
    """opportunities endpoint includes composite_score and confidence from opportunity_scores."""
    ws = _seed_workspace(db_path)
    channel_id = 99
    _seed_opportunity_with_score(db_path, channel_id)
    resp = dev_client.get(
        f"/api/v1/workspaces/{ws}/market/opportunities",
        headers=DEV_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    opp = data[0]
    assert "composite_score" in opp, "composite_score must be present in opportunities response"
    assert "confidence" in opp, "confidence must be present in opportunities response"
    assert abs(opp["composite_score"] - 0.72) < 1e-6
    assert abs(opp["confidence"] - 0.85) < 1e-6


def test_N2_opportunities_composite_score_null_when_unscored(db_path, dev_client):
    """composite_score is null (not omitted) when no score row exists for an opportunity."""
    ws = _seed_workspace(db_path)
    conn = open_db(db_path)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT INTO opportunities "
        "(channel_id, discovery_run_id, normalized_topic, raw_topic, title, topic_summary, "
        "format_recommendation, strategic_role, current_lifecycle_state, created_at, updated_at) "
        "VALUES (99, 1, 'unscored topic', 'unscored topic', 'Unscored', '', "
        "'undecided', 'discovery', 'new', ?, ?)",
        (_NOW, _NOW),
    )
    conn.commit()
    conn.close()
    resp = dev_client.get(
        f"/api/v1/workspaces/{ws}/market/opportunities",
        headers=DEV_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    opp = data[0]
    assert "composite_score" in opp
    assert opp["composite_score"] is None
    assert opp["confidence"] is None
