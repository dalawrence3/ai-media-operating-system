"""Phase 16D.4 scheduler autonomy tests.

Covers:
  - Startup reconciliation in run_scheduler_daemon (via run_scheduler_tick wrapper)
  - Periodic automatic tick dispatches analytics_observation inline
  - Restart recovery is safe and idempotent (mark_schedule_ran prevents double execution)
  - Duplicate/overlapping tick safety
  - Observer failure does not kill the scheduler loop
  - Publication-3-style already-public recovery without manual commands
  - run_scheduler_tick routes analytics_observation inline, not via RQ
  - OAuth self-build in run_observation when oauth_client=None
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.analytics.observation import (
    get_observation_state,
    reconcile_unobserved_publications,
)
from app.core.database import open_db
from app.workers.scheduler import (
    get_due_schedules,
    mark_schedule_ran,
    run_scheduler_tick,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

_NOW = "2026-08-25T00:00:00"
_PAST = "2026-01-01T00:00:00"


def _seed_public_publication(conn, pub_id: int = 3, workspace_id: str = "ws-1") -> None:
    """Insert a minimal public publication (+ required parents) without FK enforcement."""
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        """INSERT OR IGNORE INTO cp_workspaces
           (id, name, slug, actor, created_at, updated_at)
           VALUES (?, 'Test WS', ?, 'system', ?, ?)""",
        (workspace_id, workspace_id, _NOW, _NOW),
    )
    conn.execute(
        """INSERT OR IGNORE INTO publishing_plans
           (id, render_manifest_id, scene_manifest_id, production_plan_id,
            script_id, topic_id, narration_run_id, caption_run_id,
            experiment_id, input_hash, publishing_engine_version, metadata_version,
            provider, provider_version, title, created_at, updated_at)
           VALUES (?,1,1,1,1,1,1,1,'exp-1',?,?,'1','youtube','1.0','Title',?,?)""",
        (pub_id, f"hash-plan-{pub_id}", "1.0", _NOW, _NOW),
    )
    conn.execute(
        """INSERT OR IGNORE INTO publishing_jobs
           (id, publishing_plan_id, provider, provider_version,
            status, created_at, updated_at)
           VALUES (?, ?, 'youtube', '1.0', 'completed', ?, ?)""",
        (pub_id, pub_id, _NOW, _NOW),
    )
    conn.execute(
        """INSERT OR IGNORE INTO publications
           (id, publishing_plan_id, publishing_job_id,
            workspace_id, channel_id, platform_account_id,
            provider, provider_version, provider_video_id,
            visibility, status, published_at,
            publishing_engine_version, input_hash, output_sha256,
            created_at, updated_at)
           VALUES (?,?,?,?,NULL,'acct-1','youtube','1.0',?,
                   'public','published',?,'1.0',?,?,?,?)""",
        (
            pub_id,
            pub_id,
            pub_id,
            workspace_id,
            f"vid-{pub_id}",
            _NOW,
            f"hash-pub-{pub_id}",
            f"sha-{pub_id}",
            _NOW,
            _NOW,
        ),
    )
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")


def _insert_analytics_obs_schedule(
    conn,
    publication_id: int = 3,
    workspace_id: str = "ws-1",
    next_run_at: str = _PAST,
) -> str:
    """Directly insert an analytics_observation schedule row; returns schedule_id."""
    import uuid

    sid = str(uuid.uuid4())
    cfg = json.dumps({"publication_id": publication_id, "interval_seconds": 3600})
    conn.execute(
        """INSERT INTO app_schedule_definitions
           (id, workspace_id, name, operation_type, schedule_type, schedule_config_json,
            timezone, is_active, next_run_at, actor, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            sid,
            workspace_id,
            f"analytics_observation:pub_{publication_id}",
            "analytics_observation",
            "interval",
            cfg,
            "UTC",
            1,
            next_run_at,
            "system:auto_observer",
            _NOW,
            _NOW,
        ),
    )
    conn.commit()
    return sid


# ── 1. Startup reconciliation ─────────────────────────────────────────────────


def test_startup_reconciliation_adopts_public_publication(tmp_path: Path) -> None:
    """reconcile_unobserved_publications() on startup adopts already-public publications."""
    conn = open_db(tmp_path / "db.sqlite")
    _seed_public_publication(conn, pub_id=3)

    adopted = reconcile_unobserved_publications(conn)
    assert 3 in adopted

    state = get_observation_state(conn, 3)
    assert state is not None
    assert state["observation_status"] == "active"
    conn.close()


def test_startup_reconciliation_is_idempotent(tmp_path: Path) -> None:
    """Calling reconcile twice does not create duplicate schedules."""
    conn = open_db(tmp_path / "db.sqlite")
    _seed_public_publication(conn, pub_id=3)

    first = reconcile_unobserved_publications(conn)
    second = reconcile_unobserved_publications(conn)

    assert 3 in first
    assert 3 not in second  # already registered

    rows = conn.execute(
        "SELECT COUNT(*) AS c FROM app_schedule_definitions "
        "WHERE operation_type='analytics_observation' AND is_active=1"
    ).fetchone()
    assert rows["c"] == 1
    conn.close()


def test_startup_reconciliation_skips_private_publications(tmp_path: Path) -> None:
    """Private publications are not adopted by reconcile."""
    conn = open_db(tmp_path / "db.sqlite")
    _seed_public_publication(conn, pub_id=99)
    conn.execute("UPDATE publications SET visibility='private' WHERE id=99")
    conn.commit()

    adopted = reconcile_unobserved_publications(conn)
    assert 99 not in adopted
    conn.close()


# ── 2. Periodic automatic tick dispatches analytics_observation inline ────────


def test_scheduler_tick_dispatches_analytics_observation_inline(tmp_path: Path) -> None:
    """run_scheduler_tick routes analytics_observation to run_observation, not RQ."""
    conn = open_db(tmp_path / "db.sqlite")
    _seed_public_publication(conn, pub_id=3)
    _insert_analytics_obs_schedule(conn, publication_id=3, next_run_at=_PAST)

    fake_result = MagicMock()
    fake_result.error = None

    with patch("app.analytics.auto_observer.run_observation", return_value=fake_result) as mock_obs:
        job_ids = run_scheduler_tick(conn)

    # run_observation was called inline; no RQ job IDs returned for this type.
    mock_obs.assert_called_once()
    assert job_ids == []
    conn.close()


def test_scheduler_tick_non_analytics_goes_to_rq(tmp_path: Path) -> None:
    """Non-analytics_observation schedules still go through the RQ path."""
    conn = open_db(tmp_path / "db.sqlite")
    try:
        conn.execute(
            """INSERT INTO cp_workspaces
               (id, name, slug, actor, created_at, updated_at)
               VALUES ('ws-1', 'WS', 'ws-1', 'system', ?, ?)""",
            (_NOW, _NOW),
        )
        conn.commit()
    except Exception:
        pass
    conn.execute(
        """INSERT INTO app_schedule_definitions
           (id, workspace_id, name, operation_type, schedule_type, schedule_config_json,
            timezone, is_active, next_run_at, actor, created_at, updated_at)
           VALUES ('sched-pipe','ws-1','Pipe','pipeline_run','interval','{"seconds":3600}',
                   'UTC',1,?,?,?,?)""",
        (_PAST, "system:scheduler", _NOW, _NOW),
    )
    conn.commit()

    mock_queue = MagicMock()
    mock_queue.enqueue.return_value = MagicMock(id="job-rq-1")

    with patch("app.workers.jobs.get_queue", return_value=mock_queue):
        job_ids = run_scheduler_tick(conn, queue=mock_queue)

    assert "job-rq-1" in job_ids
    conn.close()


def test_scheduler_tick_future_schedule_not_dispatched(tmp_path: Path) -> None:
    """Schedules not yet due are not dispatched."""
    conn = open_db(tmp_path / "db.sqlite")
    _seed_public_publication(conn, pub_id=3)
    _insert_analytics_obs_schedule(conn, publication_id=3, next_run_at="2099-01-01T00:00:00")

    with patch("app.analytics.auto_observer.run_observation") as mock_obs:
        job_ids = run_scheduler_tick(conn)

    mock_obs.assert_not_called()
    assert job_ids == []
    conn.close()


# ── 3. Restart recovery is safe and idempotent ────────────────────────────────


def test_mark_schedule_ran_prevents_double_execution(tmp_path: Path) -> None:
    """After mark_schedule_ran advances next_run_at, a second tick sees no due schedules."""
    conn = open_db(tmp_path / "db.sqlite")
    _seed_public_publication(conn, pub_id=3)
    sid = _insert_analytics_obs_schedule(conn, publication_id=3, next_run_at=_PAST)

    # Simulate first tick claiming the schedule.
    mark_schedule_ran(conn, sid, "2099-01-01T00:00:00")

    due = get_due_schedules(conn)
    assert not any(r["id"] == sid for r in due)
    conn.close()


def test_restart_recovery_reconcile_then_tick(tmp_path: Path) -> None:
    """After a restart, reconcile finds orphan pub, tick dispatches observation."""
    conn = open_db(tmp_path / "db.sqlite")
    _seed_public_publication(conn, pub_id=3)

    # Simulate restart: reconcile first
    adopted = reconcile_unobserved_publications(conn)
    assert 3 in adopted

    # Tick runs immediately (next_run_at=NOW on registration)
    fake_result = MagicMock()
    fake_result.error = None
    with patch("app.analytics.auto_observer.run_observation", return_value=fake_result) as mock_obs:
        run_scheduler_tick(conn)

    mock_obs.assert_called_once()
    args = mock_obs.call_args
    assert args.kwargs.get("publication_id") == 3 or args[1].get("publication_id") == 3
    conn.close()


# ── 4. Duplicate / overlapping tick safety ────────────────────────────────────


def test_overlapping_ticks_do_not_double_execute(tmp_path: Path) -> None:
    """Two sequential ticks: first claims schedule, second finds nothing due."""
    conn = open_db(tmp_path / "db.sqlite")
    _seed_public_publication(conn, pub_id=3)
    _insert_analytics_obs_schedule(conn, publication_id=3, next_run_at=_PAST)

    call_count = 0

    def fake_observation(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return MagicMock(error=None)

    with patch("app.analytics.auto_observer.run_observation", side_effect=fake_observation):
        run_scheduler_tick(conn)
        run_scheduler_tick(conn)  # second tick — schedule not yet due again

    assert call_count == 1  # only executed once
    conn.close()


# ── 5. Observer failure does not kill the scheduler loop ──────────────────────


def test_observer_failure_does_not_propagate_to_tick(tmp_path: Path) -> None:
    """run_observation raising unexpectedly does not break run_scheduler_tick."""
    conn = open_db(tmp_path / "db.sqlite")
    _seed_public_publication(conn, pub_id=3)
    _insert_analytics_obs_schedule(conn, publication_id=3, next_run_at=_PAST)

    with patch(
        "app.analytics.auto_observer.run_observation",
        side_effect=RuntimeError("unexpected crash"),
    ):
        # Must not raise.
        result = run_scheduler_tick(conn)

    # Tick returned without crashing; schedule was marked ran.
    assert isinstance(result, list)
    conn.close()


def test_oauth_failure_in_observation_does_not_kill_tick(tmp_path: Path) -> None:
    """OAuth build failure inside run_observation is caught; tick continues."""
    conn = open_db(tmp_path / "db.sqlite")
    _seed_public_publication(conn, pub_id=3)
    _insert_analytics_obs_schedule(conn, publication_id=3, next_run_at=_PAST)

    # run_observation itself is real; let it fail at OAuth build (no creds in test).
    # Since it raises inside its try/except, the result should have error set.
    from app.analytics.auto_observer import run_observation

    with patch("app.analytics.auto_observer.run_observation", wraps=run_observation):
        # Should not raise even if run_observation internally raises.
        result = run_scheduler_tick(conn)

    assert isinstance(result, list)
    conn.close()


def test_second_publication_observed_even_if_first_fails(tmp_path: Path) -> None:
    """Failure for pub 3 does not prevent pub 7 from being observed in same tick."""
    conn = open_db(tmp_path / "db.sqlite")
    _seed_public_publication(conn, pub_id=3)
    _seed_public_publication(conn, pub_id=7, workspace_id="ws-7")
    _insert_analytics_obs_schedule(conn, publication_id=3, next_run_at=_PAST)
    _insert_analytics_obs_schedule(conn, publication_id=7, next_run_at=_PAST)

    observed_pubs: list[int] = []

    def fake_observation(conn, *, publication_id, schedule_id, oauth_client=None):
        observed_pubs.append(publication_id)
        if publication_id == 3:
            raise RuntimeError("pub 3 fails")
        return MagicMock(error=None)

    with patch("app.analytics.auto_observer.run_observation", side_effect=fake_observation):
        run_scheduler_tick(conn)

    assert 7 in observed_pubs  # pub 7 still ran
    conn.close()


# ── 6. Publication-3-style already-public recovery ────────────────────────────


def test_already_public_pub_recovered_without_manual_commands(tmp_path: Path) -> None:
    """Public publication is recovered by reconcile + tick without any manual call."""
    conn = open_db(tmp_path / "db.sqlite")
    _seed_public_publication(conn, pub_id=3)

    # No manual registration — simulating startup after deployment.
    assert get_observation_state(conn, 3) is None

    # Step 1: startup reconcile (done by run_scheduler_daemon on startup)
    reconcile_unobserved_publications(conn)
    state = get_observation_state(conn, 3)
    assert state is not None
    assert state["observation_status"] == "active"

    # Step 2: tick dispatches observation
    call_args: list[dict] = []

    def capture(**kwargs):
        call_args.append(kwargs)
        return MagicMock(error=None)

    with patch(
        "app.analytics.auto_observer.run_observation", side_effect=lambda conn, **kw: capture(**kw)
    ):
        run_scheduler_tick(conn)

    assert any(a.get("publication_id") == 3 for a in call_args)
    conn.close()


def test_newly_released_pub_adopted_on_next_tick(tmp_path: Path) -> None:
    """A publication released after daemon start is adopted on the next reconcile."""
    conn = open_db(tmp_path / "db.sqlite")

    # First reconcile: no public publications
    adopted1 = reconcile_unobserved_publications(conn)
    assert adopted1 == []

    # Publication goes public (simulating ace release-public)
    _seed_public_publication(conn, pub_id=3)

    # Next reconcile (every tick): pub 3 is now adopted
    adopted2 = reconcile_unobserved_publications(conn)
    assert 3 in adopted2
    conn.close()


# ── 7. OAuth auto-build in run_observation ────────────────────────────────────


def test_run_observation_tries_to_build_oauth_when_none(tmp_path: Path) -> None:
    """run_observation attempts to build OAuth from config when oauth_client=None."""
    conn = open_db(tmp_path / "db.sqlite")
    _seed_public_publication(conn, pub_id=3)
    sid = _insert_analytics_obs_schedule(conn, publication_id=3, next_run_at=_PAST)

    # Insert observation state so run_observation can find workspace_id
    from app.analytics.observation import upsert_observation_state

    upsert_observation_state(
        conn,
        publication_id=3,
        workspace_id="ws-1",
        schedule_id=sid,
    )

    from app.analytics.auto_observer import run_observation

    # Both are lazy-imported inside run_observation; patch at their definition sites.
    mock_client = MagicMock()
    with patch("app.analytics.auto_observer.RealGoogleOAuthClient", mock_client, create=True):
        with patch("app.analytics.gate.build_authenticated_analytics_provider") as mock_gate:
            mock_gate.side_effect = RuntimeError("no creds")  # fail at gate, not at build
            result = run_observation(conn, publication_id=3, schedule_id=sid, oauth_client=None)

    # result.error is set because the gate raised — gracefully handled
    assert result.error is not None  # failed gracefully; did not propagate
    conn.close()


def test_run_observation_uses_provided_oauth_client(tmp_path: Path) -> None:
    """run_observation uses an explicitly provided oauth_client without building one."""
    conn = open_db(tmp_path / "db.sqlite")
    _seed_public_publication(conn, pub_id=3)
    sid = _insert_analytics_obs_schedule(conn, publication_id=3, next_run_at=_PAST)

    from app.analytics.observation import upsert_observation_state

    upsert_observation_state(conn, publication_id=3, workspace_id="ws-1", schedule_id=sid)

    from app.analytics.auto_observer import run_observation

    explicit_client = MagicMock()
    with patch("app.analytics.gate.build_authenticated_analytics_provider") as mock_gate:
        mock_gate.side_effect = RuntimeError("gate blocked")
        result = run_observation(
            conn, publication_id=3, schedule_id=sid, oauth_client=explicit_client
        )

    # Gate was called with the explicit client, not a self-built one
    mock_gate.assert_called_once()
    assert mock_gate.call_args.kwargs.get("oauth_client") is explicit_client
    assert result.error is not None  # gate raised, but gracefully
    conn.close()
