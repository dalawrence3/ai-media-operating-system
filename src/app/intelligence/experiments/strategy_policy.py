"""Phase 17E — Channel Strategy Profile → PlanningPolicy bridge.

Answers: "given a channel's operator-defined strategy profile and its actual
first-party evidence maturity today, what PlanningPolicy should the
experiment planner use right now?"

Design:
  - The strategy profile (cp_strategy_profiles.config_json) stores POLICY:
    two named regimes (bootstrap / steady_state), each with its own market-
    vs-channel evidence weighting and exploration share, plus a transition
    rule describing when to move from one regime to the other.
  - The EFFECTIVE regime is never stored — it is computed at read time from
    the channel's actual channel_performance_baselines maturity for the
    configured trigger metric. This is deliberate: it is the only way to
    guarantee the UI and the planner never claim a first-party-evidence
    transition that hasn't actually happened. If cross-publication learning
    has never run (channel_performance_baselines is empty, which is the
    live state today), maturity is 'insufficient' and the effective regime
    is always 'bootstrap', regardless of what the profile's steady_state
    block says.
  - No topic names appear anywhere in this module. Candidate topics remain
    entirely the responsibility of Market Intelligence (opportunities /
    opportunity_scores) and the planner's existing candidate scoring.

This module reads from control_plane (strategy profile) and learning
(cross-publication baselines) — both lower layers than intelligence, so no
import cycle risk. It hands back a plain intelligence.experiments.planning
PlanningPolicy, the type the planner already understands.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from app.intelligence.experiments.planning import PlanningPolicy

STRATEGY_SCHEMA_VERSION = "1.0"

MATURITY_LEVELS = ("insufficient", "exploratory", "directional", "actionable")
MATURITY_RANK: dict[str, int] = {level: i for i, level in enumerate(MATURITY_LEVELS)}

# Fraction of exploitation-value weight held fixed for production feasibility.
# The remaining 0.80 is split between market attractiveness and channel
# evidence according to the active regime's weights — see
# strategy_config_to_planning_policy(). Matches PlanningPolicy.v1()'s
# feasibility weight so an unconfigured channel's math is unchanged.
_FIXED_FEASIBILITY_WEIGHT = 0.20


class StrategyConfigError(ValueError):
    """Raised when a strategy config_json fails validation."""


def default_bootstrap_strategy_config() -> dict[str, Any]:
    """The canonical starting strategy for a channel with no first-party
    evidence yet: market-heavy, exploration-heavy, diverse.

    Used both as the seed for a channel's first strategy version and as the
    reference shape new configs are validated against. Contains no topic
    names — candidates are sourced dynamically from live opportunities.
    """
    return {
        "schema_version": STRATEGY_SCHEMA_VERSION,
        "bootstrap": {
            "target_publication_count": 18,
            "market_intelligence_weight": 0.8,
            "channel_evidence_weight": 0.2,
            "exploration_share": 0.67,
        },
        "steady_state": {
            "market_intelligence_weight": 0.4,
            "channel_evidence_weight": 0.6,
            "exploration_share": 0.2,
        },
        "transition": {
            "trigger_metric": "average_view_percentage",
            "maturity_threshold": "directional",
        },
        "diversity": {
            "max_cluster_share": 0.4,
            "max_consecutive_same_cluster": 2,
        },
        "creative_dimensions": [
            "topic_theme",
            "hook",
            "pacing",
            "duration",
            "structure",
            "caption_density",
            "publish_timing",
        ],
        "total_portfolio_slots": 3,
    }


def _require_regime(config: dict[str, Any], key: str, errors: list[str]) -> dict[str, Any]:
    regime = config.get(key)
    if not isinstance(regime, dict):
        errors.append(f"'{key}' must be an object")
        return {}
    for weight_key in (
        "market_intelligence_weight",
        "channel_evidence_weight",
        "exploration_share",
    ):
        v = regime.get(weight_key)
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not (0.0 <= v <= 1.0):
            errors.append(f"'{key}.{weight_key}' must be a number between 0 and 1")
    target = regime.get("target_publication_count")
    if key == "bootstrap" and (
        not isinstance(target, int) or isinstance(target, bool) or target < 1
    ):
        errors.append("'bootstrap.target_publication_count' must be a positive integer")
    return regime


def validate_strategy_config(config: dict[str, Any]) -> list[str]:
    """Return a list of human-readable validation errors; empty means valid.

    Deliberately does not require or forbid any topic-name field — this
    schema has none, and never should.
    """
    errors: list[str] = []
    if not isinstance(config, dict):
        return ["config must be a JSON object"]

    _require_regime(config, "bootstrap", errors)
    _require_regime(config, "steady_state", errors)

    transition = config.get("transition")
    if not isinstance(transition, dict):
        errors.append("'transition' must be an object")
    else:
        if not isinstance(transition.get("trigger_metric"), str) or not transition.get(
            "trigger_metric"
        ):
            errors.append("'transition.trigger_metric' must be a non-empty string")
        threshold = transition.get("maturity_threshold")
        if threshold not in MATURITY_RANK:
            errors.append(f"'transition.maturity_threshold' must be one of {MATURITY_LEVELS}")

    diversity = config.get("diversity")
    if not isinstance(diversity, dict):
        errors.append("'diversity' must be an object")
    else:
        share = diversity.get("max_cluster_share")
        if (
            not isinstance(share, (int, float))
            or isinstance(share, bool)
            or not (0.0 < share <= 1.0)
        ):
            errors.append("'diversity.max_cluster_share' must be a number in (0, 1]")
        consec = diversity.get("max_consecutive_same_cluster")
        if not isinstance(consec, int) or isinstance(consec, bool) or consec < 1:
            errors.append("'diversity.max_consecutive_same_cluster' must be a positive integer")

    dims = config.get("creative_dimensions")
    if not isinstance(dims, list) or not all(isinstance(d, str) for d in dims):
        errors.append("'creative_dimensions' must be a list of strings")

    slots = config.get("total_portfolio_slots")
    if not isinstance(slots, int) or isinstance(slots, bool) or slots < 1:
        errors.append("'total_portfolio_slots' must be a positive integer")

    return errors


def compute_effective_strategy_state(
    conn: sqlite3.Connection,
    cp_channel_id: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Resolve which regime is actually in effect right now for this channel.

    Reads channel_performance_baselines directly (already keyed by the
    control-plane channel UUID — no intelligence-domain bridge needed here,
    unlike opportunities/experiments). An empty/missing baseline is treated
    as 'insufficient' maturity, never fabricated as anything higher.
    """
    from app.learning.cross_publication import get_channel_baselines

    transition = config.get("transition", {})
    trigger_metric = transition.get("trigger_metric", "average_view_percentage")
    threshold = transition.get("maturity_threshold", "directional")

    baselines = get_channel_baselines(conn, channel_id=cp_channel_id, metric_name=trigger_metric)
    current_maturity = baselines[0].sample_maturity if baselines else "insufficient"
    publication_count = baselines[0].publication_count if baselines else 0

    has_matured = MATURITY_RANK.get(current_maturity, 0) >= MATURITY_RANK.get(threshold, 2)
    effective_regime = "steady_state" if has_matured else "bootstrap"
    regime_config = config.get(effective_regime, {})

    return {
        "trigger_metric": trigger_metric,
        "maturity_threshold": threshold,
        "current_maturity": current_maturity,
        "publication_count": publication_count,
        "effective_regime": effective_regime,
        "market_intelligence_weight": regime_config.get("market_intelligence_weight"),
        "channel_evidence_weight": regime_config.get("channel_evidence_weight"),
        "exploration_share": regime_config.get("exploration_share"),
    }


def strategy_config_to_planning_policy(
    config: dict[str, Any],
    effective: dict[str, Any],
) -> PlanningPolicy:
    """Translate a strategy profile + its computed effective state into a
    concrete PlanningPolicy, by adjusting only the fields that already exist
    on PlanningPolicy for exactly this purpose — no new planner concepts.
    """
    mi_weight = effective.get("market_intelligence_weight") or 0.0
    ce_weight = effective.get("channel_evidence_weight") or 0.0
    total_weight = mi_weight + ce_weight
    if total_weight <= 0:
        # Degenerate config — fall back to the unconfigured default split.
        w_attractiveness = 0.50
        w_evidence = 0.30
    else:
        available = 1.0 - _FIXED_FEASIBILITY_WEIGHT
        w_attractiveness = available * (mi_weight / total_weight)
        w_evidence = available * (ce_weight / total_weight)

    total_slots = int(config.get("total_portfolio_slots", 3))
    exploration_share = effective.get("exploration_share")
    if exploration_share is None:
        exploration_slots = 2
    else:
        exploration_slots = round(total_slots * exploration_share)
        exploration_slots = max(0, min(total_slots, exploration_slots))
    exploitation_slots = max(0, total_slots - exploration_slots)

    diversity = config.get("diversity", {})

    return PlanningPolicy(
        max_exploration_slots=exploration_slots,
        max_exploitation_slots=exploitation_slots,
        max_cluster_share=diversity.get("max_cluster_share", 0.40),
        max_consecutive_same_cluster=diversity.get("max_consecutive_same_cluster", 2),
        w_exploitation_attractiveness=round(w_attractiveness, 4),
        w_exploitation_evidence=round(w_evidence, 4),
        w_exploitation_feasibility=_FIXED_FEASIBILITY_WEIGHT,
    )


def load_policy_for_channel(conn: sqlite3.Connection, channel_id: int) -> PlanningPolicy:
    """The planner's actual entry point: resolve a channel's active
    strategy profile (if any) into a concrete PlanningPolicy.

    Falls back to PlanningPolicy.v1() — the safe, pre-Phase-17E default —
    whenever there is no cp_channel mapping, no active strategy profile, or
    the stored config fails validation. Never raises: a missing or broken
    strategy must never block planning.
    """
    try:
        from app.control_plane import services as cp_services
        from app.intelligence.channel_bridge import get_cp_channel_id_for_intelligence_channel

        cp_channel_id = get_cp_channel_id_for_intelligence_channel(conn, channel_id)
        if cp_channel_id is None:
            return PlanningPolicy.v1()

        profile = cp_services.get_channel_strategy(conn, cp_channel_id)
        if profile is None:
            return PlanningPolicy.v1()

        config = profile.config
        if validate_strategy_config(config):
            return PlanningPolicy.v1()

        effective = compute_effective_strategy_state(conn, cp_channel_id, config)
        return strategy_config_to_planning_policy(config, effective)
    except Exception:
        return PlanningPolicy.v1()
