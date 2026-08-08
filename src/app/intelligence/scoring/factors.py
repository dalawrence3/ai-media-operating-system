"""Six scoring factor functions for M3.3.

All factors receive a FactorContext (opportunity, profile, observations,
evidence, novelty context) so they never rely on global state.
compute_content_novelty also receives the pre-fetched best_similarity from
the context rather than querying the DB itself.
"""

from __future__ import annotations

import math

from app.intelligence.dedup import normalize_topic
from app.intelligence.models import (
    FactorContext,
    FactorResult,
    FactorStatus,
    ScoringPolicy,
)


def _clip(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# Factor 1: trend_strength
# ---------------------------------------------------------------------------


def compute_trend_strength(ctx: FactorContext, policy: ScoringPolicy) -> FactorResult:
    """
    Measures recency and engagement signal strength from the top video.
    Renamed from trend_velocity because single-observation evidence measures
    strength, not change over time.
    """
    obs_ids = [obs.id for obs in ctx.observations if obs.id is not None]

    ages: list[float] = []
    total_views = 0.0
    total_likes = 0.0
    contributing_obs_ids: list[int] = []
    ev_row_ids: list[int] = []

    for obs in ctx.observations:
        evs = ctx.evidence.get(obs.id or 0, [])
        for ev in evs:
            if ev.evidence_type == "top_video_age_days" and ev.evidence_value is not None:
                ages.append(ev.evidence_value)
                if obs.id:
                    contributing_obs_ids.append(obs.id)
                if ev.id:
                    ev_row_ids.append(ev.id)
            elif ev.evidence_type == "view_count" and ev.evidence_value is not None:
                total_views += ev.evidence_value
                if obs.id and obs.id not in contributing_obs_ids:
                    contributing_obs_ids.append(obs.id)
                if ev.id:
                    ev_row_ids.append(ev.id)
            elif ev.evidence_type == "like_count" and ev.evidence_value is not None:
                total_likes += ev.evidence_value
                if ev.id:
                    ev_row_ids.append(ev.id)

    has_age = bool(ages)
    has_views = total_views > 0

    if not has_age and not has_views:
        return FactorResult(
            name="trend_strength",
            raw_score=None,
            status=FactorStatus.absent,
            observation_ids=list(set(obs_ids)),
        )

    min_age = min(ages) if ages else None
    is_stale = (min_age is not None) and (min_age > policy.freshness_decay_days)

    recency: float | None = None
    if min_age is not None:
        recency = max(0.0, 1.0 - min_age / policy.freshness_decay_days)

    engagement: float | None = None
    if has_views:
        demand_proxy = math.log10(total_views + 1) / math.log10(1_000_000)
        like_ratio = total_likes / total_views if total_views > 0 else 0.0
        like_boost = _clip(like_ratio / 0.05)
        engagement = _clip(0.7 * demand_proxy + 0.3 * like_boost)

    if recency is not None and engagement is not None:
        score = 0.5 * recency + 0.5 * engagement
    elif recency is not None:
        score = recency
    else:
        score = engagement  # type: ignore[assignment]

    status = FactorStatus.degraded if is_stale else FactorStatus.present
    notes = f"min_age={min_age}d stale={is_stale}" if min_age is not None else None

    return FactorResult(
        name="trend_strength",
        raw_score=_clip(score),
        status=status,
        observation_ids=list(set(contributing_obs_ids)),
        evidence_row_ids=list(set(ev_row_ids)),
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Factor 2: audience_demand
# ---------------------------------------------------------------------------


def compute_audience_demand(ctx: FactorContext, policy: ScoringPolicy) -> FactorResult:
    """
    Numeric demand from YouTube engagement metrics only.
    manual_demand_note has evidence_value=None and contributes zero to the score.
    Its presence is noted so confidence can account for the human signal.
    """
    total_views = 0.0
    total_likes = 0.0
    total_comments = 0.0
    has_views = False
    has_engagement_only = False
    manual_note_count = 0
    contributing_obs_ids: list[int] = []
    ev_row_ids: list[int] = []
    ages: list[float] = []

    for obs in ctx.observations:
        evs = ctx.evidence.get(obs.id or 0, [])
        for ev in evs:
            if ev.evidence_type == "manual_demand_note":
                manual_note_count += 1
            elif ev.evidence_type == "view_count" and ev.evidence_value is not None:
                total_views += ev.evidence_value
                has_views = True
                if obs.id:
                    contributing_obs_ids.append(obs.id)
                if ev.id:
                    ev_row_ids.append(ev.id)
                if obs.signal_age_days is not None:
                    ages.append(obs.signal_age_days)
            elif ev.evidence_type == "like_count" and ev.evidence_value is not None:
                total_likes += ev.evidence_value
                has_engagement_only = True
                if obs.id and obs.id not in contributing_obs_ids:
                    contributing_obs_ids.append(obs.id)
                if ev.id:
                    ev_row_ids.append(ev.id)
            elif ev.evidence_type == "comment_count" and ev.evidence_value is not None:
                total_comments += ev.evidence_value
                has_engagement_only = True
                if ev.id:
                    ev_row_ids.append(ev.id)

    note_str = f"{manual_note_count} manual demand note(s) present" if manual_note_count else None

    if not has_views and not has_engagement_only:
        return FactorResult(
            name="audience_demand",
            raw_score=None,
            status=FactorStatus.absent,
            observation_ids=list(set(contributing_obs_ids)),
            notes=note_str,
        )

    if has_views:
        demand_proxy = math.log10(total_views + 1) / math.log10(10_000_000)
        total_eng = total_likes + total_comments
        eng_rate = total_eng / total_views if total_views > 0 else 0.0
        eng_score = _clip(eng_rate / 0.03)
        score = _clip(0.6 * demand_proxy + 0.4 * eng_score)
        oldest_age = max(ages) if ages else None
        is_stale = (oldest_age is not None) and (oldest_age > policy.freshness_decay_days)
        status = FactorStatus.degraded if is_stale else FactorStatus.present
        notes_parts = []
        if note_str:
            notes_parts.append(note_str)
        if oldest_age is not None:
            notes_parts.append(f"oldest_age={oldest_age}d stale={is_stale}")
        return FactorResult(
            name="audience_demand",
            raw_score=score,
            status=status,
            observation_ids=list(set(contributing_obs_ids)),
            evidence_row_ids=list(set(ev_row_ids)),
            notes="; ".join(notes_parts) if notes_parts else None,
        )

    # engagement only (no views)
    total_eng = total_likes + total_comments
    eng_proxy = math.log10(total_eng + 1) / math.log10(100_000)
    return FactorResult(
        name="audience_demand",
        raw_score=_clip(eng_proxy),
        status=FactorStatus.insufficient,
        observation_ids=list(set(contributing_obs_ids)),
        evidence_row_ids=list(set(ev_row_ids)),
        notes=note_str,
    )


# ---------------------------------------------------------------------------
# Factor 3: competition
# ---------------------------------------------------------------------------


def compute_competition(ctx: FactorContext, policy: ScoringPolicy) -> FactorResult:
    """
    Inverse of competition saturation.
    video_count_in_niche and incumbent_subscriber_count are not currently
    emitted by any adapter, so this factor returns absent for all M3.3 data.
    Algorithm is fully specified for synthetic-data testing.
    """
    video_count: float | None = None
    subscriber_count: float | None = None
    contributing_obs_ids: list[int] = []
    ev_row_ids: list[int] = []

    for obs in ctx.observations:
        evs = ctx.evidence.get(obs.id or 0, [])
        for ev in evs:
            if ev.evidence_type == "video_count_in_niche" and ev.evidence_value is not None:
                video_count = ev.evidence_value
                if obs.id:
                    contributing_obs_ids.append(obs.id)
                if ev.id:
                    ev_row_ids.append(ev.id)
            elif ev.evidence_type == "incumbent_subscriber_count" and ev.evidence_value is not None:
                subscriber_count = ev.evidence_value
                if ev.id:
                    ev_row_ids.append(ev.id)

    if video_count is None:
        return FactorResult(
            name="competition",
            raw_score=None,
            status=FactorStatus.absent,
        )

    saturation = math.log10(video_count + 1) / math.log10(1_000_000)
    score = 1.0 - _clip(saturation)

    if subscriber_count is not None:
        incumbent_strength = math.log10(subscriber_count + 1) / math.log10(10_000_000)
        score = score * (1.0 - 0.3 * incumbent_strength)

    return FactorResult(
        name="competition",
        raw_score=_clip(score),
        status=FactorStatus.present,
        observation_ids=list(set(contributing_obs_ids)),
        evidence_row_ids=list(set(ev_row_ids)),
    )


# ---------------------------------------------------------------------------
# Factor 4: evergreen_value
# ---------------------------------------------------------------------------

_TEMPORAL_TOKENS: frozenset[str] = frozenset(
    {
        "2024",
        "2025",
        "2026",
        "today",
        "now",
        "breaking",
        "latest",
        "this week",
        "this month",
        "this year",
        "trending",
        "viral",
    }
)

_TIMELESS_TOKENS: frozenset[str] = frozenset(
    {
        "how to",
        "guide",
        "basics",
        "beginner",
        "tips",
        "complete",
        "ultimate",
        "explained",
        "what is",
        "why",
        "always",
        "forever",
    }
)


def compute_evergreen_value(ctx: FactorContext, policy: ScoringPolicy) -> FactorResult:
    """
    Heuristic: always computable from the topic string alone.
    Multi-observation consistency across discovery runs adds a bonus.
    Designed to be replaced by a classifier in a future milestone.
    """
    topic_lower = ctx.opportunity.normalized_topic.lower()

    temporal_hits = sum(1 for t in _TEMPORAL_TOKENS if t in topic_lower)
    timeless_hits = sum(1 for t in _TIMELESS_TOKENS if t in topic_lower)

    base = _clip(0.5 - temporal_hits * 0.15 + timeless_hits * 0.15, 0.05, 0.95)

    unique_run_ids = {obs.discovery_run_id for obs in ctx.observations}
    if len(unique_run_ids) >= 2:
        base = min(base + 0.10, 0.95)

    obs_ids = [obs.id for obs in ctx.observations if obs.id is not None]
    return FactorResult(
        name="evergreen_value",
        raw_score=base,
        status=FactorStatus.present,
        observation_ids=obs_ids,
        notes=f"temporal_hits={temporal_hits} timeless_hits={timeless_hits}",
    )


# ---------------------------------------------------------------------------
# Factor 5: audience_fit
# ---------------------------------------------------------------------------


def compute_audience_fit(ctx: FactorContext, policy: ScoringPolicy) -> FactorResult:
    """
    Four-component lexical fit formula using normalize_topic() for stopword-aware tokenization.
    Weights: primary_coverage=0.50, secondary_coverage=0.20, desc_overlap=0.20,
             manual_note_match=0.10.
    Returns absent (raw_score=None) if niche vocabulary is empty (require_research trigger).
    """
    topic_tokens = set(normalize_topic(ctx.opportunity.normalized_topic).split())
    niche_tokens = set(normalize_topic(ctx.profile.primary_niche).split())
    secondary_tokens: set[str] = set()
    for niche in ctx.profile.secondary_niches:
        secondary_tokens |= set(normalize_topic(niche).split())
    desc_tokens = set(normalize_topic(ctx.profile.audience_description or "").split())

    obs_ids = [obs.id for obs in ctx.observations if obs.id is not None]

    if not topic_tokens:
        return FactorResult(
            name="audience_fit",
            raw_score=None,
            status=FactorStatus.absent,
            observation_ids=obs_ids,
            notes="empty topic tokens after normalization",
        )

    if not niche_tokens:
        return FactorResult(
            name="audience_fit",
            raw_score=None,
            status=FactorStatus.absent,
            observation_ids=obs_ids,
            notes="empty niche tokens — require_research triggered",
        )

    primary_coverage = len(topic_tokens & niche_tokens) / len(topic_tokens)

    secondary_coverage = (
        len(topic_tokens & secondary_tokens) / len(topic_tokens) if secondary_tokens else 0.0
    )

    desc_overlap = len(topic_tokens & desc_tokens) / len(topic_tokens) if desc_tokens else 0.0

    # Component 4: manual demand note phrase matching against niche
    manual_note_texts: list[str] = []
    ev_ids: list[int] = []
    for obs in ctx.observations:
        for ev in ctx.evidence.get(obs.id or 0, []):
            if ev.evidence_type == "manual_demand_note" and ev.evidence_text:
                manual_note_texts.append(ev.evidence_text)
                if ev.id:
                    ev_ids.append(ev.id)

    manual_niche_match = 0.0
    if manual_note_texts and niche_tokens:
        note_tokens = set(normalize_topic(" ".join(manual_note_texts)).split())
        manual_niche_match = min(len(note_tokens & niche_tokens) / len(niche_tokens), 1.0)

    fit_score = _clip(
        0.50 * primary_coverage
        + 0.20 * secondary_coverage
        + 0.20 * desc_overlap
        + 0.10 * manual_niche_match
    )

    return FactorResult(
        name="audience_fit",
        raw_score=fit_score,
        status=FactorStatus.present,
        observation_ids=obs_ids,
        evidence_row_ids=ev_ids,
        notes=(
            f"primary={primary_coverage:.2f} secondary={secondary_coverage:.2f}"
            f" desc={desc_overlap:.2f} manual={manual_niche_match:.2f}"
        ),
    )


# ---------------------------------------------------------------------------
# Factor 6: content_novelty
# ---------------------------------------------------------------------------


def compute_content_novelty(ctx: FactorContext, policy: ScoringPolicy) -> FactorResult:
    """
    Novelty is 1.0 - best_similarity to any existing non-rejected/archived sibling.
    best_similarity is pre-fetched by the engine via get_max_similarity_to_existing()
    (a distinct function from find_existing_opportunity).
    """
    obs_ids = [obs.id for obs in ctx.observations if obs.id is not None]

    if ctx.best_similarity is None:
        return FactorResult(
            name="content_novelty",
            raw_score=1.0,
            status=FactorStatus.present,
            observation_ids=obs_ids,
            notes="no prior opportunities in channel",
        )

    novelty = 1.0 - ctx.best_similarity
    return FactorResult(
        name="content_novelty",
        raw_score=_clip(novelty),
        status=FactorStatus.present,
        observation_ids=obs_ids,
        notes=f"best_similarity={ctx.best_similarity:.3f}",
    )
