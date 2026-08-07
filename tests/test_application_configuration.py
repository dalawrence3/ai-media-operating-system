"""Tests for effective configuration hierarchy resolution."""

from __future__ import annotations

import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.application.configuration import (
    SYSTEM_DEFAULTS,
    _is_secret_key,
    resolve_config,
    snapshot_policy_refs,
)
from app.control_plane import repository as cp_repo
from app.control_plane.models import OrganizationDraft, WorkspaceDraft
from app.core.database import open_db


def _uid() -> str:
    return str(uuid.uuid4())


NOW = datetime.now(UTC).isoformat()


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
    ws = cp_repo.create_workspace(
        conn,
        WorkspaceDraft(
            id=_uid(), name="Test WS", slug="test-ws",
            actor="cli", organization_id=org.id,
        ),
    )
    return ws


class TestSystemDefaults:
    def test_defaults_are_dict(self):
        assert isinstance(SYSTEM_DEFAULTS, dict)

    def test_default_automation_level_is_manual(self):
        assert SYSTEM_DEFAULTS["default_automation_level"] == "manual"

    def test_all_required_keys_present(self):
        required = {
            "max_concurrent_operations", "pipeline_timeout_seconds",
            "retry_max_attempts", "default_automation_level",
        }
        assert required <= set(SYSTEM_DEFAULTS)


class TestResolveConfig:
    def test_returns_dict(self, conn, workspace):
        cfg = resolve_config(conn, workspace.id)
        assert isinstance(cfg, dict)

    def test_includes_system_defaults(self, conn, workspace):
        cfg = resolve_config(conn, workspace.id)
        assert "max_concurrent_operations" in cfg

    def test_effective_automation_level_present(self, conn, workspace):
        cfg = resolve_config(conn, workspace.id)
        assert "effective_automation_level" in cfg

    def test_manual_level_when_no_policy(self, conn, workspace):
        cfg = resolve_config(conn, workspace.id)
        assert cfg["effective_automation_level"] == "manual"

    def test_workspace_status_present(self, conn, workspace):
        cfg = resolve_config(conn, workspace.id)
        assert cfg["workspace_status"] == "active"

    def test_workspace_not_paused_by_default(self, conn, workspace):
        cfg = resolve_config(conn, workspace.id)
        assert cfg["workspace_paused"] is False

    def test_runtime_overrides_applied(self, conn, workspace):
        cfg = resolve_config(conn, workspace.id, overrides={"custom_key": "custom_value"})
        assert cfg["custom_key"] == "custom_value"

    def test_overrides_win_over_defaults(self, conn, workspace):
        cfg = resolve_config(
            conn, workspace.id,
            overrides={"max_concurrent_operations": 99},
        )
        assert cfg["max_concurrent_operations"] == 99

    def test_secrets_filtered_from_overrides(self, conn, workspace):
        cfg = resolve_config(
            conn, workspace.id,
            overrides={"api_key": "SECRET", "safe_key": "ok"},
        )
        assert "api_key" not in cfg
        assert cfg["safe_key"] == "ok"

    def test_missing_workspace_does_not_crash(self, conn):
        cfg = resolve_config(conn, "nonexistent-ws-id")
        assert isinstance(cfg, dict)


class TestSnapshotPolicyRefs:
    def test_returns_dict_with_ids(self, conn, workspace):
        snap = snapshot_policy_refs(conn, workspace.id)
        assert snap["workspace_id"] == workspace.id

    def test_channel_id_present_when_given(self, conn, workspace):
        snap = snapshot_policy_refs(conn, workspace.id, channel_id="ch-1")
        assert snap["channel_id"] == "ch-1"

    def test_includes_automation_level(self, conn, workspace):
        snap = snapshot_policy_refs(conn, workspace.id)
        assert "effective_automation_level" in snap

    def test_no_secrets_in_snapshot(self, conn, workspace):
        snap = snapshot_policy_refs(conn, workspace.id)
        for key in snap:
            assert not _is_secret_key(key), f"Secret key in snapshot: {key}"


class TestSecretKeyDetection:
    def test_api_key_is_secret(self):
        assert _is_secret_key("api_key") is True

    def test_token_is_secret(self):
        assert _is_secret_key("access_token") is True

    def test_password_is_secret(self):
        assert _is_secret_key("db_password") is True

    def test_credential_is_secret(self):
        assert _is_secret_key("credential_ref") is True

    def test_workspace_id_is_not_secret(self):
        assert _is_secret_key("workspace_id") is False

    def test_max_concurrent_is_not_secret(self):
        assert _is_secret_key("max_concurrent_operations") is False

    def test_timeout_is_not_secret(self):
        assert _is_secret_key("pipeline_timeout_seconds") is False
