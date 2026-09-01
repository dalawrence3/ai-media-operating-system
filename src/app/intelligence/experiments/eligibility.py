"""Phase 14C — Experiment eligibility gate models and policy.

Deterministic classification of whether an Opportunity is currently safe
and appropriate to enter autonomous experiment planning for a channel.

This module answers: "Is this Opportunity eligible?"
It does NOT answer: "Which eligible Opportunity should we make next?"

Classification tiers (highest-severity wins when multiple apply):
  INELIGIBLE       — hard block; must not enter planning
  UNRESOLVED       — soft block; semantic fit LLM call failed or ambiguous
  REQUIRES_REFRESH — market knowledge too old; eligible after signal refresh
  EXPLORATION_ONLY — partially eligible; only exploration experiments allowed
  GENERAL_ELIGIBLE — fully eligible for any experiment type
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum


class ExperimentEligibilityClassification(StrEnum):
    INELIGIBLE = "ineligible"
    UNRESOLVED = "unresolved"
    REQUIRES_REFRESH = "requires_refresh"
    EXPLORATION_ONLY = "exploration_only"
    GENERAL_ELIGIBLE = "general_eligible"


class MarketFreshnessClass(StrEnum):
    FRESH = "fresh"
    AGING = "aging"
    STALE = "stale"


@dataclass(frozen=True)
class EligibilityFinding:
    code: str
    severity: str  # "block", "warn", "info"
    message: str
    detail: str | None = None


@dataclass
class EligibilityPolicy:
    """Versioned V1 eligibility policy snapshot.

    All thresholds are stored here so every assessment carries a complete
    policy record — policy changes don't retroactively alter past assessments.

    Market knowledge age is measured from MarketInterpretationRun.completed_at,
    NOT from Opportunity.updated_at.  These are distinct concepts.
    """

    version: str = "1.0.0"

    # Market freshness thresholds (hours since MarketInterpretationRun.completed_at)
    market_fresh_max_age_hours: int = 168  # < 7 days  → FRESH
    market_aging_max_age_hours: int = 720  # < 30 days → AGING; ≥ 30 days → STALE

    # Signal maturity requirements
    min_signal_maturity_for_general: str = "directional"
    min_signal_maturity_for_exploration: str = "exploratory"

    # Confidence requirement for GENERAL_ELIGIBLE
    min_confidence_for_general: float = 0.30

    # Semantic audience fit (LLM-based, distinct from Phase 13F lexical fit)
    semantic_fit_min_score: float = 0.55
    semantic_fit_provider_timeout_s: int = 30
    semantic_fit_prompt_version: str = "2"

    # Phase 18E — topic specificity / visual groundability.
    #
    # A separate axis from semantic fit, and the reason this exists: "history
    # and society" scored 0.8 strong_fit for a curious-audience channel and was
    # produced as a video. The fit judgement was correct. It was answering a
    # different question from "is this a topic at all, or a category?"
    #
    # 0.5 is the boundary between narrow_theme and broad_category in the prompt
    # rubric: below it the string names a domain rather than a subject.
    # Groundability is set lower (0.4) deliberately — an abstract-but-specific
    # topic is a legitimate editorial choice and the visual-quality floor at
    # render time is the backstop; this gate is aimed at the case where there
    # is nothing specific to show at all.
    min_topic_specificity: float = 0.5
    min_visual_groundability: float = 0.4

    # Analytics readiness: minimum views for learning (MIN_VIEWS_FOR_LEARNING)
    min_views_for_analytics_readiness: int = 10

    # Active conflict: statuses that block a new experiment on the same opportunity
    conflict_blocking_statuses: list[str] = field(
        default_factory=lambda: [
            "draft",
            "planned",
            "in_production",
            "published",
            "observing",
            "mature",
            "analyzed",
        ]
    )

    # Production feasibility: narration speaking rate bounds
    narration_rate_min: float = 0.7
    narration_rate_max: float = 1.5

    def to_json(self) -> str:
        return json.dumps(
            {
                "version": self.version,
                "market_fresh_max_age_hours": self.market_fresh_max_age_hours,
                "market_aging_max_age_hours": self.market_aging_max_age_hours,
                "min_signal_maturity_for_general": self.min_signal_maturity_for_general,
                "min_signal_maturity_for_exploration": self.min_signal_maturity_for_exploration,
                "min_confidence_for_general": self.min_confidence_for_general,
                "semantic_fit_min_score": self.semantic_fit_min_score,
                "semantic_fit_provider_timeout_s": self.semantic_fit_provider_timeout_s,
                "semantic_fit_prompt_version": self.semantic_fit_prompt_version,
                "min_views_for_analytics_readiness": self.min_views_for_analytics_readiness,
                "conflict_blocking_statuses": self.conflict_blocking_statuses,
                "narration_rate_min": self.narration_rate_min,
                "narration_rate_max": self.narration_rate_max,
            }
        )

    @classmethod
    def v1(cls) -> EligibilityPolicy:
        return cls()


@dataclass
class ExperimentEligibilityAssessment:
    """Complete typed eligibility assessment for one Opportunity.

    Every field has a stable meaning across calls:
    - classification: the rolled-up verdict (INELIGIBLE wins over everything)
    - findings: ordered list of issues found; severity=block causes INELIGIBLE
    - policy_snapshot_json: the policy in effect at assessment time
    - market_knowledge_age_hours: computed from MarketInterpretationRun.completed_at
    """

    opportunity_id: int
    channel_id: int
    classification: ExperimentEligibilityClassification
    findings: list[EligibilityFinding]
    policy_snapshot_json: str  # serialized EligibilityPolicy at assessment time
    assessed_at: str  # ISO timestamp

    # Sub-assessment results (None = not evaluated or not applicable)
    market_freshness_class: MarketFreshnessClass | None = None
    market_knowledge_age_hours: float | None = None
    signal_maturity: str | None = None
    signal_confidence: float | None = None
    semantic_fit_score: float | None = None
    semantic_fit_label: str | None = None
    has_active_conflict: bool = False
    active_conflict_experiment_id: str | None = None
    phase12c_publication_count: int = 0
    analytics_ready: bool = False
    # "provider_called" | "deterministic_bypass" | "provider_unavailable_unresolved"
    # | "skipped_hard_block"
    semantic_fit_disposition: str | None = None

    def is_eligible(self) -> bool:
        return self.classification == ExperimentEligibilityClassification.GENERAL_ELIGIBLE

    def blocks(self) -> list[EligibilityFinding]:
        return [f for f in self.findings if f.severity == "block"]

    def warnings(self) -> list[EligibilityFinding]:
        return [f for f in self.findings if f.severity == "warn"]


@dataclass
class OpportunityEligibilityBatchItem:
    """Per-opportunity result within a batch assessment."""

    opportunity_id: int
    classification: ExperimentEligibilityClassification
    fit_score: float | None
    fit_label: str | None
    has_exclusion: bool  # excluded_topic_match block present
    is_unresolved: bool  # classification == UNRESOLVED
    assessment: ExperimentEligibilityAssessment


@dataclass
class OpportunityEligibilityBatchResult:
    """Aggregate result for assess_opportunity_batch().

    Batch is bounded: at most max_batch_size opportunities are assessed.
    Items are ordered by the input opportunity_ids list (bounded prefix).
    No ranking, no selection — the caller decides what to do with results.
    """

    channel_id: int
    assessed_at: str
    total: int  # number actually assessed (≤ max_batch_size)
    by_classification: dict[str, int]  # classification.value → count
    items: list[OpportunityEligibilityBatchItem]


@dataclass
class SemanticFitResolutionResult:
    """Result of resolve_unresolved_opportunities_for_channel() (Phase 17G).

    considered:      opportunities inspected (bounded by max_considered)
    unresolved_found: of those, how many were classified UNRESOLVED going in
    evaluated:        how many actually triggered a real LLM call (bounded by
                       max_evaluations — this is the number that spent quota)
    cache_hits:       resolved for free by reusing a persisted prior result
    eligible:         final classification in (general_eligible, exploration_only)
    ineligible:       final classification == ineligible
    still_unresolved: LLM call failed, or bounded out before evaluation
    """

    channel_id: int
    resolved_at: str
    considered: int
    unresolved_found: int
    evaluated: int
    cache_hits: int
    eligible: int
    ineligible: int
    still_unresolved: int
    opportunity_ids_evaluated: list[int] = field(default_factory=list)
