"""Backend API tests for learning/recommendations routes.

Covers:
- GET /workspaces/{ws}/recommendations
- POST /workspaces/{ws}/recommendations/{id}/accept
- POST /workspaces/{ws}/recommendations/{id}/reject
- Workspace isolation (cross-workspace access denied)
- Authentication enforcement
- Notes required for reject
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.auth.tokens import create_access_token
from app.control_plane import repository as cp_repo
from app.control_plane.models import WorkspaceDraft
from app.core.config import reset_config
from app.core.database import open_db
from app.learning.constants import (
    LEARNING_ENGINE_VERSION,
    LEARNING_SCHEMA_VERSION,
    STATUS_PENDING,
)

_SECRET = "test-secret-learning-32bytes!!!!"
NOW = "2026-01-01T00:00:00"


def _uid() -> str:
    return str(uuid.uuid4())


def _jwt(workspace_id: str, role: str) -> str:
    return create_access_token(
        1, "test@test.com", {workspace_id: role}, secret_key=_SECRET, expire_seconds=3600
    )


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
    return tmp_path / "learning_test.db"


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
        WorkspaceDraft(id=_uid(), name="Test Workspace", slug="test-ws", actor="cli"),
    )
    db_conn.commit()
    return ws


@pytest.fixture()
def workspace_with_topic(db_conn, workspace):
    cur = db_conn.execute(
        "INSERT INTO topics (title, angle, status, created_at, updated_at) VALUES (?,?,?,?,?)",
        ("Test Topic", "", "active", NOW, NOW),
    )
    topic_id = cur.lastrowid
    exec_id = _uid()
    db_conn.execute(
        """INSERT INTO app_pipeline_executions
               (id, workspace_id, channel_id, platform_account_id, topic_id,
                correlation_id, idempotency_key, status, end_stage, actor,
                created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            exec_id,
            workspace.id,
            None,
            None,
            topic_id,
            f"corr-{exec_id[:8]}",
            f"idem-{exec_id[:8]}",
            "completed",
            "learning",
            "test",
            NOW,
            NOW,
        ),
    )
    db_conn.commit()
    return workspace, topic_id


@pytest.fixture()
def seeded_recommendation(db_conn, workspace_with_topic):
    ws, topic_id = workspace_with_topic
    run_cur = db_conn.execute(
        """INSERT INTO learning_runs
               (topic_id, publication_id, publication_count, recommendation_count,
                status, engine_version, schema_version, input_hash, created_at, completed_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            topic_id,
            None,
            5,
            1,
            "completed",
            LEARNING_ENGINE_VERSION,
            LEARNING_SCHEMA_VERSION,
            "hash-run-001",
            NOW,
            NOW,
        ),
    )
    run_id = run_cur.lastrowid
    rec_cur = db_conn.execute(
        """INSERT INTO optimization_recommendations
               (learning_run_id, topic_id, publication_id, domain, subsystem, measure,
                title, explanation, expected_improvement,
                evidence_json, evidence_classification, recommendation_strength,
                confidence, confidence_score,
                affected_subsystem, subsystem_entity_type, subsystem_entity_id,
                experiment_id, engine_version, schema_version, input_hash,
                status, superseded_at, superseded_by_id, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            run_id,
            topic_id,
            None,
            "scripts",
            "hook",
            "ctr",
            "Shorter hooks associated with higher CTR",
            "Observation across 5 snapshots.",
            "Associated with improved CTR.",
            "[]",
            "observational",
            "actionable",
            "medium",
            0.62,
            "",
            "",
            None,
            None,
            LEARNING_ENGINE_VERSION,
            LEARNING_SCHEMA_VERSION,
            "hash-rec-001",
            STATUS_PENDING,
            None,
            None,
            NOW,
        ),
    )
    rec_id = rec_cur.lastrowid
    db_conn.commit()
    return ws, topic_id, rec_id


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def test_recommendations_requires_auth(prod_client, workspace):
    r = prod_client.get(f"/api/v1/workspaces/{workspace.id}/recommendations")
    assert r.status_code == 401


def test_accept_requires_auth(prod_client, workspace):
    r = prod_client.post(f"/api/v1/workspaces/{workspace.id}/recommendations/1/accept")
    assert r.status_code == 401


def test_reject_requires_auth(prod_client, workspace):
    r = prod_client.post(
        f"/api/v1/workspaces/{workspace.id}/recommendations/1/reject",
        json={"notes": "not relevant"},
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Empty state
# ---------------------------------------------------------------------------


def test_recommendations_empty_when_no_executions(dev_client, workspace):
    r = dev_client.get(
        f"/api/v1/workspaces/{workspace.id}/recommendations",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert r.status_code == 200
    assert r.json() == []


# ---------------------------------------------------------------------------
# Populated state
# ---------------------------------------------------------------------------


def test_recommendations_returns_workspace_data(dev_client, seeded_recommendation):
    ws, _, rec_id = seeded_recommendation
    r = dev_client.get(
        f"/api/v1/workspaces/{ws.id}/recommendations",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["id"] == rec_id
    assert data[0]["status"] == STATUS_PENDING


def test_recommendations_filter_by_status(dev_client, seeded_recommendation):
    ws, _, _ = seeded_recommendation
    r = dev_client.get(
        f"/api/v1/workspaces/{ws.id}/recommendations",
        params={"status": "accepted"},
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert r.status_code == 200
    assert r.json() == []


def test_recommendations_workspace_isolation(dev_client, db_conn, seeded_recommendation):
    """Cross-workspace lookup returns empty — not 403 — to avoid enumeration."""
    ws2 = cp_repo.create_workspace(
        db_conn,
        WorkspaceDraft(id=_uid(), name="Other Workspace", slug="other-ws", actor="cli"),
    )
    db_conn.commit()
    r = dev_client.get(
        f"/api/v1/workspaces/{ws2.id}/recommendations",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert r.status_code == 200
    assert r.json() == []


# ---------------------------------------------------------------------------
# Accept
# ---------------------------------------------------------------------------


def test_accept_recommendation(dev_client, seeded_recommendation):
    ws, _, rec_id = seeded_recommendation
    r = dev_client.post(
        f"/api/v1/workspaces/{ws.id}/recommendations/{rec_id}/accept",
        headers={"X-Dev-Actor": "dev:studio-user"},
        json={},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["event_type"] == "accepted"
    assert body["recommendation_id"] == rec_id


def test_accept_recommendation_with_notes(dev_client, seeded_recommendation):
    ws, _, rec_id = seeded_recommendation
    r = dev_client.post(
        f"/api/v1/workspaces/{ws.id}/recommendations/{rec_id}/accept",
        headers={"X-Dev-Actor": "dev:studio-user"},
        json={"notes": "Good signal"},
    )
    assert r.status_code == 200
    assert r.json()["notes"] == "Good signal"


def test_accept_cross_workspace_returns_404(dev_client, db_conn, seeded_recommendation):
    _, _, rec_id = seeded_recommendation
    ws2 = cp_repo.create_workspace(
        db_conn,
        WorkspaceDraft(id=_uid(), name="Other", slug="other2", actor="cli"),
    )
    db_conn.commit()
    r = dev_client.post(
        f"/api/v1/workspaces/{ws2.id}/recommendations/{rec_id}/accept",
        headers={"X-Dev-Actor": "dev:studio-user"},
        json={},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Reject
# ---------------------------------------------------------------------------


def test_reject_recommendation(dev_client, seeded_recommendation):
    ws, _, rec_id = seeded_recommendation
    r = dev_client.post(
        f"/api/v1/workspaces/{ws.id}/recommendations/{rec_id}/reject",
        headers={"X-Dev-Actor": "dev:studio-user"},
        json={"notes": "Not relevant to current strategy"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["event_type"] == "rejected"
    assert body["recommendation_id"] == rec_id
    assert body["notes"] == "Not relevant to current strategy"


def test_reject_requires_notes(dev_client, seeded_recommendation):
    ws, _, rec_id = seeded_recommendation
    r = dev_client.post(
        f"/api/v1/workspaces/{ws.id}/recommendations/{rec_id}/reject",
        headers={"X-Dev-Actor": "dev:studio-user"},
        json={"notes": ""},
    )
    assert r.status_code == 400
    assert "notes" in r.json()["detail"].lower()


def test_reject_cross_workspace_returns_404(dev_client, db_conn, seeded_recommendation):
    _, _, rec_id = seeded_recommendation
    ws2 = cp_repo.create_workspace(
        db_conn,
        WorkspaceDraft(id=_uid(), name="Other", slug="other3", actor="cli"),
    )
    db_conn.commit()
    r = dev_client.post(
        f"/api/v1/workspaces/{ws2.id}/recommendations/{rec_id}/reject",
        headers={"X-Dev-Actor": "dev:studio-user"},
        json={"notes": "not relevant"},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# RBAC — workspace role enforcement (production JWT mode)
# ---------------------------------------------------------------------------


def test_recommendations_reviewer_role_can_read(prod_client, seeded_recommendation):
    """REVIEWER satisfies recommendations:view (ANALYST minimum)."""
    ws, _, _ = seeded_recommendation
    token = _jwt(ws.id, "reviewer")
    r = prod_client.get(
        f"/api/v1/workspaces/{ws.id}/recommendations",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_recommendations_analyst_role_can_read(prod_client, seeded_recommendation):
    """ANALYST is the minimum required role for recommendations:view."""
    ws, _, _ = seeded_recommendation
    token = _jwt(ws.id, "analyst")
    r = prod_client.get(
        f"/api/v1/workspaces/{ws.id}/recommendations",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200


def test_recommendations_no_role_is_403(prod_client, seeded_recommendation):
    """A valid JWT with no role in the target workspace → 403."""
    ws, _, _ = seeded_recommendation
    token = _jwt(_uid(), "analyst")
    r = prod_client.get(
        f"/api/v1/workspaces/{ws.id}/recommendations",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403


def test_accept_reviewer_role_succeeds(prod_client, seeded_recommendation):
    """REVIEWER satisfies recommendations:review for accept."""
    ws, _, rec_id = seeded_recommendation
    token = _jwt(ws.id, "reviewer")
    r = prod_client.post(
        f"/api/v1/workspaces/{ws.id}/recommendations/{rec_id}/accept",
        headers={"Authorization": f"Bearer {token}"},
        json={},
    )
    assert r.status_code == 200


def test_accept_analyst_role_is_403(prod_client, seeded_recommendation):
    """ANALYST is below the REVIEWER minimum for recommendations:review."""
    ws, _, rec_id = seeded_recommendation
    token = _jwt(ws.id, "analyst")
    r = prod_client.post(
        f"/api/v1/workspaces/{ws.id}/recommendations/{rec_id}/accept",
        headers={"Authorization": f"Bearer {token}"},
        json={},
    )
    assert r.status_code == 403


def test_reject_reviewer_role_succeeds(prod_client, seeded_recommendation):
    """REVIEWER satisfies recommendations:review for reject."""
    ws, _, rec_id = seeded_recommendation
    token = _jwt(ws.id, "reviewer")
    r = prod_client.post(
        f"/api/v1/workspaces/{ws.id}/recommendations/{rec_id}/reject",
        headers={"Authorization": f"Bearer {token}"},
        json={"notes": "Not a priority right now"},
    )
    assert r.status_code == 200


def test_reject_no_role_is_403(prod_client, seeded_recommendation):
    """No workspace role → 403 on reject endpoint."""
    ws, _, rec_id = seeded_recommendation
    token = _jwt(_uid(), "reviewer")
    r = prod_client.post(
        f"/api/v1/workspaces/{ws.id}/recommendations/{rec_id}/reject",
        headers={"Authorization": f"Bearer {token}"},
        json={"notes": "notes"},
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# State machine — all valid and invalid recommendation transitions
# ---------------------------------------------------------------------------


def test_transition_pending_to_accepted(dev_client, seeded_recommendation):
    """pending → accepted is the canonical acceptance flow."""
    ws, _, rec_id = seeded_recommendation
    r = dev_client.post(
        f"/api/v1/workspaces/{ws.id}/recommendations/{rec_id}/accept",
        headers={"X-Dev-Actor": "dev:studio-user"},
        json={},
    )
    assert r.status_code == 200
    assert r.json()["event_type"] == "accepted"


def test_transition_pending_to_rejected(dev_client, seeded_recommendation):
    """pending → rejected is the canonical rejection flow."""
    ws, _, rec_id = seeded_recommendation
    r = dev_client.post(
        f"/api/v1/workspaces/{ws.id}/recommendations/{rec_id}/reject",
        headers={"X-Dev-Actor": "dev:studio-user"},
        json={"notes": "Not applicable to this topic"},
    )
    assert r.status_code == 200
    assert r.json()["event_type"] == "rejected"


def test_transition_accepted_to_accepted(dev_client, seeded_recommendation):
    """accepted → accepted appends a second event; status remains accepted.

    The domain uses an append-only event log: non-superseded recommendations
    always accept further review events, including re-acceptance.
    """
    ws, _, rec_id = seeded_recommendation
    url = f"/api/v1/workspaces/{ws.id}/recommendations/{rec_id}/accept"
    h = {"X-Dev-Actor": "dev:studio-user"}
    dev_client.post(url, headers=h, json={})
    r = dev_client.post(url, headers=h, json={})
    assert r.status_code == 200
    assert r.json()["event_type"] == "accepted"


def test_transition_rejected_to_rejected(dev_client, seeded_recommendation):
    """rejected → rejected appends a second event; status remains rejected."""
    ws, _, rec_id = seeded_recommendation
    url = f"/api/v1/workspaces/{ws.id}/recommendations/{rec_id}/reject"
    h = {"X-Dev-Actor": "dev:studio-user"}
    dev_client.post(url, headers=h, json={"notes": "First reason"})
    r = dev_client.post(url, headers=h, json={"notes": "Second reason"})
    assert r.status_code == 200
    assert r.json()["event_type"] == "rejected"


def test_transition_accepted_to_rejected(dev_client, seeded_recommendation):
    """accepted → rejected flips the status; previous accept event is preserved."""
    ws, _, rec_id = seeded_recommendation
    h = {"X-Dev-Actor": "dev:studio-user"}
    dev_client.post(
        f"/api/v1/workspaces/{ws.id}/recommendations/{rec_id}/accept",
        headers=h,
        json={},
    )
    r = dev_client.post(
        f"/api/v1/workspaces/{ws.id}/recommendations/{rec_id}/reject",
        headers=h,
        json={"notes": "Changed my mind"},
    )
    assert r.status_code == 200
    assert r.json()["event_type"] == "rejected"


def test_transition_rejected_to_accepted(dev_client, seeded_recommendation):
    """rejected → accepted flips the status; previous reject event is preserved."""
    ws, _, rec_id = seeded_recommendation
    h = {"X-Dev-Actor": "dev:studio-user"}
    dev_client.post(
        f"/api/v1/workspaces/{ws.id}/recommendations/{rec_id}/reject",
        headers=h,
        json={"notes": "Initial reject"},
    )
    r = dev_client.post(
        f"/api/v1/workspaces/{ws.id}/recommendations/{rec_id}/accept",
        headers=h,
        json={},
    )
    assert r.status_code == 200
    assert r.json()["event_type"] == "accepted"


def test_transition_superseded_to_accepted_is_400(dev_client, db_conn, seeded_recommendation):
    """superseded → accept must be rejected; superseded is not review-eligible."""
    ws, _, rec_id = seeded_recommendation
    # Force supersede via repository.
    from app.learning.repository import supersede_recommendation

    new_rec_cur = db_conn.execute(
        """INSERT INTO optimization_recommendations
               (learning_run_id, topic_id, publication_id, domain, subsystem, measure,
                title, explanation, expected_improvement,
                evidence_json, evidence_classification, recommendation_strength,
                confidence, confidence_score,
                affected_subsystem, subsystem_entity_type, subsystem_entity_id,
                experiment_id, engine_version, schema_version, input_hash,
                status, superseded_at, superseded_by_id, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            1,
            1,
            None,
            "scripts",
            "hook",
            "ctr",
            "New rec",
            "New explanation",
            "New improvement",
            "[]",
            "observational",
            "actionable",
            "medium",
            0.65,
            "",
            "",
            None,
            None,
            LEARNING_ENGINE_VERSION,
            LEARNING_SCHEMA_VERSION,
            "hash-new-rec",
            STATUS_PENDING,
            None,
            None,
            NOW,
        ),
    )
    new_id = new_rec_cur.lastrowid
    supersede_recommendation(db_conn, rec_id, new_id)
    db_conn.commit()
    r = dev_client.post(
        f"/api/v1/workspaces/{ws.id}/recommendations/{rec_id}/accept",
        headers={"X-Dev-Actor": "dev:studio-user"},
        json={},
    )
    assert r.status_code == 400
    assert "superseded" in r.json()["detail"].lower()


def test_transition_superseded_to_rejected_is_400(dev_client, db_conn, seeded_recommendation):
    """superseded → reject must be rejected; superseded is not review-eligible."""
    ws, _, rec_id = seeded_recommendation
    from app.learning.repository import supersede_recommendation

    new_rec_cur = db_conn.execute(
        """INSERT INTO optimization_recommendations
               (learning_run_id, topic_id, publication_id, domain, subsystem, measure,
                title, explanation, expected_improvement,
                evidence_json, evidence_classification, recommendation_strength,
                confidence, confidence_score,
                affected_subsystem, subsystem_entity_type, subsystem_entity_id,
                experiment_id, engine_version, schema_version, input_hash,
                status, superseded_at, superseded_by_id, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            1,
            1,
            None,
            "scripts",
            "hook",
            "ctr",
            "New rec 2",
            "New explanation 2",
            "New improvement 2",
            "[]",
            "observational",
            "actionable",
            "medium",
            0.65,
            "",
            "",
            None,
            None,
            LEARNING_ENGINE_VERSION,
            LEARNING_SCHEMA_VERSION,
            "hash-new-rec-2",
            STATUS_PENDING,
            None,
            None,
            NOW,
        ),
    )
    new_id = new_rec_cur.lastrowid
    supersede_recommendation(db_conn, rec_id, new_id)
    db_conn.commit()
    r = dev_client.post(
        f"/api/v1/workspaces/{ws.id}/recommendations/{rec_id}/reject",
        headers={"X-Dev-Actor": "dev:studio-user"},
        json={"notes": "This was superseded"},
    )
    assert r.status_code == 400
    assert "superseded" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Auditability — review events are persisted with actor + timestamp + notes
# ---------------------------------------------------------------------------


def test_accept_persists_review_event_with_actor(dev_client, db_conn, seeded_recommendation):
    """Accept stores a review event with reviewer=actor, event_type=accepted."""
    ws, _, rec_id = seeded_recommendation
    dev_client.post(
        f"/api/v1/workspaces/{ws.id}/recommendations/{rec_id}/accept",
        headers={"X-Dev-Actor": "dev:studio-user"},
        json={"notes": "Good signal"},
    )
    row = db_conn.execute(
        "SELECT reviewer, event_type, notes "
        "FROM recommendation_review_events WHERE recommendation_id = ?",
        (rec_id,),
    ).fetchone()
    assert row is not None
    assert row[0] == "dev:studio-user"
    assert row[1] == "accepted"
    assert row[2] == "Good signal"


def test_reject_persists_review_event_with_actor(dev_client, db_conn, seeded_recommendation):
    """Reject stores a review event with reviewer=actor, event_type=rejected, notes."""
    ws, _, rec_id = seeded_recommendation
    dev_client.post(
        f"/api/v1/workspaces/{ws.id}/recommendations/{rec_id}/reject",
        headers={"X-Dev-Actor": "dev:studio-user"},
        json={"notes": "Not relevant to this topic"},
    )
    row = db_conn.execute(
        "SELECT reviewer, event_type, notes "
        "FROM recommendation_review_events WHERE recommendation_id = ?",
        (rec_id,),
    ).fetchone()
    assert row is not None
    assert row[0] == "dev:studio-user"
    assert row[1] == "rejected"
    assert row[2] == "Not relevant to this topic"
