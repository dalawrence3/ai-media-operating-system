"""Phase 14E — Experiment strategy brief service.

Entry point: create_strategy_brief()

Contracts:
- No YouTube calls
- No LLM calls
- No content generation
- No Experiment row creation (that is downstream, human-gated)
- No automatic treatment value assignment (intended_value always None)
- Read from: experiment_selection_decisions, opportunities, channel_profile_versions,
             voice_profiles, feature_performance_observations
- Write to: experiment_strategy_briefs

Eligibility recheck: before creating a brief the service re-checks the
current eligibility classification from the DB.  If the opportunity has
become INELIGIBLE or STALE since the planning run, brief creation is blocked.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid

from app.intelligence.experiments.planning import (
    SAFE_CONTROLLABLE_FACTORS,
    PlanningIntent,
    PlanningPolicy,
)
from app.intelligence.experiments.strategy_brief import (
    BriefPlanningIntent,
    ConfoundingRisk,
    ContentConstraints,
    ControlledFactorBaseline,
    ExperimentStrategyBrief,
    FactorAutonomy,
    TreatmentFactorSpec,
)
from app.learning.constants import (
    NARRATION_PACE_SPEAKING_RATE_MAX,
    NARRATION_PACE_SPEAKING_RATE_MIN,
)
from app.visuals.policy import STYLE_BALANCED


def get_factor_autonomy(factor_name: str) -> FactorAutonomy:
    """Return the autonomy classification for a safe controllable factor.

    Reads from the unified SAFE_CONTROLLABLE_FACTORS registry in planning.py.
    Factors absent from the registry default to MANUAL_ONLY.
    """
    spec = SAFE_CONTROLLABLE_FACTORS.get(factor_name)
    return spec.autonomy if spec is not None else FactorAutonomy.MANUAL_ONLY


# ── Blocked eligibility states ────────────────────────────────────────────────

_BLOCKED_ELIGIBILITY = frozenset({"ineligible", "stale"})


# ── Internal helpers ──────────────────────────────────────────────────────────


def _utc_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _map_brief_planning_intent(
    planning_intent: str,
    cluster_is_new: bool,
    is_validation_repeat: bool,
) -> BriefPlanningIntent:
    """Map planning.PlanningIntent + context → BriefPlanningIntent."""
    if is_validation_repeat and planning_intent == PlanningIntent.EXPLORATION.value:
        return BriefPlanningIntent.VALIDATION
    if planning_intent == PlanningIntent.EXPLOITATION.value:
        return BriefPlanningIntent.EXPLOITATION
    if planning_intent == PlanningIntent.EXPLORATION.value:
        if cluster_is_new:
            return BriefPlanningIntent.MARKET_EXPLORATION
        return BriefPlanningIntent.FEATURE_EXPLORATION
    # VALIDATION intent from planner (not currently used but handle it)
    return BriefPlanningIntent.VALIDATION


def _compute_confounding_risk(
    brief_intent: BriefPlanningIntent,
    treatment_factor_count: int,
) -> ConfoundingRisk:
    """Compute confounding risk for the experiment brief.

    Rules:
    - 2+ treatment factors: HIGH (multiple independent variables)
    - 0 treatment factors: LOW (single variable: the cluster/topic)
    - 1 treatment factor on untested cluster: HIGH (violates Phase 14D.1 rule;
      should not happen, but guarded here for defence-in-depth)
    - 1 treatment factor, FEATURE_EXPLORATION or EXPLOITATION: LOW
    - 1 treatment factor, MARKET_EXPLORATION: HIGH (shouldn't happen)
    """
    if treatment_factor_count >= 2:
        return ConfoundingRisk.HIGH
    if treatment_factor_count == 0:
        return ConfoundingRisk.LOW
    # treatment_factor_count == 1
    if brief_intent == BriefPlanningIntent.MARKET_EXPLORATION:
        # Cluster is new + feature varied = confound
        return ConfoundingRisk.HIGH
    return ConfoundingRisk.LOW


def _build_hypothesis(
    brief_intent: BriefPlanningIntent,
    canonical_topic: str,
    target_metric: str,
    treatment_factors: list[TreatmentFactorSpec],
) -> str:
    """Build a deterministic hypothesis string from template."""
    topic = canonical_topic or "this topic"
    metric = target_metric or "the target metric"

    if brief_intent == BriefPlanningIntent.MARKET_EXPLORATION:
        return (
            f"Publishing content on '{topic}' will produce measurable signal on "
            f"'{metric}', establishing baseline data for this cluster."
        )
    if brief_intent == BriefPlanningIntent.FEATURE_EXPLORATION:
        factor = treatment_factors[0].factor_name if treatment_factors else "the treatment factor"
        return (
            f"Varying '{factor}' on '{topic}' content will produce a measurable "
            f"difference in '{metric}' compared to channel baseline."
        )
    if brief_intent == BriefPlanningIntent.EXPLOITATION:
        factor = treatment_factors[0].factor_name if treatment_factors else "the treatment factor"
        return (
            f"Applying '{factor}' on '{topic}' content will improve '{metric}' "
            f"above channel baseline, consistent with prior directional evidence."
        )
    # VALIDATION
    return (
        f"Re-testing '{topic}' with updated conditions will confirm or revise "
        f"the prior '{metric}' signal."
    )


def _build_treatment_factor_spec(factor_name: str) -> TreatmentFactorSpec:
    """Build a TreatmentFactorSpec for a named safe controllable factor."""
    spec = SAFE_CONTROLLABLE_FACTORS.get(factor_name)
    autonomy = get_factor_autonomy(factor_name)
    if spec is None:
        return TreatmentFactorSpec(
            factor_name=factor_name,
            value_type="unknown",
            autonomy=FactorAutonomy.MANUAL_ONLY,
        )
    return TreatmentFactorSpec(
        factor_name=spec.feature_name,
        value_type=spec.value_type,
        autonomy=autonomy,
        safe_range_min=spec.safe_range_min,
        safe_range_max=spec.safe_range_max,
        safe_values=tuple(spec.safe_values) if spec.safe_values else None,
        intended_value=None,  # always None; operator decides
    )


def _resolve_voice_profile_baseline(
    conn: sqlite3.Connection,
    channel_id: int,
) -> str | None:
    """Return the most recently used speaking_rate for this channel, or None.

    Reads from narration_runs (which store speaking_rate directly) filtered
    by the channel via the plan_id → production_plans → topics → opportunities chain.
    Returns None when the channel has no completed narration runs.
    """
    row = conn.execute(
        """
        SELECT nr.speaking_rate
        FROM narration_runs nr
        JOIN production_plans pp ON pp.id = nr.plan_id
        JOIN topics t ON t.id = pp.topic_id
        JOIN opportunities o ON o.id = t.promoted_opportunity_id
        WHERE o.channel_id = ?
          AND nr.status = 'completed'
        ORDER BY nr.id DESC
        LIMIT 1
        """,
        (channel_id,),
    ).fetchone()
    if row is None:
        return None
    return str(row["speaking_rate"])


def _resolve_controlled_baselines(
    conn: sqlite3.Connection,
    channel_id: int,
    treatment_factor_names: list[str],
    channel_profile: dict,
) -> list[ControlledFactorBaseline]:
    """Build controlled factor baseline records for all non-treatment safe factors."""
    controlled: list[ControlledFactorBaseline] = []

    # The speaking_rate baseline comes from the most recent narration run.
    speaking_rate_baseline: str | None = None
    speaking_rate_fetched = False

    for factor_name, _spec in SAFE_CONTROLLABLE_FACTORS.items():
        if factor_name in treatment_factor_names:
            continue  # treatment factors are not controlled

        if factor_name == "narration_speaking_rate":
            if not speaking_rate_fetched:
                speaking_rate_baseline = _resolve_voice_profile_baseline(conn, channel_id)
                speaking_rate_fetched = True
            speaking_rate_range = (
                NARRATION_PACE_SPEAKING_RATE_MAX - NARRATION_PACE_SPEAKING_RATE_MIN
            )
            controlled.append(
                ControlledFactorBaseline(
                    factor_name=factor_name,
                    baseline_value=speaking_rate_baseline,
                    baseline_source="voice_profile",
                    tolerance=f"±{speaking_rate_range:.2f}",
                )
            )
        elif factor_name in ("has_hook", "has_cta", "render_caption_burn_in"):
            # Boolean factors: no channel-profile baseline; observe from feature history
            controlled.append(
                ControlledFactorBaseline(
                    factor_name=factor_name,
                    baseline_value=None,
                    baseline_source="unknown",
                    tolerance=None,
                )
            )
        elif factor_name == "narration_voice_id":
            controlled.append(
                ControlledFactorBaseline(
                    factor_name=factor_name,
                    baseline_value=None,
                    baseline_source="voice_profile",
                    tolerance=None,
                )
            )
        elif factor_name == "visual_style":
            # A DECLARED baseline, not "unknown": when no experiment assigns a
            # visual treatment the pipeline genuinely runs the balanced preset,
            # so that is the control condition. Phase 18D established that a
            # controlled factor with no declared baseline reads as an
            # observation failure rather than as a control.
            controlled.append(
                ControlledFactorBaseline(
                    factor_name=factor_name,
                    baseline_value=STYLE_BALANCED,
                    baseline_source="pipeline_default",
                    tolerance=None,
                )
            )
        elif factor_name in ("script_format", "publish_day_of_week"):
            controlled.append(
                ControlledFactorBaseline(
                    factor_name=factor_name,
                    baseline_value=None,
                    baseline_source="unknown",
                    tolerance=None,
                )
            )
        else:
            controlled.append(
                ControlledFactorBaseline(
                    factor_name=factor_name,
                    baseline_value=None,
                    baseline_source="unknown",
                    tolerance=None,
                )
            )

    return controlled


def _compute_brief_hash(
    channel_id: int,
    selection_decision_id: int,
    brief_planning_intent: BriefPlanningIntent,
    treatment_factors: list[TreatmentFactorSpec],
    target_metric: str,
    policy_version: str,
) -> str:
    """Deterministic hash for the strategy brief (excludes timestamps)."""
    payload = {
        "channel_id": channel_id,
        "selection_decision_id": selection_decision_id,
        "brief_planning_intent": brief_planning_intent.value,
        "treatment_factors": [
            {"factor_name": tf.factor_name, "value_type": tf.value_type}
            for tf in sorted(treatment_factors, key=lambda t: t.factor_name)
        ],
        "target_metric": target_metric,
        "policy_version": policy_version,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _load_selection_decision(
    conn: sqlite3.Connection,
    selection_decision_id: int,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT sd.*, ecs.channel_id, ecs.planning_intent, ecs.experiment_type,
               ecs.primary_target_metric,
               ecs.primary_metric_direction, ecs.intended_treatment_factors_json,
               ecs.controlled_factors_json, ecs.opportunity_id AS ecs_opp_id,
               ecs.canonical_cluster_id, ecs.eligibility_classification,
               ecs.final_planning_score, ecs.opportunity_attractiveness,
               ecs.exploitation_value, ecs.exploration_value,
               ecs.information_gain, ecs.internal_evidence_strength,
               ecs.uncertainty, ecs.cluster_coverage_need,
               ecs.production_feasibility, ecs.planning_run_id,
               ecs.id AS candidate_score_id
        FROM experiment_selection_decisions sd
        JOIN experiment_candidate_scores ecs ON ecs.id = sd.candidate_score_id
        WHERE sd.id = ?
        """,
        (selection_decision_id,),
    ).fetchone()


def _load_opportunity_topic(conn: sqlite3.Connection, opportunity_id: int) -> str:
    row = conn.execute(
        "SELECT normalized_topic FROM opportunities WHERE id = ?",
        (opportunity_id,),
    ).fetchone()
    return row["normalized_topic"] if row else ""


def _load_active_channel_profile_version_id(
    conn: sqlite3.Connection,
    channel_id: int,
) -> int | None:
    row = conn.execute(
        "SELECT id FROM channel_profile_versions "
        "WHERE channel_id = ? AND status = 'active' LIMIT 1",
        (channel_id,),
    ).fetchone()
    return row["id"] if row else None


def _load_channel_profile_dict(
    conn: sqlite3.Connection,
    channel_id: int,
) -> dict:
    row = conn.execute(
        """SELECT primary_niche, secondary_niches_json, excluded_topics_json,
                  brand_voice, content_style, audience_description
           FROM channel_profile_versions
           WHERE channel_id = ? AND status = 'active'
           LIMIT 1""",
        (channel_id,),
    ).fetchone()
    if row is None:
        return {}
    return {
        "primary_niche": row["primary_niche"] or "",
        "secondary_niches": json.loads(row["secondary_niches_json"] or "[]"),
        "excluded_topics": json.loads(row["excluded_topics_json"] or "[]"),
        "brand_voice": row["brand_voice"] or "",
        "content_style": row["content_style"] or "",
        "audience_description": row["audience_description"] or "",
    }


def _check_cluster_is_new(
    conn: sqlite3.Connection,
    channel_id: int,
    canonical_cluster_id: int | None,
) -> bool:
    """True when no prior experiments exist on this cluster for this channel."""
    if canonical_cluster_id is None:
        return True
    row = conn.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM experiments e
        JOIN opportunities o ON o.id = e.opportunity_id
        WHERE o.channel_id = ?
          AND o.canonical_cluster_id = ?
          AND e.status NOT IN ('cancelled')
        """,
        (channel_id, canonical_cluster_id),
    ).fetchone()
    return row["cnt"] == 0


# ── Public API ────────────────────────────────────────────────────────────────


class BriefCreationError(Exception):
    """Raised when a strategy brief cannot be created."""


def create_strategy_brief(
    conn: sqlite3.Connection,
    selection_decision_id: int,
    *,
    policy: PlanningPolicy | None = None,
) -> ExperimentStrategyBrief:
    """Create (or retrieve) the strategy brief for a selected planning decision.

    Idempotent: if a brief already exists for this selection_decision_id, the
    existing brief is returned without re-creation.

    Args:
        conn: DB connection (caller commits).
        selection_decision_id: The experiment_selection_decisions.id to brief.
        policy: Planning policy (used for policy_version; V1 default if None).

    Raises:
        BriefCreationError: if the decision is not selected, the opportunity is
            no longer eligible, or the decision cannot be found.
    """
    if policy is None:
        policy = PlanningPolicy()

    # --- Idempotency: return existing brief ---
    existing = conn.execute(
        "SELECT * FROM experiment_strategy_briefs WHERE selection_decision_id = ?",
        (selection_decision_id,),
    ).fetchone()
    if existing is not None:
        return _row_to_brief(existing)

    # --- Load selection decision ---
    decision_row = _load_selection_decision(conn, selection_decision_id)
    if decision_row is None:
        raise BriefCreationError(f"Selection decision {selection_decision_id} not found.")

    if not decision_row["selected"]:
        raise BriefCreationError(
            f"Decision {selection_decision_id} is not selected (deferred candidates "
            "cannot receive a strategy brief)."
        )

    opportunity_id: int = decision_row["ecs_opp_id"]
    channel_id: int = decision_row["channel_id"]
    canonical_cluster_id: int | None = decision_row["canonical_cluster_id"]
    planning_run_id: str = decision_row["planning_run_id"]
    eligibility_classification: str = decision_row["eligibility_classification"]

    # --- Eligibility recheck ---
    # Phase 14E checks the classification stored in the candidate score.
    # A live re-assessment would require calling assess_experiment_eligibility() which may
    # invoke an LLM — prohibited here.  If the stored classification is in the blocked set
    # (ineligible / stale), brief creation is rejected.  The stored classification reflects
    # the last known eligibility at planning time; operators must replan if it changes.
    if eligibility_classification in _BLOCKED_ELIGIBILITY:
        raise BriefCreationError(
            f"Opportunity {opportunity_id} is not eligible for a strategy brief "
            f"(stored classification: {eligibility_classification!r}). Replanning required."
        )

    # --- Load context ---
    planning_intent_str: str = decision_row["planning_intent"]
    experiment_type: str = decision_row["experiment_type"]
    target_metric: str = decision_row["primary_target_metric"]
    target_direction: str = decision_row["primary_metric_direction"]
    is_validation_repeat: bool = bool(decision_row["is_validation_repeat"])

    canonical_topic = _load_opportunity_topic(conn, opportunity_id)
    channel_profile = _load_channel_profile_dict(conn, channel_id)
    channel_profile_version_id = _load_active_channel_profile_version_id(conn, channel_id)

    cluster_is_new = _check_cluster_is_new(conn, channel_id, canonical_cluster_id)

    # --- Map brief planning intent ---
    brief_intent = _map_brief_planning_intent(
        planning_intent_str, cluster_is_new, is_validation_repeat
    )

    # --- Build treatment factors ---
    raw_treatment_json = decision_row["intended_treatment_factors_json"] or "[]"
    raw_treatments: list[dict] = json.loads(raw_treatment_json)
    treatment_factors = [
        _build_treatment_factor_spec(t["factor_name"])
        for t in raw_treatments
        if t.get("factor_role") == "treatment"
    ]

    # --- Confounding risk ---
    confounding_risk = _compute_confounding_risk(brief_intent, len(treatment_factors))

    # --- Controlled factor baselines ---
    treatment_names = [tf.factor_name for tf in treatment_factors]
    controlled_factors = _resolve_controlled_baselines(
        conn, channel_id, treatment_names, channel_profile
    )

    # --- Content constraints from channel profile ---
    content_constraints = ContentConstraints(
        excluded_topics=tuple(channel_profile.get("excluded_topics", [])),
        primary_niche=channel_profile.get("primary_niche", ""),
        secondary_niches=tuple(channel_profile.get("secondary_niches", [])),
        brand_voice=channel_profile.get("brand_voice", ""),
        content_style=channel_profile.get("content_style", ""),
        audience_description=channel_profile.get("audience_description", ""),
    )

    # --- Hypothesis (deterministic template) ---
    hypothesis = _build_hypothesis(brief_intent, canonical_topic, target_metric, treatment_factors)

    # --- Strategic reasons ---
    strategic_reason = _build_strategic_reason(brief_intent, canonical_topic, is_validation_repeat)
    information_gain_reason = _build_information_gain_reason(
        brief_intent, cluster_is_new, treatment_factors
    )

    # --- Score decomposition ---
    score_decomp = {
        "final_planning_score": decision_row["final_planning_score"],
        "opportunity_attractiveness": decision_row["opportunity_attractiveness"],
        "exploitation_value": decision_row["exploitation_value"],
        "exploration_value": decision_row["exploration_value"],
        "information_gain": decision_row["information_gain"],
        "internal_evidence_strength": decision_row["internal_evidence_strength"],
        "uncertainty": decision_row["uncertainty"],
        "cluster_coverage_need": decision_row["cluster_coverage_need"],
        "production_feasibility": decision_row["production_feasibility"],
    }

    # --- Hash ---
    brief_hash = _compute_brief_hash(
        channel_id=channel_id,
        selection_decision_id=selection_decision_id,
        brief_planning_intent=brief_intent,
        treatment_factors=treatment_factors,
        target_metric=target_metric,
        policy_version=policy.version,
    )

    brief_id = str(uuid.uuid4())

    # --- Persist ---
    conn.execute(
        """
        INSERT INTO experiment_strategy_briefs (
            id, channel_id, planning_run_id, selection_decision_id,
            opportunity_id, canonical_cluster_id, channel_profile_version_id,
            brief_planning_intent, experiment_type,
            market_theme, canonical_topic, strategic_reason, information_gain_reason,
            hypothesis, target_metric, target_direction,
            treatment_factors_json, controlled_factors_json,
            content_constraints_json, confounding_risk,
            policy_version, eligibility_classification, score_decomposition_json,
            brief_hash, status
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            brief_id,
            channel_id,
            planning_run_id,
            selection_decision_id,
            opportunity_id,
            canonical_cluster_id,
            channel_profile_version_id,
            brief_intent.value,
            experiment_type,
            canonical_topic,  # market_theme = canonical_topic for now
            canonical_topic,
            strategic_reason,
            information_gain_reason,
            hypothesis,
            target_metric,
            target_direction,
            json.dumps(
                [
                    {
                        "factor_name": tf.factor_name,
                        "value_type": tf.value_type,
                        "autonomy": tf.autonomy.value,
                        "safe_range_min": tf.safe_range_min,
                        "safe_range_max": tf.safe_range_max,
                        "safe_values": list(tf.safe_values) if tf.safe_values else None,
                        "intended_value": tf.intended_value,
                    }
                    for tf in treatment_factors
                ]
            ),
            json.dumps(
                [
                    {
                        "factor_name": cf.factor_name,
                        "baseline_value": cf.baseline_value,
                        "baseline_source": cf.baseline_source,
                        "tolerance": cf.tolerance,
                    }
                    for cf in controlled_factors
                ]
            ),
            json.dumps(
                {
                    "excluded_topics": list(content_constraints.excluded_topics),
                    "primary_niche": content_constraints.primary_niche,
                    "secondary_niches": list(content_constraints.secondary_niches),
                    "brand_voice": content_constraints.brand_voice,
                    "content_style": content_constraints.content_style,
                    "audience_description": content_constraints.audience_description,
                }
            ),
            confounding_risk.value,
            policy.version,
            eligibility_classification,
            json.dumps(score_decomp),
            brief_hash,
            "pending_approval",
        ),
    )

    return ExperimentStrategyBrief(
        id=brief_id,
        channel_id=channel_id,
        planning_run_id=planning_run_id,
        selection_decision_id=selection_decision_id,
        opportunity_id=opportunity_id,
        canonical_cluster_id=canonical_cluster_id,
        channel_profile_version_id=channel_profile_version_id,
        brief_planning_intent=brief_intent,
        experiment_type=experiment_type,
        market_theme=canonical_topic,
        canonical_topic=canonical_topic,
        strategic_reason=strategic_reason,
        information_gain_reason=information_gain_reason,
        hypothesis=hypothesis,
        target_metric=target_metric,
        target_direction=target_direction,
        treatment_factors=treatment_factors,
        controlled_factors=list(controlled_factors),
        content_constraints=content_constraints,
        confounding_risk=confounding_risk,
        policy_version=policy.version,
        eligibility_classification=eligibility_classification,
        score_decomposition_json=json.dumps(score_decomp),
        brief_hash=brief_hash,
        status="pending_approval",
    )


def _build_strategic_reason(
    brief_intent: BriefPlanningIntent,
    canonical_topic: str,
    is_validation_repeat: bool,
) -> str:
    topic = canonical_topic or "this topic"
    if brief_intent == BriefPlanningIntent.MARKET_EXPLORATION:
        return f"No prior experiments on cluster '{topic}'; baseline signal needed."
    if brief_intent == BriefPlanningIntent.FEATURE_EXPLORATION:
        return f"Cluster '{topic}' has prior signal; feature experiment expands evidence."
    if brief_intent == BriefPlanningIntent.EXPLOITATION:
        return f"Cluster '{topic}' has directional evidence; exploit to optimize output."
    # VALIDATION
    return f"Prior experiment on '{topic}' needs updated signal; re-testing for validation."


def _build_information_gain_reason(
    brief_intent: BriefPlanningIntent,
    cluster_is_new: bool,
    treatment_factors: list[TreatmentFactorSpec],
) -> str:
    if brief_intent == BriefPlanningIntent.MARKET_EXPLORATION:
        return "Untested cluster: high cluster coverage gain, no feature confound."
    if brief_intent == BriefPlanningIntent.FEATURE_EXPLORATION:
        factor_names = ", ".join(tf.factor_name for tf in treatment_factors) or "treatment factor"
        return f"Tested cluster with gap in '{factor_names}' feature coverage."
    if brief_intent == BriefPlanningIntent.EXPLOITATION:
        return "High evidence cluster: information gain from exploitation experiment."
    return "Validation: confirms or revises prior signal with updated evidence."


def _row_to_brief(row: sqlite3.Row) -> ExperimentStrategyBrief:
    """Convert a DB row to an ExperimentStrategyBrief."""
    treatment_raw = json.loads(row["treatment_factors_json"] or "[]")
    treatment_factors = [
        TreatmentFactorSpec(
            factor_name=t["factor_name"],
            value_type=t["value_type"],
            autonomy=FactorAutonomy(t["autonomy"]),
            safe_range_min=t.get("safe_range_min"),
            safe_range_max=t.get("safe_range_max"),
            safe_values=tuple(t["safe_values"]) if t.get("safe_values") else None,
            intended_value=t.get("intended_value"),
        )
        for t in treatment_raw
    ]
    controlled_raw = json.loads(row["controlled_factors_json"] or "[]")
    controlled_factors = [
        ControlledFactorBaseline(
            factor_name=c["factor_name"],
            baseline_value=c.get("baseline_value"),
            baseline_source=c["baseline_source"],
            tolerance=c.get("tolerance"),
        )
        for c in controlled_raw
    ]
    cc_raw = json.loads(row["content_constraints_json"] or "{}")
    content_constraints = ContentConstraints(
        excluded_topics=tuple(cc_raw.get("excluded_topics", [])),
        primary_niche=cc_raw.get("primary_niche", ""),
        secondary_niches=tuple(cc_raw.get("secondary_niches", [])),
        brand_voice=cc_raw.get("brand_voice", ""),
        content_style=cc_raw.get("content_style", ""),
        audience_description=cc_raw.get("audience_description", ""),
    )
    return ExperimentStrategyBrief(
        id=row["id"],
        channel_id=row["channel_id"],
        planning_run_id=row["planning_run_id"],
        selection_decision_id=row["selection_decision_id"],
        opportunity_id=row["opportunity_id"],
        canonical_cluster_id=row["canonical_cluster_id"],
        channel_profile_version_id=row["channel_profile_version_id"],
        brief_planning_intent=BriefPlanningIntent(row["brief_planning_intent"]),
        experiment_type=row["experiment_type"],
        market_theme=row["market_theme"],
        canonical_topic=row["canonical_topic"],
        strategic_reason=row["strategic_reason"],
        information_gain_reason=row["information_gain_reason"],
        hypothesis=row["hypothesis"],
        target_metric=row["target_metric"],
        target_direction=row["target_direction"],
        treatment_factors=treatment_factors,
        controlled_factors=controlled_factors,
        content_constraints=content_constraints,
        confounding_risk=ConfoundingRisk(row["confounding_risk"]),
        policy_version=row["policy_version"],
        eligibility_classification=row["eligibility_classification"],
        score_decomposition_json=row["score_decomposition_json"],
        brief_hash=row["brief_hash"],
        status=row["status"],
        created_at=row["created_at"],
    )


def get_strategy_brief(
    conn: sqlite3.Connection,
    brief_id: str,
) -> ExperimentStrategyBrief | None:
    """Retrieve a strategy brief by its primary ID."""
    row = conn.execute(
        "SELECT * FROM experiment_strategy_briefs WHERE id = ?",
        (brief_id,),
    ).fetchone()
    return _row_to_brief(row) if row else None


def get_brief_for_decision(
    conn: sqlite3.Connection,
    selection_decision_id: int,
) -> ExperimentStrategyBrief | None:
    """Retrieve the strategy brief for a given selection decision, if any."""
    row = conn.execute(
        "SELECT * FROM experiment_strategy_briefs WHERE selection_decision_id = ?",
        (selection_decision_id,),
    ).fetchone()
    return _row_to_brief(row) if row else None


def list_briefs_for_channel(
    conn: sqlite3.Connection,
    channel_id: int,
    *,
    status: str | None = None,
) -> list[ExperimentStrategyBrief]:
    """List strategy briefs for a channel, optionally filtered by status."""
    if status is not None:
        rows = conn.execute(
            "SELECT * FROM experiment_strategy_briefs "
            "WHERE channel_id = ? AND status = ? ORDER BY created_at DESC",
            (channel_id, status),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM experiment_strategy_briefs "
            "WHERE channel_id = ? ORDER BY created_at DESC",
            (channel_id,),
        ).fetchall()
    return [_row_to_brief(r) for r in rows]
