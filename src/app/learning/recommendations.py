"""Phase 11 — optimization recommendation generators.

Each generator reads analytics aggregates for a publication and produces
zero or more RecommendationDraft objects.

Strict rules:
- Deterministic: same inputs → same drafts.
- No ML, no LLM calls, no network calls.
- Every draft carries complete evidence and explanation.
- Generators only produce recommendations — they never write to the DB.
"""

from __future__ import annotations

import sqlite3

from app.analytics.constants import (
    METRIC_AVERAGE_VIEW_DURATION,
    METRIC_CTR,
    METRIC_LIKES,
    METRIC_SHARES,
    METRIC_SUBSCRIBERS_GAINED,
    METRIC_SUBSCRIBERS_LOST,
    METRIC_VIEWS,
    METRIC_WATCH_TIME_SECONDS,
    PERIOD_LIFETIME,
)
from app.analytics.models import AnalyticsHandoff
from app.learning.attribution import resolve_attribution
from app.learning.constants import (
    CTR_HIGH_THRESHOLD,
    CTR_LOW_THRESHOLD,
    DOMAIN_ANALYTICS,
    DOMAIN_NARRATION,
    DOMAIN_PUBLISHING,
    DOMAIN_SCRIPTS,
    DOMAIN_TOPICS,
    ENGAGEMENT_LOW_THRESHOLD,
    EVIDENCE_OBSERVATIONAL,
    GENERATOR_CTR,
    GENERATOR_ENGAGEMENT,
    GENERATOR_RETENTION,
    GENERATOR_SHARES,
    GENERATOR_STATUS_FAILED,
    GENERATOR_STATUS_SUCCEEDED,
    GENERATOR_SUBSCRIBERS,
    GENERATOR_WATCH_TIME,
    LEARNING_ALGORITHM_VERSION,
    LEARNING_ENGINE_VERSION,
    LEARNING_SCHEMA_VERSION,
    MIN_CONFIDENCE_ACTIONABLE,
    MIN_UNIQUE_SNAPSHOTS_ACTIONABLE,
    RETENTION_HIGH_THRESHOLD_S,
    RETENTION_LOW_THRESHOLD_S,
    STRENGTH_ACTIONABLE,
    STRENGTH_EXPLORATORY,
    SUBSCRIBER_LOSS_THRESHOLD,
    SUBSYSTEM_CTR_TRENDS,
    SUBSYSTEM_ENGAGEMENT_TRENDS,
    SUBSYSTEM_HOOK_EFFECTIVENESS,
    SUBSYSTEM_NARRATION_PACE,
    SUBSYSTEM_RETENTION_TRENDS,
    SUBSYSTEM_SCRIPT_PACING,
    SUBSYSTEM_TITLE_EFFECTIVENESS,
    SUBSYSTEM_TOPIC_SELECTION,
    SUBSYSTEM_WATCH_TIME_TRENDS,
    confidence_label,
)
from app.learning.hashing import RecommendationHashInput, compute_recommendation_hash
from app.learning.models import (
    AllGeneratorResults,
    EvidenceItem,
    GeneratorResult,
    RecommendationDraft,
)
from app.learning.scoring import compute_confidence


def _get_lifetime_aggregate(
    conn: sqlite3.Connection,
    publication_id: int,
    metric_name: str,
) -> tuple[float | None, list[int]]:
    """Return (metric_value, snapshot_ids) for the lifetime aggregate, or (None, [])."""
    row = conn.execute(
        """
        SELECT metric_value, source_snapshot_ids_json
          FROM analytics_aggregates
         WHERE publication_id = ?
           AND period_type    = ?
           AND metric_name    = ?
         ORDER BY created_at DESC
         LIMIT 1
        """,
        (publication_id, PERIOD_LIFETIME, metric_name),
    ).fetchone()

    if row is None:
        return None, []

    import json

    try:
        snap_ids = json.loads(row["source_snapshot_ids_json"]) or []
    except (json.JSONDecodeError, TypeError):
        snap_ids = []

    return row["metric_value"], snap_ids


def _classify_evidence(handoff: AnalyticsHandoff) -> str:
    """Determine the evidence classification for a recommendation.

    Phase 11 reads only Platform Analytics aggregates, which are observational
    by definition.  experiment_id alone does NOT qualify as controlled_experiment
    evidence — that requires validated treatment/control semantics and measurement
    windows that Phase 11 does not implement.
    """
    return EVIDENCE_OBSERVATIONAL


def _classify_strength(
    confidence_score: float,
    evidence: list[EvidenceItem],
) -> str:
    """Classify a recommendation as actionable or exploratory.

    A recommendation is ACTIONABLE only when:
    - confidence_score >= MIN_CONFIDENCE_ACTIONABLE (0.4)
    - unique contributing snapshot count >= MIN_UNIQUE_SNAPSHOTS_ACTIONABLE (2)

    Both thresholds are versioned constants.  All other recommendations are
    EXPLORATORY — hypothesis-level signals that must not be auto-applied.
    """
    unique_snapshot_count = len({sid for ev in evidence for sid in ev.snapshot_ids})
    if (
        confidence_score >= MIN_CONFIDENCE_ACTIONABLE
        and unique_snapshot_count >= MIN_UNIQUE_SNAPSHOTS_ACTIONABLE
    ):
        return STRENGTH_ACTIONABLE
    return STRENGTH_EXPLORATORY


def _make_draft(
    *,
    learning_run_id: int,
    topic_id: int,
    publication_id: int | None,
    domain: str,
    subsystem: str,
    measure: str,
    title: str,
    explanation: str,
    expected_improvement: str,
    evidence: list[EvidenceItem],
    confidence_score: float,
    handoff: AnalyticsHandoff,
    evidence_classification: str = EVIDENCE_OBSERVATIONAL,
) -> RecommendationDraft:
    affected, entity_type, entity_id = resolve_attribution(domain, handoff)
    all_snapshot_ids: list[int] = sorted({sid for ev in evidence for sid in ev.snapshot_ids})
    strength = _classify_strength(confidence_score, evidence)
    effective_classification = _classify_evidence(handoff)

    inp = RecommendationHashInput(
        learning_run_id=learning_run_id,
        topic_id=topic_id,
        publication_id=publication_id,
        domain=domain,
        subsystem=subsystem,
        measure=measure,
        engine_version=LEARNING_ENGINE_VERSION,
        schema_version=LEARNING_SCHEMA_VERSION,
        algorithm_version=LEARNING_ALGORITHM_VERSION,
        evidence_classification=effective_classification,
        recommendation_strength=strength,
        evidence_snapshot_ids_sorted=all_snapshot_ids,
    )

    return RecommendationDraft(
        learning_run_id=learning_run_id,
        topic_id=topic_id,
        publication_id=publication_id,
        domain=domain,
        subsystem=subsystem,
        measure=measure,
        title=title,
        explanation=explanation,
        expected_improvement=expected_improvement,
        evidence=evidence,
        confidence=confidence_label(confidence_score),
        confidence_score=confidence_score,
        evidence_classification=effective_classification,
        recommendation_strength=strength,
        affected_subsystem=affected,
        subsystem_entity_type=entity_type,
        subsystem_entity_id=entity_id,
        experiment_id=handoff.experiment_id,
        engine_version=LEARNING_ENGINE_VERSION,
        schema_version=LEARNING_SCHEMA_VERSION,
        input_hash=compute_recommendation_hash(inp),
    )


# ── CTR generator ─────────────────────────────────────────────────────────────


def generate_ctr_recommendations(
    conn: sqlite3.Connection,
    handoff: AnalyticsHandoff,
    learning_run_id: int,
) -> list[RecommendationDraft]:
    """Generate title/hook recommendations from lifetime CTR."""
    publication_id = handoff.publication_id
    topic_id = handoff.topic_id
    drafts: list[RecommendationDraft] = []

    ctr_value, snap_ids = _get_lifetime_aggregate(conn, publication_id, METRIC_CTR)
    if ctr_value is None:
        return drafts

    ev = EvidenceItem(
        metric_name=METRIC_CTR,
        observed_value=ctr_value,
        comparison_value=CTR_LOW_THRESHOLD,
        period_type=PERIOD_LIFETIME,
        period_key="lifetime",
        snapshot_ids=snap_ids,
        interpretation=(
            f"Lifetime CTR is {ctr_value:.2%}, "
            f"which is {'below' if ctr_value < CTR_LOW_THRESHOLD else 'above'} "
            f"the {CTR_LOW_THRESHOLD:.0%} signal threshold."
        ),
    )

    if ctr_value < CTR_LOW_THRESHOLD:
        score, _ = compute_confidence([ev], ctr_value, CTR_LOW_THRESHOLD, "below")
        drafts.append(
            _make_draft(
                learning_run_id=learning_run_id,
                topic_id=topic_id,
                publication_id=publication_id,
                domain=DOMAIN_PUBLISHING,
                subsystem=SUBSYSTEM_TITLE_EFFECTIVENESS,
                measure=METRIC_CTR,
                title="Low click-through rate — review title and thumbnail",
                explanation=(
                    f"Lifetime CTR of {ctr_value:.2%} is below the {CTR_LOW_THRESHOLD:.0%} "
                    "signal threshold. Impressions are being shown but viewers are not clicking. "
                    "This is the strongest early signal that the title or thumbnail is not "
                    "compelling enough to differentiate from competing content."
                ),
                expected_improvement=(
                    "Improving title specificity, adding a number or concrete outcome, and "
                    "ensuring the thumbnail clearly illustrates the video's core promise "
                    "are all associated with CTR improvements in short-form content."
                ),
                evidence=[ev],
                confidence_score=score,
                handoff=handoff,
            )
        )
        score2, _ = compute_confidence([ev], ctr_value, CTR_LOW_THRESHOLD, "below")
        drafts.append(
            _make_draft(
                learning_run_id=learning_run_id,
                topic_id=topic_id,
                publication_id=publication_id,
                domain=DOMAIN_SCRIPTS,
                subsystem=SUBSYSTEM_HOOK_EFFECTIVENESS,
                measure=METRIC_CTR,
                title="Low click-through rate — review opening hook",
                explanation=(
                    f"Lifetime CTR of {ctr_value:.2%} is below the {CTR_LOW_THRESHOLD:.0%} "
                    "signal threshold. On Shorts the hook (first 2–3 seconds) often "
                    "determines whether viewers watch past the thumbnail. A weak hook "
                    "compounds the low-CTR problem even when titles are strong."
                ),
                expected_improvement=(
                    "Rewriting the opening hook to immediately address the viewer's question "
                    "or create curiosity is associated with lower swipe-away rates and warrants "
                    "testing to determine whether effective CTR responds positively."
                ),
                evidence=[ev],
                confidence_score=score2,
                handoff=handoff,
            )
        )

    elif ctr_value >= CTR_HIGH_THRESHOLD:
        score, _ = compute_confidence([ev], ctr_value, CTR_HIGH_THRESHOLD, "above")
        drafts.append(
            _make_draft(
                learning_run_id=learning_run_id,
                topic_id=topic_id,
                publication_id=publication_id,
                domain=DOMAIN_ANALYTICS,
                subsystem=SUBSYSTEM_CTR_TRENDS,
                measure=METRIC_CTR,
                title="Strong click-through rate — replicate title and thumbnail approach",
                explanation=(
                    f"Lifetime CTR of {ctr_value:.2%} is above the {CTR_HIGH_THRESHOLD:.0%} "
                    "strong-signal threshold. The current title and thumbnail approach is "
                    "performing well and should be used as a reference pattern for future content."
                ),
                expected_improvement=(
                    "Documenting what makes this title/thumbnail effective and applying "
                    "the same structural pattern to future videos warrants testing to "
                    "determine whether the channel's average CTR responds positively."
                ),
                evidence=[ev],
                confidence_score=score,
                handoff=handoff,
            )
        )

    return drafts


# ── Retention generator ───────────────────────────────────────────────────────


def generate_retention_recommendations(
    conn: sqlite3.Connection,
    handoff: AnalyticsHandoff,
    learning_run_id: int,
) -> list[RecommendationDraft]:
    """Generate pacing/length recommendations from average_view_duration."""
    publication_id = handoff.publication_id
    topic_id = handoff.topic_id
    drafts: list[RecommendationDraft] = []

    avd_value, snap_ids = _get_lifetime_aggregate(
        conn, publication_id, METRIC_AVERAGE_VIEW_DURATION
    )
    if avd_value is None:
        return drafts

    ev = EvidenceItem(
        metric_name=METRIC_AVERAGE_VIEW_DURATION,
        observed_value=avd_value,
        comparison_value=RETENTION_LOW_THRESHOLD_S,
        period_type=PERIOD_LIFETIME,
        period_key="lifetime",
        snapshot_ids=snap_ids,
        interpretation=(
            f"Average view duration is {avd_value:.1f}s. "
            f"Signal threshold is {RETENTION_LOW_THRESHOLD_S:.0f}s (low) / "
            f"{RETENTION_HIGH_THRESHOLD_S:.0f}s (high)."
        ),
    )

    if avd_value < RETENTION_LOW_THRESHOLD_S:
        score, _ = compute_confidence([ev], avd_value, RETENTION_LOW_THRESHOLD_S, "below")
        drafts.append(
            _make_draft(
                learning_run_id=learning_run_id,
                topic_id=topic_id,
                publication_id=publication_id,
                domain=DOMAIN_SCRIPTS,
                subsystem=SUBSYSTEM_SCRIPT_PACING,
                measure=METRIC_AVERAGE_VIEW_DURATION,
                title="Low retention — review script pacing and segment structure",
                explanation=(
                    f"Average view duration of {avd_value:.1f}s is below the "
                    f"{RETENTION_LOW_THRESHOLD_S:.0f}s low-retention threshold. "
                    "Viewers are leaving before the video's core content is delivered. "
                    "This typically indicates the opening does not match the viewer's "
                    "expectation set by the title, or the pacing is too slow for the format."
                ),
                expected_improvement=(
                    "Front-loading the value delivery, tightening segment transitions, "
                    "and reducing filler content between the hook and the first key claim "
                    "are associated with higher retention in similar Shorts-format content."
                ),
                evidence=[ev],
                confidence_score=score,
                handoff=handoff,
            )
        )
        drafts.append(
            _make_draft(
                learning_run_id=learning_run_id,
                topic_id=topic_id,
                publication_id=publication_id,
                domain=DOMAIN_NARRATION,
                subsystem=SUBSYSTEM_NARRATION_PACE,
                measure=METRIC_AVERAGE_VIEW_DURATION,
                title="Low retention — review narration pace",
                explanation=(
                    f"Average view duration of {avd_value:.1f}s suggests viewers "
                    "drop off early. Slow narration pace compounds pacing issues at "
                    "the script level by adding dead time between claims."
                ),
                expected_improvement=(
                    "Increasing narration pace (words per second) and removing "
                    "inter-segment pauses is associated with lower early drop-off in "
                    "short-form content where the script itself is already well-structured."
                ),
                evidence=[ev],
                confidence_score=score,
                handoff=handoff,
            )
        )

    elif avd_value >= RETENTION_HIGH_THRESHOLD_S:
        score, _ = compute_confidence([ev], avd_value, RETENTION_HIGH_THRESHOLD_S, "above")
        drafts.append(
            _make_draft(
                learning_run_id=learning_run_id,
                topic_id=topic_id,
                publication_id=publication_id,
                domain=DOMAIN_ANALYTICS,
                subsystem=SUBSYSTEM_RETENTION_TRENDS,
                measure=METRIC_AVERAGE_VIEW_DURATION,
                title="Strong retention — replicate script and narration approach",
                explanation=(
                    f"Average view duration of {avd_value:.1f}s is above the "
                    f"{RETENTION_HIGH_THRESHOLD_S:.0f}s strong-signal threshold. "
                    "Viewers are watching most or all of the content. This validates "
                    "the current script pacing and narration approach."
                ),
                expected_improvement=(
                    "Applying the same structural approach (hook style, segment length, "
                    "narration pace) to future videos on similar topics is associated with "
                    "maintaining the retention baseline observed here."
                ),
                evidence=[ev],
                confidence_score=score,
                handoff=handoff,
            )
        )

    return drafts


# ── Engagement generator ──────────────────────────────────────────────────────


def generate_engagement_recommendations(
    conn: sqlite3.Connection,
    handoff: AnalyticsHandoff,
    learning_run_id: int,
) -> list[RecommendationDraft]:
    """Generate engagement recommendations from likes / views ratio."""
    publication_id = handoff.publication_id
    topic_id = handoff.topic_id
    drafts: list[RecommendationDraft] = []

    views_value, _ = _get_lifetime_aggregate(conn, publication_id, METRIC_VIEWS)
    likes_value, snap_ids = _get_lifetime_aggregate(conn, publication_id, METRIC_LIKES)

    if views_value is None or likes_value is None or views_value == 0:
        return drafts

    engagement_rate = likes_value / views_value

    ev = EvidenceItem(
        metric_name=METRIC_LIKES,
        observed_value=engagement_rate,
        comparison_value=ENGAGEMENT_LOW_THRESHOLD,
        period_type=PERIOD_LIFETIME,
        period_key="lifetime",
        snapshot_ids=snap_ids,
        interpretation=(
            f"Engagement rate (likes/views) is {engagement_rate:.2%} "
            f"({likes_value:.0f} likes / {views_value:.0f} views). "
            f"Signal threshold is {ENGAGEMENT_LOW_THRESHOLD:.0%}."
        ),
    )

    if engagement_rate < ENGAGEMENT_LOW_THRESHOLD:
        score, _ = compute_confidence([ev], engagement_rate, ENGAGEMENT_LOW_THRESHOLD, "below")
        drafts.append(
            _make_draft(
                learning_run_id=learning_run_id,
                topic_id=topic_id,
                publication_id=publication_id,
                domain=DOMAIN_ANALYTICS,
                subsystem=SUBSYSTEM_ENGAGEMENT_TRENDS,
                measure=METRIC_LIKES,
                title="Low engagement rate — review content relevance and call to action",
                explanation=(
                    f"Engagement rate of {engagement_rate:.2%} is below the "
                    f"{ENGAGEMENT_LOW_THRESHOLD:.0%} signal threshold. "
                    "Viewers are watching but not interacting. This can indicate "
                    "the content satisfies curiosity without creating enough value "
                    "to prompt a like, or there is no clear call to action."
                ),
                expected_improvement=(
                    "Adding a clear call to action in the final segment, addressing "
                    "a more specific audience need, or improving content relevance "
                    "to the stated title — content that more precisely addresses audience "
                    "needs is historically associated with higher engagement rates."
                ),
                evidence=[ev],
                confidence_score=score,
                handoff=handoff,
            )
        )

    return drafts


# ── Watch time generator ──────────────────────────────────────────────────────


def generate_watch_time_recommendations(
    conn: sqlite3.Connection,
    handoff: AnalyticsHandoff,
    learning_run_id: int,
) -> list[RecommendationDraft]:
    """Generate distribution recommendations from watch_time_seconds vs views."""
    publication_id = handoff.publication_id
    topic_id = handoff.topic_id
    drafts: list[RecommendationDraft] = []

    views_value, _ = _get_lifetime_aggregate(conn, publication_id, METRIC_VIEWS)
    wt_value, wt_snap_ids = _get_lifetime_aggregate(conn, publication_id, METRIC_WATCH_TIME_SECONDS)
    avd_value, _ = _get_lifetime_aggregate(conn, publication_id, METRIC_AVERAGE_VIEW_DURATION)

    if views_value is None or wt_value is None or views_value == 0:
        return drafts

    # Expected watch time = views × average_view_duration
    # If avd is not available, use a conservative benchmark of 30s
    avd_benchmark = avd_value if avd_value is not None else 30.0
    expected_wt = views_value * avd_benchmark

    if expected_wt == 0:
        return drafts

    watch_ratio = wt_value / expected_wt

    ev = EvidenceItem(
        metric_name=METRIC_WATCH_TIME_SECONDS,
        observed_value=wt_value,
        comparison_value=expected_wt,
        period_type=PERIOD_LIFETIME,
        period_key="lifetime",
        snapshot_ids=wt_snap_ids,
        interpretation=(
            f"Watch time is {wt_value:.0f}s vs expected {expected_wt:.0f}s "
            f"({watch_ratio:.0%} of views × average_view_duration)."
        ),
    )

    if watch_ratio < 0.5:
        score, _ = compute_confidence([ev], watch_ratio, 0.5, "below")
        drafts.append(
            _make_draft(
                learning_run_id=learning_run_id,
                topic_id=topic_id,
                publication_id=publication_id,
                domain=DOMAIN_ANALYTICS,
                subsystem=SUBSYSTEM_WATCH_TIME_TRENDS,
                measure=METRIC_WATCH_TIME_SECONDS,
                title="Low total watch time relative to view count",
                explanation=(
                    f"Total watch time of {wt_value:.0f}s is {watch_ratio:.0%} of the "
                    "expected amount based on view count and average view duration. "
                    "This can indicate that aggregated metrics are inconsistent between "
                    "providers, or that a large fraction of views come from impressions "
                    "that do not translate to meaningful watch sessions."
                ),
                expected_improvement=(
                    "Reviewing the analytics data quality, re-ingesting with the latest "
                    "provider snapshot, and verifying the view-to-watch-time ratio matches "
                    "platform reports can help identify whether this is a data quality "
                    "issue or a genuine distribution problem."
                ),
                evidence=[ev],
                confidence_score=score,
                handoff=handoff,
            )
        )

    return drafts


# ── Subscriber growth generator ───────────────────────────────────────────────


def generate_subscriber_recommendations(
    conn: sqlite3.Connection,
    handoff: AnalyticsHandoff,
    learning_run_id: int,
) -> list[RecommendationDraft]:
    """Generate topic/content quality recommendations from net subscriber change."""
    publication_id = handoff.publication_id
    topic_id = handoff.topic_id
    drafts: list[RecommendationDraft] = []

    gained_value, gained_snap_ids = _get_lifetime_aggregate(
        conn, publication_id, METRIC_SUBSCRIBERS_GAINED
    )
    lost_value, lost_snap_ids = _get_lifetime_aggregate(
        conn, publication_id, METRIC_SUBSCRIBERS_LOST
    )

    if gained_value is None and lost_value is None:
        return drafts

    net = (gained_value or 0.0) - (lost_value or 0.0)
    snap_ids = list(set(gained_snap_ids + lost_snap_ids))

    ev = EvidenceItem(
        metric_name=METRIC_SUBSCRIBERS_GAINED,
        observed_value=net,
        comparison_value=0.0,
        period_type=PERIOD_LIFETIME,
        period_key="lifetime",
        snapshot_ids=snap_ids,
        interpretation=(
            f"Net subscriber change is {net:+.0f} "
            f"({gained_value or 0:.0f} gained − {lost_value or 0:.0f} lost)."
        ),
    )

    if net <= SUBSCRIBER_LOSS_THRESHOLD:
        score, _ = compute_confidence([ev], abs(net), 5.0, "above")
        drafts.append(
            _make_draft(
                learning_run_id=learning_run_id,
                topic_id=topic_id,
                publication_id=publication_id,
                domain=DOMAIN_TOPICS,
                subsystem=SUBSYSTEM_TOPIC_SELECTION,
                measure=METRIC_SUBSCRIBERS_GAINED,
                title="Net subscriber loss — review topic alignment with channel audience",
                explanation=(
                    f"This publication resulted in a net subscriber change of {net:+.0f}. "
                    "A net subscriber loss suggests the content may not align with the "
                    "expectations of existing subscribers, or attracted viewers unlikely "
                    "to subscribe to the channel's core niche."
                ),
                expected_improvement=(
                    "Reviewing the topic selection criteria, tightening the niche focus, "
                    "and ensuring the video's audience fit score (Phase 3) is above "
                    "the channel threshold before production is associated with lower "
                    "subscriber churn in similar content."
                ),
                evidence=[ev],
                confidence_score=score,
                handoff=handoff,
            )
        )

    elif net > 10:
        score, _ = compute_confidence([ev], net, 10.0, "above")
        drafts.append(
            _make_draft(
                learning_run_id=learning_run_id,
                topic_id=topic_id,
                publication_id=publication_id,
                domain=DOMAIN_TOPICS,
                subsystem=SUBSYSTEM_TOPIC_SELECTION,
                measure=METRIC_SUBSCRIBERS_GAINED,
                title="Strong subscriber growth — replicate topic and content approach",
                explanation=(
                    f"This publication resulted in a net subscriber gain of {net:+.0f}. "
                    "This is a strong indicator that the topic resonated with the "
                    "target audience and attracted new subscribers."
                ),
                expected_improvement=(
                    "Identifying and replicating the topic angle, research depth, and "
                    "script approach of this video in future content on related topics "
                    "is likely to maintain subscriber growth."
                ),
                evidence=[ev],
                confidence_score=score,
                handoff=handoff,
            )
        )

    return drafts


# ── Shares generator ──────────────────────────────────────────────────────────


def generate_shares_recommendations(
    conn: sqlite3.Connection,
    handoff: AnalyticsHandoff,
    learning_run_id: int,
) -> list[RecommendationDraft]:
    """Generate topic selection recommendations from shares."""
    publication_id = handoff.publication_id
    topic_id = handoff.topic_id
    drafts: list[RecommendationDraft] = []

    shares_value, snap_ids = _get_lifetime_aggregate(conn, publication_id, METRIC_SHARES)
    views_value, _ = _get_lifetime_aggregate(conn, publication_id, METRIC_VIEWS)

    if shares_value is None or views_value is None or views_value == 0:
        return drafts

    share_rate = shares_value / views_value

    # Only generate a positive reinforcement recommendation if shares are notable
    if share_rate < 0.005:
        return drafts

    ev = EvidenceItem(
        metric_name=METRIC_SHARES,
        observed_value=share_rate,
        comparison_value=0.005,
        period_type=PERIOD_LIFETIME,
        period_key="lifetime",
        snapshot_ids=snap_ids,
        interpretation=(
            f"Share rate is {share_rate:.2%} "
            f"({shares_value:.0f} shares / {views_value:.0f} views). "
            "Above 0.5% indicates genuinely shareable content."
        ),
    )

    score, _ = compute_confidence([ev], share_rate, 0.005, "above")
    drafts.append(
        _make_draft(
            learning_run_id=learning_run_id,
            topic_id=topic_id,
            publication_id=publication_id,
            domain=DOMAIN_TOPICS,
            subsystem=SUBSYSTEM_TOPIC_SELECTION,
            measure=METRIC_SHARES,
            title="High share rate — content has strong viral distribution potential",
            explanation=(
                f"Share rate of {share_rate:.2%} indicates this content is being "
                "actively shared by viewers. Shareable content often addresses a universal "
                "question or delivers a surprising/actionable insight."
            ),
            expected_improvement=(
                "Identifying what made this content shareable (topic angle, specificity, "
                "actionability) and applying those attributes to future topic selection "
                "is historically associated with higher organic distribution."
            ),
            evidence=[ev],
            confidence_score=score,
            handoff=handoff,
        )
    )

    return drafts


# ── Master generator ──────────────────────────────────────────────────────────


def generate_all_recommendations(
    conn: sqlite3.Connection,
    handoff: AnalyticsHandoff,
    learning_run_id: int,
) -> AllGeneratorResults:
    """Run all generators and return drafts plus per-generator outcome records.

    Generators run in a stable order for deterministic output.  Each generator
    is independent; a failure in one is recorded but does not block the others.

    Returns AllGeneratorResults:
    - .drafts: all successfully generated RecommendationDraft objects
    - .generator_results: one GeneratorResult per generator (succeeded or failed)

    The orchestrator uses generator_results to determine run status:
    - all succeeded      → completed
    - some failed        → partial
    - all failed         → failed
    """
    drafts: list[RecommendationDraft] = []
    generator_results: list[GeneratorResult] = []

    named_generators = [
        (GENERATOR_CTR, generate_ctr_recommendations),
        (GENERATOR_RETENTION, generate_retention_recommendations),
        (GENERATOR_ENGAGEMENT, generate_engagement_recommendations),
        (GENERATOR_WATCH_TIME, generate_watch_time_recommendations),
        (GENERATOR_SUBSCRIBERS, generate_subscriber_recommendations),
        (GENERATOR_SHARES, generate_shares_recommendations),
    ]

    for name, gen in named_generators:
        try:
            produced = gen(conn, handoff, learning_run_id)
            drafts.extend(produced)
            generator_results.append(
                GeneratorResult(
                    generator_name=name,
                    status=GENERATOR_STATUS_SUCCEEDED,
                    recommendation_count=len(produced),
                )
            )
        except Exception as exc:
            generator_results.append(
                GeneratorResult(
                    generator_name=name,
                    status=GENERATOR_STATUS_FAILED,
                    recommendation_count=0,
                    error_message=str(exc),
                )
            )

    return AllGeneratorResults(drafts=drafts, generator_results=generator_results)
