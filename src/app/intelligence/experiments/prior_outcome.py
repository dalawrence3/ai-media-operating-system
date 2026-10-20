"""Phase 18H.A — Outcome-aware repetition guard: prior cluster outcome assessment.

Determines whether a canonical cluster's most recently *completed* experiment
produced evidence stable and mature enough to call POSITIVE or NEGATIVE, using
only rows already persisted by outcome_service.evaluate_experiment_outcome /
persist_outcome. Read-only; makes no LLM or provider calls and writes nothing.

Why this exists
----------------
The Phase 18H audit found that a cluster's own experiment_outcomes history was
never read back into that cluster's future re-selection: cluster_coverage_need
only counted *how many* experiments had run on a cluster, never *how they went*.
It also found that outcome_service._classify() has no materiality margin — any
nonzero delta flips positive_observation/negative_observation — and that the
first CRISPR experiment's classification moved from positive_observation to
negative_observation across two consecutive daily evaluations mainly because
the channel-baseline average it was compared against kept shifting as other
young publications' own numbers moved, not because CRISPR's own metric changed
(36.46 -> 35.73, essentially flat). A single reading is therefore not enough
evidence to act on. This module requires two independent, mature, directional
readings to agree before calling a cluster's history POSITIVE or NEGATIVE;
anything short of that is INCONCLUSIVE, which downstream planning treats the
same as "no penalty" — exactly the CRISPR case, where only one mature
directional reading existed at the moment the retest was authorized.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from app.intelligence.experiments.models import MaturityPolicy
from app.intelligence.experiments.outcome_contract import OutcomeClassification

# Two independent mature, directional (positive/negative) readings must agree
# before prior evidence is trusted enough to influence future selection. N=1
# is deliberately insufficient: the real Sept 1 CRISPR retest was authorized
# on exactly one such reading (a positive_observation that flipped negative
# the very next evaluation) — that is the failure mode this constant exists
# to close off. N=2 is the smallest N for which "agreement" is a meaningful
# concept at all; it introduces no new percentage or margin, only a repeat of
# the same discrete-classification labels outcome_service already writes.
STABILITY_MIN_AGREEING_OBSERVATIONS = 2

# A cluster's repeat guard (see planning_service._validation_repeat_block_reason
# and _score_candidate) lifts once this many hours have passed since the
# blocking experiment's own
# outcome was last confirmed mature ("mature" transition, falling back to
# "completed" if that timestamp is missing). Reused directly from that
# experiment's own MaturityPolicy.observation_window_hours (default 168h / 7
# days) — the same window the system already uses to define one full
# observation period for that experiment — rather than inventing a new
# constant for cluster-level cooldown.
_DEFAULT_COOLDOWN_HOURS = MaturityPolicy().observation_window_hours

# A confirmed NEGATIVE prior outcome is treated as costing the cluster as much
# coverage-credit as one additional already-run experiment would, reusing the
# existing cluster_coverage_need = 1 / (1 + n) shape (planning_service.py)
# rather than introducing a new multiplier scale.
NEGATIVE_REPEAT_COVERAGE_PENALTY_WEIGHT = 1.0

_DIRECTIONAL_VALUES = {
    OutcomeClassification.POSITIVE_OBSERVATION.value,
    OutcomeClassification.NEGATIVE_OBSERVATION.value,
}


class PriorOutcomeAssessment(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    INCONCLUSIVE = "inconclusive"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class PriorClusterOutcomeResult:
    """Read-only assessment of the most recent completed experiment in a cluster."""

    assessment: PriorOutcomeAssessment
    reason: str
    prior_experiment_id: str | None = None
    prior_experiment_completed_at: str | None = None
    usable_mature_observation_count: int = 0
    latest_classification: str | None = None
    classification_history: list[str] = field(default_factory=list)
    cooldown_expires_at: str | None = None
    # Whether `now` is still within the cooldown window anchored on the prior
    # experiment's own maturity, independent of `assessment` — a plain timing
    # fact set whenever cooldown_expires_at is known (NEGATIVE, INCONCLUSIVE,
    # or a completed-but-dataless UNAVAILABLE). Downstream planning combines
    # this with `assessment` itself to decide what "recent" should mean for
    # each case, rather than baking assessment-specific meaning in here.
    cooldown_active: bool = False
    # True only when assessment is NEGATIVE and cooldown_active — this, not
    # `assessment` alone, is what pre-18H.A-refinement planning code gates on,
    # so a stale negative reading eventually stops penalizing without a
    # separate "unban" action. Kept distinct from cooldown_active (rather than
    # derived inline at every call site) so existing NEGATIVE-only behavior
    # stays exactly as it was before the recent-repeat refinement.
    guard_active: bool = False


def assess_prior_cluster_outcome(
    conn: sqlite3.Connection,
    *,
    canonical_cluster_id: int,
    channel_id: int,
    exclude_experiment_id: str | None = None,
    as_of: str | None = None,
) -> PriorClusterOutcomeResult:
    """Assess the most recent completed experiment's outcome for one cluster.

    Uses only experiment_outcomes / experiments rows already persisted.
    Performs no writes and no provider/LLM calls.

    Args:
        conn: DB connection.
        canonical_cluster_id: The cluster to assess.
        channel_id: Restricts to this channel's own opportunities/experiments.
        exclude_experiment_id: Optional; excludes one experiment id from being
            treated as "the prior experiment" (useful when re-assessing a
            cluster that already contains the very candidate being scored).
        as_of: Optional ISO timestamp; only experiments completed at or before
            this time, and only outcome rows evaluated at or before this time,
            are considered. Defaults to "no restriction" (i.e. everything
            currently persisted), which is the correct behavior for live
            planning — nothing future-dated exists in the table anyway. Used
            by tests to deterministically replay a historical decision point.
    """
    now = as_of or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")

    prior_row = conn.execute(
        """SELECT e.id, e.completed_at, e.matured_at, e.maturity_policy_json
           FROM experiments e
           JOIN opportunities o ON o.id = e.opportunity_id
           WHERE o.canonical_cluster_id = ?
             AND o.channel_id = ?
             AND e.status = 'completed'
             AND e.completed_at IS NOT NULL
             AND e.completed_at <= ?
             AND (? IS NULL OR e.id != ?)
           ORDER BY e.completed_at DESC, e.id DESC
           LIMIT 1""",
        (
            canonical_cluster_id,
            channel_id,
            now,
            exclude_experiment_id,
            exclude_experiment_id,
        ),
    ).fetchone()

    if prior_row is None:
        return PriorClusterOutcomeResult(
            assessment=PriorOutcomeAssessment.UNAVAILABLE,
            reason="no completed experiment found for this cluster",
        )

    prior_experiment_id: str = prior_row["id"]
    completed_at: str = prior_row["completed_at"]

    # Cooldown is anchored on the prior experiment's own maturity, independent
    # of what its outcome history says — computed once here so every branch
    # below (including "completed but no data yet") can report it.
    policy = MaturityPolicy.from_json(prior_row["maturity_policy_json"] or "{}")
    cooldown_hours = policy.observation_window_hours or _DEFAULT_COOLDOWN_HOURS
    anchor = prior_row["matured_at"] or completed_at
    cooldown_expires_at = _add_hours(anchor, cooldown_hours) if anchor else None
    cooldown_active = cooldown_expires_at is not None and now <= cooldown_expires_at

    outcome_rows = conn.execute(
        """SELECT classification, is_mature, evaluated_at
           FROM experiment_outcomes
           WHERE experiment_id = ? AND evaluated_at <= ?
           ORDER BY evaluated_at ASC, id ASC""",
        (prior_experiment_id, now),
    ).fetchall()

    usable = [
        r
        for r in outcome_rows
        if bool(r["is_mature"]) and r["classification"] in _DIRECTIONAL_VALUES
    ]

    if not usable:
        return PriorClusterOutcomeResult(
            assessment=PriorOutcomeAssessment.UNAVAILABLE,
            reason=(
                f"experiment {prior_experiment_id} completed but has no mature, "
                "directional (positive/negative) outcome reading yet"
            ),
            prior_experiment_id=prior_experiment_id,
            prior_experiment_completed_at=completed_at,
            cooldown_expires_at=cooldown_expires_at,
            cooldown_active=cooldown_active,
        )

    history = [r["classification"] for r in usable]
    latest = history[-1]

    if len(usable) < STABILITY_MIN_AGREEING_OBSERVATIONS:
        return PriorClusterOutcomeResult(
            assessment=PriorOutcomeAssessment.INCONCLUSIVE,
            reason=(
                f"only {len(usable)} mature directional reading(s) available "
                f"(need {STABILITY_MIN_AGREEING_OBSERVATIONS} agreeing to call it "
                f"stable) — latest is {latest}"
            ),
            prior_experiment_id=prior_experiment_id,
            prior_experiment_completed_at=completed_at,
            usable_mature_observation_count=len(usable),
            latest_classification=latest,
            classification_history=history,
            cooldown_expires_at=cooldown_expires_at,
            cooldown_active=cooldown_active,
        )

    last_n = history[-STABILITY_MIN_AGREEING_OBSERVATIONS:]

    if all(c == OutcomeClassification.NEGATIVE_OBSERVATION.value for c in last_n):
        return PriorClusterOutcomeResult(
            assessment=PriorOutcomeAssessment.NEGATIVE,
            reason=(
                f"last {STABILITY_MIN_AGREEING_OBSERVATIONS} mature readings agree: "
                "negative_observation"
            ),
            prior_experiment_id=prior_experiment_id,
            prior_experiment_completed_at=completed_at,
            usable_mature_observation_count=len(usable),
            latest_classification=latest,
            classification_history=history,
            cooldown_expires_at=cooldown_expires_at,
            cooldown_active=cooldown_active,
            guard_active=cooldown_active,
        )

    if all(c == OutcomeClassification.POSITIVE_OBSERVATION.value for c in last_n):
        return PriorClusterOutcomeResult(
            assessment=PriorOutcomeAssessment.POSITIVE,
            reason=(
                f"last {STABILITY_MIN_AGREEING_OBSERVATIONS} mature readings agree: "
                "positive_observation"
            ),
            prior_experiment_id=prior_experiment_id,
            prior_experiment_completed_at=completed_at,
            usable_mature_observation_count=len(usable),
            latest_classification=latest,
            classification_history=history,
            cooldown_expires_at=cooldown_expires_at,
            cooldown_active=cooldown_active,
        )

    return PriorClusterOutcomeResult(
        assessment=PriorOutcomeAssessment.INCONCLUSIVE,
        reason=(
            f"last {STABILITY_MIN_AGREEING_OBSERVATIONS} mature readings disagree "
            f"({', '.join(last_n)}) — treated as unstable, not negative"
        ),
        prior_experiment_id=prior_experiment_id,
        prior_experiment_completed_at=completed_at,
        usable_mature_observation_count=len(usable),
        latest_classification=latest,
        classification_history=history,
        cooldown_expires_at=cooldown_expires_at,
        cooldown_active=cooldown_active,
    )


def _add_hours(iso_ts: str, hours: float) -> str | None:
    try:
        ts = iso_ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return (dt + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")
    except (ValueError, TypeError):
        return None
