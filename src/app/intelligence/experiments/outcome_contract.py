"""Phase 14G — Experiment outcome evaluation models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class OutcomeReadiness(StrEnum):
    NOT_READY = "not_ready"
    INVALID_EXECUTION = "invalid_execution"
    INSUFFICIENT_ANALYTICS = "insufficient_analytics"
    EVALUABLE_PROVISIONAL = "evaluable_provisional"
    EVALUABLE_MATURE = "evaluable_mature"
    UNRESOLVED = "unresolved"


class OutcomeClassification(StrEnum):
    POSITIVE_OBSERVATION = "positive_observation"
    NEGATIVE_OBSERVATION = "negative_observation"
    NEUTRAL_OBSERVATION = "neutral_observation"
    INCONCLUSIVE = "inconclusive"
    INFORMATIONAL_ONLY = "informational_only"
    BASELINE_UNAVAILABLE = "baseline_unavailable"


class EvidenceMaturity(StrEnum):
    EXPLORATORY = "exploratory"
    DIRECTIONAL = "directional"
    ACTIONABLE = "actionable"


class BaselineSourceType(StrEnum):
    CHANNEL_BASELINE = "channel_baseline"
    PRIOR_EXPERIMENT = "prior_experiment"
    VALIDATION_REFERENCE = "validation_reference"
    CONTROL_PUBLICATION = "control_publication"
    NONE = "none"


OUTCOME_POLICY_VERSION = "1.0.0"


@dataclass
class ExperimentOutcomeEvaluation:
    id: str
    experiment_id: str
    readiness: OutcomeReadiness
    classification: OutcomeClassification | None
    evidence_maturity: EvidenceMaturity | None
    baseline_source_type: BaselineSourceType
    baseline_experiment_id: str | None
    target_metric_name: str | None
    target_metric_direction: str | None
    treatment_metric_value: float | None
    baseline_metric_value: float | None
    absolute_delta: float | None
    relative_delta: float | None
    is_mature: bool
    publication_age_hours: float | None
    observed_views: float | None
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    outcome_policy_version: str = OUTCOME_POLICY_VERSION
    input_hash: str = ""
    evaluated_at: str = ""
