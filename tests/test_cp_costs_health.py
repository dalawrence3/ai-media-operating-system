"""Tests for Phase 12 cost tracking and health management."""

from __future__ import annotations

import pytest

from app.control_plane.costs import check_budget, set_budget
from app.control_plane.errors import BudgetExceededError
from app.control_plane.health import get_health, list_degraded_entities, record_health
from app.control_plane.resources import record_cost


@pytest.fixture()
def db(tmp_path):
    from app.core.database import open_db

    return open_db(tmp_path / "ch.db")


@pytest.fixture()
def workspace(db):
    from app.control_plane.identity import create_workspace

    return create_workspace(db, name="Acme", slug="acme", actor="cli")


class TestCostRecording:
    def test_record_cost(self, db, workspace):
        cost = record_cost(
            db,
            workspace_id=workspace.id,
            provider_key="claude",
            cost_unit="tokens",
            quantity=1000,
            usd_equivalent=0.003,
            description="Script generation",
        )
        assert cost.usd_equivalent == 0.003
        assert cost.provider_key == "claude"

    def test_record_multiple_costs(self, db, workspace):
        record_cost(
            db,
            workspace_id=workspace.id,
            provider_key="claude",
            cost_unit="tokens",
            quantity=500,
            usd_equivalent=0.0015,
        )
        record_cost(
            db,
            workspace_id=workspace.id,
            provider_key="elevenlabs",
            cost_unit="characters",
            quantity=200,
            usd_equivalent=0.002,
        )
        from app.control_plane.services import get_cost_summary

        summary = get_cost_summary(db, workspace.id)
        assert summary["record_count"] == 2
        assert abs(summary["total_usd"] - 0.0035) < 0.0001
        assert "claude" in summary["by_provider"]
        assert "elevenlabs" in summary["by_provider"]


class TestBudgetPolicies:
    def test_set_budget(self, db, workspace):
        policy = set_budget(
            db,
            scope="workspace",
            scope_id=workspace.id,
            period="monthly",
            limit_usd=100.0,
            actor="cli",
        )
        assert policy.limit_usd == 100.0
        assert policy.is_active

    def test_check_budget_ok_when_under_limit(self, db, workspace):
        set_budget(
            db,
            scope="workspace",
            scope_id=workspace.id,
            period="monthly",
            limit_usd=100.0,
            actor="cli",
        )
        result = check_budget(db, workspace.id)
        assert result["ok"]
        assert result["warnings"] == []

    def test_warning_when_near_limit(self, db, workspace):
        set_budget(
            db,
            scope="workspace",
            scope_id=workspace.id,
            period="monthly",
            limit_usd=1.0,
            actor="cli",
            warning_threshold=0.5,
        )
        record_cost(
            db,
            workspace_id=workspace.id,
            provider_key="claude",
            cost_unit="usd",
            quantity=1,
            usd_equivalent=0.6,
        )
        result = check_budget(db, workspace.id)
        assert any("budget_warning" in w or "budget_exceeded" in w for w in result["warnings"])

    def test_block_action_raises_when_exceeded(self, db, workspace):
        set_budget(
            db,
            scope="workspace",
            scope_id=workspace.id,
            period="monthly",
            limit_usd=0.001,
            actor="cli",
            on_exceed_action="block",
        )
        record_cost(
            db,
            workspace_id=workspace.id,
            provider_key="claude",
            cost_unit="usd",
            quantity=1,
            usd_equivalent=0.5,
        )
        with pytest.raises(BudgetExceededError) as exc_info:
            check_budget(db, workspace.id)
        assert exc_info.value.scope == "workspace"

    def test_no_budget_policy_returns_ok(self, db, workspace):
        result = check_budget(db, workspace.id)
        assert result["ok"]
        assert result["warnings"] == []

    def test_warn_action_does_not_raise(self, db, workspace):
        set_budget(
            db,
            scope="workspace",
            scope_id=workspace.id,
            period="monthly",
            limit_usd=0.001,
            actor="cli",
            on_exceed_action="warn",
        )
        record_cost(
            db,
            workspace_id=workspace.id,
            provider_key="claude",
            cost_unit="usd",
            quantity=1,
            usd_equivalent=1.0,
        )
        result = check_budget(db, workspace.id)
        assert result["ok"]
        assert len(result["warnings"]) > 0


class TestHealthManagement:
    def test_record_and_get_health(self, db):
        record_health(
            db,
            entity_type="channel",
            entity_id="ch-1",
            status="healthy",
            recorded_by="monitor",
        )
        h = get_health(db, "channel", "ch-1")
        assert h is not None
        assert h.status == "healthy"

    def test_latest_record_returned(self, db):
        record_health(
            db, entity_type="provider", entity_id="claude", status="healthy", recorded_by="monitor"
        )
        record_health(
            db,
            entity_type="provider",
            entity_id="claude",
            status="degraded",
            recorded_by="monitor",
            detail="High latency",
        )
        h = get_health(db, "provider", "claude")
        assert h is not None
        assert h.status == "degraded"

    def test_missing_entity_returns_none(self, db):
        h = get_health(db, "channel", "nobody")
        assert h is None

    def test_list_degraded_entities(self, db):
        record_health(
            db,
            entity_type="platform_account",
            entity_id="acc-1",
            status="unavailable",
            recorded_by="monitor",
        )
        record_health(
            db, entity_type="channel", entity_id="ch-2", status="healthy", recorded_by="monitor"
        )
        degraded = list_degraded_entities(db)
        ids = [h.entity_id for h in degraded]
        assert "acc-1" in ids
        assert "ch-2" not in ids

    def test_multiple_degraded_entities(self, db):
        for i in range(3):
            record_health(
                db,
                entity_type="platform_account",
                entity_id=f"acc-{i}",
                status="failed",
                recorded_by="monitor",
            )
        degraded = list_degraded_entities(db)
        assert len(degraded) >= 3


class TestListDegradedEntitiesLatestOnly:
    """Read-model fix: list_degraded_entities() must reflect only each
    entity's CURRENT (latest) health record, mirroring the latest-per-entity
    semantics Exception Center already uses (Phase 18E.2). cp_health_records
    remains append-only history throughout every test here — no row is ever
    updated or deleted."""

    def _record(self, db, entity_id, status, entity_type="platform_account", detail=None):
        record_health(
            db,
            entity_type=entity_type,
            entity_id=entity_id,
            status=status,
            recorded_by="monitor",
            detail=detail,
        )

    def _ids_and_status(self, db):
        return {h.entity_id: h.status for h in list_degraded_entities(db)}

    def test_a_first_degraded_record_is_one_issue(self, db):
        self._record(db, "acc-a", "degraded")
        result = self._ids_and_status(db)
        assert result == {"acc-a": "degraded"}

    def test_b_repeated_history_yields_one_result_latest_state(self, db):
        for status in ("degraded", "unavailable", "unavailable", "degraded", "unavailable"):
            self._record(db, "acc-b", status)
        result = self._ids_and_status(db)
        assert result == {"acc-b": "unavailable"}
        assert len([h for h in list_degraded_entities(db) if h.entity_id == "acc-b"]) == 1

    def test_c_degraded_then_unavailable_shows_unavailable(self, db):
        self._record(db, "acc-c", "degraded")
        self._record(db, "acc-c", "unavailable")
        result = self._ids_and_status(db)
        assert result == {"acc-c": "unavailable"}

    def test_d_recovery_to_healthy_yields_zero_results(self, db):
        self._record(db, "acc-d", "degraded")
        self._record(db, "acc-d", "unavailable")
        self._record(db, "acc-d", "healthy")
        result = self._ids_and_status(db)
        assert "acc-d" not in result

    def test_e_degraded_healthy_degraded_shows_latest_degraded(self, db):
        self._record(db, "acc-e", "degraded")
        self._record(db, "acc-e", "healthy")
        self._record(db, "acc-e", "degraded")
        result = self._ids_and_status(db)
        assert result == {"acc-e": "degraded"}

    def test_f_two_entities_only_degraded_one_returned(self, db):
        self._record(db, "acc-f-healthy", "healthy")
        self._record(db, "acc-f-degraded", "degraded")
        result = self._ids_and_status(db)
        assert "acc-f-healthy" not in result
        assert result.get("acc-f-degraded") == "degraded"

    def test_g_identical_timestamps_deterministic_tiebreak(self, db, monkeypatch):
        """Force two records for the same entity to share recorded_at exactly
        — the newest INSERT (highest rowid) must win, not insertion order
        reversed or an arbitrary row."""
        import app.control_plane.repository as repo_mod

        frozen = "2026-01-01T00:00:00+00:00"
        monkeypatch.setattr(
            repo_mod, "_now", lambda: type("_D", (), {"isoformat": lambda self: frozen})()
        )
        self._record(db, "acc-g", "degraded")
        self._record(db, "acc-g", "unavailable")  # same recorded_at, later rowid
        result = self._ids_and_status(db)
        assert result == {"acc-g": "unavailable"}

    def test_h_review_center_pending_count_uses_corrected_result(self, db):
        from app.control_plane.identity import create_workspace
        from app.control_plane.reviews import build_review_queue

        ws = create_workspace(db, name="Acme", slug="acme-h", actor="cli")
        self._record(db, ws.id, "degraded", entity_type="workspace")
        self._record(db, ws.id, "healthy", entity_type="workspace")
        items = build_review_queue(db, ws.id)
        assert not any(i.item_type == "health_issue" and i.entity_id == ws.id for i in items)

    def test_i_exception_center_and_review_center_agree_on_recovered_entity(self, db):
        from app.application.exception_queue import get_exception_queue
        from app.control_plane.identity import create_workspace

        ws = create_workspace(db, name="Acme", slug="acme-i", actor="cli")
        self._record(db, ws.id, "unavailable", entity_type="workspace")
        self._record(db, ws.id, "healthy", entity_type="workspace")

        degraded_ids = {h.entity_id for h in list_degraded_entities(db)}
        exceptions = get_exception_queue(db, ws.id)
        exception_health_ids = {
            e.entity_id for e in exceptions if e.exception_type == "health_degraded"
        }
        assert ws.id not in degraded_ids
        assert ws.id not in exception_health_ids
