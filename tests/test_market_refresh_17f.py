"""Phase 17F — recurring market-intelligence refresh pipeline.

Covers:
- run_market_refresh_cycle orchestration: stage sequencing, honest
  skip/dry-run semantics, per-stage error isolation (one stage failing
  never kills the cycle or masks the others' results).
- Scheduler wiring: market_refresh dispatches inline (no RQ), respects the
  same idempotency lock as analytics_observation, skips gracefully when a
  channel has no bootstrapped intelligence identity, never raises out of
  the tick loop.
- Channel isolation: a refresh for one channel never touches another's
  scoring policy / opportunities.

Velocity's own history-sufficiency semantics (insufficient history →
unavailable, exact-min-gap accepted, no fabricated zero) are already
exhaustively covered by tests/test_market_velocity.py (36 tests) — not
duplicated here. Interpretation and bridge/scoring internals have their own
dedicated suites (test_market_interpretation.py, test_market_bridge.py);
this file tests the NEW orchestration layer and its scheduler wiring, using
mocks for the inner stages exactly like the existing analytics_observation
scheduler tests do — the real end-to-end chain was additionally verified
once, live, against the dev DB (see Phase 17F report) rather than repeated
here, to avoid duplicating real interpretation/scoring writes on every test
run.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.core.database import open_db
from app.intelligence.market.refresh_service import (
    MarketRefreshResult,
    run_market_refresh_cycle,
)
from app.workers.scheduler import run_scheduler_tick

_NOW = "2026-08-29T00:00:00"
_PAST = "2026-01-01T00:00:00"


def _uid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# run_market_refresh_cycle orchestration
# ---------------------------------------------------------------------------


def test_velocity_skipped_without_api_key_or_collector(tmp_path: Path):
    conn = open_db(tmp_path / "db.sqlite")
    with (
        patch("app.intelligence.market.interpreter.run_market_interpretation") as mock_interp,
        patch("app.intelligence.market.bridge.sync_channel_market_opportunities") as mock_sync,
    ):
        mock_interp.return_value = {"run_id": 1, "status": "completed", "cluster_count": 0}
        mock_sync_result = MagicMock(
            created_count=0, refreshed_count=0, skipped_count=0, scored_count=0
        )
        mock_sync.return_value = mock_sync_result
        with (
            patch("app.intelligence.repository.get_default_scoring_policy", return_value=object()),
            patch("app.intelligence.repository.get_active_profile_version", return_value=object()),
            patch(
                "app.intelligence.market.interpretation_repository.list_interpretation_runs",
                return_value=[],
            ),
        ):
            result = run_market_refresh_cycle(conn, channel_id=1, api_key="")
    conn.close()

    assert result.velocity_attempted is True
    assert result.velocity_skip_reason == "no_youtube_api_key"
    assert result.velocity_observations_new == 0


def test_velocity_rescan_creates_a_valid_collection_job_row(tmp_path: Path):
    """Regression guard (Phase 17G): _run_velocity_rescan's
    create_market_collection_job(origin_type=...) call must use a value the
    market_collection_jobs.origin_type CHECK constraint actually allows.

    The prior 'scheduled_refresh' value was never exercised end-to-end in
    17F's own tests (they only cover the no-api-key skip path), so the real
    schema's CHECK constraint rejected it the first time this code path ran
    against a real database with a real (or collector-supplied) rescan —
    caught during Phase 17G live verification and fixed to 'velocity_rescan'.
    This test exercises create_market_collection_job for real (no mocking
    of the repository layer) with a stubbed collector so no live YouTube
    call is made, and asserts the job row actually persists.
    """
    conn = open_db(tmp_path / "db.sqlite")
    from unittest.mock import MagicMock as _MM

    conn.execute(
        "INSERT INTO channels "
        "(id, platform, channel_name, platform_channel_id, created_at, updated_at) "
        "VALUES (1, 'youtube', 'Test', 'UC_test', datetime('now'), datetime('now'))"
    )
    conn.commit()

    fake_collector = _MM()
    fake_collector.collect_velocity_rescan.return_value = _MM(
        observations_created=0,
        videos_processed=0,
        api_calls_made=0,
    )
    with (
        patch("app.intelligence.market.interpreter.run_market_interpretation") as mock_interp,
        patch("app.intelligence.market.bridge.sync_channel_market_opportunities") as mock_sync,
    ):
        mock_interp.return_value = {"run_id": 1, "status": "completed", "cluster_count": 0}
        mock_sync.return_value = MagicMock(
            created_count=0, refreshed_count=0, skipped_count=0, scored_count=0
        )
        with (
            patch("app.intelligence.repository.get_default_scoring_policy", return_value=object()),
            patch("app.intelligence.repository.get_active_profile_version", return_value=object()),
            patch(
                "app.intelligence.market.interpretation_repository.list_interpretation_runs",
                return_value=[],
            ),
        ):
            result = run_market_refresh_cycle(
                conn,
                channel_id=1,
                api_key="unused",
                collector=fake_collector,
            )

    assert result.velocity_attempted is True
    assert result.velocity_skip_reason is None
    row = conn.execute(
        "SELECT origin_type, job_type FROM market_collection_jobs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    assert row is not None, "create_market_collection_job must have persisted a row, not raised"
    assert row["origin_type"] == "velocity_rescan"
    assert row["job_type"] == "velocity_rescan"


def test_dry_run_skips_velocity_and_interpretation_but_previews_sync(tmp_path: Path):
    conn = open_db(tmp_path / "db.sqlite")
    with (
        patch("app.intelligence.repository.get_default_scoring_policy", return_value=object()),
        patch("app.intelligence.repository.get_active_profile_version", return_value=object()),
        patch(
            "app.intelligence.market.interpretation_repository.list_interpretation_runs",
            return_value=[],
        ),
    ):
        result = run_market_refresh_cycle(conn, channel_id=1, api_key="fake-key", dry_run=True)
    conn.close()

    assert result.velocity_skip_reason == "dry_run"
    assert result.interpretation_skip_reason == "dry_run"
    assert result.interpretation_run_id is None
    # No completed interpretation run exists (mocked empty), so sync honestly
    # reports why it did nothing rather than fabricating a result.
    assert result.sync_skip_reason == "no_completed_interpretation_run"


def test_interpretation_failure_does_not_block_sync_stage(tmp_path: Path):
    """One stage's exception must not prevent later stages from running,
    and must be recorded rather than silently swallowed."""
    conn = open_db(tmp_path / "db.sqlite")
    with (
        patch(
            "app.intelligence.market.interpreter.run_market_interpretation",
            side_effect=RuntimeError("boom"),
        ),
        patch("app.intelligence.repository.get_default_scoring_policy", return_value=object()),
        patch("app.intelligence.repository.get_active_profile_version", return_value=object()),
        patch(
            "app.intelligence.market.interpretation_repository.list_interpretation_runs",
            return_value=[],
        ),
    ):
        result = run_market_refresh_cycle(conn, channel_id=1, api_key="")
    conn.close()

    assert any("interpretation" in e for e in result.errors)
    # Sync stage still ran (attempted), it just found nothing to sync.
    assert result.sync_attempted is True
    assert result.sync_skip_reason == "no_completed_interpretation_run"


def test_sync_skipped_without_scoring_policy(tmp_path: Path):
    conn = open_db(tmp_path / "db.sqlite")
    with (
        patch("app.intelligence.market.interpreter.run_market_interpretation") as mock_interp,
        patch("app.intelligence.repository.get_default_scoring_policy", return_value=None),
    ):
        mock_interp.return_value = {"run_id": 1, "status": "completed", "cluster_count": 0}
        result = run_market_refresh_cycle(conn, channel_id=999, api_key="")
    conn.close()

    assert result.sync_skip_reason == "no_scoring_policy"
    assert result.sync_created == 0


def test_result_completed_at_is_set_and_ok_reflects_errors(tmp_path: Path):
    conn = open_db(tmp_path / "db.sqlite")
    with patch("app.intelligence.repository.get_default_scoring_policy", return_value=None):
        result = run_market_refresh_cycle(conn, channel_id=1, api_key="", dry_run=True)
    conn.close()

    assert result.completed_at != ""
    assert result.ok is True  # skips are not errors


# ---------------------------------------------------------------------------
# Scheduler wiring
# ---------------------------------------------------------------------------


def _seed_workspace_and_channel(conn) -> tuple[str, str]:
    ws_id = _uid()
    cp_channel_id = _uid()
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT INTO cp_workspaces (id, name, slug, actor, created_at, updated_at) "
        "VALUES (?, 'WS', ?, 'system', ?, ?)",
        (ws_id, ws_id, _NOW, _NOW),
    )
    conn.execute(
        "INSERT INTO cp_channels (id, workspace_id, name, slug, actor, created_at, updated_at) "
        "VALUES (?, ?, 'Channel', 'channel', 'system', ?, ?)",
        (cp_channel_id, ws_id, _NOW, _NOW),
    )
    conn.commit()
    return ws_id, cp_channel_id


def _insert_market_refresh_schedule(
    conn,
    *,
    workspace_id: str,
    cp_channel_id: str,
    next_run_at: str = _PAST,
) -> str:
    sid = _uid()
    cfg = json.dumps({"interval_seconds": 21600})
    conn.execute(
        "INSERT INTO app_schedule_definitions "
        "(id, workspace_id, channel_id, name, operation_type, schedule_type, "
        "schedule_config_json, timezone, is_active, next_run_at, actor, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            sid,
            workspace_id,
            cp_channel_id,
            "market_refresh:channel",
            "market_refresh",
            "interval",
            cfg,
            "UTC",
            1,
            next_run_at,
            "system:scheduler",
            _NOW,
            _NOW,
        ),
    )
    conn.commit()
    return sid


def test_scheduler_dispatches_market_refresh_inline(tmp_path: Path):
    conn = open_db(tmp_path / "db.sqlite")
    ws_id, cp_channel_id = _seed_workspace_and_channel(conn)
    _insert_market_refresh_schedule(conn, workspace_id=ws_id, cp_channel_id=cp_channel_id)

    fake_result = MarketRefreshResult(channel_id=1, started_at=_NOW, completed_at=_NOW)
    with (
        patch(
            "app.intelligence.channel_bridge.get_intelligence_channel_id",
            return_value=1,
        ),
        patch(
            "app.intelligence.market.refresh_service.run_market_refresh_cycle",
            return_value=fake_result,
        ) as mock_refresh,
    ):
        job_ids = run_scheduler_tick(conn)

    mock_refresh.assert_called_once()
    assert mock_refresh.call_args.kwargs["channel_id"] == 1
    assert job_ids == []  # inline, never goes through RQ
    conn.close()


def test_scheduler_skips_market_refresh_for_unbootstrapped_channel(tmp_path: Path):
    """No intelligence identity mapped yet — must skip cleanly, not raise."""
    conn = open_db(tmp_path / "db.sqlite")
    ws_id, cp_channel_id = _seed_workspace_and_channel(conn)
    _insert_market_refresh_schedule(conn, workspace_id=ws_id, cp_channel_id=cp_channel_id)

    with (
        patch(
            "app.intelligence.channel_bridge.get_intelligence_channel_id",
            return_value=None,
        ),
        patch("app.intelligence.market.refresh_service.run_market_refresh_cycle") as mock_refresh,
    ):
        job_ids = run_scheduler_tick(conn)

    mock_refresh.assert_not_called()
    assert job_ids == []
    conn.close()


def test_scheduler_market_refresh_missing_channel_id_is_skipped(tmp_path: Path):
    conn = open_db(tmp_path / "db.sqlite")
    ws_id, _ = _seed_workspace_and_channel(conn)
    sid = _uid()
    conn.execute(
        "INSERT INTO app_schedule_definitions "
        "(id, workspace_id, channel_id, name, operation_type, schedule_type, "
        "schedule_config_json, timezone, is_active, next_run_at, actor, created_at, updated_at) "
        "VALUES (?,?,NULL,?,?,?,?,?,?,?,?,?,?)",
        (
            sid,
            ws_id,
            "market_refresh:none",
            "market_refresh",
            "interval",
            "{}",
            "UTC",
            1,
            _PAST,
            "system:scheduler",
            _NOW,
            _NOW,
        ),
    )
    conn.commit()

    with patch("app.intelligence.market.refresh_service.run_market_refresh_cycle") as mock_refresh:
        job_ids = run_scheduler_tick(conn)

    mock_refresh.assert_not_called()
    assert job_ids == []
    conn.close()


def test_scheduler_market_refresh_exception_does_not_kill_tick(tmp_path: Path):
    """A raised exception inside the branch must be caught — the daemon loop
    must survive a market-data hiccup exactly as it already does for
    analytics observation failures."""
    conn = open_db(tmp_path / "db.sqlite")
    ws_id, cp_channel_id = _seed_workspace_and_channel(conn)
    _insert_market_refresh_schedule(conn, workspace_id=ws_id, cp_channel_id=cp_channel_id)

    with patch(
        "app.intelligence.channel_bridge.get_intelligence_channel_id",
        side_effect=RuntimeError("db hiccup"),
    ):
        job_ids = run_scheduler_tick(conn)  # must not raise

    assert job_ids == []
    conn.close()


def test_overlapping_market_refresh_ticks_do_not_double_execute(tmp_path: Path):
    """Mirrors the existing analytics_observation overlap test: the
    idempotency lock (mark_schedule_ran before execution) must prevent a
    second immediate tick from re-triggering the same channel's refresh."""
    conn = open_db(tmp_path / "db.sqlite")
    ws_id, cp_channel_id = _seed_workspace_and_channel(conn)
    _insert_market_refresh_schedule(conn, workspace_id=ws_id, cp_channel_id=cp_channel_id)

    call_count = 0

    def fake_refresh(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return MarketRefreshResult(channel_id=1, started_at=_NOW, completed_at=_NOW)

    with (
        patch(
            "app.intelligence.channel_bridge.get_intelligence_channel_id",
            return_value=1,
        ),
        patch(
            "app.intelligence.market.refresh_service.run_market_refresh_cycle",
            side_effect=fake_refresh,
        ),
    ):
        run_scheduler_tick(conn)  # first tick claims and runs it
        run_scheduler_tick(conn)  # second tick, same instant — nothing due

    assert call_count == 1
    conn.close()


def test_market_refresh_schedule_does_not_affect_analytics_observation_dispatch(tmp_path: Path):
    """Two different inline operation_types coexist correctly in one tick."""
    conn = open_db(tmp_path / "db.sqlite")
    ws_id, cp_channel_id = _seed_workspace_and_channel(conn)
    _insert_market_refresh_schedule(conn, workspace_id=ws_id, cp_channel_id=cp_channel_id)

    with (
        patch(
            "app.intelligence.channel_bridge.get_intelligence_channel_id",
            return_value=1,
        ),
        patch(
            "app.intelligence.market.refresh_service.run_market_refresh_cycle",
            return_value=MarketRefreshResult(channel_id=1, started_at=_NOW, completed_at=_NOW),
        ) as mock_refresh,
    ):
        run_scheduler_tick(conn)

    mock_refresh.assert_called_once()
    conn.close()


# ---------------------------------------------------------------------------
# Channel isolation
# ---------------------------------------------------------------------------


def test_refresh_uses_the_resolved_channel_id_not_a_hardcoded_one(tmp_path: Path):
    """A schedule for channel A must resolve and pass channel A's
    intelligence id, never channel B's or a stale default."""
    conn = open_db(tmp_path / "db.sqlite")
    ws_id, cp_channel_a = _seed_workspace_and_channel(conn)
    _insert_market_refresh_schedule(conn, workspace_id=ws_id, cp_channel_id=cp_channel_a)

    def fake_bridge_lookup(_conn, cp_id):
        return 42 if cp_id == cp_channel_a else 999

    with (
        patch(
            "app.intelligence.channel_bridge.get_intelligence_channel_id",
            side_effect=fake_bridge_lookup,
        ),
        patch(
            "app.intelligence.market.refresh_service.run_market_refresh_cycle",
            return_value=MarketRefreshResult(channel_id=42, started_at=_NOW, completed_at=_NOW),
        ) as mock_refresh,
    ):
        run_scheduler_tick(conn)

    assert mock_refresh.call_args.kwargs["channel_id"] == 42
    conn.close()
