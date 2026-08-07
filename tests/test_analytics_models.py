"""Tests for analytics domain models."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.analytics.models import (
    AnalyticsHandoff,
    AnalyticsIngestDraft,
    AnalyticsMetric,
    AnalyticsReviewEvent,
    AnalyticsSnapshot,
)
from app.analytics.repository import (
    create_metric,
    create_review_event,
    create_snapshot,
    upsert_aggregate,
)
from app.core.database import open_db


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / "test.db")


def _snap(conn, input_hash="h1", **kw):
    defaults = dict(
        publication_id=1, publishing_plan_id=1, publishing_job_id=1,
        render_manifest_id=1, scene_manifest_id=1, production_plan_id=1,
        script_id=1, topic_id=1, narration_run_id=1, caption_run_id=1,
        experiment_id=None, provider="fake", provider_version="1.0.0",
        adapter_version="1.0.0", raw_metrics={"views": 100},
        input_hash=input_hash,
    )
    defaults.update(kw)
    return create_snapshot(conn, **defaults)


class TestAnalyticsSnapshot:
    def test_from_row_round_trip(self, db):
        snap = _snap(db)
        assert isinstance(snap, AnalyticsSnapshot)
        assert snap.id >= 1

    def test_raw_metrics_property(self, db):
        snap = _snap(db, raw_metrics={"views": 999, "likes": 50})
        assert snap.raw_metrics == {"views": 999, "likes": 50}

    def test_frozen(self, db):
        snap = _snap(db)
        with pytest.raises((TypeError, ValidationError)):
            snap.id = 99  # type: ignore[misc]

    def test_provider_stored(self, db):
        snap = _snap(db, provider="fake")
        assert snap.provider == "fake"

    def test_attribution_fields_stored(self, db):
        snap = _snap(db, publication_id=42, topic_id=7)
        assert snap.publication_id == 42
        assert snap.topic_id == 7


class TestAnalyticsMetric:
    def test_from_row_round_trip(self, db):
        snap = _snap(db)
        m = create_metric(
            db,
            snapshot_id=snap.id,
            publication_id=1,
            topic_id=1,
            provider="fake",
            metric_name="views",
            metric_value=1234.0,
            period_start=None,
            period_end=None,
            input_hash="mhash1",
        )
        assert isinstance(m, AnalyticsMetric)
        assert m.metric_name == "views"
        assert m.metric_value == 1234.0

    def test_frozen(self, db):
        snap = _snap(db)
        m = create_metric(
            db,
            snapshot_id=snap.id,
            publication_id=1,
            topic_id=1,
            provider="fake",
            metric_name="likes",
            metric_value=50.0,
            period_start=None,
            period_end=None,
            input_hash="mhash2",
        )
        with pytest.raises((TypeError, ValidationError)):
            m.metric_value = 99.0  # type: ignore[misc]


class TestAnalyticsAggregate:
    def test_upsert_and_retrieve(self, db):
        agg = upsert_aggregate(
            db,
            publication_id=1, topic_id=1, provider="fake",
            period_type="lifetime", period_key="all",
            metric_name="views", metric_value=5000.0,
            snapshot_count=5, input_hash="ahash1",
            calculation_method="sum", source_snapshot_ids=[1, 2, 3, 4, 5],
        )
        assert agg.metric_value == 5000.0
        assert agg.snapshot_count == 5
        assert agg.period_type == "lifetime"
        assert agg.calculation_method == "sum"

    def test_upsert_overwrites(self, db):
        upsert_aggregate(
            db, publication_id=1, topic_id=1, provider="fake",
            period_type="lifetime", period_key="all",
            metric_name="views", metric_value=1000.0,
            snapshot_count=1, input_hash="ah1",
            calculation_method="sum", source_snapshot_ids=[1],
        )
        agg = upsert_aggregate(
            db, publication_id=1, topic_id=1, provider="fake",
            period_type="lifetime", period_key="all",
            metric_name="views", metric_value=9999.0,
            snapshot_count=10, input_hash="ah2",
            calculation_method="sum", source_snapshot_ids=[1, 2],
        )
        assert agg.metric_value == 9999.0
        assert agg.snapshot_count == 10


class TestAnalyticsReviewEvent:
    def test_from_row_round_trip(self, db):
        snap = _snap(db)
        ev = create_review_event(
            db, snapshot_id=snap.id, severity="info",
            notes="looks fine", reviewer="alice", input_hash="rh1",
        )
        assert isinstance(ev, AnalyticsReviewEvent)
        assert ev.severity == "info"
        assert ev.notes == "looks fine"

    def test_frozen(self, db):
        snap = _snap(db)
        ev = create_review_event(
            db, snapshot_id=snap.id, severity="warning",
            notes="check this", reviewer="bob", input_hash="rh2",
        )
        with pytest.raises((TypeError, ValidationError)):
            ev.severity = "error"  # type: ignore[misc]


class TestIngestDraft:
    def test_defaults(self):
        d = AnalyticsIngestDraft(
            publication_id=1, publishing_plan_id=2, publishing_job_id=3,
            render_manifest_id=4, scene_manifest_id=5, production_plan_id=6,
            script_id=7, topic_id=8, narration_run_id=9, caption_run_id=10,
            provider="fake", provider_version="1.0.0", adapter_version="1.0.0",
        )
        assert d.raw_metrics == {}
        assert d.experiment_id is None
        assert d.period_start is None


class TestAnalyticsHandoff:
    def test_handoff_construction(self, db):
        snap = _snap(db)
        m = create_metric(
            db, snapshot_id=snap.id, publication_id=1, topic_id=1,
            provider="fake", metric_name="views", metric_value=500.0,
            period_start=None, period_end=None, input_hash="hh1",
        )
        agg = upsert_aggregate(
            db, publication_id=1, topic_id=1, provider="fake",
            period_type="lifetime", period_key="all",
            metric_name="views", metric_value=500.0,
            snapshot_count=1, input_hash="ha1",
            calculation_method="sum", source_snapshot_ids=[snap.id],
        )
        ev = create_review_event(
            db, snapshot_id=snap.id, severity="info",
            notes="ok", reviewer="reviewer", input_hash="hr1",
        )
        handoff = AnalyticsHandoff(
            publication_id=1,
            topic_id=1,
            provider="fake",
            publishing_plan_id=1,
            publishing_job_id=1,
            render_manifest_id=1,
            scene_manifest_id=1,
            production_plan_id=1,
            script_id=1,
            narration_run_id=1,
            caption_run_id=1,
            experiment_id=None,
            snapshots=[snap],
            metrics=[m],
            aggregates=[agg],
            review_events=[ev],
            currency_code=None,
            engine_version="1.0.0",
            analytics_schema_version="1.0.0",
            handoff_created_at="2026-08-06T00:00:00Z",
        )
        assert handoff.publication_id == 1
        assert len(handoff.snapshots) == 1
        assert len(handoff.metrics) == 1
        assert len(handoff.aggregates) == 1
        assert len(handoff.review_events) == 1

    def test_handoff_is_frozen(self, db):
        handoff = AnalyticsHandoff(
            publication_id=1, topic_id=1, provider="fake",
            publishing_plan_id=1, publishing_job_id=1,
            render_manifest_id=1, scene_manifest_id=1,
            production_plan_id=1, script_id=1,
            narration_run_id=1, caption_run_id=1,
            experiment_id=None,
            snapshots=[], metrics=[], aggregates=[], review_events=[],
            currency_code=None,
            engine_version="1.0.0", analytics_schema_version="1.0.0",
            handoff_created_at="2026-08-06T00:00:00Z",
        )
        with pytest.raises((TypeError, ValidationError)):
            handoff.publication_id = 99  # type: ignore[misc]

    def test_handoff_full_attribution_chain(self, db):
        handoff = AnalyticsHandoff(
            publication_id=7, topic_id=3, provider="fake",
            publishing_plan_id=10, publishing_job_id=20,
            render_manifest_id=30, scene_manifest_id=40,
            production_plan_id=50, script_id=60,
            narration_run_id=70, caption_run_id=80,
            experiment_id="exp-abc",
            snapshots=[], metrics=[], aggregates=[], review_events=[],
            currency_code=None,
            engine_version="1.0.0", analytics_schema_version="1.0.0",
            handoff_created_at="2026-08-06T00:00:00Z",
        )
        assert handoff.publishing_plan_id == 10
        assert handoff.publishing_job_id == 20
        assert handoff.experiment_id == "exp-abc"
