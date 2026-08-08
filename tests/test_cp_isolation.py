"""Phase 12 isolation and completeness tests.

Covers:
- Multi-account same-platform isolation (Section 4)
- Cross-workspace event/budget/health leakage (Sections 7, 16)
- Pause/resume channel isolation (Section 16)
- Event idempotency (Section 7)
- Concurrency enforcement (Section 18)
- Control-center status aggregation (Section 20)
- Audit timeline (Section 21)
- Organization / PublishingProfile / AnalyticsIdentity CRUD (Section 3)
"""

from __future__ import annotations

import uuid

import pytest

from app.control_plane import repository as repo
from app.control_plane.concurrency import (
    ConcurrencyLimitExceededError,
    check_concurrency_limit,
    count_active_operations,
)
from app.control_plane.events import emit_event
from app.control_plane.identity import create_channel, create_workspace
from app.control_plane.models import (
    AnalyticsIdentityDraft,
    OrganizationDraft,
    PublishingProfileDraft,
    WorkspaceDraft,
)
from app.control_plane.services import (
    workspace_audit_timeline,
    workspace_control_center_status,
)
from app.core.database import open_db

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _db(tmp_path, name="test.db"):
    return open_db(tmp_path / name)


def _uid():
    return str(uuid.uuid4())


NOW = "2026-08-07T10:00:00"


def _platform(conn, key="youtube"):
    plat_id = f"plat-{key}"
    conn.execute(
        "INSERT OR IGNORE INTO cp_platforms "
        "(id, platform_key, display_name, created_at) VALUES (?,?,?,?)",
        (plat_id, key, key.title(), NOW),
    )
    conn.commit()
    return plat_id


def _credential(conn, workspace_id, suffix="main"):
    cred_id = f"cred-{suffix}"
    conn.execute(
        "INSERT OR IGNORE INTO cp_credential_profiles "
        "(id, workspace_id, display_name, credential_type, status, "
        "external_ref, actor, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (cred_id, workspace_id, f"Cred {suffix}", "oauth2", "active",
         f"vault://secret/{suffix}", "cli", NOW, NOW),
    )
    conn.commit()
    return cred_id


def _platform_account(conn, channel_id, cred_id, platform_id, platform_key="youtube", suffix="1"):
    acc_id = f"acc-{suffix}"
    conn.execute(
        "INSERT OR IGNORE INTO cp_platform_accounts "
        "(id, channel_id, platform_id, platform_key, external_account_id, display_name, "
        "credential_profile_id, actor, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (acc_id, channel_id, platform_id, platform_key, f"ext-{suffix}",
         f"Handle {suffix}", cred_id, "cli", NOW, NOW),
    )
    conn.commit()
    return acc_id


def _pending_op(conn, workspace_id, idem_key, channel_id=None, account_id=None):
    op_id = f"op-{idem_key}"
    conn.execute(
        "INSERT INTO cp_operation_executions "
        "(id, operation_type, workspace_id, channel_id, platform_account_id, idempotency_key, "
        "status, actor, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (op_id, "publish", workspace_id, channel_id, account_id,
         idem_key, "pending", "cli", NOW, NOW),
    )
    conn.commit()
    return op_id


# ──────────────────────────────────────────────────────────────────────────────
# Organization CRUD
# ──────────────────────────────────────────────────────────────────────────────

class TestOrganizationCRUD:
    def test_create_and_get_organization(self, tmp_path):
        conn = _db(tmp_path)
        draft = OrganizationDraft(id=_uid(), name="Acme Inc", slug="acme-inc", actor="cli")
        org = repo.create_organization(conn, draft)
        assert org.id == draft.id
        assert org.name == "Acme Inc"
        assert org.slug == "acme-inc"

        fetched = repo.get_organization(conn, org.id)
        assert fetched.id == org.id

    def test_organization_slug_unique(self, tmp_path):
        import sqlite3
        conn = _db(tmp_path)
        repo.create_organization(
            conn, OrganizationDraft(id=_uid(), name="A", slug="same-slug", actor="cli")
        )
        with pytest.raises(sqlite3.IntegrityError):
            repo.create_organization(
                conn, OrganizationDraft(id=_uid(), name="B", slug="same-slug", actor="cli")
            )

    def test_list_organizations(self, tmp_path):
        conn = _db(tmp_path)
        repo.create_organization(
            conn, OrganizationDraft(id=_uid(), name="Org1", slug="org1", actor="cli")
        )
        repo.create_organization(
            conn, OrganizationDraft(id=_uid(), name="Org2", slug="org2", actor="cli")
        )
        orgs = repo.list_organizations(conn)
        assert len(orgs) == 2

    def test_workspace_linked_to_organization(self, tmp_path):
        conn = _db(tmp_path)
        org = repo.create_organization(
            conn, OrganizationDraft(id=_uid(), name="Corp", slug="corp", actor="cli")
        )
        ws = repo.create_workspace(conn, WorkspaceDraft(
            id=_uid(), name="BrandWS", slug="brand-ws", actor="cli", organization_id=org.id
        ))
        fetched = repo.get_workspace(conn, ws.id)
        assert fetched.organization_id == org.id


# ──────────────────────────────────────────────────────────────────────────────
# PublishingProfile CRUD
# ──────────────────────────────────────────────────────────────────────────────

class TestPublishingProfileCRUD:
    def test_create_and_get_publishing_profile(self, tmp_path):
        conn = _db(tmp_path)
        ws = create_workspace(conn, name="WS", slug="ws-pub", actor="cli")
        plat_id = _platform(conn)
        cred_id = _credential(conn, ws.id, suffix="p1")
        ch = create_channel(conn, workspace_id=ws.id, name="Ch", slug="ch-pub", actor="cli")
        acc_id = _platform_account(conn, ch.id, cred_id, plat_id, suffix="pub1")

        draft = PublishingProfileDraft(
            id=_uid(), platform_account_id=acc_id,
            config={"default_title": "My Show"}, actor="cli"
        )
        profile = repo.create_publishing_profile(conn, draft)
        assert profile.id == draft.id
        assert profile.platform_account_id == acc_id
        assert profile.config["default_title"] == "My Show"

    def test_get_active_publishing_profile(self, tmp_path):
        conn = _db(tmp_path)
        ws = create_workspace(conn, name="WS2", slug="ws-pub2", actor="cli")
        plat_id = _platform(conn)
        cred_id = _credential(conn, ws.id, suffix="p2")
        ch = create_channel(conn, workspace_id=ws.id, name="Ch2", slug="ch-pub2", actor="cli")
        acc_id = _platform_account(conn, ch.id, cred_id, plat_id, suffix="pub2")

        repo.create_publishing_profile(conn, PublishingProfileDraft(
            id=_uid(), platform_account_id=acc_id, config={}, actor="cli"
        ))
        active = repo.get_active_publishing_profile(conn, acc_id)
        assert active is not None
        assert active.is_active


# ──────────────────────────────────────────────────────────────────────────────
# AnalyticsIdentity CRUD
# ──────────────────────────────────────────────────────────────────────────────

class TestAnalyticsIdentityCRUD:
    def test_create_analytics_identity(self, tmp_path):
        conn = _db(tmp_path)
        ws = create_workspace(conn, name="WS", slug="ws-ai", actor="cli")
        plat_id = _platform(conn)
        cred_id = _credential(conn, ws.id, suffix="ai1")
        ch = create_channel(conn, workspace_id=ws.id, name="Ch", slug="ch-ai", actor="cli")
        acc_id = _platform_account(conn, ch.id, cred_id, plat_id, suffix="ai1")

        draft = AnalyticsIdentityDraft(
            id=_uid(), platform_account_id=acc_id,
            analytics_provider_key="ga4", analytics_account_id="UA-12345",
        )
        identity = repo.create_analytics_identity(conn, draft)
        assert identity.id == draft.id
        assert identity.analytics_provider_key == "ga4"
        assert identity.analytics_account_id == "UA-12345"

    def test_analytics_identity_unique_per_provider(self, tmp_path):
        import sqlite3
        conn = _db(tmp_path)
        ws = create_workspace(conn, name="WS", slug="ws-ai2", actor="cli")
        plat_id = _platform(conn)
        cred_id = _credential(conn, ws.id, suffix="ai2")
        ch = create_channel(conn, workspace_id=ws.id, name="Ch", slug="ch-ai2", actor="cli")
        acc_id = _platform_account(conn, ch.id, cred_id, plat_id, suffix="ai2")

        repo.create_analytics_identity(conn, AnalyticsIdentityDraft(
            id=_uid(), platform_account_id=acc_id,
            analytics_provider_key="ga4", analytics_account_id="UA-111"
        ))
        with pytest.raises(sqlite3.IntegrityError):
            repo.create_analytics_identity(conn, AnalyticsIdentityDraft(
                id=_uid(), platform_account_id=acc_id,
                analytics_provider_key="ga4", analytics_account_id="UA-222"
            ))

    def test_list_analytics_identities(self, tmp_path):
        conn = _db(tmp_path)
        ws = create_workspace(conn, name="WS", slug="ws-ai3", actor="cli")
        plat_id = _platform(conn)
        cred_id = _credential(conn, ws.id, suffix="ai3")
        ch = create_channel(conn, workspace_id=ws.id, name="Ch", slug="ch-ai3", actor="cli")
        acc_id = _platform_account(conn, ch.id, cred_id, plat_id, suffix="ai3")

        repo.create_analytics_identity(conn, AnalyticsIdentityDraft(
            id=_uid(), platform_account_id=acc_id,
            analytics_provider_key="ga4", analytics_account_id="UA-1"
        ))
        repo.create_analytics_identity(conn, AnalyticsIdentityDraft(
            id=_uid(), platform_account_id=acc_id,
            analytics_provider_key="mixpanel", analytics_account_id="MP-1"
        ))
        identities = repo.list_analytics_identities(conn, acc_id)
        assert len(identities) == 2


# ──────────────────────────────────────────────────────────────────────────────
# Multi-account same-platform isolation (Section 4)
# ──────────────────────────────────────────────────────────────────────────────

class TestMultiAccountIsolation:
    def test_two_accounts_same_platform_independent(self, tmp_path):
        """Two platform accounts on the same platform in one workspace are independent entities."""
        conn = _db(tmp_path)
        ws = create_workspace(conn, name="Brand", slug="brand", actor="cli")
        plat_id = _platform(conn)
        cred_a = _credential(conn, ws.id, suffix="ca")
        cred_b = _credential(conn, ws.id, suffix="cb")
        ch = create_channel(conn, workspace_id=ws.id, name="MultiCh", slug="multi-ch", actor="cli")

        acc_a = _platform_account(conn, ch.id, cred_a, plat_id, suffix="ma")
        acc_b = _platform_account(conn, ch.id, cred_b, plat_id, suffix="mb")

        assert acc_a != acc_b
        accounts = repo.list_platform_accounts_by_channel(conn, ch.id)
        assert len(accounts) == 2
        ids = {a.id for a in accounts}
        assert acc_a in ids
        assert acc_b in ids

    def test_cross_workspace_accounts_not_shared(self, tmp_path):
        """Accounts from workspace A don't appear under workspace B's channels."""
        conn = _db(tmp_path)
        ws_a = create_workspace(conn, name="BrandA", slug="brand-a", actor="cli")
        ws_b = create_workspace(conn, name="BrandB", slug="brand-b", actor="cli")
        plat_id = _platform(conn)

        cred_a = _credential(conn, ws_a.id, suffix="xa")
        ch_a = create_channel(conn, workspace_id=ws_a.id, name="ChA", slug="ch-a", actor="cli")
        _platform_account(conn, ch_a.id, cred_a, plat_id, suffix="xa")

        ch_b = create_channel(conn, workspace_id=ws_b.id, name="ChB", slug="ch-b", actor="cli")
        accounts_b = repo.list_platform_accounts_by_channel(conn, ch_b.id)
        assert len(accounts_b) == 0


# ──────────────────────────────────────────────────────────────────────────────
# Event isolation — no cross-workspace leakage (Section 7)
# ──────────────────────────────────────────────────────────────────────────────

class TestEventIsolation:
    def test_events_scoped_to_workspace(self, tmp_path):
        conn = _db(tmp_path)
        ws_a = create_workspace(conn, name="WA", slug="wa", actor="cli")
        ws_b = create_workspace(conn, name="WB", slug="wb", actor="cli")

        emit_event(conn, event_type="workspace.created", workspace_id=ws_a.id, actor="cli")
        emit_event(conn, event_type="workspace.created", workspace_id=ws_b.id, actor="cli")

        events_a = repo.list_events_by_workspace(conn, ws_a.id)
        events_b = repo.list_events_by_workspace(conn, ws_b.id)
        assert all(e.workspace_id == ws_a.id for e in events_a)
        assert all(e.workspace_id == ws_b.id for e in events_b)
        assert len(events_a) == 1
        assert len(events_b) == 1

    def test_event_scope_fields_preserved(self, tmp_path):
        conn = _db(tmp_path)
        ws = create_workspace(conn, name="WS", slug="ws-ev", actor="cli")
        ch = create_channel(conn, workspace_id=ws.id, name="Ch", slug="ch-ev", actor="cli")

        emit_event(
            conn,
            event_type="channel.paused",
            workspace_id=ws.id,
            actor="scheduler",
            channel_id=ch.id,
            source_engine="orchestration",
            source_entity_id=ch.id,
        )
        fetched = repo.list_events_by_workspace(conn, ws.id)[0]
        assert fetched.channel_id == ch.id
        assert fetched.source_engine == "orchestration"
        assert fetched.source_entity_id == ch.id

    def test_event_handler_idempotency_unique(self, tmp_path):
        """Dead-letter: same (event_id, handler_key) pair is rejected."""
        import sqlite3
        conn = _db(tmp_path)
        ws = create_workspace(conn, name="WS", slug="ws-idem", actor="cli")
        ev = emit_event(conn, event_type="workspace.created", workspace_id=ws.id, actor="cli")
        conn.execute(
            "INSERT INTO cp_event_processing (id, event_id, handler_key, status, created_at) "
            "VALUES (?,?,?,?,?)",
            ("ep-1", ev.id, "handler.notify", "pending", NOW),
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO cp_event_processing (id, event_id, handler_key, status, created_at) "
                "VALUES (?,?,?,?,?)",
                ("ep-2", ev.id, "handler.notify", "pending", NOW),
            )
            conn.commit()


# ──────────────────────────────────────────────────────────────────────────────
# Channel pause isolation (Section 16)
# ──────────────────────────────────────────────────────────────────────────────

class TestChannelPauseIsolation:
    def test_pausing_channel_a_does_not_affect_channel_b(self, tmp_path):
        conn = _db(tmp_path)
        ws = create_workspace(conn, name="WS", slug="ws-pause", actor="cli")
        ch_a = create_channel(conn, workspace_id=ws.id, name="ChA", slug="ch-pause-a", actor="cli")
        ch_b = create_channel(conn, workspace_id=ws.id, name="ChB", slug="ch-pause-b", actor="cli")

        conn.execute(
            "UPDATE cp_channels SET status = 'paused', updated_at = ? WHERE id = ?",
            (NOW, ch_a.id),
        )
        conn.commit()

        channels = repo.list_channels_by_workspace(conn, ws.id)
        status_map = {ch.id: ch.status for ch in channels}
        assert status_map[ch_a.id] == "paused"
        assert status_map[ch_b.id] == "active"

    def test_budget_per_workspace_not_leaked(self, tmp_path):
        """Budget policy rows are workspace-scoped; ws_b's budget is independent of ws_a."""
        conn = _db(tmp_path)
        ws_a = create_workspace(conn, name="WA", slug="wa-budget", actor="cli")
        ws_b = create_workspace(conn, name="WB", slug="wb-budget", actor="cli")
        conn.execute(
            "INSERT INTO cp_budget_policies "
            "(id, scope, scope_id, period, limit_usd, "
            "warning_threshold, on_exceed_action, actor, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ("bp-a", "workspace", ws_a.id, "monthly", 500.0, 0.8, "block", "cli", NOW),
        )
        conn.commit()
        ws_b_budgets = conn.execute(
            "SELECT id FROM cp_budget_policies WHERE scope_id = ?", (ws_b.id,)
        ).fetchall()
        assert len(ws_b_budgets) == 0


# ──────────────────────────────────────────────────────────────────────────────
# Concurrency enforcement (Section 18)
# ──────────────────────────────────────────────────────────────────────────────

class TestConcurrencyEnforcement:
    def test_count_active_operations_zero_when_empty(self, tmp_path):
        conn = _db(tmp_path)
        ws = create_workspace(conn, name="WS", slug="ws-conc0", actor="cli")
        assert count_active_operations(conn, ws.id) == 0

    def test_count_active_operations_counts_pending_and_running(self, tmp_path):
        conn = _db(tmp_path)
        ws = create_workspace(conn, name="WS", slug="ws-conc1", actor="cli")
        _pending_op(conn, ws.id, "k1")
        _pending_op(conn, ws.id, "k2")
        conn.execute(
            "UPDATE cp_operation_executions SET status = 'running' WHERE idempotency_key = 'k2'"
        )
        conn.commit()
        assert count_active_operations(conn, ws.id) == 2

    def test_count_completed_not_included(self, tmp_path):
        conn = _db(tmp_path)
        ws = create_workspace(conn, name="WS", slug="ws-conc2", actor="cli")
        _pending_op(conn, ws.id, "done-k")
        conn.execute(
            "UPDATE cp_operation_executions "
            "SET status = 'completed' WHERE idempotency_key = 'done-k'"
        )
        conn.commit()
        assert count_active_operations(conn, ws.id) == 0

    def test_check_concurrency_limit_passes_under_limit(self, tmp_path):
        conn = _db(tmp_path)
        ws = create_workspace(conn, name="WS", slug="ws-conc3", actor="cli")
        _pending_op(conn, ws.id, "c1")
        check_concurrency_limit(conn, ws.id, limit=5)  # 1 < 5, must not raise

    def test_check_concurrency_limit_raises_at_limit(self, tmp_path):
        conn = _db(tmp_path)
        ws = create_workspace(conn, name="WS", slug="ws-conc4", actor="cli")
        for i in range(3):
            _pending_op(conn, ws.id, f"lk{i}")
        with pytest.raises(ConcurrencyLimitExceededError) as exc_info:
            check_concurrency_limit(conn, ws.id, limit=3)
        err = exc_info.value
        assert err.current == 3
        assert err.limit == 3

    def test_concurrency_scoped_by_channel(self, tmp_path):
        conn = _db(tmp_path)
        ws = create_workspace(conn, name="WS", slug="ws-conc5", actor="cli")
        ch_a = create_channel(conn, workspace_id=ws.id, name="A", slug="ch-ca", actor="cli")
        ch_b = create_channel(conn, workspace_id=ws.id, name="B", slug="ch-cb", actor="cli")
        _pending_op(conn, ws.id, "ck1", channel_id=ch_a.id)
        _pending_op(conn, ws.id, "ck2", channel_id=ch_a.id)

        assert count_active_operations(conn, ws.id, channel_id=ch_a.id) == 2
        assert count_active_operations(conn, ws.id, channel_id=ch_b.id) == 0

    def test_concurrency_limit_exceeded_error_attributes(self, tmp_path):
        conn = _db(tmp_path)
        ws = create_workspace(conn, name="WS", slug="ws-conc6", actor="cli")
        ch = create_channel(conn, workspace_id=ws.id, name="Ch", slug="ch-conc6", actor="cli")
        _pending_op(conn, ws.id, "attr-k1", channel_id=ch.id)
        with pytest.raises(ConcurrencyLimitExceededError) as exc_info:
            check_concurrency_limit(conn, ws.id, limit=1, channel_id=ch.id)
        err = exc_info.value
        assert err.scope == "channel"
        assert err.scope_id == ch.id


# ──────────────────────────────────────────────────────────────────────────────
# Control-center status (Section 20)
# ──────────────────────────────────────────────────────────────────────────────

class TestControlCenterStatus:
    def test_empty_workspace_returns_zero_counts(self, tmp_path):
        conn = _db(tmp_path)
        ws = create_workspace(conn, name="WS", slug="ws-cc0", actor="cli")
        status = workspace_control_center_status(conn, ws.id)
        assert status["workspace_id"] == ws.id
        assert status["channel_count"] == 0
        assert status["in_progress_operations"] == []
        assert status["failed_operations"] == []
        assert status["paused_channels"] == []
        assert status["unhealthy_accounts"] == []

    def test_paused_channels_appear_in_status(self, tmp_path):
        conn = _db(tmp_path)
        ws = create_workspace(conn, name="WS", slug="ws-cc1", actor="cli")
        ch = create_channel(conn, workspace_id=ws.id, name="Ch", slug="ch-cc1", actor="cli")
        conn.execute(
            "UPDATE cp_channels SET status = 'paused', updated_at = ? WHERE id = ?", (NOW, ch.id)
        )
        conn.commit()
        status = workspace_control_center_status(conn, ws.id)
        assert len(status["paused_channels"]) == 1
        assert status["paused_channels"][0]["channel_id"] == ch.id

    def test_in_progress_ops_appear_in_status(self, tmp_path):
        conn = _db(tmp_path)
        ws = create_workspace(conn, name="WS", slug="ws-cc2", actor="cli")
        _pending_op(conn, ws.id, "cc-op-1")
        status = workspace_control_center_status(conn, ws.id)
        assert len(status["in_progress_operations"]) == 1

    def test_active_experiments_appear_in_status(self, tmp_path):
        conn = _db(tmp_path)
        ws = create_workspace(conn, name="WS", slug="ws-cc3", actor="cli")
        ch = create_channel(conn, workspace_id=ws.id, name="Ch", slug="ch-cc3", actor="cli")
        conn.execute(
            "INSERT INTO cp_experiments "
            "(id, workspace_id, channel_id, name, hypothesis, "
            "status, primary_metric, actor, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("exp-1", ws.id, ch.id, "CTR Test", "H", "active", "ctr", "cli", NOW, NOW),
        )
        conn.commit()
        status = workspace_control_center_status(conn, ws.id)
        assert len(status["active_experiments"]) == 1
        assert status["active_experiments"][0]["id"] == "exp-1"


# ──────────────────────────────────────────────────────────────────────────────
# Audit timeline (Section 21)
# ──────────────────────────────────────────────────────────────────────────────

class TestAuditTimeline:
    def test_empty_workspace_returns_empty_timeline(self, tmp_path):
        conn = _db(tmp_path)
        ws = create_workspace(conn, name="WS", slug="ws-tl0", actor="cli")
        assert workspace_audit_timeline(conn, ws.id) == []

    def test_timeline_contains_events_and_operations(self, tmp_path):
        conn = _db(tmp_path)
        ws = create_workspace(conn, name="WS", slug="ws-tl1", actor="cli")
        emit_event(conn, event_type="workspace.created", workspace_id=ws.id, actor="cli")
        _pending_op(conn, ws.id, "tl-op-1")
        tl = workspace_audit_timeline(conn, ws.id)
        kinds = {item["kind"] for item in tl}
        assert "event" in kinds
        assert "operation" in kinds

    def test_timeline_sorted_newest_first(self, tmp_path):
        conn = _db(tmp_path)
        ws = create_workspace(conn, name="WS", slug="ws-tl2", actor="cli")
        emit_event(conn, event_type="workspace.created", workspace_id=ws.id, actor="cli")
        emit_event(conn, event_type="channel.paused", workspace_id=ws.id, actor="cli")
        tl = workspace_audit_timeline(conn, ws.id)
        timestamps = [item["ts"] for item in tl]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_timeline_respects_limit(self, tmp_path):
        conn = _db(tmp_path)
        ws = create_workspace(conn, name="WS", slug="ws-tl3", actor="cli")
        for _ in range(10):
            emit_event(conn, event_type="workspace.created", workspace_id=ws.id, actor="cli")
        tl = workspace_audit_timeline(conn, ws.id, limit=5)
        assert len(tl) <= 5

    def test_timeline_scoped_to_workspace(self, tmp_path):
        conn = _db(tmp_path)
        ws_a = create_workspace(conn, name="WA", slug="wa-tl", actor="cli")
        ws_b = create_workspace(conn, name="WB", slug="wb-tl", actor="cli")
        emit_event(conn, event_type="workspace.created", workspace_id=ws_a.id, actor="cli")
        emit_event(conn, event_type="workspace.created", workspace_id=ws_b.id, actor="cli")
        tl_a = workspace_audit_timeline(conn, ws_a.id)
        # All events in ws_a's timeline belong to ws_a (verified via operation scope)
        assert len(tl_a) == 1
        assert tl_a[0]["kind"] == "event"
