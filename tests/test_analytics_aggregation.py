"""Tests for the analytics aggregation engine."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.analytics.aggregation import (
    AggregationResult,
    aggregate_all_periods,
    aggregate_publication,
)
from app.analytics.constants import PERIOD_DAILY, PERIOD_LIFETIME
from app.analytics.errors import UnknownPeriodTypeError
from app.analytics.repository import create_metric, create_snapshot, list_aggregates
from app.core.database import open_db


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / "test.db")


def _snap(conn, h="s1"):
    return create_snapshot(
        conn,
        publication_id=1,
        publishing_plan_id=1,
        publishing_job_id=1,
        render_manifest_id=1,
        scene_manifest_id=1,
        production_plan_id=1,
        script_id=1,
        topic_id=1,
        narration_run_id=1,
        caption_run_id=1,
        experiment_id=None,
        provider="fake",
        provider_version="1.0.0",
        adapter_version="1.0.0",
        raw_metrics={},
        input_hash=h,
    )


def _metric(conn, snap_id, name, value, period_start=None, hash_="mh"):
    return create_metric(
        conn,
        snapshot_id=snap_id,
        publication_id=1,
        topic_id=1,
        provider="fake",
        metric_name=name,
        metric_value=value,
        period_start=period_start,
        period_end=None,
        input_hash=hash_,
    )


class TestAggregatePublication:
    def test_returns_aggregation_result(self, db):
        snap = _snap(db)
        _metric(db, snap.id, "views", 100.0, hash_="mh1")
        result = aggregate_publication(
            db,
            publication_id=1,
            topic_id=1,
            provider="fake",
            period_type=PERIOD_LIFETIME,
        )
        assert isinstance(result, AggregationResult)
        assert result.publication_id == 1

    def test_invalid_period_raises(self, db):
        with pytest.raises(UnknownPeriodTypeError):
            aggregate_publication(
                db,
                publication_id=1,
                topic_id=1,
                provider="fake",
                period_type="quarterly",
            )

    def test_lifetime_sums_views_across_different_periods(self, db):
        """Views from different base periods are summed into the lifetime bucket."""
        s1 = _snap(db, "s1")
        s2 = _snap(db, "s2")
        _metric(db, s1.id, "views", 100.0, period_start="2026-08-01T00:00:00", hash_="m1")
        _metric(db, s2.id, "views", 200.0, period_start="2026-08-02T00:00:00", hash_="m2")
        result = aggregate_publication(
            db,
            publication_id=1,
            topic_id=1,
            provider="fake",
            period_type=PERIOD_LIFETIME,
        )
        views_agg = next(a for a in result.aggregates if a.metric_name == "views")
        assert views_agg.metric_value == 300.0

    def test_lifetime_uses_last_ctr(self, db):
        """CTR is a provider-computed ratio — the most recently ingested value wins."""
        s1 = _snap(db, "s1")
        s2 = _snap(db, "s2")
        _metric(db, s1.id, "ctr", 0.10, hash_="m1")
        _metric(db, s2.id, "ctr", 0.20, hash_="m2")
        result = aggregate_publication(
            db,
            publication_id=1,
            topic_id=1,
            provider="fake",
            period_type=PERIOD_LIFETIME,
        )
        ctr_agg = next(a for a in result.aggregates if a.metric_name == "ctr")
        assert ctr_agg.metric_value == pytest.approx(0.20)

    def test_lifetime_uses_last_likes(self, db):
        """Likes is a cumulative gauge — the most recent snapshot is authoritative."""
        s1 = _snap(db, "s1")
        s2 = _snap(db, "s2")
        _metric(db, s1.id, "likes", 50.0, hash_="m1")
        _metric(db, s2.id, "likes", 45.0, hash_="m2")  # correction down
        result = aggregate_publication(
            db,
            publication_id=1,
            topic_id=1,
            provider="fake",
            period_type=PERIOD_LIFETIME,
        )
        likes_agg = next(a for a in result.aggregates if a.metric_name == "likes")
        assert likes_agg.metric_value == pytest.approx(45.0)

    def test_no_double_counting_on_reingest(self, db):
        """Additive metrics must not double-count when same period is re-ingested."""
        s1 = _snap(db, "s1")
        s2 = _snap(db, "s2")
        _metric(db, s1.id, "views", 100.0, period_start="2026-08-01T00:00:00", hash_="m1")
        _metric(db, s2.id, "views", 150.0, period_start="2026-08-01T00:00:00", hash_="m2")
        result = aggregate_publication(
            db,
            publication_id=1,
            topic_id=1,
            provider="fake",
            period_type=PERIOD_DAILY,
        )
        views_agg = next(a for a in result.aggregates if a.metric_name == "views")
        # s2 is the last ingest for this period → 150 wins, not 100+150=250
        assert views_agg.metric_value == pytest.approx(150.0)

    def test_snapshot_count_correct(self, db):
        s1 = _snap(db, "s1")
        s2 = _snap(db, "s2")
        _metric(db, s1.id, "views", 100.0, hash_="m1")
        _metric(db, s2.id, "views", 100.0, hash_="m2")
        result = aggregate_publication(
            db,
            publication_id=1,
            topic_id=1,
            provider="fake",
            period_type=PERIOD_LIFETIME,
        )
        views_agg = next(a for a in result.aggregates if a.metric_name == "views")
        assert views_agg.snapshot_count == 2

    def test_no_metrics_returns_empty_aggregates(self, db):
        result = aggregate_publication(
            db,
            publication_id=99,
            topic_id=1,
            provider="fake",
            period_type=PERIOD_LIFETIME,
        )
        assert result.aggregates == []

    def test_daily_grouping_by_period_key(self, db):
        s1 = _snap(db, "s1")
        s2 = _snap(db, "s2")
        _metric(db, s1.id, "views", 50.0, period_start="2026-08-01T00:00:00", hash_="m1")
        _metric(db, s2.id, "views", 70.0, period_start="2026-08-02T00:00:00", hash_="m2")
        result = aggregate_publication(
            db,
            publication_id=1,
            topic_id=1,
            provider="fake",
            period_type=PERIOD_DAILY,
        )
        keys = {a.period_key for a in result.aggregates if a.metric_name == "views"}
        assert "2026-08-01" in keys
        assert "2026-08-02" in keys

    def test_provider_isolation(self, db):
        s_fake = create_snapshot(
            db,
            publication_id=1,
            publishing_plan_id=1,
            publishing_job_id=1,
            render_manifest_id=1,
            scene_manifest_id=1,
            production_plan_id=1,
            script_id=1,
            topic_id=1,
            narration_run_id=1,
            caption_run_id=1,
            experiment_id=None,
            provider="fake",
            provider_version="1.0.0",
            adapter_version="1.0.0",
            raw_metrics={},
            input_hash="sfake",
        )
        s_yt = create_snapshot(
            db,
            publication_id=1,
            publishing_plan_id=1,
            publishing_job_id=1,
            render_manifest_id=1,
            scene_manifest_id=1,
            production_plan_id=1,
            script_id=1,
            topic_id=1,
            narration_run_id=1,
            caption_run_id=1,
            experiment_id=None,
            provider="youtube",
            provider_version="1.0.0",
            adapter_version="1.0.0",
            raw_metrics={},
            input_hash="syt",
        )
        create_metric(
            db,
            snapshot_id=s_fake.id,
            publication_id=1,
            topic_id=1,
            provider="fake",
            metric_name="views",
            metric_value=100.0,
            period_start=None,
            period_end=None,
            input_hash="mf1",
        )
        create_metric(
            db,
            snapshot_id=s_yt.id,
            publication_id=1,
            topic_id=1,
            provider="youtube",
            metric_name="views",
            metric_value=999.0,
            period_start=None,
            period_end=None,
            input_hash="my1",
        )

        result = aggregate_publication(
            db,
            publication_id=1,
            topic_id=1,
            provider="fake",
            period_type=PERIOD_LIFETIME,
        )
        views_agg = next(a for a in result.aggregates if a.metric_name == "views")
        assert views_agg.metric_value == 100.0


class TestAggregateAllPeriods:
    def test_returns_four_results(self, db):
        snap = _snap(db)
        _metric(db, snap.id, "views", 1000.0, period_start="2026-08-01T00:00:00", hash_="m1")
        results = aggregate_all_periods(db, publication_id=1, topic_id=1, provider="fake")
        assert len(results) == 4
        period_types = {r.period_type for r in results}
        assert period_types == {"daily", "weekly", "monthly", "lifetime"}

    def test_aggregates_persisted(self, db):
        snap = _snap(db)
        _metric(db, snap.id, "views", 500.0, hash_="m1")
        aggregate_all_periods(db, publication_id=1, topic_id=1, provider="fake")
        aggs = list_aggregates(db, publication_id=1, metric_name="views")
        assert len(aggs) >= 1

    def test_idempotent_reruns(self, db):
        snap = _snap(db)
        _metric(db, snap.id, "views", 100.0, hash_="m1")
        aggregate_all_periods(db, publication_id=1, topic_id=1, provider="fake")
        aggregate_all_periods(db, publication_id=1, topic_id=1, provider="fake")
        aggs = list_aggregates(
            db, publication_id=1, period_type=PERIOD_LIFETIME, metric_name="views"
        )
        assert len(aggs) == 1
        assert aggs[0].metric_value == 100.0
