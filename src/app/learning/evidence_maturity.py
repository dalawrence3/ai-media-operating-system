"""Shared cross-publication evidence-maturity classification and descriptive
statistics.

Not to be confused with app.learning.maturity (MaturityRequirement /
evaluate_maturity), which answers a different question — "is this ONE
publication's analytics mature enough to evaluate at all?" — for the
per-publication recommendation generators. This module answers "how much
CROSS-publication evidence supports an aggregate pattern?" — a four-tier
classification (insufficient/exploratory/directional/actionable) over a
GROUP of publications.

Extracted from cross_publication.py (Phase 12C) so Phase 18H.C's
topic-attribute observations can reuse the exact same tiny-N vocabulary and
plain descriptive-statistics approach instead of inventing a second one.
cross_publication.py re-exports these names unchanged, so existing imports
and behavior are untouched.
"""

from __future__ import annotations

import math

# ── Evidence maturity classification ─────────────────────────────────────────

MATURITY_INSUFFICIENT = "insufficient"  # n < 2:  no cross-publication inference possible
MATURITY_EXPLORATORY = "exploratory"  # 2–3:    directionally suggestive only
MATURITY_DIRECTIONAL = "directional"  # 4–9:    pattern visible but not robust
MATURITY_ACTIONABLE = "actionable"  # >= 10:  sufficient for systematic consideration

# Semantic note: 'actionable' here means "enough evidence to consider the pattern".
# It does NOT mean "apply this change automatically" — that remains Phase 12A's domain.


def classify_maturity(n: int) -> str:
    if n < 2:
        return MATURITY_INSUFFICIENT
    if n < 4:
        return MATURITY_EXPLORATORY
    if n < 10:
        return MATURITY_DIRECTIONAL
    return MATURITY_ACTIONABLE


def compute_stats(values: list[float]) -> dict[str, object]:
    """Compute descriptive statistics for a non-empty list of floats.

    std_dev is None when n < 2 (cannot compute sample std dev with one value).
    """
    n = len(values)
    if n == 0:
        return {
            "publication_count": 0,
            "mean": None,
            "median": None,
            "min_value": None,
            "max_value": None,
            "std_dev": None,
        }
    total = sum(values)
    mean = total / n
    sorted_v = sorted(values)
    if n % 2 == 1:
        median = sorted_v[n // 2]
    else:
        median = (sorted_v[n // 2 - 1] + sorted_v[n // 2]) / 2.0
    std_dev: float | None = None
    if n >= 2:
        variance = sum((v - mean) ** 2 for v in values) / (n - 1)
        std_dev = math.sqrt(variance)
    return {
        "publication_count": n,
        "mean": mean,
        "median": median,
        "min_value": sorted_v[0],
        "max_value": sorted_v[-1],
        "std_dev": std_dev,
    }
