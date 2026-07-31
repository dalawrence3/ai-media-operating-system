"""Tests for input snapshot building and hashing."""

from __future__ import annotations

from app.intelligence.models import (
    ChannelProfileVersion,
    FactorContext,
    LifecycleState,
    Opportunity,
    OpportunityObservation,
    OpportunitySourceEvidence,
    SourceQualityTier,
)
from app.intelligence.scoring.snapshot import build_input_snapshot, compute_hash


def _profile(channel_id: int = 1) -> ChannelProfileVersion:
    return ChannelProfileVersion(
        channel_id=channel_id,
        version=1,
        primary_niche="personal finance",
        secondary_niches=["investing", "budgeting"],
        audience_description="people learning money skills",
    )


def _opp(channel_id: int = 1) -> Opportunity:
    return Opportunity(
        id=42,
        channel_id=channel_id,
        raw_topic="best index funds 2026",
        normalized_topic="best index fund 2026",
        current_lifecycle_state=LifecycleState.new, discovery_run_id=1,
    )


def _obs(obs_id: int, age: float | None = 30.0) -> OpportunityObservation:
    return OpportunityObservation(
        id=obs_id,
        opportunity_id=42,
        discovery_run_id=1,
        adapter_name="youtube_data_api",
        raw_topic="best index funds",
        normalized_topic="best index fund",
        source_quality_tier=SourceQualityTier.medium,
        signal_age_days=age,
    )


def _ev(
    ev_id: int, obs_id: int, ev_type: str, value: float | None = None, text: str | None = None
) -> OpportunitySourceEvidence:
    return OpportunitySourceEvidence(
        id=ev_id,
        observation_id=obs_id,
        evidence_type=ev_type,
        evidence_value=value,
        evidence_text=text, source_label="test", opportunity_id=1,
    )


def _ctx(observations=None, evidence=None, best_similarity=None) -> FactorContext:
    return FactorContext(
        opportunity=_opp(),
        profile=_profile(),
        observations=observations or [],
        evidence=evidence or {},
        best_similarity=best_similarity,
        matched_opportunity_id=None,
        matched_normalized_topic=None,
    )


# ---------------------------------------------------------------------------
# build_input_snapshot
# ---------------------------------------------------------------------------


def test_snapshot_contains_opportunity_fields() -> None:
    ctx = _ctx()
    snap = build_input_snapshot(ctx)
    opp = snap["opportunity"]
    assert opp["id"] == 42
    assert opp["normalized_topic"] == "best index fund 2026"
    assert opp["raw_topic"] == "best index funds 2026"


def test_snapshot_contains_profile_fields() -> None:
    ctx = _ctx()
    snap = build_input_snapshot(ctx)
    profile = snap["profile"]
    assert profile["primary_niche"] == "personal finance"
    assert "investing" in profile["secondary_niches"]


def test_snapshot_novelty_context_none() -> None:
    ctx = _ctx(best_similarity=None)
    snap = build_input_snapshot(ctx)
    assert snap["novelty_context"]["best_similarity"] is None


def test_snapshot_novelty_context_set() -> None:
    ctx = _ctx(best_similarity=0.42)
    snap = build_input_snapshot(ctx)
    assert snap["novelty_context"]["best_similarity"] == 0.42


def test_snapshot_observations_included() -> None:
    # observations is stored as a dict keyed by str(obs.id)
    obs = [_obs(1, 20.0), _obs(2, 45.0)]
    ev = {1: [_ev(10, 1, "view_count", 50000)], 2: []}
    ctx = _ctx(observations=obs, evidence=ev)
    snap = build_input_snapshot(ctx)
    obs_snaps = snap["observations"]
    assert isinstance(obs_snaps, dict)
    assert set(obs_snaps.keys()) == {"1", "2"}


def test_snapshot_evidence_sorted_deterministically() -> None:
    obs = [_obs(1)]
    # Two evidence rows with the same type — sort by value (100 < 200)
    ev1 = _ev(5, 1, "view_count", 100.0)
    ev2 = _ev(3, 1, "view_count", 200.0)
    ctx = _ctx(observations=obs, evidence={1: [ev1, ev2]})
    snap = build_input_snapshot(ctx)
    ev_list = snap["observations"]["1"]["evidence"]
    # Sorted by (evidence_type, evidence_value) → 100 before 200
    assert ev_list[0]["evidence_value"] == 100.0
    assert ev_list[1]["evidence_value"] == 200.0


def test_snapshot_evidence_sorted_by_type_then_value_then_id() -> None:
    obs = [_obs(1)]
    ev_a = _ev(10, 1, "manual_demand_note", None, "great topic")
    ev_b = _ev(11, 1, "view_count", 500.0)
    ctx = _ctx(observations=obs, evidence={1: [ev_b, ev_a]})
    snap = build_input_snapshot(ctx)
    ev_list = snap["observations"]["1"]["evidence"]
    # lexicographic: manual_demand_note < view_count
    types = [e["evidence_type"] for e in ev_list]
    assert types == ["manual_demand_note", "view_count"]


# ---------------------------------------------------------------------------
# compute_hash
# ---------------------------------------------------------------------------


def test_hash_is_64_char_hex() -> None:
    snap = build_input_snapshot(_ctx())
    h = compute_hash(snap)
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_hash_deterministic() -> None:
    ctx = _ctx(observations=[_obs(1)])
    snap1 = build_input_snapshot(ctx)
    snap2 = build_input_snapshot(ctx)
    assert compute_hash(snap1) == compute_hash(snap2)


def test_hash_changes_with_different_topic() -> None:
    ctx1 = _ctx()
    ctx2 = FactorContext(
        opportunity=Opportunity(
            id=42, channel_id=1, raw_topic="different topic",
            normalized_topic="different topic",
            current_lifecycle_state=LifecycleState.new,
            discovery_run_id=1,
        ),
        profile=_profile(),
        observations=[],
        evidence={},
        best_similarity=None,
        matched_opportunity_id=None,
        matched_normalized_topic=None,
    )
    assert compute_hash(build_input_snapshot(ctx1)) != compute_hash(build_input_snapshot(ctx2))


def test_hash_changes_with_additional_evidence() -> None:
    obs = [_obs(1)]
    ctx1 = _ctx(observations=obs, evidence={1: []})
    ctx2 = _ctx(observations=obs, evidence={1: [_ev(10, 1, "view_count", 50000)]})
    assert compute_hash(build_input_snapshot(ctx1)) != compute_hash(build_input_snapshot(ctx2))
