"""Phase 7A, §2 — exploration dimensions: domain, format, audience-value.

Every taxonomy here is a SEED list, not a closed universe: `normalize_*`
never rejects an unseen value, it only tells the caller whether the value
matches a known seed (for reporting/telemetry) or is a genuinely novel one
(which is exactly the kind of thing exploration should be free to surface).
A market-intelligence prompt or an operator can always introduce a new
domain/format by simply using a new string — nothing here gates that.

audience_value deliberately does NOT duplicate Phase 18H.C's seven
topic-attribute names (practical_utility, immediate_payoff, curiosity,
personal_relevance, surprise, broad_accessibility, niche_technicality,
defined in app.learning.topic_attributes) — those are reused directly as
the audience-value dimension, imported here rather than re-declared, so
there is exactly one place either list can drift.
"""

from __future__ import annotations

from app.learning.topic_attributes import TOPIC_ATTRIBUTE_NAMES

# ── Domain (content-space category) ────────────────────────────────────────
# Seeds only. A new domain is valid the moment something proposes it —
# these exist so the portfolio allocator and diversity guard have a stable
# vocabulary to reason about, not to gatekeep what content is allowed.
DOMAIN_SEEDS: tuple[str, ...] = (
    "language_learning",
    "personal_finance",
    "psychology",
    "business_career",
    "history",
    "science",
    "technology_ai",
    "geography_culture",
    "logic_math",
    "everyday_knowledge",
    "communication",
    "health_wellness",
)

# ── Format (interaction style / structural shape) ──────────────────────────
FORMAT_SEEDS: tuple[str, ...] = (
    "curiosity_explainer",
    "interactive_recall_quiz",
    "viewer_challenge",
    "practical_micro_lesson",
    "scenario_decision",
    "self_test",
    "story_reveal",
    "myth_bust",
    "comparison",
    "tutorial",
    "rapid_fact_sequence",
)

# ── Audience value / payoff ─────────────────────────────────────────────────
# Reused verbatim from Phase 18H.C's topic-attribute stack, not duplicated.
AUDIENCE_VALUE_SEEDS: tuple[str, ...] = TOPIC_ATTRIBUTE_NAMES


def _normalize(value: str) -> str:
    return " ".join(value.strip().casefold().replace("-", "_").split())


def normalize_domain(value: str) -> str:
    """Canonical comparison form. Never raises — an unseen domain is still
    a valid domain, just not (yet) a seeded one."""
    return _normalize(value)


def normalize_format(value: str) -> str:
    return _normalize(value)


def is_seed_domain(value: str) -> bool:
    normalized = normalize_domain(value)
    return normalized in {normalize_domain(d) for d in DOMAIN_SEEDS}


def is_seed_format(value: str) -> bool:
    normalized = normalize_format(value)
    return normalized in {normalize_format(f) for f in FORMAT_SEEDS}


def is_known_audience_value(value: str) -> bool:
    return _normalize(value) in {_normalize(a) for a in AUDIENCE_VALUE_SEEDS}
