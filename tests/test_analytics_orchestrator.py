"""Tests for the analytics orchestrator."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.analytics.constants import METRIC_REVENUE_ESTIMATE, METRIC_VIEWS
from app.analytics.errors import (
    MissingCurrencyError,
    PublicationIneligibleError,
    ReviewNotesRequiredError,
)
from app.analytics.models import AnalyticsMetric, AnalyticsSnapshot
from app.analytics.orchestrator import AnalyticsOrchestrator
from app.analytics.providers.fake import FakeAnalyticsProvider
from app.core.database import open_db


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / "test.db")


@pytest.fixture()
def provider():
    return FakeAnalyticsProvider()


def _insert_render_manifest(conn: sqlite3.Connection, render_id: int = 1) -> None:
    """Insert a minimal approved, non-superseded render manifest.

    scene_manifest_id=render_id to avoid the partial unique index
    idx_rm_one_active_normal which forbids two active renders per scene_manifest.
    """
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        """
        INSERT OR IGNORE INTO render_manifests (
            id, scene_manifest_id, narration_run_id, caption_run_id, topic_id,
            plan_id, script_id, input_hash, render_schema_version, compositor_version,
            status, created_at, updated_at
        ) VALUES (?, ?, 1, 1, 1, 1, 1, ?, '1.0.0', '1.0.0', 'approved',
                  datetime('now'), datetime('now'))
        """,
        (render_id, render_id, f"rh{render_id}"),
    )
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()


def _insert_publication(
    conn: sqlite3.Connection,
    publication_id: int = 1,
    status: str = "published",
    provider_video_id: str = "vid123",
    provider: str = "fake",
    render_id: int = 1,
) -> None:
    """Insert a minimal eligible publication row (FK constraints disabled for test isolation)."""
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        """
        INSERT OR IGNORE INTO publications (
            id, publishing_plan_id, publishing_job_id,
            provider, provider_version, provider_video_id,
            status, publishing_engine_version, input_hash, output_sha256,
            created_at, updated_at
        ) VALUES (?, 1, 1, ?, '1.0.0', ?, ?, '1.0.0', ?, 's',
                  datetime('now'), datetime('now'))
        """,
        (publication_id, provider, provider_video_id or "", status, f"ph{publication_id}"),
    )
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()


def _seed_eligible(
    conn: sqlite3.Connection,
    publication_id: int = 1,
    provider_video_id: str = "vid123",
    provider: str = "fake",
    render_id: int = 1,
) -> None:
    """Insert a complete eligible publication + render chain."""
    _insert_render_manifest(conn, render_id)
    _insert_publication(conn, publication_id, provider_video_id=provider_video_id,
                        provider=provider, render_id=render_id)


def _ingest(orch, db, publication_id=1, **kw):
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


@pytest.fixture()
def seeded_db(tmp_path: Path) -> sqlite3.Connection:
    """A DB with one eligible publication and render, ready for ingest."""
    conn = open_db(tmp_path / "test.db")
    _seed_eligible(conn)
    return conn


@pytest.fixture()
def orch(seeded_db, provider):
    return AnalyticsOrchestrator(seeded_db, provider)


class TestOrchestratorIngest:
    def test_returns_snapshot_and_metrics(self, orch, seeded_db):
        snap, metrics = _ingest(orch, seeded_db)
        assert isinstance(snap, AnalyticsSnapshot)
        assert isinstance(metrics, list)
        assert all(isinstance(m, AnalyticsMetric) for m in metrics)

    def test_metrics_have_canonical_names(self, orch, seeded_db):
        from app.analytics.constants import CANONICAL_METRICS
        _, metrics = _ingest(orch, seeded_db)
        for m in metrics:
            assert m.metric_name in CANONICAL_METRICS

    def test_idempotent_same_inputs(self, orch, seeded_db):
        snap1, _ = _ingest(orch, seeded_db, publication_id=1)
        snap2, _ = _ingest(orch, seeded_db, publication_id=1)
        assert snap1.id == snap2.id

    def test_different_period_different_snapshot(self, orch, seeded_db):
        snap1, _ = _ingest(orch, seeded_db, period_start="2026-01-01")
        snap2, _ = _ingest(orch, seeded_db, period_start="2026-02-01")
        assert snap1.id != snap2.id

    def test_different_publication_different_snapshot(self, orch, seeded_db):
        _seed_eligible(seeded_db, publication_id=2, render_id=2, provider_video_id="vid456")
        snap1, _ = _ingest(orch, seeded_db, publication_id=1, render_manifest_id=1)
        snap2, _ = _ingest(orch, seeded_db, publication_id=2, render_manifest_id=2,
                           provider_video_id="vid456")
        assert snap1.id != snap2.id

    def test_snapshot_attribution_stored(self, orch, seeded_db):
        snap, _ = _ingest(orch, seeded_db, publication_id=1, topic_id=7, experiment_id="exp-1")
        assert snap.publication_id == 1
        assert snap.topic_id == 7
        assert snap.experiment_id == "exp-1"

    def test_custom_metrics_ingested(self, seeded_db):
        p = FakeAnalyticsProvider(metrics={METRIC_VIEWS: 12345.0})
        orch = AnalyticsOrchestrator(seeded_db, p)
        _, metrics = _ingest(orch, seeded_db)
        views = next(m for m in metrics if m.metric_name == METRIC_VIEWS)
        assert views.metric_value == 12345.0

    def test_invalid_publication_id_raises(self, orch, seeded_db):
        with pytest.raises(ValueError):
            _ingest(orch, seeded_db, publication_id=0)

    def test_is_period_complete_stored(self, orch, seeded_db):
        snap, _ = _ingest(orch, seeded_db, is_period_complete=True)
        assert snap.is_period_complete == 1

    def test_is_period_complete_defaults_false(self, orch, seeded_db):
        snap, _ = _ingest(orch, seeded_db)
        assert snap.is_period_complete == 0


class TestPublicationEligibility:
    def test_eligible_publication_passes(self, db):
        _seed_eligible(db, publication_id=10)
        orch = AnalyticsOrchestrator(db, FakeAnalyticsProvider())
        snap, _ = _ingest(orch, db, publication_id=10)
        assert snap.publication_id == 10

    def test_uploaded_status_eligible(self, db):
        _insert_render_manifest(db, render_id=2)
        _insert_publication(db, publication_id=11, status="uploaded", render_id=2)
        orch = AnalyticsOrchestrator(db, FakeAnalyticsProvider())
        snap, _ = _ingest(orch, db, publication_id=11, render_manifest_id=2)
        assert snap.publication_id == 11

    def test_missing_publication_raises(self, db):
        # No publication row — but render manifest must still exist for
        # the eligibility check to reach the publication check
        _insert_render_manifest(db, render_id=1)
        orch = AnalyticsOrchestrator(db, FakeAnalyticsProvider())
        with pytest.raises(PublicationIneligibleError) as exc_info:
            _ingest(orch, db, publication_id=999)
        assert exc_info.value.publication_id == 999

    def test_failed_status_raises(self, db):
        _seed_eligible(db, publication_id=12)
        db.execute("PRAGMA foreign_keys = OFF")
        db.execute("UPDATE publications SET status='failed' WHERE id=12")
        db.execute("PRAGMA foreign_keys = ON")
        db.commit()
        orch = AnalyticsOrchestrator(db, FakeAnalyticsProvider())
        with pytest.raises(PublicationIneligibleError):
            _ingest(orch, db, publication_id=12)

    def test_deleted_status_raises(self, db):
        _seed_eligible(db, publication_id=13)
        db.execute("PRAGMA foreign_keys = OFF")
        db.execute("UPDATE publications SET status='deleted' WHERE id=13")
        db.execute("PRAGMA foreign_keys = ON")
        db.commit()
        orch = AnalyticsOrchestrator(db, FakeAnalyticsProvider())
        with pytest.raises(PublicationIneligibleError):
            _ingest(orch, db, publication_id=13)

    def test_empty_provider_video_id_raises(self, db):
        _insert_render_manifest(db, render_id=3)
        _insert_publication(db, publication_id=14, provider_video_id="", render_id=3)
        orch = AnalyticsOrchestrator(db, FakeAnalyticsProvider())
        with pytest.raises(PublicationIneligibleError):
            _ingest(orch, db, publication_id=14, render_manifest_id=3)

    def test_provider_mismatch_raises(self, db):
        _insert_render_manifest(db, render_id=4)
        _insert_publication(db, publication_id=15, provider="youtube", render_id=4)
        orch = AnalyticsOrchestrator(db, FakeAnalyticsProvider())  # fake != youtube
        with pytest.raises(PublicationIneligibleError):
            _ingest(orch, db, publication_id=15, render_manifest_id=4)

    def test_unapproved_render_raises(self, db):
        _insert_publication(db, publication_id=16)
        db.execute("PRAGMA foreign_keys = OFF")
        db.execute(
            "INSERT OR IGNORE INTO render_manifests "
            "(id, scene_manifest_id, narration_run_id, caption_run_id, topic_id, "
            "plan_id, script_id, input_hash, render_schema_version, compositor_version, "
            "status, created_at, updated_at) "
            "VALUES (5, 1, 1, 1, 1, 1, 1, 'rh5', '1.0.0', '1.0.0', 'draft', "
            "datetime('now'), datetime('now'))"
        )
        db.execute("PRAGMA foreign_keys = ON")
        db.commit()
        orch = AnalyticsOrchestrator(db, FakeAnalyticsProvider())
        with pytest.raises(PublicationIneligibleError):
            _ingest(orch, db, publication_id=16, render_manifest_id=5)

    def test_superseded_render_raises(self, db):
        _seed_eligible(db, publication_id=17, render_id=6)
        db.execute("UPDATE render_manifests SET superseded_at=datetime('now') WHERE id=6")
        db.commit()
        orch = AnalyticsOrchestrator(db, FakeAnalyticsProvider())
        with pytest.raises(PublicationIneligibleError):
            _ingest(orch, db, publication_id=17, render_manifest_id=6)

    def test_missing_render_manifest_raises(self, db):
        _insert_publication(db, publication_id=18)
        orch = AnalyticsOrchestrator(db, FakeAnalyticsProvider())
        with pytest.raises(PublicationIneligibleError):
            _ingest(orch, db, publication_id=18, render_manifest_id=9999)


class TestCurrencyContract:
    def test_non_monetary_ingest_requires_no_currency(self, seeded_db):
        p = FakeAnalyticsProvider(metrics={METRIC_VIEWS: 100.0})
        orch = AnalyticsOrchestrator(seeded_db, p)
        snap, _ = _ingest(orch, seeded_db)
        assert snap.currency_code is None

    def test_monetary_metrics_require_currency(self, seeded_db):
        p = FakeAnalyticsProvider(metrics={METRIC_REVENUE_ESTIMATE: 1.50})
        orch = AnalyticsOrchestrator(seeded_db, p)
        with pytest.raises(MissingCurrencyError):
            _ingest(orch, seeded_db)

    def test_monetary_with_currency_passes(self, seeded_db):
        p = FakeAnalyticsProvider(metrics={METRIC_REVENUE_ESTIMATE: 1.50})
        orch = AnalyticsOrchestrator(seeded_db, p)
        snap, metrics = _ingest(orch, seeded_db, currency_code="USD")
        assert snap.currency_code == "USD"
        revenue_metrics = [m for m in metrics if m.metric_name == METRIC_REVENUE_ESTIMATE]
        assert len(revenue_metrics) == 1
        assert revenue_metrics[0].metric_value == pytest.approx(1.50)

    def test_currency_code_stored_on_snapshot(self, seeded_db):
        p = FakeAnalyticsProvider(metrics={METRIC_REVENUE_ESTIMATE: 2.00})
        orch = AnalyticsOrchestrator(seeded_db, p)
        snap, _ = _ingest(orch, seeded_db, currency_code="EUR")
        assert snap.currency_code == "EUR"


class TestOrchestratorAggregate:
    def test_aggregate_does_not_raise(self, orch, seeded_db):
        _ingest(orch, seeded_db)
        orch.aggregate(publication_id=1, topic_id=1)

    def test_aggregate_creates_rollup_rows(self, orch, seeded_db):
        _ingest(orch, seeded_db)
        orch.aggregate(publication_id=1, topic_id=1)
        from app.analytics.repository import list_aggregates
        aggs = list_aggregates(seeded_db, publication_id=1)
        assert len(aggs) > 0

    def test_aggregate_stores_calculation_method(self, orch, seeded_db):
        from app.analytics.constants import CALC_METHOD_SUM, METRIC_VIEWS
        p = FakeAnalyticsProvider(metrics={METRIC_VIEWS: 100.0})
        orch2 = AnalyticsOrchestrator(seeded_db, p)
        _ingest(orch2, seeded_db)
        orch2.aggregate(publication_id=1, topic_id=1)
        from app.analytics.repository import list_aggregates
        aggs = list_aggregates(seeded_db, publication_id=1, metric_name=METRIC_VIEWS)
        assert any(a.calculation_method == CALC_METHOD_SUM for a in aggs)

    def test_aggregate_stores_source_snapshot_ids(self, orch, seeded_db):
        snap, _ = _ingest(orch, seeded_db)
        orch.aggregate(publication_id=1, topic_id=1)
        from app.analytics.repository import list_aggregates
        aggs = list_aggregates(seeded_db, publication_id=1)
        for agg in aggs:
            assert isinstance(agg.source_snapshot_ids, list)
            assert snap.id in agg.source_snapshot_ids

    def test_monetary_aggregate_carries_currency(self, seeded_db):
        p = FakeAnalyticsProvider(metrics={METRIC_REVENUE_ESTIMATE: 5.00})
        orch = AnalyticsOrchestrator(seeded_db, p)
        _ingest(orch, seeded_db, currency_code="USD")
        orch.aggregate(publication_id=1, topic_id=1)
        from app.analytics.repository import list_aggregates
        aggs = list_aggregates(
            seeded_db, publication_id=1, metric_name=METRIC_REVENUE_ESTIMATE
        )
        assert all(a.currency_code == "USD" for a in aggs)


class TestOrchestratorReview:
    def test_record_review_returns_event(self, orch, seeded_db):
        snap, _ = _ingest(orch, seeded_db)
        ev = orch.record_review(
            snapshot_id=snap.id, severity="info", notes="fine", reviewer="alice"
        )
        assert ev.severity == "info"
        assert ev.reviewer == "alice"

    def test_other_severity_requires_notes(self, orch, seeded_db):
        snap, _ = _ingest(orch, seeded_db)
        with pytest.raises(ReviewNotesRequiredError):
            orch.record_review(snapshot_id=snap.id, severity="other", notes="")

    def test_other_severity_with_notes_passes(self, orch, seeded_db):
        snap, _ = _ingest(orch, seeded_db)
        ev = orch.record_review(snapshot_id=snap.id, severity="other", notes="explaining this")
        assert ev.severity == "other"

    def test_invalid_severity_raises(self, orch, seeded_db):
        snap, _ = _ingest(orch, seeded_db)
        with pytest.raises(ValueError):
            orch.record_review(snapshot_id=snap.id, severity="debug")


class TestOrchestratorListSnapshots:
    def test_list_returns_ingested(self, orch, seeded_db):
        _ingest(orch, seeded_db, publication_id=1)
        result = orch.list_snapshots(publication_id=1)
        assert len(result) == 1

    def test_list_empty_before_ingest(self, orch):
        assert orch.list_snapshots() == []


class TestOrchestratorListReviewEvents:
    def test_list_returns_created_events(self, orch, seeded_db):
        snap, _ = _ingest(orch, seeded_db)
        orch.record_review(snapshot_id=snap.id, severity="info", notes="", reviewer="")
        events = orch.list_review_events(snap.id)
        assert len(events) == 1

    def test_list_all_events(self, orch, seeded_db):
        snap, _ = _ingest(orch, seeded_db)
        orch.record_review(snapshot_id=snap.id, severity="info", notes="", reviewer="")
        all_events = orch.list_review_events()
        assert len(all_events) >= 1
