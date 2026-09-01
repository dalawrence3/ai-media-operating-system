"""Phase 14D — Experiment planning models, policy, and safe factor registry.

This module answers: "Among eligible Opportunities, which experiment candidates
are most valuable to run next, and in what portfolio?"

It does NOT:
- Call YouTube, LLMs, or any external API
- Create Experiment rows (planning artifacts only)
- Assign actual treatment values (operators decide the value)
- Automatically select which experiment to run (that remains a human decision)

Key concepts:
- PlanningIntent: exploration / exploitation / validation (separate from ExperimentType)
- CandidateScoreComponents: decomposed scores — exploitation_value and
  exploration_value are separate
- ExploitationValue: opportunity_attractiveness + internal_evidence + production_feasibility
- ExplorationValue: information_gain + uncertainty + cluster_coverage_need
- Cluster diversity guards: max 40% cluster share, max 2 consecutive same cluster
- HIGH OPPORTUNITY SCORE does NOT automatically mean MAKE THIS NEXT

Phase 14F adds a unified factor registry: SafeFactorSpec now carries the complete
per-factor contract (autonomy, actual_value_source, control_capability, tolerance)
so that planning, briefing, and execution all read from one authoritative source.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum

from app.intelligence.experiments.strategy_brief import FactorAutonomy
from app.learning.constants import (
    NARRATION_PACE_SPEAKING_RATE_MAX,
    NARRATION_PACE_SPEAKING_RATE_MIN,
)
from app.visuals.policy import VISUAL_STYLE_SAFE_VALUES


class PlanningIntent(StrEnum):
    EXPLORATION = "exploration"
    EXPLOITATION = "exploitation"
    VALIDATION = "validation"


class ControlCapability(StrEnum):
    """Whether the production pipeline can deterministically enforce a factor's value.

    ENFORCED: the pipeline has a direct, deterministic override path (e.g.
        narration_speaking_rate via narrate_plan(speaking_rate_override=...)).
    SOFT: the factor is influenced by prompt instructions, but the LLM or provider
        is not guaranteed to honour the request exactly.
    NOT_CONTROLLABLE: no current pipeline mechanism to set this factor.
    """

    ENFORCED = "enforced"
    SOFT = "soft"
    NOT_CONTROLLABLE = "not_controllable"


@dataclass(frozen=True)
class SafeFactorSpec:
    """Unified specification for a safe, controllable experiment factor.

    Phase 14F adds autonomy, actual_value_source, control_capability, and
    tolerance so planning, briefing, and execution all share one registry.
    All new fields have defaults to preserve backwards compatibility with
    existing tests that construct SafeFactorSpec directly.
    """

    feature_name: str
    value_type: str  # "numeric", "boolean", "categorical"
    safe_range_min: float | None = None
    safe_range_max: float | None = None
    safe_values: list[str] | None = None
    bucket_width: float | None = None

    # Phase 14F unified registry fields
    autonomy: FactorAutonomy = FactorAutonomy.MANUAL_ONLY
    # Column name in content_feature_snapshots, or "not_observable"
    actual_value_source: str = "not_observable"
    control_capability: ControlCapability = ControlCapability.NOT_CONTROLLABLE
    # Allowed absolute deviation from intended_value (None = exact match required)
    tolerance_abs: float | None = None


# Registry of factors that are safe to treat as treatment variables.
# Factors NOT in this dict are unknown-safety; they will not be proposed as treatments.
# A factor being absent does NOT mean it is unsafe — it means we have not yet
# assessed its safety for autonomous proposal.
#
# This is the SINGLE authoritative registry for all factor metadata.
# Phase 14D reads safe_range_min/max for scoring.
# Phase 14E reads autonomy for brief building.
# Phase 14F reads control_capability, actual_value_source, tolerance_abs for execution.
SAFE_CONTROLLABLE_FACTORS: dict[str, SafeFactorSpec] = {
    "narration_speaking_rate": SafeFactorSpec(
        feature_name="narration_speaking_rate",
        value_type="numeric",
        safe_range_min=NARRATION_PACE_SPEAKING_RATE_MIN,
        safe_range_max=NARRATION_PACE_SPEAKING_RATE_MAX,
        bucket_width=0.1,
        autonomy=FactorAutonomy.AUTONOMOUSLY_ASSIGNABLE_NOW,
        actual_value_source="narration_speaking_rate",
        control_capability=ControlCapability.ENFORCED,
        tolerance_abs=0.001,  # floating-point rounding only; provider uses value exactly
    ),
    "has_hook": SafeFactorSpec(
        feature_name="has_hook",
        value_type="boolean",
        safe_values=["true", "false"],
        autonomy=FactorAutonomy.AUTONOMOUSLY_ASSIGNABLE_NOW,
        actual_value_source="has_hook",
        control_capability=ControlCapability.SOFT,
        tolerance_abs=None,
    ),
    "has_cta": SafeFactorSpec(
        feature_name="has_cta",
        value_type="boolean",
        safe_values=["true", "false"],
        autonomy=FactorAutonomy.AUTONOMOUSLY_ASSIGNABLE_NOW,
        actual_value_source="has_cta",
        control_capability=ControlCapability.SOFT,
        tolerance_abs=None,
    ),
    "script_format": SafeFactorSpec(
        feature_name="script_format",
        value_type="categorical",
        autonomy=FactorAutonomy.MANUAL_ONLY,
        actual_value_source="script_format",
        control_capability=ControlCapability.SOFT,
        tolerance_abs=None,
    ),
    "narration_voice_id": SafeFactorSpec(
        feature_name="narration_voice_id",
        value_type="categorical",
        autonomy=FactorAutonomy.MANUAL_ONLY,
        actual_value_source="narration_voice_id",
        control_capability=ControlCapability.ENFORCED,
        tolerance_abs=None,
    ),
    "publish_day_of_week": SafeFactorSpec(
        feature_name="publish_day_of_week",
        value_type="categorical",
        autonomy=FactorAutonomy.MANUAL_ONLY,
        actual_value_source="publish_day_of_week",
        control_capability=ControlCapability.SOFT,
        tolerance_abs=None,
    ),
    "render_caption_burn_in": SafeFactorSpec(
        feature_name="render_caption_burn_in",
        value_type="boolean",
        safe_values=["true", "false"],
        autonomy=FactorAutonomy.AUTONOMOUSLY_ASSIGNABLE_NOW,
        actual_value_source="render_caption_burn_in",
        control_capability=ControlCapability.ENFORCED,
        tolerance_abs=None,
    ),
    # Phase 18E — visual treatment as an experimentable dimension.
    #
    # ENFORCED, and genuinely so: the value is written into the stage's
    # effective_config, `policy_from_config` resolves it into the VisualPolicy
    # the engine actually runs under, and the realized value is read back from
    # the render's own assessment. It is not a prompt hint.
    #
    # The safe values are exactly the presets the renderer implements. A style
    # the renderer does not know falls back to balanced, so an experiment can
    # never request a treatment the pipeline cannot produce.
    #
    # This factor sets INTENT, not outcome. Whether a given style is good for a
    # channel is precisely what the visual features are there to discover; the
    # registry deliberately expresses no preference among them.
    "visual_style": SafeFactorSpec(
        feature_name="visual_style",
        value_type="categorical",
        safe_values=list(VISUAL_STYLE_SAFE_VALUES),
        autonomy=FactorAutonomy.AUTONOMOUSLY_ASSIGNABLE_NOW,
        actual_value_source="visual_style",
        control_capability=ControlCapability.ENFORCED,
        tolerance_abs=None,
    ),
}


@dataclass
class CandidateScoreComponents:
    """Decomposed score for one experiment candidate.

    All components are "higher = better" (direction-normalized).
    exploitation_value and exploration_value are SEPARATE — not averaged together.

    Confidence is NOT re-applied here. The opportunity_attractiveness is taken
    directly from OpportunityScore.composite_score, which already incorporates
    confidence through the Phase 13F scoring pipeline.
    """

    opportunity_attractiveness: float | None = None
    exploitation_value: float | None = None
    exploration_value: float | None = None
    information_gain: float | None = None
    internal_evidence_strength: float | None = None
    uncertainty: float | None = None
    cluster_coverage_need: float | None = None
    production_feasibility: float | None = None
    final_planning_score: float = 0.0


@dataclass
class IntendedFactor:
    """A factor proposed for an experiment (treatment or controlled).

    intended_value is None — the operator decides the actual value.
    Autonomous value assignment is explicitly prohibited.
    """

    factor_name: str
    factor_role: str  # "treatment" | "controlled"
    intended_value: str | None = None


@dataclass
class ExperimentCandidate:
    """A scored experiment candidate derived from one eligible Opportunity."""

    opportunity_id: int
    channel_id: int
    canonical_cluster_id: int | None
    eligibility_classification: str  # "general_eligible" | "exploration_only"
    planning_intent: PlanningIntent
    experiment_type: str  # "exploration" | "exploitation"
    primary_target_metric: str
    primary_metric_direction: str
    hypothesis_sketch: str
    intended_treatment_factors: list[IntendedFactor]
    controlled_factors: list[IntendedFactor]
    feature_change_risk: str  # "low" | "medium" | "high" | "unknown"
    score: CandidateScoreComponents
    semantic_fit_disposition: str | None = None


@dataclass
class ExperimentSelectionDecision:
    """Selection or deferral decision for one candidate within a portfolio plan."""

    opportunity_id: int
    selected: bool
    rank_in_pool: int | None
    pool_type: str | None  # "exploration" | "exploitation" | None
    selection_reason: str
    deferral_reason: str | None
    is_validation_repeat: bool
    candidate: ExperimentCandidate


@dataclass
class ExperimentPortfolioPlan:
    """The output of one planning run.

    Contains both selected candidates (the portfolio) and deferred candidates
    (those assessed but not chosen this cycle).

    is_dry_run=True plans are NOT persisted to the DB. Use dry_run for previewing
    a plan without committing it.
    """

    channel_id: int
    run_id: str
    planned_at: str
    is_dry_run: bool
    eligible_count: int
    exploration_only_count: int
    general_eligible_count: int
    selected_count: int
    deferred_count: int
    decisions: list[ExperimentSelectionDecision]
    policy_snapshot_json: str
    input_hash: str


@dataclass
class PlanningPolicy:
    """Versioned V1 planning policy.

    All weights and thresholds are stored here so every plan carries a complete
    policy record. Changes to defaults do not retroactively alter past plans.

    Weight invariants (enforced in tests):
      w_exploitation_* must sum to 1.0
      w_exploration_*  must sum to 1.0
      w_ig_*           must sum to 1.0
    """

    version: str = "1.0.0"

    # Portfolio slot sizes
    max_exploration_slots: int = 2
    max_exploitation_slots: int = 1

    # Cluster diversity guards
    max_cluster_share: float = 0.40  # max fraction of total plan from one cluster
    max_consecutive_same_cluster: int = 2  # at most N in a row from same cluster

    # Exploitation value weights (must sum to 1.0)
    w_exploitation_attractiveness: float = 0.50
    w_exploitation_evidence: float = 0.30
    w_exploitation_feasibility: float = 0.20

    # Exploration value weights (must sum to 1.0)
    w_exploration_information_gain: float = 0.50
    w_exploration_uncertainty: float = 0.30
    w_exploration_cluster_coverage: float = 0.20

    # Information gain sub-component weights (must sum to 1.0)
    w_ig_cluster_newness: float = 0.40
    w_ig_maturity_gap: float = 0.35
    w_ig_feature_coverage: float = 0.25

    # Signal maturity → internal evidence strength mapping
    maturity_strength: dict[str, float] = field(
        default_factory=lambda: {
            "insufficient": 0.0,
            "exploratory": 0.33,
            "directional": 0.67,
            "actionable": 1.0,
        }
    )

    # Primary target metric for all experiment candidates
    primary_target_metric: str = "average_view_percentage"
    primary_metric_direction: str = "higher_is_better"

    # Maximum number of treatment factors per experiment candidate
    max_treatment_factors: int = 1

    def to_json(self) -> str:
        return json.dumps(
            {
                "version": self.version,
                "max_exploration_slots": self.max_exploration_slots,
                "max_exploitation_slots": self.max_exploitation_slots,
                "max_cluster_share": self.max_cluster_share,
                "max_consecutive_same_cluster": self.max_consecutive_same_cluster,
                "w_exploitation_attractiveness": self.w_exploitation_attractiveness,
                "w_exploitation_evidence": self.w_exploitation_evidence,
                "w_exploitation_feasibility": self.w_exploitation_feasibility,
                "w_exploration_information_gain": self.w_exploration_information_gain,
                "w_exploration_uncertainty": self.w_exploration_uncertainty,
                "w_exploration_cluster_coverage": self.w_exploration_cluster_coverage,
                "w_ig_cluster_newness": self.w_ig_cluster_newness,
                "w_ig_maturity_gap": self.w_ig_maturity_gap,
                "w_ig_feature_coverage": self.w_ig_feature_coverage,
                "maturity_strength": self.maturity_strength,
                "primary_target_metric": self.primary_target_metric,
                "primary_metric_direction": self.primary_metric_direction,
                "max_treatment_factors": self.max_treatment_factors,
            },
            sort_keys=True,
        )

    @classmethod
    def v1(cls) -> PlanningPolicy:
        return cls()
