"""Per-channel visual policy.

Channels differ: a documentary channel wants long, cinematic takes; a
fast-cut explainer wants short beats and heavy graphics.  Policy is the seam
that lets those differ without forking the engine.

Only the pacing/mix knobs the engine actually reads are defined here.  Named
styles are presets over those knobs, not separate code paths, so adding a
style later is a dict entry rather than a rewrite.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.visuals.constants import (
    COST_LOW,
    DEFAULT_TARGET_BEAT_MS,
    MAX_ASSET_TOTAL_MS,
    MAX_ASSET_USES_PER_VIDEO,
    MAX_BEAT_MS,
    MIN_BEAT_MS,
)

STYLE_BALANCED = "balanced"
STYLE_FAST_CUT = "fast_cut"
STYLE_DOCUMENTARY = "documentary"
STYLE_MINIMALIST = "minimalist"
STYLE_DIAGRAM_HEAVY = "diagram_heavy"
STYLE_STOCK_HEAVY = "stock_heavy"


@dataclass
class VisualPolicy:
    """Channel-scoped visual strategy.  All fields have safe generic defaults."""

    style: str = STYLE_BALANCED
    target_beat_ms: int = DEFAULT_TARGET_BEAT_MS
    min_beat_ms: int = MIN_BEAT_MS
    max_beat_ms: int = MAX_BEAT_MS
    max_asset_uses_per_video: int = MAX_ASSET_USES_PER_VIDEO
    max_asset_total_ms: int = MAX_ASSET_TOTAL_MS
    require_commercial_safe: bool = True
    allow_still_motion: bool = True
    # Relative cost ceiling a provider may carry before it is skipped.
    max_provider_cost_units: int = COST_LOW
    # Multiplier on the score a retrieved candidate must beat.  >1 biases
    # toward locally generated explanatory graphics.
    stock_score_multiplier: float = 1.0
    # How many candidates to consider per beat before scoring settles.
    candidates_per_beat: int = 12
    # Queries attempted per beat.  Each query is one provider call.
    queries_per_beat: int = 2
    metadata: dict[str, Any] = field(default_factory=dict)


_PRESETS: dict[str, dict[str, Any]] = {
    STYLE_BALANCED: {},
    STYLE_FAST_CUT: {"target_beat_ms": 2400, "min_beat_ms": 1200, "max_beat_ms": 4200},
    STYLE_DOCUMENTARY: {
        "target_beat_ms": 5000,
        "max_beat_ms": 9000,
        "max_asset_total_ms": 18_000,
        "stock_score_multiplier": 0.9,
    },
    STYLE_MINIMALIST: {"target_beat_ms": 4200, "stock_score_multiplier": 1.25},
    STYLE_DIAGRAM_HEAVY: {"stock_score_multiplier": 1.35},
    STYLE_STOCK_HEAVY: {"stock_score_multiplier": 0.85, "queries_per_beat": 3},
}


# The styles an experiment may request. This is the renderer's actual
# capability surface — the keys of _PRESETS — rather than a wish list, so a
# treatment can never name a visual style the pipeline cannot produce.
VISUAL_STYLE_SAFE_VALUES: tuple[str, ...] = (
    STYLE_BALANCED,
    STYLE_FAST_CUT,
    STYLE_DOCUMENTARY,
    STYLE_MINIMALIST,
    STYLE_DIAGRAM_HEAVY,
    STYLE_STOCK_HEAVY,
)


def resolve_policy(
    style: str | None = None,
    *,
    overrides: dict[str, Any] | None = None,
) -> VisualPolicy:
    """Build a policy from a named style plus explicit overrides.

    An unknown style falls back to balanced rather than raising: a channel
    configured for a style this build does not know yet should still render.
    """
    resolved_style = (style or STYLE_BALANCED).strip().lower()
    preset = _PRESETS.get(resolved_style)
    if preset is None:
        resolved_style, preset = STYLE_BALANCED, {}

    policy = VisualPolicy(style=resolved_style, **preset)

    for key, value in (overrides or {}).items():
        if value is None or not hasattr(policy, key):
            continue
        setattr(policy, key, value)

    # Keep the pacing envelope internally consistent whatever was supplied.
    policy.min_beat_ms = max(500, min(policy.min_beat_ms, policy.target_beat_ms))
    policy.max_beat_ms = max(policy.target_beat_ms, policy.max_beat_ms)
    return policy


def policy_from_config(config: dict[str, Any] | None) -> VisualPolicy:
    """Resolve a policy from a stage's effective_config."""
    config = config or {}
    overrides = {
        "target_beat_ms": config.get("visual_target_beat_ms"),
        "min_beat_ms": config.get("visual_min_beat_ms"),
        "max_beat_ms": config.get("visual_max_beat_ms"),
        "require_commercial_safe": config.get("visual_require_commercial_safe"),
        "queries_per_beat": config.get("visual_queries_per_beat"),
        "candidates_per_beat": config.get("visual_candidates_per_beat"),
        "allow_still_motion": config.get("visual_allow_still_motion"),
        "max_provider_cost_units": config.get("visual_max_provider_cost_units"),
    }
    return resolve_policy(config.get("visual_style"), overrides=overrides)
