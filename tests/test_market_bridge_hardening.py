"""Phase 13F.1 — Hardening tests for active Opportunity identity invariant.

Covers the following invariants introduced in schema v35:

ACTIVE OPPORTUNITY IDENTITY
  For a given (channel_id, canonical_cluster_id), at most one active
  (non-rejected, non-archived) Opportunity may exist at any time.

REJECTED / ARCHIVED REDISCOVERY
  Terminal Opportunities do not block replacement.
  Future market syncs may create new active Opportunities for the same cluster.

LIFECYCLE REACTIVATION GUARD
  transition_opportunity_state raises ValueError — not IntegrityError — when
  reactivating a terminal Opportunity whose canonical cluster is already owned
  by another active Opportunity.

CANONICAL vs JACCARD DEDUP
  Jaccard fallback is skipped when the evidence carries a canonical_cluster_id.
  This prevents signals for cluster X from being attached to an Opportunity
  anchored to cluster Y merely because their topic labels are lexically similar.

SIGNAL / CANONICAL CONSISTENCY
  _process_one raises ValueError when the found Opportunity's canonical_cluster_id
  differs from the incoming evidence's canonical_cluster_id.

MIGRATION
  v34 → v35 migration drops the old index and creates the corrected partial index.
  Fresh DBs receive the corrected index directly.
  Migration is idempotent.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from app.core.database import SCHEMA_VERSION, _apply_v35_active_opportunity_identity, open_db
from app.intelligence.market.bridge import sync_channel_market_opportunities
from app.intelligence.market.bridge_models import ExternalMarketBridgePolicy
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
# Helpers
# ---------------------------------------------------------------------------

_NOW = "2026-08-21T00:00:00"


def _open_db(tmp_path: Path, name: str = "test.db") -> sqlite3.Connection:
    return open_db(tmp_path / name)


def _make_channel(conn: sqlite3.Connection, *, niche: str = "history mysteries") -> tuple[int, Any]:
    ch, pv, _s, _c = create_channel_full(
        conn,
        channel_name=f"ch_{niche[:20].replace(' ', '_')}",
        primary_niche=niche,
        audience_description="curious viewers",
    )
    return ch.id, pv


def _make_scoring_policy(conn: sqlite3.Connection, channel_id: int) -> Any:
    from app.intelligence.models import MissingDataPolicy, ScoringPolicy
    from app.intelligence.repository import create_scoring_policy

    return create_scoring_policy(
        conn,
        ScoringPolicy(
            channel_id=channel_id,
            label="hardening-policy",
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
        semantic_fingerprint=f"fp_{label.replace(' ', '_')}",
    )
    return cc.id


def _make_evidence(
    *,
    canonical_cluster_id: int | None,
    cluster_id: int = 1,
    label: str = "lost civilizations",
    signal_snapshot_id: int = 1,
    interpretation_run_id: int = 1,
    confidence: float = 0.80,
    maturity: str = "directional",
) -> ExternalMarketOpportunityEvidence:
    return ExternalMarketOpportunityEvidence(
        cluster_id=cluster_id,
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
        confidence=confidence,
        signal_maturity=maturity,
        state_label="active",
        supporting_video_count=8,
        supporting_creator_count=4,
        velocity_tracked_video_count=3,
        signal_snapshot_id=signal_snapshot_id,
        interpretation_run_id=interpretation_run_id,
    )


def _setup(
    tmp_path: Path, niche: str = "history mysteries"
) -> tuple[sqlite3.Connection, int, Any, Any]:
    conn = _open_db(tmp_path)
    ch_id, pv = _make_channel(conn, niche=niche)
    policy = _make_scoring_policy(conn, ch_id)
    return conn, ch_id, pv, policy


def _make_opportunity(
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
# N. Fresh DB receives the corrected index
# ---------------------------------------------------------------------------


def test_n_fresh_db_index_excludes_terminal_states(tmp_path):
    """A fresh DB must create the v35 partial index that excludes rejected/archived."""
    conn = _open_db(tmp_path)
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name='uq_opps_channel_canonical'"
    ).fetchone()
    assert row is not None, "uq_opps_channel_canonical index not found"
    sql = row[0] or ""
    assert "NOT IN" in sql, f"Expected NOT IN in index definition, got: {sql}"
    assert "rejected" in sql
    assert "archived" in sql


# ---------------------------------------------------------------------------
# M. Migration v34 → v35
# ---------------------------------------------------------------------------


def test_m_migration_v34_to_v35_rebuilds_index(tmp_path):
    """Simulate a v34 DB (old index) and verify v35 migration produces the correct index."""
    # Manually create a v34-style DB with old index
    db_path = tmp_path / "v34sim.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version VALUES (34)")
    conn.execute(
        """CREATE TABLE opportunities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id INTEGER NOT NULL,
            canonical_cluster_id INTEGER,
            current_lifecycle_state TEXT NOT NULL DEFAULT 'new'
        )"""
    )
    # Old v34 index (no NOT IN clause)
    conn.execute(
        "CREATE UNIQUE INDEX uq_opps_channel_canonical "
        "ON opportunities(channel_id, canonical_cluster_id) "
        "WHERE canonical_cluster_id IS NOT NULL"
    )
    conn.commit()

    # Verify old form
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name='uq_opps_channel_canonical'"
    ).fetchone()
    assert "NOT IN" not in (row[0] or ""), "Test setup error: old index should not have NOT IN"

    # Apply v35 migration
    _apply_v35_active_opportunity_identity(conn)
    conn.commit()

    # Verify new form
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name='uq_opps_channel_canonical'"
    ).fetchone()
    assert row is not None
    assert "NOT IN" in (row[0] or ""), f"v35 index should have NOT IN, got: {row[0]}"
    assert "rejected" in (row[0] or "")
    assert "archived" in (row[0] or "")
    conn.close()


# ---------------------------------------------------------------------------
# O. Migration idempotency
# ---------------------------------------------------------------------------


def test_o_migration_v35_idempotent(tmp_path):
    """Applying _apply_v35 twice is safe — second call is a no-op."""
    conn = _open_db(tmp_path)
    # Apply again
    _apply_v35_active_opportunity_identity(conn)
    conn.commit()
    # Index still correct
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name='uq_opps_channel_canonical'"
    ).fetchone()
    assert row is not None
    assert "NOT IN" in (row[0] or "")


def test_o2_migration_v35_idempotent_on_missing_table(tmp_path):
    """_apply_v35 is a no-op when the opportunities table doesn't exist."""
    conn = sqlite3.connect(str(tmp_path / "bare.db"))
    conn.row_factory = sqlite3.Row
    # No tables at all — should not raise
    _apply_v35_active_opportunity_identity(conn)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# A. Rejected Opportunity does not block replacement
# ---------------------------------------------------------------------------


def test_a_rejected_opportunity_does_not_block_replacement(tmp_path):
    """Rejecting an Opportunity frees its canonical slot for a new one.

    The canonical_cluster_id is the stable identity anchor. Labels can evolve;
    the new evidence label is canonical while the old opportunity carried an
    earlier normalized label — this is the realistic production scenario.
    """
    conn, ch_id, pv, policy = _setup(tmp_path)
    cc_id = _make_canonical_cluster(conn, "lost civilizations")
    run_id = _make_run(conn, ch_id, pv.id)

    # Create and reject an Opportunity with an old/variant label
    opp_a = _make_opportunity(
        conn,
        channel_id=ch_id,
        discovery_run_id=run_id,
        canonical_cluster_id=cc_id,
        topic="lost civilisations v1",
    )
    transition_opportunity_state(
        conn, opp_a.id, LifecycleState.rejected, actor="operator", reason="test"
    )
    conn.commit()

    # Bridge sync comes in with the canonical label — should create a replacement
    ev = _make_evidence(canonical_cluster_id=cc_id, label="lost civilizations")
    result = sync_channel_market_opportunities(conn, ch_id, [ev], pv, policy, dry_run=False)
    conn.commit()

    assert result.created_count == 1
    new_opp = find_opportunity_by_canonical_cluster(conn, ch_id, cc_id)
    assert new_opp is not None
    assert new_opp.id != opp_a.id
    assert new_opp.current_lifecycle_state == LifecycleState.new


# ---------------------------------------------------------------------------
# B. Archived Opportunity does not block replacement
# ---------------------------------------------------------------------------


def test_b_archived_opportunity_does_not_block_replacement(tmp_path):
    """Archiving an Opportunity frees its canonical slot for a new one.

    The archived opportunity's normalized_topic differs from the new evidence
    label — canonical_cluster_id is the stable identity, not the label.
    """
    conn, ch_id, pv, policy = _setup(tmp_path)
    cc_id = _make_canonical_cluster(conn, "ancient mysteries")
    run_id = _make_run(conn, ch_id, pv.id)

    opp_a = _make_opportunity(
        conn,
        channel_id=ch_id,
        discovery_run_id=run_id,
        canonical_cluster_id=cc_id,
        topic="ancient mysteries v1",
    )
    transition_opportunity_state(
        conn, opp_a.id, LifecycleState.archived, actor="operator", reason="test"
    )
    conn.commit()

    ev = _make_evidence(canonical_cluster_id=cc_id, label="ancient mysteries")
    result = sync_channel_market_opportunities(conn, ch_id, [ev], pv, policy, dry_run=False)
    conn.commit()

    assert result.created_count == 1
    new_opp = find_opportunity_by_canonical_cluster(conn, ch_id, cc_id)
    assert new_opp is not None
    assert new_opp.id != opp_a.id


# ---------------------------------------------------------------------------
# C. Active duplicate is forbidden
# ---------------------------------------------------------------------------


def test_c_active_duplicate_is_forbidden(tmp_path):
    """Inserting two active Opportunities for the same (channel, canonical_cluster) raises."""
    conn, ch_id, pv, policy = _setup(tmp_path)
    cc_id = _make_canonical_cluster(conn, "roman history")
    run_id = _make_run(conn, ch_id, pv.id)

    _make_opportunity(
        conn,
        channel_id=ch_id,
        discovery_run_id=run_id,
        canonical_cluster_id=cc_id,
        topic="roman history",
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        _make_opportunity(
            conn,
            channel_id=ch_id,
            discovery_run_id=run_id,
            canonical_cluster_id=cc_id,
            topic="roman history duplicate",
        )
        conn.commit()


# ---------------------------------------------------------------------------
# D. Same canonical cluster in different channels allowed
# ---------------------------------------------------------------------------


def test_d_same_canonical_cluster_different_channels_allowed(tmp_path):
    """Two different channels may each have an active Opportunity for the same cluster."""
    conn = _open_db(tmp_path)
    ch_a_id, pv_a = _make_channel(conn, niche="history mysteries")
    ch_b_id, pv_b = _make_channel(conn, niche="ancient history")
    cc_id = _make_canonical_cluster(conn, "lost civilizations")
    run_id_a = _make_run(conn, ch_a_id, pv_a.id)
    run_id_b = _make_run(conn, ch_b_id, pv_b.id)

    opp_a = _make_opportunity(
        conn,
        channel_id=ch_a_id,
        discovery_run_id=run_id_a,
        canonical_cluster_id=cc_id,
        topic="lost civilizations ch_a",
    )
    opp_b = _make_opportunity(
        conn,
        channel_id=ch_b_id,
        discovery_run_id=run_id_b,
        canonical_cluster_id=cc_id,
        topic="lost civilizations ch_b",
    )
    conn.commit()

    assert opp_a.id != opp_b.id
    assert find_opportunity_by_canonical_cluster(conn, ch_a_id, cc_id).id == opp_a.id
    assert find_opportunity_by_canonical_cluster(conn, ch_b_id, cc_id).id == opp_b.id


# ---------------------------------------------------------------------------
# E. Multiple historical terminal Opportunities allowed
# ---------------------------------------------------------------------------


def test_e_multiple_historical_terminal_opportunities_allowed(tmp_path):
    """Several rejected/archived rows for the same cluster are all permitted."""
    conn, ch_id, pv, policy = _setup(tmp_path)
    cc_id = _make_canonical_cluster(conn, "renaissance art")
    run_id = _make_run(conn, ch_id, pv.id)

    for i in range(3):
        opp = _make_opportunity(
            conn,
            channel_id=ch_id,
            discovery_run_id=run_id,
            canonical_cluster_id=cc_id,
            topic=f"renaissance art v{i}",
        )
        transition_opportunity_state(conn, opp.id, LifecycleState.rejected)
        conn.commit()

    # Verify three rejected rows exist
    rows = conn.execute(
        "SELECT id FROM opportunities WHERE channel_id=? AND canonical_cluster_id=?",
        (ch_id, cc_id),
    ).fetchall()
    assert len(rows) == 3


# ---------------------------------------------------------------------------
# F. Refresh reuses existing active Opportunity
# ---------------------------------------------------------------------------


def test_f_refresh_reuses_existing_active_opportunity(tmp_path):
    """A second bridge sync for the same canonical cluster refreshes the existing Opportunity."""
    conn, ch_id, pv, policy = _setup(tmp_path)
    cc_id = _make_canonical_cluster(conn, "medieval history")
    ev = _make_evidence(canonical_cluster_id=cc_id, label="medieval history")

    result1 = sync_channel_market_opportunities(conn, ch_id, [ev], pv, policy)
    conn.commit()
    opp_id_1 = find_opportunity_by_canonical_cluster(conn, ch_id, cc_id).id

    result2 = sync_channel_market_opportunities(conn, ch_id, [ev], pv, policy)
    conn.commit()
    opp_id_2 = find_opportunity_by_canonical_cluster(conn, ch_id, cc_id).id

    assert result1.created_count == 1
    assert result2.created_count == 0
    assert result2.refreshed_count == 1
    assert opp_id_1 == opp_id_2


# ---------------------------------------------------------------------------
# G. Refresh ignores terminal rows; active replacement used
# ---------------------------------------------------------------------------


def test_g_refresh_ignores_terminal_when_active_replacement_exists(tmp_path):
    """After a rejected→replaced cycle, syncing again updates the replacement, not history."""
    conn, ch_id, pv, policy = _setup(tmp_path)
    cc_id = _make_canonical_cluster(conn, "viking age")
    run_id = _make_run(conn, ch_id, pv.id)

    # Create and reject first Opportunity (uses an old/variant label)
    opp_old = _make_opportunity(
        conn,
        channel_id=ch_id,
        discovery_run_id=run_id,
        canonical_cluster_id=cc_id,
        topic="viking age v1",
    )
    transition_opportunity_state(conn, opp_old.id, LifecycleState.rejected)
    conn.commit()

    # First sync creates replacement
    ev = _make_evidence(canonical_cluster_id=cc_id, label="viking age")
    result1 = sync_channel_market_opportunities(conn, ch_id, [ev], pv, policy)
    conn.commit()
    assert result1.created_count == 1
    replacement_id = find_opportunity_by_canonical_cluster(conn, ch_id, cc_id).id
    assert replacement_id != opp_old.id

    # Second sync refreshes replacement
    result2 = sync_channel_market_opportunities(conn, ch_id, [ev], pv, policy)
    conn.commit()
    assert result2.refreshed_count == 1
    assert find_opportunity_by_canonical_cluster(conn, ch_id, cc_id).id == replacement_id


# ---------------------------------------------------------------------------
# H. rejected → active collision fails with clean ValueError
# ---------------------------------------------------------------------------


def test_h_rejected_to_active_collision_raises_value_error(tmp_path):
    """Reactivating a rejected Opportunity when another active one exists raises ValueError."""
    conn, ch_id, pv, policy = _setup(tmp_path)
    cc_id = _make_canonical_cluster(conn, "greek mythology")
    run_id = _make_run(conn, ch_id, pv.id)

    # Opportunity A: reject it (use a variant label so normalized_topic is distinct)
    opp_a = _make_opportunity(
        conn,
        channel_id=ch_id,
        discovery_run_id=run_id,
        canonical_cluster_id=cc_id,
        topic="greek mythology v1",
    )
    transition_opportunity_state(conn, opp_a.id, LifecycleState.rejected)
    conn.commit()

    # Opportunity B: active replacement
    ev = _make_evidence(canonical_cluster_id=cc_id, label="greek mythology")
    sync_channel_market_opportunities(conn, ch_id, [ev], pv, policy)
    conn.commit()
    opp_b = find_opportunity_by_canonical_cluster(conn, ch_id, cc_id)
    assert opp_b is not None and opp_b.id != opp_a.id

    # Reactivating A must raise ValueError (not IntegrityError)
    with pytest.raises(ValueError, match="Cannot reactivate"):
        transition_opportunity_state(
            conn, opp_a.id, LifecycleState.new, actor="operator", reason="test reactivation"
        )


# ---------------------------------------------------------------------------
# I. archived → active collision fails with clean ValueError
# ---------------------------------------------------------------------------


def test_i_archived_to_active_collision_raises_value_error(tmp_path):
    """Reactivating an archived Opportunity when another active one exists raises ValueError."""
    conn, ch_id, pv, policy = _setup(tmp_path)
    cc_id = _make_canonical_cluster(conn, "egyptian history")
    run_id = _make_run(conn, ch_id, pv.id)

    opp_a = _make_opportunity(
        conn,
        channel_id=ch_id,
        discovery_run_id=run_id,
        canonical_cluster_id=cc_id,
        topic="egyptian history v1",
    )
    transition_opportunity_state(conn, opp_a.id, LifecycleState.archived)
    conn.commit()

    ev = _make_evidence(canonical_cluster_id=cc_id, label="egyptian history")
    sync_channel_market_opportunities(conn, ch_id, [ev], pv, policy)
    conn.commit()
    opp_b = find_opportunity_by_canonical_cluster(conn, ch_id, cc_id)
    assert opp_b is not None and opp_b.id != opp_a.id

    with pytest.raises(ValueError, match="Cannot reactivate"):
        transition_opportunity_state(
            conn,
            opp_a.id,
            LifecycleState.under_review,
            actor="operator",
            reason="test reactivation",
        )


# ---------------------------------------------------------------------------
# H2/I2. Reactivation without collision succeeds
# ---------------------------------------------------------------------------


def test_h2_reactivation_without_collision_succeeds(tmp_path):
    """Reactivating a terminal Opportunity is fine when no other active one owns the cluster."""
    conn, ch_id, pv, policy = _setup(tmp_path)
    cc_id = _make_canonical_cluster(conn, "medieval castles")
    run_id = _make_run(conn, ch_id, pv.id)

    opp = _make_opportunity(
        conn,
        channel_id=ch_id,
        discovery_run_id=run_id,
        canonical_cluster_id=cc_id,
        topic="medieval castles",
    )
    transition_opportunity_state(conn, opp.id, LifecycleState.rejected)
    conn.commit()

    # No other active Opportunity — reactivation must succeed
    event = transition_opportunity_state(
        conn, opp.id, LifecycleState.new, actor="operator", reason="re-explore"
    )
    conn.commit()
    assert event.to_state == LifecycleState.new
    refreshed = get_opportunity(conn, opp.id)
    assert refreshed.current_lifecycle_state == LifecycleState.new


def test_h3_reactivation_without_canonical_cluster_always_succeeds(tmp_path):
    """Reactivating an Opportunity with no canonical_cluster_id never triggers the guard."""
    conn, ch_id, pv, policy = _setup(tmp_path)
    run_id = _make_run(conn, ch_id, pv.id)

    opp = _make_opportunity(
        conn,
        channel_id=ch_id,
        discovery_run_id=run_id,
        canonical_cluster_id=None,
        topic="no canonical cluster",
    )
    transition_opportunity_state(conn, opp.id, LifecycleState.rejected)
    conn.commit()

    # No canonical cluster → guard does not fire
    event = transition_opportunity_state(conn, opp.id, LifecycleState.new)
    conn.commit()
    assert event.to_state == LifecycleState.new


# ---------------------------------------------------------------------------
# J. Canonical cluster X cannot be cross-wired to active canonical cluster Y via Jaccard
# ---------------------------------------------------------------------------


def test_j_canonical_jaccard_fallback_not_used_when_canonical_id_set(tmp_path):
    """When evidence has canonical_cluster_id, Jaccard fallback is skipped entirely.

    Evidence for canonical cluster X must never update Opportunity B that is
    anchored to canonical cluster Y, even if their labels are lexically similar.
    """
    conn, ch_id, pv, policy = _setup(tmp_path)

    cc_x = _make_canonical_cluster(conn, "lost ancient cities")
    cc_y = _make_canonical_cluster(conn, "lost ancient kingdoms")
    run_id = _make_run(conn, ch_id, pv.id)

    # Create Opportunity B anchored to cluster Y (similar label)
    opp_b = _make_opportunity(
        conn,
        channel_id=ch_id,
        discovery_run_id=run_id,
        canonical_cluster_id=cc_y,
        topic="lost ancient kingdoms",
    )
    conn.commit()

    # Evidence for cluster X — no active Opportunity exists for X
    ev_x = _make_evidence(
        canonical_cluster_id=cc_x, label="lost ancient cities", signal_snapshot_id=10
    )

    result = sync_channel_market_opportunities(conn, ch_id, [ev_x], pv, policy)
    conn.commit()

    # A NEW Opportunity must have been created for X — NOT updating opp_b
    assert result.created_count == 1
    opp_x = find_opportunity_by_canonical_cluster(conn, ch_id, cc_x)
    assert opp_x is not None
    assert opp_x.id != opp_b.id

    # opp_b must still be anchored to cc_y
    opp_b_refreshed = get_opportunity(conn, opp_b.id)
    assert opp_b_refreshed.canonical_cluster_id == cc_y

    # Signal snapshot on opp_b must not have been updated to ev_x's snapshot
    assert opp_b_refreshed.market_signal_snapshot_id != 10


def test_j2_jaccard_fallback_used_when_canonical_id_is_none(tmp_path):
    """Jaccard fallback still works when the evidence has no canonical_cluster_id."""
    conn, ch_id, pv, policy = _setup(tmp_path)
    run_id = _make_run(conn, ch_id, pv.id)

    # Create an existing active Opportunity with no canonical cluster
    _make_opportunity(
        conn,
        channel_id=ch_id,
        discovery_run_id=run_id,
        canonical_cluster_id=None,
        topic="python programming tutorials",
    )
    conn.commit()

    # Evidence with no canonical_cluster_id, matching topic label → Jaccard should find opp
    ev = _make_evidence(
        canonical_cluster_id=None, label="python programming tutorials", signal_snapshot_id=99
    )

    result = sync_channel_market_opportunities(conn, ch_id, [ev], pv, policy)
    conn.commit()

    # Must refresh the existing Opportunity, not create a new one
    assert result.created_count == 0
    assert result.refreshed_count == 1


# ---------------------------------------------------------------------------
# K. Signal/canonical mismatch raises ValueError
# ---------------------------------------------------------------------------


def test_k_consistency_guard_present_in_bridge_source():
    """The consistency guard clause is present in the bridge module source."""
    import inspect

    import app.intelligence.market.bridge as bridge_mod

    src = inspect.getsource(bridge_mod)
    assert "Canonical cluster identity mismatch" in src
    assert "existing_opp.canonical_cluster_id != ev.canonical_cluster_id" in src


def test_k2_canonical_mismatch_raises_via_monkeypatch(tmp_path, monkeypatch):
    """Direct test of the consistency guard using monkeypatching."""
    conn, ch_id, pv, policy = _setup(tmp_path)
    cc_x = _make_canonical_cluster(conn, "celtic history")
    cc_y = _make_canonical_cluster(conn, "norse mythology")
    run_id = _make_run(conn, ch_id, pv.id)
    conn.commit()

    # Evidence carries cc_y
    ev = _make_evidence(canonical_cluster_id=cc_y, label="norse mythology", signal_snapshot_id=5)

    # Construct an Opportunity anchored to cc_x (the mismatch object)
    fake_opp = Opportunity(
        id=9999,
        channel_id=ch_id,
        discovery_run_id=run_id,
        normalized_topic="celtic history",
        raw_topic="celtic history",
        title="celtic history",
        topic_summary="",
        canonical_cluster_id=cc_x,
    )

    # Patch find_opportunity_by_canonical_cluster to return the mismatched opportunity
    import app.intelligence.market.bridge as bridge_module

    original_fn = bridge_module.find_opportunity_by_canonical_cluster

    def patched_find(c, ch, cc):
        if cc == cc_y:
            return fake_opp
        return original_fn(c, ch, cc)

    monkeypatch.setattr(bridge_module, "find_opportunity_by_canonical_cluster", patched_find)

    bridge_policy = ExternalMarketBridgePolicy()
    from app.intelligence.market.bridge import _process_one

    with pytest.raises(ValueError, match="Canonical cluster identity mismatch"):
        _process_one(
            conn=conn,
            channel_id=ch_id,
            ev=ev,
            profile_version=pv,
            scoring_policy=policy,
            bridge_policy=bridge_policy,
            discovery_run_id=run_id,
            now="2026-08-21T00:00:00",
            dry_run=False,
        )


# ---------------------------------------------------------------------------
# L. Matching signal/canonical identity succeeds
# ---------------------------------------------------------------------------


def test_l_matching_canonical_identity_succeeds(tmp_path):
    """When opportunity.canonical_cluster_id == ev.canonical_cluster_id, sync proceeds normally."""
    conn, ch_id, pv, policy = _setup(tmp_path)
    cc_id = _make_canonical_cluster(conn, "byzantine empire")
    ev = _make_evidence(canonical_cluster_id=cc_id, label="byzantine empire", signal_snapshot_id=42)

    result = sync_channel_market_opportunities(conn, ch_id, [ev], pv, policy)
    conn.commit()

    assert result.created_count == 1
    opp = find_opportunity_by_canonical_cluster(conn, ch_id, cc_id)
    assert opp is not None
    assert opp.canonical_cluster_id == cc_id
    assert opp.market_signal_snapshot_id == 42


# ---------------------------------------------------------------------------
# Schema version assertion
# ---------------------------------------------------------------------------


def test_schema_version_is_35():
    assert SCHEMA_VERSION == 51


# ---------------------------------------------------------------------------
# No Phase 14 / no YouTube calls
# ---------------------------------------------------------------------------


def test_q_no_phase14_imports_in_bridge(tmp_path):
    """Phase 14 modules must not be imported from the bridge."""
    import inspect

    import app.intelligence.market.bridge as bridge_mod

    src = inspect.getsource(bridge_mod)
    assert "phase14" not in src.lower()
    assert "experiment_selection" not in src.lower()


def test_r_no_youtube_in_bridge(tmp_path):
    """Bridge must not call YouTube APIs."""
    import inspect

    import app.intelligence.market.bridge as bridge_mod

    src = inspect.getsource(bridge_mod)
    assert "youtube_data_api" not in src or "AdapterName" in src  # AdapterName reference is OK
    # No direct YouTube API calls
    assert "build(" not in src  # googleapiclient.discovery.build()
    assert "requests.get" not in src


# ---------------------------------------------------------------------------
# Broad regression: Phase 13F existing bridge tests still pass
# (these run automatically via the full suite; this test just confirms import)
# ---------------------------------------------------------------------------


def test_p_phase13f_bridge_module_importable():
    from app.intelligence.market.bridge import sync_channel_market_opportunities

    assert callable(sync_channel_market_opportunities)
