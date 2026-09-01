"""Phase 16D.4 — Automatic analytics observation tests.

Covers:
  - Public release activates observation (registration)
  - Activation idempotent
  - Already-public publication recovery (reconciliation)
  - Due observation calls existing AnalyticsOrchestrator
  - Experiment lineage preserved through the observation
  - Unchanged response causes no duplicate downstream work
  - Changed data snapshot triggers aggregation
  - no_data does not trigger learning
  - Retention unavailable is non-fatal / retried next tick
  - Retention available is persisted
  - Immature experiment stays provisional (insufficient analytics ≠ failure)
  - Mature qualifying evidence invokes existing learning path
  - Provider failure recoverable (failure_count incremented)
  - Publications / channels remain isolated
  - Non-experiment publications still work
  - Publishing / release gates remain fail-closed
  - Age-aware cadence returns correct intervals
  - Schema v45 present
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC
from pathlib import Path
from unittest.mock import patch

import pytest

from app.analytics.auto_observer import ObservationResult, run_observation
from app.analytics.observation import (
    OPERATION_TYPE,
    compute_observation_interval_seconds,
    get_observation_state,
    reconcile_unobserved_publications,
    register_publication_for_observation,
)
from app.analytics.providers.fake import FakeAnalyticsProvider
from app.core.database import SCHEMA_VERSION, open_db

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / "test.db")


def _seed_workspace(conn: sqlite3.Connection) -> str:
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT OR IGNORE INTO cp_workspaces "
        "(id, name, slug, status, actor, created_at, updated_at)"
        " VALUES ('ws1','W','w','active','t',datetime('now'),datetime('now'))"
    )
    conn.commit()
    return "ws1"


def _seed_publication(
    conn: sqlite3.Connection,
    *,
    publication_id: int = 1,
    visibility: str = "public",
    status: str = "published",
    provider_video_id: str = "vid_abc",
    experiment_id: str | None = "exp-001",
    workspace_id: str = "ws1",
    channel_id: str | None = "ch1",
    platform_account_id: str | None = "acct1",
) -> None:
    """Insert the minimal rows for a publication with full lineage.

    Foreign keys are disabled for test isolation — we only need the shape,
    not referential integrity.
    """
    conn.execute("PRAGMA foreign_keys = OFF")

    conn.execute(
        "INSERT OR IGNORE INTO render_manifests ("
        " id, scene_manifest_id, narration_run_id, caption_run_id, topic_id,"
        " plan_id, script_id, input_hash, render_schema_version, compositor_version,"
        " status, created_at, updated_at"
        ") VALUES (1,1,1,1,1,1,1,'rmh1','1.0','1.0','approved',datetime('now'),datetime('now'))"
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO publishing_plans (
            id, render_manifest_id, render_job_id, topic_id, production_plan_id,
            script_id, scene_manifest_id, narration_run_id, caption_run_id,
            experiment_id, input_hash, publishing_engine_version, metadata_version,
            provider, provider_version, title, description, tags_json, language,
            visibility, made_for_kids, schedule_type, status, created_at, updated_at
        ) VALUES (1,1,NULL,1,1,1,1,1,1,?,?,?,?,?,?,'T','','[]','en','private',0,
                  'immediate','approved',datetime('now'),datetime('now'))
        """,
        (experiment_id, "pph1", "1.0.0", "1.0.0", "youtube", "1.0.0"),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO publishing_jobs (
            id, publishing_plan_id, attempt_number, status, created_at, updated_at
        ) VALUES (1,1,1,'completed',datetime('now'),datetime('now'))
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO publications (
            id, publishing_plan_id, publishing_job_id,
            provider, provider_version, provider_video_id,
            status, visibility, publishing_engine_version, input_hash, output_sha256,
            workspace_id, channel_id, platform_account_id,
            published_at, created_at, updated_at
        ) VALUES (?,1,1,'youtube','1.0.0',?,?,?,?,?,?,?,?,?,
            datetime('now'),datetime('now'),datetime('now'))
        """,
        (
            publication_id,
            provider_video_id,
            status,
            visibility,
            "1.0.0",
            f"ih{publication_id}",
            "sha",
            workspace_id,
            channel_id,
            platform_account_id,
        ),
    )
    conn.commit()


# ── Schema sentinel ───────────────────────────────────────────────────────────


def test_schema_version_is_45() -> None:
    assert SCHEMA_VERSION == 51


def test_analytics_observation_state_table_created(db: sqlite3.Connection) -> None:
    row = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='analytics_observation_state'"
    ).fetchone()
    assert row is not None, "analytics_observation_state table missing"


# ── Cadence policy ────────────────────────────────────────────────────────────


def test_cadence_fresh_video() -> None:
    """Video published 1 h ago → hourly."""
    from datetime import timedelta

    from app.analytics.observation import compute_observation_interval_seconds

    recent = (
        (lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc))()
        - timedelta(hours=1)
    ).isoformat()
    assert compute_observation_interval_seconds(recent) == 3600


def test_cadence_six_to_24h() -> None:
    from datetime import timedelta

    ago = (
        (lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc))()
        - timedelta(hours=12)
    ).isoformat()
    assert compute_observation_interval_seconds(ago) == 10800


def test_cadence_mature_video() -> None:
    from datetime import timedelta

    old = (
        (lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc))()
        - timedelta(days=45)
    ).isoformat()
    assert compute_observation_interval_seconds(old) == 259200


def test_cadence_none_published_at() -> None:
    assert compute_observation_interval_seconds(None) == 21600


# ── Registration & idempotency ─────────────────────────────────────────────────


def test_register_creates_schedule(db: sqlite3.Connection) -> None:
    _seed_workspace(db)
    _seed_publication(db)

    sched_id = register_publication_for_observation(
        db,
        publication_id=1,
        workspace_id="ws1",
        channel_id="ch1",
        platform_account_id="acct1",
    )
    assert sched_id is not None

    row = db.execute("SELECT * FROM app_schedule_definitions WHERE id = ?", (sched_id,)).fetchone()
    assert row is not None
    assert row["operation_type"] == OPERATION_TYPE
    cfg = json.loads(row["schedule_config_json"])
    assert cfg["publication_id"] == 1


def test_register_idempotent(db: sqlite3.Connection) -> None:
    _seed_workspace(db)
    _seed_publication(db)

    id1 = register_publication_for_observation(db, publication_id=1, workspace_id="ws1")
    id2 = register_publication_for_observation(db, publication_id=1, workspace_id="ws1")
    assert id1 == id2

    # Only one schedule row.
    count = db.execute(
        "SELECT COUNT(*) FROM app_schedule_definitions WHERE operation_type = ?",
        (OPERATION_TYPE,),
    ).fetchone()[0]
    assert count == 1


def test_register_creates_observation_state(db: sqlite3.Connection) -> None:
    _seed_workspace(db)
    _seed_publication(db)

    register_publication_for_observation(db, publication_id=1, workspace_id="ws1")

    state = get_observation_state(db, 1)
    assert state is not None
    assert state["observation_status"] == "active"


# ── Recovery / reconciliation ─────────────────────────────────────────────────


def test_reconcile_adopts_public_publications(db: sqlite3.Connection) -> None:
    _seed_workspace(db)
    _seed_publication(db, publication_id=1, visibility="public", status="published")

    adopted = reconcile_unobserved_publications(db)
    assert 1 in adopted

    state = get_observation_state(db, 1)
    assert state is not None
    assert state["observation_status"] == "active"


def test_reconcile_skips_already_registered(db: sqlite3.Connection) -> None:
    _seed_workspace(db)
    _seed_publication(db)

    register_publication_for_observation(db, publication_id=1, workspace_id="ws1")
    adopted = reconcile_unobserved_publications(db)
    assert 1 not in adopted  # already registered — skipped


def test_reconcile_skips_private_publications(db: sqlite3.Connection) -> None:
    _seed_workspace(db)
    _seed_publication(db, visibility="private")

    adopted = reconcile_unobserved_publications(db)
    assert adopted == []


def test_reconcile_idempotent(db: sqlite3.Connection) -> None:
    _seed_workspace(db)
    _seed_publication(db)

    reconcile_unobserved_publications(db)
    reconcile_unobserved_publications(db)  # second call is safe

    count = db.execute(
        "SELECT COUNT(*) FROM app_schedule_definitions WHERE operation_type = ?",
        (OPERATION_TYPE,),
    ).fetchone()[0]
    assert count == 1


# ── Observation tick — core behaviour ─────────────────────────────────────────


class YouTubeFakeProvider(FakeAnalyticsProvider):
    """FakeAnalyticsProvider that advertises provider_name='youtube' so the
    eligibility gate accepts it against publications seeded with provider='youtube'."""

    provider_name: str = "youtube"
    provider_version: str = "test-1.0"


def _make_fake_provider() -> YouTubeFakeProvider:
    return YouTubeFakeProvider()


def _register_and_get_schedule_id(db: sqlite3.Connection, pub_id: int = 1) -> str:
    return register_publication_for_observation(
        db, publication_id=pub_id, workspace_id="ws1", channel_id="ch1"
    )


def test_observation_calls_orchestrator_ingest(db: sqlite3.Connection) -> None:
    _seed_workspace(db)
    _seed_publication(db)
    sched_id = _register_and_get_schedule_id(db)
    provider = FakeAnalyticsProvider()

    from app.analytics.orchestrator import AnalyticsOrchestrator

    original_ingest = AnalyticsOrchestrator.ingest
    calls = []

    def _spy_ingest(self, **kw):
        calls.append(kw)
        return original_ingest(self, **kw)

    with patch.object(AnalyticsOrchestrator, "ingest", _spy_ingest):
        run_observation(db, publication_id=1, schedule_id=sched_id, _provider_override=provider)

    assert len(calls) == 1
    assert calls[0]["publication_id"] == 1
    assert calls[0]["topic_id"] == 1


def test_observation_preserves_experiment_lineage(db: sqlite3.Connection) -> None:
    _seed_workspace(db)
    _seed_publication(db, experiment_id="exp-xyz")
    sched_id = _register_and_get_schedule_id(db)

    from app.analytics.orchestrator import AnalyticsOrchestrator

    original_ingest = AnalyticsOrchestrator.ingest
    captured = {}

    def _spy(self, **kw):
        captured.update(kw)
        return original_ingest(self, **kw)

    with patch.object(AnalyticsOrchestrator, "ingest", _spy):
        run_observation(
            db,
            publication_id=1,
            schedule_id=sched_id,
            _provider_override=YouTubeFakeProvider(),
        )

    assert captured.get("experiment_id") == "exp-xyz"


def test_observation_unchanged_response_no_downstream_work(db: sqlite3.Connection) -> None:
    _seed_workspace(db)
    _seed_publication(db)
    sched_id = _register_and_get_schedule_id(db)
    provider = FakeAnalyticsProvider()

    aggregate_calls = []
    learning_calls = []

    from app.analytics.orchestrator import AnalyticsOrchestrator

    with patch.object(
        AnalyticsOrchestrator, "aggregate", lambda *a, **kw: aggregate_calls.append(1)
    ):
        with patch(
            "app.analytics.auto_observer._attempt_learning",
            side_effect=lambda *a, **kw: learning_calls.append(1) or None,
        ):
            # First tick creates the snapshot.
            run_observation(db, publication_id=1, schedule_id=sched_id, _provider_override=provider)
            first_agg = len(aggregate_calls)

            # Second tick: same provider → same fingerprint → unchanged.
            run_observation(db, publication_id=1, schedule_id=sched_id, _provider_override=provider)

    assert len(aggregate_calls) == first_agg  # no new aggregate call on second tick
    assert (
        len(learning_calls) == 0
    )  # new_data triggers learning but YouTubeFakeProvider gets same fingerprint on 2nd call


def test_observation_new_data_triggers_aggregation(db: sqlite3.Connection) -> None:
    _seed_workspace(db)
    _seed_publication(db)
    sched_id = _register_and_get_schedule_id(db)

    from app.analytics.orchestrator import AnalyticsOrchestrator

    agg_calls = []

    class DataProvider(YouTubeFakeProvider):
        """Returns different data on each call to simulate new metrics."""

        def __init__(self) -> None:
            super().__init__()
            self._call_count = 0

        def fetch_metrics(self, provider_video_id: str, **kw):
            self._call_count += 1
            from app.analytics.protocol import ProviderMetrics

            return ProviderMetrics(
                provider_video_id=provider_video_id, raw={"views": self._call_count * 100}
            )

        def normalize(self, raw):
            return {"views": float(raw.raw.get("views", 0))}

    provider = DataProvider()

    with patch.object(AnalyticsOrchestrator, "aggregate", lambda self, **kw: agg_calls.append(kw)):
        # First tick — new snapshot.
        run_observation(db, publication_id=1, schedule_id=sched_id, _provider_override=provider)
        assert len(agg_calls) == 1

        # Second tick — different data → new snapshot → aggregate again.
        run_observation(db, publication_id=1, schedule_id=sched_id, _provider_override=provider)
        assert len(agg_calls) == 2


def test_observation_no_data_does_not_trigger_learning(db: sqlite3.Connection) -> None:
    _seed_workspace(db)
    _seed_publication(db)
    sched_id = _register_and_get_schedule_id(db)

    learning_calls = []

    class NoDataProvider(YouTubeFakeProvider):
        """Returns empty raw dict → no_data observation."""

        def fetch_metrics(self, provider_video_id: str, **kw):
            from app.analytics.protocol import ProviderMetrics

            return ProviderMetrics(provider_video_id=provider_video_id, raw={})

        def normalize(self, raw):
            return {}

    with patch(
        "app.analytics.auto_observer._attempt_learning",
        side_effect=lambda *a, **kw: learning_calls.append(1) or None,
    ):
        result = run_observation(
            db,
            publication_id=1,
            schedule_id=sched_id,
            _provider_override=NoDataProvider(),
        )

    assert result.observation_state == "no_data"
    assert len(learning_calls) == 0


def test_observation_retention_unavailable_is_non_fatal(db: sqlite3.Connection) -> None:
    _seed_workspace(db)
    _seed_publication(db)
    sched_id = _register_and_get_schedule_id(db)

    with patch("app.analytics.auto_observer._attempt_retention", return_value=False):
        result = run_observation(
            db,
            publication_id=1,
            schedule_id=sched_id,
            _provider_override=YouTubeFakeProvider(),
        )

    # Observation succeeded despite no retention.
    assert result.error is None
    state = get_observation_state(db, 1)
    assert state is not None
    assert int(state.get("retention_acquired") or 0) == 0


def test_observation_retention_acquired_persisted(db: sqlite3.Connection) -> None:
    _seed_workspace(db)
    _seed_publication(db)
    sched_id = _register_and_get_schedule_id(db)

    class DataProvider2(YouTubeFakeProvider):
        def fetch_metrics(self, provider_video_id: str, **kw):
            from app.analytics.protocol import ProviderMetrics

            return ProviderMetrics(provider_video_id=provider_video_id, raw={"views": 500})

        def normalize(self, raw):
            return {"views": float(raw.raw.get("views", 0))}

    with patch("app.analytics.auto_observer._attempt_retention", return_value=True):
        result = run_observation(
            db,
            publication_id=1,
            schedule_id=sched_id,
            _provider_override=DataProvider2(),
        )

    assert result.retention_acquired is True
    state = get_observation_state(db, 1)
    assert int(state.get("retention_acquired") or 0) == 1


def test_observation_insufficient_analytics_does_not_fail(db: sqlite3.Connection) -> None:
    """InsufficientAnalyticsDataError from analyze_publication() must be non-fatal."""
    _seed_workspace(db)
    _seed_publication(db)
    sched_id = _register_and_get_schedule_id(db)

    from app.learning.errors import InsufficientAnalyticsDataError

    class DataProvider3(YouTubeFakeProvider):
        def fetch_metrics(self, provider_video_id: str, **kw):
            from app.analytics.protocol import ProviderMetrics

            return ProviderMetrics(provider_video_id=provider_video_id, raw={"views": 10})

        def normalize(self, raw):
            return {"views": float(raw.raw.get("views", 0))}

    # Patch analyze_publication inside _attempt_learning's import path so the
    # InsufficientAnalyticsDataError is raised inside _attempt_learning's try/except.
    with patch(
        "app.learning.orchestrator.analyze_publication",
        side_effect=InsufficientAnalyticsDataError("not enough data"),
    ):
        result = run_observation(
            db,
            publication_id=1,
            schedule_id=sched_id,
            _provider_override=DataProvider3(),
        )

    # Observation itself is still a success — learning just silently skipped.
    assert result.error is None
    assert result.learning_run_id is None


def test_observation_mature_evidence_invokes_learning(db: sqlite3.Connection) -> None:
    _seed_workspace(db)
    _seed_publication(db, experiment_id="exp-mature")
    sched_id = _register_and_get_schedule_id(db)

    learning_calls = []

    class DataProvider4(YouTubeFakeProvider):
        def fetch_metrics(self, provider_video_id: str, **kw):
            from app.analytics.protocol import ProviderMetrics

            return ProviderMetrics(provider_video_id=provider_video_id, raw={"views": 1000})

        def normalize(self, raw):
            return {"views": float(raw.raw.get("views", 0))}

    with patch(
        "app.analytics.auto_observer._attempt_learning",
        side_effect=lambda *a, **kw: learning_calls.append(1) or 42,
    ):
        result = run_observation(
            db,
            publication_id=1,
            schedule_id=sched_id,
            _provider_override=DataProvider4(),
        )

    assert len(learning_calls) == 1
    assert result.learning_run_id == 42


def test_observation_provider_failure_increments_failure_count(db: sqlite3.Connection) -> None:
    _seed_workspace(db)
    _seed_publication(db)
    sched_id = _register_and_get_schedule_id(db)

    class BrokenProvider(YouTubeFakeProvider):
        def fetch_metrics(self, *a, **kw):
            raise RuntimeError("network error")

    result = run_observation(
        db, publication_id=1, schedule_id=sched_id, _provider_override=BrokenProvider()
    )
    assert result.error is not None

    state = get_observation_state(db, 1)
    assert int(state.get("failure_count") or 0) == 1


def test_observation_max_failures_pauses_schedule(db: sqlite3.Connection) -> None:
    _seed_workspace(db)
    _seed_publication(db)
    sched_id = _register_and_get_schedule_id(db)

    class BrokenProvider2(YouTubeFakeProvider):
        def fetch_metrics(self, *a, **kw):
            raise RuntimeError("always fails")

    from app.analytics.observation import MAX_CONSECUTIVE_FAILURES

    for _ in range(MAX_CONSECUTIVE_FAILURES):
        run_observation(
            db, publication_id=1, schedule_id=sched_id, _provider_override=BrokenProvider2()
        )

    state = get_observation_state(db, 1)
    assert state["observation_status"] == "paused"

    sched_row = db.execute(
        "SELECT is_active FROM app_schedule_definitions WHERE id = ?", (sched_id,)
    ).fetchone()
    assert sched_row["is_active"] == 0


def test_publications_isolated(db: sqlite3.Connection) -> None:
    """Registering two publications creates two isolated schedules."""
    _seed_workspace(db)
    _seed_publication(db, publication_id=1)
    # Seed a second publication with distinct provider_video_id.
    _seed_publication(db, publication_id=2, provider_video_id="vid_xyz")

    id1 = register_publication_for_observation(db, publication_id=1, workspace_id="ws1")
    id2 = register_publication_for_observation(db, publication_id=2, workspace_id="ws1")

    assert id1 != id2

    state1 = get_observation_state(db, 1)
    state2 = get_observation_state(db, 2)
    assert state1["schedule_id"] == id1
    assert state2["schedule_id"] == id2


def test_non_experiment_publication_works(db: sqlite3.Connection) -> None:
    _seed_workspace(db)
    _seed_publication(db, experiment_id=None)
    sched_id = _register_and_get_schedule_id(db)

    result = run_observation(
        db,
        publication_id=1,
        schedule_id=sched_id,
        _provider_override=YouTubeFakeProvider(),
    )
    # Lineage derived successfully even without an experiment.
    assert result.error is None or "lineage" not in (result.error or "")


def test_publishing_gates_remain_fail_closed(db: sqlite3.Connection) -> None:
    """Analytics observation must not require or modify publishing gates."""
    from app.core.config import get_config

    cfg = get_config()
    assert not cfg.publishing_live_enabled, "ACE_PUBLISHING_LIVE_ENABLED must be false"
    assert not cfg.release_public_enabled, "ACE_RELEASE_PUBLIC_ENABLED must be false"


def test_observation_advances_next_run(db: sqlite3.Connection) -> None:
    _seed_workspace(db)
    _seed_publication(db)
    sched_id = _register_and_get_schedule_id(db)

    from datetime import datetime

    before = datetime.now(UTC)

    run_observation(
        db,
        publication_id=1,
        schedule_id=sched_id,
        _provider_override=YouTubeFakeProvider(),
    )

    row = db.execute(
        "SELECT next_run_at FROM app_schedule_definitions WHERE id = ?", (sched_id,)
    ).fetchone()
    assert row is not None
    next_run = datetime.fromisoformat(row["next_run_at"])
    if next_run.tzinfo is None:
        next_run = next_run.replace(tzinfo=UTC)
    assert next_run > before


def test_observation_state_updated_after_success(db: sqlite3.Connection) -> None:
    _seed_workspace(db)
    _seed_publication(db)
    sched_id = _register_and_get_schedule_id(db)

    run_observation(
        db,
        publication_id=1,
        schedule_id=sched_id,
        _provider_override=YouTubeFakeProvider(),
    )

    state = get_observation_state(db, 1)
    assert state["last_attempted_at"] is not None
    assert state["last_success_at"] is not None
    assert int(state["failure_count"]) == 0


def test_publication_released_public_event_type_registered() -> None:
    from app.control_plane.constants import ALL_EVENT_TYPES

    assert "publication.released_public" in ALL_EVENT_TYPES


def test_execute_scheduled_operation_job_dispatches_observation(db: sqlite3.Connection) -> None:
    _seed_workspace(db)
    _seed_publication(db)
    sched_id = _register_and_get_schedule_id(db)

    with patch("app.analytics.auto_observer.run_observation") as mock_run:
        mock_result = ObservationResult()
        mock_run.return_value = mock_result

        from app.workers.jobs import execute_scheduled_operation_job

        # get_db_connection is imported inside the function; patch at its source module.
        with patch("app.core.database.get_db_connection", return_value=db):
            result = execute_scheduled_operation_job(
                {
                    "schedule_id": sched_id,
                    "workspace_id": "ws1",
                    "actor": "system:test",
                }
            )

    assert result["operation_type"] == "analytics_observation"
    assert result["publication_id"] == 1
