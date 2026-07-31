"""Pydantic models for the Phase 3 channel strategy foundation."""

from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class Platform(StrEnum):
    youtube = "youtube"
    instagram = "instagram"
    tiktok = "tiktok"


class OperatingMode(StrEnum):
    manual = "manual"
    supervised = "supervised"
    autonomous = "autonomous"


class MaturityStage(StrEnum):
    validation = "validation"
    growth = "growth"
    monetization = "monetization"
    optimization = "optimization"
    scaling = "scaling"


class AudienceIntent(StrEnum):
    educational = "educational"
    entertainment = "entertainment"
    mixed = "mixed"


class BrandVoice(StrEnum):
    authoritative = "authoritative"
    conversational = "conversational"
    energetic = "energetic"
    calm = "calm"
    humorous = "humorous"


class ContentStyle(StrEnum):
    story_driven = "story-driven"
    list_based = "list-based"
    explainer = "explainer"
    mixed = "mixed"


class PrimaryFormat(StrEnum):
    short = "short"
    long_form = "long_form"
    both = "both"
    content_package = "content_package"


class MonetizationStatus(StrEnum):
    pre = "pre"
    active = "active"


class VersionStatus(StrEnum):
    draft = "draft"
    active = "active"
    superseded = "superseded"


class LifecycleState(StrEnum):
    new = "new"
    under_review = "under_review"
    approved = "approved"
    rejected = "rejected"
    produced = "produced"
    archived = "archived"


class FormatRecommendation(StrEnum):
    short = "short"
    long_form = "long_form"
    both = "both"
    content_package = "content_package"
    undecided = "undecided"


class StrategicRole(StrEnum):
    discovery = "discovery"
    monetization = "monetization"
    subscriber_growth = "subscriber_growth"
    authority = "authority"
    retention = "retention"
    experimentation = "experimentation"


class SourceQualityTier(StrEnum):
    high = "high"
    medium_high = "medium_high"
    medium = "medium"
    variable = "variable"


class RunStatus(StrEnum):
    pending = "pending"
    running = "running"
    completed = "completed"
    partial = "partial"
    failed = "failed"


class AdapterName(StrEnum):
    manual = "manual"
    youtube_data_api = "youtube_data_api"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Default scoring weights for the initial versioned policy (operator decision D1).
# These are configurable — not permanent constants. Referenced by scoring_policy_version "1.0.0".
DEFAULT_SCORING_WEIGHTS: dict[str, float] = {
    "audience_demand": 0.30,
    "competition": 0.25,
    "channel_fit": 0.25,
    "production_feasibility": 0.20,
}

# Default pre-monetization objective weights (must sum to 1.0).
DEFAULT_PRE_MONETIZATION_WEIGHTS: dict[str, float] = {
    "qualified_subscriber_growth": 0.40,
    "watch_hour_progress": 0.25,
    "returning_viewer_rate": 0.15,
    "audience_fit": 0.15,
    "publishing_consistency": 0.05,
}

# All valid monetization objective keys.
VALID_OBJECTIVES: frozenset[str] = frozenset(
    {
        # Pre-monetization
        "qualified_subscriber_growth",
        "watch_hour_progress",
        "returning_viewer_rate",
        "audience_fit",
        "publishing_consistency",
        # Post-monetization (can be weighted at any stage for planning)
        "ad_revenue",
        "rpm_optimization",
        "contribution_margin",
        "affiliate_conversion",
        "sponsorship_potential",
        "digital_product_revenue",
        "membership_revenue",
        "lifetime_content_value",
        "audience_growth",
        "authority_building",
    }
)

# Discovery adapters available in Phase 3 MVP.
VALID_ADAPTERS: frozenset[str] = frozenset(
    {
        "manual",
        "youtube_data_api",
        "competitor_research",  # optional — Milestone 3.2b
        "google_trends",  # optional — Milestone 3.2c (deferred, D3)
        "internal_suggestion",
    }
)

# Operator-approved capacity defaults (D6). These are ceilings, not quotas.
DEFAULT_CAPACITY = {
    "long_form_slots_per_week": 2,
    "short_slots_per_week": 4,
    "content_package_slots_per_week": 1,
    "max_concurrent_productions": 2,
    "review_hours_per_week": 3.0,
}


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class PortfolioTargets(BaseModel):
    """Content-type allocation targets. Values must sum to 1.0."""

    model_config = ConfigDict(extra="forbid")

    evergreen: float = Field(default=0.60, ge=0.0, le=1.0)
    trending: float = Field(default=0.20, ge=0.0, le=1.0)
    seasonal: float = Field(default=0.10, ge=0.0, le=1.0)
    experimental: float = Field(default=0.10, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _sum_to_one(self) -> PortfolioTargets:
        total = self.evergreen + self.trending + self.seasonal + self.experimental
        if abs(total - 1.0) > 0.001:
            raise ValueError(f"Portfolio targets must sum to 1.0, got {total:.4f}")
        return self


# ---------------------------------------------------------------------------
# Primary models
# ---------------------------------------------------------------------------


class Channel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    platform: Platform = Platform.youtube
    channel_name: str = Field(min_length=1, max_length=200)
    platform_channel_id: str | None = None
    operating_mode: OperatingMode = OperatingMode.manual
    current_profile_version_id: int | None = None
    current_strategy_id: int | None = None
    current_maturity_stage: MaturityStage = MaturityStage.validation
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("channel_name", mode="before")
    @classmethod
    def _strip_name(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v


class ChannelMonetizationStrategy(BaseModel):
    """Versioned monetization objectives. Immutable after creation."""

    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    channel_id: int
    version: int = Field(ge=1)
    monetization_status: MonetizationStatus = MonetizationStatus.pre
    objective_weights: dict[str, float] = Field(
        default_factory=lambda: dict(DEFAULT_PRE_MONETIZATION_WEIGHTS)
    )
    description: str = Field(default="", max_length=500)
    active_from: datetime | None = None
    superseded_at: datetime | None = None
    created_by: str = Field(default="", max_length=100)
    status: VersionStatus = VersionStatus.draft
    activated_at: datetime | None = None
    activated_by: str = Field(default="", max_length=100)
    activation_reason: str = Field(default="", max_length=500)

    @field_validator("objective_weights", mode="before")
    @classmethod
    def _validate_weights(cls, v: object) -> object:
        if not isinstance(v, dict) or not v:
            raise ValueError("objective_weights must be a non-empty dict")
        invalid = set(v) - VALID_OBJECTIVES
        if invalid:
            raise ValueError(f"Unknown objective keys: {sorted(invalid)}")
        for key, weight in v.items():
            if not isinstance(weight, int | float) or weight < 0.0:
                raise ValueError(f"Weight for {key!r} must be non-negative, got {weight!r}")
        total = sum(v.values())
        if abs(total - 1.0) > 0.001:
            raise ValueError(f"Objective weights must sum to 1.0, got {total:.4f}")
        return dict(v)


class ChannelProfileVersion(BaseModel):
    """Immutable snapshot of a channel's content strategy at a point in time."""

    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    channel_id: int
    version: int = Field(ge=1)
    strategy_id: int | None = None
    maturity_stage: MaturityStage = MaturityStage.validation
    # Niche and audience
    primary_niche: str = Field(min_length=1, max_length=100)
    secondary_niches: list[str] = Field(default_factory=list)
    excluded_topics: list[str] = Field(default_factory=list)
    audience_description: str = Field(default="", max_length=1000)
    audience_demographics: str = Field(default="", max_length=500)
    audience_intent: AudienceIntent = AudienceIntent.educational
    # Brand and voice
    brand_voice: BrandVoice = BrandVoice.conversational
    tone_notes: str = Field(default="", max_length=500)
    brand_rules: list[str] = Field(default_factory=list)
    content_style: ContentStyle = ContentStyle.explainer
    # Content strategy
    primary_format: PrimaryFormat = PrimaryFormat.short
    posting_cadence_per_week: int = Field(default=3, ge=1, le=21)
    portfolio_targets: PortfolioTargets = Field(default_factory=PortfolioTargets)
    # Discovery settings
    allowed_discovery_adapters: list[str] = Field(
        default_factory=lambda: ["manual", "youtube_data_api"]
    )
    max_candidates_per_run: int = Field(default=20, ge=1, le=200)
    min_opportunity_score: float = Field(default=0.40, ge=0.0, le=1.0)
    duplicate_similarity_threshold: float = Field(default=0.70, ge=0.0, le=1.0)  # D5
    signal_staleness_days: int = Field(default=7, ge=1, le=90)
    # Scoring policy reference — populated by Milestone 3.3 scoring engine
    scoring_policy_version: str = Field(default="1.0.0", max_length=20)
    # Timestamps (set by repository; immutable after creation)
    active_from: datetime | None = None
    superseded_at: datetime | None = None
    created_by: str = Field(default="", max_length=100)
    status: VersionStatus = VersionStatus.draft
    activated_at: datetime | None = None
    activated_by: str = Field(default="", max_length=100)
    activation_reason: str = Field(default="", max_length=500)

    @field_validator("primary_niche", mode="before")
    @classmethod
    def _strip_niche(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v

    @field_validator("allowed_discovery_adapters", mode="before")
    @classmethod
    def _validate_adapters(cls, v: object) -> object:
        if not isinstance(v, list):
            raise ValueError("allowed_discovery_adapters must be a list")
        invalid = set(v) - VALID_ADAPTERS
        if invalid:
            raise ValueError(f"Unknown discovery adapters: {sorted(invalid)}")
        return v


class ChannelCapacityPolicy(BaseModel):
    """Production capacity limits. All slot values are ceilings, not quotas."""

    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    channel_id: int
    # Slot ceilings — the system must never fill capacity with weak opportunities
    long_form_slots_per_week: int = Field(default=2, ge=0, le=20)
    short_slots_per_week: int = Field(default=4, ge=0, le=50)
    content_package_slots_per_week: int = Field(default=1, ge=0, le=10)
    max_concurrent_productions: int = Field(default=2, ge=1, le=20)
    # Budget ceilings
    daily_budget_usd: float = Field(default=10.0, ge=0.0)
    per_video_budget_usd: float = Field(default=5.0, ge=0.0)
    monthly_budget_usd: float = Field(default=200.0, ge=0.0)
    # Review time ceilings
    review_hours_per_week: float = Field(default=3.0, ge=0.0)
    review_hours_per_short: float = Field(default=0.5, ge=0.0)
    review_hours_per_long_form: float = Field(default=1.5, ge=0.0)
    review_hours_per_package: float = Field(default=2.5, ge=0.0)
    # Trend reservation
    trend_reservation_slots: int = Field(default=1, ge=0, le=10)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DiscoveryRun(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    channel_id: int
    profile_version_id: int
    adapter_name: AdapterName
    query_parameters_json: str = "{}"
    status: RunStatus = RunStatus.pending
    candidate_count: int = 0
    new_opportunity_count: int = 0
    dedup_count: int = 0
    failed_count: int = 0
    quota_units_consumed: int = 0
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class Opportunity(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    channel_id: int
    discovery_run_id: int
    normalized_topic: str = Field(min_length=1, max_length=200)
    raw_topic: str = Field(min_length=1, max_length=200)
    title: str = Field(default="", max_length=300)
    topic_summary: str = Field(default="", max_length=1000)
    format_recommendation: FormatRecommendation = FormatRecommendation.undecided
    strategic_role: StrategicRole = StrategicRole.discovery
    current_lifecycle_state: LifecycleState = LifecycleState.new
    created_at: datetime | None = None
    updated_at: datetime | None = None


class OpportunityObservation(BaseModel):
    """Time-sensitive evidence-collection event. is_stale is computed, not stored."""

    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    opportunity_id: int
    discovery_run_id: int
    adapter_name: AdapterName
    collected_at: datetime | None = None
    signal_age_days: float | None = None
    source_quality_tier: SourceQualityTier = SourceQualityTier.medium
    raw_payload_json: str = "{}"
    collection_notes: str = ""
    was_deduplicated: bool = False
    candidate_topic: str | None = None
    dedup_similarity_score: float | None = None

    def is_stale(self, signal_staleness_days: int) -> bool:
        if self.signal_age_days is None:
            return False
        return self.signal_age_days > signal_staleness_days


class OpportunitySourceEvidence(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    observation_id: int
    opportunity_id: int
    evidence_type: str
    evidence_value: float | None = None
    evidence_text: str | None = None
    evidence_unit: str = ""
    source_label: str
    collected_at: datetime | None = None


class OpportunityStateEvent(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    opportunity_id: int
    from_state: LifecycleState | None = None
    to_state: LifecycleState
    actor: str = Field(default="system", max_length=100)
    reason: str = Field(default="", max_length=500)
    created_at: datetime | None = None


class ChannelOperatingModeEvent(BaseModel):
    """Append-only record of every operating mode transition."""

    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    channel_id: int
    from_mode: OperatingMode | None = None
    to_mode: OperatingMode
    operator: str = Field(default="", max_length=100)
    reason: str = Field(default="", max_length=500)
    created_at: datetime | None = None


# ---------------------------------------------------------------------------
# M3.3 Scoring engine enums and models
# ---------------------------------------------------------------------------


class MissingDataPolicy(StrEnum):
    reweight_available = "reweight_available"
    apply_prior = "apply_prior"
    require_research = "require_research"
    # mandatory and reduce_confidence_only deferred to a future milestone


class FactorStatus(StrEnum):
    present = "present"
    absent = "absent"
    insufficient = "insufficient"
    degraded = "degraded"


class PolicyStatus(StrEnum):
    draft = "draft"
    active = "active"
    archived = "archived"


class FactorResult(BaseModel):
    """Internal result of a single factor computation. Not persisted directly."""

    name: str
    raw_score: float | None
    status: FactorStatus
    observation_ids: list[int] = Field(default_factory=list)
    evidence_row_ids: list[int] = Field(default_factory=list)
    notes: str | None = None


class ScoringPolicy(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    channel_id: int
    version: int = Field(default=1, ge=1)
    label: str = Field(min_length=1, max_length=200)
    description: str | None = None

    weight_trend_strength: float = Field(default=0.05, ge=0.0, le=1.0)
    weight_audience_demand: float = Field(default=0.20, ge=0.0, le=1.0)
    weight_competition: float = Field(default=0.15, ge=0.0, le=1.0)
    weight_evergreen_value: float = Field(default=0.20, ge=0.0, le=1.0)
    weight_audience_fit: float = Field(default=0.30, ge=0.0, le=1.0)
    weight_content_novelty: float = Field(default=0.10, ge=0.0, le=1.0)

    missing_trend_strength: MissingDataPolicy = MissingDataPolicy.reweight_available
    missing_audience_demand: MissingDataPolicy = MissingDataPolicy.reweight_available
    missing_competition: MissingDataPolicy = MissingDataPolicy.reweight_available
    missing_evergreen_value: MissingDataPolicy = MissingDataPolicy.reweight_available
    missing_audience_fit: MissingDataPolicy = MissingDataPolicy.require_research
    missing_content_novelty: MissingDataPolicy = MissingDataPolicy.reweight_available

    min_confidence_threshold: float = Field(default=0.40, ge=0.0, le=1.0)
    freshness_decay_days: float = Field(default=7.0, gt=0.0)
    max_corroboration_bonus: float = Field(default=0.10, ge=0.0, le=1.0)
    corroboration_bonus_per_source: float = Field(default=0.05, ge=0.0, le=1.0)

    status: PolicyStatus = PolicyStatus.draft
    is_default: bool = False
    activated_at: datetime | None = None
    archived_at: datetime | None = None
    created_at: datetime | None = None
    created_by: str | None = None

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> ScoringPolicy:
        total = (
            self.weight_trend_strength
            + self.weight_audience_demand
            + self.weight_competition
            + self.weight_evergreen_value
            + self.weight_audience_fit
            + self.weight_content_novelty
        )
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"Factor weights must sum to 1.0, got {total:.10f}")
        return self


class OpportunityScore(BaseModel):
    """Immutable scoring snapshot. Rows are never updated — always appended."""

    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    opportunity_id: int
    scoring_policy_id: int
    channel_profile_version_id: int

    composite_score: float
    confidence: float

    score_trend_strength: float | None = None
    score_audience_demand: float | None = None
    score_competition: float | None = None
    score_evergreen_value: float | None = None
    score_audience_fit: float | None = None
    score_content_novelty: float | None = None

    status_trend_strength: FactorStatus = FactorStatus.absent
    status_audience_demand: FactorStatus = FactorStatus.absent
    status_competition: FactorStatus = FactorStatus.absent
    status_evergreen_value: FactorStatus = FactorStatus.absent
    status_audience_fit: FactorStatus = FactorStatus.absent
    status_content_novelty: FactorStatus = FactorStatus.absent

    eff_weight_trend_strength: float = 0.0
    eff_weight_audience_demand: float = 0.0
    eff_weight_competition: float = 0.0
    eff_weight_evergreen_value: float = 0.0
    eff_weight_audience_fit: float = 0.0
    eff_weight_content_novelty: float = 0.0

    observation_ids_json: str = "[]"
    input_snapshot_json: str = "{}"
    input_hash: str = ""

    below_confidence_threshold: bool = False
    requires_research: bool = False
    scored_at: datetime | None = None
    scorer_version: str = "1.0"

    @property
    def observation_ids(self) -> list[int]:
        return json.loads(self.observation_ids_json)

    @property
    def input_snapshot(self) -> dict:
        return json.loads(self.input_snapshot_json)


class FactorContext(BaseModel):
    """Immutable bundle passed to every factor function. Avoids global state."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    opportunity: Opportunity
    profile: ChannelProfileVersion
    observations: list[OpportunityObservation]
    evidence: dict[int, list[OpportunitySourceEvidence]]
    best_similarity: float | None = None
    matched_opportunity_id: int | None = None
    matched_normalized_topic: str | None = None
