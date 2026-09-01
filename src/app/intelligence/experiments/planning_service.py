"""Phase 14D — Experiment planning service.

Scores eligible Opportunity candidates and builds a portfolio plan.

Contracts:
- No YouTube calls
- No LLM calls
- No content generation
- No Experiment row creation (planning artifacts only)
- No automatic treatment value assignment
- No autonomous experiment selection (plan is advisory; humans approve)
- Reads from: opportunities, opportunity_scores, experiments, cross_publication tables
- Writes to: experiment_planning_runs, experiment_candidate_scores, experiment_selection_decisions
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import UTC, datetime

from app.intelligence.experiments.eligibility import ExperimentEligibilityAssessment
from app.intelligence.experiments.planning import (
    SAFE_CONTROLLABLE_FACTORS,
    CandidateScoreComponents,
    ExperimentCandidate,
    ExperimentPortfolioPlan,
    ExperimentSelectionDecision,
    IntendedFactor,
    PlanningIntent,
    PlanningPolicy,
)


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _compute_feature_coverage_gap(feature_coverage: dict[str, dict]) -> float:
    """Fraction of safe controllable factors that have no tested buckets (0.0–1.0)."""
    if not SAFE_CONTROLLABLE_FACTORS:
        return 0.0
    untested = sum(1 for name in SAFE_CONTROLLABLE_FACTORS if not feature_coverage.get(name))
    return untested / len(SAFE_CONTROLLABLE_FACTORS)


def _build_hypothesis_sketch(normalized_topic: str, planning_intent: PlanningIntent) -> str:
    topic = normalized_topic or "this topic"
    if planning_intent == PlanningIntent.EXPLOITATION:
        return f"Exploit known signal for '{topic}' to optimize audience retention"
    elif planning_intent == PlanningIntent.VALIDATION:
        return f"Validate signal strength for '{topic}' with updated evidence"
    else:
        return f"Explore audience and feature signal for '{topic}'"


def _select_treatment_factor(
    feature_coverage: dict[str, dict],
    policy: PlanningPolicy,
) -> IntendedFactor | None:
    """Pick the safe factor most in need of coverage (least tested).

    Returns at most one treatment factor. intended_value is always None —
    the operator must decide the actual treatment value.
    """
    best_name: str | None = None
    best_gap = -1.0
    for name in SAFE_CONTROLLABLE_FACTORS:
        bucket_count = len(feature_coverage.get(name, {}))
        gap = 1.0 / (1 + bucket_count)
        if gap > best_gap:
            best_gap = gap
            best_name = name
    if best_name is None:
        return None
    return IntendedFactor(
        factor_name=best_name,
        factor_role="treatment",
        intended_value=None,
    )


def _cp_channel_id_for(conn: sqlite3.Connection, channel_id: int) -> str | None:
    """Map an intelligence channel id to its control-plane UUID.

    Learning evidence (channel_performance_baselines,
    feature_performance_observations) is keyed by the control-plane UUID
    while planning runs in the intelligence integer namespace, so every
    read of learning evidence from here has to cross the bridge. Returns
    None when the channel is not bridged, in which case the planner sees no
    channel evidence rather than another channel's.
    """
    try:
        from app.intelligence.channel_bridge import (
            get_cp_channel_id_for_intelligence_channel,
        )

        return get_cp_channel_id_for_intelligence_channel(conn, channel_id)
    except Exception:
        return None


def _channel_evidence_maturity(
    conn: sqlite3.Connection, cp_channel_id: str, metric_name: str
) -> str:
    """This channel's own evidence maturity for the planner's target metric.

    Read straight from the Phase 12C baseline the learning system already
    computes, so the thresholds (insufficient / exploratory / directional /
    actionable) stay defined in exactly one place. No baseline means no
    evidence — 'insufficient', never an optimistic default.
    """
    try:
        from app.learning.cross_publication import get_channel_baselines

        baselines = get_channel_baselines(conn, channel_id=cp_channel_id, metric_name=metric_name)
    except Exception:
        return "insufficient"
    return baselines[0].sample_maturity if baselines else "insufficient"


def _score_candidate(
    *,
    assessment: ExperimentEligibilityAssessment,
    cluster_id: int | None,
    existing_cluster_counts: dict[int | None, int],
    feature_coverage: dict[str, dict],
    policy: PlanningPolicy,
    planning_intent: PlanningIntent,
    composite_score: float | None,
    channel_evidence_maturity: str = "insufficient",
) -> CandidateScoreComponents:
    """Compute all score components for one candidate.

    All components are direction-normalized: higher is better.
    Confidence is NOT re-applied — composite_score already incorporates it
    through the Phase 13F scoring pipeline.

    The two evidence terms come from genuinely different sources, which is
    the whole point of the strategy profile's market/channel weight split:
      opportunity_attractiveness — what the wider market says (Phase 13F)
      internal_evidence_strength — what this channel's own published videos
                                   say (Phase 12C cross-publication baselines)
    """
    # 1. opportunity_attractiveness: composite_score as-is (no re-derivation)
    opportunity_attractiveness = composite_score

    # 2. internal_evidence_strength from this channel's OWN measured evidence.
    #    This previously read assessment.signal_maturity, which is market
    #    cluster maturity — so the "channel evidence" weight was really a
    #    second market weight and no amount of published Orvella video could
    #    change the ranking. A channel with nothing published has
    #    'insufficient' maturity and therefore 0.0 here, which is what keeps
    #    bootstrap leaning on market intelligence.
    internal_evidence_strength = policy.maturity_strength.get(channel_evidence_maturity, 0.0)

    # 3. production_feasibility: 1.0 for all assessments that passed eligibility
    production_feasibility = 1.0

    # 4. uncertainty = 1 - signal_confidence (higher uncertainty → more to learn)
    uncertainty = 1.0 - (assessment.signal_confidence or 0.0)
    uncertainty = min(1.0, max(0.0, uncertainty))

    # 5. cluster_coverage_need: smooth decay based on how many experiments already on cluster
    cluster_count = existing_cluster_counts.get(cluster_id, 0)
    cluster_coverage_need = 1.0 / (1 + cluster_count)

    # 6. information_gain: what we would learn from running this experiment
    ig_cluster_newness = 1.0 if cluster_count == 0 else 0.0
    ig_maturity_gap = 1.0 - internal_evidence_strength
    ig_feature_gap = _compute_feature_coverage_gap(feature_coverage)
    information_gain = (
        policy.w_ig_cluster_newness * ig_cluster_newness
        + policy.w_ig_maturity_gap * ig_maturity_gap
        + policy.w_ig_feature_coverage * ig_feature_gap
    )
    information_gain = min(1.0, max(0.0, information_gain))

    # 7. exploitation_value (only meaningful when composite_score is known)
    if opportunity_attractiveness is not None:
        exploitation_value: float | None = (
            policy.w_exploitation_attractiveness * opportunity_attractiveness
            + policy.w_exploitation_evidence * internal_evidence_strength
            + policy.w_exploitation_feasibility * production_feasibility
        )
        exploitation_value = min(1.0, max(0.0, exploitation_value))
    else:
        exploitation_value = None

    # 8. exploration_value
    exploration_value = (
        policy.w_exploration_information_gain * information_gain
        + policy.w_exploration_uncertainty * uncertainty
        + policy.w_exploration_cluster_coverage * cluster_coverage_need
    )
    exploration_value = min(1.0, max(0.0, exploration_value))

    # 9. final_planning_score: use pool-appropriate value
    if planning_intent == PlanningIntent.EXPLOITATION and exploitation_value is not None:
        final_planning_score = exploitation_value
    else:
        final_planning_score = exploration_value

    return CandidateScoreComponents(
        opportunity_attractiveness=opportunity_attractiveness,
        exploitation_value=exploitation_value,
        exploration_value=exploration_value,
        information_gain=information_gain,
        internal_evidence_strength=internal_evidence_strength,
        uncertainty=uncertainty,
        cluster_coverage_need=cluster_coverage_need,
        production_feasibility=production_feasibility,
        final_planning_score=final_planning_score,
    )


def _select_portfolio(
    candidates: list[ExperimentCandidate],
    existing_cluster_counts: dict[int | None, int],
    policy: PlanningPolicy,
) -> list[ExperimentSelectionDecision]:
    """Select candidates into exploitation and exploration slots with cluster diversity guards.

    Selection rules:
    - GENERAL_ELIGIBLE candidates are eligible for both exploitation and exploration pools.
    - EXPLORATION_ONLY candidates are eligible for exploration pool only.
    - Exploitation pool is filled first, ranked by exploitation_value DESC.
    - Exploration pool is filled second, ranked by exploration_value DESC.
    - A candidate already selected in exploitation is not double-counted in exploration.
    - Cluster diversity: max floor(max_cluster_share * total_slots) from any one cluster.
    - Consecutive diversity: at most max_consecutive_same_cluster in a row.
    - Tiebreak: lower opportunity_id wins (deterministic, stable sort).
    """
    total_slots = policy.max_exploitation_slots + policy.max_exploration_slots
    max_from_cluster = max(1, int(policy.max_cluster_share * total_slots))

    # Exploitation pool: only candidates the planning service explicitly routed to exploitation.
    # New-cluster candidates have planning_intent=EXPLORATION even when general_eligible, so
    # they must not fill exploitation slots — that would contradict the market-exploration routing.
    exploitable = [
        c
        for c in candidates
        if c.eligibility_classification == "general_eligible"
        and c.planning_intent == PlanningIntent.EXPLOITATION
    ]
    # exploration pool = all eligible candidates (exploration_only candidates also eligible)
    explorable = list(candidates)

    exploitable_sorted = sorted(
        exploitable,
        key=lambda c: (-(c.score.exploitation_value or 0.0), c.opportunity_id),
    )
    explorable_sorted = sorted(
        explorable,
        key=lambda c: (-(c.score.exploration_value or 0.0), c.opportunity_id),
    )

    selected_exploitation: list[tuple[ExperimentCandidate, int]] = []
    selected_exploration: list[tuple[ExperimentCandidate, int]] = []
    selected_ids: set[int] = set()
    plan_sequence: list[ExperimentCandidate] = []
    cluster_counts_in_plan: dict[int | None, int] = {}

    def _diversity_block(candidate: ExperimentCandidate) -> str | None:
        cluster_id = candidate.canonical_cluster_id
        if cluster_counts_in_plan.get(cluster_id, 0) >= max_from_cluster:
            return "cluster_share_exceeded"
        recent = plan_sequence[-policy.max_consecutive_same_cluster :]
        if len(recent) >= policy.max_consecutive_same_cluster and all(
            c.canonical_cluster_id == cluster_id for c in recent
        ):
            return "consecutive_cluster_exceeded"
        return None

    def _record_selection(candidate: ExperimentCandidate) -> None:
        plan_sequence.append(candidate)
        selected_ids.add(candidate.opportunity_id)
        cid = candidate.canonical_cluster_id
        cluster_counts_in_plan[cid] = cluster_counts_in_plan.get(cid, 0) + 1

    # Fill exploitation slots
    for candidate in exploitable_sorted:
        if len(selected_exploitation) >= policy.max_exploitation_slots:
            break
        if _diversity_block(candidate):
            continue
        rank = len(selected_exploitation) + 1
        selected_exploitation.append((candidate, rank))
        _record_selection(candidate)

    # Fill exploration slots (skip already selected in exploitation)
    for candidate in explorable_sorted:
        if candidate.opportunity_id in selected_ids:
            continue
        if len(selected_exploration) >= policy.max_exploration_slots:
            break
        if _diversity_block(candidate):
            continue
        rank = len(selected_exploration) + 1
        selected_exploration.append((candidate, rank))
        _record_selection(candidate)

    # Build decisions
    decisions: list[ExperimentSelectionDecision] = []

    for candidate, rank in selected_exploitation:
        score_str = (
            f"{candidate.score.exploitation_value:.3f}"
            if candidate.score.exploitation_value is not None
            else "N/A"
        )
        decisions.append(
            ExperimentSelectionDecision(
                opportunity_id=candidate.opportunity_id,
                selected=True,
                rank_in_pool=rank,
                pool_type="exploitation",
                selection_reason=(
                    f"top-{rank} in exploitation pool (exploitation_value={score_str})"
                ),
                deferral_reason=None,
                is_validation_repeat=(
                    existing_cluster_counts.get(candidate.canonical_cluster_id, 0) > 0
                ),
                candidate=candidate,
            )
        )

    for candidate, rank in selected_exploration:
        score_str = f"{candidate.score.exploration_value:.3f}"
        decisions.append(
            ExperimentSelectionDecision(
                opportunity_id=candidate.opportunity_id,
                selected=True,
                rank_in_pool=rank,
                pool_type="exploration",
                selection_reason=f"top-{rank} in exploration pool (exploration_value={score_str})",
                deferral_reason=None,
                is_validation_repeat=(
                    existing_cluster_counts.get(candidate.canonical_cluster_id, 0) > 0
                ),
                candidate=candidate,
            )
        )

    # Deferred: anything not selected
    for candidate in candidates:
        if candidate.opportunity_id in selected_ids:
            continue
        expl_full = len(selected_exploitation) >= policy.max_exploitation_slots
        expl_only = candidate.eligibility_classification == "exploration_only"
        if expl_only:
            reason = "exploration_slots_full"
        elif expl_full and len(selected_exploration) >= policy.max_exploration_slots:
            reason = "all_slots_full"
        elif expl_full:
            reason = "exploitation_slots_full"
        else:
            reason = "cluster_diversity_constraint"
        decisions.append(
            ExperimentSelectionDecision(
                opportunity_id=candidate.opportunity_id,
                selected=False,
                rank_in_pool=None,
                pool_type=None,
                selection_reason="",
                deferral_reason=reason,
                is_validation_repeat=(
                    existing_cluster_counts.get(candidate.canonical_cluster_id, 0) > 0
                ),
                candidate=candidate,
            )
        )

    return decisions


def _plan_input_hash(
    channel_id: int,
    assessments: list[ExperimentEligibilityAssessment],
    policy: PlanningPolicy,
) -> str:
    payload = {
        "channel_id": channel_id,
        "policy": policy.to_json(),
        "assessments": sorted(
            [
                {
                    "opportunity_id": a.opportunity_id,
                    "classification": a.classification.value,
                }
                for a in assessments
            ],
            key=lambda x: x["opportunity_id"],
        ),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:32]


def _candidate_input_hash(candidate: ExperimentCandidate) -> str:
    payload = {
        "opportunity_id": candidate.opportunity_id,
        "planning_intent": candidate.planning_intent.value,
        "primary_target_metric": candidate.primary_target_metric,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:32]


def _persist_plan(conn: sqlite3.Connection, plan: ExperimentPortfolioPlan) -> None:
    """Persist all planning artifacts to the DB. Does NOT commit — caller commits."""
    status = "dry_run" if plan.is_dry_run else "completed"
    conn.execute(
        """INSERT INTO experiment_planning_runs
           (id, channel_id, status, policy_snapshot_json, eligible_count,
            exploration_only_count, general_eligible_count, selected_count,
            deferred_count, input_hash)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            plan.run_id,
            plan.channel_id,
            status,
            plan.policy_snapshot_json,
            plan.eligible_count,
            plan.exploration_only_count,
            plan.general_eligible_count,
            plan.selected_count,
            plan.deferred_count,
            plan.input_hash,
        ),
    )

    for decision in plan.decisions:
        c = decision.candidate
        treatment_json = json.dumps(
            [
                {
                    "factor_name": f.factor_name,
                    "factor_role": f.factor_role,
                    "intended_value": f.intended_value,
                }
                for f in c.intended_treatment_factors
            ]
        )
        controlled_json = json.dumps(
            [
                {
                    "factor_name": f.factor_name,
                    "factor_role": f.factor_role,
                    "intended_value": f.intended_value,
                }
                for f in c.controlled_factors
            ]
        )
        candidate_hash = _candidate_input_hash(c)

        cur = conn.execute(
            """INSERT OR IGNORE INTO experiment_candidate_scores
               (planning_run_id, opportunity_id, channel_id, canonical_cluster_id,
                eligibility_classification, planning_intent, experiment_type,
                primary_target_metric, primary_metric_direction, hypothesis_sketch,
                intended_treatment_factors_json, controlled_factors_json,
                feature_change_risk, opportunity_attractiveness, exploitation_value,
                exploration_value, information_gain, internal_evidence_strength,
                uncertainty, cluster_coverage_need, production_feasibility,
                final_planning_score, input_hash, semantic_fit_disposition)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                plan.run_id,
                c.opportunity_id,
                c.channel_id,
                c.canonical_cluster_id,
                c.eligibility_classification,
                c.planning_intent.value,
                c.experiment_type,
                c.primary_target_metric,
                c.primary_metric_direction,
                c.hypothesis_sketch,
                treatment_json,
                controlled_json,
                c.feature_change_risk,
                c.score.opportunity_attractiveness,
                c.score.exploitation_value,
                c.score.exploration_value,
                c.score.information_gain,
                c.score.internal_evidence_strength,
                c.score.uncertainty,
                c.score.cluster_coverage_need,
                c.score.production_feasibility,
                c.score.final_planning_score,
                candidate_hash,
                c.semantic_fit_disposition,
            ),
        )
        candidate_score_id = cur.lastrowid
        if not candidate_score_id:
            row = conn.execute(
                "SELECT id FROM experiment_candidate_scores "
                "WHERE planning_run_id=? AND input_hash=?",
                (plan.run_id, candidate_hash),
            ).fetchone()
            candidate_score_id = row["id"]

        conn.execute(
            """INSERT INTO experiment_selection_decisions
               (planning_run_id, candidate_score_id, opportunity_id, selected,
                rank_in_pool, pool_type, selection_reason, deferral_reason,
                is_validation_repeat)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                plan.run_id,
                candidate_score_id,
                decision.opportunity_id,
                1 if decision.selected else 0,
                decision.rank_in_pool,
                decision.pool_type,
                decision.selection_reason,
                decision.deferral_reason,
                1 if decision.is_validation_repeat else 0,
            ),
        )

    conn.commit()


def build_portfolio_plan(
    conn: sqlite3.Connection,
    channel_id: int,
    assessments: list[ExperimentEligibilityAssessment],
    *,
    policy: PlanningPolicy | None = None,
    dry_run: bool = False,
) -> ExperimentPortfolioPlan:
    """Build an experiment portfolio plan from a list of eligibility assessments.

    Reads from the DB to load opportunity metadata and scoring history.
    Optionally persists planning artifacts (experiment_planning_runs,
    experiment_candidate_scores, experiment_selection_decisions).

    Never:
    - Calls YouTube, LLMs, or any external service
    - Creates Experiment rows
    - Assigns treatment values
    - Automatically selects or approves an experiment for production

    Args:
        conn: DB connection (schema v38+).
        channel_id: The channel being planned for.
        assessments: Pre-computed eligibility assessments (from eligibility_service).
        policy: Optional planning policy. Defaults to the channel's active
            Channel Strategy Profile (Phase 17E) if one exists and validates,
            else PlanningPolicy.v1().
        dry_run: If True, compute the plan but do not persist to DB.

    Returns:
        ExperimentPortfolioPlan with all decisions (selected and deferred).
    """
    if policy is None:
        from app.intelligence.experiments.strategy_policy import load_policy_for_channel

        policy = load_policy_for_channel(conn, channel_id)

    run_id = str(uuid.uuid4())
    planned_at = _utc_now()
    input_hash = _plan_input_hash(channel_id, assessments, policy)
    policy_json = policy.to_json()

    # Filter to plannable classifications only
    eligible = [
        a for a in assessments if a.classification.value in ("exploration_only", "general_eligible")
    ]
    exploration_only_count = sum(
        1 for a in eligible if a.classification.value == "exploration_only"
    )
    general_eligible_count = sum(
        1 for a in eligible if a.classification.value == "general_eligible"
    )

    if not eligible:
        plan = ExperimentPortfolioPlan(
            channel_id=channel_id,
            run_id=run_id,
            planned_at=planned_at,
            is_dry_run=dry_run,
            eligible_count=0,
            exploration_only_count=0,
            general_eligible_count=0,
            selected_count=0,
            deferred_count=0,
            decisions=[],
            policy_snapshot_json=policy_json,
            input_hash=input_hash,
        )
        if not dry_run:
            _persist_plan(conn, plan)
        return plan

    # Load this channel's own learning evidence.
    #
    # Both of these live in the control-plane channel namespace (UUID), not
    # the intelligence namespace (integer) this function is called with.
    # Passing str(channel_id) — "1" — matched nothing, so coverage was always
    # empty and channel evidence never reached ranking at all: every factor
    # looked untested and every candidate scored identically on the
    # evidence terms. The bridge lookup is what makes learning visible here.
    cp_channel_id = _cp_channel_id_for(conn, channel_id)
    feature_coverage: dict[str, dict] = {}
    channel_evidence_maturity = "insufficient"
    if cp_channel_id is not None:
        try:
            from app.learning.cross_publication import get_exploration_coverage

            feature_coverage = get_exploration_coverage(conn, channel_id=cp_channel_id)
        except Exception:
            feature_coverage = {}
        channel_evidence_maturity = _channel_evidence_maturity(
            conn, cp_channel_id, policy.primary_target_metric
        )

    # Load existing cluster experiment counts (active experiments only — cancelled/draft-cancelled
    # did not produce real production data, so their cluster should not block market exploration).
    cluster_rows = conn.execute(
        """SELECT o.canonical_cluster_id, COUNT(*) as cnt
           FROM experiments e
           JOIN opportunities o ON o.id = e.opportunity_id
           WHERE e.channel_id = ? AND e.status != 'cancelled'
           GROUP BY o.canonical_cluster_id""",
        (channel_id,),
    ).fetchall()
    existing_cluster_counts: dict[int | None, int] = {
        row["canonical_cluster_id"]: row["cnt"] for row in cluster_rows
    }

    # Build and score each candidate
    candidates: list[ExperimentCandidate] = []
    for assessment in eligible:
        opp_row = conn.execute(
            "SELECT canonical_cluster_id, normalized_topic FROM opportunities WHERE id = ?",
            (assessment.opportunity_id,),
        ).fetchone()
        if opp_row is None:
            continue

        cluster_id: int | None = opp_row["canonical_cluster_id"]
        normalized_topic: str = opp_row["normalized_topic"] or ""

        # Latest opportunity score (any scoring policy)
        score_row = conn.execute(
            """SELECT composite_score FROM opportunity_scores
               WHERE opportunity_id = ?
               ORDER BY scored_at DESC, id DESC
               LIMIT 1""",
            (assessment.opportunity_id,),
        ).fetchone()
        composite_score: float | None = score_row["composite_score"] if score_row else None

        # cluster_is_new must be evaluated BEFORE planning intent: market signal
        # maturity measures YouTube audience evidence, not Orvella's internal
        # production evidence.  A cluster with "actionable" market signals but
        # no prior Orvella experiments is a new cluster — market exploration first.
        cluster_is_new = existing_cluster_counts.get(cluster_id, 0) == 0

        # Determine planning intent based on eligibility and evidence strength.
        #
        # `evidence_strength` is Orvella's OWN measured evidence for this
        # channel (Phase 12C cross-publication baselines), not the market
        # signal maturity that assessment.signal_maturity carries. The two
        # were previously the same value, which meant a strong YouTube-wide
        # signal could push the channel into exploitation before Orvella had
        # published anything at all. During bootstrap this is 0.0, so the
        # planner explores — exactly the intended behaviour.
        evidence_strength = policy.maturity_strength.get(channel_evidence_maturity, 0.0)
        if assessment.classification.value == "exploration_only":
            planning_intent = PlanningIntent.EXPLORATION
        elif cluster_is_new:
            # New cluster: market signal maturity cannot substitute for Orvella's
            # production feature evidence.  Always explore first.
            planning_intent = PlanningIntent.EXPLORATION
        elif evidence_strength >= 0.67:
            planning_intent = PlanningIntent.EXPLOITATION
        else:
            planning_intent = PlanningIntent.EXPLORATION

        score = _score_candidate(
            assessment=assessment,
            cluster_id=cluster_id,
            existing_cluster_counts=existing_cluster_counts,
            feature_coverage=feature_coverage,
            policy=policy,
            planning_intent=planning_intent,
            composite_score=composite_score,
            channel_evidence_maturity=channel_evidence_maturity,
        )

        # Propose at most one treatment factor.
        # For pure market exploration (untested cluster + EXPLORATION intent), propose no feature
        # treatment — changing both the topic cluster AND a production feature simultaneously
        # confounds the experiment: results cannot be attributed to either variable.
        is_pure_market_exploration = (
            planning_intent == PlanningIntent.EXPLORATION and cluster_is_new
        )
        if is_pure_market_exploration:
            treatment_factors = []
        else:
            treatment = _select_treatment_factor(feature_coverage, policy)
            treatment_factors = [treatment] if treatment else []

        candidate = ExperimentCandidate(
            opportunity_id=assessment.opportunity_id,
            channel_id=channel_id,
            canonical_cluster_id=cluster_id,
            eligibility_classification=assessment.classification.value,
            planning_intent=planning_intent,
            experiment_type="exploitation"
            if planning_intent == PlanningIntent.EXPLOITATION
            else "exploration",
            primary_target_metric=policy.primary_target_metric,
            primary_metric_direction=policy.primary_metric_direction,
            hypothesis_sketch=_build_hypothesis_sketch(normalized_topic, planning_intent),
            intended_treatment_factors=treatment_factors,
            controlled_factors=[],
            feature_change_risk="low",
            score=score,
            semantic_fit_disposition=assessment.semantic_fit_disposition,
        )
        candidates.append(candidate)

    decisions = _select_portfolio(candidates, existing_cluster_counts, policy)
    selected_count = sum(1 for d in decisions if d.selected)
    deferred_count = sum(1 for d in decisions if not d.selected)

    plan = ExperimentPortfolioPlan(
        channel_id=channel_id,
        run_id=run_id,
        planned_at=planned_at,
        is_dry_run=dry_run,
        eligible_count=len(eligible),
        exploration_only_count=exploration_only_count,
        general_eligible_count=general_eligible_count,
        selected_count=selected_count,
        deferred_count=deferred_count,
        decisions=decisions,
        policy_snapshot_json=policy_json,
        input_hash=input_hash,
    )

    if not dry_run:
        _persist_plan(conn, plan)

    return plan
