"""Phase 14E — Experiment strategy brief models.

A strategy brief is the handoff artifact produced from a selected planning
decision.  It encodes:
  - WHAT to test (market cluster and/or feature treatment)
  - WHY (strategic reason, information_gain context)
  - HOW NOT to confound the experiment (controlled factor baselines)
  - CONTENT CONSTRAINTS (excluded topics, brand voice, audience)

What a strategy brief is NOT:
  - It does not generate scripts or narration.
  - It does not call YouTube, LLMs, or any external API.
  - It does not create Experiment rows (that is a downstream human-approval step).
  - It does not assign actual treatment values (operators decide).

Responsibility boundary:
  STRATEGY BRIEF: chooses WHAT to test
  IDEA GENERATION: chooses HOW to express it as a video
  PRODUCTION:      chooses HOW to produce the video
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class FactorAutonomy(StrEnum):
    """How autonomously the system can assign a value for this factor."""

    # Numeric with safe bounds + max_delta, or boolean with known safe values:
    # the system can propose an intended_value without operator review.
    AUTONOMOUSLY_ASSIGNABLE_NOW = "autonomously_assignable_now"
    # Categorical with no enumerated allowlist: must be set by a human operator.
    MANUAL_ONLY = "manual_only"
    # Factor is measured/observed but never varied as a treatment.
    OBSERVABLE_ONLY = "observable_only"


class ConfoundingRisk(StrEnum):
    """Risk that the experiment conflates two independent variables."""

    # Single variable changed (cluster only, or feature only on tested cluster).
    LOW = "low"
    # Cluster is under-tested and a feature is also varied; results may be
    # partially unattributable.
    MODERATE = "moderate"
    # Two or more treatment factors, or cluster is new AND a feature is varied
    # simultaneously — results cannot be attributed to either variable.
    HIGH = "high"


class BriefPlanningIntent(StrEnum):
    """Semantic intent of the strategy brief (richer than PlanningIntent)."""

    # Untested cluster; no treatment factors — only variable is the topic.
    MARKET_EXPLORATION = "market_exploration"
    # Tested cluster with one feature treatment — testing a production variable.
    FEATURE_EXPLORATION = "feature_exploration"
    # Re-testing a cluster that has prior experiments to validate/update signal.
    VALIDATION = "validation"
    # High-evidence cluster; treatment optimizes a known production parameter.
    EXPLOITATION = "exploitation"


@dataclass(frozen=True)
class TreatmentFactorSpec:
    """A factor proposed as the treatment variable for this experiment.

    intended_value is always None — operators decide the actual value.
    Phase 14E never assigns values autonomously.
    """

    factor_name: str
    value_type: str  # "numeric" | "boolean" | "categorical"
    autonomy: FactorAutonomy
    safe_range_min: float | None = None
    safe_range_max: float | None = None
    safe_values: tuple[str, ...] | None = None  # frozen for hashability
    intended_value: str | None = None  # always None in Phase 14E


@dataclass(frozen=True)
class ControlledFactorBaseline:
    """Baseline for a factor held constant during the experiment.

    baseline_value is the authoritative value from the named baseline_source.
    None means the source was consulted but had no value (new channel/profile).
    """

    factor_name: str
    baseline_value: str | None
    # "voice_profile" | "channel_profile" | "historical_mean" | "unknown"
    baseline_source: str
    # Allowed deviation from baseline (None = must match exactly)
    tolerance: str | None = None


@dataclass(frozen=True)
class ContentConstraints:
    """Content-level constraints derived from the channel profile.

    These constrain IDEA GENERATION without constraining the experiment itself.
    The experiment determines the strategic direction; idea generation operates
    within these bounds to express that direction as a video concept.
    """

    excluded_topics: tuple[str, ...] = field(default_factory=tuple)
    primary_niche: str = ""
    secondary_niches: tuple[str, ...] = field(default_factory=tuple)
    brand_voice: str = ""
    content_style: str = ""
    audience_description: str = ""


@dataclass
class ExperimentStrategyBrief:
    """The handoff artifact from a selected planning decision.

    One brief per selection_decision — the UNIQUE constraint on
    selection_decision_id enforces this at the DB layer.

    created_at is set by the DB default; brief_hash is deterministic and
    excludes timestamps so re-creation with the same inputs yields the same hash.
    """

    # Identity
    id: str
    channel_id: int
    planning_run_id: str
    selection_decision_id: int
    opportunity_id: int
    canonical_cluster_id: int | None
    channel_profile_version_id: int | None

    # Planning intent
    brief_planning_intent: BriefPlanningIntent
    experiment_type: str  # "exploration" | "exploitation"

    # Market context
    market_theme: str
    canonical_topic: str
    strategic_reason: str
    information_gain_reason: str

    # Experiment contract
    hypothesis: str
    target_metric: str
    target_direction: str  # "higher_is_better" | "lower_is_better"

    # Treatment specification (0 or 1 factors in Phase 14E)
    treatment_factors: list[TreatmentFactorSpec] = field(default_factory=list)

    # Controlled factor baselines (always populated)
    controlled_factors: list[ControlledFactorBaseline] = field(default_factory=list)

    # Content constraints from channel profile
    content_constraints: ContentConstraints = field(default_factory=ContentConstraints)

    # Risk assessment
    confounding_risk: ConfoundingRisk = ConfoundingRisk.LOW

    # Provenance
    policy_version: str = ""
    eligibility_classification: str = ""
    score_decomposition_json: str = "{}"

    # Deterministic fingerprint (excludes timestamps)
    brief_hash: str = ""

    # Status (human approval state)
    status: str = "pending_approval"

    # Set by DB default on INSERT
    created_at: str | None = None
