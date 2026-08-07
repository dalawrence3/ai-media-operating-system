"""Tests for the analytics repository layer."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.analytics.errors import (
    DuplicateSnapshotError,
    MetricNotFoundError,
    ReviewEventNotFoundError,
    SnapshotNotFoundError,
)
from app.analytics.repository import (
    create_metric,
    create_review_event,
    create_snapshot,
    get_metric,
    get_review_event,
    get_snapshot,
    get_snapshot_by_hash,
    list_aggregates,
    list_metrics_for_publication,
    list_metrics_for_snapshot,
    list_review_events,
    list_snapshots,
    upsert_aggregate,
)
from app.core.database import open_db


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / "test.db")


def _snap(conn, input_hash: str = "h1", **kw):
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


def _metric(
    conn, snap_id: int, name: str = "views", value: float = 100.0,
    hash_: str = "mh1", publication_id: int = 1,
):
    return create_metric(
        conn, snapshot_id=snap_id, publication_id=publication_id, topic_id=1, provider="fake",
        metric_name=name, metric_value=value,
        period_start=None, period_end=None, input_hash=hash_,
    )


class TestCreateSnapshot:
    def test_creates_with_expected_fields(self, db):
        snap = _snap(db)
        assert snap.id >= 1
        assert snap.provider == "fake"
        assert snap.publication_id == 1

    def test_stores_raw_metrics_json(self, db):
        snap = _snap(db, raw_metrics={"views": 777})
        assert snap.raw_metrics == {"views": 777}

    def test_duplicate_hash_raises(self, db):
        _snap(db, input_hash="same")
        with pytest.raises(DuplicateSnapshotError):
            _snap(db, input_hash="same")

    def test_different_hashes_allowed(self, db):
        s1 = _snap(db, input_hash="h1")
        s2 = _snap(db, input_hash="h2")
        assert s1.id != s2.id

    def test_experiment_id_stored(self, db):
        snap = _snap(db, experiment_id="exp-42")
        assert snap.experiment_id == "exp-42"

    def test_period_fields_stored(self, db):
        snap = _snap(db, period_start="2026-01-01", period_end="2026-01-31")
        assert snap.period_start == "2026-01-01"
        assert snap.period_end == "2026-01-31"


class TestGetSnapshot:
    def test_returns_correct_snapshot(self, db):
        snap = _snap(db)
        fetched = get_snapshot(db, snap.id)
        assert fetched.id == snap.id

    def test_missing_raises(self, db):
        with pytest.raises(SnapshotNotFoundError):
            get_snapshot(db, 99999)


class TestGetSnapshotByHash:
    def test_finds_by_hash(self, db):
        snap = _snap(db, input_hash="findme")
        result = get_snapshot_by_hash(db, "findme")
        assert result is not None
        assert result.id == snap.id

    def test_returns_none_if_missing(self, db):
        assert get_snapshot_by_hash(db, "notexist") is None


class TestListSnapshots:
    def test_returns_newest_first(self, db):
        s1 = _snap(db, input_hash="a")
        s2 = _snap(db, input_hash="b")
        snaps = list_snapshots(db)
        assert snaps[0].id == s2.id
        assert snaps[1].id == s1.id

    def test_filters_by_publication_id(self, db):
        _snap(db, input_hash="a", publication_id=1)
        _snap(db, input_hash="b", publication_id=2)
        result = list_snapshots(db, publication_id=2)
        assert all(s.publication_id == 2 for s in result)

    def test_filters_by_topic_id(self, db):
        _snap(db, input_hash="a", topic_id=10)
        _snap(db, input_hash="b", topic_id=20)
        result = list_snapshots(db, topic_id=10)
        assert all(s.topic_id == 10 for s in result)

    def test_limit_respected(self, db):
        for i in range(5):
            _snap(db, input_hash=f"h{i}")
        result = list_snapshots(db, limit=3)
        assert len(result) <= 3

    def test_empty_returns_empty_list(self, db):
        assert list_snapshots(db) == []


class TestMetrics:
    def test_create_metric(self, db):
        snap = _snap(db)
        m = _metric(db, snap.id, "views", 500.0)
        assert m.metric_name == "views"
        assert m.metric_value == 500.0

    def test_get_metric_missing_raises(self, db):
        with pytest.raises(MetricNotFoundError):
            get_metric(db, 99999)

    def test_list_metrics_for_snapshot(self, db):
        snap = _snap(db)
        _metric(db, snap.id, "views", 100.0, "mh1")
        _metric(db, snap.id, "likes", 10.0, "mh2")
        metrics = list_metrics_for_snapshot(db, snap.id)
        names = {m.metric_name for m in metrics}
        assert "views" in names
        assert "likes" in names

    def test_list_metrics_ordered_by_name(self, db):
        snap = _snap(db)
        _metric(db, snap.id, "views", 1.0, "mh1")
        _metric(db, snap.id, "likes", 2.0, "mh2")
        metrics = list_metrics_for_snapshot(db, snap.id)
        assert metrics[0].metric_name <= metrics[-1].metric_name

    def test_list_metrics_for_publication(self, db):
        snap = _snap(db, publication_id=5)
        _metric(db, snap.id, "views", 200.0, "mh1", publication_id=5)
        result = list_metrics_for_publication(db, 5)
        assert len(result) == 1

    def test_list_metrics_for_publication_by_name(self, db):
        snap = _snap(db)
        _metric(db, snap.id, "views", 100.0, "mh1")
        _metric(db, snap.id, "likes", 20.0, "mh2")
        result = list_metrics_for_publication(db, 1, metric_name="views")
        assert all(m.metric_name == "views" for m in result)


class TestAggregates:
    def test_upsert_creates(self, db):
        agg = upsert_aggregate(
            db, publication_id=1, topic_id=1, provider="fake",
            period_type="lifetime", period_key="all",
            metric_name="views", metric_value=1000.0,
            snapshot_count=2, input_hash="ah1",
            calculation_method="sum", source_snapshot_ids=[1, 2],
        )
        assert agg.metric_value == 1000.0
        assert agg.calculation_method == "sum"

    def test_upsert_updates_on_conflict(self, db):
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
            metric_name="views", metric_value=2000.0,
            snapshot_count=2, input_hash="ah2",
            calculation_method="sum", source_snapshot_ids=[1, 2],
        )
        assert agg.metric_value == 2000.0

    def test_list_aggregates_no_filters(self, db):
        upsert_aggregate(
            db, publication_id=1, topic_id=1, provider="fake",
            period_type="daily", period_key="2026-08-01",
            metric_name="views", metric_value=500.0,
            snapshot_count=1, input_hash="ah1",
            calculation_method="sum", source_snapshot_ids=[1],
        )
        result = list_aggregates(db)
        assert len(result) >= 1

    def test_list_aggregates_filters(self, db):
        upsert_aggregate(
            db, publication_id=10, topic_id=1, provider="fake",
            period_type="monthly", period_key="2026-08",
            metric_name="views", metric_value=100.0,
            snapshot_count=1, input_hash="ah1",
            calculation_method="sum", source_snapshot_ids=[1],
        )
        result = list_aggregates(db, publication_id=10, metric_name="views")
        assert all(a.publication_id == 10 for a in result)


class TestReviewEvents:
    def test_create_event(self, db):
        snap = _snap(db)
        ev = create_review_event(
            db, snapshot_id=snap.id, severity="info",
            notes="all good", reviewer="alice", input_hash="rh1",
        )
        assert ev.severity == "info"
        assert ev.reviewer == "alice"

    def test_get_event_missing_raises(self, db):
        with pytest.raises(ReviewEventNotFoundError):
            get_review_event(db, 99999)

    def test_list_events_for_snapshot(self, db):
        snap = _snap(db)
        create_review_event(
            db, snapshot_id=snap.id, severity="warning",
            notes="check this", reviewer="bob", input_hash="rh1",
        )
        create_review_event(
            db, snapshot_id=snap.id, severity="info",
            notes="", reviewer="", input_hash="rh2",
        )
        events = list_review_events(db, snapshot_id=snap.id)
        assert len(events) == 2

    def test_list_events_by_severity(self, db):
        snap = _snap(db)
        create_review_event(
            db, snapshot_id=snap.id, severity="error",
            notes="bad", reviewer="", input_hash="rh1",
        )
        create_review_event(
            db, snapshot_id=snap.id, severity="info",
            notes="", reviewer="", input_hash="rh2",
        )
        errors = list_review_events(db, severity="error")
        assert all(e.severity == "error" for e in errors)

    def test_events_ordered_newest_first(self, db):
        snap = _snap(db)
        create_review_event(
            db, snapshot_id=snap.id, severity="info", notes="", reviewer="", input_hash="rh1"
        )
        create_review_event(
            db, snapshot_id=snap.id, severity="warning", notes="w", reviewer="", input_hash="rh2"
        )
        events = list_review_events(db, snapshot_id=snap.id)
        assert events[0].id > events[1].id
