"""Phase 13D-C — Evidence-derived adjacent concept extraction.

No YouTube calls. No Opportunity creation. No scoring mutations.
No trending/viral/underserved labels persisted.
Single LLM call per parent probe. Evidence-grounded only.

Architecture (13D-C.1):
  - Reads VIDEO_TITLE observations from the dispatched probe's Phase 13B job.
  - Supplies REAL video titles + engagement stats to the LLM as grounding evidence.
  - Each LLM candidate must cite supplied evidence reference IDs (obs-* from titles).
  - Probes with enough videos but NO semantic metadata → SEMANTICALLY_UNGROUNDED →
    expansion aborted; LLM is NOT asked to invent adjacent concepts.
  - Excluded topics checked BEFORE evidence ref validation.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.ai.provider import AIProvider, AIRequest
from app.ai.registry import PromptRegistry
from app.intelligence.dedup import jaccard_similarity, normalize_topic
from app.intelligence.market.models import VIDEO_TITLE
from app.intelligence.market.planner_models import (
    CollectionPolicy,
    ExplorationProbeType,
)
from app.intelligence.market.planner_prompts import (
    ADJACENT_LLM_HARD_CAP,
    ADJACENT_MAX_LLM_CANDIDATES,
    ADJACENT_MAX_VIDEO_EVIDENCE,
    ADJACENT_MIN_SEMANTIC_EVIDENCE,
    ADJACENT_MIN_VIDEOS_FOR_EXPANSION,
    ADJACENT_PRIORITY_WEIGHTS,
    ADJACENT_PROMPT_NAME,
    ADJACENT_PROMPT_VERSION,
    DEFAULT_JACCARD_DEDUP_THRESHOLD,
    EXCLUDED_TOPIC_JACCARD_THRESHOLD,
)
from app.intelligence.market.planner_repository import (
    create_exploration_probe,
    create_exploration_run,
    get_latest_velocity_for_probe,
    get_probe_creator_diversity,
    get_probe_supporting_videos,
    list_prior_market_region_labels,
    list_prior_probe_normalized_queries,
    update_exploration_run_provenance,
    update_exploration_run_status,
    update_probe_status,
)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


# ---------------------------------------------------------------------------
# Maturity classification
# ---------------------------------------------------------------------------


class AdjacencyExpansionMaturity(StrEnum):
    INSUFFICIENT = "insufficient"  # < ADJACENT_MIN_VIDEOS_FOR_EXPANSION videos
    SEMANTICALLY_UNGROUNDED = "semantically_ungrounded"  # enough videos, no title evidence
    EXPLORATORY = "exploratory"  # enough videos + titles, no velocity
    CORROBORATED = "corroborated"  # enough videos + titles + velocity


# ---------------------------------------------------------------------------
# Typed contracts
# ---------------------------------------------------------------------------


class VideoEvidenceItem(BaseModel):
    """Single video observed in the parent probe's job, with title as semantic anchor."""

    evidence_id: str  # "obs-{title_obs_id}" — valid ref for LLM citation
    external_video_id: str
    title: str
    external_channel_id: str | None
    category_id: str | None
    view_count: float | None

    model_config = {"extra": "forbid"}


class ProbeEvidenceSummary(BaseModel):
    """Aggregated evidence derived from a dispatched probe's collection job."""

    probe_id: int
    query_text: str
    normalized_query: str
    exploration_depth: int
    video_count: int
    avg_view_count: float | None
    total_result_estimate: float | None
    top_categories: list[str]
    creator_count: int
    has_velocity: bool
    peak_velocity_per_day: float | None
    velocity_maturity: str | None
    has_semantic_evidence: bool
    video_evidence: list[VideoEvidenceItem]  # real titles from DB
    recurring_terms: list[str]  # deterministic lexical extraction
    maturity: str  # AdjacencyExpansionMaturity value
    evidence_ref_ids: list[str]  # all valid IDs the LLM may cite
    dispatched_job_id: int | None

    model_config = {"extra": "forbid"}


class AdjacentConceptCandidate(BaseModel):
    """One adjacent market region proposed by the LLM."""

    query: str
    market_region_label: str
    rationale: str
    evidence_refs: list[str]
    relation_to_parent: str
    estimated_niche_fit: float
    distinctiveness_rationale: str

    model_config = {"extra": "forbid"}


class AdjacentConceptOutput(BaseModel):
    """Structured LLM response — list of adjacent concept candidates."""

    probes: list[AdjacentConceptCandidate] = Field(max_length=ADJACENT_LLM_HARD_CAP)

    model_config = {"extra": "forbid"}


class AdjacentExpansionResult(BaseModel):
    """Typed return contract from plan_adjacent_expansion."""

    exploration_run_id: int
    parent_probe_id: int
    candidate_count: int
    selected_count: int
    deferred_count: int
    rejected_count: int
    selected_probe_ids: list[int]
    llm_used: bool
    provider_name: str | None
    model: str | None
    prompt_version: str | None
    maturity: str
    diagnostics: dict[str, Any]

    model_config = {"extra": "forbid"}


# ---------------------------------------------------------------------------
# Eligibility classification
# ---------------------------------------------------------------------------


def classify_expansion_eligibility(
    summary: ProbeEvidenceSummary,
) -> AdjacencyExpansionMaturity:
    """Classify a probe's evidence maturity for adjacent expansion.

    INSUFFICIENT          → too few distinct videos to ground any expansion
    SEMANTICALLY_UNGROUNDED → enough videos but no title evidence (no semantic grounding)
    EXPLORATORY           → enough videos + titles, no velocity
    CORROBORATED          → enough videos + titles + velocity
    """
    if summary.video_count < ADJACENT_MIN_VIDEOS_FOR_EXPANSION:
        return AdjacencyExpansionMaturity.INSUFFICIENT
    if not summary.has_semantic_evidence:
        return AdjacencyExpansionMaturity.SEMANTICALLY_UNGROUNDED
    if summary.has_velocity:
        return AdjacencyExpansionMaturity.CORROBORATED
    return AdjacencyExpansionMaturity.EXPLORATORY


# ---------------------------------------------------------------------------
# Deterministic term extraction
# ---------------------------------------------------------------------------


_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "for",
        "to",
        "in",
        "of",
        "on",
        "with",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "that",
        "this",
        "these",
        "those",
        "it",
        "its",
        "by",
        "at",
        "as",
        "do",
        "did",
        "does",
        "have",
        "has",
        "had",
        "how",
        "why",
        "what",
        "when",
        "where",
        "who",
        "which",
        "will",
        "can",
        "my",
        "your",
        "our",
        "you",
        "i",
        "we",
        "they",
        "he",
        "she",
    }
)


def _extract_recurring_terms(titles: list[str], min_doc_freq: int = 2) -> list[str]:
    """Return word-level tokens appearing in at least min_doc_freq distinct titles.

    Uses per-document counting (not raw frequency) to avoid single-title dominance.
    Returns up to 20 terms sorted by document frequency descending.
    """
    per_title_tokens: list[set[str]] = []
    for title in titles:
        raw = re.sub(r"[^\w\s]", " ", title.lower())
        tokens = {t for t in raw.split() if t and t not in _STOPWORDS and len(t) > 2}
        per_title_tokens.append(tokens)

    doc_freq: Counter[str] = Counter()
    for token_set in per_title_tokens:
        for tok in token_set:
            doc_freq[tok] += 1

    return [tok for tok, cnt in doc_freq.most_common(20) if cnt >= min_doc_freq]


# ---------------------------------------------------------------------------
# Internal evidence builders
# ---------------------------------------------------------------------------


def _get_title_observations(conn: sqlite3.Connection, job_id: int) -> list[dict]:
    """Return title observations linked to a job, ordered by obs id asc."""
    rows = conn.execute(
        """
        SELECT o.id AS obs_id,
               o.external_video_id,
               o.signal_value_text AS title,
               o.external_channel_id,
               o.category_id
          FROM market_intelligence_observations o
          JOIN market_job_observations jo ON jo.observation_id = o.id
         WHERE jo.job_id = ?
           AND o.signal_type = ?
           AND o.external_video_id IS NOT NULL
           AND o.signal_value_text IS NOT NULL
           AND o.signal_value_text != ''
         ORDER BY o.id ASC
         LIMIT ?
        """,
        (job_id, VIDEO_TITLE, ADJACENT_MAX_VIDEO_EVIDENCE),
    ).fetchall()
    return [dict(r) for r in rows]


def _get_view_counts_by_video(conn: sqlite3.Connection, job_id: int) -> dict[str, float]:
    """Return {external_video_id: max_view_count} for a job."""
    rows = conn.execute(
        """
        SELECT o.external_video_id,
               MAX(o.signal_value_numeric) AS view_count
          FROM market_intelligence_observations o
          JOIN market_job_observations jo ON jo.observation_id = o.id
         WHERE jo.job_id = ?
           AND o.signal_type = 'video_view_count'
           AND o.external_video_id IS NOT NULL
         GROUP BY o.external_video_id
        """,
        (job_id,),
    ).fetchall()
    return {r["external_video_id"]: r["view_count"] for r in rows}


def _build_video_evidence(
    title_rows: list[dict],
    view_count_map: dict[str, float],
) -> list[VideoEvidenceItem]:
    """Assemble VideoEvidenceItem list from title observations + view count lookup."""
    items: list[VideoEvidenceItem] = []
    for row in title_rows:
        vid_id = row["external_video_id"]
        items.append(
            VideoEvidenceItem(
                evidence_id=f"obs-{row['obs_id']}",
                external_video_id=vid_id,
                title=row["title"],
                external_channel_id=row.get("external_channel_id"),
                category_id=row.get("category_id"),
                view_count=view_count_map.get(vid_id),
            )
        )
    return items


def _build_evidence_summary(
    conn: sqlite3.Connection,
    probe_id: int,
    query_text: str,
    normalized_query: str,
    exploration_depth: int,
    dispatched_job_id: int | None,
) -> ProbeEvidenceSummary:
    """Aggregate observation data + semantic title evidence for a probe."""
    if dispatched_job_id is None:
        return ProbeEvidenceSummary(
            probe_id=probe_id,
            query_text=query_text,
            normalized_query=normalized_query,
            exploration_depth=exploration_depth,
            video_count=0,
            avg_view_count=None,
            total_result_estimate=None,
            top_categories=[],
            creator_count=0,
            has_velocity=False,
            peak_velocity_per_day=None,
            velocity_maturity=None,
            has_semantic_evidence=False,
            video_evidence=[],
            recurring_terms=[],
            maturity=AdjacencyExpansionMaturity.INSUFFICIENT.value,
            evidence_ref_ids=["job-none"],
            dispatched_job_id=None,
        )

    # Aggregate video stats
    videos = get_probe_supporting_videos(conn, probe_id)
    video_count = len(videos)

    view_counts = [v["view_count"] for v in videos if v.get("view_count") is not None]
    avg_view_count = sum(view_counts) / len(view_counts) if view_counts else None

    cat_freq: dict[str, int] = {}
    for v in videos:
        cat = v.get("category_id")
        if cat:
            cat_freq[cat] = cat_freq.get(cat, 0) + 1
    top_categories = sorted(cat_freq, key=lambda c: -cat_freq[c])[:3]

    creator_count = get_probe_creator_diversity(conn, probe_id)

    est_row = conn.execute(
        """
        SELECT o.signal_value_numeric
          FROM market_intelligence_observations o
          JOIN market_job_observations jo ON jo.observation_id = o.id
         WHERE jo.job_id = ? AND o.signal_type = 'search_result_total_estimate'
         ORDER BY o.observed_at DESC LIMIT 1
        """,
        (dispatched_job_id,),
    ).fetchone()
    total_result_estimate = est_row[0] if est_row else None

    vel = get_latest_velocity_for_probe(conn, probe_id)
    has_velocity = vel is not None
    peak_velocity_per_day = vel["units_per_day"] if vel else None
    velocity_maturity = vel["velocity_maturity"] if vel else None
    vel_id: int | None = vel["id"] if vel else None

    # Semantic evidence: VIDEO_TITLE observations
    title_rows = _get_title_observations(conn, dispatched_job_id)
    view_count_map = _get_view_counts_by_video(conn, dispatched_job_id)
    video_evidence = _build_video_evidence(title_rows, view_count_map)
    has_semantic_evidence = len(video_evidence) >= ADJACENT_MIN_SEMANTIC_EVIDENCE

    # Recurring terms from real titles
    all_titles = [item.title for item in video_evidence]
    recurring_terms = _extract_recurring_terms(all_titles)

    # Evidence reference IDs: title obs IDs are the primary semantic anchors
    ref_ids: list[str] = [f"job-{dispatched_job_id}"]
    ref_ids += [item.evidence_id for item in video_evidence]
    if vel_id is not None:
        ref_ids.append(f"vel-{vel_id}")

    # Compute maturity for final summary
    temp_summary = ProbeEvidenceSummary(
        probe_id=probe_id,
        query_text=query_text,
        normalized_query=normalized_query,
        exploration_depth=exploration_depth,
        video_count=video_count,
        avg_view_count=avg_view_count,
        total_result_estimate=total_result_estimate,
        top_categories=top_categories,
        creator_count=creator_count,
        has_velocity=has_velocity,
        peak_velocity_per_day=peak_velocity_per_day,
        velocity_maturity=velocity_maturity,
        has_semantic_evidence=has_semantic_evidence,
        video_evidence=video_evidence,
        recurring_terms=recurring_terms,
        maturity=AdjacencyExpansionMaturity.INSUFFICIENT.value,
        evidence_ref_ids=ref_ids,
        dispatched_job_id=dispatched_job_id,
    )
    maturity = classify_expansion_eligibility(temp_summary)

    return ProbeEvidenceSummary(
        probe_id=probe_id,
        query_text=query_text,
        normalized_query=normalized_query,
        exploration_depth=exploration_depth,
        video_count=video_count,
        avg_view_count=avg_view_count,
        total_result_estimate=total_result_estimate,
        top_categories=top_categories,
        creator_count=creator_count,
        has_velocity=has_velocity,
        peak_velocity_per_day=peak_velocity_per_day,
        velocity_maturity=velocity_maturity,
        has_semantic_evidence=has_semantic_evidence,
        video_evidence=video_evidence,
        recurring_terms=recurring_terms,
        maturity=maturity.value,
        evidence_ref_ids=ref_ids,
        dispatched_job_id=dispatched_job_id,
    )


# ---------------------------------------------------------------------------
# Priority scoring helpers
# ---------------------------------------------------------------------------


def _normalize_evidence_strength(video_count: int, avg_view_count: float | None) -> float:
    count_score = min(1.0, video_count / 25.0)
    view_score = min(1.0, (avg_view_count or 0.0) / 100_000.0)
    return round(count_score * 0.6 + view_score * 0.4, 4)


def _normalize_velocity_trigger(peak_velocity: float | None) -> float:
    if peak_velocity is None or peak_velocity <= 0:
        return 0.0
    return round(min(1.0, peak_velocity / 10_000.0), 4)


def _normalize_corroboration(creator_count: int) -> float:
    return round(min(1.0, max(0.0, (creator_count - 1) / 5.0)), 4)


def _compute_depth_factor(depth: int) -> float:
    return round(max(0.0, 1.0 - (depth - 1) * 0.3), 4)


def _compute_adjacent_priority(
    *,
    niche_fit: float,
    novelty: float,
    evidence_strength: float,
    velocity_trigger: float,
    corroboration: float,
    depth: int,
) -> tuple[float, dict[str, float]]:
    depth_factor = _compute_depth_factor(depth)
    components: dict[str, float] = {
        "niche_fit": max(0.0, min(1.0, niche_fit)),
        "novelty": max(0.0, min(1.0, novelty)),
        "evidence_strength": max(0.0, min(1.0, evidence_strength)),
        "velocity_trigger": max(0.0, min(1.0, velocity_trigger)),
        "corroboration": max(0.0, min(1.0, corroboration)),
        "depth_factor": depth_factor,
    }
    score = sum(ADJACENT_PRIORITY_WEIGHTS[k] * v for k, v in components.items())
    return round(score, 6), components


def _novelty_score(normalized_query: str, prior_queries: set[str]) -> float:
    return 0.2 if normalized_query in prior_queries else 1.0


def _default_policy(region_code: str | None, language_code: str | None) -> CollectionPolicy:
    return CollectionPolicy(
        max_pages=1,
        max_results=25,
        order="relevance",
        region_code=region_code,
        language_code=language_code,
    )


# ---------------------------------------------------------------------------
# LLM prompt assembly
# ---------------------------------------------------------------------------


def _format_video_evidence_block(video_evidence: list[VideoEvidenceItem]) -> str:
    """Render video evidence as a structured prompt block."""
    if not video_evidence:
        return "  (none)"
    lines: list[str] = []
    for item in video_evidence:
        lines.append(f"[{item.evidence_id}]")
        lines.append(f"  Title:   {item.title}")
        lines.append(f"  Video:   {item.external_video_id}")
        if item.external_channel_id:
            lines.append(f"  Channel: {item.external_channel_id}")
        if item.view_count is not None:
            lines.append(f"  Views:   {item.view_count:,.0f}")
        lines.append("")
    return "\n".join(lines).rstrip()


# ---------------------------------------------------------------------------
# Main planner entry point
# ---------------------------------------------------------------------------


def plan_adjacent_expansion(
    conn: sqlite3.Connection,
    *,
    provider: AIProvider,
    parent_probe_id: int,
    excluded_topics: list[str] | None = None,
    primary_niche: str = "",
    channel_id: int | None = None,
    workspace_id: str | None = None,
    max_probes: int = 8,
    max_depth: int = 3,
    search_budget: int = 15,
    planner_version: str = "v1",
    region_code: str | None = None,
    language_code: str | None = None,
) -> AdjacentExpansionResult:
    """Plan adjacent expansion from a single dispatched parent probe.

    Flow:
    1. Load parent probe + build evidence summary (with semantic titles)
    2. Classify maturity — INSUFFICIENT/SEMANTICALLY_UNGROUNDED abort without run
    3. Create exploration run
    4. Single LLM call with real video title evidence
    5. Validate evidence_refs; dedup; score; select within budget
    6. Record provenance and close run
    """
    from app.intelligence.market.planner_repository import get_exploration_probe

    parent = get_exploration_probe(conn, parent_probe_id)
    if parent is None:
        raise ValueError(f"Parent probe {parent_probe_id} not found")

    excluded_normalized = [normalize_topic(t) for t in (excluded_topics or []) if t.strip()]
    dedup_threshold = DEFAULT_JACCARD_DEDUP_THRESHOLD

    summary = _build_evidence_summary(
        conn,
        probe_id=parent.id,
        query_text=parent.query_text,
        normalized_query=parent.normalized_query,
        exploration_depth=parent.exploration_depth,
        dispatched_job_id=parent.dispatched_job_id,
    )

    maturity = classify_expansion_eligibility(summary)
    if maturity in (
        AdjacencyExpansionMaturity.INSUFFICIENT,
        AdjacencyExpansionMaturity.SEMANTICALLY_UNGROUNDED,
    ):
        return AdjacentExpansionResult(
            exploration_run_id=-1,
            parent_probe_id=parent_probe_id,
            candidate_count=0,
            selected_count=0,
            deferred_count=0,
            rejected_count=0,
            selected_probe_ids=[],
            llm_used=False,
            provider_name=None,
            model=None,
            prompt_version=None,
            maturity=maturity.value,
            diagnostics={
                "reason": maturity.value,
                "video_count": summary.video_count,
                "has_semantic_evidence": summary.has_semantic_evidence,
            },
        )

    child_depth = parent.exploration_depth + 1
    run = create_exploration_run(
        conn,
        channel_id=channel_id,
        workspace_id=workspace_id,
        planner_version=planner_version,
        max_depth=max_depth,
        max_probes=max_probes,
        search_budget=search_budget,
    )
    run = update_exploration_run_status(conn, run.id, status="running", started_at=_now())

    prior_queries = list_prior_probe_normalized_queries(conn, channel_id)
    prior_regions = list_prior_market_region_labels(conn, channel_id)
    valid_ref_ids: set[str] = set(summary.evidence_ref_ids)

    evidence_strength = _normalize_evidence_strength(summary.video_count, summary.avg_view_count)
    velocity_trigger = _normalize_velocity_trigger(summary.peak_velocity_per_day)
    corroboration = _normalize_corroboration(summary.creator_count)

    # LLM call
    llm_used = False
    provider_name: str | None = None
    model_used: str | None = None
    prompt_version_used: str | None = None
    llm_candidates: list[AdjacentConceptCandidate] = []
    llm_error: str | None = None

    try:
        registry = PromptRegistry()
        prompt = registry.get(ADJACENT_PROMPT_NAME, ADJACENT_PROMPT_VERSION)
        user_msg = prompt.format_user(
            parent_query=summary.query_text,
            parent_depth=str(summary.exploration_depth),
            region_code=region_code or "unspecified",
            video_count=str(summary.video_count),
            avg_view_count=(
                f"{summary.avg_view_count:,.0f}"
                if summary.avg_view_count is not None
                else "unavailable"
            ),
            total_result_estimate=(
                f"{summary.total_result_estimate:,.0f}"
                if summary.total_result_estimate is not None
                else "unavailable"
            ),
            top_categories=(
                ", ".join(summary.top_categories) if summary.top_categories else "unclassified"
            ),
            creator_count=str(summary.creator_count),
            has_velocity="yes" if summary.has_velocity else "no",
            peak_velocity_per_day=(
                f"{summary.peak_velocity_per_day:,.1f}"
                if summary.peak_velocity_per_day is not None
                else "unavailable"
            ),
            video_evidence_block=_format_video_evidence_block(summary.video_evidence),
            recurring_terms=(
                ", ".join(summary.recurring_terms) if summary.recurring_terms else "none identified"
            ),
            primary_niche=primary_niche or "not specified",
            excluded_topics=", ".join(excluded_topics) if excluded_topics else "none",
            prior_regions=", ".join(prior_regions) if prior_regions else "none",
            max_candidates=str(ADJACENT_MAX_LLM_CANDIDATES),
        )
        model_id = getattr(provider, "_model", provider.name)
        request = AIRequest(
            system=prompt.system,
            user=user_msg,
            model=model_id,
            response_schema=AdjacentConceptOutput,
            prompt_name=ADJACENT_PROMPT_NAME,
            prompt_version=ADJACENT_PROMPT_VERSION,
            max_tokens=4096,
        )
        response = provider.complete(request)
        provider_name = provider.name
        model_used = getattr(provider, "_model", provider.name)
        prompt_version_used = ADJACENT_PROMPT_VERSION

        if response.parsed is not None:
            output: AdjacentConceptOutput = response.parsed  # type: ignore[assignment]
            llm_candidates = output.probes[:ADJACENT_LLM_HARD_CAP]
            llm_used = True
        else:
            llm_error = "LLM returned non-structured response"

    except Exception as exc:
        llm_error = str(exc)

    # Selection pass
    selected_probe_ids: list[int] = []
    selected_normalized: list[str] = []
    search_calls_used = 0
    candidate_count = len(llm_candidates)
    deferred_count = 0
    rejected_count = 0

    scored: list[tuple[float, AdjacentConceptCandidate, str, dict[str, float]]] = []
    for cand in llm_candidates:
        nq = normalize_topic(cand.query)
        if not nq:
            rejected_count += 1
            continue

        # Excluded topics checked FIRST — semantic exclusion takes priority
        if any(
            jaccard_similarity(nq, ex) >= EXCLUDED_TOPIC_JACCARD_THRESHOLD
            for ex in excluded_normalized
        ):
            policy = _default_policy(region_code, language_code)
            probe = create_exploration_probe(
                conn,
                run_id=run.id,
                query_text=cand.query.strip(),
                normalized_query=nq,
                probe_type=ExplorationProbeType.ADJACENT_TOPIC.value,
                channel_id=channel_id,
                workspace_id=workspace_id,
                parent_probe_id=parent.id,
                parent_job_id=parent.dispatched_job_id,
                exploration_depth=child_depth,
                region_code=region_code,
                language_code=language_code,
                collection_policy=policy,
                planner_version=planner_version,
            )
            update_probe_status(
                conn,
                probe.id,
                status="rejected",
                decided_at=_now(),
                decision_reason="excluded_topic: overlaps channel excluded topics",
                priority_score=0.0,
            )
            rejected_count += 1
            continue

        # Validate evidence refs — must reference only supplied IDs
        invalid_refs = [r for r in cand.evidence_refs if r not in valid_ref_ids]
        if invalid_refs or not cand.evidence_refs:
            reject_reason = (
                "invalid_evidence_refs: references not in supplied evidence set"
                if invalid_refs
                else "missing_evidence_refs: must cite at least one evidence reference"
            )
            policy = _default_policy(region_code, language_code)
            probe = create_exploration_probe(
                conn,
                run_id=run.id,
                query_text=cand.query.strip(),
                normalized_query=nq,
                probe_type=ExplorationProbeType.ADJACENT_TOPIC.value,
                channel_id=channel_id,
                workspace_id=workspace_id,
                parent_probe_id=parent.id,
                parent_job_id=parent.dispatched_job_id,
                exploration_depth=child_depth,
                region_code=region_code,
                language_code=language_code,
                collection_policy=policy,
                planner_version=planner_version,
            )
            update_probe_status(
                conn,
                probe.id,
                status="rejected",
                decided_at=_now(),
                decision_reason=reject_reason,
                priority_score=0.0,
            )
            rejected_count += 1
            continue

        novelty = _novelty_score(nq, prior_queries)
        score, comps = _compute_adjacent_priority(
            niche_fit=max(0.0, min(1.0, cand.estimated_niche_fit)),
            novelty=novelty,
            evidence_strength=evidence_strength,
            velocity_trigger=velocity_trigger,
            corroboration=corroboration,
            depth=child_depth,
        )
        scored.append((score, cand, nq, comps))

    scored.sort(key=lambda x: -x[0])

    for score, cand, nq, comps in scored:
        policy = _default_policy(region_code, language_code)
        probe = create_exploration_probe(
            conn,
            run_id=run.id,
            query_text=cand.query.strip(),
            normalized_query=nq,
            probe_type=ExplorationProbeType.ADJACENT_TOPIC.value,
            channel_id=channel_id,
            workspace_id=workspace_id,
            parent_probe_id=parent.id,
            parent_job_id=parent.dispatched_job_id,
            exploration_depth=child_depth,
            region_code=region_code,
            language_code=language_code,
            collection_policy=policy,
            planner_version=planner_version,
        )

        if any(
            jaccard_similarity(nq, sel) >= dedup_threshold
            for sel in selected_normalized + [parent.normalized_query]
        ):
            update_probe_status(
                conn,
                probe.id,
                status="deferred",
                decided_at=_now(),
                decision_reason=f"near_duplicate: Jaccard >= {dedup_threshold}",
                priority_score=score,
                priority_components_json=json.dumps(comps),
                niche_fit_score=comps["niche_fit"],
            )
            deferred_count += 1

        elif (
            len(selected_probe_ids) >= max_probes
            or search_calls_used + policy.expected_max_search_calls > search_budget
        ):
            update_probe_status(
                conn,
                probe.id,
                status="deferred",
                decided_at=_now(),
                decision_reason="budget_exhausted: probe or search budget reached",
                priority_score=score,
                priority_components_json=json.dumps(comps),
                niche_fit_score=comps["niche_fit"],
            )
            deferred_count += 1

        else:
            update_probe_status(
                conn,
                probe.id,
                status="selected",
                decided_at=_now(),
                decision_reason=f"adjacent_topic: {cand.market_region_label}",
                priority_score=score,
                priority_components_json=json.dumps(comps),
                niche_fit_score=comps["niche_fit"],
            )
            selected_probe_ids.append(probe.id)
            selected_normalized.append(nq)
            search_calls_used += policy.expected_max_search_calls

    # Provenance + close run
    if llm_used and provider_name and model_used:
        update_exploration_run_provenance(
            conn,
            run.id,
            provider=provider_name,
            model=model_used,
            prompt_version=prompt_version_used,
        )

    selected_count = len(selected_probe_ids)
    final_status = "partial" if llm_error else "completed"
    update_exploration_run_status(
        conn,
        run.id,
        status=final_status,
        candidate_count=candidate_count,
        selected_count=selected_count,
        deferred_count=deferred_count,
        rejected_count=rejected_count,
        completed_at=_now(),
        error_message=llm_error,
    )

    diagnostics: dict[str, Any] = {
        "parent_probe_id": parent_probe_id,
        "child_depth": child_depth,
        "video_count": summary.video_count,
        "has_semantic_evidence": summary.has_semantic_evidence,
        "semantic_evidence_count": len(summary.video_evidence),
        "recurring_term_count": len(summary.recurring_terms),
        "search_calls_used": search_calls_used,
        "final_status": final_status,
        "evidence_ref_count": len(summary.evidence_ref_ids),
    }
    if llm_used:
        diagnostics["llm_candidate_count"] = candidate_count
    if llm_error:
        diagnostics["llm_error"] = llm_error

    return AdjacentExpansionResult(
        exploration_run_id=run.id,
        parent_probe_id=parent_probe_id,
        candidate_count=candidate_count,
        selected_count=selected_count,
        deferred_count=deferred_count,
        rejected_count=rejected_count,
        selected_probe_ids=selected_probe_ids,
        llm_used=llm_used,
        provider_name=provider_name,
        model=model_used,
        prompt_version=prompt_version_used,
        maturity=maturity.value,
        diagnostics=diagnostics,
    )
