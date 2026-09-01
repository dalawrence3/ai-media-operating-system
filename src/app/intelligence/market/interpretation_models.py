"""Phase 13E — Market Interpretation typed domain models.

Converts raw external market evidence (observations, velocities, probes)
into structured, versioned, decomposable market signals.

RESPONSIBILITIES
----------------
- MarketInterpretationRun: audit record for one interpretation pass
- MarketTopicCluster: a named external market theme/topic cluster (global, not channel-owned)
- MarketClusterMember: evidence members of a cluster (probes, videos)
- MarketClusterSignal: scored signal snapshot for one (cluster, run) pair
- ExternalMarketOpportunityEvidence: typed Phase 13F contract object

SCOPE
-----
All cluster and signal objects are scoped to (platform, provider, region, language),
NOT to a channel_id.  Channel-specific fit belongs to Phase 13F.

SIGNAL DIMENSIONS
-----------------
demand      — how much external audience attention exists around this cluster
saturation  — how much creator/supply competition exists
freshness   — how recent the evidence/content is
momentum    — how quickly current attention is changing (uses Phase 13C velocity)
persistence — whether demand appears durable across time

MATURITY LEVELS
---------------
insufficient → < 3 supporting videos
exploratory  → 3–4 videos
directional  → 5–9 videos
actionable   → ≥ 10 videos AND ≥ 5 distinct creators

STATE LABELS (optional, derived deterministically from numeric scores)
------------
emerging     — recent content, rising freshness
accelerating — positive momentum + freshness
mature       — demand present, moderate freshness
evergreen    — low freshness + persistence
cooling      — low momentum + low freshness
saturated    — high saturation
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Version constants
# ---------------------------------------------------------------------------

CLUSTERING_VERSION: str = "v1"
SCORING_VERSION: str = "v1"
INTERPRETATION_POLICY_VERSION: str = "v1"
SEMANTIC_CLUSTERING_VERSION: str = "v1"  # version of semantic adjudication pass

# ---------------------------------------------------------------------------
# Maturity levels (aligned with Phase 12C / 13C terminology)
# ---------------------------------------------------------------------------

SIGNAL_MATURITY_INSUFFICIENT = "insufficient"
SIGNAL_MATURITY_EXPLORATORY = "exploratory"
SIGNAL_MATURITY_DIRECTIONAL = "directional"
SIGNAL_MATURITY_ACTIONABLE = "actionable"

SIGNAL_MATURITY_ORDERED = (
    SIGNAL_MATURITY_INSUFFICIENT,
    SIGNAL_MATURITY_EXPLORATORY,
    SIGNAL_MATURITY_DIRECTIONAL,
    SIGNAL_MATURITY_ACTIONABLE,
)

# ---------------------------------------------------------------------------
# State label constants
# ---------------------------------------------------------------------------

STATE_EMERGING = "emerging"
STATE_ACCELERATING = "accelerating"
STATE_MATURE = "mature"
STATE_EVERGREEN = "evergreen"
STATE_COOLING = "cooling"
STATE_SATURATED = "saturated"

# ---------------------------------------------------------------------------
# Clustering / scoring normalization constants (V1 policy)
# All stored in ClusteringPolicy / ScoringPolicy for versioning.
# ---------------------------------------------------------------------------

CLUSTER_JACCARD_THRESHOLD: float = 0.35  # probes within threshold share a cluster
DEMAND_VIEW_LOG_SCALE: float = 7_000_000.0  # log10(7M) ≈ 1.0
DEMAND_SINGLE_VIDEO_CAP: float = 0.5  # max demand when only 1 video
SATURATION_CREATOR_SCALE: float = 30.0  # 30 distinct creators ≈ 1.0
SATURATION_SEARCH_LOG_SCALE: float = 500_000.0  # log10(500K) ≈ 1.0
FRESHNESS_HALFLIFE_DAYS: float = 180.0  # 6-month halflife for age decay
FRESHNESS_RECENT_THRESHOLD_DAYS: int = 90  # videos published < 90d count as "recent"
MOMENTUM_VPD_LOG_SCALE: float = 10_000.0  # 10K views/day ≈ 1.0
PERSISTENCE_SPREAD_DAYS: float = 730.0  # 2 years = full spread score


# ---------------------------------------------------------------------------
# Maturity / confidence helpers
# ---------------------------------------------------------------------------


def compute_signal_maturity(video_count: int, creator_count: int) -> str:
    """Return signal maturity label for a cluster.

    insufficient → < 3 supporting videos
    exploratory  → 3–4 videos
    directional  → 5–9 videos
    actionable   → ≥ 10 videos AND ≥ 5 distinct creators
    """
    if video_count < 3:
        return SIGNAL_MATURITY_INSUFFICIENT
    if video_count < 5:
        return SIGNAL_MATURITY_EXPLORATORY
    if video_count < 10:
        return SIGNAL_MATURITY_DIRECTIONAL
    if creator_count >= 5:
        return SIGNAL_MATURITY_ACTIONABLE
    return SIGNAL_MATURITY_DIRECTIONAL


def compute_signal_confidence(
    video_count: int,
    creator_count: int,
    velocity_count: int,
    completeness: float,
) -> float:
    """Normalized [0, 1] confidence blending evidence quantity, diversity, and completeness.

    count_factor     — saturates at 10 videos
    diversity_factor — saturates at 5 distinct creators
    temporal_factor  — saturates at 5 videos with velocity data
    completeness     — fraction of expected signal dimensions that have real data [0, 1]
    """
    count_factor = min(1.0, video_count / 10.0)
    diversity_factor = min(1.0, creator_count / 5.0)
    temporal_factor = min(1.0, velocity_count / 5.0)
    confidence = (
        0.40 * count_factor + 0.35 * diversity_factor + 0.15 * temporal_factor + 0.10 * completeness
    )
    return max(0.0, min(1.0, confidence))


def compute_state_label(
    *,
    demand: float | None,
    saturation: float | None,
    freshness: float | None,
    momentum: float | None,
    persistence: float | None,
) -> str | None:
    """Deterministic state label derived from numeric scores.

    Returns None when evidence is insufficient to assign a label.
    Labels are mutually exclusive and ordered by specificity.
    """
    if demand is None:
        return None
    if momentum is not None and momentum >= 0.5 and freshness is not None and freshness >= 0.5:
        return STATE_ACCELERATING
    if freshness is not None and freshness >= 0.6 and (momentum is None or momentum >= 0.2):
        return STATE_EMERGING
    if persistence is not None and persistence >= 0.5 and freshness is not None and freshness < 0.4:
        return STATE_EVERGREEN
    if momentum is not None and momentum < 0.2 and freshness is not None and freshness < 0.3:
        return STATE_COOLING
    if saturation is not None and saturation >= 0.7:
        return STATE_SATURATED
    if demand >= 0.3:
        return STATE_MATURE
    return None


# ---------------------------------------------------------------------------
# Normalization helpers (versioned V1)
# ---------------------------------------------------------------------------


def normalize_view_count(views: float) -> float:
    """Log-scale view count → [0, 1].  7M views ≈ 1.0.  Outlier-safe."""
    if views <= 0:
        return 0.0
    return min(1.0, math.log10(max(1.0, views)) / math.log10(max(1.0, DEMAND_VIEW_LOG_SCALE)))


def normalize_vpd(units_per_day: float) -> float:
    """Log-scale views/day → [0, 1].  10K views/day ≈ 1.0."""
    if units_per_day <= 0:
        return 0.0
    return min(
        1.0,
        math.log10(max(1.0, units_per_day + 1)) / math.log10(MOMENTUM_VPD_LOG_SCALE + 1),
    )


def normalize_search_estimate(search_total: float) -> float:
    """Log-scale search result total → [0, 1].  500K results ≈ 1.0."""
    if search_total <= 0:
        return 0.0
    return min(
        1.0,
        math.log10(max(1.0, search_total)) / math.log10(max(1.0, SATURATION_SEARCH_LOG_SCALE)),
    )


# ---------------------------------------------------------------------------
# Demand score
# ---------------------------------------------------------------------------


def compute_demand_score(
    *,
    view_counts: list[float],  # only non-None views included; zero is valid
    creator_count: int,
) -> tuple[float | None, dict[str, Any]]:
    """Compute demand score [0, 1].

    Returns (None, diagnostics) when no view evidence is available.

    Outlier protection: median normalized views (not mean) prevents a
    single 100M-view outlier from dominating.

    Single-video cap: DEMAND_SINGLE_VIDEO_CAP applied when len(view_counts)==1
    so one video can never generate ACTIONABLE demand.
    """
    if not view_counts:
        return None, {"reason": "no_view_data", "video_count": 0}

    normalized = [normalize_view_count(v) for v in view_counts]
    median_normalized = statistics.median(normalized)

    # Single-video cap
    if len(view_counts) == 1:
        median_normalized = min(DEMAND_SINGLE_VIDEO_CAP, median_normalized)

    creator_factor = min(1.0, creator_count / 10.0) if creator_count > 0 else 0.0

    score = min(1.0, 0.80 * median_normalized + 0.20 * creator_factor)
    components: dict[str, Any] = {
        "median_normalized_views": round(median_normalized, 4),
        "creator_factor": round(creator_factor, 4),
        "video_count": len(view_counts),
        "creator_count": creator_count,
        "raw_views_min": min(view_counts),
        "raw_views_max": max(view_counts),
        "raw_views_median": statistics.median(view_counts),
        "single_video_cap_applied": len(view_counts) == 1,
    }
    return round(score, 4), components


# ---------------------------------------------------------------------------
# Saturation score
# ---------------------------------------------------------------------------


def compute_saturation_score(
    *,
    creator_count: int,
    search_total_estimate: float | None,  # None = missing (not zero)
) -> tuple[float, dict[str, Any]]:
    """Compute saturation score [0, 1].

    missing search estimate != zero saturation.
    """
    creator_score = min(1.0, creator_count / SATURATION_CREATOR_SCALE)

    if search_total_estimate is not None and search_total_estimate >= 0:
        search_score = normalize_search_estimate(search_total_estimate)
        score = min(1.0, 0.60 * creator_score + 0.40 * search_score)
        components: dict[str, Any] = {
            "creator_score": round(creator_score, 4),
            "search_score": round(search_score, 4),
            "has_search_estimate": True,
            "search_estimate_raw": search_total_estimate,
        }
    else:
        score = creator_score
        components = {
            "creator_score": round(creator_score, 4),
            "has_search_estimate": False,
            "search_estimate_raw": None,
        }

    components["creator_count"] = creator_count
    return round(score, 4), components


# ---------------------------------------------------------------------------
# Freshness score
# ---------------------------------------------------------------------------


def compute_freshness_score(
    *,
    age_days_list: list[float],  # days since publication; only non-None included
) -> tuple[float | None, dict[str, Any]]:
    """Compute freshness score [0, 1].

    Returns (None, diagnostics) when no publication dates are available.
    Missing dates ≠ zero freshness.
    """
    if not age_days_list:
        return None, {"reason": "no_publication_dates", "video_count": 0}

    median_age = statistics.median(age_days_list)
    recency_score = math.exp(-median_age / FRESHNESS_HALFLIFE_DAYS)

    recent_count = sum(1 for d in age_days_list if d < FRESHNESS_RECENT_THRESHOLD_DAYS)
    recent_ratio = recent_count / len(age_days_list)

    score = min(1.0, 0.60 * recency_score + 0.40 * recent_ratio)
    components: dict[str, Any] = {
        "median_age_days": round(median_age, 1),
        "recency_score": round(recency_score, 4),
        "recent_count": recent_count,
        "total_with_dates": len(age_days_list),
        "recent_ratio": round(recent_ratio, 4),
    }
    return round(score, 4), components


# ---------------------------------------------------------------------------
# Momentum score
# ---------------------------------------------------------------------------


def compute_momentum_score(
    *,
    positive_vpd_list: list[float],  # units_per_day, negative corrections excluded
    total_velocity_count: int,  # all velocities seen (including non-positive)
) -> tuple[float | None, dict[str, Any]]:
    """Compute momentum score [0, 1].

    missing velocity ≠ zero momentum.
    negative correction intervals are excluded before calling this.
    """
    if total_velocity_count == 0:
        return None, {"reason": "no_velocity_data"}

    if not positive_vpd_list:
        # Velocity exists but all non-positive
        return 0.0, {
            "median_normalized_vpd": 0.0,
            "positive_ratio": 0.0,
            "video_count_with_velocity": total_velocity_count,
            "positive_count": 0,
        }

    normalized = [normalize_vpd(v) for v in positive_vpd_list]
    median_normalized = statistics.median(normalized)
    positive_ratio = len(positive_vpd_list) / total_velocity_count

    score = min(1.0, 0.70 * median_normalized + 0.30 * positive_ratio)
    components: dict[str, Any] = {
        "median_normalized_vpd": round(median_normalized, 4),
        "positive_ratio": round(positive_ratio, 4),
        "video_count_with_velocity": total_velocity_count,
        "positive_count": len(positive_vpd_list),
        "raw_vpd_median": round(statistics.median(positive_vpd_list), 2),
    }
    return round(score, 4), components


# ---------------------------------------------------------------------------
# Persistence score
# ---------------------------------------------------------------------------


def compute_persistence_score(
    *,
    age_days_list: list[float],  # same set as freshness; indexed by video
    view_counts_by_index: list[float | None],  # parallel to age_days_list
) -> tuple[float, dict[str, Any]]:
    """Compute persistence score [0, 1].

    Two components:
    - spread_score: how wide the publication date range is (up to PERSISTENCE_SPREAD_DAYS)
    - old_ratio: proportion of older videos (>1 year) that have meaningful views

    Returns 0.0 with diagnostics when < 2 dates available.
    """
    if len(age_days_list) < 2:
        return 0.0, {"reason": "insufficient_date_spread", "dates_available": len(age_days_list)}

    spread_days = max(age_days_list) - min(age_days_list)
    spread_score = min(1.0, spread_days / PERSISTENCE_SPREAD_DAYS)

    old_with_views = sum(
        1
        for i, d in enumerate(age_days_list)
        if d > 365 and view_counts_by_index[i] is not None and view_counts_by_index[i] > 0
    )
    old_ratio = old_with_views / len(age_days_list)

    score = min(1.0, 0.60 * spread_score + 0.40 * old_ratio)
    components: dict[str, Any] = {
        "spread_days": round(spread_days, 1),
        "spread_score": round(spread_score, 4),
        "old_successful_count": old_with_views,
        "old_ratio": round(old_ratio, 4),
        "dates_available": len(age_days_list),
    }
    return round(score, 4), components


# ---------------------------------------------------------------------------
# Policy snapshot builder (auto-persisted per run)
# ---------------------------------------------------------------------------


def build_interpretation_policy_snapshot(
    *,
    jaccard_threshold: float = CLUSTER_JACCARD_THRESHOLD,
    semantic_clustering_version: str = SEMANTIC_CLUSTERING_VERSION,
    caller_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the complete V1 interpretation policy as a serialisable dict.

    Called automatically by run_market_interpretation() so operators can
    inspect any run's scoring constants without referencing source code.
    Caller overrides are shallow-merged at the top level only.
    """
    snapshot: dict[str, Any] = {
        "clustering": {
            "version": CLUSTERING_VERSION,
            "jaccard_threshold": jaccard_threshold,
            "semantic_version": semantic_clustering_version,
        },
        "demand": {
            "view_log_scale": DEMAND_VIEW_LOG_SCALE,
            "single_video_cap": DEMAND_SINGLE_VIDEO_CAP,
            "weights": {"median_normalized": 0.80, "creator_factor": 0.20},
        },
        "saturation": {
            "creator_scale": SATURATION_CREATOR_SCALE,
            "search_log_scale": SATURATION_SEARCH_LOG_SCALE,
            "weights": {"creator": 0.60, "search": 0.40},
        },
        "freshness": {
            "halflife_days": FRESHNESS_HALFLIFE_DAYS,
            "recent_threshold_days": FRESHNESS_RECENT_THRESHOLD_DAYS,
            "weights": {"recency": 0.60, "recent_ratio": 0.40},
        },
        "momentum": {
            "vpd_log_scale": MOMENTUM_VPD_LOG_SCALE,
            "weights": {"median_vpd": 0.70, "positive_ratio": 0.30},
            "negative_correction_policy": "exclude_from_positive",
        },
        "persistence": {
            "spread_days": PERSISTENCE_SPREAD_DAYS,
            "old_content_threshold_days": 365,
            "weights": {"spread": 0.60, "old_ratio": 0.40},
        },
        "maturity": {
            "insufficient_video_threshold": 3,
            "exploratory_video_threshold": 5,
            "directional_video_threshold": 10,
            "actionable_creator_minimum": 5,
        },
        "confidence": {
            "count_weight": 0.40,
            "diversity_weight": 0.35,
            "temporal_weight": 0.15,
            "completeness_weight": 0.10,
            "count_saturates_at": 10,
            "diversity_saturates_at": 5,
            "temporal_saturates_at": 5,
        },
        "state_labels": {
            "accelerating": {"momentum_min": 0.5, "freshness_min": 0.5},
            "emerging": {"freshness_min": 0.6, "momentum_min_if_present": 0.2},
            "evergreen": {"persistence_min": 0.5, "freshness_max": 0.4},
            "cooling": {"momentum_max": 0.2, "freshness_max": 0.3},
            "saturated": {"saturation_min": 0.7},
            "mature": {"demand_min": 0.3},
        },
    }
    if caller_overrides:
        snapshot.update(caller_overrides)
    return snapshot


# ---------------------------------------------------------------------------
# Canonical cluster fingerprint
# ---------------------------------------------------------------------------


def make_canonical_cluster_fingerprint(
    *,
    normalized_queries: list[str],
    platform: str,
    provider: str,
    region_code: str | None,
    language_code: str | None,
) -> str:
    """Stable SHA-256 fingerprint for a canonical cluster's core probe set.

    Identical probe normalized_queries within the same scope → same fingerprint
    → same canonical identity across runs.
    """
    payload = {
        "normalized_queries": sorted(set(normalized_queries)),
        "platform": platform,
        "provider": provider,
        "region_code": region_code,
        "language_code": language_code,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------


class MarketInterpretationRun(BaseModel):
    """Audit record for one market interpretation pass.

    One row per (platform, provider, region, language, evidence_cutoff,
    clustering_version, scoring_version) combination.
    Idempotent: same inputs → same input_hash → returns existing row.
    """

    id: int
    platform: str = "youtube"
    provider: str = "youtube_data_api"
    region_code: str | None = None
    language_code: str | None = None
    clustering_version: str = CLUSTERING_VERSION
    scoring_version: str = SCORING_VERSION
    evidence_cutoff: str  # ISO timestamp; observations after this are excluded
    source_run_ids_json: str = "[]"  # JSON array of exploration_run_ids that sourced probes
    policy_snapshot_json: str = "{}"
    status: str = "pending"
    cluster_count: int = 0
    error_message: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    input_hash: str
    created_at: str

    def source_run_ids(self) -> list[int]:
        return json.loads(self.source_run_ids_json)

    def policy_snapshot(self) -> dict[str, Any]:
        return json.loads(self.policy_snapshot_json)


class CanonicalMarketCluster(BaseModel):
    """Stable cross-run market theme identity.

    Persisted in market_canonical_clusters.  Global scope (no channel_id).
    Multiple interpretation-run cluster snapshots reference the same
    canonical cluster when they represent the same real-world market theme.
    """

    id: int
    platform: str = "youtube"
    provider: str = "youtube_data_api"
    region_code: str | None = None
    language_code: str | None = None
    canonical_label: str
    normalized_label: str
    semantic_fingerprint: str  # SHA-256 of sorted probe normalized_queries
    identity_version: str = "v1"
    created_at: str
    updated_at: str


class MarketTopicCluster(BaseModel):
    """A named external market theme/topic cluster — one snapshot per run.

    Global scope: NOT owned by any channel.
    Scoped to (platform, provider, region, language).
    Each interpretation run creates a fresh set of clusters from current evidence.
    Historical clusters are preserved (one row per cluster per run).

    canonical_cluster_id links this snapshot to the stable cross-run identity
    in market_canonical_clusters (added in Phase 13E.1).
    """

    id: int
    interpretation_run_id: int
    platform: str = "youtube"
    provider: str = "youtube_data_api"
    region_code: str | None = None
    language_code: str | None = None
    cluster_label: str  # human-readable cluster name
    normalized_label: str  # stopword-stripped lowercase form
    cluster_type: str = "market_region"  # market_region | thematic | adjacent | bootstrap
    description: str = ""
    clustering_rationale: str = ""
    cluster_version: str = CLUSTERING_VERSION
    llm_used: int = 0  # 1 if LLM contributed to this cluster's label/grouping
    llm_model: str | None = None
    llm_prompt_version: str | None = None
    member_probe_count: int = 0
    member_video_count: int = 0
    canonical_cluster_id: int | None = None  # FK to market_canonical_clusters
    input_hash: str
    created_at: str


class MarketClusterMember(BaseModel):
    """Evidence member of a market topic cluster.

    member_type='probe_origin' — exploration probe that discovered this cluster.
    member_type='evidence_video' — external video that supports the cluster.
    member_type='semantic_anchor' — high-weight semantic evidence item.

    Only one of probe_id or external_video_id is set per row.
    Uniqueness enforced via partial indexes on each member type.
    """

    id: int
    cluster_id: int
    member_type: str = "evidence_video"  # probe_origin | evidence_video | semantic_anchor
    probe_id: int | None = None
    external_video_id: str | None = None
    platform: str = "youtube"
    provider: str = "youtube_data_api"
    supporting_job_id: int | None = None  # collection job that found the video
    created_at: str


class MarketClusterSignal(BaseModel):
    """Scored signal snapshot for one (cluster, interpretation run) pair.

    Historical snapshots are preserved — one row per (cluster_id, run_id).
    Scores are all normalized [0, 1] or None (missing evidence ≠ zero).
    Components JSON provides full decomposition for auditability.
    """

    id: int
    cluster_id: int
    interpretation_run_id: int

    # Signal dimensions — None means insufficient evidence, not zero
    demand_score: float | None = None
    saturation_score: float | None = None
    freshness_score: float | None = None
    momentum_score: float | None = None
    persistence_score: float | None = None
    confidence: float = 0.0  # [0, 1]; always present

    # Maturity and state
    signal_maturity: str = SIGNAL_MATURITY_INSUFFICIENT
    state_label: str | None = None  # optional deterministic label

    # Evidence counts
    supporting_video_count: int = 0
    supporting_creator_count: int = 0
    velocity_tracked_video_count: int = 0

    # Decomposed components (transparent and versioned)
    demand_components_json: str = "{}"
    saturation_components_json: str = "{}"
    freshness_components_json: str = "{}"
    momentum_components_json: str = "{}"
    persistence_components_json: str = "{}"

    # Audit
    scoring_version: str = SCORING_VERSION
    supporting_observation_ids_json: str = "[]"
    input_hash: str
    scored_at: str

    def demand_components(self) -> dict[str, Any]:
        return json.loads(self.demand_components_json)

    def saturation_components(self) -> dict[str, Any]:
        return json.loads(self.saturation_components_json)

    def freshness_components(self) -> dict[str, Any]:
        return json.loads(self.freshness_components_json)

    def momentum_components(self) -> dict[str, Any]:
        return json.loads(self.momentum_components_json)

    def persistence_components(self) -> dict[str, Any]:
        return json.loads(self.persistence_components_json)


# ---------------------------------------------------------------------------
# Phase 13F contract — typed handoff object
# ---------------------------------------------------------------------------


class ExternalMarketOpportunityEvidence(BaseModel):
    """Typed Phase 13F contract: one cluster's market intelligence package.

    Phase 13F combines this with the existing Research Engine opportunity model
    to generate channel-specific Opportunity scores.

    Phase 13E never creates Opportunity rows or scores.
    This object is derived from MarketTopicCluster + MarketClusterSignal.
    """

    cluster_id: int  # run-local cluster snapshot id
    canonical_cluster_id: int | None  # stable cross-run identity
    cluster_label: str
    normalized_label: str
    platform: str
    provider: str
    region_code: str | None
    language_code: str | None

    # Signal scores (None = missing evidence, not zero)
    demand_score: float | None
    saturation_score: float | None
    freshness_score: float | None
    momentum_score: float | None
    persistence_score: float | None
    confidence: float
    signal_maturity: str
    state_label: str | None

    # Evidence provenance
    supporting_video_count: int
    supporting_creator_count: int
    velocity_tracked_video_count: int
    signal_snapshot_id: int  # MarketClusterSignal.id
    interpretation_run_id: int

    # Decomposed components for Phase 13F inspection
    demand_components: dict[str, Any] = Field(default_factory=dict)
    saturation_components: dict[str, Any] = Field(default_factory=dict)
    freshness_components: dict[str, Any] = Field(default_factory=dict)
    momentum_components: dict[str, Any] = Field(default_factory=dict)
    persistence_components: dict[str, Any] = Field(default_factory=dict)


def build_opportunity_evidence(
    cluster: MarketTopicCluster,
    signal: MarketClusterSignal,
) -> ExternalMarketOpportunityEvidence:
    """Construct the Phase 13F contract object from a cluster + signal pair."""
    return ExternalMarketOpportunityEvidence(
        cluster_id=cluster.id,
        canonical_cluster_id=cluster.canonical_cluster_id,
        cluster_label=cluster.cluster_label,
        normalized_label=cluster.normalized_label,
        platform=cluster.platform,
        provider=cluster.provider,
        region_code=cluster.region_code,
        language_code=cluster.language_code,
        demand_score=signal.demand_score,
        saturation_score=signal.saturation_score,
        freshness_score=signal.freshness_score,
        momentum_score=signal.momentum_score,
        persistence_score=signal.persistence_score,
        confidence=signal.confidence,
        signal_maturity=signal.signal_maturity,
        state_label=signal.state_label,
        supporting_video_count=signal.supporting_video_count,
        supporting_creator_count=signal.supporting_creator_count,
        velocity_tracked_video_count=signal.velocity_tracked_video_count,
        signal_snapshot_id=signal.id,
        interpretation_run_id=signal.interpretation_run_id,
        demand_components=signal.demand_components(),
        saturation_components=signal.saturation_components(),
        freshness_components=signal.freshness_components(),
        momentum_components=signal.momentum_components(),
        persistence_components=signal.persistence_components(),
    )


# ---------------------------------------------------------------------------
# LLM structured output for optional semantic consolidation
# ---------------------------------------------------------------------------


class ClusterConsolidationCandidate(BaseModel):
    """One LLM-proposed cluster consolidation.

    member_probe_ids must reference only probe IDs supplied in the prompt.
    Any fabricated IDs are rejected by _validate_llm_cluster_members().
    """

    cluster_label: str
    cluster_type: str = "thematic"
    description: str
    member_probe_ids: list[int]  # must be subset of supplied valid IDs
    evidence_basis: str  # rationale citing supplied probe queries

    model_config = {"extra": "forbid"}


class ClusterConsolidationOutput(BaseModel):
    """Structured LLM response — cluster groupings with evidence citations."""

    clusters: list[ClusterConsolidationCandidate]

    model_config = {"extra": "forbid"}


def validate_llm_cluster_members(
    clusters: list[ClusterConsolidationCandidate],
    valid_probe_ids: set[int],
) -> list[ClusterConsolidationCandidate]:
    """Remove fabricated probe IDs; drop clusters that become empty after filtering."""
    valid_clusters = []
    for c in clusters:
        filtered_ids = [pid for pid in c.member_probe_ids if pid in valid_probe_ids]
        if not filtered_ids:
            continue  # LLM fabricated all members — drop cluster
        valid_clusters.append(
            ClusterConsolidationCandidate(
                cluster_label=c.cluster_label,
                cluster_type=c.cluster_type,
                description=c.description,
                member_probe_ids=filtered_ids,
                evidence_basis=c.evidence_basis,
            )
        )
    return valid_clusters


# ---------------------------------------------------------------------------
# Semantic adjudication output (Phase 13E.1 hybrid clustering)
# ---------------------------------------------------------------------------


class ClusterAdjudicationGroup(BaseModel):
    """One proposed cluster group from LLM semantic adjudication.

    member_probe_ids must be a subset of the supplied valid probe IDs.
    Fabricated IDs are filtered by validate_adjudication_output().
    """

    member_probe_ids: list[int]
    rationale: str  # explains why these probes belong together
    evidence_basis: str  # which video titles or probe queries support the grouping

    model_config = {"extra": "forbid"}


class ClusterAdjudicationOutput(BaseModel):
    """Structured LLM response for semantic cluster adjudication.

    The LLM proposes COMPLETE final group assignments:
    - Can MERGE lexically-dissimilar probes (different vocab, same market theme)
    - Can SPLIT lexically-similar probes (similar vocab, different market intent)
    - Must cover every supplied probe exactly once

    Validation removes fabricated IDs; missing probes become singleton groups.
    """

    final_groups: list[ClusterAdjudicationGroup]

    model_config = {"extra": "forbid"}


def validate_adjudication_output(
    output: ClusterAdjudicationOutput,
    valid_probe_ids: set[int],
    all_probe_ids: list[int],
) -> dict[int, list[int]]:
    """Validate and normalise LLM adjudication output into index-keyed groups.

    Returns groups keyed by the minimum probe ID in each group (like Jaccard groups).
    Ensures all supplied probes are assigned; fabricated IDs are silently dropped.
    Probes missing from LLM output become singleton groups.
    """
    assigned: set[int] = set()
    result_groups: dict[int, list[int]] = {}

    for group in output.final_groups:
        valid_ids = [pid for pid in group.member_probe_ids if pid in valid_probe_ids]
        if not valid_ids:
            continue  # fully fabricated group — skip
        canonical_key = min(valid_ids)
        result_groups[canonical_key] = sorted(valid_ids)
        assigned.update(valid_ids)

    # Any probe the LLM omitted becomes a singleton
    for pid in all_probe_ids:
        if pid not in assigned:
            result_groups.setdefault(pid, [pid])

    return result_groups


# ---------------------------------------------------------------------------
# Input hash helpers
# ---------------------------------------------------------------------------


def make_interpretation_run_input_hash(
    *,
    platform: str,
    provider: str,
    region_code: str | None,
    language_code: str | None,
    evidence_cutoff: str,
    source_run_ids: list[int],
    clustering_version: str,
    scoring_version: str,
) -> str:
    """Deterministic identity hash for one interpretation run."""
    payload = {
        "platform": platform,
        "provider": provider,
        "region_code": region_code,
        "language_code": language_code,
        "evidence_cutoff": evidence_cutoff,
        "source_run_ids": sorted(source_run_ids),
        "clustering_version": clustering_version,
        "scoring_version": scoring_version,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def make_cluster_input_hash(
    *,
    normalized_label: str,
    region_code: str | None,
    language_code: str | None,
    platform: str,
    provider: str,
    cluster_version: str,
    member_probe_ids: list[int],
) -> str:
    """Deterministic identity hash for a cluster.

    Changes when probe membership changes (new probes added to scope).
    """
    payload = {
        "normalized_label": normalized_label,
        "region_code": region_code,
        "language_code": language_code,
        "platform": platform,
        "provider": provider,
        "cluster_version": cluster_version,
        "member_probe_ids": sorted(member_probe_ids),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def make_signal_input_hash(
    *,
    cluster_id: int,
    member_video_ids: list[str],
    scoring_version: str,
) -> str:
    """Deterministic identity hash for a signal snapshot.

    Same cluster + same videos + same scoring policy → same hash → idempotent.
    """
    payload = {
        "cluster_id": cluster_id,
        "member_video_ids": sorted(member_video_ids),
        "scoring_version": scoring_version,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(raw.encode()).hexdigest()
