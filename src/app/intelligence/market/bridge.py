"""Phase 13F: External Market Intelligence → Existing Opportunity Evidence / Scoring Bridge.

Entry point: sync_channel_market_opportunities()

Architecture:
  GLOBAL external market intelligence (ExternalMarketOpportunityEvidence)
  + CHANNEL-SPECIFIC context (channel profile, ScoringPolicy)
  → EXISTING Opportunity system (Opportunity, OpportunityObservation, OpportunitySourceEvidence,
                                  existing scoring engine)

No YouTube calls. No content generation. No Phase 14 experiment selection.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from app.intelligence.market.bridge_models import (
    ExternalMarketBridgePolicy,
    MarketBridgeResult,
    MarketBridgeSyncItem,
)
from app.intelligence.market.interpretation_models import ExternalMarketOpportunityEvidence
from app.intelligence.models import (
    AdapterName,
    ChannelProfileVersion,
    DiscoveryRun,
    Opportunity,
    OpportunityObservation,
    OpportunitySourceEvidence,
    RunStatus,
    ScoringPolicy,
    SourceQualityTier,
)
from app.intelligence.repository import (
    create_observation,
    create_opportunity,
    create_source_evidence,
    find_existing_opportunity,
    find_opportunity_by_canonical_cluster,
    update_opportunity_signal_snapshot,
)
from app.intelligence.scoring.engine import score_opportunity


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


# ---------------------------------------------------------------------------
# Evidence emission helpers
# ---------------------------------------------------------------------------

_MARKET_SCORE_EVIDENCE: tuple[tuple[str, str], ...] = (
    ("market_demand_score", "score [0,1]"),
    ("market_saturation_score", "score [0,1]"),
    ("market_freshness_score", "score [0,1]"),
    ("market_momentum_score", "score [0,1]"),
    ("market_persistence_score", "score [0,1]"),
    ("market_confidence", "score [0,1]"),
)


def _emit_evidence(
    conn: sqlite3.Connection,
    observation_id: int,
    opportunity_id: int,
    ev: ExternalMarketOpportunityEvidence,
    bridge_policy: ExternalMarketBridgePolicy,
    now: str,
) -> None:
    """Create OpportunitySourceEvidence rows from a market evidence object."""
    source = f"market_intelligence:canonical={ev.canonical_cluster_id}:snap={ev.signal_snapshot_id}"

    def _insert(evidence_type: str, value: float | None, text: str | None, unit: str) -> None:
        create_source_evidence(
            conn,
            OpportunitySourceEvidence(
                observation_id=observation_id,
                opportunity_id=opportunity_id,
                evidence_type=evidence_type,
                evidence_value=value,
                evidence_text=text,
                evidence_unit=unit,
                source_label=source,
                collected_at=datetime.fromisoformat(now),
            ),
        )

    # Numeric signal scores
    score_map = {
        "market_demand_score": ev.demand_score,
        "market_saturation_score": ev.saturation_score,
        "market_freshness_score": ev.freshness_score,
        "market_momentum_score": ev.momentum_score,
        "market_persistence_score": ev.persistence_score,
        "market_confidence": ev.confidence,
    }
    for etype, val in score_map.items():
        if val is not None:
            _insert(etype, val, None, "score [0,1]")

    # Text/metadata evidence
    _insert("market_maturity", None, ev.signal_maturity, "maturity_level")
    if ev.state_label:
        _insert("market_state_label", None, ev.state_label, "label")

    # Provenance evidence (stored as text so operators can inspect the chain)
    if ev.canonical_cluster_id is not None:
        _insert("market_canonical_cluster_id", float(ev.canonical_cluster_id), None, "id")
    _insert("market_cluster_snapshot_id", float(ev.cluster_id), None, "id")
    _insert("market_signal_snapshot_id", float(ev.signal_snapshot_id), None, "id")
    _insert("market_interpretation_run_id", float(ev.interpretation_run_id), None, "id")


# ---------------------------------------------------------------------------
# Main service
# ---------------------------------------------------------------------------


def sync_channel_market_opportunities(
    conn: sqlite3.Connection,
    channel_id: int,
    evidences: list[ExternalMarketOpportunityEvidence],
    profile_version: ChannelProfileVersion,
    scoring_policy: ScoringPolicy,
    bridge_policy: ExternalMarketBridgePolicy | None = None,
    *,
    dry_run: bool = False,
) -> MarketBridgeResult:
    """Bridge external market evidence into the existing Opportunity system.

    For each ExternalMarketOpportunityEvidence:
      1. Check maturity gate (skip if below min_maturity_level).
      2. Find existing Opportunity by canonical_cluster_id (continuity anchor),
         falling back to topic jaccard dedup.
      3. Create or reuse the Opportunity row.
      4. Create an OpportunityObservation (adapter_name=market_intelligence).
      5. Emit OpportunitySourceEvidence rows for every signal value.
      6. Score the opportunity via the existing scoring engine.
      7. Update market_signal_snapshot_id on the Opportunity row.

    dry_run=True performs all read operations and factor mapping but writes
    nothing to the database. Returned items carry composite_score/confidence but
    score_id=None and opportunity_id reflects whether a match was found.

    No YouTube calls. No content generation. No Phase 14 selection.
    """
    if bridge_policy is None:
        bridge_policy = ExternalMarketBridgePolicy()

    result = MarketBridgeResult(
        channel_id=channel_id,
        discovery_run_id=None,
        bridge_policy_version=bridge_policy.version,
        dry_run=dry_run,
    )

    if not evidences:
        return result

    now = _now()

    # Create a single DiscoveryRun for this sync batch (skip in dry_run)
    discovery_run_id: int | None = None
    if not dry_run:
        from app.intelligence.repository import create_discovery_run

        assert profile_version.id is not None
        run = create_discovery_run(
            conn,
            DiscoveryRun(
                channel_id=channel_id,
                profile_version_id=profile_version.id,
                adapter_name=AdapterName.market_intelligence,
                query_parameters_json=json.dumps({"bridge_policy_version": bridge_policy.version}),
                status=RunStatus.running,
                started_at=datetime.fromisoformat(now),
            ),
        )
        discovery_run_id = run.id
        result.discovery_run_id = discovery_run_id

    for ev in evidences:
        item = _process_one(
            conn=conn,
            channel_id=channel_id,
            ev=ev,
            profile_version=profile_version,
            scoring_policy=scoring_policy,
            bridge_policy=bridge_policy,
            discovery_run_id=discovery_run_id,
            now=now,
            dry_run=dry_run,
        )
        result.items.append(item)

    # Mark discovery run complete
    if not dry_run and discovery_run_id is not None:
        from app.intelligence.repository import update_discovery_run

        new_count = sum(1 for i in result.items if i.opportunity_created and not i.skipped)
        update_discovery_run(
            conn,
            discovery_run_id,
            status=RunStatus.completed,
            new_opportunity_count=new_count,
            candidate_count=len(evidences),
            dedup_count=result.refreshed_count,
        )

    return result


def _process_one(
    *,
    conn: sqlite3.Connection,
    channel_id: int,
    ev: ExternalMarketOpportunityEvidence,
    profile_version: ChannelProfileVersion,
    scoring_policy: ScoringPolicy,
    bridge_policy: ExternalMarketBridgePolicy,
    discovery_run_id: int | None,
    now: str,
    dry_run: bool,
) -> MarketBridgeSyncItem:
    # 1. Maturity gate
    if not bridge_policy.meets_maturity_gate(ev.signal_maturity):
        return MarketBridgeSyncItem(
            cluster_label=ev.cluster_label,
            canonical_cluster_id=ev.canonical_cluster_id,
            opportunity_id=-1,
            opportunity_created=False,
            observation_id=-1,
            score_id=None,
            composite_score=None,
            confidence=None,
            skipped=True,
            skip_reason=(
                f"maturity={ev.signal_maturity} below gate={bridge_policy.min_maturity_level}"
            ),
        )

    # 2. Find or create Opportunity
    existing_opp: Opportunity | None = None
    opp_created = False

    if ev.canonical_cluster_id is not None:
        existing_opp = find_opportunity_by_canonical_cluster(
            conn, channel_id, ev.canonical_cluster_id
        )

    if existing_opp is None and ev.canonical_cluster_id is None:
        # Jaccard fallback: only safe when the evidence carries NO canonical identity.
        # When ev.canonical_cluster_id is set, the canonical lookup already gave the
        # authoritative answer (no active opportunity for this cluster).  Using Jaccard
        # here could attach this cluster's signals to an Opportunity anchored to a
        # DIFFERENT canonical cluster, corrupting provenance.
        match = find_existing_opportunity(
            conn,
            channel_id,
            ev.normalized_label,
            bridge_policy.topic_jaccard_threshold,
        )
        if match is not None:
            existing_opp = match[0]

    if existing_opp is None:
        # Create new Opportunity (skip on dry_run)
        if dry_run:
            return _dry_run_item(
                ev, channel_id, conn, profile_version, scoring_policy, bridge_policy
            )

        assert discovery_run_id is not None
        new_opp = Opportunity(
            channel_id=channel_id,
            discovery_run_id=discovery_run_id,
            normalized_topic=ev.normalized_label,
            raw_topic=ev.cluster_label,
            title=ev.cluster_label,
            topic_summary="",
            canonical_cluster_id=ev.canonical_cluster_id,
            market_signal_snapshot_id=ev.signal_snapshot_id,
        )
        existing_opp = create_opportunity(conn, new_opp)
        opp_created = True
    else:
        if dry_run:
            return _dry_run_item(
                ev, channel_id, conn, profile_version, scoring_policy, bridge_policy, existing_opp
            )

    opp_id = existing_opp.id
    assert opp_id is not None
    assert discovery_run_id is not None

    # Canonical identity consistency guard: if both the existing Opportunity and the
    # incoming evidence carry a canonical_cluster_id, they must match.  A mismatch
    # means signals from canonical cluster X are about to be written onto an Opportunity
    # anchored to canonical cluster Y, corrupting the provenance chain.
    if (
        existing_opp.canonical_cluster_id is not None
        and ev.canonical_cluster_id is not None
        and existing_opp.canonical_cluster_id != ev.canonical_cluster_id
    ):
        raise ValueError(
            f"Canonical cluster identity mismatch for opportunity {opp_id}: "
            f"opportunity.canonical_cluster_id={existing_opp.canonical_cluster_id} "
            f"but evidence.canonical_cluster_id={ev.canonical_cluster_id}. "
            "Refusing to cross-wire signal provenance."
        )

    # 3. Create observation
    obs = create_observation(
        conn,
        OpportunityObservation(
            opportunity_id=opp_id,
            discovery_run_id=discovery_run_id,
            adapter_name=AdapterName.market_intelligence,
            collected_at=datetime.fromisoformat(now),
            source_quality_tier=SourceQualityTier.medium,
            raw_payload_json=json.dumps(
                {
                    "canonical_cluster_id": ev.canonical_cluster_id,
                    "cluster_id": ev.cluster_id,
                    "signal_snapshot_id": ev.signal_snapshot_id,
                    "interpretation_run_id": ev.interpretation_run_id,
                    "confidence": ev.confidence,
                    "signal_maturity": ev.signal_maturity,
                }
            ),
            collection_notes=f"market_intelligence bridge v{bridge_policy.version}",
        ),
    )
    obs_id = obs.id
    assert obs_id is not None

    # 4. Emit evidence rows
    _emit_evidence(conn, obs_id, opp_id, ev, bridge_policy, now)

    # 5. Score
    score = score_opportunity(
        conn,
        opp_id,
        scoring_policy,
        profile_version,
        force=True,
    )

    # 6. Update signal snapshot pointer
    update_opportunity_signal_snapshot(conn, opp_id, ev.signal_snapshot_id)

    return MarketBridgeSyncItem(
        cluster_label=ev.cluster_label,
        canonical_cluster_id=ev.canonical_cluster_id,
        opportunity_id=opp_id,
        opportunity_created=opp_created,
        observation_id=obs_id,
        score_id=score.id,
        composite_score=score.composite_score,
        confidence=score.confidence,
    )


def _dry_run_item(
    ev: ExternalMarketOpportunityEvidence,
    channel_id: int,
    conn: sqlite3.Connection,
    profile_version: ChannelProfileVersion,
    scoring_policy: ScoringPolicy,
    bridge_policy: ExternalMarketBridgePolicy,
    existing_opp: Opportunity | None = None,
) -> MarketBridgeSyncItem:
    """Dry-run path: compute factor mapping without writing anything."""
    from app.intelligence.models import FactorContext
    from app.intelligence.scoring.confidence import compute_confidence
    from app.intelligence.scoring.factors import (
        compute_audience_demand,
        compute_audience_fit,
        compute_competition,
        compute_content_novelty,
        compute_evergreen_value,
        compute_trend_strength,
    )
    from app.intelligence.scoring.weights import compute_composite, compute_effective_weights

    # Build a synthetic Opportunity so factors can run
    if existing_opp is not None:
        synth_opp = existing_opp
    else:
        synth_opp = Opportunity(
            id=None,
            channel_id=channel_id,
            discovery_run_id=-1,
            normalized_topic=ev.normalized_label,
            raw_topic=ev.cluster_label,
            canonical_cluster_id=ev.canonical_cluster_id,
        )

    # Build synthetic evidence context from the market signals

    from app.intelligence.models import (
        AdapterName,
        SourceQualityTier,
    )
    from app.intelligence.models import (
        OpportunityObservation as _Obs,
    )
    from app.intelligence.models import (
        OpportunitySourceEvidence as _Ev,
    )

    synth_obs = _Obs(
        id=-1,
        opportunity_id=-1,
        discovery_run_id=-1,
        adapter_name=AdapterName.market_intelligence,
        source_quality_tier=SourceQualityTier.medium,
    )
    score_map = {
        "market_demand_score": ev.demand_score,
        "market_saturation_score": ev.saturation_score,
        "market_freshness_score": ev.freshness_score,
        "market_momentum_score": ev.momentum_score,
        "market_persistence_score": ev.persistence_score,
        "market_confidence": ev.confidence,
    }
    synth_evs = [
        _Ev(
            id=None,
            observation_id=-1,
            opportunity_id=-1,
            evidence_type=etype,
            evidence_value=val,
            source_label="dry_run",
        )
        for etype, val in score_map.items()
        if val is not None
    ]

    ctx = FactorContext(
        opportunity=synth_opp,
        profile=profile_version,
        observations=[synth_obs],
        evidence={-1: synth_evs},
        best_similarity=None,
        matched_opportunity_id=None,
        matched_normalized_topic=None,
    )
    factor_results = [
        compute_trend_strength(ctx, scoring_policy),
        compute_audience_demand(ctx, scoring_policy),
        compute_competition(ctx, scoring_policy),
        compute_evergreen_value(ctx, scoring_policy),
        compute_audience_fit(ctx, scoring_policy),
        compute_content_novelty(ctx, scoring_policy),
    ]
    eff_weights = compute_effective_weights(scoring_policy, factor_results)
    composite = compute_composite(factor_results, eff_weights, scoring_policy)
    confidence = compute_confidence(factor_results, [synth_obs], scoring_policy)

    return MarketBridgeSyncItem(
        cluster_label=ev.cluster_label,
        canonical_cluster_id=ev.canonical_cluster_id,
        opportunity_id=existing_opp.id if existing_opp and existing_opp.id else -1,
        opportunity_created=False,
        observation_id=-1,
        score_id=None,
        composite_score=composite,
        confidence=confidence,
    )
