"""Database connection management and schema initialization.

SQLite is the default backend for local development and unit tests.
PostgreSQL is the production backend, selected when ACE_DATABASE_URL is set.

The public entry point is open_db():
  - SQLite path: open_db(path)  — unchanged from Phases 1–14
  - PostgreSQL path: open_db_postgres(url) — returns a CompatConnection
  - Auto-dispatch: get_db_connection() reads ACE_DATABASE_URL and dispatches
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.core.db_compat import CompatConnection

logger = get_logger(__name__)

# Increment when the schema changes; add a migration branch in _migrate().
SCHEMA_VERSION = 23

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


_DDL_V12_CAPTIONS = """
CREATE TABLE IF NOT EXISTS caption_runs (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    narration_run_id            INTEGER NOT NULL
                                    REFERENCES narration_runs(id) ON DELETE RESTRICT,
    plan_id                     INTEGER NOT NULL
                                    REFERENCES production_plans(id) ON DELETE RESTRICT,
    script_id                   INTEGER NOT NULL
                                    REFERENCES scripts(id) ON DELETE RESTRICT,
    topic_id                    INTEGER NOT NULL
                                    REFERENCES topics(id) ON DELETE RESTRICT,
    experiment_id               TEXT,
    input_hash                  TEXT    NOT NULL,
    caption_schema_version      TEXT    NOT NULL,
    segmentation_version        TEXT    NOT NULL,
    timing_algorithm_version    TEXT    NOT NULL,
    style_version               TEXT    NOT NULL,
    exporter_version            TEXT    NOT NULL,
    language                    TEXT    NOT NULL DEFAULT 'en-US',
    status                      TEXT    NOT NULL DEFAULT 'running'
                                        CHECK (status IN (
                                            'running', 'completed', 'failed',
                                            'approved', 'rejected')),
    total_cue_count             INTEGER NOT NULL DEFAULT 0,
    total_duration_ms           INTEGER NOT NULL DEFAULT 0,
    failure_reason              TEXT,
    srt_path                    TEXT,
    vtt_path                    TEXT,
    json_path                   TEXT,
    srt_sha256                  TEXT,
    vtt_sha256                  TEXT,
    json_sha256                 TEXT,
    approved_at                 TEXT,
    rejected_at                 TEXT,
    superseded_at               TEXT,
    superseded_by_run_id        INTEGER REFERENCES caption_runs(id),
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    UNIQUE (narration_run_id, input_hash)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_cr_one_active_normal
    ON caption_runs (narration_run_id)
    WHERE status = 'approved'
      AND superseded_at IS NULL
      AND experiment_id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_cr_one_active_experiment
    ON caption_runs (narration_run_id, experiment_id)
    WHERE status = 'approved'
      AND superseded_at IS NULL
      AND experiment_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_cr_narration_run
    ON caption_runs (narration_run_id, created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_cr_plan
    ON caption_runs (plan_id, created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS caption_cues (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              INTEGER NOT NULL
                            REFERENCES caption_runs(id) ON DELETE RESTRICT,
    segment_id          INTEGER NOT NULL
                            REFERENCES production_segments(id) ON DELETE RESTRICT,
    narration_asset_id  INTEGER NOT NULL
                            REFERENCES narration_segment_assets(id) ON DELETE RESTRICT,
    narration_text_hash TEXT    NOT NULL,
    audio_sha256        TEXT    NOT NULL,
    cue_index           INTEGER NOT NULL,
    segment_cue_index   INTEGER NOT NULL,
    text                TEXT    NOT NULL,
    start_ms            INTEGER NOT NULL,
    end_ms              INTEGER NOT NULL,
    line_count          INTEGER NOT NULL,
    char_count          INTEGER NOT NULL,
    timing_source       TEXT    NOT NULL
                            CHECK (timing_source IN (
                                'estimated', 'provider_native', 'forced_alignment')),
    warnings_json       TEXT    NOT NULL DEFAULT '[]',
    superseded_at       TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    UNIQUE (run_id, cue_index),
    UNIQUE (run_id, segment_id, segment_cue_index)
);

CREATE INDEX IF NOT EXISTS idx_cc_run
    ON caption_cues (run_id, cue_index);

CREATE INDEX IF NOT EXISTS idx_cc_segment
    ON caption_cues (segment_id, segment_cue_index);

CREATE TABLE IF NOT EXISTS caption_review_events (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                      INTEGER NOT NULL
                                    REFERENCES caption_runs(id) ON DELETE RESTRICT,
    cue_id                      INTEGER
                                    REFERENCES caption_cues(id) ON DELETE RESTRICT,
    segment_id                  INTEGER
                                    REFERENCES production_segments(id) ON DELETE RESTRICT,
    narration_asset_id          INTEGER
                                    REFERENCES narration_segment_assets(id) ON DELETE RESTRICT,
    narration_run_id            INTEGER NOT NULL
                                    REFERENCES narration_runs(id) ON DELETE RESTRICT,
    plan_id                     INTEGER NOT NULL
                                    REFERENCES production_plans(id) ON DELETE RESTRICT,
    script_id                   INTEGER NOT NULL
                                    REFERENCES scripts(id) ON DELETE RESTRICT,
    topic_id                    INTEGER NOT NULL
                                    REFERENCES topics(id) ON DELETE RESTRICT,
    voice_profile_id            INTEGER NOT NULL
                                    REFERENCES voice_profiles(id) ON DELETE RESTRICT,
    provider                    TEXT    NOT NULL,
    model                       TEXT    NOT NULL,
    voice_id                    TEXT    NOT NULL,
    experiment_id               TEXT,
    caption_schema_version      TEXT    NOT NULL,
    segmentation_version        TEXT    NOT NULL,
    timing_algorithm_version    TEXT    NOT NULL,
    style_version               TEXT    NOT NULL,
    exporter_version            TEXT    NOT NULL,
    event_type                  TEXT    NOT NULL
                                    CHECK (event_type IN (
                                        'run_approved', 'run_rejected', 'cue_rejected')),
    reason_code                 TEXT,
    severity                    INTEGER CHECK (severity BETWEEN 1 AND 5),
    expected_correction         TEXT,
    notes                       TEXT,
    actor                       TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_cre_run
    ON caption_review_events (run_id, created_at);

CREATE INDEX IF NOT EXISTS idx_cre_cue
    ON caption_review_events (cue_id, created_at)
    WHERE cue_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_cre_narration_run
    ON caption_review_events (narration_run_id);

CREATE INDEX IF NOT EXISTS idx_cre_reason_code
    ON caption_review_events (reason_code)
    WHERE reason_code IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_cre_experiment
    ON caption_review_events (experiment_id)
    WHERE experiment_id IS NOT NULL;
"""


_DDL_V13_SCENES = """
CREATE TABLE IF NOT EXISTS scene_manifests (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    caption_run_id              INTEGER NOT NULL
                                    REFERENCES caption_runs(id) ON DELETE RESTRICT,
    narration_run_id            INTEGER NOT NULL
                                    REFERENCES narration_runs(id) ON DELETE RESTRICT,
    plan_id                     INTEGER NOT NULL
                                    REFERENCES production_plans(id) ON DELETE RESTRICT,
    script_id                   INTEGER NOT NULL
                                    REFERENCES scripts(id) ON DELETE RESTRICT,
    topic_id                    INTEGER NOT NULL
                                    REFERENCES topics(id) ON DELETE RESTRICT,
    experiment_id               TEXT,
    input_hash                  TEXT    NOT NULL UNIQUE,
    manifest_schema_version     TEXT    NOT NULL,
    planner_version             TEXT    NOT NULL,
    status                      TEXT    NOT NULL DEFAULT 'draft'
                                        CHECK (status IN (
                                            'draft','approved','rejected','superseded')),
    total_scene_count           INTEGER NOT NULL DEFAULT 0,
    total_asset_count           INTEGER NOT NULL DEFAULT 0,
    total_duration_ms           INTEGER NOT NULL DEFAULT 0,
    approved_at                 TEXT,
    rejected_at                 TEXT,
    superseded_at               TEXT,
    superseded_by_manifest_id   INTEGER REFERENCES scene_manifests(id),
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_sm_topic
    ON scene_manifests (topic_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_sm_caption_run
    ON scene_manifests (caption_run_id);

CREATE TABLE IF NOT EXISTS scene_manifest_scenes (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    manifest_id             INTEGER NOT NULL
                                REFERENCES scene_manifests(id) ON DELETE CASCADE,
    scene_index             INTEGER NOT NULL,
    segment_id              INTEGER NOT NULL
                                REFERENCES production_segments(id) ON DELETE RESTRICT,
    narration_asset_id      INTEGER
                                REFERENCES narration_segment_assets(id) ON DELETE RESTRICT,
    caption_cue_ids_json    TEXT    NOT NULL DEFAULT '[]',
    claim_ids_json          TEXT    NOT NULL DEFAULT '[]',
    evidence_ids_json       TEXT    NOT NULL DEFAULT '[]',
    script_section_index    INTEGER,
    narration_text          TEXT    NOT NULL,
    start_ms                INTEGER NOT NULL DEFAULT 0,
    end_ms                  INTEGER NOT NULL DEFAULT 0,
    duration_ms             INTEGER NOT NULL DEFAULT 0,
    shot_type               TEXT    NOT NULL,
    camera_movement         TEXT    NOT NULL,
    transition_in           TEXT    NOT NULL,
    transition_out          TEXT    NOT NULL,
    visual_objective        TEXT    NOT NULL,
    visual_rationale        TEXT    NOT NULL,
    confidence              REAL    NOT NULL DEFAULT 0.0,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    UNIQUE (manifest_id, scene_index)
);

CREATE INDEX IF NOT EXISTS idx_sms_manifest
    ON scene_manifest_scenes (manifest_id, scene_index);

CREATE INDEX IF NOT EXISTS idx_sms_segment
    ON scene_manifest_scenes (segment_id);

CREATE TABLE IF NOT EXISTS scene_manifest_assets (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    scene_id                    INTEGER NOT NULL
                                    REFERENCES scene_manifest_scenes(id) ON DELETE CASCADE,
    manifest_id                 INTEGER NOT NULL
                                    REFERENCES scene_manifests(id) ON DELETE CASCADE,
    asset_index                 INTEGER NOT NULL,
    category                    TEXT    NOT NULL,
    priority                    TEXT    NOT NULL,
    description                 TEXT    NOT NULL,
    search_query                TEXT,
    provider                    TEXT,
    source_url                  TEXT,
    license_status              TEXT    NOT NULL DEFAULT 'unknown',
    license_name                TEXT,
    attribution_required        INTEGER NOT NULL DEFAULT 0,
    attribution_text            TEXT,
    commercial_safe             INTEGER NOT NULL DEFAULT 0,
    verification_status         TEXT    NOT NULL DEFAULT 'unverified',
    usage_rights_json           TEXT    NOT NULL DEFAULT '{}',
    ai_generation_requested     INTEGER NOT NULL DEFAULT 0,
    ai_generation_prompt        TEXT,
    ai_generation_model         TEXT,
    claim_ids_json              TEXT    NOT NULL DEFAULT '[]',
    evidence_ids_json           TEXT    NOT NULL DEFAULT '[]',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    UNIQUE (scene_id, asset_index)
);

CREATE INDEX IF NOT EXISTS idx_sma_scene
    ON scene_manifest_assets (scene_id, asset_index);

CREATE INDEX IF NOT EXISTS idx_sma_manifest
    ON scene_manifest_assets (manifest_id);

CREATE INDEX IF NOT EXISTS idx_sma_category
    ON scene_manifest_assets (category);

CREATE TABLE IF NOT EXISTS scene_manifest_review_events (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    manifest_id                 INTEGER NOT NULL
                                    REFERENCES scene_manifests(id) ON DELETE CASCADE,
    scene_id                    INTEGER
                                    REFERENCES scene_manifest_scenes(id) ON DELETE CASCADE,
    topic_id                    INTEGER NOT NULL
                                    REFERENCES topics(id) ON DELETE RESTRICT,
    plan_id                     INTEGER NOT NULL
                                    REFERENCES production_plans(id) ON DELETE RESTRICT,
    script_id                   INTEGER NOT NULL
                                    REFERENCES scripts(id) ON DELETE RESTRICT,
    caption_run_id              INTEGER NOT NULL
                                    REFERENCES caption_runs(id) ON DELETE RESTRICT,
    narration_run_id            INTEGER NOT NULL
                                    REFERENCES narration_runs(id) ON DELETE RESTRICT,
    experiment_id               TEXT,
    manifest_schema_version     TEXT    NOT NULL,
    event_type                  TEXT    NOT NULL
                                    CHECK (event_type IN (
                                        'approved','rejected','scene_rejected')),
    reason_code                 TEXT,
    severity                    INTEGER CHECK (severity BETWEEN 1 AND 5),
    expected_correction         TEXT,
    notes                       TEXT,
    actor                       TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_smre_manifest
    ON scene_manifest_review_events (manifest_id, created_at);

CREATE INDEX IF NOT EXISTS idx_smre_event_type
    ON scene_manifest_review_events (event_type);
"""


_DDL_V14_RENDERS = """
CREATE TABLE IF NOT EXISTS render_manifests (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    scene_manifest_id       INTEGER NOT NULL
                                REFERENCES scene_manifests(id) ON DELETE RESTRICT,
    narration_run_id        INTEGER NOT NULL
                                REFERENCES narration_runs(id) ON DELETE RESTRICT,
    caption_run_id          INTEGER NOT NULL
                                REFERENCES caption_runs(id) ON DELETE RESTRICT,
    topic_id                INTEGER NOT NULL
                                REFERENCES topics(id) ON DELETE RESTRICT,
    plan_id                 INTEGER NOT NULL
                                REFERENCES production_plans(id) ON DELETE RESTRICT,
    script_id               INTEGER NOT NULL
                                REFERENCES scripts(id) ON DELETE RESTRICT,
    experiment_id           TEXT,
    input_hash              TEXT    NOT NULL UNIQUE,
    render_schema_version   TEXT    NOT NULL,
    compositor_version      TEXT    NOT NULL,
    total_scene_count       INTEGER NOT NULL DEFAULT 0,
    total_duration_ms       INTEGER NOT NULL DEFAULT 0,
    width                   INTEGER NOT NULL DEFAULT 1080,
    height                  INTEGER NOT NULL DEFAULT 1920,
    fps                     INTEGER NOT NULL DEFAULT 30,
    caption_burn_in         INTEGER NOT NULL DEFAULT 0 CHECK (caption_burn_in IN (0,1)),
    status                  TEXT    NOT NULL DEFAULT 'draft'
                                CHECK (status IN ('draft','approved','rejected','superseded')),
    approved_at             TEXT,
    rejected_at             TEXT,
    superseded_at           TEXT,
    superseded_by_id        INTEGER REFERENCES render_manifests(id),
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_rm_one_active_normal
    ON render_manifests(scene_manifest_id)
    WHERE status = 'approved' AND superseded_at IS NULL AND experiment_id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_rm_one_active_experiment
    ON render_manifests(scene_manifest_id, experiment_id)
    WHERE status = 'approved' AND superseded_at IS NULL AND experiment_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_rm_topic
    ON render_manifests (topic_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_rm_scene_manifest
    ON render_manifests (scene_manifest_id);

CREATE TABLE IF NOT EXISTS render_jobs (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    render_manifest_id          INTEGER NOT NULL
                                    REFERENCES render_manifests(id) ON DELETE RESTRICT,
    backend                     TEXT    NOT NULL DEFAULT 'ffmpeg',
    backend_version             TEXT    NOT NULL,
    output_path                 TEXT,
    output_sha256               TEXT,
    duration_s                  REAL,
    file_size_bytes             INTEGER,
    render_time_s               REAL,
    width                       INTEGER NOT NULL,
    height                      INTEGER NOT NULL,
    fps                         INTEGER NOT NULL,
    video_codec                 TEXT    NOT NULL DEFAULT 'libx264',
    audio_codec                 TEXT    NOT NULL DEFAULT 'aac',
    crf                         INTEGER NOT NULL DEFAULT 23,
    audio_bitrate               TEXT    NOT NULL DEFAULT '128k',
    caption_burn_in             INTEGER NOT NULL DEFAULT 0 CHECK (caption_burn_in IN (0,1)),
    ffmpeg_cmd_json             TEXT,
    status                      TEXT    NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending','rendering','completed','failed')),
    error_message               TEXT,
    validated                   INTEGER NOT NULL DEFAULT 0 CHECK (validated IN (0,1)),
    validation_metadata_json    TEXT,
    started_at                  TEXT,
    completed_at                TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_rj_manifest
    ON render_jobs (render_manifest_id, created_at DESC);

CREATE TABLE IF NOT EXISTS render_review_events (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    render_manifest_id      INTEGER NOT NULL
                                REFERENCES render_manifests(id) ON DELETE RESTRICT,
    render_job_id           INTEGER
                                REFERENCES render_jobs(id) ON DELETE RESTRICT,
    topic_id                INTEGER NOT NULL
                                REFERENCES topics(id) ON DELETE RESTRICT,
    plan_id                 INTEGER NOT NULL
                                REFERENCES production_plans(id) ON DELETE RESTRICT,
    script_id               INTEGER NOT NULL
                                REFERENCES scripts(id) ON DELETE RESTRICT,
    scene_manifest_id       INTEGER NOT NULL
                                REFERENCES scene_manifests(id) ON DELETE RESTRICT,
    experiment_id           TEXT,
    render_schema_version   TEXT    NOT NULL,
    event_type              TEXT    NOT NULL
                            CHECK (event_type IN ('render_approved','render_rejected')),
    reason_code             TEXT
                            CHECK (reason_code IS NULL OR reason_code IN (
                                'audio_sync','visual_quality','caption_alignment',
                                'duration_mismatch','encoding_error',
                                'validation_failure','other')),
    severity                INTEGER CHECK (severity BETWEEN 1 AND 5),
    expected_correction     TEXT,
    notes                   TEXT,
    actor                   TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_rre_manifest
    ON render_review_events (render_manifest_id, created_at);

CREATE INDEX IF NOT EXISTS idx_rre_event_type
    ON render_review_events (event_type);

CREATE TABLE IF NOT EXISTS render_manifest_scenes (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    render_manifest_id      INTEGER NOT NULL
                                REFERENCES render_manifests(id) ON DELETE CASCADE,
    scene_index             INTEGER NOT NULL,
    scene_id                INTEGER NOT NULL,
    segment_id              INTEGER NOT NULL,
    narration_asset_id      INTEGER,
    audio_path              TEXT,
    audio_sha256            TEXT,
    start_ms                INTEGER NOT NULL,
    end_ms                  INTEGER NOT NULL,
    duration_ms             INTEGER NOT NULL,
    shot_type               TEXT    NOT NULL DEFAULT '',
    camera_movement         TEXT    NOT NULL DEFAULT '',
    visual_objective        TEXT    NOT NULL DEFAULT '',
    caption_cue_ids_json    TEXT    NOT NULL DEFAULT '[]',
    primary_asset_id        INTEGER,
    has_placeholder         INTEGER NOT NULL DEFAULT 0 CHECK (has_placeholder IN (0,1)),
    created_at              TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    UNIQUE (render_manifest_id, scene_index)
);

CREATE INDEX IF NOT EXISTS idx_rms_manifest
    ON render_manifest_scenes (render_manifest_id);

CREATE TABLE IF NOT EXISTS resolved_assets (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    planned_asset_id        INTEGER NOT NULL,
    scene_id                INTEGER NOT NULL,
    segment_id              INTEGER NOT NULL,
    render_manifest_id      INTEGER
                                REFERENCES render_manifests(id) ON DELETE SET NULL,
    provider_identity       TEXT    NOT NULL DEFAULT '',
    provider_asset_id       TEXT,
    source_reference        TEXT,
    local_path              TEXT,
    mime_type               TEXT,
    file_size_bytes         INTEGER,
    sha256                  TEXT,
    width_px                INTEGER,
    height_px               INTEGER,
    duration_s              REAL,
    fps                     REAL,
    license_status          TEXT    NOT NULL DEFAULT 'unverified'
                                CHECK (license_status IN (
                                    'unverified','verified','rejected','not_required')),
    license_id              TEXT,
    usage_rights            TEXT,
    attribution_required    INTEGER NOT NULL DEFAULT 0 CHECK (attribution_required IN (0,1)),
    attribution_text        TEXT,
    commercial_use_verified INTEGER NOT NULL DEFAULT 0 CHECK (commercial_use_verified IN (0,1)),
    verification_actor      TEXT,
    verification_method     TEXT,
    verified_at             TEXT,
    warnings_json           TEXT    NOT NULL DEFAULT '[]',
    superseded_at           TEXT,
    superseded_by_id        INTEGER REFERENCES resolved_assets(id),
    created_at              TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    updated_at              TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_ra_scene
    ON resolved_assets (scene_id);

CREATE INDEX IF NOT EXISTS idx_ra_manifest
    ON resolved_assets (render_manifest_id);
"""


_DDL_V15_PUBLISHING = """
CREATE TABLE IF NOT EXISTS publishing_plans (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    render_manifest_id          INTEGER NOT NULL REFERENCES render_manifests(id),
    render_job_id               INTEGER REFERENCES render_jobs(id),
    topic_id                    INTEGER NOT NULL,
    production_plan_id          INTEGER NOT NULL,
    script_id                   INTEGER NOT NULL,
    scene_manifest_id           INTEGER NOT NULL,
    narration_run_id            INTEGER NOT NULL,
    caption_run_id              INTEGER NOT NULL,
    experiment_id               TEXT,

    input_hash                  TEXT NOT NULL UNIQUE,
    publishing_engine_version   TEXT NOT NULL,
    metadata_version            TEXT NOT NULL,

    provider                    TEXT NOT NULL,
    provider_version            TEXT NOT NULL,

    title                       TEXT NOT NULL,
    description                 TEXT NOT NULL DEFAULT '',
    tags_json                   TEXT NOT NULL DEFAULT '[]',
    language                    TEXT NOT NULL DEFAULT 'en',
    category                    TEXT,
    visibility                  TEXT NOT NULL DEFAULT 'private'
                                    CHECK (visibility IN ('private','unlisted','public')),
    made_for_kids               INTEGER NOT NULL DEFAULT 0 CHECK (made_for_kids IN (0,1)),
    playlist_id                 TEXT,
    thumbnail_path              TEXT,
    captions_path               TEXT,
    copyright_notice            TEXT,
    licensing_notes             TEXT,
    publication_notes           TEXT,

    schedule_type               TEXT NOT NULL DEFAULT 'immediate'
                                    CHECK (schedule_type IN ('immediate','scheduled','manual')),
    scheduled_at                TEXT,
    timezone                    TEXT,

    status                      TEXT NOT NULL DEFAULT 'draft'
                                    CHECK (status IN ('draft','approved','rejected')),
    approved_at                 TEXT,
    rejected_at                 TEXT,
    rejection_reason            TEXT,

    superseded_at               TEXT,
    superseded_by_id            INTEGER REFERENCES publishing_plans(id),

    created_at                  TEXT NOT NULL,
    updated_at                  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pp_render_manifest ON publishing_plans (render_manifest_id);
CREATE INDEX IF NOT EXISTS idx_pp_topic ON publishing_plans (topic_id);
CREATE INDEX IF NOT EXISTS idx_pp_status ON publishing_plans (status);
CREATE INDEX IF NOT EXISTS idx_pp_provider ON publishing_plans (provider);

CREATE TABLE IF NOT EXISTS publishing_jobs (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    publishing_plan_id          INTEGER NOT NULL REFERENCES publishing_plans(id),
    attempt_number              INTEGER NOT NULL DEFAULT 1,
    provider                    TEXT NOT NULL,
    provider_version            TEXT NOT NULL,

    status                      TEXT NOT NULL DEFAULT 'queued'
                                    CHECK (status IN (
                                        'queued','running','retry_scheduled',
                                        'completed','failed','cancelled'
                                    )),
    error_message               TEXT,
    retry_count                 INTEGER NOT NULL DEFAULT 0,
    retry_after                 TEXT,

    started_at                  TEXT,
    completed_at                TEXT,
    created_at                  TEXT NOT NULL,
    updated_at                  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pj_plan ON publishing_jobs (publishing_plan_id);
CREATE INDEX IF NOT EXISTS idx_pj_status ON publishing_jobs (status);

CREATE TABLE IF NOT EXISTS publications (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    publishing_plan_id          INTEGER NOT NULL REFERENCES publishing_plans(id),
    publishing_job_id           INTEGER NOT NULL REFERENCES publishing_jobs(id),

    provider                    TEXT NOT NULL,
    provider_version            TEXT NOT NULL,
    provider_video_id           TEXT,
    provider_url                TEXT,
    provider_status_json        TEXT NOT NULL DEFAULT '{}',

    status                      TEXT NOT NULL DEFAULT 'uploading'
                                    CHECK (status IN (
                                        'uploading','uploaded','scheduled',
                                        'published','failed','deleted'
                                    )),
    error_message               TEXT,

    visibility                  TEXT NOT NULL DEFAULT 'private',
    scheduled_at                TEXT,
    published_at                TEXT,
    deleted_at                  TEXT,

    publishing_engine_version   TEXT NOT NULL,
    input_hash                  TEXT NOT NULL,
    output_sha256               TEXT NOT NULL,

    created_at                  TEXT NOT NULL,
    updated_at                  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pub_plan ON publications (publishing_plan_id);
CREATE INDEX IF NOT EXISTS idx_pub_job ON publications (publishing_job_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_pub_provider_video
    ON publications (provider, provider_video_id)
    WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS publishing_review_events (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    publishing_plan_id          INTEGER NOT NULL REFERENCES publishing_plans(id),
    publishing_job_id           INTEGER REFERENCES publishing_jobs(id),
    publication_id              INTEGER REFERENCES publications(id),

    topic_id                    INTEGER NOT NULL,
    production_plan_id          INTEGER NOT NULL,
    script_id                   INTEGER NOT NULL,
    render_manifest_id          INTEGER NOT NULL,
    provider                    TEXT NOT NULL,
    experiment_id               TEXT,

    event_type                  TEXT NOT NULL
                                    CHECK (event_type IN (
                                        'plan_prepared','plan_approved','plan_rejected',
                                        'metadata_rejected',
                                        'job_queued','job_started','job_completed','job_failed',
                                        'retry_requested','schedule_changed',
                                        'publication_approved','publication_rejected',
                                        'cancellation_requested','superseded'
                                    )),
    reason_code                 TEXT,
    severity                    INTEGER CHECK (
        severity IS NULL OR (severity >= 1 AND severity <= 5)
    ),
    notes                       TEXT,
    actor                       TEXT,
    expected_correction         TEXT,

    created_at                  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pre_plan ON publishing_review_events (publishing_plan_id);
CREATE INDEX IF NOT EXISTS idx_pre_event_type ON publishing_review_events (event_type);
"""

# Phase 16 DDL — Analytics Engine (provider-neutral, immutable, append-only)
_DDL_V16_ANALYTICS = """
CREATE TABLE IF NOT EXISTS analytics_snapshots (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Full publication provenance (attribution)
    publication_id              INTEGER NOT NULL,
    publishing_plan_id          INTEGER NOT NULL,
    publishing_job_id           INTEGER NOT NULL,
    render_manifest_id          INTEGER NOT NULL,
    scene_manifest_id           INTEGER NOT NULL,
    production_plan_id          INTEGER NOT NULL,
    script_id                   INTEGER NOT NULL,
    topic_id                    INTEGER NOT NULL,
    narration_run_id            INTEGER NOT NULL,
    caption_run_id              INTEGER NOT NULL,
    experiment_id               TEXT,

    -- Provider identity
    provider                    TEXT NOT NULL,
    provider_version            TEXT NOT NULL,
    adapter_version             TEXT NOT NULL,
    engine_version              TEXT NOT NULL,
    analytics_schema_version    TEXT NOT NULL,
    db_schema_version           INTEGER NOT NULL,

    -- Content (raw preserved, canonical in metrics table)
    input_hash                  TEXT NOT NULL UNIQUE,
    raw_metrics_json            TEXT NOT NULL DEFAULT '{}',

    -- Coverage window
    period_start                TEXT,
    period_end                  TEXT,

    -- Reporting completeness.  0 = provisional/partial, 1 = provider-confirmed final.
    -- Providers may not report complete data for the current day or near-current periods.
    -- Aggregates must not silently mix provisional and final data without recording this.
    is_period_complete          INTEGER NOT NULL DEFAULT 0
                                    CHECK (is_period_complete IN (0,1)),

    -- Monetary context.  Required when any monetary metric (revenue_estimate) is present.
    -- ISO 4217 three-letter currency code (e.g. 'USD').  NULL for non-monetary snapshots.
    -- revenue_estimate is labeled 'estimate' because providers may later revise figures.
    currency_code               TEXT
                                    CHECK (currency_code IS NULL OR length(currency_code) = 3),

    -- Timestamps (no updated_at — immutable)
    ingested_at                 TEXT NOT NULL,
    created_at                  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_as_publication ON analytics_snapshots (publication_id);
CREATE INDEX IF NOT EXISTS idx_as_topic ON analytics_snapshots (topic_id);
CREATE INDEX IF NOT EXISTS idx_as_provider ON analytics_snapshots (provider);

CREATE TABLE IF NOT EXISTS analytics_metrics (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id                 INTEGER NOT NULL REFERENCES analytics_snapshots(id),
    publication_id              INTEGER NOT NULL,
    topic_id                    INTEGER NOT NULL,
    provider                    TEXT NOT NULL,

    metric_name                 TEXT NOT NULL,
    metric_value                REAL NOT NULL,
    period_start                TEXT,
    period_end                  TEXT,

    input_hash                  TEXT NOT NULL,
    created_at                  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_am_snapshot ON analytics_metrics (snapshot_id);
CREATE INDEX IF NOT EXISTS idx_am_publication ON analytics_metrics (publication_id);
CREATE INDEX IF NOT EXISTS idx_am_metric_name ON analytics_metrics (metric_name);

CREATE TABLE IF NOT EXISTS analytics_aggregates (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    publication_id              INTEGER NOT NULL,
    topic_id                    INTEGER NOT NULL,
    provider                    TEXT NOT NULL,

    period_type                 TEXT NOT NULL
                                    CHECK (period_type IN ('daily','weekly','monthly','lifetime')),
    period_key                  TEXT NOT NULL,

    metric_name                 TEXT NOT NULL,
    metric_value                REAL NOT NULL,
    snapshot_count              INTEGER NOT NULL DEFAULT 0,

    -- Calculation method: 'sum' for additive/monetary; 'latest_observation' for
    -- gauge/ratio metrics (AGG_LAST).  Phase 11 must not treat a latest_observation
    -- as a mathematically recomputed aggregate.
    calculation_method          TEXT NOT NULL DEFAULT 'sum'
                                    CHECK (calculation_method IN ('sum','latest_observation')),

    -- Aggregate-level currency code.  Carried from source snapshots for monetary metrics.
    -- NULL for non-monetary aggregates.  Aggregation raises CurrencyMismatchError if
    -- source snapshots carry different currency codes for the same monetary metric.
    currency_code               TEXT
                                    CHECK (currency_code IS NULL OR length(currency_code) = 3),

    -- JSON array of snapshot IDs that contributed to this aggregate.
    -- Enables reproducible lineage: Phase 11 can trace every aggregate back to
    -- the exact snapshot versions that produced it.
    source_snapshot_ids_json    TEXT NOT NULL DEFAULT '[]',

    input_hash                  TEXT NOT NULL,
    created_at                  TEXT NOT NULL,

    UNIQUE (publication_id, provider, period_type, period_key, metric_name)
);
CREATE INDEX IF NOT EXISTS idx_aa_publication ON analytics_aggregates (publication_id);
CREATE INDEX IF NOT EXISTS idx_aa_topic ON analytics_aggregates (topic_id);
CREATE INDEX IF NOT EXISTS idx_aa_period ON analytics_aggregates (period_type, period_key);

CREATE TABLE IF NOT EXISTS analytics_review_events (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id                 INTEGER NOT NULL REFERENCES analytics_snapshots(id),

    severity                    TEXT NOT NULL
                                    CHECK (severity IN (
                                        'info','warning','error','critical','other'
                                    )),
    notes                       TEXT NOT NULL DEFAULT '',
    reviewer                    TEXT NOT NULL DEFAULT '',
    input_hash                  TEXT NOT NULL,

    created_at                  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_are_snapshot ON analytics_review_events (snapshot_id);
CREATE INDEX IF NOT EXISTS idx_are_severity ON analytics_review_events (severity);
"""

# Phase 11 DDL — Learning & Optimization Engine.
# Three tables, all append-only:
#   learning_runs             — one row per optimizer invocation
#   optimization_recommendations — one row per recommendation (superseded not deleted)
#   recommendation_review_events — append-only human review actions
_DDL_V17_LEARNING = """
CREATE TABLE IF NOT EXISTS learning_runs (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id                INTEGER NOT NULL REFERENCES topics(id),
    publication_id          INTEGER,

    -- Counts populated on completion
    publication_count       INTEGER NOT NULL DEFAULT 0,
    recommendation_count    INTEGER NOT NULL DEFAULT 0,

    status                  TEXT NOT NULL DEFAULT 'running'
                                CHECK (status IN ('running','completed','partial','failed')),

    engine_version          TEXT NOT NULL,
    schema_version          TEXT NOT NULL,
    input_hash              TEXT NOT NULL,

    error                   TEXT,

    created_at              TEXT NOT NULL
                                DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    completed_at            TEXT
);
CREATE INDEX IF NOT EXISTS idx_lr_topic ON learning_runs (topic_id);
CREATE INDEX IF NOT EXISTS idx_lr_status ON learning_runs (status);

CREATE TABLE IF NOT EXISTS optimization_recommendations (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    learning_run_id         INTEGER NOT NULL REFERENCES learning_runs(id),
    topic_id                INTEGER NOT NULL REFERENCES topics(id),
    publication_id          INTEGER,

    -- What this recommendation targets
    domain                  TEXT NOT NULL
                                CHECK (domain IN (
                                    'topics','research','scripts','narration',
                                    'captions','scenes','media','publishing','analytics'
                                )),
    subsystem               TEXT NOT NULL,
    measure                 TEXT NOT NULL,

    -- Human-readable content
    title                   TEXT NOT NULL,
    explanation             TEXT NOT NULL,
    expected_improvement    TEXT NOT NULL,

    -- Evidence and confidence
    evidence_json           TEXT NOT NULL DEFAULT '[]',
    evidence_classification TEXT NOT NULL DEFAULT 'observational'
                                CHECK (evidence_classification IN (
                                    'observational','controlled_experiment',
                                    'human_preference','operational_failure','mixed'
                                )),
    recommendation_strength TEXT NOT NULL DEFAULT 'exploratory'
                                CHECK (recommendation_strength IN ('exploratory','actionable')),
    confidence              TEXT NOT NULL
                                CHECK (confidence IN ('low','medium','high')),
    confidence_score        REAL NOT NULL
                                CHECK (confidence_score >= 0.0 AND confidence_score <= 1.0),

    -- Attribution back to upstream subsystem
    affected_subsystem      TEXT NOT NULL DEFAULT '',
    subsystem_entity_type   TEXT NOT NULL DEFAULT '',
    subsystem_entity_id     INTEGER,

    -- Experiment context (nullable)
    experiment_id           TEXT,

    -- Provenance
    engine_version          TEXT NOT NULL,
    schema_version          TEXT NOT NULL,
    input_hash              TEXT NOT NULL,

    -- Lifecycle (append-only: superseded replaces deleted)
    status                  TEXT NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending','accepted','rejected','superseded')),
    superseded_at           TEXT,
    superseded_by_id        INTEGER REFERENCES optimization_recommendations(id),

    created_at              TEXT NOT NULL
                                DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_or_learning_run ON optimization_recommendations (learning_run_id);
CREATE INDEX IF NOT EXISTS idx_or_topic ON optimization_recommendations (topic_id);
CREATE INDEX IF NOT EXISTS idx_or_domain ON optimization_recommendations (domain);
CREATE INDEX IF NOT EXISTS idx_or_status ON optimization_recommendations (status);

CREATE TABLE IF NOT EXISTS recommendation_review_events (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    recommendation_id       INTEGER NOT NULL
                                REFERENCES optimization_recommendations(id),
    topic_id                INTEGER NOT NULL REFERENCES topics(id),

    event_type              TEXT NOT NULL
                                CHECK (event_type IN ('accepted','rejected','noted')),
    reviewer                TEXT NOT NULL DEFAULT '',
    notes                   TEXT NOT NULL DEFAULT '',
    expected_outcome        TEXT NOT NULL DEFAULT '',

    input_hash              TEXT NOT NULL,
    created_at              TEXT NOT NULL
                                DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_rre_recommendation
    ON recommendation_review_events (recommendation_id);
CREATE INDEX IF NOT EXISTS idx_rre_topic ON recommendation_review_events (topic_id);

CREATE TABLE IF NOT EXISTS learning_run_generator_results (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    learning_run_id         INTEGER NOT NULL REFERENCES learning_runs(id),
    generator_name          TEXT NOT NULL
                                CHECK (generator_name IN (
                                    'ctr','retention','engagement',
                                    'watch_time','subscribers','shares'
                                )),
    status                  TEXT NOT NULL
                                CHECK (status IN ('succeeded','failed')),
    recommendation_count    INTEGER NOT NULL DEFAULT 0,
    error_message           TEXT,
    created_at              TEXT NOT NULL
                                DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_lrgr_run ON learning_run_generator_results (learning_run_id);
"""

# Phase 12 DDL — Media Operations Control Plane (22 cp_ tables).
# All table names carry the cp_ prefix to avoid conflicts with the existing
# Phase 3 `channels` intelligence table.
# Identity hierarchy: cp_organizations → cp_workspaces → cp_channels →
#   cp_platform_accounts (with cp_credential_profiles, cp_publishing_profiles,
#   cp_analytics_identities as satellite identity concepts).
_DDL_V18_CONTROL_PLANE = """
CREATE TABLE IF NOT EXISTS cp_organizations (
    id          TEXT    PRIMARY KEY,
    name        TEXT    NOT NULL,
    slug        TEXT    NOT NULL UNIQUE,
    owner_email TEXT,
    actor       TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS cp_workspaces (
    id              TEXT    PRIMARY KEY,
    name            TEXT    NOT NULL,
    slug            TEXT    NOT NULL UNIQUE,
    status          TEXT    NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active', 'suspended', 'archived')),
    actor           TEXT    NOT NULL,
    metadata_json   TEXT,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL,
    organization_id TEXT    REFERENCES cp_organizations(id)
);

CREATE TABLE IF NOT EXISTS cp_channels (
    id              TEXT    PRIMARY KEY,
    workspace_id    TEXT    NOT NULL REFERENCES cp_workspaces(id),
    name            TEXT    NOT NULL,
    slug            TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active', 'paused', 'archived')),
    actor           TEXT    NOT NULL,
    description     TEXT,
    metadata_json   TEXT,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL,
    UNIQUE (workspace_id, slug)
);

CREATE TABLE IF NOT EXISTS cp_platforms (
    id                  TEXT    PRIMARY KEY,
    platform_key        TEXT    NOT NULL UNIQUE,
    display_name        TEXT    NOT NULL,
    is_active           INTEGER NOT NULL DEFAULT 1,
    capabilities_json   TEXT,
    created_at          TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS cp_credential_profiles (
    id                  TEXT    PRIMARY KEY,
    workspace_id        TEXT    NOT NULL REFERENCES cp_workspaces(id),
    display_name        TEXT    NOT NULL,
    credential_type     TEXT    NOT NULL
                                CHECK (credential_type IN ('oauth2', 'api_key', 'service_account')),
    status              TEXT    NOT NULL DEFAULT 'active'
                                CHECK (status IN (
                                    'active', 'expiring', 'expired', 'revoked', 'pending_validation'
                                )),
    external_ref        TEXT    NOT NULL,
    actor               TEXT    NOT NULL,
    expires_at          TEXT,
    created_at          TEXT    NOT NULL,
    updated_at          TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS cp_platform_accounts (
    id                      TEXT    PRIMARY KEY,
    channel_id              TEXT    NOT NULL REFERENCES cp_channels(id),
    platform_id             TEXT    NOT NULL REFERENCES cp_platforms(id),
    platform_key            TEXT    NOT NULL,
    external_account_id     TEXT    NOT NULL,
    display_name            TEXT    NOT NULL,
    status                  TEXT    NOT NULL DEFAULT 'connected'
                                    CHECK (status IN (
                                        'connected', 'disconnected', 'credential_invalid',
                                        'credential_expiring', 'quota_limited', 'paused'
                                    )),
    credential_profile_id   TEXT    REFERENCES cp_credential_profiles(id),
    actor                   TEXT    NOT NULL,
    metadata_json           TEXT,
    created_at              TEXT    NOT NULL,
    updated_at              TEXT    NOT NULL,
    UNIQUE (channel_id, platform_key, external_account_id)
);

CREATE TABLE IF NOT EXISTS cp_publishing_profiles (
    id                      TEXT    PRIMARY KEY,
    platform_account_id     TEXT    NOT NULL REFERENCES cp_platform_accounts(id),
    config_json             TEXT    NOT NULL DEFAULT '{}',
    is_active               INTEGER NOT NULL DEFAULT 1,
    actor                   TEXT    NOT NULL,
    created_at              TEXT    NOT NULL,
    updated_at              TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cp_pubprofile_account
    ON cp_publishing_profiles (platform_account_id, is_active);

CREATE TABLE IF NOT EXISTS cp_analytics_identities (
    id                      TEXT    PRIMARY KEY,
    platform_account_id     TEXT    NOT NULL REFERENCES cp_platform_accounts(id),
    analytics_provider_key  TEXT    NOT NULL,
    analytics_account_id    TEXT    NOT NULL,
    metadata_json           TEXT,
    created_at              TEXT    NOT NULL,
    UNIQUE (platform_account_id, analytics_provider_key)
);
CREATE INDEX IF NOT EXISTS idx_cp_analytics_account
    ON cp_analytics_identities (platform_account_id);

CREATE TABLE IF NOT EXISTS cp_automation_policies (
    id                      TEXT    PRIMARY KEY,
    scope                   TEXT    NOT NULL
                                    CHECK (scope IN ('workspace', 'channel', 'platform_account')),
    scope_id                TEXT    NOT NULL,
    automation_level        TEXT    NOT NULL
                                    CHECK (automation_level IN (
                                        'manual', 'supervised', 'autonomous'
                                    )),
    allowed_actions_json    TEXT    NOT NULL DEFAULT '[]',
    actor                   TEXT    NOT NULL,
    created_at              TEXT    NOT NULL,
    is_active               INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_cp_policy_scope
    ON cp_automation_policies (scope, scope_id, is_active);

CREATE TABLE IF NOT EXISTS cp_strategy_profiles (
    id              TEXT    PRIMARY KEY,
    channel_id      TEXT    NOT NULL REFERENCES cp_channels(id),
    version         INTEGER NOT NULL,
    config_json     TEXT    NOT NULL DEFAULT '{}',
    actor           TEXT    NOT NULL,
    created_at      TEXT    NOT NULL,
    is_active       INTEGER NOT NULL DEFAULT 1,
    UNIQUE (channel_id, version)
);
CREATE INDEX IF NOT EXISTS idx_cp_strategy_channel ON cp_strategy_profiles (channel_id, is_active);

CREATE TABLE IF NOT EXISTS cp_events (
    id                      TEXT    PRIMARY KEY,
    event_type              TEXT    NOT NULL,
    workspace_id            TEXT    NOT NULL REFERENCES cp_workspaces(id),
    actor                   TEXT    NOT NULL,
    payload_json            TEXT    NOT NULL DEFAULT '{}',
    correlation_id          TEXT,
    causation_id            TEXT,
    created_at              TEXT    NOT NULL,
    channel_id              TEXT    REFERENCES cp_channels(id),
    platform_account_id     TEXT    REFERENCES cp_platform_accounts(id),
    source_engine           TEXT,
    source_entity_id        TEXT,
    schema_version          TEXT    NOT NULL DEFAULT '1',
    experiment_id           TEXT
);
CREATE INDEX IF NOT EXISTS idx_cp_events_workspace ON cp_events (workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cp_events_type ON cp_events (event_type, workspace_id);

CREATE TABLE IF NOT EXISTS cp_event_processing (
    id              TEXT    PRIMARY KEY,
    event_id        TEXT    NOT NULL REFERENCES cp_events(id),
    handler_key     TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'pending'
                            CHECK (status IN (
                                'pending', 'processing', 'completed', 'failed', 'dead_lettered'
                            )),
    attempt_count   INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT,
    completed_at    TEXT,
    error_message   TEXT,
    created_at      TEXT    NOT NULL,
    UNIQUE (event_id, handler_key)
);
CREATE INDEX IF NOT EXISTS idx_cp_ep_pending ON cp_event_processing (status, created_at ASC);

CREATE TABLE IF NOT EXISTS cp_workflows (
    id                  TEXT    PRIMARY KEY,
    workspace_id        TEXT    NOT NULL REFERENCES cp_workspaces(id),
    name                TEXT    NOT NULL,
    trigger_event_type  TEXT    NOT NULL,
    conditions_json     TEXT    NOT NULL DEFAULT '[]',
    actions_json        TEXT    NOT NULL DEFAULT '[]',
    status              TEXT    NOT NULL DEFAULT 'draft'
                                CHECK (status IN ('draft', 'active', 'paused', 'archived')),
    actor               TEXT    NOT NULL,
    created_at          TEXT    NOT NULL,
    updated_at          TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cp_wf_workspace ON cp_workflows (workspace_id, status);
CREATE INDEX IF NOT EXISTS idx_cp_wf_trigger ON cp_workflows (trigger_event_type, status);

CREATE TABLE IF NOT EXISTS cp_workflow_runs (
    id                  TEXT    PRIMARY KEY,
    workflow_id         TEXT    NOT NULL REFERENCES cp_workflows(id),
    trigger_event_id    TEXT    NOT NULL REFERENCES cp_events(id),
    status              TEXT    NOT NULL DEFAULT 'running'
                                CHECK (status IN ('running', 'completed', 'failed')),
    result_json         TEXT,
    error_message       TEXT,
    started_at          TEXT    NOT NULL,
    completed_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_cp_wfrun_workflow ON cp_workflow_runs (workflow_id, started_at DESC);

CREATE TABLE IF NOT EXISTS cp_experiments (
    id                      TEXT    PRIMARY KEY,
    workspace_id            TEXT    NOT NULL REFERENCES cp_workspaces(id),
    channel_id              TEXT    NOT NULL REFERENCES cp_channels(id),
    name                    TEXT    NOT NULL,
    hypothesis              TEXT    NOT NULL,
    status                  TEXT    NOT NULL DEFAULT 'draft'
                                    CHECK (status IN (
                                        'draft', 'active', 'paused', 'concluded', 'cancelled'
                                    )),
    primary_metric          TEXT    NOT NULL,
    actor                   TEXT    NOT NULL,
    created_at              TEXT    NOT NULL,
    updated_at              TEXT    NOT NULL,
    activated_at            TEXT,
    concluded_at            TEXT,
    secondary_metrics_json  TEXT,
    guardrails_json         TEXT,
    min_sample_size         INTEGER
);
CREATE INDEX IF NOT EXISTS idx_cp_exp_workspace ON cp_experiments (workspace_id, status);
CREATE INDEX IF NOT EXISTS idx_cp_exp_channel ON cp_experiments (channel_id);

CREATE TABLE IF NOT EXISTS cp_experiment_variants (
    id              TEXT    PRIMARY KEY,
    experiment_id   TEXT    NOT NULL REFERENCES cp_experiments(id),
    name            TEXT    NOT NULL,
    variant_type    TEXT    NOT NULL CHECK (variant_type IN ('control', 'treatment')),
    description     TEXT,
    config_json     TEXT,
    created_at      TEXT    NOT NULL,
    UNIQUE (experiment_id, name)
);

CREATE TABLE IF NOT EXISTS cp_experiment_assignments (
    id              TEXT    PRIMARY KEY,
    experiment_id   TEXT    NOT NULL REFERENCES cp_experiments(id),
    variant_id      TEXT    NOT NULL REFERENCES cp_experiment_variants(id),
    unit_id         TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active', 'excluded')),
    assigned_at     TEXT    NOT NULL,
    UNIQUE (experiment_id, unit_id)
);
CREATE INDEX IF NOT EXISTS idx_cp_assign_exp ON cp_experiment_assignments (experiment_id, unit_id);

CREATE TABLE IF NOT EXISTS cp_operation_executions (
    id                      TEXT    PRIMARY KEY,
    operation_type          TEXT    NOT NULL,
    workspace_id            TEXT    NOT NULL REFERENCES cp_workspaces(id),
    channel_id              TEXT    REFERENCES cp_channels(id),
    platform_account_id     TEXT    REFERENCES cp_platform_accounts(id),
    idempotency_key         TEXT    NOT NULL UNIQUE,
    status                  TEXT    NOT NULL DEFAULT 'pending'
                                    CHECK (status IN (
                                        'pending', 'running', 'completed', 'failed', 'superseded'
                                    )),
    actor                   TEXT    NOT NULL,
    correlation_id          TEXT,
    source_event_id         TEXT    REFERENCES cp_events(id),
    input_json              TEXT,
    output_json             TEXT,
    error_message           TEXT,
    created_at              TEXT    NOT NULL,
    updated_at              TEXT    NOT NULL,
    engine                  TEXT,
    attempt_count           INTEGER NOT NULL DEFAULT 1,
    target_entity_id        TEXT,
    target_entity_type      TEXT,
    error_category          TEXT
);
CREATE INDEX IF NOT EXISTS idx_cp_op_workspace ON cp_operation_executions (workspace_id, status);
CREATE INDEX IF NOT EXISTS idx_cp_op_idem ON cp_operation_executions (idempotency_key);

CREATE TABLE IF NOT EXISTS cp_cost_records (
    id                          TEXT    PRIMARY KEY,
    workspace_id                TEXT    NOT NULL REFERENCES cp_workspaces(id),
    channel_id                  TEXT    REFERENCES cp_channels(id),
    platform_account_id         TEXT    REFERENCES cp_platform_accounts(id),
    operation_execution_id      TEXT    REFERENCES cp_operation_executions(id),
    provider_key                TEXT    NOT NULL,
    cost_unit                   TEXT    NOT NULL
                                        CHECK (cost_unit IN (
                                            'usd', 'tokens', 'characters', 'requests'
                                        )),
    quantity                    REAL    NOT NULL,
    usd_equivalent              REAL    NOT NULL,
    description                 TEXT,
    recorded_at                 TEXT    NOT NULL,
    engine                      TEXT,
    experiment_id               TEXT,
    entity_id                   TEXT,
    entity_type                 TEXT
);
CREATE INDEX IF NOT EXISTS idx_cp_cost_workspace
    ON cp_cost_records (workspace_id, recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_cp_cost_channel ON cp_cost_records (channel_id, recorded_at DESC);

CREATE TABLE IF NOT EXISTS cp_budget_policies (
    id                  TEXT    PRIMARY KEY,
    scope               TEXT    NOT NULL
                                CHECK (scope IN ('workspace', 'channel', 'platform_account')),
    scope_id            TEXT    NOT NULL,
    period              TEXT    NOT NULL CHECK (period IN ('daily', 'weekly', 'monthly')),
    limit_usd           REAL    NOT NULL,
    warning_threshold   REAL    NOT NULL DEFAULT 0.8,
    on_exceed_action    TEXT    NOT NULL DEFAULT 'warn'
                                CHECK (on_exceed_action IN ('warn', 'pause', 'block')),
    actor               TEXT    NOT NULL,
    created_at          TEXT    NOT NULL,
    is_active           INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_cp_budget_scope ON cp_budget_policies (scope, scope_id, is_active);

CREATE TABLE IF NOT EXISTS cp_health_records (
    id              TEXT    PRIMARY KEY,
    entity_type     TEXT    NOT NULL
                            CHECK (entity_type IN (
                                'workspace', 'channel', 'platform_account', 'provider',
                                'engine', 'workflow', 'credential_profile'
                            )),
    entity_id       TEXT    NOT NULL,
    status          TEXT    NOT NULL
                            CHECK (status IN (
                                'healthy', 'degraded', 'unavailable', 'credential_expired',
                                'quota_limited', 'paused', 'failed'
                            )),
    detail          TEXT,
    recorded_by     TEXT    NOT NULL,
    recorded_at     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cp_health_entity
    ON cp_health_records (entity_type, entity_id, recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_cp_health_status ON cp_health_records (status, recorded_at DESC);

CREATE TABLE IF NOT EXISTS cp_provider_registry (
    id                  TEXT    PRIMARY KEY,
    provider_key        TEXT    NOT NULL UNIQUE,
    domain              TEXT    NOT NULL
                                CHECK (domain IN (
                                    'ai', 'tts', 'publishing', 'analytics',
                                    'asset', 'storage', 'notification'
                                )),
    display_name        TEXT    NOT NULL,
    status              TEXT    NOT NULL DEFAULT 'active'
                                CHECK (status IN ('active', 'degraded', 'inactive')),
    capabilities_json   TEXT,
    registered_at       TEXT    NOT NULL,
    updated_at          TEXT    NOT NULL,
    quota_json          TEXT,
    cost_metadata_json  TEXT,
    version_info        TEXT
);
CREATE INDEX IF NOT EXISTS idx_cp_provider_domain ON cp_provider_registry (domain, status);
"""

# Phase 13 DDL — Application layer: pipeline executions, stage log, schedule definitions.
_DDL_V19_APPLICATION = """
CREATE TABLE IF NOT EXISTS app_pipeline_executions (
    id                      TEXT    PRIMARY KEY,
    workspace_id            TEXT    NOT NULL REFERENCES cp_workspaces(id),
    channel_id              TEXT    REFERENCES cp_channels(id),
    platform_account_id     TEXT    REFERENCES cp_platform_accounts(id),
    topic_id                INTEGER REFERENCES topics(id),
    correlation_id          TEXT    NOT NULL UNIQUE,
    idempotency_key         TEXT    NOT NULL UNIQUE,
    status                  TEXT    NOT NULL DEFAULT 'pending'
                                    CHECK (status IN (
                                        'pending', 'running', 'waiting_for_review',
                                        'blocked', 'completed', 'failed',
                                        'cancelled', 'paused'
                                    )),
    current_stage           TEXT,
    end_stage               TEXT    NOT NULL DEFAULT 'learning',
    experiment_id           TEXT,
    actor                   TEXT    NOT NULL,
    policy_snapshot_json    TEXT,
    error_message           TEXT,
    blocked_reason          TEXT,
    created_at              TEXT    NOT NULL,
    updated_at              TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_app_pipeline_workspace
    ON app_pipeline_executions (workspace_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_app_pipeline_channel
    ON app_pipeline_executions (channel_id, status);
CREATE INDEX IF NOT EXISTS idx_app_pipeline_corr
    ON app_pipeline_executions (correlation_id);

CREATE TABLE IF NOT EXISTS app_pipeline_stage_log (
    id              TEXT    PRIMARY KEY,
    pipeline_id     TEXT    NOT NULL REFERENCES app_pipeline_executions(id),
    stage           TEXT    NOT NULL,
    attempt_number  INTEGER NOT NULL DEFAULT 1,
    status          TEXT    NOT NULL DEFAULT 'pending'
                            CHECK (status IN (
                                'pending', 'running', 'completed', 'failed',
                                'skipped', 'waiting_for_review', 'blocked'
                            )),
    artifact_id     TEXT,
    artifact_type   TEXT,
    error_message   TEXT,
    duration_ms     INTEGER,
    started_at      TEXT,
    completed_at    TEXT,
    created_at      TEXT    NOT NULL,
    UNIQUE(pipeline_id, stage, attempt_number)
);
CREATE INDEX IF NOT EXISTS idx_app_stage_pipeline
    ON app_pipeline_stage_log (pipeline_id, stage);

CREATE TABLE IF NOT EXISTS app_schedule_definitions (
    id                      TEXT    PRIMARY KEY,
    workspace_id            TEXT    NOT NULL REFERENCES cp_workspaces(id),
    channel_id              TEXT    REFERENCES cp_channels(id),
    name                    TEXT    NOT NULL,
    operation_type          TEXT    NOT NULL,
    schedule_type           TEXT    NOT NULL
                                    CHECK (schedule_type IN (
                                        'cron', 'interval', 'once', 'after_event'
                                    )),
    schedule_config_json    TEXT    NOT NULL DEFAULT '{}',
    timezone                TEXT    NOT NULL DEFAULT 'UTC',
    is_active               INTEGER NOT NULL DEFAULT 1,
    last_run_at             TEXT,
    next_run_at             TEXT,
    actor                   TEXT    NOT NULL,
    created_at              TEXT    NOT NULL,
    updated_at              TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_app_schedule_workspace
    ON app_schedule_definitions (workspace_id, is_active, next_run_at);
"""


_DDL_V20_AUTH_STORAGE = """
CREATE TABLE IF NOT EXISTS auth_users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    email           TEXT    NOT NULL UNIQUE,
    display_name    TEXT    NOT NULL DEFAULT '',
    password_hash   TEXT    NOT NULL,
    is_active       INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    updated_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    last_login_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_auth_users_email ON auth_users (email);
CREATE INDEX IF NOT EXISTS idx_auth_users_active ON auth_users (is_active);

CREATE TABLE IF NOT EXISTS auth_refresh_tokens (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
    token_hash      TEXT    NOT NULL UNIQUE,
    issued_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    expires_at      TEXT    NOT NULL DEFAULT '',
    revoked_at      TEXT,
    last_used_at    TEXT,
    device_hint     TEXT    NOT NULL DEFAULT '',
    ip_hint         TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_art_user ON auth_refresh_tokens (user_id, expires_at);
CREATE INDEX IF NOT EXISTS idx_art_hash ON auth_refresh_tokens (token_hash);

CREATE TABLE IF NOT EXISTS auth_workspace_roles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
    workspace_id    TEXT    NOT NULL,
    role            TEXT    NOT NULL CHECK (role IN (
                                'owner', 'admin', 'operator', 'reviewer', 'analyst')),
    granted_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    granted_by      TEXT    NOT NULL DEFAULT '',
    UNIQUE (user_id, workspace_id)
);

CREATE INDEX IF NOT EXISTS idx_awr_user ON auth_workspace_roles (user_id);
CREATE INDEX IF NOT EXISTS idx_awr_workspace ON auth_workspace_roles (workspace_id, role);

CREATE TABLE IF NOT EXISTS obj_storage_objects (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id    TEXT    NOT NULL,
    channel_id      TEXT,
    storage_backend TEXT    NOT NULL DEFAULT 'local'
                            CHECK (storage_backend IN ('local', 's3')),
    bucket          TEXT    NOT NULL DEFAULT '',
    object_key      TEXT    NOT NULL,
    sha256          TEXT    NOT NULL,
    byte_size       INTEGER NOT NULL,
    content_type    TEXT    NOT NULL DEFAULT 'application/octet-stream',
    source_entity_type  TEXT NOT NULL DEFAULT '',
    source_entity_id    TEXT NOT NULL DEFAULT '',
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    deleted_at      TEXT,
    UNIQUE (storage_backend, bucket, object_key)
);

CREATE INDEX IF NOT EXISTS idx_oso_workspace ON obj_storage_objects (workspace_id);
CREATE INDEX IF NOT EXISTS idx_oso_sha256 ON obj_storage_objects (sha256);
CREATE INDEX IF NOT EXISTS idx_oso_source
    ON obj_storage_objects (source_entity_type, source_entity_id);
"""


# Phase 21 DDL — workspace scoping for topics (index only; column added conditionally).
_DDL_V21_TOPIC_WORKSPACE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_topics_workspace_id ON topics (workspace_id);
"""

# Phase A-retention DDL — audience retention curve storage with scene attribution.
# analytics_retention_points is a dedicated time-series table, not a row in
# analytics_metrics, because each retention ingest produces O(100) dimensioned
# points (one per elapsedVideoTimeRatio bucket) rather than a single scalar.
_DDL_V22_ANALYTICS_RETENTION = """
CREATE TABLE IF NOT EXISTS analytics_retention_points (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Link to the scalar snapshot that anchors this retention fetch
    snapshot_id         INTEGER NOT NULL REFERENCES analytics_snapshots(id),
    publication_id      INTEGER NOT NULL,
    scene_manifest_id   INTEGER NOT NULL,

    -- Retention curve dimension (YouTube: elapsedVideoTimeRatio, 0.0–1.0)
    elapsed_ratio       REAL NOT NULL,
    elapsed_ms          INTEGER,    -- elapsed_ratio × video_duration_ms; NULL if duration unknown
    elapsed_seconds     REAL,       -- elapsed_ms / 1000.0

    -- Retention metrics
    audience_watch_ratio    REAL NOT NULL,
    relative_retention      REAL,   -- NULL when not returned by API

    -- Scene attribution (derived from scene_manifest_scenes + production_segments)
    scene_index         INTEGER,    -- NULL if no scene covers this elapsed_ms
    section_type        TEXT,       -- hook/body/conclusion/cta; NULL when scene_index IS NULL

    -- Coverage window (mirrors the parent snapshot)
    period_start        TEXT,
    period_end          TEXT,

    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_arp_snapshot
    ON analytics_retention_points (snapshot_id);
CREATE INDEX IF NOT EXISTS idx_arp_publication
    ON analytics_retention_points (publication_id, elapsed_ratio);
"""


def _apply_v22_analytics_retention(conn: sqlite3.Connection) -> None:
    """Create analytics_retention_points if it doesn't exist yet."""
    conn.executescript(_DDL_V22_ANALYTICS_RETENTION)


# Phase 23 DDL — explicit operational ownership on publications.
# workspace_id is the primary authorization boundary; channel_id and
# platform_account_id provide operational lineage.  All three are nullable
# so historical rows (fake provider, pre-v23) receive NULL without a backfill.
# Real YouTube publications persist all three at creation time.
_DDL_V23_PUBLICATION_OWNERSHIP_INDEX = """
CREATE INDEX IF NOT EXISTS idx_publications_workspace_id
    ON publications (workspace_id);
"""


def _apply_v23_publication_ownership(conn: sqlite3.Connection) -> None:
    """Add workspace_id, channel_id, platform_account_id to publications if absent."""
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='publications'"
    ).fetchone()
    if not table_exists:
        return
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(publications)").fetchall()}
    if "workspace_id" not in existing_cols:
        conn.execute(
            "ALTER TABLE publications ADD COLUMN workspace_id TEXT REFERENCES cp_workspaces(id)"
        )
    if "channel_id" not in existing_cols:
        conn.execute(
            "ALTER TABLE publications ADD COLUMN channel_id TEXT REFERENCES cp_channels(id)"
        )
    if "platform_account_id" not in existing_cols:
        conn.execute(
            "ALTER TABLE publications"
            " ADD COLUMN platform_account_id TEXT REFERENCES cp_platform_accounts(id)"
        )
    conn.executescript(_DDL_V23_PUBLICATION_OWNERSHIP_INDEX)


def _apply_v21_topic_workspace(conn: sqlite3.Connection) -> None:
    """Add workspace_id column to topics if the table exists and lacks the column."""
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='topics'"
    ).fetchone()
    if not table_exists:
        return
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(topics)").fetchall()}
    if "workspace_id" not in existing_cols:
        conn.execute("ALTER TABLE topics ADD COLUMN workspace_id TEXT")
    conn.executescript(_DDL_V21_TOPIC_WORKSPACE_INDEX)


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
        conn.executescript(_DDL_V12_CAPTIONS)
        conn.executescript(_DDL_V13_SCENES)
        conn.executescript(_DDL_V14_RENDERS)
        conn.executescript(_DDL_V15_PUBLISHING)
        conn.executescript(_DDL_V16_ANALYTICS)
        conn.executescript(_DDL_V17_LEARNING)
        conn.executescript(_DDL_V18_CONTROL_PLANE)
        conn.executescript(_DDL_V19_APPLICATION)
        conn.executescript(_DDL_V20_AUTH_STORAGE)
        _apply_v21_topic_workspace(conn)
        _apply_v22_analytics_retention(conn)
        _apply_v23_publication_ownership(conn)
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
        conn.executescript(_DDL_V12_CAPTIONS)
        conn.executescript(_DDL_V13_SCENES)
        conn.executescript(_DDL_V14_RENDERS)
        conn.executescript(_DDL_V15_PUBLISHING)
        conn.executescript(_DDL_V16_ANALYTICS)
        conn.executescript(_DDL_V17_LEARNING)
        conn.executescript(_DDL_V18_CONTROL_PLANE)
        conn.executescript(_DDL_V19_APPLICATION)
        conn.executescript(_DDL_V20_AUTH_STORAGE)
        _apply_v21_topic_workspace(conn)
        _apply_v22_analytics_retention(conn)
        _apply_v23_publication_ownership(conn)
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
        conn.executescript(_DDL_V12_CAPTIONS)
        conn.executescript(_DDL_V13_SCENES)
        conn.executescript(_DDL_V14_RENDERS)
        conn.executescript(_DDL_V15_PUBLISHING)
        conn.executescript(_DDL_V16_ANALYTICS)
        conn.executescript(_DDL_V17_LEARNING)
        conn.executescript(_DDL_V18_CONTROL_PLANE)
        conn.executescript(_DDL_V19_APPLICATION)
        conn.executescript(_DDL_V20_AUTH_STORAGE)
        _apply_v21_topic_workspace(conn)
        _apply_v22_analytics_retention(conn)
        _apply_v23_publication_ownership(conn)
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
        conn.executescript(_DDL_V12_CAPTIONS)
        conn.executescript(_DDL_V13_SCENES)
        conn.executescript(_DDL_V14_RENDERS)
        conn.executescript(_DDL_V15_PUBLISHING)
        conn.executescript(_DDL_V16_ANALYTICS)
        conn.executescript(_DDL_V17_LEARNING)
        conn.executescript(_DDL_V18_CONTROL_PLANE)
        conn.executescript(_DDL_V19_APPLICATION)
        conn.executescript(_DDL_V20_AUTH_STORAGE)
        _apply_v21_topic_workspace(conn)
        _apply_v22_analytics_retention(conn)
        _apply_v23_publication_ownership(conn)
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
        conn.executescript(_DDL_V12_CAPTIONS)
        conn.executescript(_DDL_V13_SCENES)
        conn.executescript(_DDL_V14_RENDERS)
        conn.executescript(_DDL_V15_PUBLISHING)
        conn.executescript(_DDL_V16_ANALYTICS)
        conn.executescript(_DDL_V17_LEARNING)
        conn.executescript(_DDL_V18_CONTROL_PLANE)
        conn.executescript(_DDL_V19_APPLICATION)
        conn.executescript(_DDL_V20_AUTH_STORAGE)
        _apply_v21_topic_workspace(conn)
        _apply_v22_analytics_retention(conn)
        _apply_v23_publication_ownership(conn)
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
        conn.executescript(_DDL_V12_CAPTIONS)
        conn.executescript(_DDL_V13_SCENES)
        conn.executescript(_DDL_V14_RENDERS)
        conn.executescript(_DDL_V15_PUBLISHING)
        conn.executescript(_DDL_V16_ANALYTICS)
        conn.executescript(_DDL_V17_LEARNING)
        conn.executescript(_DDL_V18_CONTROL_PLANE)
        conn.executescript(_DDL_V19_APPLICATION)
        conn.executescript(_DDL_V20_AUTH_STORAGE)
        _apply_v21_topic_workspace(conn)
        _apply_v22_analytics_retention(conn)
        _apply_v23_publication_ownership(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 6:
        logger.info("Migrating schema from version 6 to %d", SCHEMA_VERSION)
        conn.executescript(_DDL_V7_RESEARCH)
        conn.executescript(_DDL_V8_CLAIMS)
        conn.executescript(_DDL_V9_SCRIPTS)
        conn.executescript(_DDL_V10_PRODUCTION)
        conn.executescript(_DDL_V11_NARRATION)
        conn.executescript(_DDL_V12_CAPTIONS)
        conn.executescript(_DDL_V13_SCENES)
        conn.executescript(_DDL_V14_RENDERS)
        conn.executescript(_DDL_V15_PUBLISHING)
        conn.executescript(_DDL_V16_ANALYTICS)
        conn.executescript(_DDL_V17_LEARNING)
        conn.executescript(_DDL_V18_CONTROL_PLANE)
        conn.executescript(_DDL_V19_APPLICATION)
        conn.executescript(_DDL_V20_AUTH_STORAGE)
        _apply_v21_topic_workspace(conn)
        _apply_v22_analytics_retention(conn)
        _apply_v23_publication_ownership(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 7:
        logger.info("Migrating schema from version 7 to %d", SCHEMA_VERSION)
        conn.executescript(_DDL_V8_CLAIMS)
        conn.executescript(_DDL_V9_SCRIPTS)
        conn.executescript(_DDL_V10_PRODUCTION)
        conn.executescript(_DDL_V11_NARRATION)
        conn.executescript(_DDL_V12_CAPTIONS)
        conn.executescript(_DDL_V13_SCENES)
        conn.executescript(_DDL_V14_RENDERS)
        conn.executescript(_DDL_V15_PUBLISHING)
        conn.executescript(_DDL_V16_ANALYTICS)
        conn.executescript(_DDL_V17_LEARNING)
        conn.executescript(_DDL_V18_CONTROL_PLANE)
        conn.executescript(_DDL_V19_APPLICATION)
        conn.executescript(_DDL_V20_AUTH_STORAGE)
        _apply_v21_topic_workspace(conn)
        _apply_v22_analytics_retention(conn)
        _apply_v23_publication_ownership(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 8:
        logger.info("Migrating schema from version 8 to %d", SCHEMA_VERSION)
        conn.executescript(_DDL_V9_SCRIPTS)
        conn.executescript(_DDL_V10_PRODUCTION)
        conn.executescript(_DDL_V11_NARRATION)
        conn.executescript(_DDL_V12_CAPTIONS)
        conn.executescript(_DDL_V13_SCENES)
        conn.executescript(_DDL_V14_RENDERS)
        conn.executescript(_DDL_V15_PUBLISHING)
        conn.executescript(_DDL_V16_ANALYTICS)
        conn.executescript(_DDL_V17_LEARNING)
        conn.executescript(_DDL_V18_CONTROL_PLANE)
        conn.executescript(_DDL_V19_APPLICATION)
        conn.executescript(_DDL_V20_AUTH_STORAGE)
        _apply_v21_topic_workspace(conn)
        _apply_v22_analytics_retention(conn)
        _apply_v23_publication_ownership(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 9:
        logger.info("Migrating schema from version 9 to %d", SCHEMA_VERSION)
        conn.executescript(_DDL_V10_PRODUCTION)
        conn.executescript(_DDL_V11_NARRATION)
        conn.executescript(_DDL_V12_CAPTIONS)
        conn.executescript(_DDL_V13_SCENES)
        conn.executescript(_DDL_V14_RENDERS)
        conn.executescript(_DDL_V15_PUBLISHING)
        conn.executescript(_DDL_V16_ANALYTICS)
        conn.executescript(_DDL_V17_LEARNING)
        conn.executescript(_DDL_V18_CONTROL_PLANE)
        conn.executescript(_DDL_V19_APPLICATION)
        conn.executescript(_DDL_V20_AUTH_STORAGE)
        _apply_v21_topic_workspace(conn)
        _apply_v22_analytics_retention(conn)
        _apply_v23_publication_ownership(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 10:
        logger.info("Migrating schema from version 10 to %d", SCHEMA_VERSION)
        conn.executescript(_DDL_V11_NARRATION)
        conn.executescript(_DDL_V12_CAPTIONS)
        conn.executescript(_DDL_V13_SCENES)
        conn.executescript(_DDL_V14_RENDERS)
        conn.executescript(_DDL_V15_PUBLISHING)
        conn.executescript(_DDL_V16_ANALYTICS)
        conn.executescript(_DDL_V17_LEARNING)
        conn.executescript(_DDL_V18_CONTROL_PLANE)
        conn.executescript(_DDL_V19_APPLICATION)
        conn.executescript(_DDL_V20_AUTH_STORAGE)
        _apply_v21_topic_workspace(conn)
        _apply_v22_analytics_retention(conn)
        _apply_v23_publication_ownership(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 11:
        logger.info("Migrating schema from version 11 to %d", SCHEMA_VERSION)
        conn.executescript(_DDL_V12_CAPTIONS)
        conn.executescript(_DDL_V13_SCENES)
        conn.executescript(_DDL_V14_RENDERS)
        conn.executescript(_DDL_V15_PUBLISHING)
        conn.executescript(_DDL_V16_ANALYTICS)
        conn.executescript(_DDL_V17_LEARNING)
        conn.executescript(_DDL_V18_CONTROL_PLANE)
        conn.executescript(_DDL_V19_APPLICATION)
        conn.executescript(_DDL_V20_AUTH_STORAGE)
        _apply_v21_topic_workspace(conn)
        _apply_v22_analytics_retention(conn)
        _apply_v23_publication_ownership(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 12:
        logger.info("Migrating schema from version 12 to %d", SCHEMA_VERSION)
        conn.executescript(_DDL_V13_SCENES)
        conn.executescript(_DDL_V14_RENDERS)
        conn.executescript(_DDL_V15_PUBLISHING)
        conn.executescript(_DDL_V16_ANALYTICS)
        conn.executescript(_DDL_V17_LEARNING)
        conn.executescript(_DDL_V18_CONTROL_PLANE)
        conn.executescript(_DDL_V19_APPLICATION)
        conn.executescript(_DDL_V20_AUTH_STORAGE)
        _apply_v21_topic_workspace(conn)
        _apply_v22_analytics_retention(conn)
        _apply_v23_publication_ownership(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 13:
        logger.info("Migrating schema from version 13 to %d", SCHEMA_VERSION)
        conn.executescript(_DDL_V14_RENDERS)
        conn.executescript(_DDL_V15_PUBLISHING)
        conn.executescript(_DDL_V16_ANALYTICS)
        conn.executescript(_DDL_V17_LEARNING)
        conn.executescript(_DDL_V18_CONTROL_PLANE)
        conn.executescript(_DDL_V19_APPLICATION)
        conn.executescript(_DDL_V20_AUTH_STORAGE)
        _apply_v21_topic_workspace(conn)
        _apply_v22_analytics_retention(conn)
        _apply_v23_publication_ownership(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 14:
        logger.info("Migrating schema from version 14 to %d", SCHEMA_VERSION)
        conn.executescript(_DDL_V15_PUBLISHING)
        conn.executescript(_DDL_V16_ANALYTICS)
        conn.executescript(_DDL_V17_LEARNING)
        conn.executescript(_DDL_V18_CONTROL_PLANE)
        conn.executescript(_DDL_V19_APPLICATION)
        conn.executescript(_DDL_V20_AUTH_STORAGE)
        _apply_v21_topic_workspace(conn)
        _apply_v22_analytics_retention(conn)
        _apply_v23_publication_ownership(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 15:
        logger.info("Migrating schema from version 15 to %d", SCHEMA_VERSION)
        conn.executescript(_DDL_V16_ANALYTICS)
        conn.executescript(_DDL_V17_LEARNING)
        conn.executescript(_DDL_V18_CONTROL_PLANE)
        conn.executescript(_DDL_V19_APPLICATION)
        conn.executescript(_DDL_V20_AUTH_STORAGE)
        _apply_v21_topic_workspace(conn)
        _apply_v22_analytics_retention(conn)
        _apply_v23_publication_ownership(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 16:
        logger.info("Migrating schema from version 16 to %d", SCHEMA_VERSION)
        conn.executescript(_DDL_V17_LEARNING)
        conn.executescript(_DDL_V18_CONTROL_PLANE)
        conn.executescript(_DDL_V19_APPLICATION)
        conn.executescript(_DDL_V20_AUTH_STORAGE)
        _apply_v21_topic_workspace(conn)
        _apply_v22_analytics_retention(conn)
        _apply_v23_publication_ownership(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 17:
        logger.info("Migrating schema from version 17 to %d", SCHEMA_VERSION)
        conn.executescript(_DDL_V18_CONTROL_PLANE)
        conn.executescript(_DDL_V19_APPLICATION)
        conn.executescript(_DDL_V20_AUTH_STORAGE)
        _apply_v21_topic_workspace(conn)
        _apply_v22_analytics_retention(conn)
        _apply_v23_publication_ownership(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 18:
        logger.info("Migrating schema from version 18 to %d", SCHEMA_VERSION)
        conn.executescript(_DDL_V19_APPLICATION)
        conn.executescript(_DDL_V20_AUTH_STORAGE)
        _apply_v21_topic_workspace(conn)
        _apply_v22_analytics_retention(conn)
        _apply_v23_publication_ownership(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")
    elif current == 19:
        logger.info("Migrating schema from version 19 to %d", SCHEMA_VERSION)
        conn.executescript(_DDL_V20_AUTH_STORAGE)
        _apply_v21_topic_workspace(conn)
        _apply_v22_analytics_retention(conn)
        _apply_v23_publication_ownership(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 20:
        logger.info("Migrating schema from version 20 to %d", SCHEMA_VERSION)
        _apply_v21_topic_workspace(conn)
        _apply_v22_analytics_retention(conn)
        _apply_v23_publication_ownership(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 21:
        logger.info("Migrating schema from version 21 to %d", SCHEMA_VERSION)
        _apply_v22_analytics_retention(conn)
        _apply_v23_publication_ownership(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 22:
        logger.info("Migrating schema from version 22 to %d", SCHEMA_VERSION)
        _apply_v23_publication_ownership(conn)
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
    # check_same_thread=False: FastAPI runs sync handlers in a threadpool via anyio;
    # the generator dependency teardown (conn.close) can execute in a different thread
    # than where the connection was opened.  The connection is never shared across
    # concurrent requests — it is per-request — so disabling the check is safe.
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _migrate(conn)
    conn.commit()
    logger.debug("Database open: %s", path)
    return conn


def open_db_postgres(url: str) -> CompatConnection:
    """Open a PostgreSQL connection and return a CompatConnection.

    Schema management for PostgreSQL is handled by Alembic (alembic upgrade head).
    This function connects and verifies the connection only — it does NOT run DDL.
    """
    import psycopg

    from app.core.db_compat import CompatConnection

    pg_conn = psycopg.connect(url, autocommit=False)
    conn = CompatConnection(pg_conn)
    logger.debug("PostgreSQL connection open: %s", _redact_url(url))
    return conn


def get_db_connection(db_url: str | None = None, db_path: Path | None = None):
    """Factory that returns an appropriate DB connection based on configuration.

    Priority:
    1. db_url argument (explicit override)
    2. ACE_DATABASE_URL environment variable → PostgreSQL CompatConnection
    3. db_path argument → SQLite Connection
    4. Config default (ACE_DB_PATH or platform default) → SQLite Connection

    Usage in tests: pass db_path to force SQLite regardless of env vars.
    Usage in production: set ACE_DATABASE_URL; db_path is ignored.
    """
    import os

    resolved_url = db_url or os.environ.get("ACE_DATABASE_URL", "")
    if resolved_url:
        return open_db_postgres(resolved_url)

    if db_path is None:
        from app.core.config import get_config

        db_path = get_config().db_path
    return open_db(db_path)


def _redact_url(url: str) -> str:
    """Replace the password in a DSN with ***."""
    import re

    return re.sub(r"(://[^:]*:)[^@]*(@)", r"\1***\2", url)
