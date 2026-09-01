"""Phase 16D.4 — Platform account health recovery after analytics observation.

Tests that _maybe_restore_account():
  - Restores credential_invalid/credential_expiring/quota_limited → connected
    after a successful (including no_data) observation.
  - Does NOT overwrite disconnected or paused (operator-intent) states.
  - Does NOT fire on failed observation.
  - Emits platform_account.resumed event.
  - Records a healthy health record.
  - After recovery, get_health() shows the account as healthy (latest-per-entity).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.analytics.auto_observer import _maybe_restore_account
from app.control_plane import repository as repo
from app.control_plane.constants import (
    ACCOUNT_STATUS_CONNECTED,
    EVENT_ACCOUNT_RESUMED,
)
from app.control_plane.health import get_health, record_health
from app.core.database import open_db

_NOW = "2026-08-25T00:00:00"


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_db(tmp_path: Path):
    return open_db(tmp_path / "db.sqlite")


def _seed_workspace_channel_account(conn, *, account_id="acct-1", status="connected"):
    """Seed the minimum cp rows for a platform account."""
    conn.execute("PRAGMA foreign_keys = OFF")
    for sql, params in [
        (
            "INSERT OR IGNORE INTO cp_workspaces (id,name,slug,actor,created_at,updated_at)"
            " VALUES ('ws-1','W','ws-1','s',?,?)",
            (_NOW, _NOW),
        ),
        (
            "INSERT OR IGNORE INTO cp_channels"
            " (id,workspace_id,name,slug,actor,status,created_at,updated_at)"
            " VALUES ('ch-1','ws-1','C','c','s','active',?,?)",
            (_NOW, _NOW),
        ),
        (
            "INSERT OR IGNORE INTO cp_platforms"
            " (id,platform_key,display_name,created_at)"
            " VALUES ('plat-1','youtube','YouTube',?)",
            (_NOW,),
        ),
        (
            "INSERT OR IGNORE INTO cp_platform_accounts"
            " (id,channel_id,platform_id,platform_key,external_account_id,"
            "  display_name,status,actor,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                account_id,
                "ch-1",
                "plat-1",
                "youtube",
                "ext-1",
                "Test Account",
                status,
                "system",
                _NOW,
                _NOW,
            ),
        ),
    ]:
        conn.execute(sql, params)
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    return account_id


# ── 1. Recoverable statuses are restored ──────────────────────────────────────


@pytest.mark.parametrize(
    "degraded_status",
    [
        "credential_invalid",
        "credential_expiring",
        "quota_limited",
    ],
)
def test_maybe_restore_account_recovers_degraded_status(tmp_path, degraded_status):
    """_maybe_restore_account restores recoverable statuses to connected."""
    conn = _make_db(tmp_path)
    _seed_workspace_channel_account(conn, account_id="acct-1", status=degraded_status)

    _maybe_restore_account(
        conn,
        platform_account_id="acct-1",
        workspace_id="ws-1",
        publication_id=3,
    )

    acct = repo.get_platform_account(conn, "acct-1")
    assert acct.status == ACCOUNT_STATUS_CONNECTED
    conn.close()


def test_maybe_restore_account_records_healthy_health_record(tmp_path):
    """After recovery, a 'healthy' health record is written."""
    conn = _make_db(tmp_path)
    _seed_workspace_channel_account(conn, account_id="acct-1", status="credential_invalid")

    # Pre-existing degraded health record.
    record_health(
        conn,
        entity_type="platform_account",
        entity_id="acct-1",
        status="degraded",
        recorded_by="system:token_refresh",
        detail="Token refresh failed",
    )
    conn.commit()

    _maybe_restore_account(
        conn,
        platform_account_id="acct-1",
        workspace_id="ws-1",
        publication_id=3,
    )

    hr = get_health(conn, "platform_account", "acct-1")
    assert hr is not None
    assert hr.status == "healthy"
    conn.close()


def test_maybe_restore_account_emits_account_resumed_event(tmp_path):
    """After recovery, platform_account.resumed cp_event is emitted."""
    conn = _make_db(tmp_path)
    _seed_workspace_channel_account(conn, account_id="acct-1", status="credential_invalid")

    _maybe_restore_account(
        conn,
        platform_account_id="acct-1",
        workspace_id="ws-1",
        publication_id=3,
    )

    row = conn.execute(
        "SELECT event_type, payload_json FROM cp_events"
        " WHERE event_type = ? ORDER BY created_at DESC LIMIT 1",
        (EVENT_ACCOUNT_RESUMED,),
    ).fetchone()
    assert row is not None
    payload = json.loads(row["payload_json"])
    assert payload.get("account_id") == "acct-1"
    assert payload.get("reason") == "successful_analytics_observation"
    conn.close()


def test_maybe_restore_latest_health_is_healthy(tmp_path):
    """After recovery, get_health() (latest-per-entity) shows healthy.

    System Health dashboards use get_latest_health_record() per entity, so
    the account appears healthy once a newer 'healthy' record is written.
    list_degraded_entities() returns all historical degraded records and is not
    used for per-entity current status.
    """
    conn = _make_db(tmp_path)
    _seed_workspace_channel_account(conn, account_id="acct-1", status="credential_invalid")

    record_health(
        conn,
        entity_type="platform_account",
        entity_id="acct-1",
        status="degraded",
        recorded_by="system:token_refresh",
        detail="pre-existing",
    )
    conn.commit()

    # Before recovery: latest record is degraded.
    before = get_health(conn, "platform_account", "acct-1")
    assert before is not None and before.status == "degraded"

    _maybe_restore_account(
        conn,
        platform_account_id="acct-1",
        workspace_id="ws-1",
        publication_id=3,
    )

    # After recovery: latest record is healthy (what System Health reads).
    after = get_health(conn, "platform_account", "acct-1")
    assert after is not None
    assert after.status == "healthy"
    conn.close()


# ── 2. no_data counts as successful recovery ──────────────────────────────────


def test_no_data_observation_still_restores_account(tmp_path):
    """no_data observations are successful — they must restore degraded accounts."""
    conn = _make_db(tmp_path)
    _seed_workspace_channel_account(conn, account_id="acct-1", status="credential_invalid")

    # _maybe_restore_account is called regardless of no_data vs new_data.
    _maybe_restore_account(
        conn,
        platform_account_id="acct-1",
        workspace_id="ws-1",
        publication_id=3,
    )

    acct = repo.get_platform_account(conn, "acct-1")
    assert acct.status == ACCOUNT_STATUS_CONNECTED
    conn.close()


# ── 3. Non-recoverable statuses are never overwritten ─────────────────────────


@pytest.mark.parametrize("safe_status", ["disconnected", "paused"])
def test_maybe_restore_does_not_overwrite_operator_intent(tmp_path, safe_status):
    """disconnected and paused are operator-set and must not be overwritten."""
    conn = _make_db(tmp_path)
    _seed_workspace_channel_account(conn, account_id="acct-1", status=safe_status)

    _maybe_restore_account(
        conn,
        platform_account_id="acct-1",
        workspace_id="ws-1",
        publication_id=3,
    )

    acct = repo.get_platform_account(conn, "acct-1")
    assert acct.status == safe_status  # unchanged
    conn.close()


def test_maybe_restore_is_noop_when_already_connected(tmp_path):
    """A connected account with no stale health record emits no event."""
    conn = _make_db(tmp_path)
    _seed_workspace_channel_account(conn, account_id="acct-1", status="connected")

    before_count = conn.execute("SELECT COUNT(*) FROM cp_events").fetchone()[0]

    _maybe_restore_account(
        conn,
        platform_account_id="acct-1",
        workspace_id="ws-1",
        publication_id=3,
    )

    after_count = conn.execute("SELECT COUNT(*) FROM cp_events").fetchone()[0]
    assert after_count == before_count  # no EVENT_ACCOUNT_RESUMED emitted
    # account status untouched
    acct = repo.get_platform_account(conn, "acct-1")
    assert acct.status == ACCOUNT_STATUS_CONNECTED
    conn.close()


def test_connected_account_with_stale_degraded_health_gets_healed(tmp_path):
    """Live bug: account.status=connected but health record still degraded.

    OAuth reconnection can restore account.status to 'connected' before the
    observer runs.  The observer then finds acct.status='connected', skips
    Pass 1, but MUST still heal the stale degraded health record in Pass 2.
    Without this fix, System Health kept reporting the account as degraded
    even though it was connected and analytics succeeded.
    """
    conn = _make_db(tmp_path)
    # Account already reconnected — status is 'connected'.
    _seed_workspace_channel_account(conn, account_id="acct-1", status="connected")

    # Stale degraded health record from the earlier token-refresh failure.
    record_health(
        conn,
        entity_type="platform_account",
        entity_id="acct-1",
        status="degraded",
        recorded_by="system:token_refresh",
        detail="Token refresh failed; account needs reconnection.",
    )
    conn.commit()

    # Sanity: System Health currently sees degraded.
    assert get_health(conn, "platform_account", "acct-1").status == "degraded"

    _maybe_restore_account(
        conn,
        platform_account_id="acct-1",
        workspace_id="ws-1",
        publication_id=3,
    )

    # After observation: account still connected (untouched by Pass 1).
    acct = repo.get_platform_account(conn, "acct-1")
    assert acct.status == ACCOUNT_STATUS_CONNECTED

    # Pass 2 must have written a healthy health record.
    hr = get_health(conn, "platform_account", "acct-1")
    assert hr is not None
    assert hr.status == "healthy"  # System Health now reports healthy

    # No spurious EVENT_ACCOUNT_RESUMED — account was already connected.
    resumed_count = conn.execute(
        "SELECT COUNT(*) FROM cp_events WHERE event_type = ?",
        (EVENT_ACCOUNT_RESUMED,),
    ).fetchone()[0]
    assert resumed_count == 0

    conn.close()


def test_maybe_restore_is_noop_when_account_id_is_none(tmp_path):
    """None platform_account_id is silently skipped."""
    conn = _make_db(tmp_path)
    # Should not raise even with no account seeded.
    _maybe_restore_account(
        conn,
        platform_account_id=None,
        workspace_id="ws-1",
        publication_id=3,
    )
    conn.close()


# ── 4. Failed observation does not restore account ────────────────────────────


def test_failed_observation_does_not_restore_account(tmp_path):
    """run_observation() failure leaves account status unchanged."""
    from app.analytics.auto_observer import run_observation
    from app.analytics.observation import register_publication_for_observation

    conn = _make_db(tmp_path)
    _seed_workspace_channel_account(conn, account_id="acct-1", status="credential_invalid")

    # Seed observation state pointing to the account
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        """INSERT OR IGNORE INTO publishing_plans
           (id,render_manifest_id,scene_manifest_id,production_plan_id,
            script_id,topic_id,narration_run_id,caption_run_id,
            experiment_id,input_hash,publishing_engine_version,metadata_version,
            provider,provider_version,title,created_at,updated_at)
           VALUES (1,1,1,1,1,1,1,1,'exp-1','h1','1.0','1','youtube','1.0','T',?,?)""",
        (_NOW, _NOW),
    )
    conn.execute(
        """INSERT OR IGNORE INTO publishing_jobs
           (id,publishing_plan_id,provider,provider_version,status,created_at,updated_at)
           VALUES (1,1,'youtube','1.0','completed',?,?)""",
        (_NOW, _NOW),
    )
    conn.execute(
        """INSERT OR IGNORE INTO publications
           (id,publishing_plan_id,publishing_job_id,workspace_id,channel_id,platform_account_id,
            provider,provider_version,provider_video_id,visibility,status,published_at,
            publishing_engine_version,input_hash,output_sha256,created_at,updated_at)
           VALUES (3,1,1,'ws-1','ch-1','acct-1','youtube','1.0','vid-3',
                   'public','published',?,'1.0','hp','hs',?,?)""",
        (_NOW, _NOW, _NOW),
    )
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")

    sid = register_publication_for_observation(
        conn,
        publication_id=3,
        workspace_id="ws-1",
        channel_id="ch-1",
        platform_account_id="acct-1",
        published_at=_NOW,
    )

    # Observation fails because OAuth is unavailable.
    with patch(
        "app.analytics.gate.build_authenticated_analytics_provider",
        side_effect=RuntimeError("oauth unavailable"),
    ):
        result = run_observation(conn, publication_id=3, schedule_id=sid, oauth_client=None)

    assert result.error is not None  # failed

    acct = repo.get_platform_account(conn, "acct-1")
    assert acct.status == "credential_invalid"  # NOT restored
    conn.close()


# ── 5. Full observation cycle restores account ────────────────────────────────


def test_successful_observation_with_fake_provider_restores_account(tmp_path):
    """Full run_observation with a fake provider restores a degraded account."""
    from app.analytics.auto_observer import run_observation
    from app.analytics.observation import register_publication_for_observation
    from tests.test_analytics_auto_observer_16d4 import YouTubeFakeProvider

    conn = _make_db(tmp_path)
    _seed_workspace_channel_account(conn, account_id="acct-1", status="credential_invalid")

    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        """INSERT OR IGNORE INTO render_manifests
           (id,scene_manifest_id,narration_run_id,caption_run_id,topic_id,plan_id,script_id,
            input_hash,render_schema_version,compositor_version,status,created_at)
           VALUES (1,1,1,1,1,1,1,'rm-h3','1.0','1.0','approved',?)""",
        (_NOW,),
    )
    conn.execute(
        """INSERT OR IGNORE INTO publishing_plans
           (id,render_manifest_id,scene_manifest_id,production_plan_id,
            script_id,topic_id,narration_run_id,caption_run_id,
            experiment_id,input_hash,publishing_engine_version,metadata_version,
            provider,provider_version,title,created_at,updated_at)
           VALUES (1,1,1,1,1,1,1,1,'exp-1','h2','1.0','1','youtube','1.0','T',?,?)""",
        (_NOW, _NOW),
    )
    conn.execute(
        """INSERT OR IGNORE INTO publishing_jobs
           (id,publishing_plan_id,provider,provider_version,status,created_at,updated_at)
           VALUES (1,1,'youtube','1.0','completed',?,?)""",
        (_NOW, _NOW),
    )
    conn.execute(
        """INSERT OR IGNORE INTO publications
           (id,publishing_plan_id,publishing_job_id,workspace_id,channel_id,platform_account_id,
            provider,provider_version,provider_video_id,visibility,status,published_at,
            publishing_engine_version,input_hash,output_sha256,created_at,updated_at)
           VALUES (3,1,1,'ws-1','ch-1','acct-1','youtube','1.0','vid-3',
                   'public','published',?,'1.0','h3','hs3',?,?)""",
        (_NOW, _NOW, _NOW),
    )
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")

    sid = register_publication_for_observation(
        conn,
        publication_id=3,
        workspace_id="ws-1",
        channel_id="ch-1",
        platform_account_id="acct-1",
        published_at=_NOW,
    )

    result = run_observation(
        conn,
        publication_id=3,
        schedule_id=sid,
        _provider_override=YouTubeFakeProvider(),
    )

    assert result.error is None

    acct = repo.get_platform_account(conn, "acct-1")
    assert acct.status == ACCOUNT_STATUS_CONNECTED  # recovered!
    conn.close()
