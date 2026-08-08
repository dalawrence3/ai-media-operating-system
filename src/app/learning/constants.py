"""Phase 11 — Learning & Optimization Engine constants.

Every recommendation is deterministic, explainable, and reproducible.
No ML, no embeddings, no automatic optimization.
All changes are recommended for human review; nothing is applied automatically.
"""

from __future__ import annotations

# ── Engine version constants ──────────────────────────────────────────────────

LEARNING_ENGINE_VERSION = "1.0.0"
LEARNING_SCHEMA_VERSION = "1.0.0"
LEARNING_ALGORITHM_VERSION = "learning-v1"
LEARNING_DB_SCHEMA_VERSION = 17

# ── Recommendation domains ────────────────────────────────────────────────────
# Domain identifies which broad area of the pipeline the recommendation targets.

DOMAIN_TOPICS = "topics"
DOMAIN_RESEARCH = "research"
DOMAIN_SCRIPTS = "scripts"
DOMAIN_NARRATION = "narration"
DOMAIN_CAPTIONS = "captions"
DOMAIN_SCENES = "scenes"
DOMAIN_MEDIA = "media"
DOMAIN_PUBLISHING = "publishing"
DOMAIN_ANALYTICS = "analytics"

RECOMMENDATION_DOMAINS: frozenset[str] = frozenset(
    {
        DOMAIN_TOPICS,
        DOMAIN_RESEARCH,
        DOMAIN_SCRIPTS,
        DOMAIN_NARRATION,
        DOMAIN_CAPTIONS,
        DOMAIN_SCENES,
        DOMAIN_MEDIA,
        DOMAIN_PUBLISHING,
        DOMAIN_ANALYTICS,
    }
)

# ── Recommendation subsystems ─────────────────────────────────────────────────
# Subsystem identifies the specific optimization target within a domain.

# Topics domain
SUBSYSTEM_TOPIC_SELECTION = "topic_selection"

# Research domain
SUBSYSTEM_RESEARCH_DEPTH = "research_depth"
SUBSYSTEM_CLAIM_DENSITY = "claim_density"
SUBSYSTEM_CITATION_QUALITY = "citation_quality"

# Scripts domain
SUBSYSTEM_HOOK_EFFECTIVENESS = "hook_effectiveness"
SUBSYSTEM_SCRIPT_PACING = "script_pacing"
SUBSYSTEM_SEGMENT_LENGTH = "segment_length"

# Narration domain
SUBSYSTEM_NARRATION_PACE = "narration_pace"

# Captions domain
SUBSYSTEM_CAPTION_DENSITY = "caption_density"

# Scenes domain
SUBSYSTEM_SCENE_PACING = "scene_pacing"
SUBSYSTEM_ASSET_SELECTION = "asset_selection"
SUBSYSTEM_TRANSITION_USAGE = "transition_usage"

# Media domain
SUBSYSTEM_RENDER_SETTINGS = "render_settings"
SUBSYSTEM_THUMBNAIL_STRATEGY = "thumbnail_strategy"

# Publishing domain
SUBSYSTEM_UPLOAD_TIMING = "upload_timing"
SUBSYSTEM_PUBLISHING_SCHEDULE = "publishing_schedule"
SUBSYSTEM_TITLE_EFFECTIVENESS = "title_effectiveness"
SUBSYSTEM_DESCRIPTION_QUALITY = "description_quality"
SUBSYSTEM_TAG_QUALITY = "tag_quality"

# Analytics domain
SUBSYSTEM_ENGAGEMENT_TRENDS = "engagement_trends"
SUBSYSTEM_RETENTION_TRENDS = "retention_trends"
SUBSYSTEM_CTR_TRENDS = "ctr_trends"
SUBSYSTEM_WATCH_TIME_TRENDS = "watch_time_trends"

RECOMMENDATION_SUBSYSTEMS: frozenset[str] = frozenset(
    {
        SUBSYSTEM_TOPIC_SELECTION,
        SUBSYSTEM_RESEARCH_DEPTH,
        SUBSYSTEM_CLAIM_DENSITY,
        SUBSYSTEM_CITATION_QUALITY,
        SUBSYSTEM_HOOK_EFFECTIVENESS,
        SUBSYSTEM_SCRIPT_PACING,
        SUBSYSTEM_SEGMENT_LENGTH,
        SUBSYSTEM_NARRATION_PACE,
        SUBSYSTEM_CAPTION_DENSITY,
        SUBSYSTEM_SCENE_PACING,
        SUBSYSTEM_ASSET_SELECTION,
        SUBSYSTEM_TRANSITION_USAGE,
        SUBSYSTEM_RENDER_SETTINGS,
        SUBSYSTEM_THUMBNAIL_STRATEGY,
        SUBSYSTEM_UPLOAD_TIMING,
        SUBSYSTEM_PUBLISHING_SCHEDULE,
        SUBSYSTEM_TITLE_EFFECTIVENESS,
        SUBSYSTEM_DESCRIPTION_QUALITY,
        SUBSYSTEM_TAG_QUALITY,
        SUBSYSTEM_ENGAGEMENT_TRENDS,
        SUBSYSTEM_RETENTION_TRENDS,
        SUBSYSTEM_CTR_TRENDS,
        SUBSYSTEM_WATCH_TIME_TRENDS,
    }
)

# ── Domain → subsystem membership ────────────────────────────────────────────

DOMAIN_SUBSYSTEMS: dict[str, frozenset[str]] = {
    DOMAIN_TOPICS: frozenset({SUBSYSTEM_TOPIC_SELECTION}),
    DOMAIN_RESEARCH: frozenset(
        {
            SUBSYSTEM_RESEARCH_DEPTH,
            SUBSYSTEM_CLAIM_DENSITY,
            SUBSYSTEM_CITATION_QUALITY,
        }
    ),
    DOMAIN_SCRIPTS: frozenset(
        {
            SUBSYSTEM_HOOK_EFFECTIVENESS,
            SUBSYSTEM_SCRIPT_PACING,
            SUBSYSTEM_SEGMENT_LENGTH,
        }
    ),
    DOMAIN_NARRATION: frozenset({SUBSYSTEM_NARRATION_PACE}),
    DOMAIN_CAPTIONS: frozenset({SUBSYSTEM_CAPTION_DENSITY}),
    DOMAIN_SCENES: frozenset(
        {
            SUBSYSTEM_SCENE_PACING,
            SUBSYSTEM_ASSET_SELECTION,
            SUBSYSTEM_TRANSITION_USAGE,
        }
    ),
    DOMAIN_MEDIA: frozenset(
        {
            SUBSYSTEM_RENDER_SETTINGS,
            SUBSYSTEM_THUMBNAIL_STRATEGY,
        }
    ),
    DOMAIN_PUBLISHING: frozenset(
        {
            SUBSYSTEM_UPLOAD_TIMING,
            SUBSYSTEM_PUBLISHING_SCHEDULE,
            SUBSYSTEM_TITLE_EFFECTIVENESS,
            SUBSYSTEM_DESCRIPTION_QUALITY,
            SUBSYSTEM_TAG_QUALITY,
        }
    ),
    DOMAIN_ANALYTICS: frozenset(
        {
            SUBSYSTEM_ENGAGEMENT_TRENDS,
            SUBSYSTEM_RETENTION_TRENDS,
            SUBSYSTEM_CTR_TRENDS,
            SUBSYSTEM_WATCH_TIME_TRENDS,
        }
    ),
}

# ── Confidence levels ─────────────────────────────────────────────────────────

CONFIDENCE_LOW = "low"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_HIGH = "high"

CONFIDENCE_LEVELS: frozenset[str] = frozenset(
    {
        CONFIDENCE_LOW,
        CONFIDENCE_MEDIUM,
        CONFIDENCE_HIGH,
    }
)

# Numeric score thresholds → confidence label
CONFIDENCE_LOW_THRESHOLD = 0.0  # [0.0, 0.4)
CONFIDENCE_MEDIUM_THRESHOLD = 0.4  # [0.4, 0.7)
CONFIDENCE_HIGH_THRESHOLD = 0.7  # [0.7, 1.0]


def confidence_label(score: float) -> str:
    """Map a 0.0–1.0 score to a confidence level label."""
    if score >= CONFIDENCE_HIGH_THRESHOLD:
        return CONFIDENCE_HIGH
    if score >= CONFIDENCE_MEDIUM_THRESHOLD:
        return CONFIDENCE_MEDIUM
    return CONFIDENCE_LOW


# ── Recommendation strength ───────────────────────────────────────────────────
# Distinguishes recommendations that have sufficient evidence (actionable) from
# those produced from a single weak data point (exploratory hypothesis).
# Phase 12 must not automatically act on exploratory recommendations.

STRENGTH_EXPLORATORY = "exploratory"  # insufficient evidence; hypothesis only
STRENGTH_ACTIONABLE = "actionable"  # meets minimum evidence and confidence

RECOMMENDATION_STRENGTHS: frozenset[str] = frozenset(
    {
        STRENGTH_EXPLORATORY,
        STRENGTH_ACTIONABLE,
    }
)

# ── Generator identifiers ─────────────────────────────────────────────────────

GENERATOR_CTR = "ctr"
GENERATOR_RETENTION = "retention"
GENERATOR_ENGAGEMENT = "engagement"
GENERATOR_WATCH_TIME = "watch_time"
GENERATOR_SUBSCRIBERS = "subscribers"
GENERATOR_SHARES = "shares"

KNOWN_GENERATORS: frozenset[str] = frozenset(
    {
        GENERATOR_CTR,
        GENERATOR_RETENTION,
        GENERATOR_ENGAGEMENT,
        GENERATOR_WATCH_TIME,
        GENERATOR_SUBSCRIBERS,
        GENERATOR_SHARES,
    }
)

GENERATOR_STATUS_SUCCEEDED = "succeeded"
GENERATOR_STATUS_FAILED = "failed"

# ── Evidence classification ───────────────────────────────────────────────────
# Every recommendation must declare how its evidence was produced.
# Phase 12+ uses this to decide whether the recommendation can be applied
# directly or must first be validated by a controlled experiment.

EVIDENCE_OBSERVATIONAL = "observational"  # derived from platform analytics
EVIDENCE_CONTROLLED_EXP = "controlled_experiment"  # from a Phase 12 A/B experiment
EVIDENCE_HUMAN_PREFERENCE = "human_preference"  # from explicit human review signal
EVIDENCE_OPERATIONAL_FAILURE = "operational_failure"  # from a production error / rejection
EVIDENCE_MIXED = "mixed"  # multiple evidence types combined

EVIDENCE_CLASSIFICATIONS: frozenset[str] = frozenset(
    {
        EVIDENCE_OBSERVATIONAL,
        EVIDENCE_CONTROLLED_EXP,
        EVIDENCE_HUMAN_PREFERENCE,
        EVIDENCE_OPERATIONAL_FAILURE,
        EVIDENCE_MIXED,
    }
)

# ── Recommendation review statuses ───────────────────────────────────────────

STATUS_PENDING = "pending"
STATUS_ACCEPTED = "accepted"
STATUS_REJECTED = "rejected"
STATUS_SUPERSEDED = "superseded"

RECOMMENDATION_STATUSES: frozenset[str] = frozenset(
    {
        STATUS_PENDING,
        STATUS_ACCEPTED,
        STATUS_REJECTED,
        STATUS_SUPERSEDED,
    }
)

# Statuses that accept review events
REVIEW_ELIGIBLE_STATUSES: frozenset[str] = frozenset(
    {
        STATUS_PENDING,
        STATUS_ACCEPTED,
        STATUS_REJECTED,
    }
)

# ── Review event types ────────────────────────────────────────────────────────

EVENT_ACCEPTED = "accepted"
EVENT_REJECTED = "rejected"
EVENT_NOTED = "noted"

REVIEW_EVENT_TYPES: frozenset[str] = frozenset(
    {
        EVENT_ACCEPTED,
        EVENT_REJECTED,
        EVENT_NOTED,
    }
)

# ── Learning run statuses ─────────────────────────────────────────────────────

RUN_STATUS_RUNNING = "running"
RUN_STATUS_COMPLETED = "completed"
RUN_STATUS_PARTIAL = "partial"  # some generators succeeded, some failed
RUN_STATUS_FAILED = "failed"

LEARNING_RUN_STATUSES: frozenset[str] = frozenset(
    {
        RUN_STATUS_RUNNING,
        RUN_STATUS_COMPLETED,
        RUN_STATUS_PARTIAL,
        RUN_STATUS_FAILED,
    }
)

# ── Analytics metric thresholds used by generators ───────────────────────────
# These are the observable signal boundaries the generators use to decide
# whether to emit a recommendation.  They are deterministic constants, not
# learned parameters, and are versioned with LEARNING_ALGORITHM_VERSION.

# CTR (fraction, 0–1)
CTR_LOW_THRESHOLD = 0.02  # < 2% → title / hook improvement candidates
CTR_HIGH_THRESHOLD = 0.05  # ≥ 5% → what's working signal

# Retention (seconds): average_view_duration for a ≤60s Short
RETENTION_LOW_THRESHOLD_S = 20.0  # < 20 s → pacing / length concerns
RETENTION_HIGH_THRESHOLD_S = 45.0  # ≥ 45 s → strong retention signal

# Engagement rate (likes / views)
ENGAGEMENT_LOW_THRESHOLD = 0.01  # < 1% → content engagement concern

# Net subscriber growth per video
# Any net loss triggers the concern recommendation.
SUBSCRIBER_LOSS_THRESHOLD = 0  # net <= 0 → content quality concern

# Watch-time: watch_time_seconds vs. views × retention benchmark
WATCH_TIME_LOW_FACTOR = 0.5  # watch_time < 50% of views × RETENTION_HIGH → distribution concern

# Minimum data points required for medium/high confidence
MIN_SNAPSHOTS_MEDIUM_CONFIDENCE = 2
MIN_SNAPSHOTS_HIGH_CONFIDENCE = 5

# Minimum evidence required for ACTIONABLE recommendation strength
MIN_UNIQUE_SNAPSHOTS_ACTIONABLE = MIN_SNAPSHOTS_MEDIUM_CONFIDENCE  # 2
MIN_CONFIDENCE_ACTIONABLE = CONFIDENCE_MEDIUM_THRESHOLD  # 0.4

# ── Subsystem entity type labels ──────────────────────────────────────────────

ENTITY_TOPIC = "topic"
ENTITY_SCRIPT = "script"
ENTITY_PRODUCTION_PLAN = "production_plan"
ENTITY_NARRATION_RUN = "narration_run"
ENTITY_CAPTION_RUN = "caption_run"
ENTITY_SCENE_MANIFEST = "scene_manifest"
ENTITY_RENDER_MANIFEST = "render_manifest"
ENTITY_PUBLISHING_PLAN = "publishing_plan"
ENTITY_PUBLICATION = "publication"

SUBSYSTEM_ENTITY_TYPES: frozenset[str] = frozenset(
    {
        ENTITY_TOPIC,
        ENTITY_SCRIPT,
        ENTITY_PRODUCTION_PLAN,
        ENTITY_NARRATION_RUN,
        ENTITY_CAPTION_RUN,
        ENTITY_SCENE_MANIFEST,
        ENTITY_RENDER_MANIFEST,
        ENTITY_PUBLISHING_PLAN,
        ENTITY_PUBLICATION,
    }
)
