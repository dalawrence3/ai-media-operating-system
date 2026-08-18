"""Tests for the v24 Analytics Observation Model.

Covers: observation_state, response_fingerprint, observed_at, idempotency
semantics, no_data path, legacy null fields, and list_snapshots filtering.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from app.analytics.constants import METRIC_VIEWS
from app.analytics.orchestrator import AnalyticsOrchestrator
from app.analytics.providers.fake import FakeAnalyticsProvider
from app.analytics.repository import (
    get_snapshot_by_hash,
    list_snapshots,
)
from app.core.database import open_db


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / "test.db")


def _insert_render_manifest(conn: sqlite3.Connection, render_id: int = 1) -> None:
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT OR IGNORE INTO render_manifests ("
        " id, scene_manifest_id, narration_run_id, caption_run_id, topic_id,"
        " plan_id, script_id, input_hash, render_schema_version, compositor_version,"
        " status, created_at, updated_at"
        ") VALUES (?, ?, 1, 1, 1, 1, 1, ?, '1.0.0', '1.0.0', 'approved',"
        " datetime('now'), datetime('now'))",
        (render_id, render_id, f"rh{render_id}"),
    )
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()


def _insert_publication(
    conn: sqlite3.Connection,
    publication_id: int = 1,
    provider_video_id: str = "vid123",
    render_id: int = 1,
) -> None:
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT OR IGNORE INTO publications ("
        " id, publishing_plan_id, publishing_job_id,"
        " provider, provider_version, provider_video_id,"
        " status, publishing_engine_version, input_hash, output_sha256,"
        " created_at, updated_at"
        ") VALUES (?, 1, 1, 'fake', '1.0.0', ?, 'published', '1.0.0', ?, 's',"
        " datetime('now'), datetime('now'))",
        (publication_id, provider_video_id, f"ph{publication_id}"),
    )
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()


def _seed_eligible(conn, publication_id=1, provider_video_id="vid123", render_id=1):
    _insert_render_manifest(conn, render_id)
    _insert_publication(
        conn, publication_id, provider_video_id=provider_video_id, render_id=render_id
    )


def _ingest(orch, publication_id=1, **kw):
    defaults = dict(
        provider_video_id="vid123",
        publication_id=publication_id,
        publishing_plan_id=1,
        publishing_job_id=1,
        render_manifest_id=1,
        scene_manifest_id=1,
        production_plan_id=1,
        script_id=1,
        topic_id=1,
        narration_run_id=1,
        caption_run_id=1,
    )
    defaults.update(kw)
    return orch.ingest(**defaults)


class TestObservationStateDataPath:
    def test_observation_state_is_data_when_metrics_present(self, db):
        _seed_eligible(db)
        snap, _ = _ingest(AnalyticsOrchestrator(db, FakeAnalyticsProvider()))
        assert snap.observation_state == "data"

    def test_metrics_written_when_data(self, db):
        _seed_eligible(db)
        snap, metrics = _ingest(AnalyticsOrchestrator(db, FakeAnalyticsProvider()))
        assert snap.observation_state == "data"
        assert len(metrics) > 0

    def test_observed_at_stored_on_new_snapshot(self, db):
        _seed_eligible(db)
        snap, _ = _ingest(AnalyticsOrchestrator(db, FakeAnalyticsProvider()))
        assert snap.observed_at is not None
        assert "T" in snap.observed_at  # ISO-8601 format

    def test_response_fingerprint_stored_on_new_snapshot(self, db):
        _seed_eligible(db)
        snap, _ = _ingest(AnalyticsOrchestrator(db, FakeAnalyticsProvider()))
        assert snap.response_fingerprint is not None
        assert len(snap.response_fingerprint) == 64

    def test_response_fingerprint_matches_raw_response(self, db):
        _seed_eligible(db)
        metrics = {METRIC_VIEWS: 100.0}
        provider = FakeAnalyticsProvider(metrics=metrics)
        snap, _ = _ingest(AnalyticsOrchestrator(db, provider))
        expected = hashlib.sha256(
            json.dumps(metrics, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        assert snap.response_fingerprint == expected


class TestObservationStateNoDataPath:
    def test_observation_state_is_no_data_when_empty_response(self, db):
        _seed_eligible(db)
        snap, metrics = _ingest(AnalyticsOrchestrator(db, FakeAnalyticsProvider(metrics={})))
        assert snap.observation_state == "no_data"
        assert metrics == []

    def test_no_data_snapshot_is_created_not_skipped(self, db):
        _seed_eligible(db)
        snap, metrics = _ingest(AnalyticsOrchestrator(db, FakeAnalyticsProvider(metrics={})))
        assert snap.id is not None
        assert len(metrics) == 0

    def test_no_data_observed_at_stored(self, db):
        _seed_eligible(db)
        snap, _ = _ingest(AnalyticsOrchestrator(db, FakeAnalyticsProvider(metrics={})))
        assert snap.observed_at is not None

    def test_no_data_response_fingerprint_is_sha256_of_empty_object(self, db):
        _seed_eligible(db)
        snap, _ = _ingest(AnalyticsOrchestrator(db, FakeAnalyticsProvider(metrics={})))
        expected = hashlib.sha256(
            json.dumps({}, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        assert snap.response_fingerprint == expected


class TestIdempotencyWithResponseFingerprint:
    def test_same_window_same_response_returns_existing_snapshot(self, db):
        _seed_eligible(db)
        orch = AnalyticsOrchestrator(db, FakeAnalyticsProvider())
        snap1, _ = _ingest(orch)
        snap2, _ = _ingest(orch)
        assert snap1.id == snap2.id

    def test_same_window_different_response_creates_new_snapshot(self, db):
        _seed_eligible(db)
        snap1, _ = _ingest(
            AnalyticsOrchestrator(db, FakeAnalyticsProvider(metrics={METRIC_VIEWS: 100.0}))
        )
        snap2, _ = _ingest(
            AnalyticsOrchestrator(db, FakeAnalyticsProvider(metrics={METRIC_VIEWS: 200.0}))
        )
        assert snap1.id != snap2.id

    def test_no_data_then_data_creates_new_snapshot(self, db):
        _seed_eligible(db)
        snap_empty, _ = _ingest(AnalyticsOrchestrator(db, FakeAnalyticsProvider(metrics={})))
        snap_data, _ = _ingest(AnalyticsOrchestrator(db, FakeAnalyticsProvider()))
        assert snap_empty.id != snap_data.id
        assert snap_empty.observation_state == "no_data"
        assert snap_data.observation_state == "data"

    def test_observed_at_not_in_hash_same_snapshot_returned(self, db):
        """Repeated ingest with identical data returns the same snapshot despite time passing."""
        _seed_eligible(db)
        orch = AnalyticsOrchestrator(db, FakeAnalyticsProvider())
        snap1, _ = _ingest(orch)
        snap2, _ = _ingest(orch)
        assert snap1.id == snap2.id


class TestListSnapshotsObservationStateFilter:
    def test_filter_data_returns_only_data_snapshots(self, db):
        _seed_eligible(db)
        _ingest(
            AnalyticsOrchestrator(db, FakeAnalyticsProvider(metrics={})), period_start="2026-01-01"
        )
        _ingest(AnalyticsOrchestrator(db, FakeAnalyticsProvider()), period_start="2026-02-01")
        data_snaps = list_snapshots(db, observation_state="data")
        assert len(data_snaps) == 1
        assert data_snaps[0].observation_state == "data"

    def test_filter_no_data_returns_only_no_data_snapshots(self, db):
        _seed_eligible(db)
        _ingest(
            AnalyticsOrchestrator(db, FakeAnalyticsProvider(metrics={})), period_start="2026-01-01"
        )
        _ingest(AnalyticsOrchestrator(db, FakeAnalyticsProvider()), period_start="2026-02-01")
        no_data_snaps = list_snapshots(db, observation_state="no_data")
        assert len(no_data_snaps) == 1
        assert no_data_snaps[0].observation_state == "no_data"

    def test_no_filter_returns_all_snapshots(self, db):
        _seed_eligible(db)
        _ingest(
            AnalyticsOrchestrator(db, FakeAnalyticsProvider(metrics={})), period_start="2026-01-01"
        )
        _ingest(AnalyticsOrchestrator(db, FakeAnalyticsProvider()), period_start="2026-02-01")
        all_snaps = list_snapshots(db)
        assert len(all_snaps) == 2


class TestLegacySnapshotNullFields:
    def test_legacy_row_has_null_observation_fields(self, db):
        """Rows inserted without v24 observation columns deserialize with None fields."""
        db.execute(
            "INSERT INTO analytics_snapshots ("
            " publication_id, publishing_plan_id, publishing_job_id,"
            " render_manifest_id, scene_manifest_id, production_plan_id,"
            " script_id, topic_id, narration_run_id, caption_run_id,"
            " provider, provider_version, adapter_version, engine_version,"
            " analytics_schema_version, db_schema_version,"
            " input_hash, raw_metrics_json, ingested_at, created_at"
            ") VALUES (1,1,1,1,1,1,1,1,1,1,"
            " 'fake','1.0.0','1.0.0','1.0.0','1.0.0',23,"
            " 'old-hash','{}','2026-01-01','2026-01-01')"
        )
        db.commit()
        snap = get_snapshot_by_hash(db, "old-hash")
        assert snap is not None
        assert snap.observed_at is None
        assert snap.response_fingerprint is None
        assert snap.observation_state is None

    def test_observation_state_check_constraint_rejects_invalid(self, db):
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO analytics_snapshots ("
                " publication_id, publishing_plan_id, publishing_job_id,"
                " render_manifest_id, scene_manifest_id, production_plan_id,"
                " script_id, topic_id, narration_run_id, caption_run_id,"
                " provider, provider_version, adapter_version, engine_version,"
                " analytics_schema_version, db_schema_version,"
                " input_hash, raw_metrics_json, ingested_at, created_at,"
                " observation_state"
                ") VALUES (1,1,1,1,1,1,1,1,1,1,"
                " 'fake','1.0.0','1.0.0','1.0.0','1.0.0',24,"
                " 'bad-hash','{}','2026-01-01','2026-01-01',"
                " 'invalid_state')"
            )
            db.commit()
