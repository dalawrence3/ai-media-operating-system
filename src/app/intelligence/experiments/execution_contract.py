"""Phase 14F — Experiment Execution Contract models.

An ExperimentExecutionContract is the handoff from an approved strategy brief
to the production pipeline.  It captures, at the moment of production initiation:

  WHAT we agreed to test     (treatment factors, intended values)
  WHAT we agreed to hold     (controlled factors, baselines)
  HOW we will enforce it     (control_capability per factor)
  WHETHER it was honoured    (fidelity, post-production)

Design invariants:
  - No LLM calls
  - No YouTube calls
  - No score changes / re-ranking
  - intended values come from experiment_factors.intended_value (operator-set)
  - actual values come from content_feature_snapshots (post-production)
  - valid_for_learning is None until fidelity has been evaluated
  - A dry_run contract is persisted (for audit) but never injected into production
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ExecutionMode(StrEnum):
    DRY_RUN = "dry_run"
    REAL = "real"


class ParameterAuthority(StrEnum):
    """Who governs an individual production parameter for this execution.

    Precedence (high → low):
      EXPERIMENT_TREATMENT > EXPERIMENT_CONTROL > LEARNING_APPLICATION > PRODUCTION_DEFAULT

    The Experiment governs a parameter only when that parameter is explicitly
    declared as a TREATMENT or CONTROL factor.  Parameters not mentioned by the
    Experiment remain under normal Learning Application / production-default policy.
    """

    # Experiment explicitly tests this parameter at an intended value.
    EXPERIMENT_TREATMENT = "experiment_treatment"
    # Experiment explicitly holds this parameter at a baseline (control).
    EXPERIMENT_CONTROL = "experiment_control"
    # Phase 12A learning application governs; experiment does not mention this parameter.
    LEARNING_APPLICATION = "learning_application"
    # Voice-profile or pipeline default; no learning application active.
    PRODUCTION_DEFAULT = "production_default"
    # Experiment has a contract but does not declare authority over this parameter.
    EXPERIMENT_NOT_GOVERNING = "experiment_not_governing"


class FidelityClassification(StrEnum):
    """Overall execution-fidelity verdict for an experiment run.

    Richer than the bare valid_for_learning boolean.  Phase 14G uses this to
    distinguish "not ready yet" from "confirmed failure" from "passes with caveats".

    Precedence (classification logic in execution_service.compare_intended_vs_actual):
      NOT_YET_ASSESSABLE > NOT_VALID (treatment) > UNRESOLVED > NOT_VALID (hard-control)
      > UNRESOLVED (hard-control) > VALID_WITH_WARNINGS > VALID

    valid_for_learning mapping:
      VALID / VALID_WITH_WARNINGS → True
      NOT_VALID                   → False
      NOT_YET_ASSESSABLE / UNRESOLVED → None
    """

    # All required treatment and enforced-control dimensions pass.
    VALID = "valid"
    # Treatment passes; soft-control drift or other non-fatal caveats noted.
    VALID_WITH_WARNINGS = "valid_with_warnings"
    # Treatment deviated OR enforced control materially drifted.
    NOT_VALID = "not_valid"
    # Production not yet complete; required actual values are unavailable.
    NOT_YET_ASSESSABLE = "not_yet_assessable"
    # A required dimension exists but cannot be confirmed — evaluator absent/failed,
    # treatment factor not observable, or market semantic check inconclusive.
    UNRESOLVED = "unresolved"


class FidelityOutcome(StrEnum):
    """Result of comparing an intended factor value to the actual produced value."""

    # actual == intended (within tolerance_abs)
    MATCHED = "matched"
    # |actual - intended| <= tolerance_abs (numeric factors only)
    WITHIN_TOLERANCE = "within_tolerance"
    # |actual - intended| > tolerance_abs, or string mismatch
    DEVIATED = "deviated"
    # no column or extraction path for this factor in the current schema
    NOT_OBSERVABLE = "not_observable"
    # extraction path exists but production has not yet completed
    NOT_YET_AVAILABLE = "not_yet_available"


@dataclass(frozen=True)
class TreatmentConfig:
    """The committed treatment specification for one factor."""

    factor_name: str
    value_type: str
    # Operator-set value; None means operator has not yet assigned a value
    intended_value: str | None
    safe_range_min: float | None
    safe_range_max: float | None
    safe_values: tuple[str, ...] | None
    # Safety validation results (computed at contract creation)
    abs_valid: bool = True  # passes absolute bounds check
    delta_valid: bool = True  # passes max_delta check against baseline
    # Baseline used for delta check (from controlled_factors)
    delta_baseline: str | None = None


@dataclass(frozen=True)
class ControlConfig:
    """The committed baseline specification for one controlled factor."""

    factor_name: str
    baseline_value: str | None
    baseline_source: str
    # Whether the pipeline can deterministically enforce this baseline
    control_capability: str  # "enforced" | "soft" | "not_controllable"
    tolerance: str | None


@dataclass(frozen=True)
class FactorFidelity:
    """Post-production comparison for one factor."""

    factor_name: str
    intended_value: str | None
    actual_value: str | None
    outcome: FidelityOutcome
    reason: str


@dataclass
class ExecutionFidelity:
    """Aggregated fidelity result after production is complete.

    Phase 14F.2 adds classification (richer than the bare boolean) and
    fidelity_policy_version so Phase 14G can tell which rules were applied.

    valid_for_learning is derived from classification:
      VALID / VALID_WITH_WARNINGS → True
      NOT_VALID                   → False
      NOT_YET_ASSESSABLE / UNRESOLVED → None
    """

    treatment_outcomes: list[FactorFidelity] = field(default_factory=list)
    control_outcomes: list[FactorFidelity] = field(default_factory=list)
    valid_for_learning: bool | None = None
    confounding_risk_realized: str = "low"
    reasons: list[str] = field(default_factory=list)
    # Phase 14F.2 fields — default to None/"" for backwards compat with old JSON blobs
    classification: FidelityClassification | None = None
    fidelity_policy_version: str = ""


@dataclass
class ExperimentExecutionContract:
    """The authoritative handoff from strategy to production.

    Created by execution_service.create_execution_contract().
    Idempotent: one contract per experiment (UNIQUE on experiment_id).

    Status lifecycle:
      pending   — contract created, awaiting approval / real execution
      approved  — operator approved; real execution may proceed
      executing — production stages are in progress
      completed — production finished (fidelity available)
      failed    — production failed; experiment lifecycle preserved
      blocked   — cannot execute (eligibility, lineage, or safety failure)
    """

    id: str
    experiment_id: str
    brief_id: str
    idea_id: int | None
    channel_id: int
    opportunity_id: int
    canonical_cluster_id: int | None

    execution_mode: ExecutionMode

    # Pre-execution eligibility recheck
    eligibility_recheck_result: str | None
    eligibility_blocked: bool

    # Committed treatment and control specifications
    treatment_configs: list[TreatmentConfig]
    control_configs: list[ControlConfig]

    # Computed narration override (None = no speaking_rate treatment)
    narration_speaking_rate_override: float | None

    # Aggregate safety flags
    treatment_delta_valid: bool
    treatment_abs_valid: bool

    status: str
    execution_policy_version: str

    # Populated post-production by compare_intended_vs_actual()
    fidelity: ExecutionFidelity | None
    valid_for_learning: bool | None

    created_at: str | None = None
    approved_at: str | None = None
    executed_at: str | None = None
    completed_at: str | None = None
