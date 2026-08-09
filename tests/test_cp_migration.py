"""Tests for Phase 12 schema migration (v17 → v18)."""

from __future__ import annotations

import sqlite3

import pytest

from app.core.database import SCHEMA_VERSION, open_db


def _in_memory_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


class TestSchemaVersion:
    def test_schema_version_is_18(self):
        assert SCHEMA_VERSION == 20


class TestV18DDLStandalone:
    def test_control_plane_tables_creatable(self, tmp_path):
        db_path = tmp_path / "test_v18.db"
        conn = open_db(db_path)
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        cp_tables = {
            "cp_organizations",
            "cp_workspaces",
            "cp_channels",
            "cp_platforms",
            "cp_credential_profiles",
            "cp_platform_accounts",
            "cp_publishing_profiles",
            "cp_analytics_identities",
            "cp_automation_policies",
            "cp_strategy_profiles",
            "cp_events",
            "cp_event_processing",
            "cp_workflows",
            "cp_workflow_runs",
            "cp_experiments",
            "cp_experiment_variants",
            "cp_experiment_assignments",
            "cp_operation_executions",
            "cp_cost_records",
            "cp_budget_policies",
            "cp_health_records",
            "cp_provider_registry",
        }
        for table in cp_tables:
            assert table in tables, f"Missing table: {table}"

    def test_total_cp_table_count_is_22(self, tmp_path):
        db_path = tmp_path / "test_count.db"
        conn = open_db(db_path)
        cp_tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'cp_%'"
            ).fetchall()
        ]
        assert len(cp_tables) == 22


class TestV18Indexes:
    def test_expected_indexes_exist(self, tmp_path):
        db_path = tmp_path / "test_idx.db"
        conn = open_db(db_path)
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_cp_%'"
            ).fetchall()
        }
        expected = {
            "idx_cp_policy_scope",
            "idx_cp_strategy_channel",
            "idx_cp_events_workspace",
            "idx_cp_events_type",
            "idx_cp_ep_pending",
            "idx_cp_wf_workspace",
            "idx_cp_wf_trigger",
            "idx_cp_wfrun_workflow",
            "idx_cp_exp_workspace",
            "idx_cp_exp_channel",
            "idx_cp_assign_exp",
            "idx_cp_op_workspace",
            "idx_cp_op_idem",
            "idx_cp_cost_workspace",
            "idx_cp_cost_channel",
            "idx_cp_budget_scope",
            "idx_cp_health_entity",
            "idx_cp_health_status",
            "idx_cp_provider_domain",
            "idx_cp_pubprofile_account",
            "idx_cp_analytics_account",
        }
        for idx in expected:
            assert idx in indexes, f"Missing index: {idx}"


class TestV17ToV18Migration:
    def test_fresh_v18_schema_has_version_18(self, tmp_path):
        db_path = tmp_path / "fresh.db"
        conn = open_db(db_path)
        version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
        assert version == 20

    def test_prior_v17_tables_still_present(self, tmp_path):
        db_path = tmp_path / "v17_migrate.db"
        conn = open_db(db_path)
        v17_tables = [
            "learning_runs",
            "optimization_recommendations",
            "recommendation_review_events",
            "learning_run_generator_results",
        ]
        existing = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        for table in v17_tables:
            assert table in existing


class TestWorkspaceConstraints:
    def test_slug_unique_constraint(self, tmp_path):
        conn = open_db(tmp_path / "slug.db")
        now = "2026-08-07T10:00:00"
        conn.execute(
            "INSERT INTO cp_workspaces VALUES (?,?,?,?,?,?,?,?,?)",
            ("ws-1", "Acme", "acme", "active", "cli", None, now, now, None),
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO cp_workspaces VALUES (?,?,?,?,?,?,?,?,?)",
                ("ws-2", "Acme 2", "acme", "active", "cli", None, now, now, None),
            )
            conn.commit()

    def test_status_check_constraint(self, tmp_path):
        conn = open_db(tmp_path / "status.db")
        now = "2026-08-07T10:00:00"
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO cp_workspaces VALUES (?,?,?,?,?,?,?,?,?)",
                ("ws-1", "X", "x", "invalid_status", "cli", None, now, now, None),
            )
            conn.commit()


class TestAutomationPolicyConstraints:
    def test_level_check_constraint(self, tmp_path):
        conn = open_db(tmp_path / "policy.db")
        now = "2026-08-07T10:00:00"
        ws_id = "ws-1"
        conn.execute(
            "INSERT INTO cp_workspaces VALUES (?,?,?,?,?,?,?,?,?)",
            (ws_id, "Acme", "acme", "active", "cli", None, now, now, None),
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO cp_automation_policies "
                "(id, scope, scope_id, automation_level, "
                "allowed_actions_json, actor, created_at, is_active) "
                "VALUES (?,?,?,?,?,?,?,?)",
                ("p-1", "workspace", ws_id, "superauto", "[]", "cli", now, 1),
            )
            conn.commit()


class TestExperimentAssignmentUnique:
    def test_unit_can_only_be_assigned_once(self, tmp_path):
        conn = open_db(tmp_path / "assign.db")
        now = "2026-08-07T10:00:00"
        conn.execute(
            "INSERT INTO cp_workspaces VALUES (?,?,?,?,?,?,?,?,?)",
            ("ws-1", "A", "a", "active", "cli", None, now, now, None),
        )
        conn.execute(
            "INSERT INTO cp_channels VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("ch-1", "ws-1", "Tech", "tech", "active", "cli", None, None, now, now),
        )
        conn.execute(
            "INSERT INTO cp_experiments VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "e-1",
                "ws-1",
                "ch-1",
                "Test",
                "H",
                "draft",
                "ctr",
                "cli",
                now,
                now,
                None,
                None,
                None,
                None,
                None,
            ),
        )
        conn.execute(
            "INSERT INTO cp_experiment_variants VALUES (?,?,?,?,?,?,?)",
            ("v-1", "e-1", "Control", "control", None, None, now),
        )
        conn.commit()
        conn.execute(
            "INSERT INTO cp_experiment_assignments VALUES (?,?,?,?,?,?)",
            ("a-1", "e-1", "v-1", "unit-123", "active", now),
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO cp_experiment_assignments VALUES (?,?,?,?,?,?)",
                ("a-2", "e-1", "v-1", "unit-123", "active", now),
            )
            conn.commit()


class TestIdempotencyKeyUnique:
    def test_duplicate_idempotency_key_rejected(self, tmp_path):
        conn = open_db(tmp_path / "idem.db")
        now = "2026-08-07T10:00:00"
        conn.execute(
            "INSERT INTO cp_workspaces VALUES (?,?,?,?,?,?,?,?,?)",
            ("ws-1", "A", "a", "active", "cli", None, now, now, None),
        )
        conn.commit()
        conn.execute(
            "INSERT INTO cp_events "
            "(id, event_type, workspace_id, actor, payload_json, "
            "correlation_id, causation_id, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("ev-1", "workspace.created", "ws-1", "cli", "{}", None, None, now),
        )
        conn.commit()
        conn.execute(
            "INSERT INTO cp_operation_executions "
            "(id, operation_type, workspace_id, idempotency_key, "
            "status, actor, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("op-1", "publish", "ws-1", "key-abc", "pending", "cli", now, now),
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO cp_operation_executions "
                "(id, operation_type, workspace_id, idempotency_key, "
                "status, actor, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                ("op-2", "publish", "ws-1", "key-abc", "pending", "cli", now, now),
            )
            conn.commit()
