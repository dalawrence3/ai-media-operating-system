"""Phase 13F tests: External Market Intelligence → Opportunity Evidence / Scoring Bridge.

Tests A–BB (52+ tests) covering:
- Schema v34 migration
- AdapterName.market_intelligence
- Opportunity.canonical_cluster_id / market_signal_snapshot_id
- Canonical cluster → channel Opportunity identity
- Multi-channel same-cluster isolation
- Same channel + canonical cluster deduplication
- Newer signal refresh / evidence history preservation
- Signal → factor mappings (all 6 factors)
- Competition directionality (inversion: 1 - saturation)
- Confidence modulation
- Maturity gate
- Missing vs zero signal handling
- No raw-signal double counting
- Bridge policy versioning
- ExternalMarketBridgePolicy
- Dry-run semantics
- Lifecycle / observation history
- Provenance evidence rows
- No YouTube calls / no Phase 12C mutation / no Phase 14
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from app.core.database import open_db
from app.intelligence.adapters.base import VALID_EVIDENCE_TYPES
from app.intelligence.market.bridge import sync_channel_market_opportunities
from app.intelligence.market.bridge_models import (
    MATURITY_LEVELS,
    ExternalMarketBridgePolicy,
)
from app.intelligence.market.interpretation_models import (
    ExternalMarketOpportunityEvidence,
)
from app.intelligence.models import AdapterName, LifecycleState, Opportunity
from app.intelligence.repository import (
    create_opportunity,
    find_opportunity_by_canonical_cluster,
    get_opportunity,
    list_evidence,
    list_observations,
    list_opportunities,
    update_opportunity_signal_snapshot,
)

# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------

_NOW = "2026-08-21T00:00:00"


def _open_test_db(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / "test.db")


def _make_channel_and_profile(
    conn: sqlite3.Connection, *, niche: str = "python programming"
) -> tuple[int, Any]:
    """Create channel + active profile; return (channel_id, profile_version)."""
    from app.intelligence.repository import create_channel_full

    ch, pv, _strat, _cap = create_channel_full(
        conn,
        channel_name=f"test_{niche[:20].replace(' ', '_')}",
        primary_niche=niche,
        audience_description="software engineers",
    )
    return ch.id, pv


def _make_scoring_policy(conn: sqlite3.Connection, channel_id: int) -> Any:
    from app.intelligence.models import MissingDataPolicy, ScoringPolicy
    from app.intelligence.repository import create_scoring_policy

    policy = ScoringPolicy(
        channel_id=channel_id,
        label="test-policy",
        policy_version="1.0.0",
        weight_trend_strength=0.05,
        weight_audience_demand=0.20,
        weight_competition=0.15,
        weight_evergreen_value=0.20,
        weight_audience_fit=0.30,
        weight_content_novelty=0.10,
        missing_trend_strength=MissingDataPolicy.reweight_available,
        missing_audience_demand=MissingDataPolicy.reweight_available,
        missing_competition=MissingDataPolicy.reweight_available,
        missing_evergreen_value=MissingDataPolicy.reweight_available,
        missing_audience_fit=MissingDataPolicy.reweight_available,
        missing_content_novelty=MissingDataPolicy.reweight_available,
    )
    return create_scoring_policy(conn, policy)


def _make_canonical_cluster(conn: sqlite3.Connection, label: str = "python tutorials") -> int:
    """Insert a canonical cluster and return its id."""
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
    cluster_id: int = 1,
    canonical_cluster_id: int | None = None,
    *,
    demand: float = 0.70,
    saturation: float = 0.30,
    freshness: float = 0.65,
    momentum: float = 0.60,
    persistence: float = 0.75,
    confidence: float = 0.80,
    maturity: str = "directional",
    signal_snapshot_id: int = 1,
    interpretation_run_id: int = 1,
    label: str = "python tutorials",
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
        demand_score=demand,
        saturation_score=saturation,
        freshness_score=freshness,
        momentum_score=momentum,
        persistence_score=persistence,
        confidence=confidence,
        signal_maturity=maturity,
        state_label="active",
        supporting_video_count=10,
        supporting_creator_count=5,
        velocity_tracked_video_count=3,
        signal_snapshot_id=signal_snapshot_id,
        interpretation_run_id=interpretation_run_id,
    )


def _setup(
    tmp_path: Path, niche: str = "python programming"
) -> tuple[sqlite3.Connection, int, Any, Any]:
    """Return (conn, channel_id, profile_version, scoring_policy)."""
    conn = _open_test_db(tmp_path)
    channel_id, pv = _make_channel_and_profile(conn, niche=niche)
    policy = _make_scoring_policy(conn, channel_id)
    return conn, channel_id, pv, policy


# ---------------------------------------------------------------------------
# A. Schema v34 — canonical_cluster_id in opportunities
# ---------------------------------------------------------------------------


def test_a_schema_v34_opportunities_has_canonical_cluster_id(tmp_path):
    conn = _open_test_db(tmp_path)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(opportunities)").fetchall()}
    assert "canonical_cluster_id" in cols
    assert "market_signal_snapshot_id" in cols


def test_b_schema_v34_discovery_runs_accepts_market_intelligence(tmp_path):
    conn, channel_id, pv, _ = _setup(tmp_path)
    from app.intelligence.models import DiscoveryRun
    from app.intelligence.repository import create_discovery_run

    run = create_discovery_run(
        conn,
        DiscoveryRun(
            channel_id=channel_id,
            profile_version_id=pv.id,
            adapter_name=AdapterName.market_intelligence,
            started_at=datetime.fromisoformat(_NOW),
        ),
    )
    assert run.id is not None
    assert run.adapter_name == AdapterName.market_intelligence


def test_c_schema_v34_canonical_unique_index_enforced(tmp_path):
    conn, channel_id, pv, _ = _setup(tmp_path)
    from app.intelligence.models import DiscoveryRun
    from app.intelligence.repository import create_discovery_run

    run = create_discovery_run(
        conn,
        DiscoveryRun(
            channel_id=channel_id,
            profile_version_id=pv.id,
            adapter_name=AdapterName.market_intelligence,
            started_at=datetime.fromisoformat(_NOW),
        ),
    )
    cc_id = _make_canonical_cluster(conn)
    create_opportunity(
        conn,
        Opportunity(
            channel_id=channel_id,
            discovery_run_id=run.id,
            normalized_topic="python tutorials",
            raw_topic="Python Tutorials",
            canonical_cluster_id=cc_id,
        ),
    )
    # Second INSERT with same (channel_id, canonical_cluster_id) must fail
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO opportunities (channel_id, discovery_run_id, normalized_topic, raw_topic, "
            "current_lifecycle_state, created_at, updated_at, canonical_cluster_id) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (channel_id, run.id, "python tutorials 2", "Python 2", "new", _NOW, _NOW, cc_id),
        )


# ---------------------------------------------------------------------------
# D. AdapterName enum
# ---------------------------------------------------------------------------


def test_d_adapter_name_market_intelligence_value():
    assert AdapterName.market_intelligence == "market_intelligence"
    assert AdapterName.market_intelligence in AdapterName.__members__.values()


def test_e_adapter_name_backward_compatible():
    assert AdapterName.manual == "manual"
    assert AdapterName.youtube_data_api == "youtube_data_api"


# ---------------------------------------------------------------------------
# F. VALID_EVIDENCE_TYPES extended
# ---------------------------------------------------------------------------


def test_f_market_evidence_types_in_valid_set():
    expected = {
        "market_demand_score",
        "market_saturation_score",
        "market_freshness_score",
        "market_momentum_score",
        "market_persistence_score",
        "market_confidence",
        "market_maturity",
        "market_state_label",
        "market_canonical_cluster_id",
        "market_cluster_snapshot_id",
        "market_signal_snapshot_id",
        "market_interpretation_run_id",
    }
    assert expected <= VALID_EVIDENCE_TYPES


def test_g_legacy_evidence_types_still_present():
    legacy = {
        "view_count",
        "like_count",
        "comment_count",
        "video_count_in_niche",
        "top_video_age_days",
        "incumbent_subscriber_count",
        "manual_demand_note",
    }
    assert legacy <= VALID_EVIDENCE_TYPES


# ---------------------------------------------------------------------------
# H. ExternalMarketBridgePolicy
# ---------------------------------------------------------------------------


def test_h_bridge_policy_defaults():
    p = ExternalMarketBridgePolicy()
    assert p.version == "1.0.0"
    assert p.min_maturity_level == "directional"
    assert p.min_confidence_for_present == 0.30
    assert p.max_signal_age_days == 90


def test_i_bridge_policy_meets_maturity_gate():
    p = ExternalMarketBridgePolicy(min_maturity_level="directional")
    assert not p.meets_maturity_gate("insufficient")
    assert not p.meets_maturity_gate("exploratory")
    assert p.meets_maturity_gate("directional")
    assert p.meets_maturity_gate("actionable")


def test_j_bridge_policy_maturity_levels_ordered():
    for i, level in enumerate(MATURITY_LEVELS):
        p = ExternalMarketBridgePolicy(min_maturity_level=level)
        for prior in MATURITY_LEVELS[:i]:
            assert not p.meets_maturity_gate(prior), f"{prior} should fail gate {level}"
        for at_or_above in MATURITY_LEVELS[i:]:
            assert p.meets_maturity_gate(at_or_above)


def test_k_bridge_policy_confidence_status():
    p = ExternalMarketBridgePolicy(min_confidence_for_present=0.30)
    assert p.evidence_status(0.29) == "insufficient"
    assert p.evidence_status(0.30) == "present"
    assert p.evidence_status(1.00) == "present"


def test_l_bridge_policy_custom_maturity_gate():
    p = ExternalMarketBridgePolicy(min_maturity_level="actionable")
    assert not p.meets_maturity_gate("directional")
    assert p.meets_maturity_gate("actionable")


# ---------------------------------------------------------------------------
# M. Canonical cluster → channel Opportunity (core bridge)
# ---------------------------------------------------------------------------


def test_m_canonical_cluster_creates_channel_opportunity(tmp_path):
    conn, channel_id, pv, policy = _setup(tmp_path)
    cc_id = _make_canonical_cluster(conn)
    ev = _make_evidence(canonical_cluster_id=cc_id, signal_snapshot_id=1)
    result = sync_channel_market_opportunities(conn, channel_id, [ev], pv, policy)
    conn.commit()
    assert result.created_count == 1
    assert result.skipped_count == 0
    opps = list_opportunities(conn, channel_id)
    assert len(opps) == 1
    assert opps[0].canonical_cluster_id == cc_id
    assert opps[0].normalized_topic == ev.normalized_label


def test_n_same_channel_same_canonical_deduplicates(tmp_path):
    conn, channel_id, pv, policy = _setup(tmp_path)
    cc_id = _make_canonical_cluster(conn)
    ev = _make_evidence(canonical_cluster_id=cc_id, signal_snapshot_id=1)

    r1 = sync_channel_market_opportunities(conn, channel_id, [ev], pv, policy)
    conn.commit()
    assert r1.created_count == 1

    ev2 = _make_evidence(canonical_cluster_id=cc_id, signal_snapshot_id=2, momentum=0.9)
    r2 = sync_channel_market_opportunities(conn, channel_id, [ev2], pv, policy)
    conn.commit()
    assert r2.created_count == 0
    assert r2.refreshed_count == 1

    opps = list_opportunities(conn, channel_id)
    assert len(opps) == 1  # no duplicate


def test_o_different_channels_same_cluster_are_independent(tmp_path):
    conn = _open_test_db(tmp_path)
    channel_id1, pv1 = _make_channel_and_profile(conn, niche="python programming")
    policy1 = _make_scoring_policy(conn, channel_id1)
    channel_id2, pv2 = _make_channel_and_profile(conn, niche="data science")
    policy2 = _make_scoring_policy(conn, channel_id2)

    cc_id = _make_canonical_cluster(conn)
    ev = _make_evidence(canonical_cluster_id=cc_id)

    sync_channel_market_opportunities(conn, channel_id1, [ev], pv1, policy1)
    sync_channel_market_opportunities(conn, channel_id2, [ev], pv2, policy2)
    conn.commit()

    opps1 = list_opportunities(conn, channel_id1)
    opps2 = list_opportunities(conn, channel_id2)
    assert len(opps1) == 1
    assert len(opps2) == 1
    assert opps1[0].id != opps2[0].id


def test_p_newer_signal_adds_observation_preserves_history(tmp_path):
    conn, channel_id, pv, policy = _setup(tmp_path)
    cc_id = _make_canonical_cluster(conn)
    ev1 = _make_evidence(canonical_cluster_id=cc_id, signal_snapshot_id=1, momentum=0.5)
    sync_channel_market_opportunities(conn, channel_id, [ev1], pv, policy)

    ev2 = _make_evidence(canonical_cluster_id=cc_id, signal_snapshot_id=2, momentum=0.9)
    sync_channel_market_opportunities(conn, channel_id, [ev2], pv, policy)
    conn.commit()

    opp = list_opportunities(conn, channel_id)[0]
    obs = list_observations(conn, opp.id)
    assert len(obs) == 2  # two observations — history preserved


def test_q_signal_snapshot_updated_after_refresh(tmp_path):
    conn, channel_id, pv, policy = _setup(tmp_path)
    cc_id = _make_canonical_cluster(conn)
    ev1 = _make_evidence(canonical_cluster_id=cc_id, signal_snapshot_id=10)
    sync_channel_market_opportunities(conn, channel_id, [ev1], pv, policy)
    conn.commit()

    ev2 = _make_evidence(canonical_cluster_id=cc_id, signal_snapshot_id=20)
    sync_channel_market_opportunities(conn, channel_id, [ev2], pv, policy)
    conn.commit()

    opp = list_opportunities(conn, channel_id)[0]
    assert opp.market_signal_snapshot_id == 20


# ---------------------------------------------------------------------------
# R. find_opportunity_by_canonical_cluster
# ---------------------------------------------------------------------------


def test_r_find_by_canonical_cluster_returns_correct_opp(tmp_path):
    conn, channel_id, pv, _ = _setup(tmp_path)
    cc_id = _make_canonical_cluster(conn)
    from app.intelligence.models import DiscoveryRun
    from app.intelligence.repository import create_discovery_run

    run = create_discovery_run(
        conn,
        DiscoveryRun(
            channel_id=channel_id,
            profile_version_id=pv.id,
            adapter_name=AdapterName.market_intelligence,
            started_at=datetime.fromisoformat(_NOW),
        ),
    )
    opp = create_opportunity(
        conn,
        Opportunity(
            channel_id=channel_id,
            discovery_run_id=run.id,
            normalized_topic="python tutorials",
            raw_topic="Python Tutorials",
            canonical_cluster_id=cc_id,
        ),
    )
    found = find_opportunity_by_canonical_cluster(conn, channel_id, cc_id)
    assert found is not None
    assert found.id == opp.id


def test_s_find_by_canonical_cluster_ignores_rejected(tmp_path):
    conn, channel_id, pv, _ = _setup(tmp_path)
    cc_id = _make_canonical_cluster(conn)
    from app.intelligence.models import DiscoveryRun
    from app.intelligence.repository import create_discovery_run, transition_opportunity_state

    run = create_discovery_run(
        conn,
        DiscoveryRun(
            channel_id=channel_id,
            profile_version_id=pv.id,
            adapter_name=AdapterName.market_intelligence,
            started_at=datetime.fromisoformat(_NOW),
        ),
    )
    opp = create_opportunity(
        conn,
        Opportunity(
            channel_id=channel_id,
            discovery_run_id=run.id,
            normalized_topic="python tutorials",
            raw_topic="Python Tutorials",
            canonical_cluster_id=cc_id,
        ),
    )
    transition_opportunity_state(conn, opp.id, LifecycleState.rejected)
    found = find_opportunity_by_canonical_cluster(conn, channel_id, cc_id)
    assert found is None


def test_t_find_by_canonical_cluster_returns_none_missing(tmp_path):
    conn, channel_id, pv, _ = _setup(tmp_path)
    found = find_opportunity_by_canonical_cluster(conn, channel_id, 9999)
    assert found is None


# ---------------------------------------------------------------------------
# U. Evidence rows emitted correctly
# ---------------------------------------------------------------------------


def test_u_evidence_rows_emitted_for_all_signals(tmp_path):
    conn, channel_id, pv, policy = _setup(tmp_path)
    ev = _make_evidence()
    sync_channel_market_opportunities(conn, channel_id, [ev], pv, policy)
    conn.commit()

    opp = list_opportunities(conn, channel_id)[0]
    obs = list_observations(conn, opp.id)
    assert len(obs) == 1

    evidence_rows = list_evidence(conn, obs[0].id)
    ev_types = {e.evidence_type for e in evidence_rows}
    assert "market_demand_score" in ev_types
    assert "market_saturation_score" in ev_types
    assert "market_freshness_score" in ev_types
    assert "market_momentum_score" in ev_types
    assert "market_persistence_score" in ev_types
    assert "market_confidence" in ev_types
    assert "market_maturity" in ev_types
    assert "market_signal_snapshot_id" in ev_types
    assert "market_interpretation_run_id" in ev_types


def test_v_provenance_evidence_values_match_input(tmp_path):
    conn, channel_id, pv, policy = _setup(tmp_path)
    cc_id = _make_canonical_cluster(conn, label="python tutorials")
    ev = _make_evidence(canonical_cluster_id=cc_id, signal_snapshot_id=99, interpretation_run_id=7)
    sync_channel_market_opportunities(conn, channel_id, [ev], pv, policy)
    conn.commit()

    opp = list_opportunities(conn, channel_id)[0]
    obs = list_observations(conn, opp.id)
    evidence_rows = list_evidence(conn, obs[0].id)
    by_type = {e.evidence_type: e for e in evidence_rows}

    assert by_type["market_signal_snapshot_id"].evidence_value == 99.0
    assert by_type["market_interpretation_run_id"].evidence_value == 7.0
    assert by_type["market_demand_score"].evidence_value == pytest.approx(0.70)


def test_w_observation_adapter_name_is_market_intelligence(tmp_path):
    conn, channel_id, pv, policy = _setup(tmp_path)
    ev = _make_evidence()
    sync_channel_market_opportunities(conn, channel_id, [ev], pv, policy)
    conn.commit()

    opp = list_opportunities(conn, channel_id)[0]
    obs = list_observations(conn, opp.id)
    assert obs[0].adapter_name == AdapterName.market_intelligence


# ---------------------------------------------------------------------------
# X. Signal → Factor mapping: trend_strength
# ---------------------------------------------------------------------------


def test_x_trend_strength_uses_market_path(tmp_path):
    from app.intelligence.models import (
        FactorContext,
        FactorStatus,
        OpportunityObservation,
        OpportunitySourceEvidence,
        SourceQualityTier,
    )
    from app.intelligence.scoring.factors import compute_trend_strength

    conn, channel_id, pv, policy = _setup(tmp_path)
    _make_evidence(momentum=0.80, freshness=0.60, confidence=1.0)

    # Build synthetic context
    synth_opp = Opportunity(
        id=1,
        channel_id=channel_id,
        discovery_run_id=1,
        normalized_topic="python tutorials",
        raw_topic="Python Tutorials",
    )
    synth_obs = OpportunityObservation(
        id=1,
        opportunity_id=1,
        discovery_run_id=1,
        adapter_name=AdapterName.market_intelligence,
        source_quality_tier=SourceQualityTier.medium,
    )
    evs = [
        OpportunitySourceEvidence(
            id=1,
            observation_id=1,
            opportunity_id=1,
            evidence_type="market_momentum_score",
            evidence_value=0.80,
            source_label="test",
        ),
        OpportunitySourceEvidence(
            id=2,
            observation_id=1,
            opportunity_id=1,
            evidence_type="market_freshness_score",
            evidence_value=0.60,
            source_label="test",
        ),
        OpportunitySourceEvidence(
            id=3,
            observation_id=1,
            opportunity_id=1,
            evidence_type="market_confidence",
            evidence_value=1.0,
            source_label="test",
        ),
    ]
    ctx = FactorContext(
        opportunity=synth_opp,
        profile=pv,
        observations=[synth_obs],
        evidence={1: evs},
        best_similarity=None,
        matched_opportunity_id=None,
        matched_normalized_topic=None,
    )
    result = compute_trend_strength(ctx, policy)
    expected = 0.60 * 0.80 + 0.40 * 0.60  # = 0.72
    assert result.raw_score == pytest.approx(expected, abs=0.01)
    assert result.status == FactorStatus.present
    assert "market" in (result.notes or "")


def test_y_trend_strength_confidence_modulates_score(tmp_path):
    from app.intelligence.models import (
        FactorContext,
        OpportunityObservation,
        OpportunitySourceEvidence,
        SourceQualityTier,
    )
    from app.intelligence.scoring.factors import compute_trend_strength

    _, channel_id, pv, policy = _setup(tmp_path)
    synth_opp = Opportunity(
        id=1, channel_id=channel_id, discovery_run_id=1, normalized_topic="test", raw_topic="Test"
    )
    synth_obs = OpportunityObservation(
        id=1,
        opportunity_id=1,
        discovery_run_id=1,
        adapter_name=AdapterName.market_intelligence,
        source_quality_tier=SourceQualityTier.medium,
    )
    evs = [
        OpportunitySourceEvidence(
            id=1,
            observation_id=1,
            opportunity_id=1,
            evidence_type="market_momentum_score",
            evidence_value=1.0,
            source_label="test",
        ),
        OpportunitySourceEvidence(
            id=2,
            observation_id=1,
            opportunity_id=1,
            evidence_type="market_freshness_score",
            evidence_value=1.0,
            source_label="test",
        ),
        OpportunitySourceEvidence(
            id=3,
            observation_id=1,
            opportunity_id=1,
            evidence_type="market_confidence",
            evidence_value=0.5,
            source_label="test",
        ),
    ]
    ctx = FactorContext(
        opportunity=synth_opp,
        profile=pv,
        observations=[synth_obs],
        evidence={1: evs},
        best_similarity=None,
        matched_opportunity_id=None,
        matched_normalized_topic=None,
    )
    result = compute_trend_strength(ctx, policy)
    # blended = 0.60*1 + 0.40*1 = 1.0; conf=0.5 → final=0.5
    assert result.raw_score == pytest.approx(0.5, abs=0.01)


def test_z_trend_strength_falls_back_to_legacy_without_market(tmp_path):
    from app.intelligence.models import (
        FactorContext,
        FactorStatus,
        OpportunityObservation,
        OpportunitySourceEvidence,
        SourceQualityTier,
    )
    from app.intelligence.scoring.factors import compute_trend_strength

    _, channel_id, pv, policy = _setup(tmp_path)
    synth_opp = Opportunity(
        id=1, channel_id=channel_id, discovery_run_id=1, normalized_topic="test", raw_topic="Test"
    )
    synth_obs = OpportunityObservation(
        id=1,
        opportunity_id=1,
        discovery_run_id=1,
        adapter_name=AdapterName.youtube_data_api,
        source_quality_tier=SourceQualityTier.medium,
    )
    evs = [
        OpportunitySourceEvidence(
            id=1,
            observation_id=1,
            opportunity_id=1,
            evidence_type="view_count",
            evidence_value=100_000.0,
            source_label="test",
        ),
    ]
    ctx = FactorContext(
        opportunity=synth_opp,
        profile=pv,
        observations=[synth_obs],
        evidence={1: evs},
        best_similarity=None,
        matched_opportunity_id=None,
        matched_normalized_topic=None,
    )
    result = compute_trend_strength(ctx, policy)
    assert result.raw_score is not None
    assert result.status == FactorStatus.present
    assert "market" not in (result.notes or "")


# ---------------------------------------------------------------------------
# AA. Signal → Factor mapping: audience_demand
# ---------------------------------------------------------------------------


def test_aa_demand_uses_market_demand_score(tmp_path):
    from app.intelligence.models import (
        FactorContext,
        FactorStatus,
        OpportunityObservation,
        OpportunitySourceEvidence,
        SourceQualityTier,
    )
    from app.intelligence.scoring.factors import compute_audience_demand

    _, channel_id, pv, policy = _setup(tmp_path)
    synth_opp = Opportunity(
        id=1, channel_id=channel_id, discovery_run_id=1, normalized_topic="test", raw_topic="Test"
    )
    synth_obs = OpportunityObservation(
        id=1,
        opportunity_id=1,
        discovery_run_id=1,
        adapter_name=AdapterName.market_intelligence,
        source_quality_tier=SourceQualityTier.medium,
    )
    evs = [
        OpportunitySourceEvidence(
            id=1,
            observation_id=1,
            opportunity_id=1,
            evidence_type="market_demand_score",
            evidence_value=0.80,
            source_label="test",
        ),
        OpportunitySourceEvidence(
            id=2,
            observation_id=1,
            opportunity_id=1,
            evidence_type="market_confidence",
            evidence_value=0.90,
            source_label="test",
        ),
    ]
    ctx = FactorContext(
        opportunity=synth_opp,
        profile=pv,
        observations=[synth_obs],
        evidence={1: evs},
        best_similarity=None,
        matched_opportunity_id=None,
        matched_normalized_topic=None,
    )
    result = compute_audience_demand(ctx, policy)
    assert result.raw_score == pytest.approx(0.80 * 0.90, abs=0.01)
    assert result.status == FactorStatus.present


def test_ab_demand_no_double_counting_raw_views_with_market(tmp_path):
    """When market_demand_score is present, raw view_count must NOT also drive the score."""
    from app.intelligence.models import (
        FactorContext,
        OpportunityObservation,
        OpportunitySourceEvidence,
        SourceQualityTier,
    )
    from app.intelligence.scoring.factors import compute_audience_demand

    _, channel_id, pv, policy = _setup(tmp_path)
    synth_opp = Opportunity(
        id=1, channel_id=channel_id, discovery_run_id=1, normalized_topic="test", raw_topic="Test"
    )
    synth_obs = OpportunityObservation(
        id=1,
        opportunity_id=1,
        discovery_run_id=1,
        adapter_name=AdapterName.market_intelligence,
        source_quality_tier=SourceQualityTier.medium,
    )
    evs = [
        OpportunitySourceEvidence(
            id=1,
            observation_id=1,
            opportunity_id=1,
            evidence_type="market_demand_score",
            evidence_value=0.50,
            source_label="test",
        ),
        # Also a huge view_count that would inflate if double-counted
        OpportunitySourceEvidence(
            id=2,
            observation_id=1,
            opportunity_id=1,
            evidence_type="view_count",
            evidence_value=10_000_000.0,
            source_label="test",
        ),
    ]
    ctx = FactorContext(
        opportunity=synth_opp,
        profile=pv,
        observations=[synth_obs],
        evidence={1: evs},
        best_similarity=None,
        matched_opportunity_id=None,
        matched_normalized_topic=None,
    )
    result = compute_audience_demand(ctx, policy)
    # Market path should win: score = 0.50 * 1.0 = 0.50 (not 1.0 from views)
    assert result.raw_score == pytest.approx(0.50, abs=0.02)


# ---------------------------------------------------------------------------
# AC. Signal → Factor mapping: competition (directionality)
# ---------------------------------------------------------------------------


def test_ac_competition_inverts_saturation(tmp_path):
    from app.intelligence.models import (
        FactorContext,
        FactorStatus,
        OpportunityObservation,
        OpportunitySourceEvidence,
        SourceQualityTier,
    )
    from app.intelligence.scoring.factors import compute_competition

    _, channel_id, pv, policy = _setup(tmp_path)
    synth_opp = Opportunity(
        id=1, channel_id=channel_id, discovery_run_id=1, normalized_topic="test", raw_topic="Test"
    )
    synth_obs = OpportunityObservation(
        id=1,
        opportunity_id=1,
        discovery_run_id=1,
        adapter_name=AdapterName.market_intelligence,
        source_quality_tier=SourceQualityTier.medium,
    )
    evs = [
        OpportunitySourceEvidence(
            id=1,
            observation_id=1,
            opportunity_id=1,
            evidence_type="market_saturation_score",
            evidence_value=0.30,
            source_label="test",
        ),
        OpportunitySourceEvidence(
            id=2,
            observation_id=1,
            opportunity_id=1,
            evidence_type="market_confidence",
            evidence_value=1.0,
            source_label="test",
        ),
    ]
    ctx = FactorContext(
        opportunity=synth_opp,
        profile=pv,
        observations=[synth_obs],
        evidence={1: evs},
        best_similarity=None,
        matched_opportunity_id=None,
        matched_normalized_topic=None,
    )
    result = compute_competition(ctx, policy)
    # attractiveness = 1 - 0.30 = 0.70; conf=1.0 → score=0.70
    assert result.raw_score == pytest.approx(0.70, abs=0.01)
    assert result.status == FactorStatus.present
    assert "saturation" in (result.notes or "")


def test_ad_competition_high_saturation_low_score(tmp_path):
    from app.intelligence.models import (
        FactorContext,
        OpportunityObservation,
        OpportunitySourceEvidence,
        SourceQualityTier,
    )
    from app.intelligence.scoring.factors import compute_competition

    _, channel_id, pv, policy = _setup(tmp_path)
    synth_opp = Opportunity(
        id=1, channel_id=channel_id, discovery_run_id=1, normalized_topic="test", raw_topic="Test"
    )
    synth_obs = OpportunityObservation(
        id=1,
        opportunity_id=1,
        discovery_run_id=1,
        adapter_name=AdapterName.market_intelligence,
        source_quality_tier=SourceQualityTier.medium,
    )
    evs = [
        OpportunitySourceEvidence(
            id=1,
            observation_id=1,
            opportunity_id=1,
            evidence_type="market_saturation_score",
            evidence_value=0.95,
            source_label="test",
        ),
    ]
    ctx = FactorContext(
        opportunity=synth_opp,
        profile=pv,
        observations=[synth_obs],
        evidence={1: evs},
        best_similarity=None,
        matched_opportunity_id=None,
        matched_normalized_topic=None,
    )
    result = compute_competition(ctx, policy)
    assert result.raw_score == pytest.approx(0.05, abs=0.02)  # nearly 0 — very saturated


def test_ae_competition_confidence_modulates_attractiveness(tmp_path):
    from app.intelligence.models import (
        FactorContext,
        OpportunityObservation,
        OpportunitySourceEvidence,
        SourceQualityTier,
    )
    from app.intelligence.scoring.factors import compute_competition

    _, channel_id, pv, policy = _setup(tmp_path)
    synth_opp = Opportunity(
        id=1, channel_id=channel_id, discovery_run_id=1, normalized_topic="test", raw_topic="Test"
    )
    synth_obs = OpportunityObservation(
        id=1,
        opportunity_id=1,
        discovery_run_id=1,
        adapter_name=AdapterName.market_intelligence,
        source_quality_tier=SourceQualityTier.medium,
    )
    evs = [
        OpportunitySourceEvidence(
            id=1,
            observation_id=1,
            opportunity_id=1,
            evidence_type="market_saturation_score",
            evidence_value=0.0,
            source_label="test",
        ),
        OpportunitySourceEvidence(
            id=2,
            observation_id=1,
            opportunity_id=1,
            evidence_type="market_confidence",
            evidence_value=0.5,
            source_label="test",
        ),
    ]
    ctx = FactorContext(
        opportunity=synth_opp,
        profile=pv,
        observations=[synth_obs],
        evidence={1: evs},
        best_similarity=None,
        matched_opportunity_id=None,
        matched_normalized_topic=None,
    )
    result = compute_competition(ctx, policy)
    # saturation=0.0 → attractiveness=1.0; conf=0.5 → 0.5
    assert result.raw_score == pytest.approx(0.5, abs=0.01)


def test_af_competition_absent_without_any_signal(tmp_path):
    from app.intelligence.models import (
        FactorContext,
        FactorStatus,
        OpportunityObservation,
        SourceQualityTier,
    )
    from app.intelligence.scoring.factors import compute_competition

    _, channel_id, pv, policy = _setup(tmp_path)
    synth_opp = Opportunity(
        id=1, channel_id=channel_id, discovery_run_id=1, normalized_topic="test", raw_topic="Test"
    )
    synth_obs = OpportunityObservation(
        id=1,
        opportunity_id=1,
        discovery_run_id=1,
        adapter_name=AdapterName.youtube_data_api,
        source_quality_tier=SourceQualityTier.medium,
    )
    ctx = FactorContext(
        opportunity=synth_opp,
        profile=pv,
        observations=[synth_obs],
        evidence={1: []},
        best_similarity=None,
        matched_opportunity_id=None,
        matched_normalized_topic=None,
    )
    result = compute_competition(ctx, policy)
    assert result.raw_score is None
    assert result.status == FactorStatus.absent


# ---------------------------------------------------------------------------
# AG. Signal → Factor mapping: evergreen_value
# ---------------------------------------------------------------------------


def test_ag_evergreen_blends_persistence_with_lexical(tmp_path):
    from app.intelligence.models import (
        FactorContext,
        OpportunityObservation,
        OpportunitySourceEvidence,
        SourceQualityTier,
    )
    from app.intelligence.scoring.factors import compute_evergreen_value

    _, channel_id, pv, policy = _setup(tmp_path)
    synth_opp = Opportunity(
        id=1,
        channel_id=channel_id,
        discovery_run_id=1,
        normalized_topic="how to learn python",
        raw_topic="How to Learn Python",
    )
    synth_obs = OpportunityObservation(
        id=1,
        opportunity_id=1,
        discovery_run_id=1,
        adapter_name=AdapterName.market_intelligence,
        source_quality_tier=SourceQualityTier.medium,
    )
    evs = [
        OpportunitySourceEvidence(
            id=1,
            observation_id=1,
            opportunity_id=1,
            evidence_type="market_persistence_score",
            evidence_value=0.90,
            source_label="test",
        ),
    ]
    ctx = FactorContext(
        opportunity=synth_opp,
        profile=pv,
        observations=[synth_obs],
        evidence={1: evs},
        best_similarity=None,
        matched_opportunity_id=None,
        matched_normalized_topic=None,
    )
    result = compute_evergreen_value(ctx, policy)
    assert result.raw_score is not None
    # "how to" is a timeless token → lexical_base > 0.5
    # blended = 0.60 * 0.90 + 0.40 * lexical_base ≥ 0.54 + small lexical contribution
    assert result.raw_score >= 0.54
    assert "market_persist" in (result.notes or "")


def test_ah_evergreen_falls_back_to_lexical_without_persistence(tmp_path):
    from app.intelligence.models import (
        FactorContext,
        OpportunityObservation,
        SourceQualityTier,
    )
    from app.intelligence.scoring.factors import compute_evergreen_value

    _, channel_id, pv, policy = _setup(tmp_path)
    synth_opp = Opportunity(
        id=1,
        channel_id=channel_id,
        discovery_run_id=1,
        normalized_topic="how to learn python",
        raw_topic="How to Learn Python",
    )
    synth_obs = OpportunityObservation(
        id=1,
        opportunity_id=1,
        discovery_run_id=1,
        adapter_name=AdapterName.youtube_data_api,
        source_quality_tier=SourceQualityTier.medium,
    )
    ctx = FactorContext(
        opportunity=synth_opp,
        profile=pv,
        observations=[synth_obs],
        evidence={1: []},
        best_similarity=None,
        matched_opportunity_id=None,
        matched_normalized_topic=None,
    )
    result = compute_evergreen_value(ctx, policy)
    assert result.raw_score is not None
    assert "market_persist" not in (result.notes or "")


# ---------------------------------------------------------------------------
# AI. Audience fit and content novelty — unchanged / channel-specific
# ---------------------------------------------------------------------------


def test_ai_audience_fit_uses_channel_profile_not_global_demand(tmp_path):
    from app.intelligence.models import (
        FactorContext,
        OpportunityObservation,
        SourceQualityTier,
    )
    from app.intelligence.scoring.factors import compute_audience_fit

    _, channel_id, pv, policy = _setup(tmp_path, niche="python programming")
    synth_opp = Opportunity(
        id=1,
        channel_id=channel_id,
        discovery_run_id=1,
        normalized_topic="python tutorials beginners",
        raw_topic="Python Tutorials",
    )
    synth_obs = OpportunityObservation(
        id=1,
        opportunity_id=1,
        discovery_run_id=1,
        adapter_name=AdapterName.market_intelligence,
        source_quality_tier=SourceQualityTier.medium,
    )
    ctx = FactorContext(
        opportunity=synth_opp,
        profile=pv,
        observations=[synth_obs],
        evidence={1: []},
        best_similarity=None,
        matched_opportunity_id=None,
        matched_normalized_topic=None,
    )
    result = compute_audience_fit(ctx, policy)
    # "python" overlaps with niche="python programming" → some fit
    assert result.raw_score is not None
    assert result.raw_score > 0.0


def test_aj_content_novelty_channel_specific(tmp_path):
    from app.intelligence.models import (
        FactorContext,
        OpportunityObservation,
        SourceQualityTier,
    )
    from app.intelligence.scoring.factors import compute_content_novelty

    _, channel_id, pv, policy = _setup(tmp_path)
    synth_opp = Opportunity(
        id=1,
        channel_id=channel_id,
        discovery_run_id=1,
        normalized_topic="new topic",
        raw_topic="New Topic",
    )
    synth_obs = OpportunityObservation(
        id=1,
        opportunity_id=1,
        discovery_run_id=1,
        adapter_name=AdapterName.market_intelligence,
        source_quality_tier=SourceQualityTier.medium,
    )
    ctx = FactorContext(
        opportunity=synth_opp,
        profile=pv,
        observations=[synth_obs],
        evidence={1: []},
        best_similarity=None,
        matched_opportunity_id=None,
        matched_normalized_topic=None,
    )
    result = compute_content_novelty(ctx, policy)
    # No existing channel opportunities → best_similarity=None → novelty=1.0
    assert result.raw_score == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# AK. Maturity gate
# ---------------------------------------------------------------------------


def test_ak_maturity_gate_skips_insufficient(tmp_path):
    conn, channel_id, pv, policy = _setup(tmp_path)
    bridge_policy = ExternalMarketBridgePolicy(min_maturity_level="directional")
    ev = _make_evidence(maturity="insufficient")
    result = sync_channel_market_opportunities(conn, channel_id, [ev], pv, policy, bridge_policy)
    conn.commit()
    assert result.skipped_count == 1
    assert result.created_count == 0
    assert list_opportunities(conn, channel_id) == []


def test_al_maturity_gate_skips_exploratory(tmp_path):
    conn, channel_id, pv, policy = _setup(tmp_path)
    bridge_policy = ExternalMarketBridgePolicy(min_maturity_level="directional")
    ev = _make_evidence(maturity="exploratory")
    result = sync_channel_market_opportunities(conn, channel_id, [ev], pv, policy, bridge_policy)
    conn.commit()
    assert result.skipped_count == 1


def test_am_maturity_gate_allows_directional(tmp_path):
    conn, channel_id, pv, policy = _setup(tmp_path)
    bridge_policy = ExternalMarketBridgePolicy(min_maturity_level="directional")
    ev = _make_evidence(maturity="directional")
    result = sync_channel_market_opportunities(conn, channel_id, [ev], pv, policy, bridge_policy)
    conn.commit()
    assert result.skipped_count == 0
    assert result.created_count == 1


def test_an_maturity_gate_allows_actionable(tmp_path):
    conn, channel_id, pv, policy = _setup(tmp_path)
    bridge_policy = ExternalMarketBridgePolicy(min_maturity_level="directional")
    ev = _make_evidence(maturity="actionable")
    result = sync_channel_market_opportunities(conn, channel_id, [ev], pv, policy, bridge_policy)
    conn.commit()
    assert result.skipped_count == 0
    assert result.created_count == 1


# ---------------------------------------------------------------------------
# AO. Scoring integration
# ---------------------------------------------------------------------------


def test_ao_score_created_for_each_opportunity(tmp_path):
    conn, channel_id, pv, policy = _setup(tmp_path)
    cc1 = _make_canonical_cluster(conn, label="python tutorials")
    cc2 = _make_canonical_cluster(conn, label="data structures")
    evs = [
        _make_evidence(canonical_cluster_id=cc1, signal_snapshot_id=1, label="python tutorials"),
        _make_evidence(
            canonical_cluster_id=cc2, signal_snapshot_id=2, label="data structures", cluster_id=2
        ),
    ]
    result = sync_channel_market_opportunities(conn, channel_id, evs, pv, policy)
    conn.commit()
    assert result.scored_count == 2
    assert all(i.score_id is not None for i in result.items if not i.skipped)


def test_ap_existing_scoring_policy_used(tmp_path):
    conn, channel_id, pv, policy = _setup(tmp_path)
    ev = _make_evidence()
    result = sync_channel_market_opportunities(conn, channel_id, [ev], pv, policy)
    conn.commit()
    assert result.items[0].score_id is not None
    # Verify score row references the correct policy
    from app.intelligence.repository import list_scores_for_opportunity

    opp = list_opportunities(conn, channel_id)[0]
    scores = list_scores_for_opportunity(conn, opp.id)
    assert len(scores) == 1
    assert scores[0].scoring_policy_id == policy.id


def test_aq_competition_factor_activated_by_saturation(tmp_path):
    """The competition factor is now activated by market_saturation_score —
    previously always absent."""
    conn, channel_id, pv, policy = _setup(tmp_path)
    from app.intelligence.models import FactorStatus
    from app.intelligence.repository import list_scores_for_opportunity

    ev = _make_evidence(saturation=0.20, confidence=1.0)
    sync_channel_market_opportunities(conn, channel_id, [ev], pv, policy)
    conn.commit()
    opp = list_opportunities(conn, channel_id)[0]
    scores = list_scores_for_opportunity(conn, opp.id)
    assert scores[0].status_competition == FactorStatus.present
    assert scores[0].score_competition is not None


# ---------------------------------------------------------------------------
# AR. Dry-run
# ---------------------------------------------------------------------------


def test_ar_dry_run_writes_no_opportunities(tmp_path):
    conn, channel_id, pv, policy = _setup(tmp_path)
    ev = _make_evidence()
    result = sync_channel_market_opportunities(conn, channel_id, [ev], pv, policy, dry_run=True)
    assert list_opportunities(conn, channel_id) == []
    assert result.dry_run is True


def test_as_dry_run_returns_composite_score(tmp_path):
    conn, channel_id, pv, policy = _setup(tmp_path)
    ev = _make_evidence(demand=0.80, saturation=0.20, confidence=1.0)
    result = sync_channel_market_opportunities(conn, channel_id, [ev], pv, policy, dry_run=True)
    item = result.items[0]
    assert item.composite_score is not None
    assert 0.0 <= item.composite_score <= 1.0


def test_at_dry_run_score_id_is_none(tmp_path):
    conn, channel_id, pv, policy = _setup(tmp_path)
    ev = _make_evidence()
    result = sync_channel_market_opportunities(conn, channel_id, [ev], pv, policy, dry_run=True)
    assert result.items[0].score_id is None


def test_au_dry_run_maturity_gate_still_applied(tmp_path):
    conn, channel_id, pv, policy = _setup(tmp_path)
    ev = _make_evidence(maturity="insufficient")
    result = sync_channel_market_opportunities(conn, channel_id, [ev], pv, policy, dry_run=True)
    assert result.items[0].skipped is True


# ---------------------------------------------------------------------------
# AV. Confidence modulation
# ---------------------------------------------------------------------------


def test_av_zero_confidence_produces_near_zero_score(tmp_path):
    """Confidence=0.0 on market signals should drive market-path factors toward 0."""
    from app.intelligence.models import (
        FactorContext,
        OpportunityObservation,
        OpportunitySourceEvidence,
        SourceQualityTier,
    )
    from app.intelligence.scoring.factors import compute_trend_strength

    _, channel_id, pv, policy = _setup(tmp_path)
    synth_opp = Opportunity(
        id=1, channel_id=channel_id, discovery_run_id=1, normalized_topic="test", raw_topic="Test"
    )
    synth_obs = OpportunityObservation(
        id=1,
        opportunity_id=1,
        discovery_run_id=1,
        adapter_name=AdapterName.market_intelligence,
        source_quality_tier=SourceQualityTier.medium,
    )
    evs = [
        OpportunitySourceEvidence(
            id=1,
            observation_id=1,
            opportunity_id=1,
            evidence_type="market_momentum_score",
            evidence_value=1.0,
            source_label="test",
        ),
        OpportunitySourceEvidence(
            id=2,
            observation_id=1,
            opportunity_id=1,
            evidence_type="market_freshness_score",
            evidence_value=1.0,
            source_label="test",
        ),
        OpportunitySourceEvidence(
            id=3,
            observation_id=1,
            opportunity_id=1,
            evidence_type="market_confidence",
            evidence_value=0.0,
            source_label="test",
        ),
    ]
    ctx = FactorContext(
        opportunity=synth_opp,
        profile=pv,
        observations=[synth_obs],
        evidence={1: evs},
        best_similarity=None,
        matched_opportunity_id=None,
        matched_normalized_topic=None,
    )
    result = compute_trend_strength(ctx, policy)
    assert result.raw_score == pytest.approx(0.0, abs=0.01)


def test_aw_full_confidence_passes_signal_through(tmp_path):
    from app.intelligence.models import (
        FactorContext,
        OpportunityObservation,
        OpportunitySourceEvidence,
        SourceQualityTier,
    )
    from app.intelligence.scoring.factors import compute_audience_demand

    _, channel_id, pv, policy = _setup(tmp_path)
    synth_opp = Opportunity(
        id=1, channel_id=channel_id, discovery_run_id=1, normalized_topic="test", raw_topic="Test"
    )
    synth_obs = OpportunityObservation(
        id=1,
        opportunity_id=1,
        discovery_run_id=1,
        adapter_name=AdapterName.market_intelligence,
        source_quality_tier=SourceQualityTier.medium,
    )
    evs = [
        OpportunitySourceEvidence(
            id=1,
            observation_id=1,
            opportunity_id=1,
            evidence_type="market_demand_score",
            evidence_value=0.75,
            source_label="test",
        ),
        OpportunitySourceEvidence(
            id=2,
            observation_id=1,
            opportunity_id=1,
            evidence_type="market_confidence",
            evidence_value=1.0,
            source_label="test",
        ),
    ]
    ctx = FactorContext(
        opportunity=synth_opp,
        profile=pv,
        observations=[synth_obs],
        evidence={1: evs},
        best_similarity=None,
        matched_opportunity_id=None,
        matched_normalized_topic=None,
    )
    result = compute_audience_demand(ctx, policy)
    assert result.raw_score == pytest.approx(0.75, abs=0.01)


# ---------------------------------------------------------------------------
# AX. Missing vs zero signal handling
# ---------------------------------------------------------------------------


def test_ax_missing_signal_not_treated_as_zero(tmp_path):
    """Missing market demand (None) → factor is absent, not 0.0."""
    from app.intelligence.models import (
        FactorContext,
        FactorStatus,
        OpportunityObservation,
        SourceQualityTier,
    )
    from app.intelligence.scoring.factors import compute_audience_demand

    _, channel_id, pv, policy = _setup(tmp_path)
    synth_opp = Opportunity(
        id=1, channel_id=channel_id, discovery_run_id=1, normalized_topic="test", raw_topic="Test"
    )
    synth_obs = OpportunityObservation(
        id=1,
        opportunity_id=1,
        discovery_run_id=1,
        adapter_name=AdapterName.market_intelligence,
        source_quality_tier=SourceQualityTier.medium,
    )
    ctx = FactorContext(
        opportunity=synth_opp,
        profile=pv,
        observations=[synth_obs],
        evidence={1: []},
        best_similarity=None,
        matched_opportunity_id=None,
        matched_normalized_topic=None,
    )
    result = compute_audience_demand(ctx, policy)
    assert result.raw_score is None
    assert result.status == FactorStatus.absent


def test_ay_zero_saturation_maps_to_high_competition_score(tmp_path):
    """saturation=0.0 → competition_attractiveness=1.0 (best case, no competition)."""
    from app.intelligence.models import (
        FactorContext,
        OpportunityObservation,
        OpportunitySourceEvidence,
        SourceQualityTier,
    )
    from app.intelligence.scoring.factors import compute_competition

    _, channel_id, pv, policy = _setup(tmp_path)
    synth_opp = Opportunity(
        id=1, channel_id=channel_id, discovery_run_id=1, normalized_topic="test", raw_topic="Test"
    )
    synth_obs = OpportunityObservation(
        id=1,
        opportunity_id=1,
        discovery_run_id=1,
        adapter_name=AdapterName.market_intelligence,
        source_quality_tier=SourceQualityTier.medium,
    )
    evs = [
        OpportunitySourceEvidence(
            id=1,
            observation_id=1,
            opportunity_id=1,
            evidence_type="market_saturation_score",
            evidence_value=0.0,
            source_label="test",
        ),
    ]
    ctx = FactorContext(
        opportunity=synth_opp,
        profile=pv,
        observations=[synth_obs],
        evidence={1: evs},
        best_similarity=None,
        matched_opportunity_id=None,
        matched_normalized_topic=None,
    )
    result = compute_competition(ctx, policy)
    assert result.raw_score == pytest.approx(1.0, abs=0.01)


# ---------------------------------------------------------------------------
# AZ. MarketBridgeResult
# ---------------------------------------------------------------------------


def test_az_bridge_result_counts_correct(tmp_path):
    conn, channel_id, pv, policy = _setup(tmp_path)
    cc1 = _make_canonical_cluster(conn, label="topic a")
    cc2 = _make_canonical_cluster(conn, label="topic b")
    evs = [
        _make_evidence(canonical_cluster_id=cc1, signal_snapshot_id=1, label="topic a"),
        _make_evidence(
            canonical_cluster_id=cc2, signal_snapshot_id=2, label="topic b", cluster_id=2
        ),
        _make_evidence(
            canonical_cluster_id=None,
            signal_snapshot_id=3,
            label="topic c",
            maturity="insufficient",
            cluster_id=3,
        ),
    ]
    result = sync_channel_market_opportunities(conn, channel_id, evs, pv, policy)
    conn.commit()
    assert result.created_count == 2
    assert result.skipped_count == 1
    assert result.discovery_run_id is not None


def test_ba_bridge_result_discovery_run_is_market_intelligence(tmp_path):
    conn, channel_id, pv, policy = _setup(tmp_path)
    ev = _make_evidence()
    result = sync_channel_market_opportunities(conn, channel_id, [ev], pv, policy)
    conn.commit()
    assert result.discovery_run_id is not None
    from app.intelligence.repository import get_discovery_run

    run = get_discovery_run(conn, result.discovery_run_id)
    assert run.adapter_name == AdapterName.market_intelligence


def test_bb_bridge_policy_version_in_result(tmp_path):
    conn, channel_id, pv, policy = _setup(tmp_path)
    bridge_policy = ExternalMarketBridgePolicy(version="2.0.0")
    ev = _make_evidence()
    result = sync_channel_market_opportunities(conn, channel_id, [ev], pv, policy, bridge_policy)
    assert result.bridge_policy_version == "2.0.0"


# ---------------------------------------------------------------------------
# BC. No live data safety
# ---------------------------------------------------------------------------


def test_bc_no_youtube_calls(tmp_path, monkeypatch):
    """Confirm bridge does not import or call YouTube adapter."""

    # If YouTube adapter is imported, it would appear in globals
    import sys

    youtube_modules_before = {k for k in sys.modules if "youtube" in k.lower()}
    conn, channel_id, pv, policy = _setup(tmp_path)
    ev = _make_evidence()
    sync_channel_market_opportunities(conn, channel_id, [ev], pv, policy)
    youtube_modules_after = {k for k in sys.modules if "youtube" in k.lower()}
    # No new youtube modules should have been imported during bridge execution
    new_youtube = youtube_modules_after - youtube_modules_before
    assert not new_youtube, f"Bridge imported YouTube modules: {new_youtube}"


def test_bd_no_phase_12c_mutation(tmp_path):
    """Bridge creates Opportunities but does not touch Phase 12C narration runs."""
    conn, channel_id, pv, policy = _setup(tmp_path)
    ev = _make_evidence()
    sync_channel_market_opportunities(conn, channel_id, [ev], pv, policy)
    conn.commit()
    # Phase 12C tables: narration_runs, narration_segments
    narration_rows = conn.execute("SELECT COUNT(*) FROM narration_runs").fetchone()[0]
    assert narration_rows == 0


def test_be_no_content_generation(tmp_path):
    """Bridge must not create any production/script/render rows."""
    conn, channel_id, pv, policy = _setup(tmp_path)
    ev = _make_evidence()
    sync_channel_market_opportunities(conn, channel_id, [ev], pv, policy)
    conn.commit()
    scripts = conn.execute("SELECT COUNT(*) FROM scripts").fetchone()[0]
    assert scripts == 0


def test_bf_empty_evidences_returns_empty_result(tmp_path):
    conn, channel_id, pv, policy = _setup(tmp_path)
    result = sync_channel_market_opportunities(conn, channel_id, [], pv, policy)
    assert result.created_count == 0
    assert result.discovery_run_id is None


def test_bg_update_opportunity_signal_snapshot(tmp_path):
    conn, channel_id, pv, _ = _setup(tmp_path)
    from app.intelligence.models import DiscoveryRun
    from app.intelligence.repository import create_discovery_run

    run = create_discovery_run(
        conn,
        DiscoveryRun(
            channel_id=channel_id,
            profile_version_id=pv.id,
            adapter_name=AdapterName.market_intelligence,
            started_at=datetime.fromisoformat(_NOW),
        ),
    )
    opp = create_opportunity(
        conn,
        Opportunity(
            channel_id=channel_id,
            discovery_run_id=run.id,
            normalized_topic="python tutorials",
            raw_topic="Python Tutorials",
        ),
    )
    update_opportunity_signal_snapshot(conn, opp.id, signal_snapshot_id=55)
    refreshed = get_opportunity(conn, opp.id)
    assert refreshed.market_signal_snapshot_id == 55
