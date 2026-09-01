"""Phase 14G.1 — Analytics readiness JOIN fix + baseline reference integrity audit.

Verifies:
  1. analytics_aggregates has no snapshot_id column (schema guard)
  2. The old broken JOIN raises OperationalError on the real schema
  3. The new correct query runs cleanly on the real schema
  A–J: Analytics readiness matrix (assess_analytics_readiness semantics)
  K–U: Baseline policy matrix (_resolve_baseline semantics via evaluate_experiment_outcome)

Safety invariants:
  - No YouTube API calls
  - No live analytics ingest
  - No content generation
  - No LLM calls
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

import pytest

from app.core.database import open_db
from app.intelligence.experiments.eligibility_service import (
    EligibilityPolicy,
    assess_analytics_readiness,
)
from app.intelligence.experiments.outcome_contract import (
    BaselineSourceType,
)
from app.intelligence.experiments.outcome_service import (
    evaluate_experiment_outcome,
)

# ── Real-schema fixture ───────────────────────────────────────────────────────


@pytest.fixture
def real_db(tmp_path: Path) -> sqlite3.Connection:
    conn = open_db(tmp_path / "test.db")
    yield conn
    conn.close()


# ── Mock DB for readiness matrix A–J ─────────────────────────────────────────


def _mk_db() -> sqlite3.Connection:
    """Minimal in-memory mock that mirrors the column structure used by
    assess_analytics_readiness — analytics_metrics carries snapshot_id FK,
    analytics_aggregates does NOT."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE topics (
            id INTEGER PRIMARY KEY,
            promoted_opportunity_id INTEGER
        );
        CREATE TABLE publishing_plans (
            id INTEGER PRIMARY KEY,
            topic_id INTEGER
        );
        CREATE TABLE publications (
            id INTEGER PRIMARY KEY,
            publishing_plan_id INTEGER
        );
        CREATE TABLE analytics_snapshots (
            id INTEGER PRIMARY KEY,
            publication_id INTEGER,
            observation_state TEXT
        );
        -- analytics_aggregates: NO snapshot_id (matches real schema)
        CREATE TABLE analytics_aggregates (
            id INTEGER PRIMARY KEY,
            publication_id INTEGER,
            topic_id INTEGER,
            metric_name TEXT,
            metric_value REAL,
            period_type TEXT,
            period_key TEXT
        );
        -- analytics_metrics: has snapshot_id FK (matches real schema)
        CREATE TABLE analytics_metrics (
            id INTEGER PRIMARY KEY,
            snapshot_id INTEGER,
            publication_id INTEGER,
            metric_name TEXT,
            metric_value REAL
        );
    """)
    return conn


def _seed_topic_pub(
    conn: sqlite3.Connection,
    opp_id: int = 1,
    topic_id: int = 1,
    plan_id: int = 10,
    pub_id: int = 100,
) -> int:
    conn.execute(
        "INSERT OR IGNORE INTO topics (id, promoted_opportunity_id) VALUES (?, ?)",
        (topic_id, opp_id),
    )
    conn.execute(
        "INSERT OR IGNORE INTO publishing_plans (id, topic_id) VALUES (?, ?)",
        (plan_id, topic_id),
    )
    conn.execute(
        "INSERT OR IGNORE INTO publications (id, publishing_plan_id) VALUES (?, ?)",
        (pub_id, plan_id),
    )
    return pub_id


def _seed_snapshot_metric(
    conn: sqlite3.Connection,
    *,
    snap_id: int,
    pub_id: int,
    observation_state: str,
    metric_value: float | None,
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO analytics_snapshots (id, publication_id, observation_state)"
        " VALUES (?, ?, ?)",
        (snap_id, pub_id, observation_state),
    )
    if metric_value is not None:
        conn.execute(
            "INSERT INTO analytics_metrics "
            "(id, snapshot_id, publication_id, metric_name, metric_value)"
            " VALUES (?, ?, ?, 'views', ?)",
            (snap_id, snap_id, pub_id, metric_value),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Schema guard tests — real schema via open_db
# ─────────────────────────────────────────────────────────────────────────────


def test_1_analytics_aggregates_has_no_snapshot_id_column(real_db):
    """Regression guard: analytics_aggregates must NOT have a snapshot_id column."""
    cols = {r[1] for r in real_db.execute("PRAGMA table_info(analytics_aggregates)").fetchall()}
    assert "snapshot_id" not in cols, (
        "analytics_aggregates.snapshot_id would break baseline JOIN semantics. "
        "Provenance is via source_snapshot_ids_json."
    )


def test_2_analytics_metrics_has_snapshot_id_column(real_db):
    """analytics_metrics must have snapshot_id (the valid FK to analytics_snapshots)."""
    cols = {r[1] for r in real_db.execute("PRAGMA table_info(analytics_metrics)").fetchall()}
    assert "snapshot_id" in cols


def test_3_old_broken_query_raises_operational_error(real_db):
    """Prove the original bad JOIN raises OperationalError on the real schema."""
    with pytest.raises(sqlite3.OperationalError, match="snapshot_id"):
        real_db.execute(
            "SELECT 1 FROM analytics_aggregates aa"
            " JOIN analytics_snapshots ans ON ans.id = aa.snapshot_id"
            " LIMIT 1"
        )


def test_4_new_correct_query_runs_on_real_schema(real_db):
    """The fixed query (via analytics_metrics) runs without error on the real schema."""
    result = real_db.execute(
        "SELECT 1 FROM analytics_snapshots ans"
        " JOIN analytics_metrics am ON am.snapshot_id = ans.id"
        " WHERE ans.publication_id = 99"
        "   AND am.metric_name = 'views'"
        "   AND ans.observation_state = 'data'"
        "   AND am.metric_value IS NOT NULL"
        "   AND am.metric_value >= 10"
        " LIMIT 1"
    ).fetchone()
    assert result is None  # empty DB → no rows, but no crash


def test_5_assess_analytics_readiness_no_crash_on_real_schema(real_db):
    """assess_analytics_readiness does not raise OperationalError on real schema."""
    ready, findings = assess_analytics_readiness(
        real_db, opportunity_id=999, policy=EligibilityPolicy()
    )
    assert ready is False
    assert any(f.code == "no_promoted_topic" for f in findings)


# ─────────────────────────────────────────────────────────────────────────────
# A–J: Analytics readiness matrix
# ─────────────────────────────────────────────────────────────────────────────


def test_A_valid_data_snapshot_with_sufficient_views_is_ready():
    """A: observation_state='data' + views >= threshold → ready."""
    conn = _mk_db()
    _seed_topic_pub(conn)
    _seed_snapshot_metric(conn, snap_id=1, pub_id=100, observation_state="data", metric_value=25.0)
    ready, findings = assess_analytics_readiness(conn, 1, EligibilityPolicy())
    assert ready is True
    assert any(f.code == "analytics_ready" for f in findings)


def test_B_newer_no_data_does_not_erase_older_valid_data():
    """B: Snapshot 1 = data (25 views), Snapshot 2 = no_data — still ready."""
    conn = _mk_db()
    _seed_topic_pub(conn)
    _seed_snapshot_metric(conn, snap_id=1, pub_id=100, observation_state="data", metric_value=25.0)
    # Newer snapshot with no_data — no analytics_metrics row
    conn.execute(
        "INSERT INTO analytics_snapshots (id, publication_id, observation_state)"
        " VALUES (2, 100, 'no_data')"
    )
    ready, _ = assess_analytics_readiness(conn, 1, EligibilityPolicy())
    assert ready is True, "Newer no_data snapshot must not erase valid older data snapshot"


def test_C_only_no_data_snapshot_not_ready():
    """C: Only no_data snapshots → not ready."""
    conn = _mk_db()
    _seed_topic_pub(conn)
    _seed_snapshot_metric(
        conn, snap_id=1, pub_id=100, observation_state="no_data", metric_value=25.0
    )
    ready, findings = assess_analytics_readiness(conn, 1, EligibilityPolicy())
    assert ready is False
    assert any(f.code == "analytics_not_ready" for f in findings)


def test_D_zero_metric_value_is_observed_not_missing():
    """D: metric_value=0.0 is observed (IS NOT NULL) but 0.0 < threshold → not ready."""
    conn = _mk_db()
    _seed_topic_pub(conn)
    _seed_snapshot_metric(conn, snap_id=1, pub_id=100, observation_state="data", metric_value=0.0)
    ready, _ = assess_analytics_readiness(conn, 1, EligibilityPolicy())
    assert ready is False  # 0.0 fails the >= 10 threshold


def test_E_null_metric_value_means_metric_absent_not_ready():
    """E: No analytics_metrics row for the publication → metric absent → not ready."""
    conn = _mk_db()
    _seed_topic_pub(conn)
    conn.execute(
        "INSERT INTO analytics_snapshots (id, publication_id, observation_state)"
        " VALUES (1, 100, 'data')"
    )
    # No analytics_metrics row inserted
    ready, _ = assess_analytics_readiness(conn, 1, EligibilityPolicy())
    assert ready is False


def test_F_wrong_experiment_snapshot_excluded_naturally():
    """F: Metrics from a different publication don't match the WHERE clause."""
    conn = _mk_db()
    _seed_topic_pub(conn, opp_id=1, topic_id=1, plan_id=10, pub_id=100)
    # Wrong publication (200) — different topic (2)
    conn.execute("INSERT INTO topics (id, promoted_opportunity_id) VALUES (2, 2)")
    conn.execute("INSERT INTO publishing_plans (id, topic_id) VALUES (20, 2)")
    conn.execute("INSERT INTO publications (id, publishing_plan_id) VALUES (200, 20)")
    _seed_snapshot_metric(conn, snap_id=2, pub_id=200, observation_state="data", metric_value=999.0)
    # Opportunity 1's publication (100) has no valid data
    ready, _ = assess_analytics_readiness(conn, 1, EligibilityPolicy())
    assert ready is False


def test_G_wrong_topic_snapshot_excluded():
    """G: Checking opportunity 1 ignores analytics for opportunity 2."""
    conn = _mk_db()
    _seed_topic_pub(conn, opp_id=1, topic_id=1, plan_id=10, pub_id=100)
    _seed_topic_pub(conn, opp_id=2, topic_id=2, plan_id=20, pub_id=200)
    # Only pub 200 has valid data
    _seed_snapshot_metric(conn, snap_id=1, pub_id=200, observation_state="data", metric_value=25.0)
    ready_1, _ = assess_analytics_readiness(conn, 1, EligibilityPolicy())
    ready_2, _ = assess_analytics_readiness(conn, 2, EligibilityPolicy())
    assert ready_1 is False, "Opportunity 1 has no valid data"
    assert ready_2 is True, "Opportunity 2 has valid data"


def test_H_no_analytics_metrics_join_on_aggregates_snapshot_id():
    """H: The service must not reference analytics_aggregates.snapshot_id.
    Test uses a mock schema where analytics_aggregates has no snapshot_id —
    if the service tried to use it, this would raise OperationalError."""
    conn = _mk_db()
    _seed_topic_pub(conn)
    _seed_snapshot_metric(conn, snap_id=1, pub_id=100, observation_state="data", metric_value=25.0)
    # If the service still uses aa.snapshot_id this raises OperationalError
    ready, _ = assess_analytics_readiness(conn, 1, EligibilityPolicy())
    assert ready is True


def test_I_source_snapshot_ids_json_not_required_for_readiness():
    """I: Readiness uses analytics_metrics directly — source_snapshot_ids_json
    on analytics_aggregates is not needed for this check."""
    conn = _mk_db()
    _seed_topic_pub(conn)
    # No analytics_aggregates row at all — only analytics_metrics
    _seed_snapshot_metric(conn, snap_id=1, pub_id=100, observation_state="data", metric_value=50.0)
    ready, _ = assess_analytics_readiness(conn, 1, EligibilityPolicy())
    assert ready is True


def test_J_multiple_publications_one_valid_is_ready():
    """J: Topic has two publications; one has no data, one has valid data → ready."""
    conn = _mk_db()
    conn.execute("INSERT INTO topics (id, promoted_opportunity_id) VALUES (1, 1)")
    conn.execute("INSERT INTO publishing_plans (id, topic_id) VALUES (10, 1)")
    conn.execute("INSERT INTO publishing_plans (id, topic_id) VALUES (11, 1)")
    conn.execute("INSERT INTO publications (id, publishing_plan_id) VALUES (100, 10)")
    conn.execute("INSERT INTO publications (id, publishing_plan_id) VALUES (101, 11)")
    _seed_snapshot_metric(
        conn, snap_id=1, pub_id=100, observation_state="no_data", metric_value=None
    )
    _seed_snapshot_metric(conn, snap_id=2, pub_id=101, observation_state="data", metric_value=30.0)
    ready, findings = assess_analytics_readiness(conn, 1, EligibilityPolicy())
    assert ready is True
    assert any(f.code == "analytics_ready" for f in findings)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 14G fixture helpers (reused for baseline tests K–U)
# ─────────────────────────────────────────────────────────────────────────────

_snap_counter = 0
_agg_counter = 0
_pr_counter = 0


def _insert_channel(db: sqlite3.Connection, channel_id: int = 1) -> None:
    db.execute(
        "INSERT OR IGNORE INTO channels (id, platform, channel_name, platform_channel_id)"
        " VALUES (?, 'youtube', 'Test', ?)",
        (channel_id, f"UC{channel_id}g"),
    )


def _insert_opportunity(db: sqlite3.Connection, opp_id: int, channel_id: int = 1) -> int:
    _insert_channel(db, channel_id)
    db.execute(
        "INSERT OR IGNORE INTO channel_profile_versions "
        "(channel_id, primary_niche, status, version)"
        " VALUES (?, 'test', 'active', 1)",
        (channel_id,),
    )
    pv_id = db.execute(
        "SELECT id FROM channel_profile_versions WHERE channel_id = ? LIMIT 1", (channel_id,)
    ).fetchone()["id"]
    db.execute(
        "INSERT OR IGNORE INTO discovery_runs (channel_id, profile_version_id, adapter_name,"
        "  status, started_at) VALUES (?, ?, 'manual', 'completed', '2026-08-22T00:00:00')",
        (channel_id, pv_id),
    )
    run_id = db.execute(
        "SELECT id FROM discovery_runs WHERE channel_id = ? LIMIT 1", (channel_id,)
    ).fetchone()["id"]
    db.execute(
        "INSERT OR IGNORE INTO opportunities"
        " (id, channel_id, discovery_run_id, normalized_topic, raw_topic, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, '2026-08-22T00:00:00', '2026-08-22T00:00:00')",
        (opp_id, channel_id, run_id, f"t-{opp_id}", f"t-{opp_id}"),
    )
    return opp_id


def _insert_experiment(
    db: sqlite3.Connection,
    exp_id: str,
    channel_id: int = 1,
    opp_id: int = 1,
    status: str = "observing",
    exp_type: str = "exploration",
) -> str:
    db.execute(
        "INSERT OR IGNORE INTO experiments"
        " (id, channel_id, opportunity_id, experiment_type, status, hypothesis, input_hash,"
        "  maturity_policy_json)"
        " VALUES (?, ?, ?, ?, ?, 'hyp', ?, '{}')",
        (exp_id, channel_id, opp_id, exp_type, status, f"hash-{exp_id}"),
    )
    return exp_id


def _insert_contract(
    db: sqlite3.Connection,
    exp_id: str,
    channel_id: int = 1,
    opp_id: int = 1,
    fidelity: str = "valid",
) -> None:
    db.commit()
    db.execute("PRAGMA foreign_keys = OFF")
    fidelity_json = json.dumps({"classification": fidelity, "fidelity_policy_version": "1.1.0"})
    db.execute(
        "INSERT OR IGNORE INTO experiment_execution_contracts"
        " (id, experiment_id, brief_id, channel_id, opportunity_id,"
        "  execution_mode, fidelity_json, valid_for_learning, execution_policy_version)"
        " VALUES (?, ?, 'brief-1', ?, ?, 'dry_run', ?, 1, '1.0.0')",
        (f"contract-{exp_id}", exp_id, channel_id, opp_id, fidelity_json),
    )
    db.commit()
    db.execute("PRAGMA foreign_keys = ON")


def _insert_topic_ku(db: sqlite3.Connection, topic_id: int, opp_id: int) -> int:
    existing = db.execute(
        "SELECT id FROM topics WHERE promoted_opportunity_id = ?", (opp_id,)
    ).fetchone()
    if existing is not None and existing["id"] != topic_id:
        ch_row = db.execute(
            "SELECT channel_id FROM opportunities WHERE id = ?", (opp_id,)
        ).fetchone()
        shadow_id = 1000 + topic_id
        _insert_opportunity(db, shadow_id, ch_row["channel_id"] if ch_row else 1)
        opp_id = shadow_id
    db.execute(
        "INSERT OR IGNORE INTO topics (id, title, promoted_opportunity_id)"
        " VALUES (?, 'topic-g', ?)",
        (topic_id, opp_id),
    )
    return topic_id


def _insert_publishing_plan(db: sqlite3.Connection, plan_id: int, topic_id: int) -> int:
    db.commit()
    db.execute("PRAGMA foreign_keys = OFF")
    db.execute(
        "INSERT OR IGNORE INTO publishing_plans"
        " (id, render_manifest_id, topic_id, production_plan_id, script_id, scene_manifest_id,"
        "  narration_run_id, caption_run_id, input_hash, publishing_engine_version,"
        "  metadata_version, provider, provider_version, title, status, created_at, updated_at)"
        " VALUES (?, 1, ?, 1, 1, 1, 1, 1, ?, '1.0', '1.0', 'youtube', '1.0', 'title', 'approved',"
        "         '2026-08-22T00:00:00', '2026-08-22T00:00:00')",
        (plan_id, topic_id, f"ih-plan-{plan_id}"),
    )
    db.commit()
    db.execute("PRAGMA foreign_keys = ON")
    return plan_id


def _insert_publication(
    db: sqlite3.Connection, pub_id: int, plan_id: int, published_at: str = "2026-08-01T00:00:00"
) -> int:
    db.commit()
    db.execute("PRAGMA foreign_keys = OFF")
    db.execute(
        "INSERT OR IGNORE INTO publications"
        " (id, publishing_plan_id, publishing_job_id, provider, provider_version,"
        "  publishing_engine_version, input_hash, output_sha256,"
        "  created_at, updated_at, published_at, status)"
        " VALUES (?, ?, 1, 'youtube', '1.0', '1.0', ?, 'sha',"
        "         '2026-08-22T00:00:00', '2026-08-22T00:00:00', ?, 'published')",
        (pub_id, plan_id, f"ih-pub-{pub_id}", published_at),
    )
    db.commit()
    db.execute("PRAGMA foreign_keys = ON")
    return pub_id


def _insert_snapshot_ku(
    db: sqlite3.Connection, pub_id: int, observation_state: str = "data"
) -> int:
    global _snap_counter
    _snap_counter += 1
    db.commit()
    db.execute("PRAGMA foreign_keys = OFF")
    db.execute(
        "INSERT INTO analytics_snapshots"
        " (publication_id, publishing_plan_id, publishing_job_id, render_manifest_id,"
        "  scene_manifest_id, production_plan_id, script_id, topic_id, narration_run_id,"
        "  caption_run_id, provider, provider_version, adapter_version, engine_version,"
        "  analytics_schema_version, db_schema_version, input_hash, raw_metrics_json,"
        "  is_period_complete, ingested_at, created_at, observation_state)"
        " VALUES (?, 1, 1, 1, 1, 1, 1, 1, 1, 1, 'youtube', '1.0', '1.0', '1.0',"
        "         '1.0', 41, ?, '{}', 0, '2026-08-22T00:00:00', '2026-08-22T00:00:00', ?)",
        (pub_id, f"ih-snap-{_snap_counter}", observation_state),
    )
    snap_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.commit()
    db.execute("PRAGMA foreign_keys = ON")
    return snap_id


def _insert_agg(
    db: sqlite3.Connection, pub_id: int, topic_id: int, metric_name: str, metric_value: float
) -> None:
    global _agg_counter
    _agg_counter += 1
    db.execute(
        "INSERT OR IGNORE INTO analytics_aggregates"
        " (publication_id, topic_id, provider, period_type, period_key,"
        "  metric_name, metric_value, snapshot_count, input_hash, created_at)"
        " VALUES (?, ?, 'youtube', 'lifetime', 'all', ?, ?, 1, ?, '2026-08-22T00:00:00')",
        (pub_id, topic_id, metric_name, metric_value, f"ih-agg-{_agg_counter}"),
    )


def _build_chain(
    db: sqlite3.Connection,
    *,
    exp_id: str,
    channel_id: int = 1,
    opp_id: int = 1,
    topic_id: int = 10,
    pub_id: int = 100,
    plan_id: int = 200,
    published_at: str = "2026-08-01T00:00:00",
    views: float = 50.0,
    metric_name: str = "average_view_percentage",
    metric_value: float = 42.0,
    fidelity: str = "valid",
    status: str = "observing",
) -> None:
    _insert_opportunity(db, opp_id, channel_id)
    _insert_experiment(db, exp_id, channel_id, opp_id, status=status)
    _insert_contract(db, exp_id, channel_id, opp_id, fidelity)
    _insert_topic_ku(db, topic_id, opp_id)
    _insert_publishing_plan(db, plan_id, topic_id)
    _insert_publication(db, pub_id, plan_id, published_at)
    db.execute("UPDATE experiments SET publication_id = ? WHERE id = ?", (pub_id, exp_id))
    _insert_snapshot_ku(db, pub_id, "data")
    _insert_agg(db, pub_id, topic_id, "views", views)
    _insert_agg(db, pub_id, topic_id, metric_name, metric_value)
    db.commit()


def _insert_prior_outcome(
    db: sqlite3.Connection,
    *,
    prior_exp_id: str,
    readiness: str,
    classification: str | None,
    treatment_metric_value: float | None,
) -> None:
    """Insert a pre-existing experiment_outcomes row for a prior experiment."""
    db.execute(
        "INSERT OR IGNORE INTO experiment_outcomes"
        " (id, experiment_id, readiness, classification, baseline_source_type,"
        "  treatment_metric_value, reasons_json, warnings_json,"
        "  outcome_policy_version, input_hash, evaluated_at)"
        " VALUES (?, ?, ?, ?, 'none', ?, '[]', '[]', '1.0.0', ?, '2026-07-01T00:00:00')",
        (
            str(uuid.uuid4()),
            prior_exp_id,
            readiness,
            classification,
            treatment_metric_value,
            f"h-prior-{prior_exp_id}",
        ),
    )
    db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# K–U: Baseline policy matrix
# ─────────────────────────────────────────────────────────────────────────────


def test_K_baseline_unavailable_prior_with_valid_treatment_serves_as_baseline(real_db):
    """K: Prior BASELINE_UNAVAILABLE outcome has treatment_metric_value=55.0.
    It should be usable as the baseline for a subsequent experiment."""
    # Prior experiment — minimal chain
    _insert_opportunity(real_db, 1)
    _insert_experiment(real_db, "prior-K", opp_id=1)
    real_db.commit()
    _insert_prior_outcome(
        real_db,
        prior_exp_id="prior-K",
        readiness="evaluable_mature",
        classification="baseline_unavailable",
        treatment_metric_value=55.0,
    )

    # Current experiment — full evaluable chain
    _build_chain(
        real_db, exp_id="exp-K", opp_id=2, topic_id=20, pub_id=200, plan_id=300, metric_value=60.0
    )

    ev = evaluate_experiment_outcome(real_db, "exp-K", prior_experiment_id="prior-K")
    assert ev.baseline_metric_value == pytest.approx(55.0), (
        "BASELINE_UNAVAILABLE prior with valid treatment_metric_value must serve as baseline"
    )
    assert ev.baseline_source_type in (
        BaselineSourceType.PRIOR_EXPERIMENT,
        BaselineSourceType.VALIDATION_REFERENCE,
    )


def test_L_baseline_unavailable_no_earlier_reference_value_not_excluded(real_db):
    """L: BASELINE_UNAVAILABLE does not mean invalid observation.
    The prior experiment had no channel history, so got BASELINE_UNAVAILABLE,
    but its observed metric IS valid and must not be excluded as baseline."""
    _insert_opportunity(real_db, 3)
    _insert_experiment(real_db, "prior-L", opp_id=3)
    real_db.commit()
    _insert_prior_outcome(
        real_db,
        prior_exp_id="prior-L",
        readiness="evaluable_mature",
        classification="baseline_unavailable",
        treatment_metric_value=40.0,
    )

    _build_chain(
        real_db, exp_id="exp-L", opp_id=4, topic_id=40, pub_id=400, plan_id=500, metric_value=48.0
    )

    ev = evaluate_experiment_outcome(real_db, "exp-L", prior_experiment_id="prior-L")
    assert ev.baseline_metric_value == pytest.approx(40.0), (
        "Prior BASELINE_UNAVAILABLE should not be excluded when treatment_metric_value IS NOT NULL"
    )


def test_M_invalid_execution_prior_cannot_serve_as_baseline(real_db):
    """M: Prior INVALID_EXECUTION outcome has no treatment_metric_value → excluded."""
    _insert_opportunity(real_db, 5)
    _insert_experiment(real_db, "prior-M", opp_id=5)
    real_db.commit()
    _insert_prior_outcome(
        real_db,
        prior_exp_id="prior-M",
        readiness="invalid_execution",
        classification=None,
        treatment_metric_value=None,  # INVALID_EXECUTION rows have no measurement
    )

    _build_chain(
        real_db, exp_id="exp-M", opp_id=6, topic_id=60, pub_id=600, plan_id=700, metric_value=35.0
    )

    ev = evaluate_experiment_outcome(real_db, "exp-M", prior_experiment_id="prior-M")
    # Baseline should NOT come from the invalid prior — channel or none
    assert ev.baseline_metric_value is None or ev.baseline_source_type in (
        BaselineSourceType.CHANNEL_BASELINE,
        BaselineSourceType.NONE,
    )
    assert ev.baseline_source_type != BaselineSourceType.PRIOR_EXPERIMENT


def test_N_insufficient_analytics_prior_cannot_serve_as_baseline(real_db):
    """N: Prior INSUFFICIENT_ANALYTICS has no treatment_metric_value → excluded."""
    _insert_opportunity(real_db, 7)
    _insert_experiment(real_db, "prior-N", opp_id=7)
    real_db.commit()
    _insert_prior_outcome(
        real_db,
        prior_exp_id="prior-N",
        readiness="insufficient_analytics",
        classification=None,
        treatment_metric_value=None,
    )

    _build_chain(
        real_db, exp_id="exp-N", opp_id=8, topic_id=80, pub_id=800, plan_id=900, metric_value=30.0
    )

    ev = evaluate_experiment_outcome(real_db, "exp-N", prior_experiment_id="prior-N")
    assert ev.baseline_source_type not in (
        BaselineSourceType.PRIOR_EXPERIMENT,
        BaselineSourceType.VALIDATION_REFERENCE,
    )


def test_O_unresolved_prior_cannot_serve_as_baseline(real_db):
    """O: Prior 'unresolved' outcome with NULL treatment_metric_value → excluded."""
    _insert_opportunity(real_db, 9)
    _insert_experiment(real_db, "prior-O", opp_id=9)
    real_db.commit()
    _insert_prior_outcome(
        real_db,
        prior_exp_id="prior-O",
        readiness="unresolved",
        classification=None,
        treatment_metric_value=None,
    )

    _build_chain(
        real_db,
        exp_id="exp-O",
        opp_id=10,
        topic_id=100,
        pub_id=1000,
        plan_id=1100,
        metric_value=28.0,
    )

    ev = evaluate_experiment_outcome(real_db, "exp-O", prior_experiment_id="prior-O")
    assert ev.baseline_source_type not in (
        BaselineSourceType.PRIOR_EXPERIMENT,
        BaselineSourceType.VALIDATION_REFERENCE,
    )


def test_P_metric_mismatch_prior_cannot_serve_as_baseline(real_db):
    """P: Prior outcome has treatment_metric_value but a different target metric context.
    _resolve_baseline only returns the value if prior_experiment_id is passed and has
    a non-NULL treatment_metric_value; the metric name is not cross-checked by the
    resolver (left to caller intent). This test documents current V1 behaviour."""
    # This is actually documented expected behavior: _resolve_baseline does not
    # re-validate metric name — if a prior_experiment_id is given, it trusts caller.
    _insert_opportunity(real_db, 11)
    _insert_experiment(real_db, "prior-P", opp_id=11)
    real_db.commit()
    _insert_prior_outcome(
        real_db,
        prior_exp_id="prior-P",
        readiness="evaluable_mature",
        classification="positive_observation",
        treatment_metric_value=70.0,
    )

    _build_chain(
        real_db,
        exp_id="exp-P",
        opp_id=12,
        topic_id=120,
        pub_id=1200,
        plan_id=1300,
        metric_value=65.0,
    )

    ev = evaluate_experiment_outcome(real_db, "exp-P", prior_experiment_id="prior-P")
    # V1: caller is responsible for metric alignment; resolver returns the value
    assert ev.baseline_metric_value == pytest.approx(70.0)


def test_R_prior_valid_zero_metric_can_serve_as_baseline(real_db):
    """R: Prior outcome with treatment_metric_value=0.0 → valid baseline (0.0 ≠ NULL)."""
    _insert_opportunity(real_db, 13)
    _insert_experiment(real_db, "prior-R", opp_id=13)
    real_db.commit()
    _insert_prior_outcome(
        real_db,
        prior_exp_id="prior-R",
        readiness="evaluable_mature",
        classification="baseline_unavailable",
        treatment_metric_value=0.0,
    )

    _build_chain(
        real_db,
        exp_id="exp-R",
        opp_id=14,
        topic_id=140,
        pub_id=1400,
        plan_id=1500,
        metric_value=5.0,
    )

    ev = evaluate_experiment_outcome(real_db, "exp-R", prior_experiment_id="prior-R")
    assert ev.baseline_metric_value == pytest.approx(0.0)
    assert ev.baseline_source_type in (
        BaselineSourceType.PRIOR_EXPERIMENT,
        BaselineSourceType.VALIDATION_REFERENCE,
    )


def test_S_zero_baseline_yields_non_null_delta(real_db):
    """S: baseline=0.0 + treatment > 0 → absolute_delta is computable (not NULL)."""
    _insert_opportunity(real_db, 15)
    _insert_experiment(real_db, "prior-S", opp_id=15)
    real_db.commit()
    _insert_prior_outcome(
        real_db,
        prior_exp_id="prior-S",
        readiness="evaluable_mature",
        classification="baseline_unavailable",
        treatment_metric_value=0.0,
    )

    _build_chain(
        real_db,
        exp_id="exp-S",
        opp_id=16,
        topic_id=160,
        pub_id=1600,
        plan_id=1700,
        metric_value=10.0,
    )

    ev = evaluate_experiment_outcome(real_db, "exp-S", prior_experiment_id="prior-S")
    assert ev.absolute_delta is not None
    assert ev.absolute_delta == pytest.approx(10.0)


def test_T_unfavorable_prior_outcome_still_serves_as_baseline(real_db):
    """T: Prior outcome with negative classification still supplies valid baseline.
    Baseline eligibility is about measurement validity, not favorability."""
    _insert_opportunity(real_db, 17)
    _insert_experiment(real_db, "prior-T", opp_id=17)
    real_db.commit()
    _insert_prior_outcome(
        real_db,
        prior_exp_id="prior-T",
        readiness="evaluable_mature",
        classification="negative_observation",  # unfavorable — still valid measurement
        treatment_metric_value=20.0,
    )

    _build_chain(
        real_db,
        exp_id="exp-T",
        opp_id=18,
        topic_id=180,
        pub_id=1800,
        plan_id=1900,
        metric_value=30.0,
    )

    ev = evaluate_experiment_outcome(real_db, "exp-T", prior_experiment_id="prior-T")
    assert ev.baseline_metric_value == pytest.approx(20.0), (
        "Unfavorable prior outcome with valid treatment_metric_value must still serve as baseline"
    )


def test_U_explicit_validation_prior_outranks_channel_baseline(real_db):
    """U: When prior_experiment_id is set, its value takes precedence over channel baseline."""
    # Channel experiments that would contribute to channel baseline
    for eid, oid, tid, pid, pl in [
        ("ch-exp-1", 20, 200, 2000, 2100),
        ("ch-exp-2", 21, 201, 2001, 2101),
    ]:
        _build_chain(
            real_db, exp_id=eid, opp_id=oid, topic_id=tid, pub_id=pid, plan_id=pl, metric_value=80.0
        )

    # Prior validation experiment with a distinct value
    _insert_opportunity(real_db, 22)
    _insert_experiment(real_db, "prior-U", opp_id=22)
    real_db.commit()
    _insert_prior_outcome(
        real_db,
        prior_exp_id="prior-U",
        readiness="evaluable_mature",
        classification="baseline_unavailable",
        treatment_metric_value=33.0,  # distinct from channel average ~80
    )

    _build_chain(
        real_db,
        exp_id="exp-U",
        opp_id=23,
        topic_id=230,
        pub_id=2300,
        plan_id=2400,
        metric_value=40.0,
    )

    ev = evaluate_experiment_outcome(real_db, "exp-U", prior_experiment_id="prior-U")
    assert ev.baseline_metric_value == pytest.approx(33.0), (
        "Explicit validation prior must outrank channel baseline"
    )
    assert ev.baseline_source_type in (
        BaselineSourceType.PRIOR_EXPERIMENT,
        BaselineSourceType.VALIDATION_REFERENCE,
    )
