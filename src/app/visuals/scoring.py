"""Semantic visual engine — deterministic candidate scoring.

A provider returning a result is not evidence that the result is relevant.
Every candidate is scored against the beat it would illustrate, and a
candidate below the acceptance floor is rejected in favour of a locally
generated explanatory graphic.

Scoring is deterministic: the same beat and the same candidate always produce
the same score, so a render is reproducible and a regression is diagnosable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.visuals.constants import (
    INTENT_NUMBER,
    LICENSE_UNSAFE,
    MEDIA_VIDEO,
    MIN_ACCEPTABLE_SCORE,
    MIN_EVIDENCE_WEIGHT,
    NUMBER_STOCK_SCORE_FLOOR,
    STRUCTURAL_INTENTS,
    STRUCTURAL_STOCK_SCORE_FLOOR,
)
from app.visuals.models import ScoredCandidate, VisualBeat, VisualCandidate

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9'’-]*")

# Weights sum to 1.0 for the reward terms; penalties subtract afterwards.
_W_SEMANTIC = 0.38
_W_MEDIA_FIT = 0.13
_W_QUERY_RANK = 0.05
_W_TECHNICAL = 0.13
_W_PROVIDER_PRIOR = 0.04
_W_DURATION = 0.09
# How much the candidate looks like it belongs to *this video* rather than to
# some other subject that happens to share a word with the beat.
_W_TOPIC = 0.18

REASON_LICENSE_UNSAFE = "license_unsafe"
REASON_NOT_COMMERCIAL_SAFE = "not_commercial_safe"
REASON_BELOW_FLOOR = "below_relevance_floor"
REASON_STRUCTURAL_FLOOR = "below_structural_floor"
REASON_AVOID_TERM = "matches_avoid_concept"
REASON_OVERUSED_IN_VIDEO = "overused_in_video"
REASON_NO_RELEVANCE_EVIDENCE = "no_relevance_evidence"
REASON_WEAK_EVIDENCE = "weak_relevance_evidence"


@dataclass
class ScoringContext:
    """Per-video state the scorer needs beyond the beat itself."""

    target_width: int = 1080
    target_height: int = 1920
    require_commercial_safe: bool = True
    # asset_key → milliseconds already allocated in this video
    used_ms_in_video: dict[str, int] = field(default_factory=dict)
    # asset_key → number of beats already using it in this video
    used_count_in_video: dict[str, int] = field(default_factory=dict)
    # asset_key → 0..1 recency-weighted prior use on this channel
    channel_reuse: dict[str, float] = field(default_factory=dict)
    # Subject vocabulary of the whole video, used to detect off-domain drift.
    topic_terms: list[str] = field(default_factory=list)
    # Named entities across the whole video. These are the high-specificity
    # terms: matching one is strong evidence, matching an ordinary word is not.
    topic_entities: list[str] = field(default_factory=list)
    # Minimum total evidence weight a candidate must carry to be accepted.
    min_evidence: float = MIN_EVIDENCE_WEIGHT
    # Minimum overall score a candidate must clear. Held in the context rather
    # than read from the constant directly so a bounded remediation pass can
    # lower the bar for beats that produced nothing, without changing the bar
    # for the ordinary first pass.
    min_score: float = MIN_ACCEPTABLE_SCORE
    # Descriptor terms of assets already chosen for this video, used to detect
    # monotony of *kind* — fifteen different clips that all look the same.
    used_descriptors: list[set[str]] = field(default_factory=list)
    max_uses_per_video: int = 2
    max_asset_total_ms: int = 12_000

    @property
    def portrait(self) -> bool:
        return self.target_height >= self.target_width


def _terms(text: str) -> set[str]:
    return {w.lower().strip("'’-") for w in _WORD_RE.findall(text or "") if len(w) > 2}


def _candidate_terms(candidate: VisualCandidate) -> set[str]:
    """Terms describing what the asset actually is.

    Deliberately excludes the query that retrieved it.  Including the query
    made every candidate appear to match its own search, so relevance scoring
    could not distinguish a real hit from a homonym.
    """
    bag: set[str] = set()
    for tag in candidate.tags:
        bag |= _terms(tag)
    bag |= _terms(candidate.title)
    return bag


def _overlap_ratio(wanted: set[str], have: set[str]) -> float:
    if not wanted:
        return 0.0
    return len(wanted & have) / len(wanted)


def _semantic_score(beat: VisualBeat, candidate: VisualCandidate) -> float:
    """How much the candidate's own metadata is about this beat.

    Weighted toward the beat's named entities, which are the terms whose
    presence or absence most reliably separates on-topic from off-topic.
    """
    have = _candidate_terms(candidate)
    keyword_hit = _overlap_ratio(set(_join(beat.keywords)), have)
    entity_hit = _overlap_ratio(set(_join(beat.entities)), have)
    query_hit = _overlap_ratio(_terms(beat.primary_query), have)

    if not beat.entities:
        return round(min(1.0, 0.6 * keyword_hit + 0.4 * query_hit), 4)
    return round(min(1.0, 0.4 * keyword_hit + 0.4 * entity_hit + 0.2 * query_hit), 4)


def _join(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        out.extend(w.lower().strip("'’-") for w in _WORD_RE.findall(value) if len(w) > 2)
    return out


def _similarity_to_used(candidate: VisualCandidate, ctx: ScoringContext) -> float:
    """Highest descriptive overlap with an asset already chosen for this video.

    Asset identity alone cannot catch monotony: ten different stock clips of
    the same abstract subject are ten distinct assets and one visual idea.
    """
    have = _candidate_terms(candidate)
    if not have or not ctx.used_descriptors:
        return 0.0
    best = 0.0
    for used in ctx.used_descriptors:
        if not used:
            continue
        union = have | used
        if union:
            best = max(best, len(have & used) / len(union))
    return round(best, 4)


def _term_specificity(term: str, high_specificity: set[str]) -> float:
    """How much it means that a candidate matched this particular word.

    Length is a crude but genuinely domain-neutral proxy for specificity, and
    named entities are promoted regardless of length ("DNA" is three letters
    and maximally specific). This is what separates a clip that is about the
    subject from one that merely shares an ordinary English word with it —
    "repair" matches a car garage and a DNA repair diagram equally well.
    """
    if term in high_specificity:
        return 1.0
    length = len(term)
    if length >= 9:
        return 0.9
    if length >= 7:
        return 0.7
    if length >= 5:
        return 0.45
    return 0.3


def _specific_tokens(entities: list[str]) -> set[str]:
    """Tokens from named entities that are unambiguous on their own.

    Only acronyms and digit-bearing tokens qualify — "DNA", "HDR", "Cas9",
    "G2". Ordinary words are excluded even when they sit inside a named
    entity, because membership in an entity says nothing about whether the
    word is ambiguous in isolation: "Homology Directed Repair" would
    otherwise promote "repair", and a car-repair clip would then count as
    strong evidence for a DNA-repair beat.

    Excluded words are not discarded — they still earn specificity by length
    in ``_term_specificity``, which is what they are actually worth.
    """
    out: set[str] = set()
    for entity in entities:
        for token in _WORD_RE.findall(entity):
            lowered = token.lower().strip("'’-")
            if len(lowered) < 2:
                continue
            if (token.isupper() and len(token) >= 2) or any(c.isdigit() for c in token):
                out.add(lowered)
    return out


def _evidence_weight(
    beat: VisualBeat, candidate: VisualCandidate, ctx: ScoringContext
) -> tuple[float, set[str]]:
    """Total specificity of every subject term the candidate actually matches."""
    have = _candidate_terms(candidate)
    wanted = set(_join(beat.keywords)) | set(_join(beat.entities)) | set(_join(ctx.topic_terms))
    matched = wanted & have
    high_specificity = _specific_tokens(beat.entities) | _specific_tokens(ctx.topic_entities)
    return sum(_term_specificity(t, high_specificity) for t in matched), matched


def _topic_coherence(candidate: VisualCandidate, ctx: ScoringContext) -> float:
    """How much the candidate belongs to this video's subject at all.

    A clip of a tailor genuinely is about "template"; it is not about the
    video.  Without this factor there is nothing to separate the two.
    """
    if not ctx.topic_terms:
        return 0.6  # neutral: no vocabulary to judge against
    return _overlap_ratio(set(_join(ctx.topic_terms)), _candidate_terms(candidate))


def _media_fit_score(beat: VisualBeat, candidate: VisualCandidate) -> float:
    preferences = beat.media_type_preferences or []
    if candidate.media_type not in preferences:
        return 0.25
    position = preferences.index(candidate.media_type)
    return max(0.2, 1.0 - position * 0.3)


def _query_rank_score(candidate: VisualCandidate, beat: VisualBeat) -> float:
    """Earlier queries and earlier provider results are weak positive priors.

    Weak on purpose: provider ordering is popularity, not relevance, and
    trusting it is how a child throwing a ball won a CRISPR scene.
    """
    try:
        query_position = [q.lower() for q in beat.search_queries].index(candidate.query.lower())
    except ValueError:
        query_position = len(beat.search_queries)
    query_component = max(0.0, 1.0 - query_position * 0.25)
    rank_component = max(0.0, 1.0 - candidate.provider_rank * 0.08)
    return round(0.5 * query_component + 0.5 * rank_component, 4)


def _technical_score(candidate: VisualCandidate, ctx: ScoringContext) -> float:
    width = candidate.width or 0
    height = candidate.height or 0
    if width <= 0 or height <= 0:
        return 0.45

    score = 0.0
    # Orientation: a portrait source survives a 9:16 crop with no loss.
    source_portrait = height >= width
    score += 0.5 if source_portrait == ctx.portrait else 0.22

    # Resolution headroom against the target frame.
    scale = min(width / max(ctx.target_width, 1), height / max(ctx.target_height, 1))
    if scale >= 1.0:
        score += 0.5
    elif scale >= 0.75:
        score += 0.35
    elif scale >= 0.5:
        score += 0.18
    return round(min(1.0, score), 4)


def _duration_score(beat: VisualBeat, candidate: VisualCandidate) -> float:
    """Reward clips long enough to cover the beat without visible looping."""
    if candidate.media_type != MEDIA_VIDEO:
        return 0.75  # stills are duration-agnostic; motion treatment covers them
    if not candidate.duration_s:
        return 0.5
    beat_s = beat.duration_ms / 1000.0
    if candidate.duration_s >= beat_s:
        # Very long clips are fine but we only ever use the head of them.
        return 1.0 if candidate.duration_s <= beat_s * 6 else 0.85
    ratio = candidate.duration_s / max(beat_s, 0.001)
    return round(max(0.15, ratio), 4)


def _provider_prior(candidate: VisualCandidate) -> float:
    return max(0.2, 1.0 - (candidate.tier - 1) * 0.18)


def _structural_floor(beat: VisualBeat) -> float:
    if beat.visual_intent == INTENT_NUMBER:
        return NUMBER_STOCK_SCORE_FLOOR
    return STRUCTURAL_STOCK_SCORE_FLOOR


def score_candidate(
    beat: VisualBeat,
    candidate: VisualCandidate,
    ctx: ScoringContext,
) -> ScoredCandidate:
    """Score one candidate for one beat, with a named factor breakdown."""
    factors: dict[str, float] = {
        "semantic": _semantic_score(beat, candidate),
        "topic": _topic_coherence(candidate, ctx),
        "media_fit": _media_fit_score(beat, candidate),
        "query_rank": _query_rank_score(candidate, beat),
        "technical": _technical_score(candidate, ctx),
        "provider_prior": _provider_prior(candidate),
        "duration": _duration_score(beat, candidate),
    }

    score = (
        _W_SEMANTIC * factors["semantic"]
        + _W_TOPIC * factors["topic"]
        + _W_MEDIA_FIT * factors["media_fit"]
        + _W_QUERY_RANK * factors["query_rank"]
        + _W_TECHNICAL * factors["technical"]
        + _W_PROVIDER_PRIOR * factors["provider_prior"]
        + _W_DURATION * factors["duration"]
    )

    # ── Penalties ───────────────────────────────────────────────────────────
    have = _candidate_terms(candidate)
    avoid_hits = len(set(_join(beat.avoid_terms)) & have)
    own_hits = len(set(_join(beat.keywords)) & have)
    avoid_penalty = 0.0
    if avoid_hits:
        # Only penalise drift: a candidate that also matches this beat's own
        # terms is on-topic and merely shares vocabulary with a sibling beat.
        avoid_penalty = min(0.3, 0.12 * max(0, avoid_hits - own_hits))
    factors["avoid_penalty"] = round(avoid_penalty, 4)
    score -= avoid_penalty

    used_ms = ctx.used_ms_in_video.get(candidate.asset_key, 0)
    used_count = ctx.used_count_in_video.get(candidate.asset_key, 0)
    repetition_penalty = 0.0
    if used_count:
        repetition_penalty += 0.28 * used_count
    if used_ms:
        repetition_penalty += 0.25 * min(1.0, used_ms / max(ctx.max_asset_total_ms, 1))
    factors["repetition_penalty"] = round(repetition_penalty, 4)
    score -= repetition_penalty

    similarity = _similarity_to_used(candidate, ctx)
    # Ramps in only past meaningful overlap so a genuinely apt second clip on
    # the same subject is not punished for sharing a word or two.
    similarity_penalty = 0.34 * max(0.0, similarity - 0.25)
    factors["similarity_penalty"] = round(similarity_penalty, 4)
    score -= similarity_penalty

    channel_penalty = 0.30 * ctx.channel_reuse.get(candidate.asset_key, 0.0)
    factors["channel_reuse_penalty"] = round(channel_penalty, 4)
    score -= channel_penalty

    evidence, matched = _evidence_weight(beat, candidate, ctx)
    factors["evidence"] = round(evidence, 4)

    score = round(max(0.0, min(1.0, score)), 4)

    # ── Hard rejections ─────────────────────────────────────────────────────
    reason: str | None = None
    if candidate.license_status == LICENSE_UNSAFE:
        reason = REASON_LICENSE_UNSAFE
    elif ctx.require_commercial_safe and not candidate.commercial_safe:
        reason = REASON_NOT_COMMERCIAL_SAFE
    elif used_count >= ctx.max_uses_per_video:
        reason = REASON_OVERUSED_IN_VIDEO
    elif used_ms >= ctx.max_asset_total_ms:
        reason = REASON_OVERUSED_IN_VIDEO
    elif not matched:
        # The provider's own description of the asset shares nothing with this
        # beat OR with the video. Technical quality cannot make an unrelated
        # clip relevant, so it must not be able to carry one over the floor.
        reason = REASON_NO_RELEVANCE_EVIDENCE
    elif evidence < ctx.min_evidence:
        # A single ordinary word in common is not evidence of aboutness.
        reason = REASON_WEAK_EVIDENCE
    elif avoid_hits and own_hits == 0 and factors["semantic"] < 0.2:
        reason = REASON_AVOID_TERM
    elif score < ctx.min_score:
        reason = REASON_BELOW_FLOOR
    elif beat.visual_intent in STRUCTURAL_INTENTS and score < _structural_floor(beat):
        # For structural intents a generated diagram is the better product,
        # so retrieved footage has to clear a distinctly higher bar.
        reason = REASON_STRUCTURAL_FLOOR

    return ScoredCandidate(
        candidate=candidate, score=score, factors=factors, rejected_reason=reason
    )


def rank_candidates(
    beat: VisualBeat,
    candidates: list[VisualCandidate],
    ctx: ScoringContext,
) -> list[ScoredCandidate]:
    """Score every candidate; accepted ones first, highest score first.

    Ties break on a stable key so ranking is reproducible across runs.
    """
    scored = [score_candidate(beat, candidate, ctx) for candidate in candidates]
    scored.sort(key=lambda s: (s.rejected_reason is not None, -s.score, s.candidate.asset_key))
    return scored
