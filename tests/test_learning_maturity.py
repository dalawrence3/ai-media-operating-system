"""Tests for the evidence maturity / sufficiency layer (Phase 11).

Covers the maturity evaluator itself and its integration with the
recommendation generator dispatch loop.

Test scenarios:
  A. Zero-view publication → all view-dependent generators skipped
  B. Below-threshold views → same skip behaviour
  C. Sufficient views + low AVD → retention recommendations fire
  D. Sufficient views + healthy AVD → no retention warnings
  E. Zero subscriber change → no subscriber-loss recommendation
  F. Negative subscriber change + sufficient evidence → loss recommendation fires
  G. Idempotency — unchanged evidence does not duplicate recommendations
  H. Seed aggregate filtering remains intact
  I. Recommendation provenance — traces to correct snapshot IDs
  J. Missing metric vs zero metric — remain semantically distinct
"""

from __future__ import annotations

import json
import pathlib
import tempfile
from unittest.mock import MagicMock

import pytest

from app.analytics.constants import (
    METRIC_AVERAGE_VIEW_DURATION,
    METRIC_CTR,
    METRIC_LIKES,
    METRIC_SUBSCRIBERS_GAINED,
    METRIC_SUBSCRIBERS_LOST,
    METRIC_VIEWS,
)
from app.core.database import open_db
from app.learning.constants import (
    GENERATOR_CTR,
    GENERATOR_ENGAGEMENT,
    GENERATOR_RETENTION,
    GENERATOR_STATUS_SKIPPED,
    GENERATOR_STATUS_SUCCEEDED,
    GENERATOR_SUBSCRIBERS,
    MIN_VIEWS_FOR_LEARNING,
)
from app.learning.maturity import (
    REQUIRE_CTR_DATA,
    REQUIRE_NONE,
    REQUIRE_VIEWS,
    MaturityRequirement,
    evaluate_maturity,
)
from app.learning.recommendations import (
    generate_all_recommendations,
    generate_retention_recommendations,
    generate_subscriber_recommendations,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as d:
        conn = open_db(pathlib.Path(d) / "test.db")
        conn.execute("INSERT INTO topics (title, angle) VALUES ('Test', 'test')")
        conn.commit()
        yield conn


def _make_handoff(publication_id: int = 1, topic_id: int = 1):
    h = MagicMock()
    h.publication_id = publication_id
    h.topic_id = topic_id
    h.experiment_id = None
    h.script_id = 2
    h.narration_run_id = 3
    h.caption_run_id = 4
    h.scene_manifest_id = 5
    h.render_manifest_id = 6
    h.publishing_plan_id = 7
    h.publishing_job_id = 8
    h.production_plan_id = 10
    return h


def _insert_aggregate(
    conn,
    publication_id: int,
    metric_name: str,
    value: float,
    snapshot_ids: list[int] | None = None,
    input_hash: str | None = None,
) -> None:
    """Insert a real (non-seed) lifetime aggregate row."""
    conn.execute(
        """
        INSERT INTO analytics_aggregates
            (publication_id, topic_id, provider, period_type, period_key,
             metric_name, metric_value, snapshot_count, calculation_method,
             currency_code, source_snapshot_ids_json, input_hash, created_at)
        VALUES (?,1,'fake','lifetime','lifetime',?,?,1,'sum',NULL,?,?,
                strftime('%Y-%m-%dT%H:%M:%S','now'))
        """,
        (
            publication_id,
            metric_name,
            value,
            json.dumps(snapshot_ids or [1]),
            input_hash or f"real_{metric_name}_{value}",
        ),
    )
    conn.commit()


def _insert_seed_aggregate(
    conn,
    publication_id: int,
    metric_name: str,
    value: float,
) -> None:
    """Insert a seed aggregate row (input_hash starts with 'seed-')."""
    conn.execute(
        """
        INSERT INTO analytics_aggregates
            (publication_id, topic_id, provider, period_type, period_key,
             metric_name, metric_value, snapshot_count, calculation_method,
             currency_code, source_snapshot_ids_json, input_hash, created_at)
        VALUES (?,1,'fake','lifetime','lifetime',?,?,1,'sum',NULL,'[99]',?,
                strftime('%Y-%m-%dT%H:%M:%S','now'))
        """,
        (publication_id, metric_name, value, f"seed-{metric_name}-001"),
    )
    conn.commit()


# ── A / B: Zero / below-threshold views ──────────────────────────────────────


class TestZeroViewPublication:
    """A. Zero-view publication: all view-dependent generators must be skipped."""

    def test_maturity_fails_at_zero_views(self, db):
        result = evaluate_maturity(db, publication_id=1, requirement=REQUIRE_VIEWS)
        assert not result.sufficient
        assert "0" in result.reason
        assert result.observed_views == 0.0
        assert result.required_views == MIN_VIEWS_FOR_LEARNING

    def test_maturity_fails_when_no_aggregate_at_all(self, db):
        # No rows at all → treated identically to 0 views
        result = evaluate_maturity(db, publication_id=1, requirement=REQUIRE_VIEWS)
        assert not result.sufficient
        assert result.observed_views == 0.0

    def test_retention_generator_skipped_at_zero_views(self, db):
        # Even with low AVD, retention must not fire without sufficient views
        _insert_aggregate(db, 1, METRIC_AVERAGE_VIEW_DURATION, 2.0)
        results = generate_all_recommendations(db, _make_handoff(), learning_run_id=1)
        retention_gr = next(
            gr for gr in results.generator_results if gr.generator_name == GENERATOR_RETENTION
        )
        assert retention_gr.status == GENERATOR_STATUS_SKIPPED
        assert not any(d.domain == "scripts" for d in results.drafts)
        assert not any(d.domain == "narration" for d in results.drafts)

    def test_subscriber_generator_skipped_at_zero_views(self, db):
        _insert_aggregate(db, 1, METRIC_SUBSCRIBERS_GAINED, 0.0)
        _insert_aggregate(db, 1, METRIC_SUBSCRIBERS_LOST, 0.0)
        results = generate_all_recommendations(db, _make_handoff(), learning_run_id=1)
        sub_gr = next(
            gr for gr in results.generator_results if gr.generator_name == GENERATOR_SUBSCRIBERS
        )
        assert sub_gr.status == GENERATOR_STATUS_SKIPPED

    def test_engagement_generator_skipped_at_zero_views(self, db):
        _insert_aggregate(db, 1, METRIC_LIKES, 0.0)
        results = generate_all_recommendations(db, _make_handoff(), learning_run_id=1)
        eng_gr = next(
            gr for gr in results.generator_results if gr.generator_name == GENERATOR_ENGAGEMENT
        )
        assert eng_gr.status == GENERATOR_STATUS_SKIPPED

    def test_learning_run_completes_when_all_generators_skip(self, db):
        # run should succeed (not fail) even when all generators are skipped
        results = generate_all_recommendations(db, _make_handoff(), learning_run_id=1)
        assert all(gr.status == GENERATOR_STATUS_SKIPPED for gr in results.generator_results)
        assert results.drafts == []

    def test_skip_reason_is_recorded_in_error_message(self, db):
        results = generate_all_recommendations(db, _make_handoff(), learning_run_id=1)
        retention_gr = next(
            gr for gr in results.generator_results if gr.generator_name == GENERATOR_RETENTION
        )
        assert retention_gr.error_message is not None
        assert "views" in retention_gr.error_message.lower()


class TestBelowThresholdViews:
    """B. Below-threshold views (non-zero but < MIN_VIEWS_FOR_LEARNING) → same skips."""

    def test_maturity_fails_below_threshold(self, db):
        _insert_aggregate(db, 1, METRIC_VIEWS, MIN_VIEWS_FOR_LEARNING - 1)
        result = evaluate_maturity(db, publication_id=1, requirement=REQUIRE_VIEWS)
        assert not result.sufficient
        assert result.observed_views == MIN_VIEWS_FOR_LEARNING - 1

    def test_generators_skip_below_threshold(self, db):
        _insert_aggregate(db, 1, METRIC_VIEWS, MIN_VIEWS_FOR_LEARNING - 1)
        _insert_aggregate(db, 1, METRIC_AVERAGE_VIEW_DURATION, 5.0)
        results = generate_all_recommendations(db, _make_handoff(), learning_run_id=1)
        retention_gr = next(
            gr for gr in results.generator_results if gr.generator_name == GENERATOR_RETENTION
        )
        assert retention_gr.status == GENERATOR_STATUS_SKIPPED


# ── C / D: Sufficient views + retention thresholds ───────────────────────────


class TestSufficientViewsRetention:
    """C. Sufficient views + low AVD → retention recommendations fire."""

    def test_maturity_passes_at_threshold(self, db):
        _insert_aggregate(db, 1, METRIC_VIEWS, MIN_VIEWS_FOR_LEARNING)
        result = evaluate_maturity(db, publication_id=1, requirement=REQUIRE_VIEWS)
        assert result.sufficient

    def test_maturity_passes_above_threshold(self, db):
        _insert_aggregate(db, 1, METRIC_VIEWS, 100.0)
        result = evaluate_maturity(db, publication_id=1, requirement=REQUIRE_VIEWS)
        assert result.sufficient

    def test_low_avd_fires_when_sufficient_views(self, db):
        _insert_aggregate(db, 1, METRIC_VIEWS, 50.0)
        _insert_aggregate(db, 1, METRIC_AVERAGE_VIEW_DURATION, 5.0)  # < 20s threshold
        drafts = generate_retention_recommendations(db, _make_handoff(), learning_run_id=1)
        assert any(d.domain == "scripts" for d in drafts)
        assert any(d.domain == "narration" for d in drafts)

    def test_retention_generator_succeeds_with_sufficient_views(self, db):
        _insert_aggregate(db, 1, METRIC_VIEWS, 50.0)
        _insert_aggregate(db, 1, METRIC_AVERAGE_VIEW_DURATION, 5.0)
        results = generate_all_recommendations(db, _make_handoff(), learning_run_id=1)
        ret_gr = next(
            gr for gr in results.generator_results if gr.generator_name == GENERATOR_RETENTION
        )
        assert ret_gr.status == GENERATOR_STATUS_SUCCEEDED
        assert ret_gr.recommendation_count == 2


class TestSufficientViewsHealthyRetention:
    """D. Sufficient views + healthy AVD → no retention warnings."""

    def test_high_avd_no_warning_recommendations(self, db):
        _insert_aggregate(db, 1, METRIC_VIEWS, 50.0)
        _insert_aggregate(db, 1, METRIC_AVERAGE_VIEW_DURATION, 50.0)  # > 45s threshold
        drafts = generate_retention_recommendations(db, _make_handoff(), learning_run_id=1)
        # Only positive-reinforcement recommendation — no warning drafts
        assert not any("Low retention" in d.title for d in drafts)

    def test_mid_range_avd_no_recommendations(self, db):
        _insert_aggregate(db, 1, METRIC_VIEWS, 50.0)
        _insert_aggregate(db, 1, METRIC_AVERAGE_VIEW_DURATION, 30.0)  # between thresholds
        drafts = generate_retention_recommendations(db, _make_handoff(), learning_run_id=1)
        assert drafts == []


# ── E / F: Subscriber semantics ──────────────────────────────────────────────


class TestSubscriberSemantics:
    """E. Zero subscriber change must NOT trigger a loss recommendation."""

    def test_zero_net_change_does_not_fire(self, db):
        _insert_aggregate(db, 1, METRIC_VIEWS, 50.0)
        _insert_aggregate(db, 1, METRIC_SUBSCRIBERS_GAINED, 0.0)
        _insert_aggregate(db, 1, METRIC_SUBSCRIBERS_LOST, 0.0)
        drafts = generate_subscriber_recommendations(db, _make_handoff(), learning_run_id=1)
        assert not any("loss" in d.title.lower() for d in drafts)

    def test_zero_gained_zero_lost_is_not_negative(self, db):
        _insert_aggregate(db, 1, METRIC_VIEWS, 50.0)
        _insert_aggregate(db, 1, METRIC_SUBSCRIBERS_GAINED, 5.0)
        _insert_aggregate(db, 1, METRIC_SUBSCRIBERS_LOST, 5.0)  # net = 0
        drafts = generate_subscriber_recommendations(db, _make_handoff(), learning_run_id=1)
        assert not any("loss" in d.title.lower() for d in drafts)


class TestSubscriberLoss:
    """F. Strictly negative net + sufficient views → loss recommendation fires."""

    def test_negative_net_fires(self, db):
        _insert_aggregate(db, 1, METRIC_VIEWS, 50.0)
        _insert_aggregate(db, 1, METRIC_SUBSCRIBERS_GAINED, 2.0)
        _insert_aggregate(db, 1, METRIC_SUBSCRIBERS_LOST, 7.0)  # net = -5
        drafts = generate_subscriber_recommendations(db, _make_handoff(), learning_run_id=1)
        assert any("loss" in d.title.lower() for d in drafts)

    def test_negative_net_skipped_without_sufficient_views(self, db):
        # Views = 0, so the maturity gate should block the subscriber generator
        _insert_aggregate(db, 1, METRIC_SUBSCRIBERS_GAINED, 2.0)
        _insert_aggregate(db, 1, METRIC_SUBSCRIBERS_LOST, 7.0)  # net = -5
        results = generate_all_recommendations(db, _make_handoff(), learning_run_id=1)
        sub_gr = next(
            gr for gr in results.generator_results if gr.generator_name == GENERATOR_SUBSCRIBERS
        )
        assert sub_gr.status == GENERATOR_STATUS_SKIPPED
        assert not any("loss" in d.title.lower() for d in results.drafts)


# ── G: Idempotency ────────────────────────────────────────────────────────────


class TestIdempotency:
    """G. Running with unchanged evidence must not produce duplicate recommendations."""

    def _bootstrap_pub_and_snapshot(self, db) -> None:
        """Insert minimum rows for analyze_publication to succeed."""
        db.execute("PRAGMA foreign_keys = OFF")
        db.execute(
            """
            INSERT OR IGNORE INTO render_manifests
                (id, scene_manifest_id, narration_run_id, caption_run_id, topic_id,
                 plan_id, script_id, input_hash, render_schema_version,
                 compositor_version, status, created_at, updated_at)
            VALUES (1,1,1,1,1,1,1,'rmh1','1.0.0','1.0.0','approved',
                    datetime('now'), datetime('now'))
            """
        )
        db.execute(
            """
            INSERT OR IGNORE INTO publications
                (id, publishing_plan_id, publishing_job_id, provider,
                 provider_version, provider_video_id, status,
                 publishing_engine_version, input_hash, output_sha256,
                 created_at, updated_at)
            VALUES (1,1,1,'fake','1.0.0','v1','published','1.0.0','h1','s1',
                    datetime('now'), datetime('now'))
            """
        )
        from app.core.database import SCHEMA_VERSION

        db.execute(
            """
            INSERT OR IGNORE INTO analytics_snapshots
                (id, publication_id, publishing_plan_id, publishing_job_id,
                 render_manifest_id, scene_manifest_id, production_plan_id,
                 script_id, topic_id, narration_run_id, caption_run_id,
                 provider, provider_version, adapter_version, engine_version,
                 analytics_schema_version, db_schema_version, input_hash,
                 raw_metrics_json, period_start, period_end, is_period_complete,
                 currency_code, ingested_at, created_at,
                 observed_at, response_fingerprint, observation_state)
            VALUES (1,1,1,1,1,1,1,1,1,1,1,'fake','1.0.0','1.0.0','1.0.0',
                    '1.0.0',?,
                    'snap1','{}',NULL,NULL,0,NULL,
                    datetime('now'),datetime('now'),
                    datetime('now'),'fp1','data')
            """,
            (SCHEMA_VERSION,),
        )
        db.execute("PRAGMA foreign_keys = ON")
        db.commit()

    def test_double_run_no_duplicates(self, db):
        from app.learning.orchestrator import analyze_publication
        from app.learning.repository import list_recommendations

        self._bootstrap_pub_and_snapshot(db)

        # Insert sufficient data for some generators to fire
        _insert_aggregate(db, 1, METRIC_VIEWS, 50.0, snapshot_ids=[1])
        _insert_aggregate(db, 1, METRIC_AVERAGE_VIEW_DURATION, 5.0, snapshot_ids=[1])
        _insert_aggregate(db, 1, METRIC_SUBSCRIBERS_GAINED, 0.0, snapshot_ids=[1])
        _insert_aggregate(db, 1, METRIC_SUBSCRIBERS_LOST, 5.0, snapshot_ids=[1])

        run1 = analyze_publication(db, publication_id=1, topic_id=1)
        run2 = analyze_publication(db, publication_id=1, topic_id=1)

        recs = list_recommendations(db, publication_id=1, status="pending")
        # Same evidence → run2 must be idempotent (no new drafts)
        assert run1 != run2  # two separate runs

        # All pending recommendations should have unique (domain, subsystem, measure) keys
        keys = [(r.domain, r.subsystem, r.measure) for r in recs]
        assert len(keys) == len(set(keys)), "duplicate recommendations found"


# ── H: Seed aggregate filtering ───────────────────────────────────────────────


class TestSeedFiltering:
    """H. Seed aggregates must not satisfy maturity checks or influence generators."""

    def test_seed_views_do_not_satisfy_maturity(self, db):
        # Insert a seed views aggregate — must not be counted
        _insert_seed_aggregate(db, 1, METRIC_VIEWS, 1000.0)
        result = evaluate_maturity(db, publication_id=1, requirement=REQUIRE_VIEWS)
        assert not result.sufficient
        assert result.observed_views == 0.0

    def test_seed_views_fail_and_real_views_pass_independently(self, db):
        # Seed-only pub (pub_id=1): maturity must fail because seeds are excluded
        _insert_seed_aggregate(db, 1, METRIC_VIEWS, 1000.0)
        seed_result = evaluate_maturity(db, publication_id=1, requirement=REQUIRE_VIEWS)
        assert not seed_result.sufficient, "seed aggregate must not satisfy maturity"

        # Real-only pub (pub_id=2): maturity must pass because real aggregate qualifies
        _insert_aggregate(db, 2, METRIC_VIEWS, 50.0)
        real_result = evaluate_maturity(db, publication_id=2, requirement=REQUIRE_VIEWS)
        assert real_result.sufficient, "real aggregate must satisfy maturity"

    def test_seed_avd_not_used_by_retention_generator(self, db):
        _insert_aggregate(db, 1, METRIC_VIEWS, 50.0)
        _insert_seed_aggregate(db, 1, METRIC_AVERAGE_VIEW_DURATION, 2.0)
        # No real AVD aggregate → generator returns empty
        drafts = generate_retention_recommendations(db, _make_handoff(), learning_run_id=1)
        assert drafts == []


# ── I: Provenance ─────────────────────────────────────────────────────────────


class TestProvenance:
    """I. Recommendations must trace to the correct source snapshot IDs."""

    def test_recommendation_snapshot_ids_match_aggregate_source(self, db):
        expected_snap_ids = [3, 7]
        _insert_aggregate(db, 1, METRIC_VIEWS, 50.0, snapshot_ids=expected_snap_ids)
        _insert_aggregate(db, 1, METRIC_AVERAGE_VIEW_DURATION, 5.0, snapshot_ids=expected_snap_ids)
        drafts = generate_retention_recommendations(db, _make_handoff(), learning_run_id=1)
        assert drafts, "Expected at least one draft"
        for draft in drafts:
            for ev in draft.evidence:
                assert sorted(ev.snapshot_ids) == sorted(expected_snap_ids)


# ── J: Missing metric vs zero metric ─────────────────────────────────────────


class TestMissingVsZeroMetric:
    """J. A missing metric aggregate and a zero-value aggregate are semantically distinct."""

    def test_missing_avd_returns_no_drafts(self, db):
        # No AVD aggregate at all → generator exits early (metric not found)
        _insert_aggregate(db, 1, METRIC_VIEWS, 50.0)
        drafts = generate_retention_recommendations(db, _make_handoff(), learning_run_id=1)
        assert drafts == []

    def test_zero_avd_returns_drafts(self, db):
        # AVD exists but is 0.0 → observation_state='data', infers low retention
        _insert_aggregate(db, 1, METRIC_VIEWS, 50.0)
        _insert_aggregate(db, 1, METRIC_AVERAGE_VIEW_DURATION, 0.0)
        drafts = generate_retention_recommendations(db, _make_handoff(), learning_run_id=1)
        # 0.0 < RETENTION_LOW_THRESHOLD_S(20.0) → retention recommendations fire
        assert any("Low retention" in d.title for d in drafts)

    def test_missing_ctr_triggers_ctr_skip(self, db):
        # No CTR aggregate → CTR generator skipped (required_metrics check)
        _insert_aggregate(db, 1, METRIC_VIEWS, 100.0)
        results = generate_all_recommendations(db, _make_handoff(), learning_run_id=1)
        ctr_gr = next(gr for gr in results.generator_results if gr.generator_name == GENERATOR_CTR)
        assert ctr_gr.status == GENERATOR_STATUS_SKIPPED
        assert "ctr" in (ctr_gr.error_message or "").lower()

    def test_ctr_present_allows_ctr_generator(self, db):
        _insert_aggregate(db, 1, METRIC_CTR, 0.01)  # below threshold → should fire
        result = evaluate_maturity(db, publication_id=1, requirement=REQUIRE_CTR_DATA)
        assert result.sufficient


# ── Maturity unit tests ───────────────────────────────────────────────────────


class TestMaturityEvaluatorUnit:
    """Unit tests for the maturity evaluator in isolation."""

    def test_require_none_always_passes(self, db):
        result = evaluate_maturity(db, publication_id=1, requirement=REQUIRE_NONE)
        assert result.sufficient

    def test_custom_min_views_requirement(self, db):
        req = MaturityRequirement(min_lifetime_views=100.0)
        _insert_aggregate(db, 1, METRIC_VIEWS, 50.0)
        result = evaluate_maturity(db, publication_id=1, requirement=req)
        assert not result.sufficient
        assert result.observed_views == 50.0
        assert result.required_views == 100.0

    def test_multiple_required_metrics_all_present(self, db):
        req = MaturityRequirement(required_metrics=frozenset({METRIC_CTR, METRIC_LIKES}))
        _insert_aggregate(db, 1, METRIC_CTR, 0.03)
        _insert_aggregate(db, 1, METRIC_LIKES, 10.0)
        result = evaluate_maturity(db, publication_id=1, requirement=req)
        assert result.sufficient

    def test_multiple_required_metrics_one_missing(self, db):
        req = MaturityRequirement(required_metrics=frozenset({METRIC_CTR, METRIC_LIKES}))
        _insert_aggregate(db, 1, METRIC_CTR, 0.03)
        # METRIC_LIKES not inserted
        result = evaluate_maturity(db, publication_id=1, requirement=req)
        assert not result.sufficient
        assert METRIC_LIKES in result.missing_metrics

    def test_source_snapshot_ids_in_result(self, db):
        _insert_aggregate(db, 1, METRIC_VIEWS, 50.0, snapshot_ids=[11, 22])
        result = evaluate_maturity(
            db,
            publication_id=1,
            requirement=MaturityRequirement(min_lifetime_views=10.0),
        )
        assert result.sufficient
        # sufficient=True means we didn't capture snap_ids in a failure result;
        # the result is just "sufficient" with no snapshot context needed.
        # When it fails, snapshot_ids are captured:
        _insert_aggregate(db, 2, METRIC_VIEWS, 0.0, snapshot_ids=[33])
        fail_result = evaluate_maturity(
            db,
            publication_id=2,
            requirement=MaturityRequirement(min_lifetime_views=10.0),
        )
        assert not fail_result.sufficient
        assert 33 in fail_result.source_snapshot_ids

    def test_exactly_at_threshold_passes(self, db):
        _insert_aggregate(db, 1, METRIC_VIEWS, float(MIN_VIEWS_FOR_LEARNING))
        result = evaluate_maturity(db, publication_id=1, requirement=REQUIRE_VIEWS)
        assert result.sufficient

    def test_one_below_threshold_fails(self, db):
        _insert_aggregate(db, 1, METRIC_VIEWS, float(MIN_VIEWS_FOR_LEARNING) - 1)
        result = evaluate_maturity(db, publication_id=1, requirement=REQUIRE_VIEWS)
        assert not result.sufficient
