"""Tests for workspace health checks."""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

import pytest

from app.application import health as health_mod
from app.application.contracts import HealthView
from app.control_plane import repository as cp_repo
from app.control_plane.models import (
    ChannelDraft,
    HealthRecordDraft,
    OrganizationDraft,
    WorkspaceDraft,
)
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


def _write_health(
    conn, workspace_id: str, status: str, detail: str = "", entity_type: str = "workspace"
) -> None:
    """Write a health record for a workspace-scoped entity (workspace itself, by default)."""
    cp_repo.create_health_record(
        conn,
        HealthRecordDraft(
            id=_uid(),
            entity_type=entity_type,
            entity_id=workspace_id,
            status=status,
            recorded_by="test",
            detail=detail,
        ),
    )


class TestWorkspaceHealthLatestPerEntity:
    """get_workspace_health() must evaluate ONLY the latest health record per entity.

    The append-only cp_health_records table means historical degraded rows remain
    forever.  Workspace health must not be affected by a degraded record that has
    been superseded by a later healthy one.
    """

    def test_latest_degraded_record_produces_degraded_status(self, conn, workspace):
        """Sanity: a latest degraded record still degrades workspace health."""
        _write_health(conn, workspace.id, "degraded", "token failed")
        result = health_mod.get_workspace_health(conn, workspace.id)
        assert result.overall_status == "degraded"
        entity_ids = [e["entity_id"] for e in result.details["degraded_entities"]]
        assert workspace.id in entity_ids

    def test_degraded_then_healthy_clears_workspace_health(self, conn, workspace):
        """Live bug: old degraded + newer healthy → workspace must be ok (not degraded)."""
        _write_health(conn, workspace.id, "degraded", "token failed")
        _write_health(conn, workspace.id, "healthy", "restored after observation")

        result = health_mod.get_workspace_health(conn, workspace.id)
        assert result.overall_status != "degraded", (
            "Historical degraded record must not mark workspace as degraded "
            "when the latest record is healthy."
        )
        entity_ids = [e["entity_id"] for e in result.details["degraded_entities"]]
        assert workspace.id not in entity_ids

    def test_historical_rows_remain_in_db_after_recovery(self, conn, workspace):
        """Append-only invariant: the old degraded row must not be deleted."""
        _write_health(conn, workspace.id, "degraded", "old")
        _write_health(conn, workspace.id, "healthy", "new")

        all_rows = conn.execute(
            "SELECT status FROM cp_health_records WHERE entity_id=? ORDER BY recorded_at",
            (workspace.id,),
        ).fetchall()
        statuses = [r["status"] for r in all_rows]
        assert "degraded" in statuses, "Historical degraded record must be preserved."
        assert "healthy" in statuses

    def test_multiple_entities_each_use_own_latest_record(self, conn, workspace):
        """Two entities in the same workspace are evaluated independently from their
        own latest records — a channel's degraded status must not be masked by (or
        leak onto) the workspace entity's own status, and vice versa."""
        channel = cp_repo.create_channel(
            conn,
            ChannelDraft(
                id=_uid(), workspace_id=workspace.id, name="Chan", slug="chan", actor="cli"
            ),
        )

        # Workspace entity: degraded -> healthy (latest wins; should read ok).
        _write_health(conn, workspace.id, "degraded", "old")
        _write_health(conn, workspace.id, "healthy", "new")
        # Channel entity: healthy -> degraded (latest wins; should read degraded).
        _write_health(conn, channel.id, "healthy", "old", entity_type="channel")
        _write_health(conn, channel.id, "degraded", "new", entity_type="channel")

        result = health_mod.get_workspace_health(conn, workspace.id)
        degraded_entity_ids = [e["entity_id"] for e in result.details["degraded_entities"]]
        assert workspace.id not in degraded_entity_ids, (
            "Workspace entity's latest record is healthy — must not appear degraded "
            "just because a sibling entity in the same workspace is degraded."
        )
        assert channel.id in degraded_entity_ids, (
            "Channel entity's latest record is degraded and must be reported, "
            "independently of the workspace entity's own status."
        )

    def test_more_than_50_historical_records_uses_latest(self, conn, workspace):
        """LIMIT 50 must not cause a healthy entity to appear as degraded."""
        # Write 55 degraded records then one healthy record.
        for i in range(55):
            _write_health(conn, workspace.id, "degraded", f"failure {i}")
        _write_health(conn, workspace.id, "healthy", "finally recovered")

        result = health_mod.get_workspace_health(conn, workspace.id)
        # The latest record is healthy, so the entity must not appear as degraded.
        entity_ids = [e["entity_id"] for e in result.details["degraded_entities"]]
        assert workspace.id not in entity_ids

    def test_entity_with_no_health_record_not_in_degraded(self, conn, workspace):
        """An entity with no health records is simply absent from degraded_entities."""
        result = health_mod.get_workspace_health(conn, workspace.id)
        entity_ids = [e["entity_id"] for e in result.details["degraded_entities"]]
        assert workspace.id not in entity_ids
