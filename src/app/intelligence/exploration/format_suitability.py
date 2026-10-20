"""Phase 7C.1, Part B — format suitability heuristic.

Answers one question per (format, audience_value) pair: "how naturally can
this content objective be expressed in this format?" A cheap, deterministic,
interpretable lookup table — never an AI call per cell (§7: "keep it
cheap"), and never keyed on domain, so it can never become a disguised
domain->format routing table (explicitly forbidden — "Do NOT hardcode:
language_learning -> quiz, finance -> micro lesson"). audience_value is a
cross-domain dimension (any domain can pair with any audience_value: a
finance topic can be curiosity-driven, a language topic can be practical-
utility-driven), so scoring on it alone keeps this generic by construction.

Scores are grounded in each format's OWN documented interaction mechanics
(format_contract.py's FormatSpec descriptions), not arbitrary numbers:
  - interactive_recall_quiz: prompt -> think -> reveal is inherently about
    testing recall and delivering an immediate right/wrong payoff.
  - practical_micro_lesson: problem -> concrete example -> actionable
    takeaway is inherently about applying something useful right now.
  - curiosity_explainer: hook -> explanation -> payoff reveal is inherently
    built around curiosity and surprise, and works at any subject depth.

This is ONE input to exploration ranking, not a router — see
portfolio.py's FORMAT_SUITABILITY_WEIGHT for how it's kept from overriding
novelty/diversity (Part B, §6).
"""

from __future__ import annotations

# No opinion — a (format, audience_value) pair not explicitly scored below
# is treated as an ordinary, unremarkable fit. Never penalized, never
# favored; keeps the table additive as new formats/audience values appear.
DEFAULT_SUITABILITY: float = 0.5

_SUITABILITY_TABLE: dict[str, dict[str, float]] = {
    "interactive_recall_quiz": {
        "immediate_payoff": 0.9,
        "practical_utility": 0.7,
        "broad_accessibility": 0.6,
        "curiosity": 0.5,
        "surprise": 0.5,
        "personal_relevance": 0.4,
        "niche_technicality": 0.4,
    },
    "practical_micro_lesson": {
        "practical_utility": 0.9,
        "immediate_payoff": 0.7,
        "personal_relevance": 0.7,
        "broad_accessibility": 0.6,
        "niche_technicality": 0.5,
        "curiosity": 0.4,
        "surprise": 0.3,
    },
    "curiosity_explainer": {
        "curiosity": 0.9,
        "surprise": 0.8,
        "broad_accessibility": 0.7,
        "niche_technicality": 0.6,
        "personal_relevance": 0.5,
        "practical_utility": 0.4,
        "immediate_payoff": 0.4,
    },
}


def format_suitability(format_name: str, audience_value: str) -> float:
    """0..1 heuristic fit score. A format with no entry at all (every seed
    format not yet production-capable) returns DEFAULT_SUITABILITY — this
    function never blocks or penalizes an unsupported format; that's
    format_contract.py's job."""
    return _SUITABILITY_TABLE.get(format_name, {}).get(audience_value, DEFAULT_SUITABILITY)
