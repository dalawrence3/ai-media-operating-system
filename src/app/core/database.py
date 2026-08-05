"""SQLite connection management and schema initialization."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.core.logging import get_logger

logger = get_logger(__name__)

# Increment when the schema changes; add a migration branch in _migrate().
SCHEMA_VERSION = 11

# Phase 1 DDL — topics, sources, scripts, runs.
_DDL_V1 = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS topics (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT    NOT NULL,
    angle      TEXT    NOT NULL DEFAULT '',
    status     TEXT    NOT NULL DEFAULT 'active'
                       CHECK (status IN ('active', 'archived')),
    created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    updated_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);

CREATE TABLE IF NOT EXISTS sources (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id   INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    kind       TEXT    NOT NULL CHECK (kind IN ('url', 'file', 'note')),
    reference  TEXT    NOT NULL,
    notes      TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);

CREATE TABLE IF NOT EXISTS scripts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id   INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    version    INTEGER NOT NULL DEFAULT 1,
    body       TEXT    NOT NULL,
    status     TEXT    NOT NULL DEFAULT 'draft'
                       CHECK (status IN ('draft', 'approved', 'rejected')),
    created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    updated_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    UNIQUE (topic_id, version)
);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id    INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    script_id   INTEGER REFERENCES scripts(id) ON DELETE SET NULL,
    status      TEXT    NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    started_at  TEXT,
    finished_at TEXT,
    error       TEXT,
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);
"""

# Phase 2 DDL — ai_calls.
_DDL_V2 = """
CREATE TABLE IF NOT EXISTS ai_calls (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    provider            TEXT    NOT NULL,
    model               TEXT    NOT NULL,
    prompt_name         TEXT    NOT NULL DEFAULT '',
    prompt_version      TEXT    NOT NULL DEFAULT '',
    input_tokens        INTEGER,
    output_tokens       INTEGER,
    estimated_cost_usd  REAL,
    duration_ms         INTEGER,
    retry_count         INTEGER NOT NULL DEFAULT 0,
    status              TEXT    NOT NULL CHECK (status IN ('success', 'failed')),
    error_category      TEXT,
    error_message       TEXT,
    run_id              INTEGER REFERENCES runs(id) ON DELETE SET NULL,
    created_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    completed_at        TEXT
);
"""


# Phase 3 DDL — channel strategy foundation.
# Creation order matters for FK satisfaction at INSERT time:
#   channels → channel_monetization_strategies → channel_profile_versions
#   → channel_capacity_policies → channel_operating_mode_events
# channels.current_profile_version_id and current_strategy_id start NULL
# and are set after the dependent rows are created (see repository.create_channel_full).
_DDL_V3 = """
CREATE TABLE IF NOT EXISTS channels (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    platform                    TEXT    NOT NULL DEFAULT 'youtube'
                                        CHECK (platform IN ('youtube', 'instagram', 'tiktok')),
    channel_name                TEXT    NOT NULL,
    platform_channel_id         TEXT,
    operating_mode              TEXT    NOT NULL DEFAULT 'manual'
                                        CHECK (operating_mode IN (
                                            'manual', 'supervised', 'autonomous')),
    current_profile_version_id  INTEGER,
    current_strategy_id         INTEGER,
    current_maturity_stage      TEXT    NOT NULL DEFAULT 'validation'
                                        CHECK (current_maturity_stage IN (
                                            'validation', 'growth', 'monetization',
                                            'optimization', 'scaling')),
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    updated_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);

CREATE TABLE IF NOT EXISTS channel_monetization_strategies (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id              INTEGER NOT NULL REFERENCES channels(id),
    version                 INTEGER NOT NULL,
    monetization_status     TEXT    NOT NULL DEFAULT 'pre'
                                    CHECK (monetization_status IN ('pre', 'active')),
    objective_weights_json  TEXT    NOT NULL,
    description             TEXT    NOT NULL DEFAULT '',
    active_from             TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    superseded_at           TEXT,
    created_by              TEXT    NOT NULL DEFAULT '',
    status                  TEXT    NOT NULL DEFAULT 'draft'
                                    CHECK (status IN ('draft', 'active', 'superseded')),
    activated_at            TEXT,
    activated_by            TEXT    NOT NULL DEFAULT '',
    activation_reason       TEXT    NOT NULL DEFAULT '',
    UNIQUE (channel_id, version)
);

CREATE TABLE IF NOT EXISTS channel_profile_versions (
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id                      INTEGER NOT NULL REFERENCES channels(id),
    version                         INTEGER NOT NULL,
    strategy_id                     INTEGER REFERENCES channel_monetization_strategies(id),
    maturity_stage                  TEXT    NOT NULL DEFAULT 'validation'
                                            CHECK (maturity_stage IN (
                                                'validation', 'growth', 'monetization',
                                                'optimization', 'scaling')),
    primary_niche                   TEXT    NOT NULL,
    secondary_niches_json           TEXT    NOT NULL DEFAULT '[]',
    excluded_topics_json            TEXT    NOT NULL DEFAULT '[]',
    audience_description            TEXT    NOT NULL DEFAULT '',
    audience_demographics           TEXT    NOT NULL DEFAULT '',
    audience_intent                 TEXT    NOT NULL DEFAULT 'educational'
                                            CHECK (audience_intent IN (
                                                'educational', 'entertainment', 'mixed')),
    brand_voice                     TEXT    NOT NULL DEFAULT 'conversational'
                                            CHECK (brand_voice IN (
                                                'authoritative', 'conversational', 'energetic',
                                                'calm', 'humorous')),
    tone_notes                      TEXT    NOT NULL DEFAULT '',
    brand_rules_json                TEXT    NOT NULL DEFAULT '[]',
    content_style                   TEXT    NOT NULL DEFAULT 'explainer'
                                            CHECK (content_style IN (
                                                'story-driven', 'list-based',
                                                'explainer', 'mixed')),
    primary_format                  TEXT    NOT NULL DEFAULT 'short'
                                            CHECK (primary_format IN (
                                                'short', 'long_form', 'both', 'content_package')),
    posting_cadence_per_week        INTEGER NOT NULL DEFAULT 3,
    portfolio_targets_json          TEXT    NOT NULL
        DEFAULT '{"evergreen":0.6,"trending":0.2,"seasonal":0.1,"experimental":0.1}',
    allowed_discovery_adapters_json TEXT    NOT NULL DEFAULT '["manual", "youtube_data_api"]',
    max_candidates_per_run          INTEGER NOT NULL DEFAULT 20,
    min_opportunity_score           REAL    NOT NULL DEFAULT 0.40,
    duplicate_similarity_threshold  REAL    NOT NULL DEFAULT 0.70,
    signal_staleness_days           INTEGER NOT NULL DEFAULT 7,
    scoring_policy_version          TEXT    NOT NULL DEFAULT '1.0.0',
    active_from                     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    superseded_at                   TEXT,
    created_by                      TEXT    NOT NULL DEFAULT '',
    status                          TEXT    NOT NULL DEFAULT 'draft'
                                            CHECK (status IN ('draft', 'active', 'superseded')),
    activated_at                    TEXT,
    activated_by                    TEXT    NOT NULL DEFAULT '',
    activation_reason               TEXT    NOT NULL DEFAULT '',
    UNIQUE (channel_id, version)
);

CREATE TABLE IF NOT EXISTS channel_capacity_policies (
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id                      INTEGER NOT NULL UNIQUE REFERENCES channels(id),
    long_form_slots_per_week        INTEGER NOT NULL DEFAULT 2,
    short_slots_per_week            INTEGER NOT NULL DEFAULT 4,
    content_package_slots_per_week  INTEGER NOT NULL DEFAULT 1,
    max_concurrent_productions      INTEGER NOT NULL DEFAULT 2,
    daily_budget_usd                REAL    NOT NULL DEFAULT 10.0,
    per_video_budget_usd            REAL    NOT NULL DEFAULT 5.0,
    monthly_budget_usd              REAL    NOT NULL DEFAULT 200.0,
    review_hours_per_week           REAL    NOT NULL DEFAULT 3.0,
    review_hours_per_short          REAL    NOT NULL DEFAULT 0.5,
    review_hours_per_long_form      REAL    NOT NULL DEFAULT 1.5,
    review_hours_per_package        REAL    NOT NULL DEFAULT 2.5,
    trend_reservation_slots         INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    updated_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);

CREATE TABLE IF NOT EXISTS channel_operating_mode_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id  INTEGER NOT NULL REFERENCES channels(id),
    from_mode   TEXT    CHECK (from_mode IN ('manual', 'supervised', 'autonomous')),
    to_mode     TEXT    NOT NULL CHECK (to_mode IN ('manual', 'supervised', 'autonomous')),
    operator    TEXT    NOT NULL DEFAULT '',
    reason      TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);
"""


# Phase 4 DDL — opportunity discovery foundation.
# Creation order: discovery_runs → opportunities → opportunity_observations
#   → opportunity_source_evidence → opportunity_state_events
_DDL_V4_NEW_TABLES = """
CREATE TABLE IF NOT EXISTS discovery_runs (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id              INTEGER NOT NULL REFERENCES channels(id),
    profile_version_id      INTEGER NOT NULL REFERENCES channel_profile_versions(id),
    adapter_name            TEXT    NOT NULL
                                    CHECK (adapter_name IN ('manual', 'youtube_data_api')),
    query_parameters_json   TEXT    NOT NULL DEFAULT '{}',
    status                  TEXT    NOT NULL DEFAULT 'pending'
                                    CHECK (status IN (
                                        'pending', 'running', 'completed', 'partial', 'failed')),
    candidate_count         INTEGER NOT NULL DEFAULT 0,
    new_opportunity_count   INTEGER NOT NULL DEFAULT 0,
    dedup_count             INTEGER NOT NULL DEFAULT 0,
    failed_count            INTEGER NOT NULL DEFAULT 0,
    quota_units_consumed    INTEGER NOT NULL DEFAULT 0,
    error_message           TEXT,
    started_at              TEXT    NOT NULL,
    completed_at            TEXT
);

CREATE TABLE IF NOT EXISTS opportunities (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id              INTEGER NOT NULL REFERENCES channels(id),
    discovery_run_id        INTEGER NOT NULL REFERENCES discovery_runs(id),
    normalized_topic        TEXT    NOT NULL,
    raw_topic               TEXT    NOT NULL,
    title                   TEXT    NOT NULL DEFAULT '',
    topic_summary           TEXT    NOT NULL DEFAULT '',
    format_recommendation   TEXT    NOT NULL DEFAULT 'undecided'
                                    CHECK (format_recommendation IN (
                                        'short', 'long_form', 'both',
                                        'content_package', 'undecided')),
    strategic_role          TEXT    NOT NULL DEFAULT 'discovery'
                                    CHECK (strategic_role IN (
                                        'discovery', 'monetization', 'subscriber_growth',
                                        'authority', 'retention', 'experimentation')),
    current_lifecycle_state TEXT    NOT NULL DEFAULT 'new'
                                    CHECK (current_lifecycle_state IN (
                                        'new', 'under_review', 'approved',
                                        'rejected', 'produced', 'archived')),
    created_at              TEXT    NOT NULL,
    updated_at              TEXT    NOT NULL,
    UNIQUE (channel_id, normalized_topic)
);

CREATE TABLE IF NOT EXISTS opportunity_observations (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id          INTEGER NOT NULL REFERENCES opportunities(id),
    discovery_run_id        INTEGER NOT NULL REFERENCES discovery_runs(id),
    adapter_name            TEXT    NOT NULL,
    collected_at            TEXT    NOT NULL,
    signal_age_days         REAL,
    source_quality_tier     TEXT    NOT NULL DEFAULT 'medium'
                                    CHECK (source_quality_tier IN (
                                        'high', 'medium_high', 'medium', 'variable')),
    raw_payload_json        TEXT    NOT NULL DEFAULT '{}',
    collection_notes        TEXT    NOT NULL DEFAULT '',
    was_deduplicated        INTEGER NOT NULL DEFAULT 0 CHECK (was_deduplicated IN (0, 1)),
    candidate_topic         TEXT,
    dedup_similarity_score  REAL
);

CREATE TABLE IF NOT EXISTS opportunity_source_evidence (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    observation_id  INTEGER NOT NULL REFERENCES opportunity_observations(id),
    opportunity_id  INTEGER NOT NULL REFERENCES opportunities(id),
    evidence_type   TEXT    NOT NULL,
    evidence_value  REAL,
    evidence_text   TEXT,
    evidence_unit   TEXT    NOT NULL DEFAULT '',
    source_label    TEXT    NOT NULL,
    collected_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS opportunity_state_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id  INTEGER NOT NULL REFERENCES opportunities(id),
    from_state      TEXT,
    to_state        TEXT    NOT NULL,
    actor           TEXT    NOT NULL DEFAULT 'system',
    reason          TEXT    NOT NULL DEFAULT '',
    created_at      TEXT    NOT NULL
);
"""


# Phase 5 DDL — versioned scoring policies and opportunity scores.
# Creation order: scoring_policies → opportunity_scores
_DDL_V5_SCORING = """
CREATE TABLE IF NOT EXISTS scoring_policies (
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id                      INTEGER NOT NULL REFERENCES channels(id),
    version                         INTEGER NOT NULL,
    label                           TEXT    NOT NULL,
    description                     TEXT,
    weight_trend_strength           REAL    NOT NULL DEFAULT 0.05,
    weight_audience_demand          REAL    NOT NULL DEFAULT 0.20,
    weight_competition              REAL    NOT NULL DEFAULT 0.15,
    weight_evergreen_value          REAL    NOT NULL DEFAULT 0.20,
    weight_audience_fit             REAL    NOT NULL DEFAULT 0.30,
    weight_content_novelty          REAL    NOT NULL DEFAULT 0.10,
    missing_trend_strength          TEXT    NOT NULL DEFAULT 'reweight_available',
    missing_audience_demand         TEXT    NOT NULL DEFAULT 'reweight_available',
    missing_competition             TEXT    NOT NULL DEFAULT 'reweight_available',
    missing_evergreen_value         TEXT    NOT NULL DEFAULT 'reweight_available',
    missing_audience_fit            TEXT    NOT NULL DEFAULT 'require_research',
    missing_content_novelty         TEXT    NOT NULL DEFAULT 'reweight_available',
    min_confidence_threshold        REAL    NOT NULL DEFAULT 0.40,
    freshness_decay_days            REAL    NOT NULL DEFAULT 7.0,
    max_corroboration_bonus         REAL    NOT NULL DEFAULT 0.10,
    corroboration_bonus_per_source  REAL    NOT NULL DEFAULT 0.05,
    status                          TEXT    NOT NULL DEFAULT 'draft',
    is_default                      INTEGER NOT NULL DEFAULT 0,
    activated_at                    TEXT,
    archived_at                     TEXT,
    created_at                      TEXT    NOT NULL,
    created_by                      TEXT,
    UNIQUE(channel_id, version)
);

CREATE UNIQUE INDEX IF NOT EXISTS scoring_policies_one_default_per_channel
    ON scoring_policies(channel_id) WHERE is_default = 1;

CREATE TABLE IF NOT EXISTS opportunity_scores (
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id                  INTEGER NOT NULL REFERENCES opportunities(id),
    scoring_policy_id               INTEGER NOT NULL REFERENCES scoring_policies(id),
    channel_profile_version_id      INTEGER NOT NULL REFERENCES channel_profile_versions(id),
    composite_score                 REAL    NOT NULL,
    confidence                      REAL    NOT NULL,
    score_trend_strength            REAL,
    score_audience_demand           REAL,
    score_competition               REAL,
    score_evergreen_value           REAL,
    score_audience_fit              REAL,
    score_content_novelty           REAL,
    status_trend_strength           TEXT    NOT NULL DEFAULT 'absent',
    status_audience_demand          TEXT    NOT NULL DEFAULT 'absent',
    status_competition              TEXT    NOT NULL DEFAULT 'absent',
    status_evergreen_value          TEXT    NOT NULL DEFAULT 'absent',
    status_audience_fit             TEXT    NOT NULL DEFAULT 'absent',
    status_content_novelty          TEXT    NOT NULL DEFAULT 'absent',
    eff_weight_trend_strength       REAL    NOT NULL,
    eff_weight_audience_demand      REAL    NOT NULL,
    eff_weight_competition          REAL    NOT NULL,
    eff_weight_evergreen_value      REAL    NOT NULL,
    eff_weight_audience_fit         REAL    NOT NULL,
    eff_weight_content_novelty      REAL    NOT NULL,
    observation_ids_json            TEXT    NOT NULL DEFAULT '[]',
    input_snapshot_json             TEXT    NOT NULL DEFAULT '{}',
    input_hash                      TEXT    NOT NULL,
    below_confidence_threshold      INTEGER NOT NULL DEFAULT 0,
    requires_research               INTEGER NOT NULL DEFAULT 0,
    scored_at                       TEXT    NOT NULL,
    scorer_version                  TEXT    NOT NULL DEFAULT '1.0'
);

CREATE INDEX IF NOT EXISTS opportunity_scores_opp_policy
    ON opportunity_scores(opportunity_id, scoring_policy_id, scored_at DESC, id DESC);
"""


# Phase 6 DDL — promote opportunities to topics.
# ALTER TABLE cannot add a UNIQUE column reliably in all SQLite versions,
# so uniqueness is enforced via a partial index (NULL values excluded).
_DDL_V6_PROMOTE = """
ALTER TABLE topics ADD COLUMN promoted_opportunity_id INTEGER REFERENCES opportunities(id);

CREATE UNIQUE INDEX uq_topics_promoted_opportunity
    ON topics(promoted_opportunity_id)
    WHERE promoted_opportunity_id IS NOT NULL;
"""


# Phase 7 DDL — source_contents for acquired and extracted content.
_DDL_V7_RESEARCH = """
CREATE TABLE IF NOT EXISTS source_contents (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id               INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    fetch_status            TEXT    NOT NULL CHECK (fetch_status IN ('ok', 'failed')),
    extraction_status       TEXT    NOT NULL
                                CHECK (extraction_status IN ('ok', 'partial', 'failed')),
    http_status             INTEGER,
    canonical_url           TEXT,
    mime_type               TEXT,
    fetched_at              TEXT    NOT NULL,
    raw_text                TEXT,
    retrieval_hash          TEXT,
    normalized_text_hash    TEXT,
    hash_algorithm          TEXT    NOT NULL DEFAULT 'sha256-nfc-v1',
    word_count              INTEGER,
    title                   TEXT,
    author                  TEXT,
    published_at            TEXT,
    domain_type             TEXT    CHECK (domain_type IN
                                ('academic', 'news', 'government', 'blog', 'forum', 'unknown')),
    extraction_method       TEXT    CHECK (extraction_method IN
                                ('html_parser', 'pdf', 'plaintext', 'markdown')),
    extraction_error        TEXT,
    suspected_truncation    INTEGER NOT NULL DEFAULT 0 CHECK (suspected_truncation IN (0, 1)),
    quality_score           REAL,
    quality_factors_json    TEXT,
    quality_scorer_version  TEXT,
    created_at              TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);

CREATE INDEX IF NOT EXISTS sc_source_id
    ON source_contents(source_id, fetched_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS sc_normalized_text_hash
    ON source_contents(normalized_text_hash)
    WHERE normalized_text_hash IS NOT NULL;
"""


_DDL_V8_CLAIMS = """
CREATE TABLE IF NOT EXISTS claim_extraction_runs (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    source_content_id       INTEGER NOT NULL
                                REFERENCES source_contents(id) ON DELETE CASCADE,
    status                  TEXT    NOT NULL
                                CHECK (status IN
                                    ('running', 'completed', 'partial', 'failed')),
    input_hash              TEXT    NOT NULL,
    total_chunk_count       INTEGER NOT NULL,
    completed_chunk_count   INTEGER NOT NULL DEFAULT 0,
    failed_chunk_count      INTEGER NOT NULL DEFAULT 0,
    accepted_claim_count    INTEGER,
    was_truncated           INTEGER NOT NULL DEFAULT 0,
    prompt_name             TEXT    NOT NULL DEFAULT '',
    prompt_version          TEXT    NOT NULL DEFAULT '',
    model                   TEXT    NOT NULL,
    provider                TEXT    NOT NULL,
    extraction_algo_version TEXT    NOT NULL,
    error_message           TEXT,
    superseded_at           TEXT,
    superseded_by_run_id    INTEGER REFERENCES claim_extraction_runs(id),
    started_at              TEXT    NOT NULL,
    completed_at            TEXT,
    created_at              TEXT    NOT NULL
                                DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);

CREATE TABLE IF NOT EXISTS claim_extraction_run_calls (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_extraction_run_id INTEGER NOT NULL
                                REFERENCES claim_extraction_runs(id) ON DELETE CASCADE,
    ai_call_id              INTEGER
                                REFERENCES ai_calls(id) ON DELETE SET NULL,
    chunk_index             INTEGER NOT NULL,
    chunk_hash              TEXT    NOT NULL,
    input_char_start        INTEGER NOT NULL,
    input_char_end          INTEGER NOT NULL,
    status                  TEXT    NOT NULL
                                CHECK (status IN ('running', 'completed', 'failed')),
    retry_count             INTEGER NOT NULL DEFAULT 0,
    accepted_claim_count    INTEGER,
    error_message           TEXT,
    started_at              TEXT    NOT NULL,
    completed_at            TEXT,
    created_at              TEXT    NOT NULL
                                DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    UNIQUE (claim_extraction_run_id, chunk_index)
);

CREATE TABLE IF NOT EXISTS claims (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    extraction_run_id       INTEGER NOT NULL
                                REFERENCES claim_extraction_runs(id) ON DELETE CASCADE,
    chunk_index             INTEGER NOT NULL,
    claim_text              TEXT    NOT NULL,
    claim_type              TEXT    NOT NULL
                                CHECK (claim_type IN
                                    ('factual', 'statistical', 'attribution', 'definition')),
    supporting_quote        TEXT,
    quote_support_status    TEXT    NOT NULL
                                CHECK (quote_support_status IN
                                    ('exact', 'normalized', 'unsupported', 'no_quote')),
    quote_start             INTEGER,
    quote_end               INTEGER,
    page_number             INTEGER,
    requires_date_review    INTEGER NOT NULL DEFAULT 0,
    created_at              TEXT    NOT NULL
                                DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_cer_source_content
    ON claim_extraction_runs(source_content_id);

CREATE INDEX IF NOT EXISTS idx_cer_input_hash
    ON claim_extraction_runs(input_hash);

CREATE INDEX IF NOT EXISTS idx_cerc_run
    ON claim_extraction_run_calls(claim_extraction_run_id);

CREATE INDEX IF NOT EXISTS idx_cerc_ai_call
    ON claim_extraction_run_calls(ai_call_id);

CREATE INDEX IF NOT EXISTS idx_claims_run
    ON claims(extraction_run_id);
"""


# Phase 9 DDL — script generation runs and citations; new Script columns.
# Migration order: ALTER scripts columns first (scripts table already exists from v1),
# then script_generation_runs (references scripts(id)), then script_citations.
# No partial unique index on scripts(topic_id) WHERE status='approved' — historical
# v8 data may contain multiple approved scripts per topic.
_DDL_V9_SCRIPTS = """
ALTER TABLE scripts ADD COLUMN body_json TEXT;
ALTER TABLE scripts ADD COLUMN format TEXT NOT NULL DEFAULT 'short'
    CHECK(format IN ('short', 'long_form'));
ALTER TABLE scripts ADD COLUMN approved_at TEXT;
ALTER TABLE scripts ADD COLUMN superseded_at TEXT;

CREATE TABLE IF NOT EXISTS script_generation_runs (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id                    INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    script_id                   INTEGER REFERENCES scripts(id) ON DELETE SET NULL,
    status                      TEXT    NOT NULL
                                    CHECK (status IN ('running', 'completed', 'failed')),
    input_hash                  TEXT    NOT NULL,
    evidence_hash               TEXT    NOT NULL,
    prompt_hash                 TEXT    NOT NULL,
    prompt_name                 TEXT    NOT NULL,
    prompt_version              TEXT    NOT NULL,
    model                       TEXT    NOT NULL,
    temperature                 REAL    NOT NULL,
    max_tokens                  INTEGER NOT NULL,
    tone                        TEXT    NOT NULL DEFAULT '',
    audience                    TEXT    NOT NULL DEFAULT '',
    target_duration_s           INTEGER NOT NULL,
    computed_word_count         INTEGER,
    computed_duration_s         INTEGER,
    warnings_json               TEXT,
    requires_evidence_review    INTEGER NOT NULL DEFAULT 0,
    ai_call_id                  INTEGER REFERENCES ai_calls(id) ON DELETE SET NULL,
    error_message               TEXT,
    superseded_at               TEXT,
    superseded_by_run_id        INTEGER REFERENCES script_generation_runs(id),
    started_at                  TEXT    NOT NULL,
    completed_at                TEXT,
    created_at                  TEXT    NOT NULL
                                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);

CREATE TABLE IF NOT EXISTS script_citations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    script_id       INTEGER NOT NULL REFERENCES scripts(id) ON DELETE CASCADE,
    claim_id        INTEGER NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    section_index   INTEGER NOT NULL,
    citation_order  INTEGER NOT NULL,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_sgr_topic
    ON script_generation_runs(topic_id, created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_sgr_input_hash
    ON script_generation_runs(input_hash);

CREATE INDEX IF NOT EXISTS idx_sgr_script
    ON script_generation_runs(script_id);

CREATE INDEX IF NOT EXISTS idx_sc_script
    ON script_citations(script_id, section_index, citation_order);
"""


# Phase 10 DDL — production plans, segments, citations, and review events.
# Creation order: production_plans → production_segments → production_segment_citations
#   → production_plan_review_events
# production_plans.script_id uses ON DELETE RESTRICT: a Script with plans cannot be deleted.
# production_plan_review_events.topic_id/script_id use ON DELETE RESTRICT: review events
#   are training labels and must not be destroyed by parent-row deletion.
_DDL_V10_PRODUCTION = """
CREATE TABLE IF NOT EXISTS production_plans (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id                    INTEGER NOT NULL
                                    REFERENCES topics(id) ON DELETE CASCADE,
    script_id                   INTEGER NOT NULL
                                    REFERENCES scripts(id) ON DELETE RESTRICT,
    script_version              INTEGER NOT NULL,
    input_hash                  TEXT    NOT NULL,
    script_body_hash            TEXT    NOT NULL,
    plan_schema_version         TEXT    NOT NULL,
    renderer_version            TEXT    NOT NULL,
    duration_algorithm_version  TEXT    NOT NULL,
    title                       TEXT    NOT NULL DEFAULT '',
    format                      TEXT    NOT NULL DEFAULT 'short'
                                        CHECK (format IN ('short', 'long_form')),
    total_estimated_duration_s  INTEGER NOT NULL DEFAULT 0,
    total_word_count            INTEGER NOT NULL DEFAULT 0,
    warnings_json               TEXT    NOT NULL DEFAULT '[]',
    requires_evidence_review    INTEGER NOT NULL DEFAULT 0,
    evidence_hash               TEXT    NOT NULL DEFAULT '',
    generation_run_id           INTEGER
                                    REFERENCES script_generation_runs(id)
                                    ON DELETE SET NULL,
    experiment_id               TEXT,
    status                      TEXT    NOT NULL DEFAULT 'draft'
                                        CHECK (status IN ('draft', 'approved', 'rejected')),
    approved_at                 TEXT,
    superseded_at               TEXT,
    rejected_at                 TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    UNIQUE (script_id, input_hash)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_pp_one_active_normal
    ON production_plans(topic_id)
    WHERE status = 'approved' AND superseded_at IS NULL AND experiment_id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_pp_one_active_experiment
    ON production_plans(topic_id, experiment_id)
    WHERE status = 'approved' AND superseded_at IS NULL AND experiment_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_pp_topic_created
    ON production_plans(topic_id, created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_pp_script
    ON production_plans(script_id, script_version);

CREATE TABLE IF NOT EXISTS production_segments (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id                 INTEGER NOT NULL
                                REFERENCES production_plans(id) ON DELETE CASCADE,
    segment_index           INTEGER NOT NULL,
    section_index           INTEGER NOT NULL,
    section_type            TEXT    NOT NULL,
    narration_text          TEXT    NOT NULL,
    estimated_duration_s    INTEGER NOT NULL DEFAULT 0,
    estimated_word_count    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    UNIQUE (plan_id, segment_index)
);

CREATE INDEX IF NOT EXISTS idx_ps_plan
    ON production_segments(plan_id, segment_index);

CREATE TABLE IF NOT EXISTS production_segment_citations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    segment_id      INTEGER NOT NULL
                        REFERENCES production_segments(id) ON DELETE CASCADE,
    claim_id        INTEGER NOT NULL
                        REFERENCES claims(id) ON DELETE RESTRICT,
    citation_order  INTEGER NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    UNIQUE (segment_id, claim_id),
    UNIQUE (segment_id, citation_order)
);

CREATE INDEX IF NOT EXISTS idx_psc_segment
    ON production_segment_citations(segment_id, citation_order);

CREATE INDEX IF NOT EXISTS idx_psc_claim
    ON production_segment_citations(claim_id);

CREATE TABLE IF NOT EXISTS production_plan_review_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id       INTEGER NOT NULL
                      REFERENCES production_plans(id) ON DELETE CASCADE,
    topic_id      INTEGER NOT NULL
                      REFERENCES topics(id) ON DELETE RESTRICT,
    script_id     INTEGER NOT NULL
                      REFERENCES scripts(id) ON DELETE RESTRICT,
    evidence_hash TEXT    NOT NULL,
    model         TEXT,
    prompt_hash   TEXT,
    experiment_id TEXT,
    decision      TEXT NOT NULL
                      CHECK (decision IN ('approved', 'rejected')),
    reason_code   TEXT
                      CHECK (reason_code IS NULL OR reason_code IN (
                          'segment_structure', 'narration_wording', 'pacing',
                          'duration', 'citation_mapping', 'evidence_concern',
                          'format_mismatch', 'other'
                      )),
    notes         TEXT,
    actor         TEXT,
    created_at    TEXT NOT NULL
                      DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_ppre_plan
    ON production_plan_review_events(plan_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_ppre_model_prompt
    ON production_plan_review_events(model, prompt_hash)
    WHERE model IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_ppre_experiment
    ON production_plan_review_events(experiment_id)
    WHERE experiment_id IS NOT NULL;
"""


_DDL_V11_NARRATION = """
CREATE TABLE IF NOT EXISTS voice_profiles (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id          INTEGER REFERENCES channels(id) ON DELETE SET NULL,
    provider            TEXT    NOT NULL,
    model               TEXT    NOT NULL,
    voice_id            TEXT    NOT NULL,
    name                TEXT    NOT NULL,
    language            TEXT    NOT NULL DEFAULT 'en-US',
    speaking_rate       REAL    NOT NULL DEFAULT 1.0,
    style               TEXT,
    stability           REAL,
    similarity_boost    REAL,
    settings_json       TEXT    NOT NULL DEFAULT '{}',
    version             INTEGER NOT NULL DEFAULT 1,
    is_default          INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0, 1)),
    superseded_by_id    INTEGER REFERENCES voice_profiles(id),
    created_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    updated_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_vp_channel
    ON voice_profiles (channel_id)
    WHERE channel_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS narration_runs (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id                 INTEGER NOT NULL REFERENCES production_plans(id) ON DELETE RESTRICT,
    plan_input_hash         TEXT    NOT NULL,
    voice_profile_id        INTEGER NOT NULL REFERENCES voice_profiles(id) ON DELETE RESTRICT,
    voice_profile_version   INTEGER NOT NULL,
    language                TEXT    NOT NULL,
    speaking_rate           REAL    NOT NULL,
    style                   TEXT,
    stability               REAL,
    similarity_boost        REAL,
    settings_json           TEXT    NOT NULL,
    output_format           TEXT    NOT NULL,
    sample_rate_hz          INTEGER NOT NULL,
    input_hash              TEXT    NOT NULL,
    status  TEXT    NOT NULL DEFAULT 'running'
            CHECK (status IN ('running', 'completed', 'failed', 'approved', 'rejected')),
    experiment_id           TEXT,
    notes                   TEXT,
    error_message           TEXT,
    completed_at            TEXT,
    approved_at             TEXT,
    rejected_at             TEXT,
    superseded_at           TEXT,
    superseded_by_run_id    INTEGER REFERENCES narration_runs(id),
    created_at              TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    updated_at              TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    UNIQUE (plan_id, input_hash)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_nr_one_active_normal
    ON narration_runs (plan_id)
    WHERE status = 'approved'
      AND superseded_at IS NULL
      AND experiment_id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_nr_one_active_experiment
    ON narration_runs (plan_id, experiment_id)
    WHERE status = 'approved'
      AND superseded_at IS NULL
      AND experiment_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_nr_plan
    ON narration_runs (plan_id);

CREATE TABLE IF NOT EXISTS narration_segment_assets (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                  INTEGER NOT NULL REFERENCES narration_runs(id) ON DELETE RESTRICT,
    segment_id              INTEGER NOT NULL REFERENCES production_segments(id) ON DELETE RESTRICT,
    narration_text_hash     TEXT    NOT NULL,
    provider                TEXT    NOT NULL,
    model                   TEXT    NOT NULL,
    voice_id                TEXT    NOT NULL,
    voice_profile_id        INTEGER NOT NULL REFERENCES voice_profiles(id) ON DELETE RESTRICT,
    voice_profile_version   INTEGER NOT NULL,
    language                TEXT    NOT NULL,
    speaking_rate           REAL    NOT NULL,
    style                   TEXT,
    stability               REAL,
    similarity_boost        REAL,
    settings_json_hash      TEXT    NOT NULL,
    output_format           TEXT    NOT NULL,
    sample_rate_hz          INTEGER NOT NULL,
    input_hash              TEXT    NOT NULL,
    status  TEXT    NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending', 'synthesized', 'rejected')),
    audio_path              TEXT,
    audio_sha256            TEXT,
    duration_seconds        REAL,
    characters_billed       INTEGER,
    cost_usd                REAL,
    superseded_at           TEXT,
    created_at              TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    updated_at              TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_nsa_active_per_run_segment
    ON narration_segment_assets (run_id, segment_id)
    WHERE status != 'rejected' AND superseded_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_nsa_run
    ON narration_segment_assets (run_id);

CREATE INDEX IF NOT EXISTS idx_nsa_segment
    ON narration_segment_assets (segment_id);

CREATE TABLE IF NOT EXISTS tts_calls (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                      INTEGER NOT NULL REFERENCES narration_runs(id) ON DELETE RESTRICT,
    segment_id                  INTEGER REFERENCES production_segments(id) ON DELETE RESTRICT,
    provider                    TEXT    NOT NULL,
    model                       TEXT    NOT NULL,
    voice_id                    TEXT    NOT NULL,
    input_characters            INTEGER NOT NULL,
    characters_billed           INTEGER NOT NULL,
    output_format               TEXT    NOT NULL,
    sample_rate_hz              INTEGER NOT NULL,
    duration_seconds            REAL,
    cost_usd                    REAL    NOT NULL,
    success                     INTEGER NOT NULL CHECK (success IN (0, 1)),
    error_message               TEXT,
    latency_ms                  INTEGER,
    request_id                  TEXT,
    provider_metadata_json      TEXT,
    narration_schema_version    TEXT    NOT NULL,
    narration_algorithm_version TEXT    NOT NULL,
    called_at                   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_tts_run
    ON tts_calls (run_id);

CREATE TABLE IF NOT EXISTS narration_review_events (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id               INTEGER NOT NULL REFERENCES narration_runs(id) ON DELETE RESTRICT,
    plan_id              INTEGER NOT NULL REFERENCES production_plans(id) ON DELETE RESTRICT,
    script_id            INTEGER NOT NULL REFERENCES scripts(id) ON DELETE RESTRICT,
    topic_id             INTEGER NOT NULL REFERENCES topics(id) ON DELETE RESTRICT,
    voice_profile_id     INTEGER NOT NULL REFERENCES voice_profiles(id) ON DELETE RESTRICT,
    provider             TEXT    NOT NULL,
    model                TEXT    NOT NULL,
    voice_id             TEXT    NOT NULL,
    experiment_id        TEXT,
    segment_id           INTEGER REFERENCES production_segments(id) ON DELETE RESTRICT,
    asset_id             INTEGER REFERENCES narration_segment_assets(id) ON DELETE RESTRICT,
    replacement_asset_id INTEGER REFERENCES narration_segment_assets(id) ON DELETE RESTRICT,
    event_type           TEXT    NOT NULL
                         CHECK (event_type IN
                                ('run_approved', 'run_rejected',
                                 'segment_rejected', 'segment_regenerated')),
    reason_code          TEXT,
    severity             INTEGER CHECK (severity BETWEEN 1 AND 5),
    expected_correction  TEXT,
    notes                TEXT,
    actor                TEXT,
    created_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_nre_run
    ON narration_review_events (run_id, created_at);

CREATE INDEX IF NOT EXISTS idx_nre_asset
    ON narration_review_events (asset_id, created_at)
    WHERE asset_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_nre_reason_code
    ON narration_review_events (reason_code)
    WHERE reason_code IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_nre_voice_provider
    ON narration_review_events (voice_profile_id, provider, model);

CREATE INDEX IF NOT EXISTS idx_nre_experiment
    ON narration_review_events (experiment_id)
    WHERE experiment_id IS NOT NULL;
"""


def _get_version(conn: sqlite3.Connection) -> int:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()
    if not exists:
        return 0
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    return row[0] if row else 0


def _set_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))


def _migrate(conn: sqlite3.Connection) -> None:
    current = _get_version(conn)
    if current == SCHEMA_VERSION:
        return

    if current == 0:
        logger.info("Initialising schema at version %d", SCHEMA_VERSION)
        conn.executescript(_DDL_V1)
        conn.executescript(_DDL_V2)
        conn.executescript(_DDL_V3)
        conn.executescript(_DDL_V4_NEW_TABLES)
        conn.executescript(_DDL_V5_SCORING)
        conn.executescript(_DDL_V6_PROMOTE)
        conn.executescript(_DDL_V7_RESEARCH)
        conn.executescript(_DDL_V8_CLAIMS)
        conn.executescript(_DDL_V9_SCRIPTS)
        conn.executescript(_DDL_V10_PRODUCTION)
        conn.executescript(_DDL_V11_NARRATION)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Schema ready at version %d", SCHEMA_VERSION)

    elif current == 1:
        logger.info("Migrating schema from version 1 to %d", SCHEMA_VERSION)
        conn.executescript(_DDL_V2)
        conn.executescript(_DDL_V3)
        conn.executescript(_DDL_V4_NEW_TABLES)
        conn.executescript(_DDL_V5_SCORING)
        conn.executescript(_DDL_V6_PROMOTE)
        conn.executescript(_DDL_V7_RESEARCH)
        conn.executescript(_DDL_V8_CLAIMS)
        conn.executescript(_DDL_V9_SCRIPTS)
        conn.executescript(_DDL_V10_PRODUCTION)
        conn.executescript(_DDL_V11_NARRATION)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 2:
        logger.info("Migrating schema from version 2 to %d", SCHEMA_VERSION)
        conn.executescript(_DDL_V3)
        conn.executescript(_DDL_V4_NEW_TABLES)
        conn.executescript(_DDL_V5_SCORING)
        conn.executescript(_DDL_V6_PROMOTE)
        conn.executescript(_DDL_V7_RESEARCH)
        conn.executescript(_DDL_V8_CLAIMS)
        conn.executescript(_DDL_V9_SCRIPTS)
        conn.executescript(_DDL_V10_PRODUCTION)
        conn.executescript(_DDL_V11_NARRATION)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 3:
        logger.info("Migrating schema from version 3 to %d", SCHEMA_VERSION)
        conn.executescript(_DDL_V4_NEW_TABLES)
        conn.executescript(_DDL_V5_SCORING)
        conn.executescript(_DDL_V6_PROMOTE)
        conn.executescript(_DDL_V7_RESEARCH)
        conn.executescript(_DDL_V8_CLAIMS)
        conn.executescript(_DDL_V9_SCRIPTS)
        conn.executescript(_DDL_V10_PRODUCTION)
        conn.executescript(_DDL_V11_NARRATION)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 4:
        logger.info("Migrating schema from version 4 to %d", SCHEMA_VERSION)
        conn.executescript(_DDL_V5_SCORING)
        conn.executescript(_DDL_V6_PROMOTE)
        conn.executescript(_DDL_V7_RESEARCH)
        conn.executescript(_DDL_V8_CLAIMS)
        conn.executescript(_DDL_V9_SCRIPTS)
        conn.executescript(_DDL_V10_PRODUCTION)
        conn.executescript(_DDL_V11_NARRATION)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 5:
        logger.info("Migrating schema from version 5 to %d", SCHEMA_VERSION)
        conn.executescript(_DDL_V6_PROMOTE)
        conn.executescript(_DDL_V7_RESEARCH)
        conn.executescript(_DDL_V8_CLAIMS)
        conn.executescript(_DDL_V9_SCRIPTS)
        conn.executescript(_DDL_V10_PRODUCTION)
        conn.executescript(_DDL_V11_NARRATION)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 6:
        logger.info("Migrating schema from version 6 to %d", SCHEMA_VERSION)
        conn.executescript(_DDL_V7_RESEARCH)
        conn.executescript(_DDL_V8_CLAIMS)
        conn.executescript(_DDL_V9_SCRIPTS)
        conn.executescript(_DDL_V10_PRODUCTION)
        conn.executescript(_DDL_V11_NARRATION)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 7:
        logger.info("Migrating schema from version 7 to %d", SCHEMA_VERSION)
        conn.executescript(_DDL_V8_CLAIMS)
        conn.executescript(_DDL_V9_SCRIPTS)
        conn.executescript(_DDL_V10_PRODUCTION)
        conn.executescript(_DDL_V11_NARRATION)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 8:
        logger.info("Migrating schema from version 8 to %d", SCHEMA_VERSION)
        conn.executescript(_DDL_V9_SCRIPTS)
        conn.executescript(_DDL_V10_PRODUCTION)
        conn.executescript(_DDL_V11_NARRATION)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 9:
        logger.info("Migrating schema from version 9 to %d", SCHEMA_VERSION)
        conn.executescript(_DDL_V10_PRODUCTION)
        conn.executescript(_DDL_V11_NARRATION)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 10:
        logger.info("Migrating schema from version 10 to %d", SCHEMA_VERSION)
        conn.executescript(_DDL_V11_NARRATION)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    else:
        raise RuntimeError(
            f"Unsupported schema version {current}; expected <= {SCHEMA_VERSION}. "
            "Manual migration required."
        )


def open_db(path: Path) -> sqlite3.Connection:
    """Open (or create) the SQLite database, enforce FK constraints, and run migrations."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _migrate(conn)
    conn.commit()
    logger.debug("Database open: %s", path)
    return conn
