"""Phase 14F — Experiment Execution Service.

Entry point: create_execution_contract()

Contracts:
  - No YouTube calls
  - No LLM calls
  - No content generation
  - No score changes / re-ranking
  - No autonomous value assignment (reads operator-set intended_value only)
  - No live production triggers (use existing pipeline infrastructure)
  - Read from: experiments, experiment_strategy_briefs, experiment_factors,
               experiment_candidate_scores, content_feature_snapshots, production_plans
  - Write to: experiment_execution_contracts

Eligibility recheck reads the stored classification from experiment_candidate_scores
(same approach as Phase 14E; no live LLM call).

Safety bounds (NARRATION_PACE_*) come exclusively from learning.constants.
Phase 14F adds no duplicate constants.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from app.intelligence.experiments.execution_contract import (
    ControlConfig,
    ExecutionFidelity,
    ExecutionMode,
    ExperimentExecutionContract,
    FactorFidelity,
    FidelityClassification,
    FidelityOutcome,
    ParameterAuthority,
    TreatmentConfig,
)
from app.intelligence.experiments.planning import (
    SAFE_CONTROLLABLE_FACTORS,
    ControlCapability,
)
from app.intelligence.experiments.strategy_brief import BriefPlanningIntent
from app.learning.constants import (
    NARRATION_PACE_MAX_DELTA,
    NARRATION_PACE_SPEAKING_RATE_MAX,  # noqa: F401 — re-exported; see test_AU_*
    NARRATION_PACE_SPEAKING_RATE_MIN,  # noqa: F401 — re-exported; see test_AU_*
)

EXECUTION_POLICY_VERSION = "1.0.0"
# Phase 14F.2: fidelity policy version tracks which validity rules were applied.
FIDELITY_POLICY_VERSION = "1.1.0"
# Synthetic factor name used in treatment_outcomes for market-exploration experiments.
MARKET_THEME_FACTOR_NAME = "market_canonical_cluster"

# Eligibility states that block execution (stricter than brief creation).
# REQUIRES_REFRESH is added here because production can be days after planning.
_BLOCKED_FOR_EXECUTION = frozenset(
    {
        "ineligible",
        "stale",
        "unresolved",
        "requires_refresh",
    }
)

# Speaking_rate fidelity tolerance (floating-point rounding; provider uses value exactly)
_SPEAKING_RATE_FIDELITY_TOL = 0.001


class ExecutionContractError(Exception):
    """Raised when an execution contract cannot be created or validated."""


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


# ── Eligibility recheck ────────────────────────────────────────────────────────


def _recheck_eligibility(conn: sqlite3.Connection, opportunity_id: int) -> str | None:
    """Return the most recent stored eligibility_classification for this opportunity.

    Uses the stored classification from experiment_candidate_scores.
    No live LLM or YouTube call is made.  Returns None when no score exists.
    """
    row = conn.execute(
        """
        SELECT eligibility_classification
        FROM experiment_candidate_scores
        WHERE opportunity_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (opportunity_id,),
    ).fetchone()
    return row["eligibility_classification"] if row else None


# ── Lineage validation ─────────────────────────────────────────────────────────


class _LineageError(ExecutionContractError):
    """Raised when experiment / brief / channel / opportunity do not agree."""


def _validate_lineage(
    experiment_row: sqlite3.Row,
    brief_row: sqlite3.Row,
) -> None:
    """Ensure experiment and brief share channel, opportunity, and cluster."""
    if experiment_row["channel_id"] != brief_row["channel_id"]:
        raise _LineageError(
            f"Channel mismatch: experiment has channel_id={experiment_row['channel_id']} "
            f"but brief has channel_id={brief_row['channel_id']}."
        )

    exp_opp = experiment_row["opportunity_id"]
    brief_opp = brief_row["opportunity_id"]
    if exp_opp is not None and brief_opp is not None and exp_opp != brief_opp:
        raise _LineageError(
            f"Opportunity mismatch: experiment has opportunity_id={exp_opp} "
            f"but brief has opportunity_id={brief_opp}."
        )

    # experiments table has no canonical_cluster_id column — skip cluster check at experiment level
    # (cluster is tracked on the opportunity and enforced by brief lineage)


# ── Treatment / safety validation ─────────────────────────────────────────────


def _validate_treatment_value(
    factor_name: str,
    intended_str: str,
    baseline_str: str | None,
) -> tuple[bool, bool]:
    """Validate absolute bounds and max_delta for a numeric treatment factor.

    Returns (abs_valid, delta_valid).
    Only narration_speaking_rate has absolute+delta bounds enforced here.
    Non-numeric factors return (True, True) — categorical safety is not range-based.
    """
    spec = SAFE_CONTROLLABLE_FACTORS.get(factor_name)
    if spec is None or spec.value_type != "numeric":
        return True, True

    try:
        intended = float(intended_str)
    except (TypeError, ValueError):
        return False, False

    abs_valid = True
    if spec.safe_range_min is not None and intended < spec.safe_range_min:
        abs_valid = False
    if spec.safe_range_max is not None and intended > spec.safe_range_max:
        abs_valid = False

    delta_valid = True
    if baseline_str is not None:
        try:
            baseline = float(baseline_str)
            if abs(intended - baseline) > NARRATION_PACE_MAX_DELTA:
                delta_valid = False
        except (TypeError, ValueError):
            delta_valid = False

    return abs_valid, delta_valid


def _build_treatment_configs(
    conn: sqlite3.Connection,
    experiment_id: str,
    brief_treatment_raw: list[dict],
    controlled_baseline_map: dict[str, str | None],
) -> tuple[list[TreatmentConfig], bool, bool]:
    """Build TreatmentConfig list from experiment_factors.

    Reads operator-set intended_value from experiment_factors (Phase 14A table).
    Returns (configs, all_abs_valid, all_delta_valid).
    """
    factor_rows = {
        r["factor_name"]: r
        for r in conn.execute(
            "SELECT factor_name, intended_value, value_type FROM experiment_factors "
            "WHERE experiment_id = ? AND factor_role = 'treatment'",
            (experiment_id,),
        ).fetchall()
    }

    configs: list[TreatmentConfig] = []
    all_abs_valid = True
    all_delta_valid = True

    for tf in brief_treatment_raw:
        fname = tf["factor_name"]
        spec = SAFE_CONTROLLABLE_FACTORS.get(fname)
        factor_row = factor_rows.get(fname)
        intended_value: str | None = factor_row["intended_value"] if factor_row else None

        baseline_str = controlled_baseline_map.get(fname)

        abs_valid = True
        delta_valid = True
        if intended_value is not None:
            abs_valid, delta_valid = _validate_treatment_value(fname, intended_value, baseline_str)

        if not abs_valid:
            all_abs_valid = False
        if not delta_valid:
            all_delta_valid = False

        configs.append(
            TreatmentConfig(
                factor_name=fname,
                value_type=tf.get("value_type", spec.value_type if spec else "unknown"),
                intended_value=intended_value,
                safe_range_min=spec.safe_range_min if spec else None,
                safe_range_max=spec.safe_range_max if spec else None,
                safe_values=tuple(spec.safe_values) if spec and spec.safe_values else None,
                abs_valid=abs_valid,
                delta_valid=delta_valid,
                delta_baseline=baseline_str,
            )
        )

    return configs, all_abs_valid, all_delta_valid


def _build_control_configs(
    controlled_raw: list[dict],
) -> list[ControlConfig]:
    """Build ControlConfig list from brief's controlled_factors."""
    configs: list[ControlConfig] = []
    for cf in controlled_raw:
        fname = cf["factor_name"]
        spec = SAFE_CONTROLLABLE_FACTORS.get(fname)
        cap = spec.control_capability.value if spec else ControlCapability.NOT_CONTROLLABLE.value
        configs.append(
            ControlConfig(
                factor_name=fname,
                baseline_value=cf.get("baseline_value"),
                baseline_source=cf.get("baseline_source", "unknown"),
                control_capability=cap,
                tolerance=cf.get("tolerance"),
            )
        )
    return configs


def _narration_speaking_rate_override(
    treatment_configs: list[TreatmentConfig],
) -> float | None:
    """Extract the speaking_rate override from treatment configs, if present."""
    for tc in treatment_configs:
        if tc.factor_name == "narration_speaking_rate" and tc.intended_value is not None:
            try:
                return float(tc.intended_value)
            except (TypeError, ValueError):
                return None
    return None


# ── Persistence ────────────────────────────────────────────────────────────────


def _row_to_contract(row: sqlite3.Row) -> ExperimentExecutionContract:
    treatment_raw = json.loads(row["treatment_factors_json"] or "[]")
    treatment_configs = [
        TreatmentConfig(
            factor_name=t["factor_name"],
            value_type=t["value_type"],
            intended_value=t.get("intended_value"),
            safe_range_min=t.get("safe_range_min"),
            safe_range_max=t.get("safe_range_max"),
            safe_values=tuple(t["safe_values"]) if t.get("safe_values") else None,
            abs_valid=bool(t.get("abs_valid", True)),
            delta_valid=bool(t.get("delta_valid", True)),
            delta_baseline=t.get("delta_baseline"),
        )
        for t in treatment_raw
    ]
    control_raw = json.loads(row["control_factors_json"] or "[]")
    control_configs = [
        ControlConfig(
            factor_name=c["factor_name"],
            baseline_value=c.get("baseline_value"),
            baseline_source=c.get("baseline_source", "unknown"),
            control_capability=c.get("control_capability", "not_controllable"),
            tolerance=c.get("tolerance"),
        )
        for c in control_raw
    ]
    fidelity: ExecutionFidelity | None = None
    if row["fidelity_json"]:
        raw_f = json.loads(row["fidelity_json"])
        raw_cls = raw_f.get("classification")
        classification = FidelityClassification(raw_cls) if raw_cls else None
        fidelity = ExecutionFidelity(
            treatment_outcomes=[
                FactorFidelity(
                    factor_name=f["factor_name"],
                    intended_value=f.get("intended_value"),
                    actual_value=f.get("actual_value"),
                    outcome=FidelityOutcome(f["outcome"]),
                    reason=f.get("reason", ""),
                )
                for f in raw_f.get("treatment_outcomes", [])
            ],
            control_outcomes=[
                FactorFidelity(
                    factor_name=f["factor_name"],
                    intended_value=f.get("intended_value"),
                    actual_value=f.get("actual_value"),
                    outcome=FidelityOutcome(f["outcome"]),
                    reason=f.get("reason", ""),
                )
                for f in raw_f.get("control_outcomes", [])
            ],
            valid_for_learning=raw_f.get("valid_for_learning"),
            confounding_risk_realized=raw_f.get("confounding_risk_realized", "low"),
            reasons=raw_f.get("reasons", []),
            classification=classification,
            fidelity_policy_version=raw_f.get("fidelity_policy_version", ""),
        )

    vfl_raw = row["valid_for_learning"]
    valid_for_learning: bool | None = None if vfl_raw is None else bool(vfl_raw)

    return ExperimentExecutionContract(
        id=row["id"],
        experiment_id=row["experiment_id"],
        brief_id=row["brief_id"],
        idea_id=row["idea_id"],
        channel_id=row["channel_id"],
        opportunity_id=row["opportunity_id"],
        canonical_cluster_id=row["canonical_cluster_id"],
        execution_mode=ExecutionMode(row["execution_mode"]),
        eligibility_recheck_result=row["eligibility_recheck_result"],
        eligibility_blocked=bool(row["eligibility_blocked"]),
        treatment_configs=treatment_configs,
        control_configs=control_configs,
        narration_speaking_rate_override=row["narration_speaking_rate_override"],
        treatment_delta_valid=bool(row["treatment_delta_valid"]),
        treatment_abs_valid=bool(row["treatment_abs_valid"]),
        status=row["status"],
        execution_policy_version=row["execution_policy_version"],
        fidelity=fidelity,
        valid_for_learning=valid_for_learning,
        created_at=row["created_at"],
        approved_at=row["approved_at"],
        executed_at=row["executed_at"],
        completed_at=row["completed_at"],
    )


# ── Public API ─────────────────────────────────────────────────────────────────


def create_execution_contract(
    conn: sqlite3.Connection,
    experiment_id: str,
    brief_id: str,
    *,
    mode: ExecutionMode = ExecutionMode.DRY_RUN,
    idea_id: int | None = None,
    policy_version: str = EXECUTION_POLICY_VERSION,
) -> ExperimentExecutionContract:
    """Create (or retrieve) the execution contract for an experiment.

    Idempotent: returns existing contract if one already exists for experiment_id.

    Args:
        conn: DB connection (caller commits).
        experiment_id: The experiments.id to execute.
        brief_id: The experiment_strategy_briefs.id that approved the strategy.
        mode: DRY_RUN (default) or REAL.
        idea_id: Optional experiment_idea_candidates.id for the approved idea.
        policy_version: Execution policy version string.

    Raises:
        ExecutionContractError: lineage mismatch, missing entities, or blocked
            eligibility that prevents any execution (including dry_run).
    """
    # --- Idempotency ---
    existing = conn.execute(
        "SELECT * FROM experiment_execution_contracts WHERE experiment_id = ?",
        (experiment_id,),
    ).fetchone()
    if existing is not None:
        return _row_to_contract(existing)

    # --- Load experiment ---
    exp_row = conn.execute(
        "SELECT id, channel_id, opportunity_id, status FROM experiments WHERE id = ?",
        (experiment_id,),
    ).fetchone()
    if exp_row is None:
        raise ExecutionContractError(f"Experiment {experiment_id!r} not found.")
    if exp_row["status"] in ("cancelled", "completed"):
        raise ExecutionContractError(
            f"Experiment {experiment_id!r} has terminal status {exp_row['status']!r}; "
            "cannot create execution contract."
        )

    # --- Load brief ---
    brief_row = conn.execute(
        "SELECT * FROM experiment_strategy_briefs WHERE id = ?",
        (brief_id,),
    ).fetchone()
    if brief_row is None:
        raise ExecutionContractError(f"Strategy brief {brief_id!r} not found.")
    if brief_row["status"] == "superseded":
        raise ExecutionContractError(
            f"Strategy brief {brief_id!r} is superseded; cannot execute against a stale brief."
        )

    # --- Lineage validation ---
    _validate_lineage(exp_row, brief_row)

    channel_id: int = exp_row["channel_id"]
    opportunity_id: int = brief_row["opportunity_id"]
    canonical_cluster_id: int | None = brief_row["canonical_cluster_id"]

    # --- Eligibility recheck ---
    elig_result = _recheck_eligibility(conn, opportunity_id)
    eligibility_blocked = elig_result is not None and elig_result in _BLOCKED_FOR_EXECUTION

    # --- Parse brief factors ---
    treatment_raw: list[dict] = json.loads(brief_row["treatment_factors_json"] or "[]")
    controlled_raw: list[dict] = json.loads(brief_row["controlled_factors_json"] or "[]")

    # Build baseline map for delta validation (factor_name → baseline_value_str)
    controlled_baseline_map: dict[str, str | None] = {
        cf["factor_name"]: cf.get("baseline_value") for cf in controlled_raw
    }

    # --- Build treatment configs (reads intended_value from experiment_factors) ---
    treatment_configs, all_abs_valid, all_delta_valid = _build_treatment_configs(
        conn, experiment_id, treatment_raw, controlled_baseline_map
    )

    # --- Build control configs ---
    control_configs = _build_control_configs(controlled_raw)

    # --- Narration speaking_rate override ---
    narration_override = _narration_speaking_rate_override(treatment_configs)

    # Block real execution if safety is violated; allow dry_run to show the problem
    contract_status = "pending"
    if eligibility_blocked:
        contract_status = "blocked"
    elif mode == ExecutionMode.REAL and (not all_abs_valid or not all_delta_valid):
        contract_status = "blocked"

    contract_id = str(uuid.uuid4())

    conn.execute(
        """
        INSERT INTO experiment_execution_contracts (
            id, experiment_id, brief_id, idea_id,
            channel_id, opportunity_id, canonical_cluster_id,
            execution_mode,
            eligibility_recheck_result, eligibility_blocked,
            treatment_factors_json, control_factors_json,
            narration_speaking_rate_override,
            treatment_delta_valid, treatment_abs_valid,
            status, execution_policy_version
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            contract_id,
            experiment_id,
            brief_id,
            idea_id,
            channel_id,
            opportunity_id,
            canonical_cluster_id,
            mode.value,
            elig_result,
            int(eligibility_blocked),
            json.dumps(
                [
                    {
                        "factor_name": tc.factor_name,
                        "value_type": tc.value_type,
                        "intended_value": tc.intended_value,
                        "safe_range_min": tc.safe_range_min,
                        "safe_range_max": tc.safe_range_max,
                        "safe_values": list(tc.safe_values) if tc.safe_values else None,
                        "abs_valid": tc.abs_valid,
                        "delta_valid": tc.delta_valid,
                        "delta_baseline": tc.delta_baseline,
                    }
                    for tc in treatment_configs
                ]
            ),
            json.dumps(
                [
                    {
                        "factor_name": cc.factor_name,
                        "baseline_value": cc.baseline_value,
                        "baseline_source": cc.baseline_source,
                        "control_capability": cc.control_capability,
                        "tolerance": cc.tolerance,
                    }
                    for cc in control_configs
                ]
            ),
            narration_override,
            int(all_delta_valid),
            int(all_abs_valid),
            contract_status,
            policy_version,
        ),
    )

    return ExperimentExecutionContract(
        id=contract_id,
        experiment_id=experiment_id,
        brief_id=brief_id,
        idea_id=idea_id,
        channel_id=channel_id,
        opportunity_id=opportunity_id,
        canonical_cluster_id=canonical_cluster_id,
        execution_mode=mode,
        eligibility_recheck_result=elig_result,
        eligibility_blocked=eligibility_blocked,
        treatment_configs=treatment_configs,
        control_configs=control_configs,
        narration_speaking_rate_override=narration_override,
        treatment_delta_valid=all_delta_valid,
        treatment_abs_valid=all_abs_valid,
        status=contract_status,
        execution_policy_version=policy_version,
        fidelity=None,
        valid_for_learning=None,
    )


def get_execution_contract(
    conn: sqlite3.Connection,
    contract_id: str,
) -> ExperimentExecutionContract | None:
    """Retrieve an execution contract by its primary ID."""
    row = conn.execute(
        "SELECT * FROM experiment_execution_contracts WHERE id = ?",
        (contract_id,),
    ).fetchone()
    return _row_to_contract(row) if row else None


def get_contract_for_experiment(
    conn: sqlite3.Connection,
    experiment_id: str,
) -> ExperimentExecutionContract | None:
    """Retrieve the execution contract for a given experiment, if any."""
    row = conn.execute(
        "SELECT * FROM experiment_execution_contracts WHERE experiment_id = ?",
        (experiment_id,),
    ).fetchone()
    return _row_to_contract(row) if row else None


def get_experiment_speaking_rate_override(
    conn: sqlite3.Connection,
    experiment_id: str,
) -> float | None:
    """Return the speaking_rate override from an approved real-mode execution contract.

    Called by NarrationExecutor to inject experiment treatment into the narration
    stage.  Returns None when:
      - no contract exists for this experiment
      - the contract is in dry_run mode
      - the contract is not in approved or executing status
      - the contract has no narration_speaking_rate treatment

    The Phase 12A learning application speaking_rate remains the fallback when
    this returns None.
    """
    row = conn.execute(
        """
        SELECT narration_speaking_rate_override
        FROM experiment_execution_contracts
        WHERE experiment_id = ?
          AND execution_mode = 'real'
          AND status IN ('approved', 'executing')
        LIMIT 1
        """,
        (experiment_id,),
    ).fetchone()
    if row is None:
        return None
    return row["narration_speaking_rate_override"]  # may be None even if row exists


def resolve_narration_speaking_rate_authority(
    conn: sqlite3.Connection,
    experiment_id: str,
) -> tuple[float | None, ParameterAuthority]:
    """Return (rate, authority) for narration_speaking_rate in the context of this experiment.

    Precedence:
      EXPERIMENT_TREATMENT — experiment has narration_speaking_rate as a treatment factor
                             with a non-null intended_value → use that value; Learning
                             Application must NOT be consumed.
      EXPERIMENT_CONTROL   — experiment has narration_speaking_rate as an ENFORCED control
                             factor with a non-null baseline_value → use baseline value;
                             Learning Application must NOT be consumed.
      EXPERIMENT_NOT_GOVERNING — experiment has a real/approved contract but does not
                                  declare authority over narration_speaking_rate → Learning
                                  Application continues to govern normally.

    Returns (None, EXPERIMENT_NOT_GOVERNING) when:
      - no real/approved contract exists for this experiment
      - the contract exists but narration_speaking_rate is neither a treatment nor an
        enforced control factor
    """
    row = conn.execute(
        """
        SELECT treatment_factors_json, control_factors_json
        FROM experiment_execution_contracts
        WHERE experiment_id = ?
          AND execution_mode = 'real'
          AND status IN ('approved', 'executing')
        LIMIT 1
        """,
        (experiment_id,),
    ).fetchone()

    if row is None:
        return (None, ParameterAuthority.EXPERIMENT_NOT_GOVERNING)

    # Check TREATMENT first (higher precedence)
    try:
        treatment_factors = json.loads(row["treatment_factors_json"] or "[]")
    except (TypeError, ValueError):
        treatment_factors = []

    for factor in treatment_factors:
        if factor.get("factor_name") == "narration_speaking_rate":
            intended = factor.get("intended_value")
            if intended is not None:
                try:
                    return (float(intended), ParameterAuthority.EXPERIMENT_TREATMENT)
                except (TypeError, ValueError):
                    pass

    # Check CONTROL (only ENFORCED capability suppresses learning application)
    try:
        control_factors = json.loads(row["control_factors_json"] or "[]")
    except (TypeError, ValueError):
        control_factors = []

    for factor in control_factors:
        if factor.get("factor_name") == "narration_speaking_rate":
            if factor.get("control_capability") == "enforced":
                baseline = factor.get("baseline_value")
                if baseline is not None:
                    try:
                        return (float(baseline), ParameterAuthority.EXPERIMENT_CONTROL)
                    except (TypeError, ValueError):
                        pass

    return (None, ParameterAuthority.EXPERIMENT_NOT_GOVERNING)


# ── Fidelity helpers ──────────────────────────────────────────────────────────


def _get_brief_planning_intent(
    conn: sqlite3.Connection,
    brief_id: str,
) -> str | None:
    """Return the brief_planning_intent for a strategy brief row."""
    row = conn.execute(
        "SELECT brief_planning_intent FROM experiment_strategy_briefs WHERE id = ?",
        (brief_id,),
    ).fetchone()
    return row["brief_planning_intent"] if row else None


def _get_script_body_for_experiment(
    conn: sqlite3.Connection,
    experiment_id: str,
) -> str | None:
    """Return the script body for the most recent production plan of an experiment.

    Returns None when no production plan (and therefore no script) exists yet.
    This is the evidence source for market-theme semantic fidelity checks.
    """
    row = conn.execute(
        """
        SELECT s.body
        FROM scripts s
        JOIN production_plans pp ON s.id = pp.script_id
        WHERE pp.experiment_id = ?
        ORDER BY pp.id DESC
        LIMIT 1
        """,
        (experiment_id,),
    ).fetchone()
    return row["body"] if row else None


# ── Fidelity comparison ────────────────────────────────────────────────────────


def _read_actual_from_feature_snapshot(
    conn: sqlite3.Connection,
    experiment_id: str,
    factor_name: str,
) -> tuple[bool, str | None]:
    """Read the actual factor value from content_feature_snapshots via experiment lineage.

    Returns (snapshot_exists, actual_value_str):
      (False, None) — no feature snapshot for this experiment yet → NOT_YET_AVAILABLE
      (True, None)  — snapshot exists but column is NULL → NOT_OBSERVABLE
      (True, str)   — value present

    Path: experiments → production_plans.experiment_id →
          content_feature_snapshots.production_plan_id
    """
    spec = SAFE_CONTROLLABLE_FACTORS.get(factor_name)
    col = spec.actual_value_source if spec else "not_observable"
    if col == "not_observable":
        return True, None  # schema has no column for this factor

    # Find the feature snapshot via production_plan
    snapshot_row = conn.execute(
        """
        SELECT cfs.*
        FROM content_feature_snapshots cfs
        JOIN production_plans pp ON cfs.production_plan_id = pp.id
        WHERE pp.experiment_id = ?
        ORDER BY cfs.id DESC
        LIMIT 1
        """,
        (experiment_id,),
    ).fetchone()

    if snapshot_row is None:
        return False, None  # production not yet complete

    raw = snapshot_row[col]

    # Integer boolean columns (has_hook, has_cta, render_caption_burn_in) → "true"/"false"
    spec_vtype = spec.value_type if spec else "unknown"
    if spec_vtype == "boolean" and raw is not None:
        return True, "true" if int(raw) == 1 else "false"

    # Numeric (narration_speaking_rate)
    if raw is None:
        return True, None
    return True, str(raw)


# Reason marker for "the factor was observed but no baseline was declared for
# it". Distinct from every other NOT_OBSERVABLE reason because it is not an
# observation failure: nothing was claimed, so nothing can have drifted.
# Treating the two alike classified every experiment on a bootstrapping
# channel as UNRESOLVED, which blocked its outcome, its maturity, and its
# contribution to learning — permanently, since a channel with no history is
# exactly the one that has no baselines.
NO_BASELINE_DECLARED = "no baseline declared for this factor"


def _evaluate_fidelity_outcome(
    factor_name: str,
    intended_str: str | None,
    snapshot_exists: bool,
    actual_str: str | None,
) -> tuple[FidelityOutcome, str]:
    """Compute the fidelity outcome for one factor."""
    spec = SAFE_CONTROLLABLE_FACTORS.get(factor_name)

    if spec and spec.actual_value_source == "not_observable":
        return FidelityOutcome.NOT_OBSERVABLE, "no extraction path in schema"

    if not snapshot_exists:
        return FidelityOutcome.NOT_YET_AVAILABLE, "feature snapshot not yet available"

    if actual_str is None:
        return FidelityOutcome.NOT_OBSERVABLE, "feature extractor returned NULL"

    if intended_str is None:
        # The factor WAS observed — there was simply no baseline to hold it
        # to. On a new channel most controlled factors legitimately have
        # baseline_source="unknown" (see _resolve_controlled_baselines), and
        # a control claim that was never made cannot have been violated.
        # Distinguished from a genuine observation failure by its reason, so
        # the classifier can weigh the two differently.
        return FidelityOutcome.NOT_OBSERVABLE, NO_BASELINE_DECLARED

    # Numeric comparison with tolerance
    if spec and spec.value_type == "numeric":
        try:
            intended_f = float(intended_str)
            actual_f = float(actual_str)
            diff = abs(actual_f - intended_f)
            tol = (
                spec.tolerance_abs
                if spec.tolerance_abs is not None
                else _SPEAKING_RATE_FIDELITY_TOL
            )
            if diff <= tol:
                return FidelityOutcome.MATCHED, f"diff={diff:.6f} ≤ tol={tol}"
            return (
                FidelityOutcome.DEVIATED,
                f"actual={actual_str} vs intended={intended_str} diff={diff:.6f} > tol={tol}",
            )
        except (TypeError, ValueError):
            return FidelityOutcome.NOT_OBSERVABLE, "could not parse numeric values"

    # Exact string match (boolean and categorical)
    if actual_str == intended_str:
        return FidelityOutcome.MATCHED, "exact match"
    return FidelityOutcome.DEVIATED, f"actual={actual_str!r} vs intended={intended_str!r}"


def compare_intended_vs_actual(
    conn: sqlite3.Connection,
    contract: ExperimentExecutionContract,
    *,
    market_theme_evaluator: Callable[[int, str | None], str] | None = None,
) -> ExecutionFidelity:
    """Compare intended factor values against actual production values.

    Phase 14F.2: experiment-type-aware classification with FidelityClassification.

    Reads actual values from content_feature_snapshots via production_plan lineage.
    Does NOT modify the DB — callers must persist the result via
    persist_fidelity() if desired.

    valid_for_learning derivation from classification:
      VALID / VALID_WITH_WARNINGS → True
      NOT_VALID                   → False
      NOT_YET_ASSESSABLE / UNRESOLVED → None
    """
    treatment_outcomes: list[FactorFidelity] = []
    control_outcomes: list[FactorFidelity] = []
    reasons: list[str] = []

    intent = _get_brief_planning_intent(conn, contract.brief_id)
    is_market_exploration = intent == BriefPlanningIntent.MARKET_EXPLORATION.value

    # ── Malformed check ──────────────────────────────────────────────────────
    # Non-market experiments with zero treatment configs are structurally invalid.
    malformed_no_treatments = not contract.treatment_configs and not is_market_exploration

    # ── Market-theme treatment dimension ────────────────────────────────────
    # For MARKET_EXPLORATION, the "treatment" is cluster fidelity, evaluated via
    # an injectable evaluator (no LLM, no live API in Phase 14F.2).
    market_theme_outcome: FidelityOutcome | None = None
    market_theme_reason = ""
    if is_market_exploration:
        cluster_id = contract.canonical_cluster_id
        if cluster_id is None:
            market_theme_outcome = FidelityOutcome.NOT_OBSERVABLE
            market_theme_reason = "canonical_cluster_id absent; market theme unverifiable"
            reasons.append(f"Market theme: {market_theme_reason}")
        elif market_theme_evaluator is None:
            market_theme_outcome = FidelityOutcome.NOT_OBSERVABLE
            market_theme_reason = "no market_theme_evaluator provided"
            reasons.append(f"Market theme: {market_theme_reason}")
        else:
            script_body = _get_script_body_for_experiment(conn, contract.experiment_id)
            try:
                result = market_theme_evaluator(cluster_id, script_body)
            except Exception as exc:
                result = "unresolved"
                market_theme_reason = f"market theme evaluator raised: {exc!r}"
            else:
                market_theme_reason = ""
            if result == "matched":
                market_theme_outcome = FidelityOutcome.MATCHED
                market_theme_reason = "market theme matched canonical cluster"
            elif result == "deviated":
                market_theme_outcome = FidelityOutcome.DEVIATED
                market_theme_reason = "market theme deviated from canonical cluster"
                reasons.append(f"Market theme: {market_theme_reason}")
            elif result == "not_yet_available":
                market_theme_outcome = FidelityOutcome.NOT_YET_AVAILABLE
                market_theme_reason = "market theme not yet available"
            else:  # "unresolved" or evaluator exception
                market_theme_outcome = FidelityOutcome.NOT_OBSERVABLE
                if not market_theme_reason:
                    market_theme_reason = f"market theme evaluator returned: {result}"
                reasons.append(f"Market theme: {market_theme_reason}")

        treatment_outcomes.append(
            FactorFidelity(
                factor_name=MARKET_THEME_FACTOR_NAME,
                intended_value=str(cluster_id) if cluster_id is not None else None,
                actual_value=None,
                outcome=market_theme_outcome,
                reason=market_theme_reason,
            )
        )

    # ── Treatment factors ────────────────────────────────────────────────────
    treatment_has_unavailable = False
    treatment_has_deviated = False
    treatment_has_unresolved = False

    for tc in contract.treatment_configs:
        snap_exists, actual_str = _read_actual_from_feature_snapshot(
            conn, contract.experiment_id, tc.factor_name
        )
        outcome, reason = _evaluate_fidelity_outcome(
            tc.factor_name, tc.intended_value, snap_exists, actual_str
        )
        if outcome == FidelityOutcome.NOT_YET_AVAILABLE:
            treatment_has_unavailable = True
        elif outcome == FidelityOutcome.DEVIATED:
            treatment_has_deviated = True
            reasons.append(f"Treatment {tc.factor_name}: {reason}")
        elif outcome == FidelityOutcome.NOT_OBSERVABLE:
            treatment_has_unresolved = True
            reasons.append(f"Treatment {tc.factor_name}: not observable")
        treatment_outcomes.append(
            FactorFidelity(
                factor_name=tc.factor_name,
                intended_value=tc.intended_value,
                actual_value=actual_str,
                outcome=outcome,
                reason=reason,
            )
        )

    # Incorporate market-theme outcome into treatment flags
    if market_theme_outcome is not None:
        if market_theme_outcome == FidelityOutcome.NOT_YET_AVAILABLE:
            treatment_has_unavailable = True
        elif market_theme_outcome == FidelityOutcome.DEVIATED:
            treatment_has_deviated = True
        elif market_theme_outcome == FidelityOutcome.NOT_OBSERVABLE:
            treatment_has_unresolved = True

    # ── Control factors ──────────────────────────────────────────────────────
    control_has_hard_deviated = False  # ENFORCED drift → NOT_VALID
    control_has_hard_unresolved = False  # ENFORCED NOT_OBSERVABLE → UNRESOLVED
    control_has_soft_warning = False  # SOFT drift/not-observable → VALID_WITH_WARNINGS
    confounding_risk_realized = "low"

    for cc in contract.control_configs:
        cap = cc.control_capability
        if cap == ControlCapability.ENFORCED.value:
            snap_exists, actual_str = _read_actual_from_feature_snapshot(
                conn, contract.experiment_id, cc.factor_name
            )
            outcome, reason = _evaluate_fidelity_outcome(
                cc.factor_name, cc.baseline_value, snap_exists, actual_str
            )
            if outcome == FidelityOutcome.DEVIATED:
                control_has_hard_deviated = True
                reasons.append(f"Control {cc.factor_name} drifted: {reason}")
                confounding_risk_realized = "high"
            elif outcome == FidelityOutcome.NOT_OBSERVABLE:
                if reason == NO_BASELINE_DECLARED:
                    # No baseline was ever declared for this factor, so the
                    # experiment made no claim about holding it constant.
                    # Worth recording, but it does not make the execution
                    # unassessable.
                    control_has_soft_warning = True
                    reasons.append(
                        f"Control {cc.factor_name}: observed, but no baseline was "
                        "declared to compare against (warning only)"
                    )
                else:
                    control_has_hard_unresolved = True
                    reasons.append(f"Control {cc.factor_name}: enforced but not observable")
            elif outcome == FidelityOutcome.NOT_YET_AVAILABLE:
                # Enforced control not yet available — defer assessment
                treatment_has_unavailable = True
            control_outcomes.append(
                FactorFidelity(
                    factor_name=cc.factor_name,
                    intended_value=cc.baseline_value,
                    actual_value=actual_str,
                    outcome=outcome,
                    reason=reason,
                )
            )
        elif cap == ControlCapability.SOFT.value:
            snap_exists, actual_str = _read_actual_from_feature_snapshot(
                conn, contract.experiment_id, cc.factor_name
            )
            outcome, reason = _evaluate_fidelity_outcome(
                cc.factor_name, cc.baseline_value, snap_exists, actual_str
            )
            if outcome in (FidelityOutcome.DEVIATED, FidelityOutcome.NOT_OBSERVABLE):
                control_has_soft_warning = True
                reasons.append(f"Soft control {cc.factor_name}: {reason} (warning only)")
            control_outcomes.append(
                FactorFidelity(
                    factor_name=cc.factor_name,
                    intended_value=cc.baseline_value,
                    actual_value=actual_str,
                    outcome=outcome,
                    reason=reason,
                )
            )
        else:
            # NOT_CONTROLLABLE — record as not observable, no fidelity impact
            control_outcomes.append(
                FactorFidelity(
                    factor_name=cc.factor_name,
                    intended_value=cc.baseline_value,
                    actual_value=None,
                    outcome=FidelityOutcome.NOT_OBSERVABLE,
                    reason=f"control_capability={cap}; not controllable",
                )
            )

    # ── Classification ───────────────────────────────────────────────────────
    # Priority (highest wins):
    #   malformed (non-market, zero treatments)    → NOT_VALID
    #   NOT_YET_AVAILABLE (any dimension)          → NOT_YET_ASSESSABLE
    #   treatment DEVIATED                         → NOT_VALID
    #   treatment UNRESOLVED (NOT_OBSERVABLE)      → UNRESOLVED
    #   enforced control DEVIATED                  → NOT_VALID
    #   enforced control UNRESOLVED                → UNRESOLVED
    #   soft control drift/unobservable            → VALID_WITH_WARNINGS
    #   all clear                                  → VALID
    if malformed_no_treatments:
        classification = FidelityClassification.NOT_VALID
        reasons.insert(0, "No treatment factors declared for non-market experiment.")
    elif treatment_has_unavailable:
        classification = FidelityClassification.NOT_YET_ASSESSABLE
    elif treatment_has_deviated:
        classification = FidelityClassification.NOT_VALID
        if not any(r.startswith("Treatment") or r.startswith("Market") for r in reasons):
            reasons.insert(0, "Treatment deviated from intended value.")
    elif treatment_has_unresolved:
        classification = FidelityClassification.UNRESOLVED
    elif control_has_hard_deviated:
        classification = FidelityClassification.NOT_VALID
    elif control_has_hard_unresolved:
        classification = FidelityClassification.UNRESOLVED
    elif control_has_soft_warning:
        classification = FidelityClassification.VALID_WITH_WARNINGS
    else:
        classification = FidelityClassification.VALID

    # Derive valid_for_learning from classification
    if classification in (FidelityClassification.VALID, FidelityClassification.VALID_WITH_WARNINGS):
        valid_for_learning: bool | None = True
    elif classification == FidelityClassification.NOT_VALID:
        valid_for_learning = False
    else:  # NOT_YET_ASSESSABLE or UNRESOLVED
        valid_for_learning = None

    if control_has_hard_deviated and valid_for_learning is not False:
        confounding_risk_realized = "high"

    return ExecutionFidelity(
        treatment_outcomes=treatment_outcomes,
        control_outcomes=control_outcomes,
        valid_for_learning=valid_for_learning,
        confounding_risk_realized=confounding_risk_realized,
        reasons=reasons,
        classification=classification,
        fidelity_policy_version=FIDELITY_POLICY_VERSION,
    )


def persist_fidelity(
    conn: sqlite3.Connection,
    contract_id: str,
    fidelity: ExecutionFidelity,
) -> None:
    """Persist fidelity results to the execution contract row."""
    fidelity_dict = {
        "treatment_outcomes": [
            {
                "factor_name": f.factor_name,
                "intended_value": f.intended_value,
                "actual_value": f.actual_value,
                "outcome": f.outcome.value,
                "reason": f.reason,
            }
            for f in fidelity.treatment_outcomes
        ],
        "control_outcomes": [
            {
                "factor_name": f.factor_name,
                "intended_value": f.intended_value,
                "actual_value": f.actual_value,
                "outcome": f.outcome.value,
                "reason": f.reason,
            }
            for f in fidelity.control_outcomes
        ],
        "valid_for_learning": fidelity.valid_for_learning,
        "confounding_risk_realized": fidelity.confounding_risk_realized,
        "reasons": fidelity.reasons,
        "classification": fidelity.classification.value if fidelity.classification else None,
        "fidelity_policy_version": fidelity.fidelity_policy_version,
    }
    vfl = None if fidelity.valid_for_learning is None else int(fidelity.valid_for_learning)
    conn.execute(
        """
        UPDATE experiment_execution_contracts
        SET fidelity_json = ?,
            valid_for_learning = ?,
            confounding_risk_realized = ?,
            status = CASE WHEN status NOT IN ('failed', 'blocked') THEN 'completed' ELSE status END,
            completed_at = ?
        WHERE id = ?
        """,
        (
            json.dumps(fidelity_dict),
            vfl,
            fidelity.confounding_risk_realized,
            _utc_now(),
            contract_id,
        ),
    )
