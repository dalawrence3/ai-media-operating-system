"""Tests for workspace health checks."""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

import pytest

from app.application import health as health_mod
from app.application.contracts import HealthView
from app.control_plane import repository as cp_repo
from app.control_plane.models import OrganizationDraft, WorkspaceDraft
from app.core.database import open_db


def _uid() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def conn():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = Path(f.name)
    c = open_db(path)
    yield c
    c.close()
    path.unlink(missing_ok=True)


@pytest.fixture
def workspace(conn):
    org = cp_repo.create_organization(
        conn, OrganizationDraft(id=_uid(), name="Org", slug="org", actor="cli")
    )
    return cp_repo.create_workspace(
        conn,
        WorkspaceDraft(id=_uid(), name="WS", slug="ws", actor="cli", organization_id=org.id),
    )


class TestGetWorkspaceHealth:
    def test_returns_health_view(self, conn, workspace):
        result = health_mod.get_workspace_health(conn, workspace.id)
        assert isinstance(result, HealthView)

    def test_overall_status_valid_values(self, conn, workspace):
        result = health_mod.get_workspace_health(conn, workspace.id)
        assert result.overall_status in ("ok", "warn", "degraded")

    def test_new_workspace_ok_or_warn(self, conn, workspace):
        result = health_mod.get_workspace_health(conn, workspace.id)
        assert result.overall_status != "degraded"

    def test_workspace_id_in_result(self, conn, workspace):
        result = health_mod.get_workspace_health(conn, workspace.id)
        assert result.workspace_id == workspace.id

    def test_budget_status_present(self, conn, workspace):
        result = health_mod.get_workspace_health(conn, workspace.id)
        assert result.budget_status in ("ok", "warn", "block", "unknown")

    def test_event_bus_ok_initially(self, conn, workspace):
        result = health_mod.get_workspace_health(conn, workspace.id)
        assert result.event_bus_ok is True

    def test_dead_letter_count_zero_initially(self, conn, workspace):
        result = health_mod.get_workspace_health(conn, workspace.id)
        assert result.dead_letter_count == 0

    def test_stuck_operation_count_zero_initially(self, conn, workspace):
        result = health_mod.get_workspace_health(conn, workspace.id)
        assert result.stuck_operation_count == 0

    def test_active_pipeline_count_zero_initially(self, conn, workspace):
        result = health_mod.get_workspace_health(conn, workspace.id)
        assert result.active_pipeline_count == 0

    def test_details_dict_present(self, conn, workspace):
        result = health_mod.get_workspace_health(conn, workspace.id)
        assert isinstance(result.details, dict)

    def test_nonexistent_workspace_does_not_crash(self, conn):
        result = health_mod.get_workspace_health(conn, "nonexistent-ws")
        assert isinstance(result, HealthView)
