"""Phase 12C — Cross-Publication Learning Foundation tests.

Test coverage A–X as specified:
A  channel isolation
B  workspace isolation
C  Publication 1 / n=1 remains insufficient
D  zero preserved as observed zero
E  missing metrics excluded rather than converted to zero
F  each publication counted once
G  seed aggregates excluded
H  daily/weekly/monthly/lifetime duplication avoided (lifetime only consumed)
I  deterministic numeric bucketing
J  categorical comparison
K  boolean comparison
L  baseline mean/median correct
M  comparison mean/median correct
N  baseline zero avoids invalid relative division
O  maturity classification correct
P  source publication provenance preserved
Q  source snapshot provenance preserved
R  idempotent persistence
S  changed evidence changes input hash
T  learning_application_used comparison supported
U  exploration coverage counts tested feature values correctly
V  CLI inspection works
W  analytics metrics are outcomes, never content features
X  no causal language encoded into persisted results (observation_type='association')
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
from typing import Any

import pytest

from app.core.database import open_db
from app.learning.cross_publication import (
    ALL_COMPARABLE_FEATURES,
    MATURITY_ACTIONABLE,
    MATURITY_DIRECTIONAL,
    MATURITY_EXPLORATORY,
    MATURITY_INSUFFICIENT,
    OUTCOME_METRICS,
    CrossPublicationResult,
    _classify_maturity,
    _compute_stats,
    _numeric_bucket,
    feature_bucket,
    get_channel_baselines,
    get_exploration_coverage,
    get_feature_observations,
    run_cross_publication_learning,
)

_NOW = "2026-08-19T12:00:00"
_CHANNEL_A = "ch-alpha"
_CHANNEL_B = "ch-beta"
_WORKSPACE_A = "ws-alpha"


# ── Fixture helpers ───────────────────────────────────────────────────────────


def _open_test_db(tmp_path: pathlib.Path) -> sqlite3.Connection:
    conn = open_db(tmp_path / "test.db")
    conn.execute("PRAGMA foreign_keys = OFF")
    return conn


def _insert_feature_snapshot(
    conn: sqlite3.Connection,
    *,
    publication_id: int,
    channel_id: str,
    workspace_id: str = _WORKSPACE_A,
    topic_id: int = 1,
    narration_speaking_rate: float | None = 1.0,
    script_format: str | None = "short",
    script_word_count: int | None = 120,
    has_hook: int = 1,
    has_cta: int = 1,
    learning_application_used: int = 0,
    scene_count: int | None = 8,
    narration_actual_duration_s: float | None = 55.0,
) -> None:
    conn.execute(
        """
        INSERT INTO content_feature_snapshots (
            publication_id, topic_id, workspace_id, channel_id,
            feature_schema_version, extractor_version, input_hash, extracted_at, created_at,
            publishing_plan_id, production_plan_id, script_id,
            narration_run_id, caption_run_id, scene_manifest_id, render_manifest_id,
            voice_profile_id,
            narration_speaking_rate, script_format, script_word_count,
            has_hook, has_cta, learning_application_used, scene_count,
            narration_actual_duration_s
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            publication_id,
            topic_id,
            workspace_id,
            channel_id,
            "features-v1",
            "extractor-v1",
            f"hash-{publication_id}",
            _NOW,
            _NOW,
            publication_id,
            publication_id,
            publication_id,
            publication_id,
            publication_id,
            publication_id,
            publication_id,
            1,
            narration_speaking_rate,
            script_format,
            script_word_count,
            has_hook,
            has_cta,
            learning_application_used,
            scene_count,
            narration_actual_duration_s,
        ),
    )


def _insert_lifetime_aggregate(
    conn: sqlite3.Connection,
    *,
    publication_id: int,
    metric_name: str,
    metric_value: float,
    topic_id: int = 1,
    provider: str = "youtube",
    snapshot_id: int | None = None,
    is_seed: bool = False,
) -> None:
    snap_id = snapshot_id or publication_id * 100
    input_hash = (
        f"seed-{publication_id}-{metric_name}" if is_seed else f"agg-{publication_id}-{metric_name}"
    )
    conn.execute(
        """
        INSERT INTO analytics_aggregates (
            publication_id, topic_id, provider,
            period_type, period_key, metric_name, metric_value,
            snapshot_count, calculation_method, currency_code,
            source_snapshot_ids_json, input_hash, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(publication_id, provider, period_type, period_key, metric_name)
        DO UPDATE SET metric_value = excluded.metric_value,
                      input_hash = excluded.input_hash
        """,
        (
            publication_id,
            topic_id,
            provider,
            "lifetime",
            "lifetime",
            metric_name,
            metric_value,
            1,
            "sum",
            None,
            json.dumps([snap_id]),
            input_hash,
            _NOW,
        ),
    )


def _seed_channel(
    conn: sqlite3.Connection,
    *,
    channel_id: str,
    publications: list[dict[str, Any]],
) -> None:
    """Insert multiple publications with features and lifetime analytics."""
    for pub in publications:
        pub_id = pub["publication_id"]
        metrics = pub.pop("metrics", {})
        _insert_feature_snapshot(conn, channel_id=channel_id, **pub)
        for metric, value in metrics.items():
            _insert_lifetime_aggregate(
                conn, publication_id=pub_id, metric_name=metric, metric_value=value
            )
    conn.commit()


# ── Unit-level tests ──────────────────────────────────────────────────────────


class TestBucketing:
    """Test I — deterministic numeric bucketing."""

    def test_numeric_bucket_width_01(self):
        assert _numeric_bucket(1.0, 0.1) == "1–1.1"

    def test_numeric_bucket_width_01_fractional(self):
        assert _numeric_bucket(0.95, 0.1) == "0.9–1"

    def test_numeric_bucket_is_deterministic(self):
        b1 = _numeric_bucket(1.05, 0.1)
        b2 = _numeric_bucket(1.07, 0.1)
        assert b1 == b2  # same floor bucket

    def test_numeric_bucket_width_50(self):
        bkt = _numeric_bucket(120.0, 50.0)
        assert bkt == "100–150"

    def test_numeric_bucket_exactly_at_boundary(self):
        bkt = _numeric_bucket(1.1, 0.1)
        assert bkt == "1.1–1.2"

    def test_feature_bucket_categorical(self):
        assert feature_bucket("script_format", "short") == "short"

    def test_feature_bucket_boolean_true(self):
        assert feature_bucket("has_hook", 1) == "true"

    def test_feature_bucket_boolean_false(self):
        assert feature_bucket("has_hook", 0) == "false"

    def test_feature_bucket_null_returns_none(self):
        assert feature_bucket("narration_speaking_rate", None) is None

    def test_feature_bucket_unknown_feature_returns_none(self):
        assert feature_bucket("not_a_real_feature", "value") is None


class TestMaturityClassification:
    """Test O — maturity classification correct."""

    def test_n0_insufficient(self):
        assert _classify_maturity(0) == MATURITY_INSUFFICIENT

    def test_n1_insufficient(self):
        assert _classify_maturity(1) == MATURITY_INSUFFICIENT

    def test_n2_exploratory(self):
        assert _classify_maturity(2) == MATURITY_EXPLORATORY

    def test_n3_exploratory(self):
        assert _classify_maturity(3) == MATURITY_EXPLORATORY

    def test_n4_directional(self):
        assert _classify_maturity(4) == MATURITY_DIRECTIONAL

    def test_n9_directional(self):
        assert _classify_maturity(9) == MATURITY_DIRECTIONAL

    def test_n10_actionable(self):
        assert _classify_maturity(10) == MATURITY_ACTIONABLE

    def test_n100_actionable(self):
        assert _classify_maturity(100) == MATURITY_ACTIONABLE


class TestStatistics:
    """Tests L, M, N — statistics correctness."""

    def test_single_value(self):
        s = _compute_stats([5.0])
        assert s["publication_count"] == 1
        assert s["mean"] == 5.0
        assert s["median"] == 5.0
        assert s["min_value"] == 5.0
        assert s["max_value"] == 5.0
        assert s["std_dev"] is None  # Test L: std_dev is None for n=1

    def test_two_values_mean_median(self):
        s = _compute_stats([3.0, 7.0])
        assert s["mean"] == 5.0
        assert s["median"] == 5.0

    def test_three_values_median_is_middle(self):
        s = _compute_stats([1.0, 5.0, 9.0])
        assert s["median"] == 5.0

    def test_std_dev_computed_for_n_ge_2(self):
        s = _compute_stats([2.0, 4.0])
        assert s["std_dev"] is not None
        assert s["std_dev"] > 0

    def test_empty_produces_zero_count(self):
        s = _compute_stats([])
        assert s["publication_count"] == 0
        assert s["mean"] is None


# ── Integration tests (require DB) ───────────────────────────────────────────


class TestA_ChannelIsolation:
    """Test A — channel isolation."""

    def test_channel_isolation(self, tmp_path):
        conn = _open_test_db(tmp_path)
        # Publication 1 in channel A; publication 2 in channel B.
        _seed_channel(
            conn,
            channel_id=_CHANNEL_A,
            publications=[
                {"publication_id": 1, "metrics": {"views": 200.0}},
            ],
        )
        _seed_channel(
            conn,
            channel_id=_CHANNEL_B,
            publications=[
                {"publication_id": 2, "metrics": {"views": 5000.0}},
            ],
        )

        run_cross_publication_learning(conn, channel_id=_CHANNEL_A)
        run_cross_publication_learning(conn, channel_id=_CHANNEL_B)

        baselines_a = get_channel_baselines(conn, channel_id=_CHANNEL_A, metric_name="views")
        baselines_b = get_channel_baselines(conn, channel_id=_CHANNEL_B, metric_name="views")

        assert len(baselines_a) == 1
        assert len(baselines_b) == 1
        assert baselines_a[0].mean == 200.0
        assert baselines_b[0].mean == 5000.0


class TestB_WorkspaceIsolation:
    """Test B — workspace_id is stored correctly for provenance."""

    def test_workspace_id_stored(self, tmp_path):
        conn = _open_test_db(tmp_path)
        _seed_channel(
            conn,
            channel_id=_CHANNEL_A,
            publications=[
                {"publication_id": 1, "metrics": {"views": 100.0}},
            ],
        )
        run_cross_publication_learning(conn, channel_id=_CHANNEL_A, workspace_id="ws-test")
        baselines = get_channel_baselines(conn, channel_id=_CHANNEL_A)
        assert all(b.workspace_id == "ws-test" for b in baselines)


class TestC_SinglePublicationInsufficient:
    """Test C — n=1 remains insufficient."""

    def test_n1_baseline_is_insufficient(self, tmp_path):
        conn = _open_test_db(tmp_path)
        _seed_channel(
            conn,
            channel_id=_CHANNEL_A,
            publications=[
                {"publication_id": 1, "metrics": {"views": 150.0, "average_view_duration": 42.0}},
            ],
        )
        run_cross_publication_learning(conn, channel_id=_CHANNEL_A)
        baselines = get_channel_baselines(conn, channel_id=_CHANNEL_A)
        for b in baselines:
            assert b.sample_maturity == MATURITY_INSUFFICIENT
            assert b.publication_count == 1

    def test_n1_observations_are_insufficient(self, tmp_path):
        conn = _open_test_db(tmp_path)
        _seed_channel(
            conn,
            channel_id=_CHANNEL_A,
            publications=[
                {"publication_id": 1, "metrics": {"views": 150.0}},
            ],
        )
        run_cross_publication_learning(conn, channel_id=_CHANNEL_A)
        obs = get_feature_observations(conn, channel_id=_CHANNEL_A)
        for o in obs:
            assert o.sample_maturity == MATURITY_INSUFFICIENT


class TestD_ZeroPreservedAsObservedZero:
    """Test D — observed zero is distinct from absent data."""

    def test_zero_views_included_in_baseline(self, tmp_path):
        conn = _open_test_db(tmp_path)
        _seed_channel(
            conn,
            channel_id=_CHANNEL_A,
            publications=[
                {"publication_id": 1, "metrics": {"views": 0.0}},
                {"publication_id": 2, "metrics": {"views": 200.0}},
            ],
        )
        run_cross_publication_learning(conn, channel_id=_CHANNEL_A)
        baselines = get_channel_baselines(conn, channel_id=_CHANNEL_A, metric_name="views")
        assert len(baselines) == 1
        # Mean should include the 0, not skip it
        assert baselines[0].mean == 100.0
        assert baselines[0].publication_count == 2
        assert baselines[0].min_value == 0.0


class TestE_MissingMetricsExcluded:
    """Test E — missing analytics excluded, not converted to zero."""

    def test_publication_without_metric_excluded_from_baseline(self, tmp_path):
        conn = _open_test_db(tmp_path)
        # Pub 1 has views; pub 2 has no analytics at all.
        _insert_feature_snapshot(conn, publication_id=1, channel_id=_CHANNEL_A)
        _insert_feature_snapshot(conn, publication_id=2, channel_id=_CHANNEL_A)
        _insert_lifetime_aggregate(conn, publication_id=1, metric_name="views", metric_value=300.0)
        conn.commit()

        run_cross_publication_learning(conn, channel_id=_CHANNEL_A)
        baselines = get_channel_baselines(conn, channel_id=_CHANNEL_A, metric_name="views")
        assert len(baselines) == 1
        # Only pub 1 has views; pub 2 is absent (not counted as 0)
        assert baselines[0].publication_count == 1
        assert baselines[0].mean == 300.0


class TestF_EachPublicationCountedOnce:
    """Test F — each publication counted once per metric."""

    def test_publication_counted_once_in_baseline(self, tmp_path):
        conn = _open_test_db(tmp_path)
        # Pub 1 has two feature fields but one lifetime aggregate.
        _insert_feature_snapshot(
            conn,
            publication_id=1,
            channel_id=_CHANNEL_A,
            narration_speaking_rate=1.0,
            script_format="short",
        )
        _insert_lifetime_aggregate(conn, publication_id=1, metric_name="views", metric_value=100.0)
        conn.commit()

        run_cross_publication_learning(conn, channel_id=_CHANNEL_A)
        baselines = get_channel_baselines(conn, channel_id=_CHANNEL_A, metric_name="views")
        assert baselines[0].publication_count == 1

    def test_publication_counted_once_across_features(self, tmp_path):
        conn = _open_test_db(tmp_path)
        _insert_feature_snapshot(
            conn,
            publication_id=1,
            channel_id=_CHANNEL_A,
            narration_speaking_rate=1.0,
            script_format="short",
        )
        _insert_lifetime_aggregate(conn, publication_id=1, metric_name="views", metric_value=100.0)
        conn.commit()

        run_cross_publication_learning(conn, channel_id=_CHANNEL_A)
        # Publication 1 should appear in both feature observations but counted once each
        obs_rate = get_feature_observations(
            conn, channel_id=_CHANNEL_A, feature_name="narration_speaking_rate"
        )
        obs_format = get_feature_observations(
            conn, channel_id=_CHANNEL_A, feature_name="script_format"
        )
        for o in obs_rate + obs_format:
            assert o.publication_count == 1


class TestG_SeedAggregatesExcluded:
    """Test G — seed aggregates excluded from baselines."""

    def test_seed_aggregate_not_included(self, tmp_path):
        conn = _open_test_db(tmp_path)
        _insert_feature_snapshot(conn, publication_id=1, channel_id=_CHANNEL_A)
        _insert_feature_snapshot(conn, publication_id=2, channel_id=_CHANNEL_A)
        # Pub 1: real aggregate; pub 2: seed aggregate only
        _insert_lifetime_aggregate(conn, publication_id=1, metric_name="views", metric_value=100.0)
        _insert_lifetime_aggregate(
            conn, publication_id=2, metric_name="views", metric_value=999.0, is_seed=True
        )
        conn.commit()

        run_cross_publication_learning(conn, channel_id=_CHANNEL_A)
        baselines = get_channel_baselines(conn, channel_id=_CHANNEL_A, metric_name="views")
        assert baselines[0].publication_count == 1  # seed pub 2 excluded
        assert baselines[0].mean == 100.0


class TestH_LifetimeOnlyConsumed:
    """Test H — only lifetime period is used; daily/weekly/monthly rows are ignored."""

    def test_only_lifetime_rows_used(self, tmp_path):
        conn = _open_test_db(tmp_path)
        _insert_feature_snapshot(conn, publication_id=1, channel_id=_CHANNEL_A)
        # Insert a lifetime row AND a daily row
        conn.execute(
            """
            INSERT INTO analytics_aggregates (
                publication_id, topic_id, provider,
                period_type, period_key, metric_name, metric_value,
                snapshot_count, calculation_method, currency_code,
                source_snapshot_ids_json, input_hash, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                1,
                1,
                "youtube",
                "lifetime",
                "lifetime",
                "views",
                200.0,
                1,
                "sum",
                None,
                "[100]",
                "hash-lifetime",
                _NOW,
            ),
        )
        conn.execute(
            """
            INSERT INTO analytics_aggregates (
                publication_id, topic_id, provider,
                period_type, period_key, metric_name, metric_value,
                snapshot_count, calculation_method, currency_code,
                source_snapshot_ids_json, input_hash, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                1,
                1,
                "youtube",
                "daily",
                "2026-08-01",
                "views",
                50.0,
                1,
                "sum",
                None,
                "[101]",
                "hash-daily",
                _NOW,
            ),
        )
        conn.commit()

        run_cross_publication_learning(conn, channel_id=_CHANNEL_A)
        baselines = get_channel_baselines(conn, channel_id=_CHANNEL_A, metric_name="views")
        # Should be 200 (lifetime), not 250 (lifetime + daily)
        assert baselines[0].mean == 200.0


class TestI_NumericBucketing:
    """Test I — deterministic numeric bucketing in practice."""

    def test_two_pubs_same_bucket_grouped(self, tmp_path):
        conn = _open_test_db(tmp_path)
        # Both pubs have speaking rate in the 1.0–1.1 bucket
        _seed_channel(
            conn,
            channel_id=_CHANNEL_A,
            publications=[
                {"publication_id": 1, "narration_speaking_rate": 1.0, "metrics": {"views": 100.0}},
                {"publication_id": 2, "narration_speaking_rate": 1.05, "metrics": {"views": 200.0}},
            ],
        )
        run_cross_publication_learning(conn, channel_id=_CHANNEL_A)
        obs = get_feature_observations(
            conn,
            channel_id=_CHANNEL_A,
            feature_name="narration_speaking_rate",
            metric_name="views",
        )
        # Both should be in the same bucket
        assert len(obs) == 1
        assert obs[0].publication_count == 2
        assert obs[0].mean == 150.0

    def test_two_pubs_different_buckets_separated(self, tmp_path):
        conn = _open_test_db(tmp_path)
        _seed_channel(
            conn,
            channel_id=_CHANNEL_A,
            publications=[
                {"publication_id": 1, "narration_speaking_rate": 1.0, "metrics": {"views": 100.0}},
                {"publication_id": 2, "narration_speaking_rate": 0.8, "metrics": {"views": 200.0}},
            ],
        )
        run_cross_publication_learning(conn, channel_id=_CHANNEL_A)
        obs = get_feature_observations(
            conn,
            channel_id=_CHANNEL_A,
            feature_name="narration_speaking_rate",
            metric_name="views",
        )
        assert len(obs) == 2
        buckets = {o.feature_bucket for o in obs}
        assert len(buckets) == 2


class TestJ_CategoricalComparison:
    """Test J — categorical feature comparison."""

    def test_script_format_categorical(self, tmp_path):
        conn = _open_test_db(tmp_path)
        _seed_channel(
            conn,
            channel_id=_CHANNEL_A,
            publications=[
                {"publication_id": 1, "script_format": "short", "metrics": {"views": 100.0}},
                {"publication_id": 2, "script_format": "long_form", "metrics": {"views": 400.0}},
            ],
        )
        run_cross_publication_learning(conn, channel_id=_CHANNEL_A)
        obs = get_feature_observations(
            conn, channel_id=_CHANNEL_A, feature_name="script_format", metric_name="views"
        )
        buckets = {o.feature_bucket: o.mean for o in obs}
        assert buckets.get("short") == 100.0
        assert buckets.get("long_form") == 400.0


class TestK_BooleanComparison:
    """Test K — boolean feature comparison."""

    def test_has_hook_boolean(self, tmp_path):
        conn = _open_test_db(tmp_path)
        _seed_channel(
            conn,
            channel_id=_CHANNEL_A,
            publications=[
                {"publication_id": 1, "has_hook": 1, "metrics": {"average_view_duration": 40.0}},
                {"publication_id": 2, "has_hook": 0, "metrics": {"average_view_duration": 20.0}},
            ],
        )
        run_cross_publication_learning(conn, channel_id=_CHANNEL_A)
        obs = get_feature_observations(
            conn,
            channel_id=_CHANNEL_A,
            feature_name="has_hook",
            metric_name="average_view_duration",
        )
        buckets = {o.feature_bucket: o.mean for o in obs}
        assert buckets.get("true") == 40.0
        assert buckets.get("false") == 20.0


class TestL_BaselineCorrect:
    """Test L — baseline mean/median correct."""

    def test_baseline_mean_correct(self, tmp_path):
        conn = _open_test_db(tmp_path)
        _seed_channel(
            conn,
            channel_id=_CHANNEL_A,
            publications=[
                {"publication_id": 1, "metrics": {"views": 100.0}},
                {"publication_id": 2, "metrics": {"views": 200.0}},
                {"publication_id": 3, "metrics": {"views": 300.0}},
            ],
        )
        run_cross_publication_learning(conn, channel_id=_CHANNEL_A)
        baselines = get_channel_baselines(conn, channel_id=_CHANNEL_A, metric_name="views")
        assert baselines[0].mean == 200.0
        assert baselines[0].median == 200.0
        assert baselines[0].min_value == 100.0
        assert baselines[0].max_value == 300.0


class TestM_ObservationMeanCorrect:
    """Test M — feature observation mean/median correct."""

    def test_observation_mean_is_bucket_mean(self, tmp_path):
        conn = _open_test_db(tmp_path)
        # Pub 1 and 3 in same bucket (speaking_rate 1.0–1.1); pub 2 in different bucket
        _seed_channel(
            conn,
            channel_id=_CHANNEL_A,
            publications=[
                {"publication_id": 1, "narration_speaking_rate": 1.0, "metrics": {"views": 100.0}},
                {"publication_id": 2, "narration_speaking_rate": 0.8, "metrics": {"views": 400.0}},
                {"publication_id": 3, "narration_speaking_rate": 1.05, "metrics": {"views": 200.0}},
            ],
        )
        run_cross_publication_learning(conn, channel_id=_CHANNEL_A)
        obs = get_feature_observations(
            conn, channel_id=_CHANNEL_A, feature_name="narration_speaking_rate", metric_name="views"
        )
        bucket_means = {o.feature_bucket: o.mean for o in obs}
        # Pubs 1 and 3 both in 1.0–1.1 bucket: mean = (100+200)/2 = 150
        high_rate_bucket = feature_bucket("narration_speaking_rate", 1.0)
        assert bucket_means[high_rate_bucket] == 150.0


class TestN_BaselineZeroNoInvalidDivision:
    """Test N — baseline zero avoids invalid relative division."""

    def test_zero_baseline_means_no_relative_diff(self, tmp_path):
        conn = _open_test_db(tmp_path)
        _seed_channel(
            conn,
            channel_id=_CHANNEL_A,
            publications=[
                {"publication_id": 1, "has_hook": 1, "metrics": {"views": 0.0}},
                {"publication_id": 2, "has_hook": 0, "metrics": {"views": 0.0}},
            ],
        )
        run_cross_publication_learning(conn, channel_id=_CHANNEL_A)
        obs = get_feature_observations(
            conn, channel_id=_CHANNEL_A, feature_name="has_hook", metric_name="views"
        )
        for o in obs:
            # baseline_mean = 0.0; rel_diff must be None (no division by zero)
            assert o.rel_diff_from_baseline is None


class TestO_MaturityClassification:
    """Test O — maturity classification in persisted rows is correct."""

    def test_n2_persisted_as_exploratory(self, tmp_path):
        conn = _open_test_db(tmp_path)
        _seed_channel(
            conn,
            channel_id=_CHANNEL_A,
            publications=[
                {"publication_id": 1, "metrics": {"views": 100.0}},
                {"publication_id": 2, "metrics": {"views": 200.0}},
            ],
        )
        run_cross_publication_learning(conn, channel_id=_CHANNEL_A)
        baselines = get_channel_baselines(conn, channel_id=_CHANNEL_A, metric_name="views")
        assert baselines[0].sample_maturity == MATURITY_EXPLORATORY


class TestP_SourcePublicationProvenance:
    """Test P — source publication IDs preserved in baselines and observations."""

    def test_source_publication_ids_in_baseline(self, tmp_path):
        conn = _open_test_db(tmp_path)
        _seed_channel(
            conn,
            channel_id=_CHANNEL_A,
            publications=[
                {"publication_id": 10, "metrics": {"views": 100.0}},
                {"publication_id": 20, "metrics": {"views": 200.0}},
            ],
        )
        run_cross_publication_learning(conn, channel_id=_CHANNEL_A)
        baselines = get_channel_baselines(conn, channel_id=_CHANNEL_A, metric_name="views")
        src = set(baselines[0].source_publication_ids)
        assert {10, 20} <= src

    def test_source_publication_ids_in_observation(self, tmp_path):
        conn = _open_test_db(tmp_path)
        _seed_channel(
            conn,
            channel_id=_CHANNEL_A,
            publications=[
                {"publication_id": 10, "has_hook": 1, "metrics": {"views": 100.0}},
                {"publication_id": 20, "has_hook": 1, "metrics": {"views": 200.0}},
            ],
        )
        run_cross_publication_learning(conn, channel_id=_CHANNEL_A)
        obs = get_feature_observations(
            conn, channel_id=_CHANNEL_A, feature_name="has_hook", metric_name="views"
        )
        has_hook_obs = [o for o in obs if o.feature_bucket == "true"]
        assert len(has_hook_obs) == 1
        src = set(has_hook_obs[0].source_publication_ids)
        assert {10, 20} <= src


class TestQ_SourceSnapshotProvenance:
    """Test Q — source snapshot IDs preserved."""

    def test_snapshot_ids_stored(self, tmp_path):
        conn = _open_test_db(tmp_path)
        _insert_feature_snapshot(conn, publication_id=1, channel_id=_CHANNEL_A)
        _insert_lifetime_aggregate(
            conn, publication_id=1, metric_name="views", metric_value=100.0, snapshot_id=999
        )
        conn.commit()

        run_cross_publication_learning(conn, channel_id=_CHANNEL_A)
        baselines = get_channel_baselines(conn, channel_id=_CHANNEL_A, metric_name="views")
        assert 999 in baselines[0].source_snapshot_ids


class TestR_IdempotentPersistence:
    """Test R — idempotent persistence: same evidence → same row, not duplicate."""

    def test_same_run_twice_no_duplicate_baselines(self, tmp_path):
        conn = _open_test_db(tmp_path)
        _seed_channel(
            conn,
            channel_id=_CHANNEL_A,
            publications=[
                {"publication_id": 1, "metrics": {"views": 100.0}},
            ],
        )
        run_cross_publication_learning(conn, channel_id=_CHANNEL_A)
        run_cross_publication_learning(conn, channel_id=_CHANNEL_A)
        baselines = get_channel_baselines(conn, channel_id=_CHANNEL_A, metric_name="views")
        assert len(baselines) == 1

    def test_same_run_twice_no_duplicate_observations(self, tmp_path):
        conn = _open_test_db(tmp_path)
        _seed_channel(
            conn,
            channel_id=_CHANNEL_A,
            publications=[
                {"publication_id": 1, "has_hook": 1, "metrics": {"views": 100.0}},
            ],
        )
        run_cross_publication_learning(conn, channel_id=_CHANNEL_A)
        run_cross_publication_learning(conn, channel_id=_CHANNEL_A)
        obs = get_feature_observations(
            conn, channel_id=_CHANNEL_A, feature_name="has_hook", metric_name="views"
        )
        assert len(obs) == 1


class TestS_ChangedEvidenceChangesHash:
    """Test S — changed evidence changes input hash."""

    def test_new_publication_changes_baseline_hash(self, tmp_path):
        conn = _open_test_db(tmp_path)
        _seed_channel(
            conn,
            channel_id=_CHANNEL_A,
            publications=[
                {"publication_id": 1, "metrics": {"views": 100.0}},
            ],
        )
        run_cross_publication_learning(conn, channel_id=_CHANNEL_A)
        baselines_before = get_channel_baselines(conn, channel_id=_CHANNEL_A, metric_name="views")
        hash_before = baselines_before[0].input_hash

        # Add second publication
        _insert_feature_snapshot(conn, publication_id=2, channel_id=_CHANNEL_A)
        _insert_lifetime_aggregate(conn, publication_id=2, metric_name="views", metric_value=200.0)
        conn.commit()

        run_cross_publication_learning(conn, channel_id=_CHANNEL_A)
        baselines_after = get_channel_baselines(conn, channel_id=_CHANNEL_A, metric_name="views")
        hash_after = baselines_after[0].input_hash

        assert hash_before != hash_after
        assert baselines_after[0].publication_count == 2


class TestT_LearningApplicationUsedComparison:
    """Test T — learning_application_used boolean comparison supported."""

    def test_learning_application_used_in_all_features(self):
        assert "learning_application_used" in ALL_COMPARABLE_FEATURES

    def test_learning_application_used_feature_comparison(self, tmp_path):
        conn = _open_test_db(tmp_path)
        _seed_channel(
            conn,
            channel_id=_CHANNEL_A,
            publications=[
                {
                    "publication_id": 1,
                    "learning_application_used": 0,
                    "metrics": {"average_view_duration": 30.0},
                },
                {
                    "publication_id": 2,
                    "learning_application_used": 1,
                    "metrics": {"average_view_duration": 45.0},
                },
            ],
        )
        run_cross_publication_learning(conn, channel_id=_CHANNEL_A)
        obs = get_feature_observations(
            conn,
            channel_id=_CHANNEL_A,
            feature_name="learning_application_used",
            metric_name="average_view_duration",
        )
        buckets = {o.feature_bucket: o.mean for o in obs}
        assert buckets.get("true") == 45.0
        assert buckets.get("false") == 30.0


class TestU_ExplorationCoverage:
    """Test U — exploration coverage counts tested feature values correctly."""

    def test_coverage_counts_publications_per_bucket(self, tmp_path):
        conn = _open_test_db(tmp_path)
        _seed_channel(
            conn,
            channel_id=_CHANNEL_A,
            publications=[
                {"publication_id": 1, "script_format": "short", "metrics": {"views": 100.0}},
                {"publication_id": 2, "script_format": "short", "metrics": {"views": 200.0}},
                {"publication_id": 3, "script_format": "long_form", "metrics": {"views": 300.0}},
            ],
        )
        run_cross_publication_learning(conn, channel_id=_CHANNEL_A)
        coverage = get_exploration_coverage(
            conn, channel_id=_CHANNEL_A, feature_name="script_format"
        )
        assert "script_format" in coverage
        assert coverage["script_format"]["short"]["publication_count"] == 2
        assert coverage["script_format"]["long_form"]["publication_count"] == 1

    def test_coverage_includes_maturity(self, tmp_path):
        conn = _open_test_db(tmp_path)
        _seed_channel(
            conn,
            channel_id=_CHANNEL_A,
            publications=[
                {"publication_id": 1, "script_format": "short", "metrics": {"views": 100.0}},
            ],
        )
        run_cross_publication_learning(conn, channel_id=_CHANNEL_A)
        coverage = get_exploration_coverage(
            conn, channel_id=_CHANNEL_A, feature_name="script_format"
        )
        assert coverage["script_format"]["short"]["sample_maturity"] == MATURITY_INSUFFICIENT

    def test_coverage_includes_pub_ids(self, tmp_path):
        conn = _open_test_db(tmp_path)
        _seed_channel(
            conn,
            channel_id=_CHANNEL_A,
            publications=[
                {"publication_id": 7, "script_format": "short", "metrics": {"views": 100.0}},
            ],
        )
        run_cross_publication_learning(conn, channel_id=_CHANNEL_A)
        coverage = get_exploration_coverage(
            conn, channel_id=_CHANNEL_A, feature_name="script_format"
        )
        assert 7 in coverage["script_format"]["short"]["source_publication_ids"]


class TestV_CLI:
    """Test V — CLI inspection works."""

    def test_cross_pub_cli_command(self, tmp_path):
        from unittest.mock import patch

        from typer.testing import CliRunner

        from app.learning.cli import learn_app

        db_path = tmp_path / "cli_test.db"
        conn = open_db(db_path)
        conn.execute("PRAGMA foreign_keys = OFF")
        _insert_feature_snapshot(conn, publication_id=1, channel_id=_CHANNEL_A)
        _insert_lifetime_aggregate(conn, publication_id=1, metric_name="views", metric_value=100.0)
        conn.commit()
        conn.close()

        runner = CliRunner()
        with patch("app.learning.cli._get_db", lambda: open_db(db_path)):
            result = runner.invoke(learn_app, ["cross-pub", "--channel", _CHANNEL_A])
        assert result.exit_code == 0, result.output
        assert "Cross-publication analysis complete" in result.output

    def test_baseline_cli_command(self, tmp_path):
        from unittest.mock import patch

        from typer.testing import CliRunner

        from app.learning.cli import learn_app

        db_path = tmp_path / "cli_baseline.db"
        conn = open_db(db_path)
        conn.execute("PRAGMA foreign_keys = OFF")
        _insert_feature_snapshot(conn, publication_id=1, channel_id=_CHANNEL_A)
        _insert_lifetime_aggregate(conn, publication_id=1, metric_name="views", metric_value=100.0)
        conn.commit()
        run_cross_publication_learning(conn, channel_id=_CHANNEL_A)
        conn.close()

        runner = CliRunner()
        with patch("app.learning.cli._get_db", lambda: open_db(db_path)):
            result = runner.invoke(learn_app, ["baseline", "--channel", _CHANNEL_A])
        assert result.exit_code == 0, result.output
        assert "views" in result.output

    def test_coverage_cli_command(self, tmp_path):
        from unittest.mock import patch

        from typer.testing import CliRunner

        from app.learning.cli import learn_app

        db_path = tmp_path / "cli_coverage.db"
        conn = open_db(db_path)
        conn.execute("PRAGMA foreign_keys = OFF")
        _insert_feature_snapshot(conn, publication_id=1, channel_id=_CHANNEL_A)
        _insert_lifetime_aggregate(conn, publication_id=1, metric_name="views", metric_value=100.0)
        conn.commit()
        run_cross_publication_learning(conn, channel_id=_CHANNEL_A)
        conn.close()

        runner = CliRunner()
        with patch("app.learning.cli._get_db", lambda: open_db(db_path)):
            result = runner.invoke(learn_app, ["coverage", "--channel", _CHANNEL_A])
        assert result.exit_code == 0, result.output
        assert "script_format" in result.output or "narration_speaking_rate" in result.output


class TestW_AnalyticsAreOutcomesNotFeatures:
    """Test W — analytics metrics are outcomes, never content features."""

    def test_outcome_metrics_not_in_all_comparable_features(self):
        for m in OUTCOME_METRICS:
            assert m not in ALL_COMPARABLE_FEATURES, (
                f"Metric {m!r} should not be in ALL_COMPARABLE_FEATURES — "
                "analytics outcomes must not be treated as content features."
            )

    def test_content_feature_snapshots_columns_not_in_outcome_metrics(self):
        content_feature_columns = {
            "narration_speaking_rate",
            "script_word_count",
            "script_format",
            "has_hook",
            "has_cta",
            "scene_count",
            "learning_application_used",
        }
        for col in content_feature_columns:
            assert col not in OUTCOME_METRICS, (
                f"Content feature {col!r} must not appear in OUTCOME_METRICS."
            )


class TestX_NoCausalLanguage:
    """Test X — no causal language encoded into persisted results."""

    def test_observation_type_is_association(self, tmp_path):
        conn = _open_test_db(tmp_path)
        _seed_channel(
            conn,
            channel_id=_CHANNEL_A,
            publications=[
                {"publication_id": 1, "has_hook": 1, "metrics": {"views": 100.0}},
            ],
        )
        run_cross_publication_learning(conn, channel_id=_CHANNEL_A)
        obs = get_feature_observations(conn, channel_id=_CHANNEL_A)
        for o in obs:
            assert o.observation_type == "association", (
                f"Expected 'association' but got {o.observation_type!r} — "
                "cross-publication results must never claim causation."
            )

    def test_cross_pub_result_returns_dataclass(self, tmp_path):
        conn = _open_test_db(tmp_path)
        _seed_channel(
            conn,
            channel_id=_CHANNEL_A,
            publications=[
                {"publication_id": 1, "metrics": {"views": 100.0}},
            ],
        )
        result = run_cross_publication_learning(conn, channel_id=_CHANNEL_A)
        assert isinstance(result, CrossPublicationResult)
        assert result.schema_version == "cross-pub-v1"
        assert result.observer_version == "observer-v1"


# ── Synthetic multi-publication validation ────────────────────────────────────


class TestSyntheticMultiPublication:
    """Controlled multi-publication fixture demonstrating Phase 12C correctness.

    Publication A: speaking_rate=0.9, AVD=35s
    Publication B: speaking_rate=1.0, AVD=45s
    Publication C: speaking_rate=1.0, AVD=55s

    Expected: baseline AVD = 45s; 1.0 bucket mean = 50s; 0.9 bucket mean = 35s.
    The engine reports an association, not a causal claim.
    """

    @pytest.fixture
    def multi_pub_db(self, tmp_path):
        conn = _open_test_db(tmp_path)
        _seed_channel(
            conn,
            channel_id=_CHANNEL_A,
            publications=[
                {
                    "publication_id": 1,
                    "narration_speaking_rate": 0.9,
                    "metrics": {"average_view_duration": 35.0, "views": 100.0},
                },
                {
                    "publication_id": 2,
                    "narration_speaking_rate": 1.0,
                    "metrics": {"average_view_duration": 45.0, "views": 200.0},
                },
                {
                    "publication_id": 3,
                    "narration_speaking_rate": 1.0,
                    "metrics": {"average_view_duration": 55.0, "views": 300.0},
                },
            ],
        )
        run_cross_publication_learning(conn, channel_id=_CHANNEL_A)
        return conn

    def test_avd_baseline_correct(self, multi_pub_db):
        baselines = get_channel_baselines(
            multi_pub_db, channel_id=_CHANNEL_A, metric_name="average_view_duration"
        )
        assert len(baselines) == 1
        # Mean of [35, 45, 55] = 45
        assert baselines[0].mean == pytest.approx(45.0)
        assert baselines[0].publication_count == 3

    def test_high_rate_bucket_mean(self, multi_pub_db):
        obs = get_feature_observations(
            multi_pub_db,
            channel_id=_CHANNEL_A,
            feature_name="narration_speaking_rate",
            metric_name="average_view_duration",
        )
        # 1.0 bucket contains pubs 2 and 3 → mean = 50
        rate_1_bkt = feature_bucket("narration_speaking_rate", 1.0)
        obs_dict = {o.feature_bucket: o for o in obs}
        assert rate_1_bkt in obs_dict
        assert obs_dict[rate_1_bkt].mean == pytest.approx(50.0)

    def test_low_rate_bucket_is_insufficient(self, multi_pub_db):
        obs = get_feature_observations(
            multi_pub_db,
            channel_id=_CHANNEL_A,
            feature_name="narration_speaking_rate",
            metric_name="average_view_duration",
        )
        rate_09_bkt = feature_bucket("narration_speaking_rate", 0.9)
        obs_dict = {o.feature_bucket: o for o in obs}
        assert obs_dict[rate_09_bkt].sample_maturity == MATURITY_INSUFFICIENT

    def test_observation_type_is_association_not_causal(self, multi_pub_db):
        obs = get_feature_observations(multi_pub_db, channel_id=_CHANNEL_A)
        for o in obs:
            assert o.observation_type == "association"
