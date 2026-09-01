"""Domain models for Phase 14A production experiment ledger."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum


class ExperimentType(StrEnum):
    exploration = "exploration"
    exploitation = "exploitation"


class ExperimentStatus(StrEnum):
    draft = "draft"
    planned = "planned"
    in_production = "in_production"
    published = "published"
    observing = "observing"
    mature = "mature"
    analyzed = "analyzed"
    completed = "completed"
    cancelled = "cancelled"


# Valid forward transitions in the experiment lifecycle.
ALLOWED_TRANSITIONS: dict[ExperimentStatus, set[ExperimentStatus]] = {
    ExperimentStatus.draft: {ExperimentStatus.planned, ExperimentStatus.cancelled},
    ExperimentStatus.planned: {ExperimentStatus.in_production, ExperimentStatus.cancelled},
    ExperimentStatus.in_production: {ExperimentStatus.published, ExperimentStatus.cancelled},
    ExperimentStatus.published: {ExperimentStatus.observing, ExperimentStatus.cancelled},
    ExperimentStatus.observing: {ExperimentStatus.mature, ExperimentStatus.cancelled},
    ExperimentStatus.mature: {ExperimentStatus.analyzed, ExperimentStatus.cancelled},
    ExperimentStatus.analyzed: {ExperimentStatus.completed, ExperimentStatus.cancelled},
    ExperimentStatus.completed: set(),
    ExperimentStatus.cancelled: set(),
}

# Timestamp column to set on each forward transition.
_TRANSITION_TIMESTAMP: dict[ExperimentStatus, str] = {
    ExperimentStatus.planned: "planned_at",
    ExperimentStatus.in_production: "in_production_at",
    ExperimentStatus.published: "published_at",
    ExperimentStatus.observing: "observing_at",
    ExperimentStatus.mature: "matured_at",
    ExperimentStatus.analyzed: "analyzed_at",
    ExperimentStatus.completed: "completed_at",
    ExperimentStatus.cancelled: "cancelled_at",
}


class MetricDirection(StrEnum):
    higher_is_better = "higher_is_better"
    lower_is_better = "lower_is_better"
    target_range = "target_range"
    informational_only = "informational_only"


class FactorRole(StrEnum):
    treatment = "treatment"
    controlled = "controlled"
    observed = "observed"


@dataclass
class MaturityPolicy:
    minimum_age_hours: int = 48
    minimum_views: int = 10
    observation_window_hours: int = 168
    required_metrics: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(
            {
                "minimum_age_hours": self.minimum_age_hours,
                "minimum_views": self.minimum_views,
                "observation_window_hours": self.observation_window_hours,
                "required_metrics": self.required_metrics,
            }
        )

    @classmethod
    def from_json(cls, raw: str) -> MaturityPolicy:
        d = json.loads(raw)
        return cls(
            minimum_age_hours=d.get("minimum_age_hours", 48),
            minimum_views=d.get("minimum_views", 10),
            observation_window_hours=d.get("observation_window_hours", 168),
            required_metrics=d.get("required_metrics", []),
        )

    @classmethod
    def default(cls) -> MaturityPolicy:
        return cls()


@dataclass
class ExperimentMetricTarget:
    id: int | None
    experiment_id: str
    metric_name: str
    direction: MetricDirection
    target_value: float | None
    target_min: float | None
    target_max: float | None
    is_primary: bool


@dataclass
class ExperimentFactor:
    id: int | None
    experiment_id: str
    factor_name: str
    factor_role: FactorRole
    intended_value: str | None
    actual_value: str | None
    value_type: str


@dataclass
class ExperimentStateEvent:
    id: int | None
    experiment_id: str
    from_state: str | None
    to_state: str
    actor: str
    reason: str
    created_at: str


@dataclass
class Experiment:
    id: str
    channel_id: int
    experiment_type: ExperimentType
    status: ExperimentStatus
    hypothesis: str
    hypothesis_null: str
    maturity_policy_json: str
    policy_snapshot_json: str
    created_at: str
    updated_at: str
    opportunity_id: int | None = None
    hypothesis_metric: str | None = None
    input_hash: str | None = None
    planned_at: str | None = None
    in_production_at: str | None = None
    published_at: str | None = None
    observing_at: str | None = None
    matured_at: str | None = None
    analyzed_at: str | None = None
    completed_at: str | None = None
    cancelled_at: str | None = None
    cancelled_reason: str | None = None
    publication_id: int | None = None
    metric_targets: list[ExperimentMetricTarget] = field(default_factory=list)
    factors: list[ExperimentFactor] = field(default_factory=list)

    @property
    def maturity_policy(self) -> MaturityPolicy:
        return MaturityPolicy.from_json(self.maturity_policy_json)
