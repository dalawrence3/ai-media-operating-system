"""Phase 14C — Experiment eligibility gate service.

Functions never initiate a live API call internally — the ai_provider is
passed in by the caller; this service never constructs one. The one DB
mutation this module performs (Phase 17G) is narrowly scoped: persisting a
successful semantic-fit LLM result to opportunity_semantic_fit_results so a
later assessment with an identical input_hash can reuse it instead of
spending another LLM call. Nothing else is ever written.

Entry point: assess_experiment_eligibility()
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from app.intelligence.experiments.eligibility import (
    EligibilityFinding,
    EligibilityPolicy,
    ExperimentEligibilityAssessment,
    ExperimentEligibilityClassification,
    MarketFreshnessClass,
)
from app.intelligence.market.interpretation_models import SIGNAL_MATURITY_ORDERED


def _now_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _maturity_rank(maturity: str) -> int:
    try:
        return list(SIGNAL_MATURITY_ORDERED).index(maturity)
    except ValueError:
        return -1


def _normalize_for_niche_match(s: str) -> str:
    """Lowercase and collapse internal whitespace for niche comparison."""
    return " ".join(s.lower().split())


def _is_deterministic_niche_match(
    *,
    opportunity_normalized_topic: str,
    primary_niche: str,
    secondary_niches: list[str],
) -> bool:
    """True only when opportunity topic exactly matches (after normalization) the
    channel's primary niche or a secondary niche.

    This bypass is intentionally narrow — loose lexical overlap is NOT sufficient.
    Exact match means the normalized strings are identical character-for-character.
    """
    if not opportunity_normalized_topic or not primary_niche:
        return False
    norm_topic = _normalize_for_niche_match(opportunity_normalized_topic)
    if norm_topic == _normalize_for_niche_match(primary_niche):
        return True
    for niche in secondary_niches:
        if niche and norm_topic == _normalize_for_niche_match(niche):
            return True
    return False


# ---------------------------------------------------------------------------
# Individual sub-assessments (each returns findings + structured data)
# ---------------------------------------------------------------------------


def assess_market_freshness(
    conn: sqlite3.Connection,
    opportunity_id: int,
    *,
    market_signal_snapshot_id: int | None,
    policy: EligibilityPolicy,
) -> tuple[MarketFreshnessClass, float | None, list[EligibilityFinding]]:
    """Assess market knowledge age from MarketInterpretationRun.completed_at.

    Returns (freshness_class, age_hours_or_None, findings).

    IMPORTANT: uses MarketInterpretationRun.completed_at, NOT Opportunity.updated_at.
    These are distinct concepts.
    """
    findings: list[EligibilityFinding] = []

    if market_signal_snapshot_id is None:
        findings.append(
            EligibilityFinding(
                code="no_market_signal_snapshot",
                severity="block",
                message="Opportunity has no market signal snapshot; cannot assess freshness.",
            )
        )
        return MarketFreshnessClass.STALE, None, findings

    # Get signal → get cluster → get interpretation run
    signal_row = conn.execute(
        "SELECT interpretation_run_id FROM market_cluster_signals WHERE id = ?",
        (market_signal_snapshot_id,),
    ).fetchone()
    if signal_row is None:
        findings.append(
            EligibilityFinding(
                code="market_signal_not_found",
                severity="block",
                message=f"market_cluster_signals row {market_signal_snapshot_id} not found.",
            )
        )
        return MarketFreshnessClass.STALE, None, findings

    run_row = conn.execute(
        "SELECT completed_at FROM market_interpretation_runs WHERE id = ?",
        (signal_row["interpretation_run_id"],),
    ).fetchone()
    if run_row is None or run_row["completed_at"] is None:
        findings.append(
            EligibilityFinding(
                code="interpretation_run_not_completed",
                severity="warn",
                message="Market interpretation run has no completed_at; treating as STALE.",
                detail=f"run_id={signal_row['interpretation_run_id']}",
            )
        )
        return MarketFreshnessClass.STALE, None, findings

    raw_ts = run_row["completed_at"].replace("Z", "+00:00")
    completed_at = datetime.fromisoformat(raw_ts)
    if completed_at.tzinfo is None:
        completed_at = completed_at.replace(tzinfo=UTC)
    now = datetime.now(UTC)
    age_hours = (now - completed_at).total_seconds() / 3600.0

    if age_hours < policy.market_fresh_max_age_hours:
        freshness = MarketFreshnessClass.FRESH
    elif age_hours < policy.market_aging_max_age_hours:
        freshness = MarketFreshnessClass.AGING
        findings.append(
            EligibilityFinding(
                code="market_knowledge_aging",
                severity="warn",
                message=f"Market knowledge is {age_hours:.0f}h old (threshold: "
                f"{policy.market_aging_max_age_hours}h); consider refreshing.",
            )
        )
    else:
        freshness = MarketFreshnessClass.STALE
        findings.append(
            EligibilityFinding(
                code="market_knowledge_stale",
                severity="warn",
                message=f"Market knowledge is {age_hours:.0f}h old (stale threshold: "
                f"{policy.market_aging_max_age_hours}h); refresh required.",
            )
        )

    return freshness, age_hours, findings


def assess_signal_integrity(
    conn: sqlite3.Connection,
    *,
    canonical_cluster_id: int | None,
    market_signal_snapshot_id: int | None,
) -> EligibilityFinding | None:
    """Check that Opportunity.canonical_cluster_id matches the signal snapshot's cluster.

    Returns a block finding on mismatch (orphaned signal), None on success.
    """
    if market_signal_snapshot_id is None:
        return None  # already covered by assess_market_freshness

    if canonical_cluster_id is None:
        return EligibilityFinding(
            code="missing_canonical_cluster_id",
            severity="block",
            message="Opportunity has no canonical_cluster_id; cannot verify signal integrity.",
        )

    row = conn.execute(
        """
        SELECT mtc.canonical_cluster_id
        FROM market_cluster_signals mcs
        JOIN market_topic_clusters mtc ON mtc.id = mcs.cluster_id
        WHERE mcs.id = ?
        """,
        (market_signal_snapshot_id,),
    ).fetchone()

    if row is None:
        return EligibilityFinding(
            code="signal_cluster_not_found",
            severity="block",
            message=f"Cannot resolve cluster for signal {market_signal_snapshot_id}.",
        )

    if row["canonical_cluster_id"] != canonical_cluster_id:
        return EligibilityFinding(
            code="signal_canonical_mismatch",
            severity="block",
            message=(
                f"Opportunity.canonical_cluster_id={canonical_cluster_id} does not match "
                f"signal's cluster canonical_cluster_id={row['canonical_cluster_id']}. "
                "Orphaned signal — the opportunity's market identity has drifted."
            ),
            detail=f"signal_id={market_signal_snapshot_id}",
        )

    return None


def assess_excluded_topics(
    *,
    opportunity_normalized_topic: str,
    opportunity_title: str,
    excluded_topics: list[str],
) -> EligibilityFinding | None:
    """Deterministic excluded-topic guard (exact substring, case-insensitive).

    Runs before any LLM call.  Returns a block finding on match, None otherwise.
    """
    haystack_parts = [
        opportunity_normalized_topic.lower(),
        opportunity_title.lower(),
    ]
    for excluded in excluded_topics:
        needle = excluded.lower().strip()
        if not needle:
            continue
        for haystack in haystack_parts:
            if needle in haystack:
                return EligibilityFinding(
                    code="excluded_topic_match",
                    severity="block",
                    message=f"Opportunity matches excluded topic {excluded!r}.",
                    detail=(
                        f"matched_in="
                        f"{'normalized_topic' if needle in haystack_parts[0] else 'title'}"
                    ),
                )
    return None


def assess_signal_maturity_confidence(
    *,
    signal_maturity: str,
    signal_confidence: float,
    policy: EligibilityPolicy,
) -> list[EligibilityFinding]:
    """Assess signal maturity and confidence levels.

    Returns findings that inform the classification roll-up.
    No block findings here — maturity drives EXPLORATION_ONLY vs GENERAL_ELIGIBLE.
    """
    findings: list[EligibilityFinding] = []
    rank = _maturity_rank(signal_maturity)
    min_exploration_rank = _maturity_rank(policy.min_signal_maturity_for_exploration)
    min_general_rank = _maturity_rank(policy.min_signal_maturity_for_general)

    if rank < min_exploration_rank:
        findings.append(
            EligibilityFinding(
                code="insufficient_signal_maturity",
                severity="block",
                message=(
                    f"Signal maturity {signal_maturity!r} is below the minimum "
                    f"{policy.min_signal_maturity_for_exploration!r} required for any experiment."
                ),
            )
        )
    elif rank < min_general_rank:
        findings.append(
            EligibilityFinding(
                code="low_signal_maturity_exploration_only",
                severity="warn",
                message=(
                    f"Signal maturity {signal_maturity!r} allows exploration experiments only "
                    f"(general requires {policy.min_signal_maturity_for_general!r})."
                ),
            )
        )

    if signal_confidence < policy.min_confidence_for_general and rank >= min_general_rank:
        findings.append(
            EligibilityFinding(
                code="low_signal_confidence",
                severity="warn",
                message=(
                    f"Signal confidence {signal_confidence:.3f} is below the threshold "
                    f"{policy.min_confidence_for_general} for general eligibility."
                ),
            )
        )

    return findings


def assess_active_conflicts(
    conn: sqlite3.Connection,
    opportunity_id: int,
    policy: EligibilityPolicy,
) -> tuple[bool, str | None, list[EligibilityFinding]]:
    """Detect active experiments already planned or running against this opportunity.

    Returns (has_conflict, blocking_experiment_id, findings).
    """
    placeholders = ",".join("?" * len(policy.conflict_blocking_statuses))
    row = conn.execute(
        f"SELECT id FROM experiments WHERE opportunity_id = ? "
        f"AND status IN ({placeholders}) LIMIT 1",
        [opportunity_id, *policy.conflict_blocking_statuses],
    ).fetchone()

    if row is not None:
        conflict_id = row["id"]
        return (
            True,
            conflict_id,
            [
                EligibilityFinding(
                    code="active_experiment_conflict",
                    severity="block",
                    message=(
                        f"Experiment {conflict_id!r} is already active for this opportunity "
                        f"(status in: {', '.join(policy.conflict_blocking_statuses)})."
                    ),
                    detail=f"conflict_experiment_id={conflict_id}",
                )
            ],
        )

    return False, None, []


def assess_analytics_readiness(
    conn: sqlite3.Connection,
    opportunity_id: int,
    policy: EligibilityPolicy,
) -> tuple[bool, list[EligibilityFinding]]:
    """Check whether the opportunity has analytics data suitable for learning.

    Ready = at least one publication for this opportunity's topic has:
      - observation_state = 'data' (not NULL, not 'no_data')
      - views aggregate >= min_views_for_analytics_readiness
    """
    findings: list[EligibilityFinding] = []

    topic_row = conn.execute(
        "SELECT id FROM topics WHERE promoted_opportunity_id = ?",
        (opportunity_id,),
    ).fetchone()

    if topic_row is None:
        findings.append(
            EligibilityFinding(
                code="no_promoted_topic",
                severity="info",
                message="Opportunity has not been promoted to a topic; no analytics available.",
            )
        )
        return False, findings

    topic_id = topic_row["id"]

    pub_rows = conn.execute(
        """
        SELECT p.id AS pub_id
        FROM publications p
        JOIN publishing_plans pp ON pp.id = p.publishing_plan_id
        WHERE pp.topic_id = ?
        """,
        (topic_id,),
    ).fetchall()

    if not pub_rows:
        findings.append(
            EligibilityFinding(
                code="no_publications_for_topic",
                severity="info",
                message="No publications found for this opportunity's topic.",
            )
        )
        return False, findings

    ready_count = 0
    for pub_row in pub_rows:
        pub_id = pub_row["pub_id"]

        # Look for ANY snapshot with observation_state='data' (not just the latest).
        # A newer 'no_data' snapshot does not erase older valid data.
        # metric_value IS NOT NULL distinguishes "zero views observed" (0.0) from
        # "metric missing" (NULL).  Both 0.0 and NULL fail the >= threshold, but
        # for different reasons: 0.0 is observed insufficient data; NULL is absent.
        # analytics_aggregates has no snapshot_id column — provenance is via
        # source_snapshot_ids_json.  Join through analytics_metrics which carries
        # the real snapshot_id FK.
        has_valid = conn.execute(
            """
            SELECT 1 FROM analytics_snapshots ans
            JOIN analytics_metrics am ON am.snapshot_id = ans.id
            WHERE ans.publication_id = ?
              AND am.metric_name = 'views'
              AND ans.observation_state = 'data'
              AND am.metric_value IS NOT NULL
              AND am.metric_value >= ?
            LIMIT 1
            """,
            (pub_id, policy.min_views_for_analytics_readiness),
        ).fetchone()

        if has_valid is not None:
            ready_count += 1

    if ready_count > 0:
        findings.append(
            EligibilityFinding(
                code="analytics_ready",
                severity="info",
                message=f"{ready_count} publication(s) meet analytics readiness threshold.",
            )
        )
        return True, findings

    findings.append(
        EligibilityFinding(
            code="analytics_not_ready",
            severity="info",
            message=(
                f"No publications for this opportunity meet the analytics readiness "
                f"threshold (observation_state='data' and views >= "
                f"{policy.min_views_for_analytics_readiness})."
            ),
        )
    )
    return False, findings


def assess_phase12c_maturity(
    conn: sqlite3.Connection,
    opportunity_id: int,
) -> tuple[int, list[EligibilityFinding]]:
    """Summarise Phase 12C content feature evidence for this opportunity.

    Returns (publication_count_with_features, findings).
    """
    findings: list[EligibilityFinding] = []

    topic_row = conn.execute(
        "SELECT id FROM topics WHERE promoted_opportunity_id = ?",
        (opportunity_id,),
    ).fetchone()

    if topic_row is None:
        return 0, findings

    topic_id = topic_row["id"]
    count_row = conn.execute(
        "SELECT COUNT(*) AS n FROM content_feature_snapshots WHERE topic_id = ?",
        (topic_id,),
    ).fetchone()
    n = count_row["n"] if count_row else 0

    findings.append(
        EligibilityFinding(
            code="phase12c_feature_count",
            severity="info",
            message=f"{n} publication(s) have Phase 12C content feature snapshots.",
        )
    )
    return n, findings


# ---------------------------------------------------------------------------
# Semantic-fit persisted cache (Phase 17G)
#
# Keyed by a deterministic hash over exactly the inputs assess_semantic_fit
# reads. A channel profile edit creates a new ChannelProfileVersion (this
# system's constraints are versioned and immutable), which changes
# channel_profile_version_id and therefore the hash — so the cache
# self-invalidates on any material profile change with no explicit
# invalidation logic required. Only successful evaluations are cached;
# a failed/timed-out call is never persisted, so it keeps resolving to
# UNRESOLVED on the next attempt rather than freezing into a wrong answer.
# ---------------------------------------------------------------------------

_SEMANTIC_FIT_CACHE_TABLE = "opportunity_semantic_fit_results"
_SEMANTIC_FIT_MODEL = "claude-haiku-4-5-20251001"


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def compute_semantic_fit_input_hash(
    *,
    opportunity_normalized_topic: str,
    opportunity_title: str,
    opportunity_topic_summary: str,
    channel_profile_version_id: int | None,
    primary_niche: str,
    secondary_niches: list[str],
    excluded_topics: list[str],
    audience_description: str | None,
    prompt_version: str,
) -> str:
    """Deterministic cache key over exactly what assess_semantic_fit reads.

    channel_profile_version_id is included even though the other profile
    fields already capture its content — it makes the hash change the
    instant a new profile version is activated, without relying on every
    field being reproduced exactly, and makes debugging a cache miss
    (`git blame`-style) easier: the version id is directly visible.
    """
    payload = {
        "opportunity_normalized_topic": opportunity_normalized_topic,
        "opportunity_title": opportunity_title,
        "opportunity_topic_summary": opportunity_topic_summary,
        "channel_profile_version_id": channel_profile_version_id,
        "primary_niche": primary_niche,
        "secondary_niches": sorted(secondary_niches),
        "excluded_topics": sorted(excluded_topics),
        "audience_description": audience_description or "",
        "prompt_version": prompt_version,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:32]


def get_cached_semantic_fit(
    conn: sqlite3.Connection,
    *,
    opportunity_id: int,
    input_hash: str,
) -> sqlite3.Row | None:
    """Look up a previously persisted successful semantic-fit result.

    Returns None if the table doesn't exist yet (older DB / minimal test
    fixture) or no row matches — both treated identically as a cache miss.
    """
    if not _table_exists(conn, _SEMANTIC_FIT_CACHE_TABLE):
        return None
    return conn.execute(
        f"SELECT * FROM {_SEMANTIC_FIT_CACHE_TABLE} "
        "WHERE opportunity_id = ? AND input_hash = ? "
        "ORDER BY id DESC LIMIT 1",
        (opportunity_id, input_hash),
    ).fetchone()


def save_semantic_fit_result(
    conn: sqlite3.Connection,
    *,
    opportunity_id: int,
    channel_id: int,
    channel_profile_version_id: int | None,
    prompt_version: str,
    input_hash: str,
    score: float,
    fit_label: str,
    rationale: str,
    provider_name: str,
    model: str,
    specificity: TopicSpecificityResult | None = None,
) -> None:
    """Persist a successful semantic-fit evaluation. No-ops if the cache
    table doesn't exist (older DB / minimal test fixture) rather than
    raising — caching is an optimization, never a hard dependency.

    The Phase 18E specificity fields are written on the same row because they
    came from the same call; a row cached before those columns existed simply
    leaves them NULL, which reads as "not evaluated".
    """
    if not _table_exists(conn, _SEMANTIC_FIT_CACHE_TABLE):
        return
    spec = specificity or TopicSpecificityResult()
    columns = {r["name"] for r in conn.execute(f"PRAGMA table_info('{_SEMANTIC_FIT_CACHE_TABLE}')")}
    has_spec_columns = "topic_specificity" in columns

    if has_spec_columns:
        conn.execute(
            f"""
            INSERT INTO {_SEMANTIC_FIT_CACHE_TABLE}
                (opportunity_id, channel_id, channel_profile_version_id, prompt_version,
                 input_hash, score, fit_label, rationale, provider_name, model, evaluated_at,
                 topic_specificity, specificity_label, visual_groundability,
                 concrete_subjects_json, viewer_promise, refined_topic)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (opportunity_id, input_hash) DO NOTHING
            """,
            (
                opportunity_id,
                channel_id,
                channel_profile_version_id,
                prompt_version,
                input_hash,
                score,
                fit_label,
                rationale,
                provider_name,
                model,
                _now_utc(),
                spec.topic_specificity,
                spec.specificity_label,
                spec.visual_groundability,
                json.dumps(spec.concrete_subjects) if spec.evaluated else None,
                spec.viewer_promise,
                spec.refined_topic,
            ),
        )
        conn.commit()
        return

    conn.execute(
        f"""
        INSERT INTO {_SEMANTIC_FIT_CACHE_TABLE}
            (opportunity_id, channel_id, channel_profile_version_id, prompt_version,
             input_hash, score, fit_label, rationale, provider_name, model, evaluated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (opportunity_id, input_hash) DO NOTHING
        """,
        (
            opportunity_id,
            channel_id,
            channel_profile_version_id,
            prompt_version,
            input_hash,
            score,
            fit_label,
            rationale,
            provider_name,
            model,
            _now_utc(),
        ),
    )
    conn.commit()


class _SemanticFitOutput(BaseModel):
    """Pydantic schema for the semantic fit LLM response.

    Phase 18E fields are OPTIONAL so a prompt v1 response, a replayed call, or
    a fake provider still validates. Absent means "not evaluated", which the
    caller reports as unknown rather than treating as a failing score.
    """

    score: float
    fit_label: str
    rationale: str

    # Phase 18E — topic specificity / visual groundability (prompt v2+).
    topic_specificity: float | None = None
    specificity_label: str | None = None
    visual_groundability: float | None = None
    concrete_subjects: list[str] | None = None
    viewer_promise: str | None = None
    refined_topic: str | None = None

    model_config = {"extra": "forbid"}


@dataclass
class TopicSpecificityResult:
    """What the evaluator concluded about the topic being a topic at all.

    `evaluated` is False when the prompt version in use does not answer the
    question, or the response omitted it. That is deliberately distinct from
    a low score: unknown must never be read as "broad category".
    """

    evaluated: bool = False
    topic_specificity: float | None = None
    specificity_label: str | None = None
    visual_groundability: float | None = None
    concrete_subjects: list[str] = field(default_factory=list)
    viewer_promise: str | None = None
    refined_topic: str | None = None

    @property
    def is_broad_category(self) -> bool:
        return self.specificity_label == "broad_category"


def assess_semantic_fit(
    *,
    opportunity_normalized_topic: str,
    opportunity_title: str,
    opportunity_topic_summary: str,
    primary_niche: str,
    audience_description: str | None,
    excluded_topics: list[str],
    ai_provider: Any,
    policy: EligibilityPolicy,
) -> tuple[float | None, str | None, list[EligibilityFinding], TopicSpecificityResult]:
    """LLM-based semantic audience fit AND topic-specificity assessment.

    Returns (score_or_None, fit_label_or_None, findings, specificity).

    Phase 18E folded topic specificity into this call rather than adding a
    second one: it is a judgement about the same opportunity, from the same
    context, and the prompt already had the channel niche it needs. Two calls
    would have doubled the cost to ask one more question.

    Uses FakeProvider in tests.  This function never constructs a provider —
    the caller passes one in.  If ai_provider is None, skips with a warn finding.
    """
    from app.ai.provider import AIRequest
    from app.ai.registry import PromptRegistry

    findings: list[EligibilityFinding] = []

    if ai_provider is None:
        findings.append(
            EligibilityFinding(
                code="semantic_fit_skipped",
                severity="warn",
                message="No AI provider supplied; semantic audience fit was not evaluated.",
            )
        )
        return None, None, findings, TopicSpecificityResult()

    registry = PromptRegistry()
    try:
        prompt = registry.get("eligibility-semantic-fit", policy.semantic_fit_prompt_version)
    except Exception as exc:
        findings.append(
            EligibilityFinding(
                code="semantic_fit_prompt_missing",
                severity="warn",
                message=f"Semantic fit prompt not found: {exc}",
            )
        )
        return None, None, findings, TopicSpecificityResult()

    excluded_str = ", ".join(excluded_topics) if excluded_topics else "(none)"
    user_text = prompt.format_user(
        opportunity_normalized_topic=opportunity_normalized_topic,
        opportunity_title=opportunity_title or "(untitled)",
        opportunity_topic_summary=opportunity_topic_summary or "(no summary)",
        primary_niche=primary_niche,
        audience_description=audience_description or "(not specified)",
        excluded_topics=excluded_str,
    )

    rationale: str = ""
    specificity = TopicSpecificityResult()
    try:
        request = AIRequest(
            system=prompt.system,
            user=user_text,
            model=_SEMANTIC_FIT_MODEL,
            response_schema=_SemanticFitOutput,
        )
        response = ai_provider.complete(request)
        if response.parsed is not None:
            output: _SemanticFitOutput = response.parsed  # type: ignore[assignment]
            score = max(0.0, min(1.0, float(output.score)))
            fit_label = output.fit_label
            rationale = output.rationale
            specificity = _specificity_from_output(output)
        else:
            raw: dict[str, Any] = json.loads(response.raw_text)
            score = max(0.0, min(1.0, float(raw.get("score", 0.0))))
            fit_label = str(raw.get("fit_label", ""))
            rationale = str(raw.get("rationale", ""))
            specificity = _specificity_from_output(_SemanticFitOutput(**raw))
    except Exception as exc:
        findings.append(
            EligibilityFinding(
                code="semantic_fit_call_failed",
                severity="warn",
                message=f"Semantic fit LLM call failed: {exc}",
            )
        )
        return None, None, findings, TopicSpecificityResult()

    score = max(0.0, min(1.0, score))

    if score < policy.semantic_fit_min_score:
        findings.append(
            EligibilityFinding(
                code="semantic_fit_below_threshold",
                severity="block",
                message=(
                    f"Semantic audience fit score {score:.3f} is below the minimum "
                    f"{policy.semantic_fit_min_score}."
                ),
                detail=f"fit_label={fit_label!r}, rationale={rationale!r}",
            )
        )
    else:
        findings.append(
            EligibilityFinding(
                code="semantic_fit_passed",
                severity="info",
                message=f"Semantic audience fit score {score:.3f} meets threshold.",
                detail=f"fit_label={fit_label!r}, rationale={rationale!r}",
            )
        )

    findings.extend(evaluate_topic_specificity(specificity, policy=policy))

    return score, fit_label, findings, specificity


def _specificity_from_cache_row(row: Any) -> TopicSpecificityResult:
    """Rebuild a specificity result from a cached semantic-fit row."""

    def _get(key: str) -> Any:
        try:
            return row[key]
        except (IndexError, KeyError, TypeError):
            return None

    label = _get("specificity_label")
    spec = _get("topic_specificity")
    if label is None and spec is None:
        return TopicSpecificityResult()

    raw_subjects = _get("concrete_subjects_json")
    try:
        subjects = json.loads(raw_subjects) if raw_subjects else []
    except (json.JSONDecodeError, TypeError):
        subjects = []

    return TopicSpecificityResult(
        evaluated=True,
        topic_specificity=None if spec is None else float(spec),
        specificity_label=label,
        visual_groundability=(
            None if _get("visual_groundability") is None else float(_get("visual_groundability"))
        ),
        concrete_subjects=list(subjects),
        viewer_promise=_get("viewer_promise"),
        refined_topic=_get("refined_topic"),
    )


def _specificity_findings_for_bypassed_fit(
    conn: sqlite3.Connection,
    *,
    opportunity_id: int,
    semantic_fit_input_hash: str,
    policy: EligibilityPolicy,
) -> list[EligibilityFinding]:
    """Specificity findings when the FIT question was deterministically bypassed.

    Uses a cached answer when one exists; otherwise reports honestly that the
    question was not asked. It deliberately does NOT trigger an LLM call of its
    own — the bypass exists to avoid spend, and silently reintroducing a call
    here would undo that. The warn finding makes the gap visible instead.
    """
    cached = get_cached_semantic_fit(
        conn, opportunity_id=opportunity_id, input_hash=semantic_fit_input_hash
    )
    if cached is None:
        return [
            EligibilityFinding(
                code="topic_specificity_not_evaluated",
                severity="warn",
                message=(
                    "Semantic fit was bypassed by an exact niche match and no cached "
                    "specificity answer exists, so topic concreteness was not gated. "
                    "Note that an exact niche match is category-shaped by construction."
                ),
            )
        ]
    return evaluate_topic_specificity(_specificity_from_cache_row(cached), policy=policy)


def _specificity_from_output(output: _SemanticFitOutput) -> TopicSpecificityResult:
    """Read the Phase 18E fields out of a structured response.

    A response that omits them (prompt v1, or a provider that answered only
    the fit question) yields evaluated=False, not zeros.
    """
    if output.topic_specificity is None and output.specificity_label is None:
        return TopicSpecificityResult()
    return TopicSpecificityResult(
        evaluated=True,
        topic_specificity=(
            None
            if output.topic_specificity is None
            else max(0.0, min(1.0, float(output.topic_specificity)))
        ),
        specificity_label=output.specificity_label,
        visual_groundability=(
            None
            if output.visual_groundability is None
            else max(0.0, min(1.0, float(output.visual_groundability)))
        ),
        concrete_subjects=list(output.concrete_subjects or []),
        viewer_promise=output.viewer_promise,
        refined_topic=output.refined_topic,
    )


def evaluate_topic_specificity(
    specificity: TopicSpecificityResult,
    *,
    policy: EligibilityPolicy,
) -> list[EligibilityFinding]:
    """Turn a specificity result into eligibility findings.

    Blocks a candidate that names a CATEGORY rather than a subject. This is
    the gate that "history and society" needed and semantic fit could never
    provide: that topic scored 0.8 strong_fit, correctly, because it genuinely
    suits the channel's audience. Fit and specificity are different questions.

    Fail-open on unknown. An unevaluated topic produces a warn, never a block,
    for the same reason a missing visual assessment does not block publishing:
    "we did not ask" must not be recorded as "the answer was bad".
    """
    findings: list[EligibilityFinding] = []

    if not specificity.evaluated:
        findings.append(
            EligibilityFinding(
                code="topic_specificity_not_evaluated",
                severity="warn",
                message=(
                    "Topic specificity was not evaluated; the prompt version in use does "
                    "not answer it. The candidate was not gated on concreteness."
                ),
            )
        )
        return findings

    detail = (
        f"specificity={specificity.topic_specificity}, "
        f"label={specificity.specificity_label!r}, "
        f"groundability={specificity.visual_groundability}, "
        f"subjects={specificity.concrete_subjects}, "
        f"promise={specificity.viewer_promise!r}"
        + (f", refined_topic={specificity.refined_topic!r}" if specificity.refined_topic else "")
    )

    spec = specificity.topic_specificity
    if spec is not None and spec < policy.min_topic_specificity:
        findings.append(
            EligibilityFinding(
                code="topic_not_concrete",
                severity="block",
                message=(
                    f"Topic specificity {spec:.2f} is below the minimum "
                    f"{policy.min_topic_specificity} "
                    f"({specificity.specificity_label or 'unlabelled'}). This names a category, "
                    "not a video topic."
                ),
                detail=detail,
            )
        )
        return findings

    ground = specificity.visual_groundability
    if ground is not None and ground < policy.min_visual_groundability:
        findings.append(
            EligibilityFinding(
                code="topic_not_visually_groundable",
                severity="block",
                message=(
                    f"Visual groundability {ground:.2f} is below the minimum "
                    f"{policy.min_visual_groundability}; there is nothing specific to show, "
                    "so production would fall back to text cards."
                ),
                detail=detail,
            )
        )
        return findings

    findings.append(
        EligibilityFinding(
            code="topic_specificity_passed",
            severity="info",
            message=(
                f"Concrete topic (specificity {spec if spec is not None else '?'}, "
                f"groundability {ground if ground is not None else '?'})."
            ),
            detail=detail,
        )
    )
    return findings


# ── Phase 18E.1: production topic selection ──────────────────────────────────

# Deterministic bounds for a usable refinement. These are shape checks, not
# quality judgements — the quality judgement was made by the evaluation that
# produced the string, and re-litigating it here would mean a second opinion
# with less context than the first.
_MIN_REFINEMENT_WORDS = 3
_MAX_REFINEMENT_WORDS = 25
_MAX_REFINEMENT_CHARS = 200


@dataclass
class ProductionTopicDecision:
    """Which string becomes the production topic, and why.

    Carries `source_topic` alongside `production_topic` so the caller never has
    to reconstruct what the opportunity originally said — the two concepts stay
    separate all the way to the topic row.
    """

    production_topic: str
    source_topic: str
    used_refinement: bool
    reason: str
    specificity_label: str | None = None
    viewer_promise: str | None = None
    concrete_subjects: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.used_refinement and self.production_topic != self.source_topic


def _refinement_is_well_formed(refinement: str | None, *, source_topic: str) -> tuple[bool, str]:
    """Deterministic shape checks on a candidate refinement.

    Cheap, explainable, and no second LLM call: the refinement came from the
    same structured response as the specificity verdict, so the model has
    already been asked. What is left is guarding against a degenerate string —
    blank, absurdly long, or a restatement of the source that would change
    nothing while looking like a decision.
    """
    if refinement is None:
        return False, "no refinement was generated"
    candidate = " ".join(refinement.split())
    if not candidate:
        return False, "refinement was blank"
    if len(candidate) > _MAX_REFINEMENT_CHARS:
        return False, f"refinement exceeds {_MAX_REFINEMENT_CHARS} characters"

    words = candidate.split()
    if len(words) < _MIN_REFINEMENT_WORDS:
        return False, f"refinement has fewer than {_MIN_REFINEMENT_WORDS} words"
    if len(words) > _MAX_REFINEMENT_WORDS:
        return False, f"refinement has more than {_MAX_REFINEMENT_WORDS} words"

    if candidate.casefold() == " ".join(source_topic.split()).casefold():
        return False, "refinement is identical to the source topic"

    return True, ""


def select_production_topic(
    conn: sqlite3.Connection,
    *,
    opportunity_id: int,
    source_topic: str,
    input_hash: str | None = None,
    policy: EligibilityPolicy | None = None,
) -> ProductionTopicDecision:
    """Decide which topic string production should actually use.

    Phase 18E generated a refinement for every evaluated opportunity and then
    threw it away: `promote_opportunity` reads `opportunities.title` and has no
    notion that a better framing exists. This is the function that closes that
    gap, and it is deliberately conservative about when it does so.

    The rules, and why each is what it is:

    * `concrete_topic` — keep the original. A topic that already reads as a
      single specific subject does not need rewriting, and swapping it for a
      model's paraphrase would churn production topics for no gain.

    * `narrow_theme` — use the refinement. This is the case the phase exists
      for: the topic is usable but only after someone decides what the video is
      actually about, and the evaluation already made that decision.

    * `broad_category` — never. Such candidates are blocked at eligibility
      (`topic_not_concrete`, severity=block → INELIGIBLE), so they cannot reach
      materialization at all; refusing here as well means a refinement can
      never become a backdoor around that verdict even if an upstream caller
      changes.

    * unevaluated — keep the original, matching Phase 18E's fail-open stance:
      "we did not ask" must not become "we decided".

    Never raises. A topic that cannot be improved is not a failure, and a
    materialization must not break because an evaluation is missing.
    """
    effective_policy = policy or EligibilityPolicy.v1()
    decision = ProductionTopicDecision(
        production_topic=source_topic,
        source_topic=source_topic,
        used_refinement=False,
        reason="no evaluation available",
    )

    try:
        row = (
            get_cached_semantic_fit(conn, opportunity_id=opportunity_id, input_hash=input_hash)
            if input_hash
            else _latest_semantic_fit_row(conn, opportunity_id)
        )
    except Exception as exc:  # noqa: BLE001 — an unreadable evaluation is not a verdict
        decision.reason = f"semantic evaluation could not be read: {exc}"
        return decision

    if row is None:
        return decision

    spec = _specificity_from_cache_row(row)
    decision.specificity_label = spec.specificity_label
    decision.viewer_promise = spec.viewer_promise
    decision.concrete_subjects = list(spec.concrete_subjects)

    if not spec.evaluated:
        decision.reason = "topic specificity was never evaluated"
        return decision

    if spec.is_broad_category:
        decision.reason = (
            "source is a broad category; a refinement must not bypass the eligibility block"
        )
        return decision

    if spec.specificity_label == "concrete_topic":
        decision.reason = "source topic is already concrete"
        return decision

    # Only a candidate that cleared the Phase 18E floors may be refined — the
    # refinement inherits the evaluation's authority, so that evaluation has to
    # have passed on its own terms first.
    gate_findings = evaluate_topic_specificity(spec, policy=effective_policy)
    if any(f.severity == "block" for f in gate_findings):
        decision.reason = "evaluation did not clear the specificity/groundability floors"
        return decision

    ok, why_not = _refinement_is_well_formed(spec.refined_topic, source_topic=source_topic)
    if not ok:
        decision.reason = why_not
        return decision

    decision.production_topic = " ".join((spec.refined_topic or "").split())
    decision.used_refinement = True
    decision.reason = f"source was {spec.specificity_label!r}; using the evaluated refinement"
    return decision


def _latest_semantic_fit_row(conn: sqlite3.Connection, opportunity_id: int) -> Any:
    """The most recent evaluation for this opportunity, whatever its input hash.

    Materialization happens well after eligibility, and the caller does not
    carry the hash the planner used. Taking the newest row is correct because
    a newer evaluation supersedes an older one by construction — the hash only
    changes when the prompt, the profile or the opportunity text changed.
    """
    if not _table_exists(conn, _SEMANTIC_FIT_CACHE_TABLE):
        return None
    return conn.execute(
        f"SELECT * FROM {_SEMANTIC_FIT_CACHE_TABLE} "
        "WHERE opportunity_id = ? ORDER BY evaluated_at DESC, id DESC LIMIT 1",
        (opportunity_id,),
    ).fetchone()


# ---------------------------------------------------------------------------
# Classification roll-up
# ---------------------------------------------------------------------------


def _roll_up_classification(
    findings: list[EligibilityFinding],
    *,
    market_freshness: MarketFreshnessClass | None,
    signal_maturity: str | None,
    signal_confidence: float,
    semantic_fit_score: float | None,
    semantic_fit_called: bool,
    policy: EligibilityPolicy,
) -> ExperimentEligibilityClassification:
    """Derive classification from accumulated findings.

    Priority (highest severity wins):
      INELIGIBLE:       any block finding
      UNRESOLVED:       semantic fit called but result is None (call failed)
      REQUIRES_REFRESH: market knowledge STALE and no other block
      EXPLORATION_ONLY: low maturity, low confidence, or AGING market
      GENERAL_ELIGIBLE: all checks pass
    """
    has_block = any(f.severity == "block" for f in findings)
    if has_block:
        return ExperimentEligibilityClassification.INELIGIBLE

    # Semantic fit was attempted but returned no score (provider call failed)
    if semantic_fit_called and semantic_fit_score is None:
        return ExperimentEligibilityClassification.UNRESOLVED

    if market_freshness == MarketFreshnessClass.STALE:
        return ExperimentEligibilityClassification.REQUIRES_REFRESH

    # Signal maturity / confidence checks
    if signal_maturity is not None:
        rank = _maturity_rank(signal_maturity)
        general_rank = _maturity_rank(policy.min_signal_maturity_for_general)
        if rank < general_rank:
            return ExperimentEligibilityClassification.EXPLORATION_ONLY
        if signal_confidence < policy.min_confidence_for_general:
            return ExperimentEligibilityClassification.EXPLORATION_ONLY

    return ExperimentEligibilityClassification.GENERAL_ELIGIBLE


# ---------------------------------------------------------------------------
# Main gate
# ---------------------------------------------------------------------------


def assess_experiment_eligibility(
    conn: sqlite3.Connection,
    opportunity_id: int,
    channel_id: int,
    ai_provider: Any = None,
    policy: EligibilityPolicy | None = None,
) -> ExperimentEligibilityAssessment:
    """Deterministic eligibility gate for autonomous experiment planning.

    This function is read-only — it never mutates the DB, never calls YouTube,
    and never triggers content generation.  The ai_provider is used only for
    semantic audience fit scoring; pass None to skip that sub-assessment.

    Returns a fully typed ExperimentEligibilityAssessment.
    """
    from app.intelligence.repository import (
        get_active_profile_version,
        get_opportunity,
    )

    if policy is None:
        policy = EligibilityPolicy.v1()

    assessed_at = _now_utc()
    all_findings: list[EligibilityFinding] = []

    # ── 1. Load opportunity ────────────────────────────────────────────────
    opportunity = get_opportunity(conn, opportunity_id)
    if opportunity is None:
        return ExperimentEligibilityAssessment(
            opportunity_id=opportunity_id,
            channel_id=channel_id,
            classification=ExperimentEligibilityClassification.INELIGIBLE,
            findings=[
                EligibilityFinding(
                    code="opportunity_not_found",
                    severity="block",
                    message=f"Opportunity {opportunity_id} not found.",
                )
            ],
            policy_snapshot_json=policy.to_json(),
            assessed_at=assessed_at,
        )

    # ── 2. Load channel profile ────────────────────────────────────────────
    channel_profile = get_active_profile_version(conn, channel_id)
    excluded_topics: list[str] = []
    primary_niche = ""
    audience_description: str | None = None
    if channel_profile is not None:
        excluded_topics = channel_profile.excluded_topics or []
        primary_niche = channel_profile.primary_niche or ""
        audience_description = channel_profile.audience_description

    # ── 3. Excluded-topic guard (deterministic, before any LLM call) ───────
    topic_finding = assess_excluded_topics(
        opportunity_normalized_topic=opportunity.normalized_topic,
        opportunity_title=opportunity.title or "",
        excluded_topics=excluded_topics,
    )
    if topic_finding is not None:
        all_findings.append(topic_finding)

    # ── 4. Market signal integrity ──────────────────────────────────────────
    integrity_finding = assess_signal_integrity(
        conn,
        canonical_cluster_id=opportunity.canonical_cluster_id,
        market_signal_snapshot_id=opportunity.market_signal_snapshot_id,
    )
    if integrity_finding is not None:
        all_findings.append(integrity_finding)

    # ── 5. Market freshness ─────────────────────────────────────────────────
    freshness_class, age_hours, freshness_findings = assess_market_freshness(
        conn,
        opportunity_id=opportunity_id,
        market_signal_snapshot_id=opportunity.market_signal_snapshot_id,
        policy=policy,
    )
    all_findings.extend(freshness_findings)

    # ── 6. Signal maturity + confidence ────────────────────────────────────
    signal_maturity: str | None = None
    signal_confidence: float = 0.0

    if opportunity.market_signal_snapshot_id is not None:
        sig_row = conn.execute(
            "SELECT signal_maturity, confidence FROM market_cluster_signals WHERE id = ?",
            (opportunity.market_signal_snapshot_id,),
        ).fetchone()
        if sig_row is not None:
            signal_maturity = sig_row["signal_maturity"]
            signal_confidence = sig_row["confidence"]

    if signal_maturity is not None:
        maturity_findings = assess_signal_maturity_confidence(
            signal_maturity=signal_maturity,
            signal_confidence=signal_confidence,
            policy=policy,
        )
        all_findings.extend(maturity_findings)

    # ── 7. Active experiment conflict detection ─────────────────────────────
    has_conflict, conflict_exp_id, conflict_findings = assess_active_conflicts(
        conn, opportunity_id, policy
    )
    all_findings.extend(conflict_findings)

    # ── 8. Semantic audience fit ────────────────────────────────────────────
    # Phase 14C.1: this gate must never be silently skipped.
    # When the topic does not deterministically match the channel niche AND
    # no AI provider is available, the classification is UNRESOLVED — not
    # promoted to GENERAL_ELIGIBLE by omission.
    fit_score: float | None = None
    fit_label: str | None = None
    semantic_fit_called = False
    semantic_fit_disposition: str | None = None

    # Short-circuit: if hard block findings already accumulated, the result is
    # INELIGIBLE regardless of semantic fit.  Skip the LLM call.
    has_blocks_so_far = any(f.severity == "block" for f in all_findings)

    if has_blocks_so_far:
        semantic_fit_disposition = "skipped_hard_block"
    else:
        secondary_niches: list[str] = channel_profile.secondary_niches if channel_profile else []
        deterministic_bypass = _is_deterministic_niche_match(
            opportunity_normalized_topic=opportunity.normalized_topic,
            primary_niche=primary_niche,
            secondary_niches=secondary_niches,
        )

        semantic_fit_input_hash = compute_semantic_fit_input_hash(
            opportunity_normalized_topic=opportunity.normalized_topic,
            opportunity_title=opportunity.title or "",
            opportunity_topic_summary=opportunity.topic_summary or "",
            channel_profile_version_id=channel_profile.id if channel_profile else None,
            primary_niche=primary_niche,
            secondary_niches=secondary_niches,
            excluded_topics=excluded_topics,
            audience_description=audience_description,
            prompt_version=policy.semantic_fit_prompt_version,
        )
        cached_fit = (
            None
            if deterministic_bypass
            else get_cached_semantic_fit(
                conn,
                opportunity_id=opportunity_id,
                input_hash=semantic_fit_input_hash,
            )
        )

        if deterministic_bypass:
            # Exact niche match — semantic evaluator adds no information ABOUT FIT.
            #
            # It adds a great deal about specificity, and this is precisely the
            # case where that matters most: a channel's primary_niche is itself
            # a category ("science and technology explained"), so an opportunity
            # that matches it exactly is a category by construction. Bypassing
            # the specificity question here would exempt the single most
            # category-like candidate on the channel.
            fit_score = 1.0
            fit_label = "deterministic_niche_match"
            semantic_fit_disposition = "deterministic_bypass"
            all_findings.append(
                EligibilityFinding(
                    code="semantic_fit_deterministic_bypass",
                    severity="info",
                    message=(
                        "Semantic fit bypassed: opportunity topic exactly matches channel niche."
                    ),
                )
            )
            all_findings.extend(
                _specificity_findings_for_bypassed_fit(
                    conn,
                    opportunity_id=opportunity_id,
                    semantic_fit_input_hash=semantic_fit_input_hash,
                    policy=policy,
                )
            )
        elif cached_fit is not None:
            # A prior real LLM call already resolved this exact input — reuse
            # it rather than spending another call. Compare against the
            # threshold exactly as assess_semantic_fit itself would.
            semantic_fit_called = True
            fit_score = float(cached_fit["score"])
            fit_label = cached_fit["fit_label"]
            semantic_fit_disposition = "cache_hit"
            if fit_score < policy.semantic_fit_min_score:
                all_findings.append(
                    EligibilityFinding(
                        code="semantic_fit_below_threshold",
                        severity="block",
                        message=(
                            f"Semantic audience fit score {fit_score:.3f} (cached) is below the "
                            f"minimum {policy.semantic_fit_min_score}."
                        ),
                        detail=f"fit_label={fit_label!r}, cached_at={cached_fit['evaluated_at']}",
                    )
                )
            else:
                all_findings.append(
                    EligibilityFinding(
                        code="semantic_fit_passed",
                        severity="info",
                        message=(
                            f"Semantic audience fit score {fit_score:.3f} (cached) meets threshold."
                        ),
                        detail=f"cached_at={cached_fit['evaluated_at']}",
                    )
                )
            # The cached row carries the specificity answer from the same call.
            # Re-running it through the gate (rather than storing a verdict)
            # means a threshold change takes effect without invalidating the
            # cache — the expensive part was the call, not the comparison.
            all_findings.extend(
                evaluate_topic_specificity(_specificity_from_cache_row(cached_fit), policy=policy)
            )
        elif ai_provider is not None:
            semantic_fit_called = True
            _provider_name = getattr(ai_provider, "provider_name", None) or getattr(
                ai_provider, "name", None
            )
            if _provider_name == "fake":
                semantic_fit_disposition = "fake_provider_test"
            elif _provider_name == "replay":
                semantic_fit_disposition = "replay_prior_real_call"
            else:
                semantic_fit_disposition = "provider_called"
            fit_score, fit_label, fit_findings, fit_specificity = assess_semantic_fit(
                opportunity_normalized_topic=opportunity.normalized_topic,
                opportunity_title=opportunity.title or "",
                opportunity_topic_summary=opportunity.topic_summary or "",
                primary_niche=primary_niche,
                audience_description=audience_description,
                excluded_topics=excluded_topics,
                ai_provider=ai_provider,
                policy=policy,
            )
            all_findings.extend(fit_findings)
            # Only successful, real (non-fake/replay) calls are cached — a
            # fake/replay result isn't a genuine LLM answer worth reusing,
            # and a failed call (fit_score is None) must stay UNRESOLVED.
            if fit_score is not None and _provider_name not in ("fake", "replay"):
                save_semantic_fit_result(
                    conn,
                    opportunity_id=opportunity_id,
                    channel_id=channel_id,
                    channel_profile_version_id=channel_profile.id if channel_profile else None,
                    prompt_version=policy.semantic_fit_prompt_version,
                    input_hash=semantic_fit_input_hash,
                    score=fit_score,
                    fit_label=fit_label or "",
                    rationale=next(
                        (
                            f.detail or ""
                            for f in fit_findings
                            if f.code
                            in (
                                "semantic_fit_passed",
                                "semantic_fit_below_threshold",
                            )
                        ),
                        "",
                    ),
                    provider_name=_provider_name or "unknown",
                    model=_SEMANTIC_FIT_MODEL,
                    specificity=fit_specificity,
                )
        else:
            # Ambiguous fit + no provider = UNRESOLVED.
            # Setting semantic_fit_called=True with fit_score=None causes
            # _roll_up_classification to return UNRESOLVED (beats REQUIRES_REFRESH
            # and below in the priority chain).
            semantic_fit_called = True
            semantic_fit_disposition = "provider_unavailable_unresolved"
            all_findings.append(
                EligibilityFinding(
                    code="semantic_fit_provider_required",
                    severity="warn",
                    message=(
                        "Semantic audience fit requires an AI provider but none was supplied. "
                        "The opportunity topic does not deterministically match the channel niche. "
                        "Classification: UNRESOLVED until a provider is available."
                    ),
                )
            )

    # ── 9. Analytics readiness ──────────────────────────────────────────────
    analytics_ready, analytics_findings = assess_analytics_readiness(conn, opportunity_id, policy)
    all_findings.extend(analytics_findings)

    # ── 10. Phase 12C maturity summary ────────────────────────────────────
    pub_count, p12c_findings = assess_phase12c_maturity(conn, opportunity_id)
    all_findings.extend(p12c_findings)

    # ── 11. Roll-up classification ─────────────────────────────────────────
    classification = _roll_up_classification(
        all_findings,
        market_freshness=freshness_class,
        signal_maturity=signal_maturity,
        signal_confidence=signal_confidence,
        semantic_fit_score=fit_score,
        semantic_fit_called=semantic_fit_called,
        policy=policy,
    )

    return ExperimentEligibilityAssessment(
        opportunity_id=opportunity_id,
        channel_id=channel_id,
        classification=classification,
        findings=all_findings,
        policy_snapshot_json=policy.to_json(),
        assessed_at=assessed_at,
        market_freshness_class=freshness_class,
        market_knowledge_age_hours=age_hours,
        signal_maturity=signal_maturity,
        signal_confidence=signal_confidence,
        semantic_fit_score=fit_score,
        semantic_fit_label=fit_label,
        has_active_conflict=has_conflict,
        active_conflict_experiment_id=conflict_exp_id,
        phase12c_publication_count=pub_count,
        analytics_ready=analytics_ready,
        semantic_fit_disposition=semantic_fit_disposition,
    )


# ---------------------------------------------------------------------------
# Batch assessment (bounded; no ranking; caller decides what to do)
# ---------------------------------------------------------------------------


def assess_opportunity_batch(
    conn: sqlite3.Connection,
    opportunity_ids: list[int],
    channel_id: int,
    ai_provider: Any = None,
    policy: EligibilityPolicy | None = None,
    max_batch_size: int = 50,
) -> Any:  # returns OpportunityEligibilityBatchResult
    """Assess eligibility for multiple opportunities under a single channel.

    Each opportunity is assessed independently using assess_experiment_eligibility.
    The batch is bounded to max_batch_size to prevent runaway LLM spend.
    Results preserve insertion order; no ranking or selection is applied.

    The returned OpportunityEligibilityBatchResult carries:
      - opportunity_id, classification, fit_score, fit_label per item
      - has_exclusion (excluded_topic_match block present)
      - is_unresolved (classification == UNRESOLVED)
      - full ExperimentEligibilityAssessment for each item
    """
    from app.intelligence.experiments.eligibility import (
        OpportunityEligibilityBatchItem,
        OpportunityEligibilityBatchResult,
    )

    assessed_at = _now_utc()
    if policy is None:
        policy = EligibilityPolicy.v1()

    bounded_ids = opportunity_ids[:max_batch_size]
    items: list[OpportunityEligibilityBatchItem] = []
    by_classification: dict[str, int] = {}

    for opp_id in bounded_ids:
        assessment = assess_experiment_eligibility(
            conn,
            opp_id,
            channel_id,
            ai_provider=ai_provider,
            policy=policy,
        )
        cls_val = assessment.classification.value
        by_classification[cls_val] = by_classification.get(cls_val, 0) + 1
        has_exclusion = any(f.code == "excluded_topic_match" for f in assessment.findings)
        is_unresolved = assessment.classification == ExperimentEligibilityClassification.UNRESOLVED
        items.append(
            OpportunityEligibilityBatchItem(
                opportunity_id=opp_id,
                classification=assessment.classification,
                fit_score=assessment.semantic_fit_score,
                fit_label=assessment.semantic_fit_label,
                has_exclusion=has_exclusion,
                is_unresolved=is_unresolved,
                assessment=assessment,
            )
        )

    return OpportunityEligibilityBatchResult(
        channel_id=channel_id,
        assessed_at=assessed_at,
        total=len(items),
        by_classification=by_classification,
        items=items,
    )


# ---------------------------------------------------------------------------
# Batch semantic-fit resolution (Phase 17G — the Phase 18 entry point)
# ---------------------------------------------------------------------------


def resolve_unresolved_opportunities_for_channel(
    conn: sqlite3.Connection,
    channel_id: int,
    ai_provider: Any = None,
    policy: EligibilityPolicy | None = None,
    max_opportunities_considered: int = 50,
    max_evaluations: int = 10,
) -> Any:  # returns SemanticFitResolutionResult
    """Resolve UNRESOLVED opportunities for a channel via semantic fit.

    This is the one canonical operation Phase 18 should call to turn
    UNRESOLVED opportunities into ELIGIBLE/INELIGIBLE — it is the only place
    in the system that spends real semantic-fit LLM calls in bulk.

    Two-pass, cost-conscious by construction:
      1. Assess every considered opportunity with ai_provider=None. This is
         zero-cost — it reuses any persisted cache hit for free and only
         reports UNRESOLVED for opportunities with no valid cached result.
      2. Of those still UNRESOLVED, re-assess up to max_evaluations of them
         WITH ai_provider — this is the only step that can spend an LLM
         call, and it is hard-bounded regardless of how many opportunities
         are unresolved. assess_experiment_eligibility persists any
         successful result itself, so a later call (this one or the
         planner's) with the same input_hash reuses it for free.

    A failed LLM call is never forced to ELIGIBLE or INELIGIBLE — it simply
    remains UNRESOLVED (assess_experiment_eligibility's own behavior; this
    function does not override it).

    Channel isolation: only opportunities with opportunities.channel_id ==
    channel_id are ever considered.
    """
    from app.intelligence.experiments.eligibility import SemanticFitResolutionResult

    resolved_at = _now_utc()
    if policy is None:
        policy = EligibilityPolicy.v1()

    rows = conn.execute(
        """SELECT id FROM opportunities
           WHERE channel_id = ?
             AND current_lifecycle_state NOT IN ('rejected', 'archived', 'produced')
           ORDER BY id DESC LIMIT ?""",
        (channel_id, max_opportunities_considered),
    ).fetchall()
    considered_ids = [r["id"] for r in rows]

    # Pass 1: free — cache hits resolve here, everything else surfaces as
    # genuinely UNRESOLVED.
    unresolved_ids: list[int] = []
    eligible = 0
    ineligible = 0
    for opp_id in considered_ids:
        a = assess_experiment_eligibility(conn, opp_id, channel_id, ai_provider=None, policy=policy)
        if a.classification == ExperimentEligibilityClassification.UNRESOLVED:
            unresolved_ids.append(opp_id)
        elif a.classification in (
            ExperimentEligibilityClassification.GENERAL_ELIGIBLE,
            ExperimentEligibilityClassification.EXPLORATION_ONLY,
        ):
            eligible += 1
        elif a.classification == ExperimentEligibilityClassification.INELIGIBLE:
            ineligible += 1

    unresolved_found = len(unresolved_ids)

    # Pass 2: bounded — only these opportunities can trigger a real LLM call.
    to_evaluate = unresolved_ids[:max_evaluations] if ai_provider is not None else []
    evaluated = 0
    cache_hits = 0
    still_unresolved = unresolved_found - len(to_evaluate)

    for opp_id in to_evaluate:
        a = assess_experiment_eligibility(
            conn, opp_id, channel_id, ai_provider=ai_provider, policy=policy
        )
        if a.semantic_fit_disposition == "cache_hit":
            cache_hits += 1
        else:
            evaluated += 1
        if a.classification in (
            ExperimentEligibilityClassification.GENERAL_ELIGIBLE,
            ExperimentEligibilityClassification.EXPLORATION_ONLY,
        ):
            eligible += 1
        elif a.classification == ExperimentEligibilityClassification.INELIGIBLE:
            ineligible += 1
        else:
            still_unresolved += 1

    return SemanticFitResolutionResult(
        channel_id=channel_id,
        resolved_at=resolved_at,
        considered=len(considered_ids),
        unresolved_found=unresolved_found,
        evaluated=evaluated,
        cache_hits=cache_hits,
        eligible=eligible,
        ineligible=ineligible,
        still_unresolved=still_unresolved,
        opportunity_ids_evaluated=to_evaluate,
    )
