"""Phase 14G — Experiment outcome evaluation service.

Evaluates whether a completed experiment produced enough trustworthy evidence
to inform learning. Read-only with respect to experiments, analytics, and
Phase12C tables. Only writes to experiment_outcomes.

Safety invariants:
- No YouTube API calls
- No live analytics ingest
- No content generation
- No LLM calls
- No modification to experiments, analytics, Phase12C, or publication tables
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import UTC, datetime

from app.intelligence.experiments.models import MaturityPolicy, MetricDirection
from app.intelligence.experiments.outcome_contract import (
    OUTCOME_POLICY_VERSION,
    BaselineSourceType,
    EvidenceMaturity,
    ExperimentOutcomeEvaluation,
    OutcomeClassification,
    OutcomeReadiness,
)

_EVALUABLE_STATUSES = frozenset(
    {
        "published",
        "observing",
        "mature",
        "analyzed",
        "completed",
    }
)

_DEFAULT_TARGET_METRIC = "average_view_percentage"
_DEFAULT_TARGET_DIRECTION = MetricDirection.higher_is_better.value


# ── Public API ────────────────────────────────────────────────────────────────


def evaluate_experiment_outcome(
    conn: sqlite3.Connection,
    experiment_id: str,
    prior_experiment_id: str | None = None,
) -> ExperimentOutcomeEvaluation:
    """Evaluate experiment outcome (read-only). Does not persist.

    Args:
        conn: DB connection.
        experiment_id: The experiment to evaluate.
        prior_experiment_id: Optional; if this is a validation or sequential
            experiment, the prior experiment whose treatment_metric_value
            serves as the baseline.
    """
    evaluated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")

    exp_row = conn.execute("SELECT * FROM experiments WHERE id = ?", (experiment_id,)).fetchone()
    if exp_row is None:
        raise KeyError(f"Experiment '{experiment_id}' not found")

    status = exp_row["status"]

    if status not in _EVALUABLE_STATUSES:
        return _make_result(
            experiment_id=experiment_id,
            readiness=OutcomeReadiness.NOT_READY,
            reasons=[f"experiment status '{status}' is not evaluable"],
            evaluated_at=evaluated_at,
        )

    # ── Fidelity gate ─────────────────────────────────────────────────────────
    contract_row = conn.execute(
        "SELECT fidelity_json FROM experiment_execution_contracts WHERE experiment_id = ?",
        (experiment_id,),
    ).fetchone()

    if contract_row is None:
        return _make_result(
            experiment_id=experiment_id,
            readiness=OutcomeReadiness.INVALID_EXECUTION,
            reasons=["no execution contract found"],
            evaluated_at=evaluated_at,
        )

    raw_fidelity = (
        json.loads(contract_row["fidelity_json"] or "{}") if contract_row["fidelity_json"] else {}
    )
    fidelity_classification = raw_fidelity.get("classification")

    if fidelity_classification not in ("valid", "valid_with_warnings"):
        return _make_result(
            experiment_id=experiment_id,
            readiness=OutcomeReadiness.INVALID_EXECUTION,
            reasons=[
                f"fidelity classification '{fidelity_classification}' blocks outcome evaluation"
            ],
            evaluated_at=evaluated_at,
        )

    # ── Publication gate ──────────────────────────────────────────────────────
    publication_id = exp_row["publication_id"]
    if publication_id is None:
        return _make_result(
            experiment_id=experiment_id,
            readiness=OutcomeReadiness.INSUFFICIENT_ANALYTICS,
            reasons=["experiment has no linked publication"],
            evaluated_at=evaluated_at,
        )

    pub_row = conn.execute(
        "SELECT published_at FROM publications WHERE id = ?", (publication_id,)
    ).fetchone()

    policy = MaturityPolicy.from_json(exp_row["maturity_policy_json"] or "{}")

    now_utc = datetime.now(UTC)
    publication_age_hours: float | None = None
    age_ok = False

    if pub_row and pub_row["published_at"]:
        try:
            pub_dt_str = pub_row["published_at"].replace("Z", "+00:00")
            pub_dt = datetime.fromisoformat(pub_dt_str)
            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=UTC)
            publication_age_hours = (now_utc - pub_dt).total_seconds() / 3600.0
            age_ok = publication_age_hours >= policy.minimum_age_hours
        except (ValueError, TypeError):
            pass

    # ── Analytics maturity ────────────────────────────────────────────────────
    # Seed rows (input_hash LIKE 'seed-%') are development fixtures, not
    # observations. Phase 12C already excludes them from learning; excluding
    # them here too is what keeps an outcome from being scored against
    # fabricated numbers. ORDER BY id DESC makes the pick deterministic when
    # more than one real row exists for a metric.
    views_row = conn.execute(
        """SELECT metric_value FROM analytics_aggregates
           WHERE publication_id = ? AND period_type = 'lifetime' AND metric_name = 'views'
             AND input_hash NOT LIKE 'seed-%'
           ORDER BY id DESC LIMIT 1""",
        (publication_id,),
    ).fetchone()

    observed_views: float | None = views_row["metric_value"] if views_row else None
    views_ok = observed_views is not None and observed_views >= policy.minimum_views

    has_data_snapshot = (
        conn.execute(
            "SELECT 1 FROM analytics_snapshots "
            "WHERE publication_id = ? AND observation_state = 'data' LIMIT 1",
            (publication_id,),
        ).fetchone()
        is not None
    )

    has_any_analytics = (observed_views is not None) or has_data_snapshot
    if not has_any_analytics:
        readiness = OutcomeReadiness.INSUFFICIENT_ANALYTICS
    elif age_ok and views_ok:
        readiness = OutcomeReadiness.EVALUABLE_MATURE
    elif age_ok or views_ok:
        readiness = OutcomeReadiness.EVALUABLE_PROVISIONAL
    else:
        # Some data exists but neither maturity threshold is met
        readiness = OutcomeReadiness.INSUFFICIENT_ANALYTICS

    if readiness == OutcomeReadiness.INSUFFICIENT_ANALYTICS:
        reasons: list[str] = []
        if not views_ok:
            v_str = f"{observed_views:.0f}" if observed_views is not None else "none"
            reasons.append(f"insufficient views: {v_str} < {policy.minimum_views}")
        if not age_ok:
            a_str = (
                f"{publication_age_hours:.1f}h" if publication_age_hours is not None else "unknown"
            )
            reasons.append(f"insufficient publication age: {a_str} < {policy.minimum_age_hours}h")
        return _make_result(
            experiment_id=experiment_id,
            readiness=readiness,
            publication_age_hours=publication_age_hours,
            observed_views=observed_views,
            reasons=reasons,
            evaluated_at=evaluated_at,
        )

    # ── Target metric ─────────────────────────────────────────────────────────
    target_row = conn.execute(
        """SELECT metric_name, direction, target_min, target_max, target_value
           FROM experiment_metric_targets
           WHERE experiment_id = ? AND is_primary = 1
           LIMIT 1""",
        (experiment_id,),
    ).fetchone()

    if target_row:
        target_metric_name = target_row["metric_name"]
        target_metric_direction = target_row["direction"]
        target_min = target_row["target_min"]
        target_max = target_row["target_max"]
    else:
        target_metric_name = _DEFAULT_TARGET_METRIC
        target_metric_direction = _DEFAULT_TARGET_DIRECTION
        target_min = None
        target_max = None

    # ── Treatment value ───────────────────────────────────────────────────────
    treatment_row = conn.execute(
        """SELECT metric_value FROM analytics_aggregates
           WHERE publication_id = ? AND period_type = 'lifetime' AND metric_name = ?
             AND input_hash NOT LIKE 'seed-%'
           ORDER BY id DESC LIMIT 1""",
        (publication_id, target_metric_name),
    ).fetchone()

    treatment_value: float | None = treatment_row["metric_value"] if treatment_row else None

    if treatment_value is None:
        return _make_result(
            experiment_id=experiment_id,
            readiness=readiness,
            classification=OutcomeClassification.INCONCLUSIVE,
            target_metric_name=target_metric_name,
            target_metric_direction=target_metric_direction,
            is_mature=(readiness == OutcomeReadiness.EVALUABLE_MATURE),
            publication_age_hours=publication_age_hours,
            observed_views=observed_views,
            reasons=["target metric value not available in analytics"],
            evaluated_at=evaluated_at,
        )

    # ── Baseline ──────────────────────────────────────────────────────────────
    channel_id = exp_row["channel_id"]
    is_validation = _is_validation_repeat(conn, experiment_id, exp_row["opportunity_id"])

    baseline_source, baseline_value, baseline_exp_id = _resolve_baseline(
        conn=conn,
        channel_id=channel_id,
        publication_id=publication_id,
        target_metric_name=target_metric_name,
        is_validation=is_validation,
        prior_experiment_id=prior_experiment_id,
    )

    # ── Direction and classification ──────────────────────────────────────────
    direction = MetricDirection(target_metric_direction)
    absolute_delta: float | None = None
    relative_delta: float | None = None

    if direction == MetricDirection.informational_only:
        classification = OutcomeClassification.INFORMATIONAL_ONLY
    elif baseline_value is None:
        classification = OutcomeClassification.BASELINE_UNAVAILABLE
    else:
        absolute_delta = treatment_value - baseline_value
        relative_delta = (absolute_delta / baseline_value) if baseline_value != 0.0 else None
        classification = _classify(
            direction=direction,
            absolute_delta=absolute_delta,
            treatment_value=treatment_value,
            target_min=target_min,
            target_max=target_max,
        )

    # ── Evidence maturity ─────────────────────────────────────────────────────
    if is_validation and prior_experiment_id is not None:
        evidence_maturity = EvidenceMaturity.DIRECTIONAL
    else:
        evidence_maturity = EvidenceMaturity.EXPLORATORY

    # ── Warnings ──────────────────────────────────────────────────────────────
    warnings: list[str] = []
    if readiness == OutcomeReadiness.EVALUABLE_PROVISIONAL:
        warnings.append("provisional evaluation: not all maturity thresholds met")
        if not age_ok and publication_age_hours is not None:
            warnings.append(
                f"publication age {publication_age_hours:.1f}h < "
                f"{policy.minimum_age_hours}h minimum"
            )
        if not views_ok:
            v_str = f"{observed_views:.0f}" if observed_views is not None else "none"
            warnings.append(f"observed views {v_str} < {policy.minimum_views} minimum")

    # ── Input hash ────────────────────────────────────────────────────────────
    hash_parts = [
        experiment_id,
        fidelity_classification or "",
        readiness.value,
        classification.value if classification else "none",
        target_metric_name or "",
        target_metric_direction or "",
        f"{treatment_value:.6f}" if treatment_value is not None else "null",
        f"{baseline_value:.6f}" if baseline_value is not None else "null",
        baseline_source.value,
        prior_experiment_id or "",
        OUTCOME_POLICY_VERSION,
    ]
    input_hash = hashlib.sha256("|".join(hash_parts).encode()).hexdigest()

    return ExperimentOutcomeEvaluation(
        id=str(uuid.uuid4()),
        experiment_id=experiment_id,
        readiness=readiness,
        classification=classification,
        evidence_maturity=evidence_maturity,
        baseline_source_type=baseline_source,
        baseline_experiment_id=baseline_exp_id,
        target_metric_name=target_metric_name,
        target_metric_direction=target_metric_direction,
        treatment_metric_value=treatment_value,
        baseline_metric_value=baseline_value,
        absolute_delta=absolute_delta,
        relative_delta=relative_delta,
        is_mature=(readiness == OutcomeReadiness.EVALUABLE_MATURE),
        publication_age_hours=publication_age_hours,
        observed_views=observed_views,
        reasons=[],
        warnings=warnings,
        outcome_policy_version=OUTCOME_POLICY_VERSION,
        input_hash=input_hash,
        evaluated_at=evaluated_at,
    )


def persist_outcome(
    conn: sqlite3.Connection,
    evaluation: ExperimentOutcomeEvaluation,
) -> None:
    """Persist outcome row. Append-only; idempotent on same input_hash."""
    conn.execute(
        """INSERT OR IGNORE INTO experiment_outcomes (
               id, experiment_id, readiness, classification, evidence_maturity,
               baseline_source_type, baseline_experiment_id,
               target_metric_name, target_metric_direction,
               treatment_metric_value, baseline_metric_value,
               absolute_delta, relative_delta,
               is_mature, publication_age_hours, observed_views,
               reasons_json, warnings_json,
               outcome_policy_version, input_hash, evaluated_at
           ) VALUES (
               :id, :experiment_id, :readiness, :classification, :evidence_maturity,
               :baseline_source_type, :baseline_experiment_id,
               :target_metric_name, :target_metric_direction,
               :treatment_metric_value, :baseline_metric_value,
               :absolute_delta, :relative_delta,
               :is_mature, :publication_age_hours, :observed_views,
               :reasons_json, :warnings_json,
               :outcome_policy_version, :input_hash, :evaluated_at
           )""",
        {
            "id": evaluation.id,
            "experiment_id": evaluation.experiment_id,
            "readiness": evaluation.readiness.value,
            "classification": evaluation.classification.value
            if evaluation.classification
            else None,
            "evidence_maturity": evaluation.evidence_maturity.value
            if evaluation.evidence_maturity
            else None,
            "baseline_source_type": evaluation.baseline_source_type.value,
            "baseline_experiment_id": evaluation.baseline_experiment_id,
            "target_metric_name": evaluation.target_metric_name,
            "target_metric_direction": evaluation.target_metric_direction,
            "treatment_metric_value": evaluation.treatment_metric_value,
            "baseline_metric_value": evaluation.baseline_metric_value,
            "absolute_delta": evaluation.absolute_delta,
            "relative_delta": evaluation.relative_delta,
            "is_mature": 1 if evaluation.is_mature else 0,
            "publication_age_hours": evaluation.publication_age_hours,
            "observed_views": evaluation.observed_views,
            "reasons_json": json.dumps(evaluation.reasons),
            "warnings_json": json.dumps(evaluation.warnings),
            "outcome_policy_version": evaluation.outcome_policy_version,
            "input_hash": evaluation.input_hash,
            "evaluated_at": evaluation.evaluated_at,
        },
    )


def get_latest_experiment_outcome(
    conn: sqlite3.Connection,
    experiment_id: str,
) -> ExperimentOutcomeEvaluation | None:
    """Return the most recent outcome evaluation for an experiment, or None."""
    row = conn.execute(
        """SELECT * FROM experiment_outcomes
           WHERE experiment_id = ?
           ORDER BY evaluated_at DESC, id DESC
           LIMIT 1""",
        (experiment_id,),
    ).fetchone()
    return _row_to_evaluation(row) if row else None


def list_experiment_outcomes(
    conn: sqlite3.Connection,
    experiment_id: str,
) -> list[ExperimentOutcomeEvaluation]:
    """Return all outcome evaluations for an experiment, newest first."""
    rows = conn.execute(
        """SELECT * FROM experiment_outcomes
           WHERE experiment_id = ?
           ORDER BY evaluated_at DESC, id DESC""",
        (experiment_id,),
    ).fetchall()
    return [_row_to_evaluation(r) for r in rows]


def get_outcome_for_phase14d(
    conn: sqlite3.Connection,
    experiment_id: str,
) -> dict | None:
    """Return the latest outcome as a dict for Phase14D/learning consumption, or None."""
    ev = get_latest_experiment_outcome(conn, experiment_id)
    if ev is None:
        return None
    return {
        "experiment_id": ev.experiment_id,
        "readiness": ev.readiness.value,
        "classification": ev.classification.value if ev.classification else None,
        "evidence_maturity": ev.evidence_maturity.value if ev.evidence_maturity else None,
        "baseline_source_type": ev.baseline_source_type.value,
        "baseline_experiment_id": ev.baseline_experiment_id,
        "target_metric_name": ev.target_metric_name,
        "target_metric_direction": ev.target_metric_direction,
        "treatment_metric_value": ev.treatment_metric_value,
        "baseline_metric_value": ev.baseline_metric_value,
        "absolute_delta": ev.absolute_delta,
        "relative_delta": ev.relative_delta,
        "is_mature": ev.is_mature,
        "publication_age_hours": ev.publication_age_hours,
        "observed_views": ev.observed_views,
        "warnings": ev.warnings,
        "outcome_policy_version": ev.outcome_policy_version,
        "input_hash": ev.input_hash,
        "evaluated_at": ev.evaluated_at,
    }


# ── Private helpers ───────────────────────────────────────────────────────────


def _make_result(
    experiment_id: str,
    readiness: OutcomeReadiness,
    *,
    classification: OutcomeClassification | None = None,
    evidence_maturity: EvidenceMaturity | None = None,
    baseline_source_type: BaselineSourceType = BaselineSourceType.NONE,
    baseline_experiment_id: str | None = None,
    target_metric_name: str | None = None,
    target_metric_direction: str | None = None,
    treatment_metric_value: float | None = None,
    baseline_metric_value: float | None = None,
    absolute_delta: float | None = None,
    relative_delta: float | None = None,
    is_mature: bool = False,
    publication_age_hours: float | None = None,
    observed_views: float | None = None,
    reasons: list[str] | None = None,
    warnings: list[str] | None = None,
    evaluated_at: str = "",
) -> ExperimentOutcomeEvaluation:
    hash_parts = [
        experiment_id,
        readiness.value,
        classification.value if classification else "none",
        target_metric_name or "",
        target_metric_direction or "",
        f"{treatment_metric_value:.6f}" if treatment_metric_value is not None else "null",
        f"{baseline_metric_value:.6f}" if baseline_metric_value is not None else "null",
        baseline_source_type.value,
        OUTCOME_POLICY_VERSION,
    ]
    input_hash = hashlib.sha256("|".join(hash_parts).encode()).hexdigest()
    return ExperimentOutcomeEvaluation(
        id=str(uuid.uuid4()),
        experiment_id=experiment_id,
        readiness=readiness,
        classification=classification,
        evidence_maturity=evidence_maturity,
        baseline_source_type=baseline_source_type,
        baseline_experiment_id=baseline_experiment_id,
        target_metric_name=target_metric_name,
        target_metric_direction=target_metric_direction,
        treatment_metric_value=treatment_metric_value,
        baseline_metric_value=baseline_metric_value,
        absolute_delta=absolute_delta,
        relative_delta=relative_delta,
        is_mature=is_mature,
        publication_age_hours=publication_age_hours,
        observed_views=observed_views,
        reasons=reasons or [],
        warnings=warnings or [],
        outcome_policy_version=OUTCOME_POLICY_VERSION,
        input_hash=input_hash,
        evaluated_at=evaluated_at,
    )


def _is_validation_repeat(
    conn: sqlite3.Connection,
    experiment_id: str,
    opportunity_id: int | None,
) -> bool:
    if opportunity_id is None:
        return False
    row = conn.execute(
        """SELECT is_validation_repeat FROM experiment_selection_decisions
           WHERE opportunity_id = ? AND selected = 1
           ORDER BY created_at DESC LIMIT 1""",
        (opportunity_id,),
    ).fetchone()
    return bool(row["is_validation_repeat"]) if row else False


def _resolve_baseline(
    conn: sqlite3.Connection,
    channel_id: int,
    publication_id: int,
    target_metric_name: str,
    is_validation: bool,
    prior_experiment_id: str | None,
) -> tuple[BaselineSourceType, float | None, str | None]:
    if prior_experiment_id is not None:
        prior_row = conn.execute(
            """SELECT treatment_metric_value FROM experiment_outcomes
               WHERE experiment_id = ? AND treatment_metric_value IS NOT NULL
               ORDER BY evaluated_at DESC LIMIT 1""",
            (prior_experiment_id,),
        ).fetchone()
        if prior_row and prior_row["treatment_metric_value"] is not None:
            source = (
                BaselineSourceType.VALIDATION_REFERENCE
                if is_validation
                else BaselineSourceType.PRIOR_EXPERIMENT
            )
            return source, prior_row["treatment_metric_value"], prior_experiment_id

    avg_row = conn.execute(
        """SELECT AVG(aa.metric_value) AS avg_val
           FROM analytics_aggregates aa
           JOIN topics t ON t.id = aa.topic_id
           JOIN opportunities o ON o.id = t.promoted_opportunity_id
           WHERE o.channel_id = ?
             AND aa.period_type = 'lifetime'
             AND aa.metric_name = ?
             AND aa.publication_id != ?
             AND aa.metric_value IS NOT NULL
             AND aa.input_hash NOT LIKE 'seed-%'""",
        (channel_id, target_metric_name, publication_id),
    ).fetchone()

    avg_val = avg_row["avg_val"] if avg_row else None
    if avg_val is not None:
        return BaselineSourceType.CHANNEL_BASELINE, avg_val, None

    return BaselineSourceType.NONE, None, None


def _classify(
    direction: MetricDirection,
    absolute_delta: float,
    treatment_value: float,
    target_min: float | None,
    target_max: float | None,
) -> OutcomeClassification:
    if direction == MetricDirection.higher_is_better:
        if absolute_delta > 0:
            return OutcomeClassification.POSITIVE_OBSERVATION
        if absolute_delta < 0:
            return OutcomeClassification.NEGATIVE_OBSERVATION
        return OutcomeClassification.NEUTRAL_OBSERVATION

    if direction == MetricDirection.lower_is_better:
        if absolute_delta < 0:
            return OutcomeClassification.POSITIVE_OBSERVATION
        if absolute_delta > 0:
            return OutcomeClassification.NEGATIVE_OBSERVATION
        return OutcomeClassification.NEUTRAL_OBSERVATION

    if direction == MetricDirection.target_range:
        if target_min is not None and target_max is not None:
            if target_min <= treatment_value <= target_max:
                return OutcomeClassification.POSITIVE_OBSERVATION
            return OutcomeClassification.NEGATIVE_OBSERVATION
        return OutcomeClassification.NEUTRAL_OBSERVATION

    return OutcomeClassification.INFORMATIONAL_ONLY


def _row_to_evaluation(row: sqlite3.Row) -> ExperimentOutcomeEvaluation:
    return ExperimentOutcomeEvaluation(
        id=row["id"],
        experiment_id=row["experiment_id"],
        readiness=OutcomeReadiness(row["readiness"]),
        classification=OutcomeClassification(row["classification"])
        if row["classification"]
        else None,
        evidence_maturity=EvidenceMaturity(row["evidence_maturity"])
        if row["evidence_maturity"]
        else None,
        baseline_source_type=BaselineSourceType(row["baseline_source_type"]),
        baseline_experiment_id=row["baseline_experiment_id"],
        target_metric_name=row["target_metric_name"],
        target_metric_direction=row["target_metric_direction"],
        treatment_metric_value=row["treatment_metric_value"],
        baseline_metric_value=row["baseline_metric_value"],
        absolute_delta=row["absolute_delta"],
        relative_delta=row["relative_delta"],
        is_mature=bool(row["is_mature"]),
        publication_age_hours=row["publication_age_hours"],
        observed_views=row["observed_views"],
        reasons=json.loads(row["reasons_json"] or "[]"),
        warnings=json.loads(row["warnings_json"] or "[]"),
        outcome_policy_version=row["outcome_policy_version"],
        input_hash=row["input_hash"],
        evaluated_at=row["evaluated_at"],
    )
