"""Phase 7C.2, §2/§3 — exploration-cell domain compatibility.

Answers one question: "is this discovered opportunity sufficiently
compatible with the exploration cell's domain to be attributed to that
cell?" Deliberately NOT domain-specific code (no `if domain ==
"language_learning"` anywhere) and NOT literal string equality — a real
opportunity's domain signal is free text (an LLM-authored cluster
`domain_family` like "Food & Culinary Science", or a title/summary),
while `ExplorationCell.domain` is a closed taxonomy seed like
"language_learning". Those two vocabularies never match verbatim even
when genuinely describing the same subject.

Root cause this exists to fix (Phase 7C.2 audit): nothing between "an
opportunity was promoted" and "link it to the most recent exploration
cell" ever checked whether the two were actually related — the linkage
was blind. A `language_learning` cell was able to inherit a "science of
cooking food" opportunity purely because it happened to be the most
recently promoted one, with zero domain relationship at all.

Deterministic keyword-overlap heuristic, not another AI call (§3/§7):
every domain seed already has real, already-computed free text to check
against by the time an opportunity exists (title, normalized_topic,
topic_summary, and the cold-start-generated cluster's own domain_family/
canonical_label) — no new provider call is needed to answer this
narrower question. A domain with no explicit keyword set (a future,
unknown domain) still gets a sane generic set derived from its own name,
so this generalizes rather than silently permitting or blocking anything
it has never seen.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.intelligence.dedup import normalize_topic

COMPATIBLE = "compatible"
ADJACENT = "adjacent"
INCOMPATIBLE = "incompatible"


class DomainCompatibilityClass(StrEnum):
    compatible = COMPATIBLE
    adjacent = ADJACENT
    incompatible = INCOMPATIBLE


@dataclass(frozen=True)
class DomainCompatibilityResult:
    classification: DomainCompatibilityClass
    matched_keywords: tuple[str, ...]
    domain: str

    @property
    def eligible_for_linkage(self) -> bool:
        """compatible AND adjacent-but-valid may both be attributed to the
        cell — only incompatible is refused (§4: 'Possible outcomes:
        compatible / adjacent-but-valid / incompatible... An incompatible
        opportunity may still be a perfectly valid general market
        opportunity. It simply MUST NOT inherit the wrong exploration
        cell.')."""
        return self.classification != DomainCompatibilityClass.incompatible


# Representative keywords per domain seed — deliberately small and
# editorial (matches format_suitability.py's precedent), not exhaustive.
# A hit is a real, distinct signal; multiple hits raise confidence.
_DOMAIN_KEYWORDS: dict[str, frozenset[str]] = {
    "language_learning": frozenset(
        {
            "language",
            "languages",
            "vocabulary",
            "pronunciation",
            "grammar",
            "phrase",
            "phrases",
            "linguistic",
            "linguistics",
            "bilingual",
            "dialect",
            "fluency",
            "fluent",
            "translate",
            "translation",
            "conversational",
        }
    ),
    "personal_finance": frozenset(
        {
            "money",
            "budget",
            "budgeting",
            "saving",
            "savings",
            "debt",
            "credit",
            "invest",
            "investing",
            "investment",
            "interest",
            "income",
            "expense",
            "financial",
            "finance",
            "retirement",
            "tax",
            "taxes",
        }
    ),
    "psychology": frozenset(
        {
            "psychology",
            "psychological",
            "cognitive",
            "behavior",
            "behavioral",
            "brain",
            "mind",
            "emotion",
            "emotional",
            "mental",
            "bias",
            "perception",
            "memory",
            "motivation",
        }
    ),
    "business_career": frozenset(
        {
            "business",
            "career",
            "entrepreneurship",
            "entrepreneur",
            "startup",
            "workplace",
            "job",
            "resume",
            "negotiation",
            "leadership",
            "management",
            "marketing",
            "sales",
            "productivity",
        }
    ),
    "history": frozenset(
        {
            "history",
            "historical",
            "ancient",
            "empire",
            "civilization",
            "war",
            "revolution",
            "century",
            "era",
            "dynasty",
            "medieval",
        }
    ),
    "science": frozenset(
        {
            "science",
            "scientific",
            "chemistry",
            "chemical",
            "biology",
            "physics",
            "experiment",
            "molecule",
            "atom",
            "cell",
            "organism",
        }
    ),
    "technology_ai": frozenset(
        {
            "technology",
            "tech",
            "artificial",
            "intelligence",
            "computer",
            "software",
            "algorithm",
            "robot",
            "robotics",
            "digital",
            "internet",
            "machine",
        }
    ),
    "geography_culture": frozenset(
        {
            "geography",
            "culture",
            "cultural",
            "country",
            "countries",
            "continent",
            "tradition",
            "custom",
            "region",
            "nation",
            "world",
            "map",
        }
    ),
    "logic_math": frozenset(
        {
            "math",
            "mathematics",
            "mathematical",
            "logic",
            "puzzle",
            "equation",
            "geometry",
            "algebra",
            "number",
            "calculation",
            "probability",
            "statistics",
        }
    ),
    "everyday_knowledge": frozenset(
        {
            "everyday",
            "household",
            "hack",
            "trivia",
            "common",
            "ordinary",
            "daily",
            "practical",
        }
    ),
    "communication": frozenset(
        {
            "communication",
            "speaking",
            "writing",
            "conversation",
            "persuasion",
            "rhetoric",
            "public",
            "listening",
            "storytelling",
        }
    ),
    "health_wellness": frozenset(
        {
            "health",
            "wellness",
            "fitness",
            "nutrition",
            "diet",
            "exercise",
            "medicine",
            "medical",
            "disease",
            "wellbeing",
            "sleep",
        }
    ),
}


def _keywords_for_domain(domain: str) -> frozenset[str]:
    """Known domain -> its curated keyword set. Unknown/future domain ->
    a generic set derived from splitting its own name, so this never
    crashes and never silently treats an unrecognized domain as always
    compatible or always incompatible."""
    normalized = domain.strip().lower()
    if normalized in _DOMAIN_KEYWORDS:
        return _DOMAIN_KEYWORDS[normalized]
    return frozenset(w for w in normalized.replace("_", " ").split() if w)


def classify_domain_compatibility(domain: str, context_text: str) -> DomainCompatibilityResult:
    """Pure, deterministic classification — no DB access, no AI call.

    `context_text` should be the richest available free text describing
    the opportunity (title + normalized_topic + topic_summary + the
    cluster's own domain_family/canonical_label, whatever combination is
    available) — see `resolve_opportunity_domain_context` for the real
    DB-backed assembly of this string.
    """
    keywords = _keywords_for_domain(domain)
    tokens = set(normalize_topic(context_text).split())
    matched = tuple(sorted(keywords & tokens))

    if len(matched) >= 2:
        classification = DomainCompatibilityClass.compatible
    elif len(matched) == 1:
        classification = DomainCompatibilityClass.adjacent
    else:
        classification = DomainCompatibilityClass.incompatible

    return DomainCompatibilityResult(
        classification=classification, matched_keywords=matched, domain=domain
    )
