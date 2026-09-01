"""Phase 18D — analytics → experiment outcome → learning-consumed bridge.

Phase 14G built a complete, honest outcome evaluator (`outcome_service`) and
Phase 12C built cross-publication learning. Neither was ever reachable from
the autonomous loop: `evaluate_experiment_outcome` and `persist_outcome`
were called only from the CLI, so a published experiment could accumulate
real analytics indefinitely and never produce an outcome row.

This module is the missing driver. It runs after an observation tick that
brought in new data, and does exactly four things, each of which is a no-op
when its precondition is not met:

1. Extract content features for the publication (Phase 12B). Nothing in the
   autonomous path did this, which meant cross-publication learning — which
   only considers publications that have a feature snapshot — could not see
   any autonomously produced video.

2. Assess execution fidelity against the experiment's contract (Phase 14F).
   The outcome evaluator refuses to score an experiment whose execution
   fidelity is unknown, so without this every outcome was INVALID_EXECUTION.

3. Evaluate and persist the outcome (Phase 14G), using that evaluator's own
   maturity thresholds unchanged.

4. Advance the ledger to match the evidence that actually exists.

What "maturity" means here is decided entirely by the existing evaluator:

  INSUFFICIENT_ANALYTICS   → no outcome row, experiment stays `observing`
  EVALUABLE_PROVISIONAL    → outcome persisted and marked provisional,
                             experiment stays `observing` because evidence
                             is still accumulating
  EVALUABLE_MATURE         → outcome persisted, experiment → `mature` →
                             `analyzed`

A young publication with no views is therefore represented as honestly
immature and remains eligible for every later observation. Nothing in this
module invents a metric, lowers a threshold, or promotes an experiment that
the evaluator did not certify.

`analyzed → completed` is deliberately gated on something further: the
publication must actually appear in the channel's cross-publication
learning evidence. `completed` means "this experiment's evidence has been
consumed", which is also what releases its opportunity from the eligibility
gate's active-conflict block. Without that gate, either experiments would
never complete (and every opportunity they touched would stay blocked
forever) or they would complete before their evidence reached learning.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from app.core.logging import get_logger
from app.intelligence.experiments.models import ExperimentStatus

logger = get_logger(__name__)


@dataclass
class OutcomeBridgeResult:
    """What one bridge run did, reported honestly including the no-ops."""

    publication_id: int
    experiment_id: str | None = None
    features_extracted: bool = False
    fidelity_classification: str | None = None
    outcome_readiness: str | None = None
    outcome_classification: str | None = None
    outcome_persisted: bool = False
    experiment_status: str | None = None
    transitions: list[str] | None = None
    skipped_reason: str | None = None
    errors: list[str] | None = None

    def __post_init__(self) -> None:
        if self.transitions is None:
            self.transitions = []
        if self.errors is None:
            self.errors = []


# ── Step 1: content features ─────────────────────────────────────────────────


def ensure_content_features(conn: sqlite3.Connection, publication_id: int) -> bool:
    """Extract and persist the Phase 12B feature snapshot. Idempotent.

    Returns True when a snapshot was newly created. Cross-publication
    learning only considers publications that have one of these, so without
    this call an autonomously produced video contributes nothing to the
    channel's own evidence no matter how much analytics it collects.
    """
    from app.learning.features import extract_and_save

    _snapshot, created = extract_and_save(conn, publication_id)
    return created


# ── Step 2: execution fidelity ───────────────────────────────────────────────


def ensure_execution_fidelity(conn: sqlite3.Connection, experiment_id: str) -> str | None:
    """Assess and persist execution fidelity if it has not been assessed yet.

    Returns the fidelity classification, or None when there is no execution
    contract to assess against. Already-assessed contracts are left alone so
    a later re-run cannot reclassify a completed experiment.
    """
    from app.intelligence.experiments.execution_service import (
        compare_intended_vs_actual,
        get_contract_for_experiment,
        persist_fidelity,
    )

    contract = get_contract_for_experiment(conn, experiment_id)
    if contract is None:
        return None

    existing_raw = conn.execute(
        "SELECT fidelity_json FROM experiment_execution_contracts WHERE experiment_id = ?",
        (experiment_id,),
    ).fetchone()
    if existing_raw is not None and existing_raw["fidelity_json"]:
        try:
            return json.loads(existing_raw["fidelity_json"]).get("classification")
        except (json.JSONDecodeError, TypeError):
            pass  # malformed — reassess below

    # A market-exploration experiment's treatment IS its topic cluster, so
    # fidelity needs a way to ask "is the produced video actually about that
    # cluster?". Without an evaluator the engine correctly abstains
    # (UNRESOLVED), which blocked every autonomous market experiment from
    # ever producing an outcome. The evaluator is deterministic and local —
    # no LLM, no network — so this stays a pure bookkeeping step.
    from app.intelligence.experiments.market_theme_fidelity import (
        build_market_theme_evaluator,
    )

    fidelity = compare_intended_vs_actual(
        conn, contract, market_theme_evaluator=build_market_theme_evaluator(conn)
    )
    persist_fidelity(conn, contract.id, fidelity)
    conn.commit()
    return fidelity.classification.value if fidelity.classification else None


# ── Step 4b: has learning actually consumed this publication? ────────────────


def learning_has_consumed(
    conn: sqlite3.Connection, *, cp_channel_id: str, publication_id: int
) -> bool:
    """True when this publication contributes to the channel's learning evidence.

    Checked against channel_performance_baselines rather than against the
    mere existence of a feature snapshot, because a snapshot with no
    analytics contributes to no baseline — the question is whether the
    evidence reached learning, not whether it was extracted.
    """
    rows = conn.execute(
        "SELECT source_publication_ids_json FROM channel_performance_baselines "
        "WHERE channel_id = ?",
        (cp_channel_id,),
    ).fetchall()
    for row in rows:
        try:
            ids = json.loads(row["source_publication_ids_json"] or "[]")
        except (json.JSONDecodeError, TypeError):
            continue
        if publication_id in ids:
            return True
    return False


# ── Driver ───────────────────────────────────────────────────────────────────


def run_outcome_bridge(
    conn: sqlite3.Connection,
    *,
    publication_id: int,
    actor: str = "system:auto_observer",
) -> OutcomeBridgeResult:
    """Run the analytics → outcome → ledger bridge for one publication.

    Safe to call on every observation tick: each step is idempotent and each
    is skipped when its evidence is not there yet. Never raises — the
    publication is already public and the observation already succeeded, so
    a bookkeeping failure must not turn either into an error.
    """
    result = OutcomeBridgeResult(publication_id=publication_id)

    from app.intelligence.experiments.lifecycle import (
        advance_experiment_to,
        derive_experiment_for_publication,
    )

    experiment_id = derive_experiment_for_publication(conn, publication_id)
    if experiment_id is None:
        result.skipped_reason = "publication has no experiment in its lineage"
        return result
    result.experiment_id = experiment_id

    # ── 1. Features ──────────────────────────────────────────────────────────
    try:
        result.features_extracted = ensure_content_features(conn, publication_id)
        conn.commit()
    except Exception as exc:  # noqa: BLE001 — incomplete lineage is expected sometimes
        result.errors.append(f"feature extraction: {exc}")
        logger.info(
            "outcome bridge: feature extraction skipped for publication %d: %s",
            publication_id,
            exc,
        )

    # ── 2. Fidelity ──────────────────────────────────────────────────────────
    try:
        result.fidelity_classification = ensure_execution_fidelity(conn, experiment_id)
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f"fidelity assessment: {exc}")
        logger.info("outcome bridge: fidelity assessment failed for %s: %s", experiment_id, exc)

    # ── 3. Outcome ───────────────────────────────────────────────────────────
    from app.intelligence.experiments.outcome_contract import OutcomeReadiness
    from app.intelligence.experiments.outcome_service import (
        evaluate_experiment_outcome,
        persist_outcome,
    )
    from app.intelligence.experiments.repository import get_experiment

    try:
        evaluation = evaluate_experiment_outcome(conn, experiment_id)
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f"outcome evaluation: {exc}")
        result.experiment_status = get_experiment(conn, experiment_id).status.value
        return result

    result.outcome_readiness = evaluation.readiness.value
    result.outcome_classification = (
        evaluation.classification.value if evaluation.classification else None
    )

    evaluable = evaluation.readiness in (
        OutcomeReadiness.EVALUABLE_MATURE,
        OutcomeReadiness.EVALUABLE_PROVISIONAL,
    )
    if evaluable:
        try:
            persist_outcome(conn, evaluation)
            conn.commit()
            result.outcome_persisted = True
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"outcome persist: {exc}")

    # ── 4. Ledger ────────────────────────────────────────────────────────────
    # `mature` is claimed only for EVALUABLE_MATURE. A provisional outcome is
    # real evidence but not mature evidence, and saying otherwise would be
    # exactly the fabrication this phase is meant to prevent.
    target = ExperimentStatus.observing
    if evaluation.readiness == OutcomeReadiness.EVALUABLE_MATURE and result.outcome_persisted:
        target = ExperimentStatus.analyzed

        pub = conn.execute(
            "SELECT channel_id FROM publications WHERE id = ?", (publication_id,)
        ).fetchone()
        cp_channel_id = pub["channel_id"] if pub else None
        if cp_channel_id and learning_has_consumed(
            conn, cp_channel_id=cp_channel_id, publication_id=publication_id
        ):
            target = ExperimentStatus.completed

    try:
        result.transitions = advance_experiment_to(
            conn,
            experiment_id,
            target,
            actor=actor,
            reason=f"outcome readiness={evaluation.readiness.value}",
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f"ledger advance: {exc}")

    result.experiment_status = get_experiment(conn, experiment_id).status.value

    if result.transitions or result.outcome_persisted:
        logger.info(
            "outcome bridge: experiment %s readiness=%s status=%s (persisted=%s)",
            experiment_id,
            result.outcome_readiness,
            result.experiment_status,
            result.outcome_persisted,
        )
    return result


__all__ = [
    "OutcomeBridgeResult",
    "ensure_content_features",
    "ensure_execution_fidelity",
    "learning_has_consumed",
    "run_outcome_bridge",
]
