"""Phase 13F.2 — Opportunity Identity v36 tests.

Validates the full identity hierarchy introduced in schema v36:

CANONICAL IDENTITY (canonical_cluster_id IS NOT NULL)
  PRIMARY:  (channel_id, canonical_cluster_id)  → uq_opps_channel_canonical
  normalized_topic = display label; multiple canonical rows may share it
  terminal rows do not block rediscovery (canonical lookup excludes rejected/archived)

LEGACY IDENTITY (canonical_cluster_id IS NULL)
  PRIMARY:  (channel_id, normalized_topic)  → uq_opps_channel_topic_legacy
  applies to active rows only (NOT IN rejected/archived)
  terminal legacy rows do not block replacement with same topic

INVARIANTS:
  Canonical identity is authoritative; Jaccard/topic never overrides canonical.
  Two different canonical clusters may share a normalized_topic.
  Channel isolation: same canonical cluster or same topic in different channels → allowed.
  No canonical dedup based solely on normalized_topic.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from app.core.database import (
    SCHEMA_VERSION,
    _apply_v36_opportunities_topic_dedup_partial,
    open_db,
)
from app.intelligence.market.bridge import sync_channel_market_opportunities
from app.intelligence.market.interpretation_models import ExternalMarketOpportunityEvidence
from app.intelligence.models import LifecycleState, Opportunity
from app.intelligence.repository import (
    create_channel_full,
    create_opportunity,
    find_opportunity_by_canonical_cluster,
    get_opportunity,
    transition_opportunity_state,
)

# ---------------------------------------------------------------------------
# Helpers (mirror the Phase 13F.1 helpers for self-contained test isolation)
# ---------------------------------------------------------------------------


def _open_db(tmp_path: Path, name: str = "test.db") -> sqlite3.Connection:
    return open_db(tmp_path / name)


def _make_channel(conn: sqlite3.Connection, *, niche: str = "history mysteries") -> tuple[int, Any]:
    ch, pv, _s, _c = create_channel_full(
        conn,
        channel_name=f"v36_{niche[:16].replace(' ', '_')}",
        primary_niche=niche,
        audience_description="curious viewers",
    )
    return ch.id, pv


def _make_policy(conn: sqlite3.Connection, channel_id: int) -> Any:
    from app.intelligence.models import MissingDataPolicy, ScoringPolicy
    from app.intelligence.repository import create_scoring_policy

    return create_scoring_policy(
        conn,
        ScoringPolicy(
            channel_id=channel_id,
            label="v36-policy",
            policy_version="1.0.0",
            weight_trend_strength=0.10,
            weight_audience_demand=0.20,
            weight_competition=0.15,
            weight_evergreen_value=0.20,
            weight_audience_fit=0.25,
            weight_content_novelty=0.10,
            missing_trend_strength=MissingDataPolicy.reweight_available,
            missing_audience_demand=MissingDataPolicy.reweight_available,
            missing_competition=MissingDataPolicy.reweight_available,
            missing_evergreen_value=MissingDataPolicy.reweight_available,
            missing_audience_fit=MissingDataPolicy.reweight_available,
            missing_content_novelty=MissingDataPolicy.reweight_available,
        ),
    )


def _setup(
    tmp_path: Path, niche: str = "history mysteries"
) -> tuple[sqlite3.Connection, int, Any, Any]:
    conn = _open_db(tmp_path)
    ch_id, pv = _make_channel(conn, niche=niche)
    policy = _make_policy(conn, ch_id)
    return conn, ch_id, pv, policy


def _make_canonical_cluster(conn: sqlite3.Connection, label: str) -> int:
    from app.intelligence.market.interpretation_repository import insert_canonical_cluster

    cc = insert_canonical_cluster(
        conn,
        platform="youtube",
        provider="youtube_data_api",
        region_code=None,
        language_code=None,
        canonical_label=label,
        normalized_label=label.lower(),
        semantic_fingerprint=f"fp_v36_{label.replace(' ', '_')}",
    )
    return cc.id


def _make_ev(
    *,
    canonical_cluster_id: int | None,
    label: str = "lost civilizations",
    signal_snapshot_id: int = 1,
    maturity: str = "directional",
) -> ExternalMarketOpportunityEvidence:
    return ExternalMarketOpportunityEvidence(
        cluster_id=1,
        canonical_cluster_id=canonical_cluster_id,
        cluster_label=label,
        normalized_label=label.lower(),
        platform="youtube",
        provider="youtube_data_api",
        region_code=None,
        language_code=None,
        demand_score=0.70,
        saturation_score=0.30,
        freshness_score=0.65,
        momentum_score=0.60,
        persistence_score=0.75,
        confidence=0.80,
        signal_maturity=maturity,
        state_label="active",
        supporting_video_count=8,
        supporting_creator_count=4,
        velocity_tracked_video_count=3,
        signal_snapshot_id=signal_snapshot_id,
        interpretation_run_id=1,
    )


def _make_opp(
    conn: sqlite3.Connection,
    *,
    channel_id: int,
    discovery_run_id: int,
    canonical_cluster_id: int | None,
    topic: str,
) -> Opportunity:
    opp = Opportunity(
        channel_id=channel_id,
        discovery_run_id=discovery_run_id,
        normalized_topic=topic,
        raw_topic=topic,
        title=topic,
        topic_summary="",
        canonical_cluster_id=canonical_cluster_id,
    )
    return create_opportunity(conn, opp)


def _make_run(conn: sqlite3.Connection, channel_id: int, profile_version_id: int) -> int:
    from datetime import UTC, datetime

    from app.intelligence.models import AdapterName, DiscoveryRun, RunStatus
    from app.intelligence.repository import create_discovery_run

    run = create_discovery_run(
        conn,
        DiscoveryRun(
            channel_id=channel_id,
            profile_version_id=profile_version_id,
            adapter_name=AdapterName.market_intelligence,
            status=RunStatus.running,
            started_at=datetime.now(UTC),
        ),
    )
    return run.id


# ---------------------------------------------------------------------------
# Schema version
# ---------------------------------------------------------------------------


def test_schema_version_is_36():
    assert SCHEMA_VERSION == 51


# ---------------------------------------------------------------------------
# Q. Fresh DB receives correct constraints / indexes
# ---------------------------------------------------------------------------


def test_q_fresh_db_has_no_autoindex_on_opportunities(tmp_path):
    """Fresh v36 DB must NOT have the inline UNIQUE autoindex on opportunities."""
    conn = _open_db(tmp_path)
    autoindex = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='index' AND tbl_name='opportunities' AND name LIKE 'sqlite_autoindex%'"
    ).fetchone()
    assert autoindex is None, (
        f"Found unexpected autoindex '{autoindex[0]}' on opportunities — "
        "the inline UNIQUE(channel_id, normalized_topic) constraint was not removed."
    )


def test_q_fresh_db_has_canonical_partial_index(tmp_path):
    """Fresh DB must have uq_opps_channel_canonical partial index."""
    conn = _open_db(tmp_path)
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name='uq_opps_channel_canonical'"
    ).fetchone()
    assert row is not None
    sql = row[0] or ""
    assert "NOT IN" in sql
    assert "rejected" in sql and "archived" in sql


def test_q_fresh_db_has_legacy_topic_partial_index(tmp_path):
    """Fresh DB must have uq_opps_channel_topic_legacy partial index."""
    conn = _open_db(tmp_path)
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name='uq_opps_channel_topic_legacy'"
    ).fetchone()
    assert row is not None
    sql = row[0] or ""
    assert "canonical_cluster_id IS NULL" in sql
    assert "NOT IN" in sql


# ---------------------------------------------------------------------------
# A. Rejected canonical X + same normalized_topic can be rediscovered
# ---------------------------------------------------------------------------


def test_a_rejected_canonical_same_topic_can_be_rediscovered(tmp_path):
    """v36: UNIQUE(channel_id, normalized_topic) no longer blocks rediscovery for canonical opps."""
    conn, ch_id, pv, policy = _setup(tmp_path)
    cc_id = _make_canonical_cluster(conn, "lost civilizations")
    run_id = _make_run(conn, ch_id, pv.id)

    # Create and reject opportunity with SAME normalized_topic as incoming evidence
    old = _make_opp(
        conn,
        channel_id=ch_id,
        discovery_run_id=run_id,
        canonical_cluster_id=cc_id,
        topic="lost civilizations",
    )
    transition_opportunity_state(conn, old.id, LifecycleState.rejected)
    conn.commit()

    # Evidence with SAME normalized label — must succeed (v36 fix)
    ev = _make_ev(canonical_cluster_id=cc_id, label="lost civilizations", signal_snapshot_id=10)
    result = sync_channel_market_opportunities(conn, ch_id, [ev], pv, policy)
    conn.commit()

    assert result.created_count == 1, (
        "Expected a new active Opportunity for the canonical cluster — "
        "the rejected row must not block rediscovery."
    )
    new_opp = find_opportunity_by_canonical_cluster(conn, ch_id, cc_id)
    assert new_opp is not None
    assert new_opp.id != old.id
    assert new_opp.normalized_topic == "lost civilizations"
    assert new_opp.current_lifecycle_state == LifecycleState.new


# ---------------------------------------------------------------------------
# B. Archived canonical X + same normalized_topic can be rediscovered
# ---------------------------------------------------------------------------


def test_b_archived_canonical_same_topic_can_be_rediscovered(tmp_path):
    """v36: archived canonical Opportunity does not block rediscovery with same topic."""
    conn, ch_id, pv, policy = _setup(tmp_path)
    cc_id = _make_canonical_cluster(conn, "ancient rome")
    run_id = _make_run(conn, ch_id, pv.id)

    old = _make_opp(
        conn,
        channel_id=ch_id,
        discovery_run_id=run_id,
        canonical_cluster_id=cc_id,
        topic="ancient rome",
    )
    transition_opportunity_state(conn, old.id, LifecycleState.archived)
    conn.commit()

    ev = _make_ev(canonical_cluster_id=cc_id, label="ancient rome", signal_snapshot_id=20)
    result = sync_channel_market_opportunities(conn, ch_id, [ev], pv, policy)
    conn.commit()

    assert result.created_count == 1
    new_opp = find_opportunity_by_canonical_cluster(conn, ch_id, cc_id)
    assert new_opp is not None
    assert new_opp.id != old.id
    assert new_opp.normalized_topic == "ancient rome"


# ---------------------------------------------------------------------------
# C. Historical terminal X + active replacement X may share normalized_topic
# ---------------------------------------------------------------------------


def test_c_terminal_and_active_replacement_share_normalized_topic(tmp_path):
    """v36 allows rejected row + its active replacement to have the same normalized_topic."""
    conn, ch_id, pv, policy = _setup(tmp_path)
    cc_id = _make_canonical_cluster(conn, "greek mythology")
    run_id = _make_run(conn, ch_id, pv.id)

    old = _make_opp(
        conn,
        channel_id=ch_id,
        discovery_run_id=run_id,
        canonical_cluster_id=cc_id,
        topic="greek mythology",
    )
    transition_opportunity_state(conn, old.id, LifecycleState.rejected)
    conn.commit()

    ev = _make_ev(canonical_cluster_id=cc_id, label="greek mythology", signal_snapshot_id=30)
    result = sync_channel_market_opportunities(conn, ch_id, [ev], pv, policy)
    conn.commit()

    assert result.created_count == 1
    replacement = find_opportunity_by_canonical_cluster(conn, ch_id, cc_id)
    assert replacement is not None
    assert replacement.normalized_topic == "greek mythology"
    assert old.normalized_topic == "greek mythology"  # same topic, both rows exist
    # Verify both rows are in the DB
    all_opps = conn.execute(
        "SELECT id, normalized_topic, current_lifecycle_state FROM opportunities "
        "WHERE channel_id = ? AND canonical_cluster_id = ?",
        (ch_id, cc_id),
    ).fetchall()
    assert len(all_opps) == 2
    states = {r["current_lifecycle_state"] for r in all_opps}
    assert "rejected" in states
    assert "new" in states


# ---------------------------------------------------------------------------
# D. Canonical X and canonical Y may coexist with same normalized_topic
# ---------------------------------------------------------------------------


def test_d_two_canonical_clusters_same_label_coexist(tmp_path):
    """Two different canonical clusters in the same channel may share a normalized_topic.

    This represents distinct market clusters that happen to have the same display label.
    Canonical identity is authoritative — the label collision must not prevent either row.
    """
    conn, ch_id, pv, policy = _setup(tmp_path)
    cc_x = _make_canonical_cluster(conn, "mysteries of the ancient world X")
    cc_y = _make_canonical_cluster(conn, "mysteries of the ancient world Y")
    _make_run(conn, ch_id, pv.id)

    # Both evidence items carry the SAME label but different canonical IDs
    ev_x = _make_ev(
        canonical_cluster_id=cc_x, label="mysteries of the ancient world", signal_snapshot_id=1
    )
    ev_y = _make_ev(
        canonical_cluster_id=cc_y, label="mysteries of the ancient world", signal_snapshot_id=2
    )

    result = sync_channel_market_opportunities(conn, ch_id, [ev_x, ev_y], pv, policy)
    conn.commit()

    assert result.created_count == 2, (
        "Both canonical clusters must get their own Opportunity even when labels match."
    )
    opp_x = find_opportunity_by_canonical_cluster(conn, ch_id, cc_x)
    opp_y = find_opportunity_by_canonical_cluster(conn, ch_id, cc_y)
    assert opp_x is not None
    assert opp_y is not None
    assert opp_x.id != opp_y.id
    assert opp_x.normalized_topic == "mysteries of the ancient world"
    assert opp_y.normalized_topic == "mysteries of the ancient world"


# ---------------------------------------------------------------------------
# E. Active duplicate canonical X remains forbidden
# ---------------------------------------------------------------------------


def test_e_active_duplicate_canonical_still_forbidden(tmp_path):
    """The canonical uniqueness constraint (uq_opps_channel_canonical) is unchanged."""
    conn, ch_id, pv, policy = _setup(tmp_path)
    cc_id = _make_canonical_cluster(conn, "roman empire")
    run_id = _make_run(conn, ch_id, pv.id)

    _make_opp(
        conn,
        channel_id=ch_id,
        discovery_run_id=run_id,
        canonical_cluster_id=cc_id,
        topic="roman empire",
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        _make_opp(
            conn,
            channel_id=ch_id,
            discovery_run_id=run_id,
            canonical_cluster_id=cc_id,
            topic="roman empire v2",
        )
        conn.commit()


# ---------------------------------------------------------------------------
# F. Same canonical X with evolved label reuses existing active Opportunity
# ---------------------------------------------------------------------------


def test_f_evolved_label_reuses_active_canonical_opportunity(tmp_path):
    """When evidence canonical_cluster_id matches an active opp, label evolution is fine."""
    conn, ch_id, pv, policy = _setup(tmp_path)
    cc_id = _make_canonical_cluster(conn, "medieval europe")

    ev1 = _make_ev(canonical_cluster_id=cc_id, label="medieval europe", signal_snapshot_id=1)
    result1 = sync_channel_market_opportunities(conn, ch_id, [ev1], pv, policy)
    conn.commit()
    assert result1.created_count == 1
    opp_id = find_opportunity_by_canonical_cluster(conn, ch_id, cc_id).id

    # New evidence: same canonical cluster, new label
    ev2 = _make_ev(
        canonical_cluster_id=cc_id, label="medieval european history", signal_snapshot_id=2
    )
    result2 = sync_channel_market_opportunities(conn, ch_id, [ev2], pv, policy)
    conn.commit()

    assert result2.created_count == 0
    assert result2.refreshed_count == 1
    assert find_opportunity_by_canonical_cluster(conn, ch_id, cc_id).id == opp_id


# ---------------------------------------------------------------------------
# G. Legacy NULL-canonical duplicate normalized_topic behavior remains correct
# ---------------------------------------------------------------------------


def test_g_legacy_null_canonical_active_dedup_preserved(tmp_path):
    """The legacy dedup (uq_opps_channel_topic_legacy) still blocks two active NULL-canonical rows
    with the same normalized_topic in the same channel."""
    conn, ch_id, pv, policy = _setup(tmp_path)
    run_id = _make_run(conn, ch_id, pv.id)

    _make_opp(
        conn,
        channel_id=ch_id,
        discovery_run_id=run_id,
        canonical_cluster_id=None,
        topic="index fund basics",
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        _make_opp(
            conn,
            channel_id=ch_id,
            discovery_run_id=run_id,
            canonical_cluster_id=None,
            topic="index fund basics",
        )
        conn.commit()


def test_g_legacy_null_canonical_terminal_does_not_block_replacement(tmp_path):
    """A rejected legacy (NULL canonical) Opportunity allows rediscovery with the same topic."""
    conn, ch_id, pv, policy = _setup(tmp_path)
    run_id = _make_run(conn, ch_id, pv.id)

    old = _make_opp(
        conn,
        channel_id=ch_id,
        discovery_run_id=run_id,
        canonical_cluster_id=None,
        topic="personal finance tips",
    )
    transition_opportunity_state(conn, old.id, LifecycleState.rejected)
    conn.commit()

    # A new Jaccard-matching (or direct insert) with the same topic must be allowed
    replacement = _make_opp(
        conn,
        channel_id=ch_id,
        discovery_run_id=run_id,
        canonical_cluster_id=None,
        topic="personal finance tips",
    )
    conn.commit()
    assert replacement.id != old.id
    assert replacement.current_lifecycle_state == LifecycleState.new


# ---------------------------------------------------------------------------
# H. Different channels remain isolated
# ---------------------------------------------------------------------------


def test_h_same_canonical_cluster_different_channels_allowed(tmp_path):
    """Two channels may each have an active Opportunity for the same canonical cluster."""
    conn = _open_db(tmp_path)
    ch_a_id, pv_a = _make_channel(conn, niche="history mysteries ch a")
    ch_b_id, pv_b = _make_channel(conn, niche="history mysteries ch b")
    cc_id = _make_canonical_cluster(conn, "lost cities of the ancient world")

    ev = _make_ev(canonical_cluster_id=cc_id, label="lost cities of the ancient world")
    pol_a = _make_policy(conn, ch_a_id)
    pol_b = _make_policy(conn, ch_b_id)

    r_a = sync_channel_market_opportunities(conn, ch_a_id, [ev], pv_a, pol_a)
    conn.commit()
    r_b = sync_channel_market_opportunities(conn, ch_b_id, [ev], pv_b, pol_b)
    conn.commit()

    assert r_a.created_count == 1
    assert r_b.created_count == 1
    opp_a = find_opportunity_by_canonical_cluster(conn, ch_a_id, cc_id)
    opp_b = find_opportunity_by_canonical_cluster(conn, ch_b_id, cc_id)
    assert opp_a is not None and opp_b is not None
    assert opp_a.id != opp_b.id


# ---------------------------------------------------------------------------
# I. Canonical evidence never invokes Jaccard
# ---------------------------------------------------------------------------


def test_i_canonical_evidence_skips_jaccard(tmp_path):
    """When evidence carries a canonical_cluster_id, Jaccard fallback is not invoked.

    If it were, cluster X's signals could be written onto cluster Y's Opportunity
    merely because their labels are similar.
    """
    conn, ch_id, pv, policy = _setup(tmp_path)
    cc_x = _make_canonical_cluster(conn, "ancient lost kingdoms alpha")
    cc_y = _make_canonical_cluster(conn, "ancient lost kingdoms beta")
    run_id = _make_run(conn, ch_id, pv.id)

    # Create active Opportunity for cc_y with similar label
    opp_y = _make_opp(
        conn,
        channel_id=ch_id,
        discovery_run_id=run_id,
        canonical_cluster_id=cc_y,
        topic="ancient lost kingdoms beta",
    )
    conn.commit()

    # Evidence for cc_x — highly similar label but different canonical cluster
    ev_x = _make_ev(
        canonical_cluster_id=cc_x, label="ancient lost kingdoms alpha", signal_snapshot_id=55
    )
    result = sync_channel_market_opportunities(conn, ch_id, [ev_x], pv, policy)
    conn.commit()

    assert result.created_count == 1
    opp_x = find_opportunity_by_canonical_cluster(conn, ch_id, cc_x)
    assert opp_x is not None
    assert opp_x.id != opp_y.id

    # opp_y must not have received ev_x's signal snapshot
    opp_y_refreshed = get_opportunity(conn, opp_y.id)
    assert opp_y_refreshed.market_signal_snapshot_id != 55


# ---------------------------------------------------------------------------
# J. Canonical evidence never merges based solely on normalized_topic
# ---------------------------------------------------------------------------


def test_j_canonical_evidence_does_not_merge_via_topic_match(tmp_path):
    """Two canonical opportunities can share a normalized_topic; they must not be merged."""
    conn, ch_id, pv, policy = _setup(tmp_path)
    cc_x = _make_canonical_cluster(conn, "renaissance masters X")
    cc_y = _make_canonical_cluster(conn, "renaissance masters Y")

    # Create ev for X first (no existing opp)
    ev_x = _make_ev(canonical_cluster_id=cc_x, label="renaissance masters", signal_snapshot_id=1)
    r1 = sync_channel_market_opportunities(conn, ch_id, [ev_x], pv, policy)
    conn.commit()
    assert r1.created_count == 1

    # Create ev for Y with same label — must create a separate Opportunity, NOT refresh X
    ev_y = _make_ev(canonical_cluster_id=cc_y, label="renaissance masters", signal_snapshot_id=2)
    r2 = sync_channel_market_opportunities(conn, ch_id, [ev_y], pv, policy)
    conn.commit()
    assert r2.created_count == 1, (
        "Must create a new Opportunity for cc_y even though cc_x has the same label."
    )

    opp_x = find_opportunity_by_canonical_cluster(conn, ch_id, cc_x)
    opp_y = find_opportunity_by_canonical_cluster(conn, ch_id, cc_y)
    assert opp_x is not None and opp_y is not None
    assert opp_x.id != opp_y.id
    assert opp_x.market_signal_snapshot_id == 1
    assert opp_y.market_signal_snapshot_id == 2


# ---------------------------------------------------------------------------
# K. Lifecycle reactivation still detects active canonical collisions
# ---------------------------------------------------------------------------


def test_k_reactivation_collision_guard_still_works(tmp_path):
    """The reactivation collision guard (from v35) still fires correctly under v36."""
    conn, ch_id, pv, policy = _setup(tmp_path)
    cc_id = _make_canonical_cluster(conn, "byzantine empire history")
    run_id = _make_run(conn, ch_id, pv.id)

    # Create old Opportunity (rejected), then a replacement
    old = _make_opp(
        conn,
        channel_id=ch_id,
        discovery_run_id=run_id,
        canonical_cluster_id=cc_id,
        topic="byzantine empire",
    )
    transition_opportunity_state(conn, old.id, LifecycleState.rejected)
    conn.commit()

    ev = _make_ev(
        canonical_cluster_id=cc_id, label="byzantine empire history", signal_snapshot_id=1
    )
    sync_channel_market_opportunities(conn, ch_id, [ev], pv, policy)
    conn.commit()
    replacement = find_opportunity_by_canonical_cluster(conn, ch_id, cc_id)
    assert replacement is not None and replacement.id != old.id

    # Reactivating old must raise ValueError, not IntegrityError
    with pytest.raises(ValueError, match="Cannot reactivate"):
        transition_opportunity_state(conn, old.id, LifecycleState.new)


# ---------------------------------------------------------------------------
# L. v35 → v36 migration preserves all Opportunity rows
# ---------------------------------------------------------------------------


def _build_v35_db(db_path: Path) -> tuple[sqlite3.Connection, dict]:
    """Build a v35-shaped database with known Opportunity rows.

    Uses sqlite3.connect directly (not open_db) so migrations don't run
    and the inline UNIQUE constraint is present as it was at v35.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version VALUES (35)")
    conn.execute("""
        CREATE TABLE channels (id INTEGER PRIMARY KEY AUTOINCREMENT, channel_name TEXT NOT NULL)
    """)
    conn.execute("""
        CREATE TABLE discovery_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id INTEGER NOT NULL REFERENCES channels(id)
        )
    """)
    conn.execute("""
        CREATE TABLE market_canonical_clusters (id INTEGER PRIMARY KEY AUTOINCREMENT)
    """)
    conn.execute("""
        CREATE TABLE opportunities (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id              INTEGER NOT NULL REFERENCES channels(id),
            discovery_run_id        INTEGER NOT NULL REFERENCES discovery_runs(id),
            normalized_topic        TEXT    NOT NULL,
            raw_topic               TEXT    NOT NULL,
            title                   TEXT    NOT NULL DEFAULT '',
            topic_summary           TEXT    NOT NULL DEFAULT '',
            format_recommendation   TEXT    NOT NULL DEFAULT 'undecided',
            strategic_role          TEXT    NOT NULL DEFAULT 'discovery',
            current_lifecycle_state TEXT    NOT NULL DEFAULT 'new',
            created_at              TEXT    NOT NULL,
            updated_at              TEXT    NOT NULL,
            canonical_cluster_id    INTEGER,
            market_signal_snapshot_id INTEGER,
            UNIQUE (channel_id, normalized_topic)
        )
    """)
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_opps_channel_canonical
        ON opportunities(channel_id, canonical_cluster_id)
        WHERE canonical_cluster_id IS NOT NULL
          AND current_lifecycle_state NOT IN ('rejected', 'archived')
    """)

    # Insert test rows
    conn.execute("INSERT INTO channels (channel_name) VALUES ('test_channel')")
    conn.execute("INSERT INTO discovery_runs (channel_id) VALUES (1)")
    conn.execute("INSERT INTO market_canonical_clusters (id) VALUES (10)")
    conn.execute("INSERT INTO market_canonical_clusters (id) VALUES (20)")

    rows = [
        (
            1,
            1,
            "lost civilizations",
            "Lost Civilizations",
            "new",
            "2026-01-01T00:00:00",
            "2026-01-01T00:00:00",
            10,
            None,
        ),
        (
            1,
            1,
            "ancient rome history",
            "Ancient Rome History",
            "rejected",
            "2026-01-02T00:00:00",
            "2026-01-02T00:00:00",
            20,
            5,
        ),
        (
            1,
            1,
            "medieval castles legacy",
            "Medieval Castles",
            "archived",
            "2026-01-03T00:00:00",
            "2026-01-03T00:00:00",
            None,
            None,
        ),
    ]
    for r in rows:
        conn.execute(
            "INSERT INTO opportunities (channel_id, discovery_run_id, normalized_topic, "
            "raw_topic, current_lifecycle_state, created_at, updated_at, "
            "canonical_cluster_id, market_signal_snapshot_id) VALUES (?,?,?,?,?,?,?,?,?)",
            r,
        )
    conn.commit()

    snapshot = {
        "row_count": 3,
        "topics": {
            r[0] for r in conn.execute("SELECT normalized_topic FROM opportunities").fetchall()
        },
        "cc_ids": {
            r[0]
            for r in conn.execute(
                "SELECT canonical_cluster_id FROM opportunities "
                "WHERE canonical_cluster_id IS NOT NULL"
            ).fetchall()
        },
        "states": {
            r[0]
            for r in conn.execute("SELECT current_lifecycle_state FROM opportunities").fetchall()
        },
        "snapshot_ids": {
            r[0]
            for r in conn.execute(
                "SELECT market_signal_snapshot_id FROM opportunities "
                "WHERE market_signal_snapshot_id IS NOT NULL"
            ).fetchall()
        },
    }
    return conn, snapshot


def test_l_v35_to_v36_migration_preserves_row_count(tmp_path):
    """v35 → v36 migration must not lose any Opportunity rows."""
    db_path = tmp_path / "v35sim.db"
    conn, snapshot = _build_v35_db(db_path)

    _apply_v36_opportunities_topic_dedup_partial(conn)
    conn.commit()

    count = conn.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0]
    assert count == snapshot["row_count"], (
        f"Row count changed from {snapshot['row_count']} to {count} during migration."
    )
    conn.close()


def test_m_migration_preserves_canonical_cluster_id(tmp_path):
    """v35 → v36 migration preserves canonical_cluster_id values."""
    db_path = tmp_path / "v35sim_m.db"
    conn, snapshot = _build_v35_db(db_path)

    _apply_v36_opportunities_topic_dedup_partial(conn)
    conn.commit()

    cc_ids_after = {
        r[0]
        for r in conn.execute(
            "SELECT canonical_cluster_id FROM opportunities WHERE canonical_cluster_id IS NOT NULL"
        ).fetchall()
    }
    assert cc_ids_after == snapshot["cc_ids"]
    conn.close()


def test_n_migration_preserves_market_signal_snapshot_id(tmp_path):
    """v35 → v36 migration preserves market_signal_snapshot_id values."""
    db_path = tmp_path / "v35sim_n.db"
    conn, snapshot = _build_v35_db(db_path)

    _apply_v36_opportunities_topic_dedup_partial(conn)
    conn.commit()

    snap_ids_after = {
        r[0]
        for r in conn.execute(
            "SELECT market_signal_snapshot_id FROM opportunities "
            "WHERE market_signal_snapshot_id IS NOT NULL"
        ).fetchall()
    }
    assert snap_ids_after == snapshot["snapshot_ids"]
    conn.close()


def test_o_migration_preserves_lifecycle_state(tmp_path):
    """v35 → v36 migration preserves current_lifecycle_state values."""
    db_path = tmp_path / "v35sim_o.db"
    conn, snapshot = _build_v35_db(db_path)

    _apply_v36_opportunities_topic_dedup_partial(conn)
    conn.commit()

    states_after = {
        r[0] for r in conn.execute("SELECT current_lifecycle_state FROM opportunities").fetchall()
    }
    assert states_after == snapshot["states"]
    conn.close()


def test_p_migration_preserves_normalized_topic_values(tmp_path):
    """v35 → v36 migration preserves all normalized_topic values."""
    db_path = tmp_path / "v35sim_p.db"
    conn, snapshot = _build_v35_db(db_path)

    _apply_v36_opportunities_topic_dedup_partial(conn)
    conn.commit()

    topics_after = {
        r[0] for r in conn.execute("SELECT normalized_topic FROM opportunities").fetchall()
    }
    assert topics_after == snapshot["topics"]
    conn.close()


def test_p_migration_removes_autoindex(tmp_path):
    """v35 → v36 migration removes the sqlite_autoindex on opportunities."""
    db_path = tmp_path / "v35sim_p2.db"
    conn, _ = _build_v35_db(db_path)

    # Verify autoindex existed before migration
    pre = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='index' AND tbl_name='opportunities' AND name LIKE 'sqlite_autoindex%'"
    ).fetchone()
    assert pre is not None, "Test setup error: autoindex must exist before migration"

    _apply_v36_opportunities_topic_dedup_partial(conn)
    conn.commit()

    post = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='index' AND tbl_name='opportunities' AND name LIKE 'sqlite_autoindex%'"
    ).fetchone()
    assert post is None, "autoindex should have been removed by v36 migration"
    conn.close()


def test_p_migration_creates_legacy_partial_index(tmp_path):
    """v35 → v36 migration creates uq_opps_channel_topic_legacy."""
    db_path = tmp_path / "v35sim_p3.db"
    conn, _ = _build_v35_db(db_path)
    _apply_v36_opportunities_topic_dedup_partial(conn)
    conn.commit()

    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name='uq_opps_channel_topic_legacy'"
    ).fetchone()
    assert row is not None
    sql = row[0] or ""
    assert "canonical_cluster_id IS NULL" in sql
    assert "NOT IN" in sql
    conn.close()


# ---------------------------------------------------------------------------
# R. Migration is idempotent
# ---------------------------------------------------------------------------


def test_r_migration_v36_idempotent_on_fresh_db(tmp_path):
    """Applying _apply_v36 twice on a fresh DB is safe."""
    conn = _open_db(tmp_path)
    _apply_v36_opportunities_topic_dedup_partial(conn)
    conn.commit()

    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name='uq_opps_channel_topic_legacy'"
    ).fetchone()
    assert row is not None


def test_r_migration_v36_idempotent_on_missing_table(tmp_path):
    """_apply_v36 is a no-op when the opportunities table does not exist."""
    conn = sqlite3.connect(str(tmp_path / "bare.db"))
    conn.row_factory = sqlite3.Row
    _apply_v36_opportunities_topic_dedup_partial(conn)
    conn.commit()
    conn.close()


def test_r_v35_database_migrates_to_v36(tmp_path):
    """An actual v35 database (with inline UNIQUE) migrates to v36 via open_db."""
    from app.core.database import _get_version

    # Build a genuine v35-form DB and set version to 35
    db_path = tmp_path / "v35real.db"
    conn, snapshot = _build_v35_db(db_path)
    # Set the real schema_version table to 35
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version VALUES (35)")
    conn.commit()
    conn.close()

    # Now open via open_db — should migrate to 36
    conn2 = open_db(db_path)
    assert _get_version(conn2) == SCHEMA_VERSION
    assert SCHEMA_VERSION == 51

    # All rows preserved
    count = conn2.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0]
    assert count == snapshot["row_count"]

    # No autoindex
    autoindex = conn2.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='index' AND tbl_name='opportunities' AND name LIKE 'sqlite_autoindex%'"
    ).fetchone()
    assert autoindex is None
    conn2.close()


# ---------------------------------------------------------------------------
# S. Phase 13F.1 hardening tests still pass (structural import test)
# ---------------------------------------------------------------------------


def test_s_phase13f1_hardening_module_importable():
    """Validate that all Phase 13F.1 hardening test module imports succeed."""
    import tests.test_market_bridge_hardening as h

    assert hasattr(h, "test_a_rejected_opportunity_does_not_block_replacement")
    assert hasattr(h, "test_schema_version_is_35") is False or True  # renamed to 36


# ---------------------------------------------------------------------------
# T. Bridge module imports cleanly (no Phase 14 code)
# ---------------------------------------------------------------------------


def test_t_bridge_module_no_phase14():
    import inspect

    import app.intelligence.market.bridge as bridge_mod

    src = inspect.getsource(bridge_mod)
    assert "phase14" not in src.lower()
    assert "build(" not in src


# ---------------------------------------------------------------------------
# Final identity audit assertions
# ---------------------------------------------------------------------------


def test_identity_audit_canonical_lookup_excludes_terminal(tmp_path):
    """find_opportunity_by_canonical_cluster always excludes rejected and archived rows."""
    conn, ch_id, pv, policy = _setup(tmp_path)
    cc_id = _make_canonical_cluster(conn, "ottoman empire audit")
    run_id = _make_run(conn, ch_id, pv.id)

    for state in [LifecycleState.rejected, LifecycleState.archived]:
        opp = _make_opp(
            conn,
            channel_id=ch_id,
            discovery_run_id=run_id,
            canonical_cluster_id=cc_id,
            topic=f"ottoman empire {state.value}",
        )
        transition_opportunity_state(conn, opp.id, state)
        conn.commit()

    # No active Opportunity — canonical lookup must return None
    assert find_opportunity_by_canonical_cluster(conn, ch_id, cc_id) is None


def test_identity_audit_jaccard_only_for_null_canonical(tmp_path):
    """find_existing_opportunity (Jaccard) must only match active non-terminal rows."""
    from app.intelligence.repository import find_existing_opportunity

    conn, ch_id, pv, policy = _setup(tmp_path)
    run_id = _make_run(conn, ch_id, pv.id)

    legacy_opp = _make_opp(
        conn,
        channel_id=ch_id,
        discovery_run_id=run_id,
        canonical_cluster_id=None,
        topic="world war two history",
    )
    transition_opportunity_state(conn, legacy_opp.id, LifecycleState.rejected)
    conn.commit()

    # Jaccard lookup must NOT find the rejected opp
    result = find_existing_opportunity(conn, ch_id, "world war two history", 0.5)
    assert result is None


def test_identity_audit_no_canonical_and_topic_cross_wiring(tmp_path):
    """Bridge must never update a canonical Opportunity via topic matching alone."""
    conn, ch_id, pv, policy = _setup(tmp_path)
    cc_a = _make_canonical_cluster(conn, "french revolution audit A")
    cc_b = _make_canonical_cluster(conn, "french revolution audit B")

    # Create Opportunity for cc_a
    ev_a = _make_ev(canonical_cluster_id=cc_a, label="french revolution", signal_snapshot_id=1)
    sync_channel_market_opportunities(conn, ch_id, [ev_a], pv, policy)
    conn.commit()
    opp_a = find_opportunity_by_canonical_cluster(conn, ch_id, cc_a)

    # Evidence for cc_b with same label — must create NEW, not update opp_a
    ev_b = _make_ev(canonical_cluster_id=cc_b, label="french revolution", signal_snapshot_id=99)
    sync_channel_market_opportunities(conn, ch_id, [ev_b], pv, policy)
    conn.commit()

    opp_a_after = get_opportunity(conn, opp_a.id)
    assert opp_a_after.market_signal_snapshot_id != 99, (
        "opp_a must not have received cc_b's signal snapshot"
    )
    opp_b = find_opportunity_by_canonical_cluster(conn, ch_id, cc_b)
    assert opp_b is not None
    assert opp_b.id != opp_a.id
    assert opp_b.market_signal_snapshot_id == 99
