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
SCHEMA_VERSION = 51

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
    updated_at              TEXT    NOT NULL
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
                                CHECK (status IN ('succeeded','failed','skipped')),
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


def _apply_v24_analytics_observation(conn: sqlite3.Connection) -> None:
    """Add observed_at, response_fingerprint, observation_state to analytics_snapshots."""
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='analytics_snapshots'"
    ).fetchone()
    if not table_exists:
        return
    existing_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(analytics_snapshots)").fetchall()
    }
    if "observed_at" not in existing_cols:
        conn.execute("ALTER TABLE analytics_snapshots ADD COLUMN observed_at TEXT")
    if "response_fingerprint" not in existing_cols:
        conn.execute("ALTER TABLE analytics_snapshots ADD COLUMN response_fingerprint TEXT")
    if "observation_state" not in existing_cols:
        conn.execute(
            "ALTER TABLE analytics_snapshots ADD COLUMN observation_state TEXT"
            " CHECK (observation_state IS NULL OR observation_state IN ('data', 'no_data'))"
        )


def _apply_v25_generator_skipped_status(conn: sqlite3.Connection) -> None:
    """Allow 'skipped' as a valid status in learning_run_generator_results.

    SQLite does not support ALTER TABLE to modify CHECK constraints, so this
    migration recreates the table with the updated constraint and re-populates
    all existing rows.  The index is also recreated.
    """
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='learning_run_generator_results'"
    ).fetchone()
    if not table_exists:
        return

    # Check if the current constraint already allows 'skipped'.
    ddl_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='learning_run_generator_results'"
    ).fetchone()
    if ddl_row and "'skipped'" in ddl_row[0]:
        return  # already migrated

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS learning_run_generator_results_v25 (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            learning_run_id         INTEGER NOT NULL
                                        REFERENCES learning_runs(id),
            generator_name          TEXT NOT NULL
                                        CHECK (generator_name IN (
                                            'ctr','retention','engagement',
                                            'watch_time','subscribers','shares'
                                        )),
            status                  TEXT NOT NULL
                                        CHECK (status IN (
                                            'succeeded','failed','skipped'
                                        )),
            recommendation_count    INTEGER NOT NULL DEFAULT 0,
            error_message           TEXT,
            created_at              TEXT NOT NULL
                                        DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
        );

        INSERT INTO learning_run_generator_results_v25
            (id, learning_run_id, generator_name, status,
             recommendation_count, error_message, created_at)
        SELECT
            id, learning_run_id, generator_name, status,
            recommendation_count, error_message, created_at
        FROM learning_run_generator_results;

        DROP TABLE learning_run_generator_results;

        ALTER TABLE learning_run_generator_results_v25
            RENAME TO learning_run_generator_results;

        CREATE INDEX IF NOT EXISTS idx_lrgr_run
            ON learning_run_generator_results (learning_run_id);
        """
    )


# Phase 26 DDL — Recommendation Application Framework (Phase 12A).
_DDL_V26_RECOMMENDATION_APPLICATIONS = """
CREATE TABLE IF NOT EXISTS recommendation_applications (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Source recommendation provenance
    recommendation_id           INTEGER NOT NULL
                                    REFERENCES optimization_recommendations(id),
    learning_run_id             INTEGER NOT NULL
                                    REFERENCES learning_runs(id),
    -- Target parameter identity
    domain                      TEXT NOT NULL,
    subsystem                   TEXT NOT NULL,
    parameter_name              TEXT NOT NULL,
    -- Structured intent (explicit, never inferred from strings)
    intent_direction            TEXT NOT NULL
                                    CHECK (intent_direction IN (
                                        'increase','decrease','maintain'
                                    )),
    intent_magnitude            REAL NOT NULL
                                    CHECK (intent_magnitude >= 0.0),
    intent_target_value         REAL NOT NULL,
    -- Scope (always anchored to topic_id; never leaks across topics)
    topic_id                    INTEGER NOT NULL REFERENCES topics(id),
    channel_id                  INTEGER,
    workspace_id                TEXT,
    -- Publication + snapshot provenance
    publication_id              INTEGER,
    source_snapshot_ids_json    TEXT NOT NULL DEFAULT '[]',
    -- Value tracking
    value_before                REAL,
    value_applied               REAL,
    -- Which narration run consumed this application
    narration_run_id            INTEGER,
    -- Lifecycle
    status                      TEXT NOT NULL DEFAULT 'proposed'
                                    CHECK (status IN (
                                        'proposed','applied','superseded','reverted','failed'
                                    )),
    -- Safety bounds snapshot (recorded at proposal for auditability)
    safety_min                  REAL NOT NULL,
    safety_max                  REAL NOT NULL,
    safety_max_delta            REAL NOT NULL,
    -- Deterministic identity hash
    input_hash                  TEXT NOT NULL,
    -- Error tracking
    error_message               TEXT,
    -- Timestamps
    proposed_at                 TEXT NOT NULL,
    applied_at                  TEXT,
    reverted_at                 TEXT,
    superseded_at               TEXT,
    created_at                  TEXT NOT NULL,
    updated_at                  TEXT NOT NULL,
    -- Idempotency: one active application per (recommendation, topic, parameter)
    UNIQUE (recommendation_id, topic_id, parameter_name)
);

CREATE INDEX IF NOT EXISTS idx_rec_app_topic_param
    ON recommendation_applications (topic_id, parameter_name, status);
"""


def _apply_v26_recommendation_applications(conn: sqlite3.Connection) -> None:
    """Add the recommendation_applications table (Phase 12A)."""
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='recommendation_applications'"
    ).fetchone()
    if table_exists:
        return
    conn.executescript(_DDL_V26_RECOMMENDATION_APPLICATIONS)


# Phase 27 DDL — Content Feature Attribution (Phase 12B).
# One immutable, versioned feature snapshot per publication.
# Wide typed columns support direct SQL comparisons in Phase 12C learning queries.
# A features_json overflow column holds future/experimental features not yet
# promoted to typed columns, avoiding ALTER TABLE churn during iteration.
_DDL_V27_CONTENT_FEATURES = """
CREATE TABLE IF NOT EXISTS content_feature_snapshots (
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Scope
    publication_id                  INTEGER NOT NULL,
    topic_id                        INTEGER NOT NULL,

    -- Control plane scope (nullable for legacy/fake-provider publications)
    workspace_id                    TEXT,
    channel_id                      TEXT,

    -- Versioning (frozen at extraction time; historical rows never updated)
    feature_schema_version          TEXT    NOT NULL,
    extractor_version               TEXT    NOT NULL,
    input_hash                      TEXT    NOT NULL,
    extracted_at                    TEXT    NOT NULL,
    created_at                      TEXT    NOT NULL,

    -- Full lineage IDs (join targets for Phase 12C; never inferred)
    publishing_plan_id              INTEGER NOT NULL,
    production_plan_id              INTEGER NOT NULL,
    script_id                       INTEGER NOT NULL,
    narration_run_id                INTEGER NOT NULL,
    caption_run_id                  INTEGER NOT NULL,
    scene_manifest_id               INTEGER NOT NULL,
    render_manifest_id              INTEGER NOT NULL,
    voice_profile_id                INTEGER NOT NULL,

    -- SCRIPT features
    script_format                   TEXT,   -- 'short' | 'long_form'
    script_word_count               INTEGER,
    script_segment_count            INTEGER,
    script_section_count            INTEGER,
    has_hook                        INTEGER NOT NULL DEFAULT 0,  -- 0/1
    has_cta                         INTEGER NOT NULL DEFAULT 0,  -- 0/1
    hook_word_count                 INTEGER,

    -- PRODUCTION features
    target_duration_s               INTEGER,

    -- NARRATION features
    narration_speaking_rate         REAL,
    narration_provider              TEXT,
    narration_model                 TEXT,
    narration_voice_id              TEXT,
    narration_language              TEXT,
    narration_actual_duration_s     REAL,
    narration_segment_count         INTEGER,

    -- LEARNING APPLICATION features
    learning_application_used       INTEGER NOT NULL DEFAULT 0,  -- 0/1
    learning_application_id         INTEGER,
    learning_application_parameter  TEXT,
    learning_application_value      REAL,

    -- CAPTION features
    caption_total_cue_count         INTEGER,
    caption_total_duration_ms       INTEGER,
    caption_style_version           TEXT,
    caption_segmentation_version    TEXT,
    caption_timing_source           TEXT,   -- dominant timing source across cues

    -- SCENE features
    scene_count                     INTEGER,
    scene_asset_count               INTEGER,
    scene_has_ai_generated_assets   INTEGER NOT NULL DEFAULT 0,  -- 0/1
    scene_ai_generated_asset_count  INTEGER,
    scene_dominant_shot_type        TEXT,
    scene_dominant_transition       TEXT,

    -- RENDER features
    render_width                    INTEGER,
    render_height                   INTEGER,
    render_fps                      INTEGER,
    render_caption_burn_in          INTEGER NOT NULL DEFAULT 0,  -- 0/1
    render_actual_duration_s        REAL,
    render_file_size_bytes          INTEGER,

    -- PUBLISHING features
    publish_provider                TEXT,
    publish_visibility              TEXT,
    publish_made_for_kids           INTEGER NOT NULL DEFAULT 0,  -- 0/1
    publish_category                TEXT,
    publish_tag_count               INTEGER,
    publish_published_at            TEXT,
    publish_day_of_week             INTEGER,  -- 0=Monday … 6=Sunday
    publish_hour_utc                INTEGER,  -- 0–23
    publish_schedule_type           TEXT,

    -- DERIVED features (computed at extraction; deterministic from typed fields above)
    words_per_second                REAL,
    scenes_per_minute               REAL,
    avg_scene_duration_ms           REAL,
    caption_cues_per_second         REAL,

    -- Overflow for future / experimental features not yet promoted to typed columns
    features_json                   TEXT    NOT NULL DEFAULT '{}',

    -- One snapshot per publication; idempotent re-extraction
    UNIQUE (publication_id),
    UNIQUE (input_hash)
);

CREATE INDEX IF NOT EXISTS idx_cfs_topic
    ON content_feature_snapshots (topic_id);
CREATE INDEX IF NOT EXISTS idx_cfs_speaking_rate
    ON content_feature_snapshots (narration_speaking_rate)
    WHERE narration_speaking_rate IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_cfs_format
    ON content_feature_snapshots (script_format)
    WHERE script_format IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_cfs_provider
    ON content_feature_snapshots (publish_provider)
    WHERE publish_provider IS NOT NULL;
"""


def _apply_v27_content_features(conn: sqlite3.Connection) -> None:
    """Add the content_feature_snapshots table (Phase 12B)."""
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='content_feature_snapshots'"
    ).fetchone()
    if table_exists:
        return
    conn.executescript(_DDL_V27_CONTENT_FEATURES)


# Phase 28 DDL — Cross-Publication Learning Foundation (Phase 12C).
# Persists channel-scoped performance baselines and feature × bucket association
# observations.  These are the query targets for Phase 13/14 planning engines.
_DDL_V28_CROSS_PUB_LEARNING = """
CREATE TABLE IF NOT EXISTS channel_performance_baselines (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Scope
    channel_id                  TEXT    NOT NULL,
    workspace_id                TEXT,

    -- What metric this baseline covers
    metric_name                 TEXT    NOT NULL,
    period_type                 TEXT    NOT NULL DEFAULT 'lifetime',

    -- Descriptive statistics across all channel publications with this metric
    publication_count           INTEGER NOT NULL DEFAULT 0,
    mean                        REAL,
    median                      REAL,
    min_value                   REAL,
    max_value                   REAL,
    std_dev                     REAL,   -- NULL when publication_count < 2

    -- Evidence quality
    sample_maturity             TEXT    NOT NULL
                                    CHECK (sample_maturity IN (
                                        'insufficient','exploratory','directional','actionable'
                                    )),

    -- Source provenance
    source_publication_ids_json TEXT    NOT NULL DEFAULT '[]',
    source_snapshot_ids_json    TEXT    NOT NULL DEFAULT '[]',

    -- Versioning
    comparison_schema_version   TEXT    NOT NULL,
    observer_version            TEXT    NOT NULL,
    input_hash                  TEXT    NOT NULL,

    created_at                  TEXT    NOT NULL
                                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    updated_at                  TEXT    NOT NULL
                                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),

    -- One active baseline per (channel, metric)
    UNIQUE (channel_id, metric_name)
);

CREATE INDEX IF NOT EXISTS idx_cpb_channel
    ON channel_performance_baselines (channel_id);


CREATE TABLE IF NOT EXISTS feature_performance_observations (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Scope
    channel_id                  TEXT    NOT NULL,
    workspace_id                TEXT,

    -- What is being compared
    feature_name                TEXT    NOT NULL,
    feature_bucket              TEXT    NOT NULL,  -- bucket label or categorical value
    metric_name                 TEXT    NOT NULL,
    period_type                 TEXT    NOT NULL DEFAULT 'lifetime',

    -- Descriptive statistics for publications in this bucket × metric
    publication_count           INTEGER NOT NULL DEFAULT 0,
    mean                        REAL,
    median                      REAL,
    min_value                   REAL,
    max_value                   REAL,
    std_dev                     REAL,   -- NULL when publication_count < 2

    -- Comparison to channel baseline (NULL when baseline unavailable or baseline=0 for rel)
    baseline_mean               REAL,
    baseline_median             REAL,
    abs_diff_from_baseline      REAL,
    rel_diff_from_baseline      REAL,

    -- Evidence quality
    sample_maturity             TEXT    NOT NULL
                                    CHECK (sample_maturity IN (
                                        'insufficient','exploratory','directional','actionable'
                                    )),

    -- Semantic label: results here are ASSOCIATIONS, not causal effects.
    observation_type            TEXT    NOT NULL DEFAULT 'association',

    -- Source provenance
    source_publication_ids_json TEXT    NOT NULL DEFAULT '[]',
    source_snapshot_ids_json    TEXT    NOT NULL DEFAULT '[]',

    -- Versioning
    comparison_schema_version   TEXT    NOT NULL,
    observer_version            TEXT    NOT NULL,
    input_hash                  TEXT    NOT NULL,

    created_at                  TEXT    NOT NULL
                                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    updated_at                  TEXT    NOT NULL
                                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),

    -- One observation per (channel, feature, bucket, metric)
    UNIQUE (channel_id, feature_name, feature_bucket, metric_name)
);

CREATE INDEX IF NOT EXISTS idx_fpo_channel
    ON feature_performance_observations (channel_id);
CREATE INDEX IF NOT EXISTS idx_fpo_feature
    ON feature_performance_observations (feature_name, feature_bucket);
CREATE INDEX IF NOT EXISTS idx_fpo_metric
    ON feature_performance_observations (metric_name);
"""


def _apply_v28_cross_pub_learning(conn: sqlite3.Connection) -> None:
    """Add channel_performance_baselines and feature_performance_observations (Phase 12C)."""
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='channel_performance_baselines'"
    ).fetchone()
    if table_exists:
        return
    conn.executescript(_DDL_V28_CROSS_PUB_LEARNING)


_DDL_V29_MARKET_INTELLIGENCE = """
CREATE TABLE IF NOT EXISTS market_collection_jobs (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id                TEXT    REFERENCES cp_workspaces(id),
    channel_id                  INTEGER REFERENCES channels(id),
    job_type                    TEXT    NOT NULL
                                        CHECK (job_type IN (
                                            'search_scan', 'velocity_rescan',
                                            'competitor_scan', 'channel_stats'
                                        )),
    origin_type                 TEXT    NOT NULL DEFAULT 'manual'
                                        CHECK (origin_type IN (
                                            'manual', 'channel_bootstrap',
                                            'exploration_planner', 'adjacent_topic',
                                            'refresh', 'velocity_rescan'
                                        )),
    parent_job_id               INTEGER REFERENCES market_collection_jobs(id),
    exploration_depth           INTEGER NOT NULL DEFAULT 0,
    provider                    TEXT    NOT NULL DEFAULT 'youtube_data_api',
    platform                    TEXT    NOT NULL DEFAULT 'youtube',
    seeds_json                  TEXT    NOT NULL DEFAULT '[]',
    quota_policy_snapshot_json  TEXT,
    status                      TEXT    NOT NULL DEFAULT 'pending'
                                        CHECK (status IN (
                                            'pending', 'running', 'completed',
                                            'partial', 'failed'
                                        )),
    observation_count           INTEGER NOT NULL DEFAULT 0,
    quota_consumed_total        INTEGER NOT NULL DEFAULT 0,
    error_message               TEXT,
    failure_stage               TEXT,
    scheduled_for               TEXT,
    started_at                  TEXT,
    completed_at                TEXT,
    created_at                  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mcj_channel ON market_collection_jobs (channel_id);
CREATE INDEX IF NOT EXISTS idx_mcj_workspace ON market_collection_jobs (workspace_id);
CREATE INDEX IF NOT EXISTS idx_mcj_status ON market_collection_jobs (status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_mcj_parent ON market_collection_jobs (parent_job_id);

CREATE TABLE IF NOT EXISTS market_intelligence_observations (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    platform                    TEXT    NOT NULL DEFAULT 'youtube',
    provider                    TEXT    NOT NULL DEFAULT 'youtube_data_api',
    collector_name              TEXT    NOT NULL,
    external_video_id           TEXT,
    external_channel_id         TEXT,
    query_text                  TEXT,
    normalized_query            TEXT,
    region_code                 TEXT,
    language_code               TEXT,
    category_id                 TEXT,
    signal_type                 TEXT    NOT NULL,
    signal_value_numeric        REAL,
    signal_value_text           TEXT,
    content_published_at        TEXT,
    content_age_days            REAL,
    observed_at                 TEXT    NOT NULL,
    provider_payload_fingerprint TEXT,
    input_hash                  TEXT    NOT NULL UNIQUE,
    retain_until                TEXT,
    created_at                  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mio_platform_provider
    ON market_intelligence_observations (platform, provider);
CREATE INDEX IF NOT EXISTS idx_mio_video
    ON market_intelligence_observations (external_video_id)
    WHERE external_video_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_mio_channel_ext
    ON market_intelligence_observations (external_channel_id)
    WHERE external_channel_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_mio_signal_type
    ON market_intelligence_observations (signal_type, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_mio_query
    ON market_intelligence_observations (normalized_query)
    WHERE normalized_query IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_mio_observed_at
    ON market_intelligence_observations (observed_at DESC);

CREATE TABLE IF NOT EXISTS market_job_observations (
    job_id          INTEGER NOT NULL REFERENCES market_collection_jobs(id),
    observation_id  INTEGER NOT NULL REFERENCES market_intelligence_observations(id),
    discovered_at   TEXT    NOT NULL,
    PRIMARY KEY (job_id, observation_id)
);
CREATE INDEX IF NOT EXISTS idx_mjo_observation ON market_job_observations (observation_id);

CREATE TABLE IF NOT EXISTS market_job_quota_usage (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          INTEGER NOT NULL REFERENCES market_collection_jobs(id),
    provider        TEXT    NOT NULL,
    operation       TEXT    NOT NULL,
    quota_bucket    TEXT    NOT NULL,
    units_consumed  INTEGER NOT NULL DEFAULT 0,
    call_count      INTEGER NOT NULL DEFAULT 1,
    limit_snapshot  INTEGER,
    window_type     TEXT    NOT NULL DEFAULT 'daily'
                            CHECK (window_type IN ('daily', 'per_request', 'per_minute')),
    observed_at     TEXT    NOT NULL,
    UNIQUE (job_id, provider, operation, quota_bucket)
);
CREATE INDEX IF NOT EXISTS idx_mjqu_job ON market_job_quota_usage (job_id);
CREATE INDEX IF NOT EXISTS idx_mjqu_provider
    ON market_job_quota_usage (provider, quota_bucket, observed_at DESC);
"""


def _apply_v29_market_intelligence(conn: sqlite3.Connection) -> None:
    """Add market_collection_jobs, market_intelligence_observations, and quota
    tables (Phase 13A)."""
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_collection_jobs'"
    ).fetchone()
    if table_exists:
        return
    conn.executescript(_DDL_V29_MARKET_INTELLIGENCE)


_DDL_V30_VELOCITY = """
CREATE TABLE IF NOT EXISTS market_velocity_estimates (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    platform                    TEXT    NOT NULL DEFAULT 'youtube',
    provider                    TEXT    NOT NULL DEFAULT 'youtube_data_api',
    external_video_id           TEXT    NOT NULL,
    signal_type                 TEXT    NOT NULL DEFAULT 'video_view_count',
    start_observation_id        INTEGER NOT NULL
                                        REFERENCES market_intelligence_observations(id),
    end_observation_id          INTEGER NOT NULL
                                        REFERENCES market_intelligence_observations(id),
    start_time                  TEXT    NOT NULL,
    end_time                    TEXT    NOT NULL,
    start_value                 REAL    NOT NULL,
    end_value                   REAL    NOT NULL,
    raw_delta                   REAL    NOT NULL,
    elapsed_seconds             REAL    NOT NULL,
    units_per_hour              REAL,
    units_per_day               REAL,
    is_negative_delta           INTEGER NOT NULL DEFAULT 0,
    video_age_hours_at_start    REAL,
    video_age_hours_at_end      REAL,
    velocity_maturity           TEXT    NOT NULL DEFAULT 'early'
                                        CHECK (velocity_maturity IN (
                                            'insufficient', 'early',
                                            'establishing', 'mature'
                                        )),
    calculation_version         TEXT    NOT NULL DEFAULT 'v1',
    input_hash                  TEXT    NOT NULL UNIQUE,
    created_at                  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mve_video
    ON market_velocity_estimates
    (platform, provider, external_video_id, signal_type, end_time DESC);
CREATE INDEX IF NOT EXISTS idx_mve_end_obs
    ON market_velocity_estimates (end_observation_id);
CREATE INDEX IF NOT EXISTS idx_mve_maturity
    ON market_velocity_estimates (velocity_maturity);
"""


def _apply_v30_velocity(conn: sqlite3.Connection) -> None:
    """Add market_velocity_estimates table (Phase 13C)."""
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_velocity_estimates'"
    ).fetchone()
    if table_exists:
        return
    conn.executescript(_DDL_V30_VELOCITY)


_DDL_V31_EXPLORATION = """
CREATE TABLE IF NOT EXISTS market_exploration_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id        TEXT    REFERENCES cp_workspaces(id),
    channel_id          INTEGER REFERENCES channels(id),
    planner_version     TEXT    NOT NULL DEFAULT 'v1',
    prompt_version      TEXT,
    provider            TEXT,
    model               TEXT,
    max_depth           INTEGER NOT NULL DEFAULT 3,
    max_probes          INTEGER NOT NULL DEFAULT 10,
    search_budget       INTEGER NOT NULL DEFAULT 20,
    policy_json         TEXT    NOT NULL DEFAULT '{}',
    input_hash          TEXT    NOT NULL UNIQUE,
    status              TEXT    NOT NULL DEFAULT 'pending'
                                CHECK (status IN (
                                    'pending', 'running', 'completed',
                                    'partial', 'failed'
                                )),
    candidate_count     INTEGER NOT NULL DEFAULT 0,
    selected_count      INTEGER NOT NULL DEFAULT 0,
    deferred_count      INTEGER NOT NULL DEFAULT 0,
    rejected_count      INTEGER NOT NULL DEFAULT 0,
    dispatched_count    INTEGER NOT NULL DEFAULT 0,
    error_message       TEXT,
    started_at          TEXT,
    completed_at        TEXT,
    created_at          TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mer_channel
    ON market_exploration_runs (channel_id);
CREATE INDEX IF NOT EXISTS idx_mer_workspace
    ON market_exploration_runs (workspace_id);
CREATE INDEX IF NOT EXISTS idx_mer_status
    ON market_exploration_runs (status, created_at DESC);

CREATE TABLE IF NOT EXISTS market_exploration_probes (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    exploration_run_id          INTEGER NOT NULL
                                        REFERENCES market_exploration_runs(id),
    workspace_id                TEXT    REFERENCES cp_workspaces(id),
    channel_id                  INTEGER REFERENCES channels(id),
    query_text                  TEXT    NOT NULL,
    normalized_query            TEXT    NOT NULL,
    probe_type                  TEXT    NOT NULL
                                        CHECK (probe_type IN (
                                            'channel_bootstrap', 'market_region',
                                            'adjacent_topic', 'velocity_followup',
                                            'validation'
                                        )),
    parent_probe_id             INTEGER REFERENCES market_exploration_probes(id),
    parent_job_id               INTEGER REFERENCES market_collection_jobs(id),
    exploration_depth           INTEGER NOT NULL DEFAULT 0,
    region_code                 TEXT,
    language_code               TEXT,
    collection_policy_json      TEXT    NOT NULL DEFAULT '{}',
    status                      TEXT    NOT NULL DEFAULT 'candidate'
                                        CHECK (status IN (
                                            'candidate', 'selected', 'deferred',
                                            'rejected', 'dispatched'
                                        )),
    priority_score              REAL,
    priority_components_json    TEXT,
    niche_fit_score             REAL,
    semantic_fit_status         TEXT
                                CHECK (semantic_fit_status IN (
                                    'eligible', 'ineligible', 'pending', NULL
                                )),
    decision_reason             TEXT,
    corroboration_count         INTEGER NOT NULL DEFAULT 0,
    dispatched_job_id           INTEGER REFERENCES market_collection_jobs(id),
    planner_version             TEXT    NOT NULL DEFAULT 'v1',
    input_hash                  TEXT    NOT NULL,
    decided_at                  TEXT,
    dispatched_at               TEXT,
    created_at                  TEXT    NOT NULL,
    UNIQUE (exploration_run_id, input_hash)
);
CREATE INDEX IF NOT EXISTS idx_mep_run
    ON market_exploration_probes (exploration_run_id, status);
CREATE INDEX IF NOT EXISTS idx_mep_channel
    ON market_exploration_probes (channel_id);
CREATE INDEX IF NOT EXISTS idx_mep_parent_probe
    ON market_exploration_probes (parent_probe_id)
    WHERE parent_probe_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_mep_dispatched_job
    ON market_exploration_probes (dispatched_job_id)
    WHERE dispatched_job_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_mep_probe_type
    ON market_exploration_probes (probe_type, status);

CREATE TABLE IF NOT EXISTS market_probe_evidence (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    probe_id        INTEGER NOT NULL REFERENCES market_exploration_probes(id),
    evidence_type   TEXT    NOT NULL
                            CHECK (evidence_type IN ('observation', 'velocity', 'job')),
    observation_id  INTEGER REFERENCES market_intelligence_observations(id),
    velocity_id     INTEGER REFERENCES market_velocity_estimates(id),
    job_id          INTEGER REFERENCES market_collection_jobs(id),
    evidence_notes  TEXT,
    created_at      TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mpe_probe
    ON market_probe_evidence (probe_id);
CREATE INDEX IF NOT EXISTS idx_mpe_observation
    ON market_probe_evidence (observation_id)
    WHERE observation_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_mpe_velocity
    ON market_probe_evidence (velocity_id)
    WHERE velocity_id IS NOT NULL;
"""


_DDL_V32_INTERPRETATION = """
CREATE TABLE IF NOT EXISTS market_interpretation_runs (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    platform                TEXT    NOT NULL DEFAULT 'youtube',
    provider                TEXT    NOT NULL DEFAULT 'youtube_data_api',
    region_code             TEXT,
    language_code           TEXT,
    clustering_version      TEXT    NOT NULL DEFAULT 'v1',
    scoring_version         TEXT    NOT NULL DEFAULT 'v1',
    evidence_cutoff         TEXT    NOT NULL,
    source_run_ids_json     TEXT    NOT NULL DEFAULT '[]',
    policy_snapshot_json    TEXT    NOT NULL DEFAULT '{}',
    status                  TEXT    NOT NULL DEFAULT 'pending',
    cluster_count           INTEGER NOT NULL DEFAULT 0,
    error_message           TEXT,
    started_at              TEXT,
    completed_at            TEXT,
    input_hash              TEXT    NOT NULL UNIQUE,
    created_at              TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_mir_platform_provider
    ON market_interpretation_runs (platform, provider);
CREATE INDEX IF NOT EXISTS idx_mir_status
    ON market_interpretation_runs (status);

CREATE TABLE IF NOT EXISTS market_topic_clusters (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    interpretation_run_id   INTEGER NOT NULL
                                REFERENCES market_interpretation_runs(id),
    platform                TEXT    NOT NULL DEFAULT 'youtube',
    provider                TEXT    NOT NULL DEFAULT 'youtube_data_api',
    region_code             TEXT,
    language_code           TEXT,
    cluster_label           TEXT    NOT NULL,
    normalized_label        TEXT    NOT NULL,
    cluster_type            TEXT    NOT NULL DEFAULT 'market_region',
    description             TEXT    NOT NULL DEFAULT '',
    clustering_rationale    TEXT    NOT NULL DEFAULT '',
    cluster_version         TEXT    NOT NULL DEFAULT 'v1',
    llm_used                INTEGER NOT NULL DEFAULT 0,
    llm_model               TEXT,
    llm_prompt_version      TEXT,
    member_probe_count      INTEGER NOT NULL DEFAULT 0,
    member_video_count      INTEGER NOT NULL DEFAULT 0,
    input_hash              TEXT    NOT NULL,
    created_at              TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (interpretation_run_id, input_hash)
);
CREATE INDEX IF NOT EXISTS idx_mtc_run
    ON market_topic_clusters (interpretation_run_id);
CREATE INDEX IF NOT EXISTS idx_mtc_normalized_label
    ON market_topic_clusters (normalized_label);

CREATE TABLE IF NOT EXISTS market_cluster_members (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id              INTEGER NOT NULL
                                REFERENCES market_topic_clusters(id),
    member_type             TEXT    NOT NULL DEFAULT 'evidence_video',
    probe_id                INTEGER
                                REFERENCES market_exploration_probes(id),
    external_video_id       TEXT,
    platform                TEXT    NOT NULL DEFAULT 'youtube',
    provider                TEXT    NOT NULL DEFAULT 'youtube_data_api',
    supporting_job_id       INTEGER,
    created_at              TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (cluster_id, member_type, probe_id),
    UNIQUE (cluster_id, member_type, external_video_id)
);
CREATE INDEX IF NOT EXISTS idx_mcm_cluster
    ON market_cluster_members (cluster_id);
CREATE INDEX IF NOT EXISTS idx_mcm_probe
    ON market_cluster_members (probe_id)
    WHERE probe_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_mcm_video
    ON market_cluster_members (external_video_id)
    WHERE external_video_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS market_cluster_signals (
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id                      INTEGER NOT NULL
                                        REFERENCES market_topic_clusters(id),
    interpretation_run_id           INTEGER NOT NULL
                                        REFERENCES market_interpretation_runs(id),
    demand_score                    REAL,
    saturation_score                REAL,
    freshness_score                 REAL,
    momentum_score                  REAL,
    persistence_score               REAL,
    confidence                      REAL    NOT NULL DEFAULT 0.0,
    signal_maturity                 TEXT    NOT NULL DEFAULT 'insufficient',
    state_label                     TEXT,
    supporting_video_count          INTEGER NOT NULL DEFAULT 0,
    supporting_creator_count        INTEGER NOT NULL DEFAULT 0,
    velocity_tracked_video_count    INTEGER NOT NULL DEFAULT 0,
    demand_components_json          TEXT    NOT NULL DEFAULT '{}',
    saturation_components_json      TEXT    NOT NULL DEFAULT '{}',
    freshness_components_json       TEXT    NOT NULL DEFAULT '{}',
    momentum_components_json        TEXT    NOT NULL DEFAULT '{}',
    persistence_components_json     TEXT    NOT NULL DEFAULT '{}',
    scoring_version                 TEXT    NOT NULL DEFAULT 'v1',
    supporting_observation_ids_json TEXT    NOT NULL DEFAULT '[]',
    input_hash                      TEXT    NOT NULL,
    scored_at                       TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (cluster_id, interpretation_run_id)
);
CREATE INDEX IF NOT EXISTS idx_mcs_cluster
    ON market_cluster_signals (cluster_id);
CREATE INDEX IF NOT EXISTS idx_mcs_run
    ON market_cluster_signals (interpretation_run_id);
"""


_DDL_V33_CANONICAL = """
CREATE TABLE IF NOT EXISTS market_canonical_clusters (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    platform             TEXT    NOT NULL DEFAULT 'youtube',
    provider             TEXT    NOT NULL DEFAULT 'youtube_data_api',
    region_code          TEXT,
    language_code        TEXT,
    canonical_label      TEXT    NOT NULL,
    normalized_label     TEXT    NOT NULL,
    semantic_fingerprint TEXT    NOT NULL,
    identity_version     TEXT    NOT NULL DEFAULT 'v1',
    created_at           TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at           TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (platform, provider, region_code, language_code, semantic_fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_mcc_normalized_label
    ON market_canonical_clusters (normalized_label);
CREATE INDEX IF NOT EXISTS idx_mcc_scope
    ON market_canonical_clusters (platform, provider, region_code, language_code);
"""


def _apply_v33_canonical_clusters(conn: sqlite3.Connection) -> None:
    """Add canonical cluster identity table and two new columns (Phase 13E.1)."""
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_canonical_clusters'"
    ).fetchone()
    if table_exists:
        return
    conn.executescript(_DDL_V33_CANONICAL)
    # Add canonical_cluster_id FK to existing market_topic_clusters.
    try:
        conn.execute(
            "ALTER TABLE market_topic_clusters "
            "ADD COLUMN canonical_cluster_id INTEGER "
            "REFERENCES market_canonical_clusters(id)"
        )
    except sqlite3.OperationalError:
        pass
    # Add market_region_label to exploration probes for richer provenance.
    try:
        conn.execute("ALTER TABLE market_exploration_probes ADD COLUMN market_region_label TEXT")
    except sqlite3.OperationalError:
        pass


def _apply_v32_interpretation(conn: sqlite3.Connection) -> None:
    """Add 4 market interpretation tables (Phase 13E)."""
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_interpretation_runs'"
    ).fetchone()
    if table_exists:
        return
    conn.executescript(_DDL_V32_INTERPRETATION)


def _apply_v31_exploration(conn: sqlite3.Connection) -> None:
    """Add market_exploration_runs, market_exploration_probes,
    market_probe_evidence (Phase 13D-A)."""
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_exploration_runs'"
    ).fetchone()
    if table_exists:
        return
    conn.executescript(_DDL_V31_EXPLORATION)


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


def _apply_v34_market_bridge(conn: sqlite3.Connection) -> None:
    """Extend discovery_runs adapter CHECK + add canonical FK columns to
    opportunities (Phase 13F)."""
    # Skip entirely if opportunities table doesn't exist yet (hand-crafted test
    # DBs, partial schemas)
    opp_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='opportunities'"
    ).fetchone()
    if not opp_exists:
        return

    # Idempotency: skip if canonical_cluster_id already present in opportunities
    cols = {row[1] for row in conn.execute("PRAGMA table_info(opportunities)").fetchall()}
    if "canonical_cluster_id" in cols:
        return

    # Rebuild discovery_runs to extend adapter_name CHECK constraint
    dr_schema = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='discovery_runs'"
    ).fetchone()
    if dr_schema and "market_intelligence" not in (dr_schema[0] or ""):
        conn.executescript("""
PRAGMA foreign_keys = OFF;

CREATE TABLE discovery_runs_v34 (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id              INTEGER NOT NULL REFERENCES channels(id),
    profile_version_id      INTEGER NOT NULL REFERENCES channel_profile_versions(id),
    adapter_name            TEXT    NOT NULL
                                    CHECK (adapter_name IN (
                                        'manual', 'youtube_data_api',
                                        'market_intelligence')),
    query_parameters_json   TEXT    NOT NULL DEFAULT '{}',
    status                  TEXT    NOT NULL DEFAULT 'pending'
                                    CHECK (status IN (
                                        'pending', 'running', 'completed',
                                        'partial', 'failed')),
    candidate_count         INTEGER NOT NULL DEFAULT 0,
    new_opportunity_count   INTEGER NOT NULL DEFAULT 0,
    dedup_count             INTEGER NOT NULL DEFAULT 0,
    failed_count            INTEGER NOT NULL DEFAULT 0,
    quota_units_consumed    INTEGER NOT NULL DEFAULT 0,
    error_message           TEXT,
    started_at              TEXT    NOT NULL,
    completed_at            TEXT
);

INSERT INTO discovery_runs_v34 SELECT * FROM discovery_runs;
DROP TABLE discovery_runs;
ALTER TABLE discovery_runs_v34 RENAME TO discovery_runs;

PRAGMA foreign_keys = ON;
""")

    # Add canonical provenance columns to opportunities
    conn.execute(
        "ALTER TABLE opportunities ADD COLUMN canonical_cluster_id INTEGER "
        "REFERENCES market_canonical_clusters(id)"
    )
    conn.execute("ALTER TABLE opportunities ADD COLUMN market_signal_snapshot_id INTEGER")
    # Partial unique index: one active opportunity per (channel, canonical_cluster)
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_opps_channel_canonical "
        "ON opportunities(channel_id, canonical_cluster_id) "
        "WHERE canonical_cluster_id IS NOT NULL"
    )


def _apply_v35_active_opportunity_identity(conn: sqlite3.Connection) -> None:
    """Replace the v34 partial unique index with one that excludes rejected/archived rows.

    v34 index:  WHERE canonical_cluster_id IS NOT NULL
    v35 index:  WHERE canonical_cluster_id IS NOT NULL
                  AND current_lifecycle_state NOT IN ('rejected', 'archived')

    A rejected or archived Opportunity must not block future rediscovery of the same
    canonical cluster by preventing a new active Opportunity from being created.

    Idempotency: skipped if the index already contains the NOT IN clause.
    Guard: skipped if the opportunities table does not exist.
    """
    opp_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='opportunities'"
    ).fetchone()
    if not opp_exists:
        return

    idx = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name='uq_opps_channel_canonical'"
    ).fetchone()
    if idx and "NOT IN" in (idx[0] or ""):
        return  # Already at v35 form

    conn.execute("DROP INDEX IF EXISTS uq_opps_channel_canonical")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_opps_channel_canonical "
        "ON opportunities(channel_id, canonical_cluster_id) "
        "WHERE canonical_cluster_id IS NOT NULL "
        "  AND current_lifecycle_state NOT IN ('rejected', 'archived')"
    )


_DDL_V37_EXPERIMENT_LEDGER = """
CREATE TABLE IF NOT EXISTS experiments (
    id                      TEXT    PRIMARY KEY,
    channel_id              INTEGER NOT NULL REFERENCES channels(id),
    opportunity_id          INTEGER REFERENCES opportunities(id),
    experiment_type         TEXT    NOT NULL
                                CHECK (experiment_type IN ('exploration', 'exploitation')),
    status                  TEXT    NOT NULL DEFAULT 'draft'
                                CHECK (status IN (
                                    'draft', 'planned', 'in_production', 'published',
                                    'observing', 'mature', 'analyzed',
                                    'completed', 'cancelled'
                                )),
    hypothesis              TEXT    NOT NULL DEFAULT '',
    hypothesis_null         TEXT    NOT NULL DEFAULT '',
    hypothesis_metric       TEXT,
    input_hash              TEXT    UNIQUE,
    maturity_policy_json    TEXT    NOT NULL DEFAULT '{}',
    policy_snapshot_json    TEXT    NOT NULL DEFAULT '{}',
    created_at              TEXT    NOT NULL
                                DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    updated_at              TEXT    NOT NULL
                                DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    planned_at              TEXT,
    in_production_at        TEXT,
    published_at            TEXT,
    observing_at            TEXT,
    matured_at              TEXT,
    analyzed_at             TEXT,
    completed_at            TEXT,
    cancelled_at            TEXT,
    cancelled_reason        TEXT,
    publication_id          INTEGER REFERENCES publications(id)
);

CREATE INDEX IF NOT EXISTS idx_experiments_channel
    ON experiments (channel_id);
CREATE INDEX IF NOT EXISTS idx_experiments_opportunity
    ON experiments (opportunity_id);
CREATE INDEX IF NOT EXISTS idx_experiments_status
    ON experiments (status);

CREATE TABLE IF NOT EXISTS experiment_state_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id   TEXT    NOT NULL REFERENCES experiments(id),
    from_state      TEXT,
    to_state        TEXT    NOT NULL,
    actor           TEXT    NOT NULL DEFAULT 'system',
    reason          TEXT    NOT NULL DEFAULT '',
    created_at      TEXT    NOT NULL
                        DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_ese_experiment
    ON experiment_state_events (experiment_id);

CREATE TABLE IF NOT EXISTS experiment_metric_targets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id   TEXT    NOT NULL REFERENCES experiments(id),
    metric_name     TEXT    NOT NULL,
    direction       TEXT    NOT NULL
                        CHECK (direction IN (
                            'higher_is_better', 'lower_is_better',
                            'target_range', 'informational_only'
                        )),
    target_value    REAL,
    target_min      REAL,
    target_max      REAL,
    is_primary      INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
    UNIQUE (experiment_id, metric_name)
);

CREATE INDEX IF NOT EXISTS idx_emt_experiment
    ON experiment_metric_targets (experiment_id);

CREATE TABLE IF NOT EXISTS experiment_factors (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id   TEXT    NOT NULL REFERENCES experiments(id),
    factor_name     TEXT    NOT NULL,
    factor_role     TEXT    NOT NULL
                        CHECK (factor_role IN ('treatment', 'controlled', 'observed')),
    intended_value  TEXT,
    actual_value    TEXT,
    value_type      TEXT    NOT NULL DEFAULT 'string',
    UNIQUE (experiment_id, factor_name)
);

CREATE INDEX IF NOT EXISTS idx_ef_experiment
    ON experiment_factors (experiment_id);
"""


def _apply_v37_experiment_ledger(conn: sqlite3.Connection) -> None:
    """Add experiments, experiment_state_events, experiment_metric_targets,
    experiment_factors (Phase 14A)."""
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='experiments'"
    ).fetchone():
        return
    conn.executescript(_DDL_V37_EXPERIMENT_LEDGER)


_DDL_V38_EXPERIMENT_PLANNING = """
CREATE TABLE IF NOT EXISTS experiment_planning_runs (
    id                      TEXT    PRIMARY KEY,
    channel_id              INTEGER NOT NULL REFERENCES channels(id),
    status                  TEXT    NOT NULL DEFAULT 'completed'
                                CHECK (status IN ('completed', 'dry_run', 'failed')),
    policy_snapshot_json    TEXT    NOT NULL DEFAULT '{}',
    eligible_count          INTEGER NOT NULL DEFAULT 0,
    exploration_only_count  INTEGER NOT NULL DEFAULT 0,
    general_eligible_count  INTEGER NOT NULL DEFAULT 0,
    selected_count          INTEGER NOT NULL DEFAULT 0,
    deferred_count          INTEGER NOT NULL DEFAULT 0,
    input_hash              TEXT    NOT NULL,
    created_at              TEXT    NOT NULL
                                DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_planning_runs_channel
    ON experiment_planning_runs (channel_id);

CREATE TABLE IF NOT EXISTS experiment_candidate_scores (
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,
    planning_run_id                 TEXT    NOT NULL REFERENCES experiment_planning_runs(id),
    opportunity_id                  INTEGER NOT NULL,
    channel_id                      INTEGER NOT NULL,
    canonical_cluster_id            INTEGER,
    eligibility_classification      TEXT    NOT NULL,
    planning_intent                 TEXT    NOT NULL
                                        CHECK (planning_intent IN
                                            ('exploration', 'exploitation', 'validation')),
    experiment_type                 TEXT    NOT NULL
                                        CHECK (experiment_type IN ('exploration', 'exploitation')),
    primary_target_metric           TEXT    NOT NULL,
    primary_metric_direction        TEXT    NOT NULL,
    hypothesis_sketch               TEXT    NOT NULL DEFAULT '',
    intended_treatment_factors_json TEXT    NOT NULL DEFAULT '[]',
    controlled_factors_json         TEXT    NOT NULL DEFAULT '[]',
    feature_change_risk             TEXT    NOT NULL DEFAULT 'low'
                                        CHECK (feature_change_risk IN
                                            ('low', 'medium', 'high', 'unknown')),
    opportunity_attractiveness      REAL,
    exploitation_value              REAL,
    exploration_value               REAL,
    information_gain                REAL,
    internal_evidence_strength      REAL,
    uncertainty                     REAL,
    cluster_coverage_need           REAL,
    production_feasibility          REAL,
    final_planning_score            REAL    NOT NULL,
    input_hash                      TEXT    NOT NULL,
    UNIQUE (planning_run_id, input_hash)
);

CREATE INDEX IF NOT EXISTS idx_candidate_scores_run
    ON experiment_candidate_scores (planning_run_id);
CREATE INDEX IF NOT EXISTS idx_candidate_scores_opportunity
    ON experiment_candidate_scores (opportunity_id);

CREATE TABLE IF NOT EXISTS experiment_selection_decisions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    planning_run_id     TEXT    NOT NULL REFERENCES experiment_planning_runs(id),
    candidate_score_id  INTEGER NOT NULL REFERENCES experiment_candidate_scores(id),
    opportunity_id      INTEGER NOT NULL,
    selected            INTEGER NOT NULL DEFAULT 0 CHECK (selected IN (0, 1)),
    rank_in_pool        INTEGER,
    pool_type           TEXT,
    selection_reason    TEXT    NOT NULL DEFAULT '',
    deferral_reason     TEXT,
    is_validation_repeat INTEGER NOT NULL DEFAULT 0 CHECK (is_validation_repeat IN (0, 1)),
    created_at          TEXT    NOT NULL
                            DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_selection_decisions_run
    ON experiment_selection_decisions (planning_run_id);
"""


def _apply_v38_experiment_planning(conn: sqlite3.Connection) -> None:
    """Add experiment_planning_runs, experiment_candidate_scores,
    experiment_selection_decisions (Phase 14D)."""
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='experiment_planning_runs'"
    ).fetchone():
        return
    conn.executescript(_DDL_V38_EXPERIMENT_PLANNING)


_DDL_V39_STRATEGY_BRIEF = """
CREATE TABLE IF NOT EXISTS experiment_strategy_briefs (
    id                          TEXT    PRIMARY KEY,
    channel_id                  INTEGER NOT NULL REFERENCES channels(id),
    planning_run_id             TEXT    NOT NULL REFERENCES experiment_planning_runs(id),
    selection_decision_id       INTEGER NOT NULL REFERENCES experiment_selection_decisions(id),
    opportunity_id              INTEGER NOT NULL,
    canonical_cluster_id        INTEGER,
    channel_profile_version_id  INTEGER,
    brief_planning_intent       TEXT    NOT NULL
                                    CHECK (brief_planning_intent IN (
                                        'market_exploration', 'feature_exploration',
                                        'validation', 'exploitation')),
    experiment_type             TEXT    NOT NULL
                                    CHECK (experiment_type IN ('exploration', 'exploitation')),
    market_theme                TEXT    NOT NULL DEFAULT '',
    canonical_topic             TEXT    NOT NULL DEFAULT '',
    strategic_reason            TEXT    NOT NULL DEFAULT '',
    information_gain_reason     TEXT    NOT NULL DEFAULT '',
    hypothesis                  TEXT    NOT NULL DEFAULT '',
    target_metric               TEXT    NOT NULL DEFAULT '',
    target_direction            TEXT    NOT NULL DEFAULT '',
    treatment_factors_json      TEXT    NOT NULL DEFAULT '[]',
    controlled_factors_json     TEXT    NOT NULL DEFAULT '[]',
    content_constraints_json    TEXT    NOT NULL DEFAULT '{}',
    confounding_risk            TEXT    NOT NULL DEFAULT 'low'
                                    CHECK (confounding_risk IN ('low', 'moderate', 'high')),
    policy_version              TEXT    NOT NULL DEFAULT '',
    eligibility_classification  TEXT    NOT NULL DEFAULT '',
    score_decomposition_json    TEXT    NOT NULL DEFAULT '{}',
    brief_hash                  TEXT    NOT NULL UNIQUE,
    status                      TEXT    NOT NULL DEFAULT 'pending_approval'
                                    CHECK (status IN
                                        ('pending_approval', 'approved', 'superseded')),
    created_at                  TEXT    NOT NULL
                                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_strategy_briefs_channel
    ON experiment_strategy_briefs (channel_id);
CREATE INDEX IF NOT EXISTS idx_strategy_briefs_planning_run
    ON experiment_strategy_briefs (planning_run_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_strategy_briefs_selection_decision
    ON experiment_strategy_briefs (selection_decision_id);

CREATE TABLE IF NOT EXISTS experiment_idea_candidates (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    brief_id                TEXT    NOT NULL REFERENCES experiment_strategy_briefs(id),
    channel_id              INTEGER NOT NULL,
    title_sketch            TEXT    NOT NULL DEFAULT '',
    hook_sketch             TEXT    NOT NULL DEFAULT '',
    content_angle           TEXT    NOT NULL DEFAULT '',
    constraint_flags_json   TEXT    NOT NULL DEFAULT '[]',
    semantic_fit_score      REAL,
    is_duplicate            INTEGER NOT NULL DEFAULT 0 CHECK (is_duplicate IN (0, 1)),
    selection_rank          INTEGER,
    status                  TEXT    NOT NULL DEFAULT 'candidate'
                                CHECK (status IN
                                    ('candidate', 'selected', 'rejected', 'superseded')),
    created_at              TEXT    NOT NULL
                                DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_idea_candidates_brief
    ON experiment_idea_candidates (brief_id);
"""


def _apply_v39_strategy_brief(conn: sqlite3.Connection) -> None:
    """Add experiment_strategy_briefs and experiment_idea_candidates (Phase 14E)."""
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='experiment_strategy_briefs'"
    ).fetchone():
        return
    conn.executescript(_DDL_V39_STRATEGY_BRIEF)


_DDL_V40_EXECUTION_CONTRACT = """
CREATE TABLE IF NOT EXISTS experiment_execution_contracts (
    id                              TEXT PRIMARY KEY,
    experiment_id                   TEXT NOT NULL UNIQUE
                                        REFERENCES experiments(id),
    brief_id                        TEXT NOT NULL
                                        REFERENCES experiment_strategy_briefs(id),
    idea_id                         INTEGER
                                        REFERENCES experiment_idea_candidates(id),
    channel_id                      INTEGER NOT NULL,
    opportunity_id                  INTEGER NOT NULL,
    canonical_cluster_id            INTEGER,

    execution_mode                  TEXT NOT NULL CHECK (execution_mode IN ('dry_run', 'real')),

    eligibility_recheck_result      TEXT,
    eligibility_blocked             INTEGER NOT NULL DEFAULT 0,

    treatment_factors_json          TEXT,
    control_factors_json            TEXT,
    narration_speaking_rate_override REAL,
    treatment_delta_valid           INTEGER NOT NULL DEFAULT 1,
    treatment_abs_valid             INTEGER NOT NULL DEFAULT 1,

    status                          TEXT NOT NULL DEFAULT 'pending'
                                        CHECK (status IN (
                                            'pending', 'approved', 'executing',
                                            'completed', 'failed', 'blocked'
                                        )),
    execution_policy_version        TEXT NOT NULL,

    fidelity_json                   TEXT,
    valid_for_learning              INTEGER,
    confounding_risk_realized       TEXT NOT NULL DEFAULT 'low',

    created_at                      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    approved_at                     TEXT,
    executed_at                     TEXT,
    completed_at                    TEXT
);
"""


def _apply_v40_execution_contract(conn: sqlite3.Connection) -> None:
    """Add experiment_execution_contracts table (Phase 14F)."""
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='experiment_execution_contracts'"
    ).fetchone():
        return
    conn.executescript(_DDL_V40_EXECUTION_CONTRACT)


_DDL_V41_EXPERIMENT_OUTCOMES = """
CREATE TABLE IF NOT EXISTS experiment_outcomes (
    id                      TEXT    PRIMARY KEY,
    experiment_id           TEXT    NOT NULL REFERENCES experiments(id),

    readiness               TEXT    NOT NULL
                                CHECK (readiness IN (
                                    'not_ready', 'invalid_execution', 'insufficient_analytics',
                                    'evaluable_provisional', 'evaluable_mature', 'unresolved'
                                )),
    classification          TEXT
                                CHECK (classification IS NULL OR classification IN (
                                    'positive_observation', 'negative_observation',
                                    'neutral_observation', 'inconclusive',
                                    'informational_only', 'baseline_unavailable'
                                )),
    evidence_maturity       TEXT
                                CHECK (evidence_maturity IS NULL OR evidence_maturity IN (
                                    'exploratory', 'directional', 'actionable'
                                )),

    baseline_source_type    TEXT    NOT NULL DEFAULT 'none'
                                CHECK (baseline_source_type IN (
                                    'channel_baseline', 'prior_experiment',
                                    'validation_reference', 'control_publication', 'none'
                                )),
    baseline_experiment_id  TEXT    REFERENCES experiments(id),

    target_metric_name      TEXT,
    target_metric_direction TEXT,

    treatment_metric_value  REAL,
    baseline_metric_value   REAL,
    absolute_delta          REAL,
    relative_delta          REAL,

    is_mature               INTEGER NOT NULL DEFAULT 0 CHECK (is_mature IN (0, 1)),
    publication_age_hours   REAL,
    observed_views          REAL,

    reasons_json            TEXT    NOT NULL DEFAULT '[]',
    warnings_json           TEXT    NOT NULL DEFAULT '[]',

    outcome_policy_version  TEXT    NOT NULL,
    input_hash              TEXT    NOT NULL,
    evaluated_at            TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_eo_experiment
    ON experiment_outcomes (experiment_id);
CREATE INDEX IF NOT EXISTS idx_eo_evaluated_at
    ON experiment_outcomes (evaluated_at);
"""


def _apply_v41_experiment_outcomes(conn: sqlite3.Connection) -> None:
    """Add experiment_outcomes table (Phase 14G)."""
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='experiment_outcomes'"
    ).fetchone():
        return
    conn.executescript(_DDL_V41_EXPERIMENT_OUTCOMES)


def _apply_v42_cp_channel_bridge(conn: sqlite3.Connection) -> None:
    """Add cp_channel_id bridge column to channels table (Phase 16B.1).

    Creates an explicit, machine-verifiable 1:1 mapping between the intelligence
    domain's integer-PK channels and the control-plane UUID-based cp_channels.
    The UNIQUE partial index enforces that at most one intelligence channel maps
    to each control-plane channel. Missing mapping fails loudly at lookup time.
    Idempotent — no-op if channels table absent (partial test DBs) or column exists.
    """
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='channels'"
    ).fetchone():
        return
    if conn.execute(
        "SELECT 1 FROM pragma_table_info('channels') WHERE name='cp_channel_id'"
    ).fetchone():
        return
    conn.execute("ALTER TABLE channels ADD COLUMN cp_channel_id TEXT REFERENCES cp_channels(id)")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_channels_cp_channel_id "
        "ON channels (cp_channel_id) WHERE cp_channel_id IS NOT NULL"
    )


def _apply_v49_scheduled_publishing(conn: sqlite3.Connection) -> None:
    """Channel publishing authorization + slot publish tracking (Phase 18C).

    Three deliberate design decisions encoded here:

    1. Channel authorization is its OWN table, not a column on
       autonomy_policies. Authorizing a channel to publish publicly without
       per-video approval is categorically different from configuring a
       cadence, and it must be impossible to flip it as a side effect of
       editing an unrelated policy field (Phase 18C section 6). A separate
       table with its own grant/revoke audit columns makes that structural
       rather than merely conventional.

    2. Publish state lives on publishing_slots, matching the Phase 18B
       precedent: publishing is a property of fulfilling a specific reserved
       slot, not an independent entity.

    3. publishing_upload_attempts exists solely to survive the one failure
       mode that can create a duplicate YouTube video: provider.upload()
       succeeding while the local write that records its provider_video_id
       fails. An intent row is committed BEFORE the provider call, so a
       crashed attempt is always discoverable afterwards and the next run
       reconciles instead of blindly re-uploading (section 13).

    Idempotent — safe to run against a database at any prior version.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS channel_publishing_authorizations (
            channel_id                  TEXT    PRIMARY KEY
                                            REFERENCES cp_channels(id),
            workspace_id                TEXT    NOT NULL REFERENCES cp_workspaces(id),

            authorized                  INTEGER NOT NULL DEFAULT 0
                                            CHECK (authorized IN (0, 1)),
            authorized_at               TEXT,
            authorized_by               TEXT,
            revoked_at                  TEXT,
            revoked_by                  TEXT,
            policy_version              INTEGER NOT NULL DEFAULT 1,

            max_publications_per_24h    INTEGER NOT NULL DEFAULT 1
                                            CHECK (max_publications_per_24h > 0),
            missed_slot_grace_minutes   INTEGER NOT NULL DEFAULT 120
                                            CHECK (missed_slot_grace_minutes >= 0),

            created_at                  TEXT    NOT NULL,
            updated_at                  TEXT    NOT NULL,

            -- An authorized row must always record who authorized it and when.
            -- This is the schema-level guarantee behind section 6's audit
            -- requirement: an anonymous authorization cannot be persisted.
            CHECK (authorized = 0 OR (authorized_at IS NOT NULL AND authorized_by IS NOT NULL))
        );

        CREATE TABLE IF NOT EXISTS publishing_upload_attempts (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            attempt_key         TEXT    NOT NULL UNIQUE,
            slot_id             INTEGER NOT NULL REFERENCES publishing_slots(id),
            publishing_plan_id  INTEGER NOT NULL,
            channel_id          TEXT    NOT NULL,
            workspace_id        TEXT    NOT NULL,

            state               TEXT    NOT NULL DEFAULT 'intent_recorded'
                                    CHECK (state IN (
                                        'intent_recorded', 'succeeded',
                                        'uncertain', 'failed', 'reconciled'
                                    )),
            provider            TEXT    NOT NULL,
            provider_video_id   TEXT,
            error_message       TEXT,

            created_at          TEXT    NOT NULL,
            resolved_at         TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_upload_attempts_slot
            ON publishing_upload_attempts (slot_id, state);
        """
    )

    slot_columns = {
        "publish_status": "TEXT CHECK (publish_status IN ("
        "'pending', 'publishing', 'uploaded', 'released', "
        "'failed', 'skipped_missed', 'blocked') "
        "OR publish_status IS NULL)",
        "publication_id": "INTEGER REFERENCES publications(id)",
        "publish_provider_video_id": "TEXT",
        "publish_started_at": "TEXT",
        "publish_uploaded_at": "TEXT",
        "publish_released_at": "TEXT",
        "publish_failed_at": "TEXT",
        "publish_failure_category": "TEXT",
        "publish_error": "TEXT",
        "publish_retry_count": "INTEGER NOT NULL DEFAULT 0",
        "rescheduled_from_slot_id": "INTEGER REFERENCES publishing_slots(id)",
    }
    existing_cols = {
        r["name"] for r in conn.execute("PRAGMA table_info('publishing_slots')").fetchall()
    }
    for col, ddl in slot_columns.items():
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE publishing_slots ADD COLUMN {col} {ddl}")

    conn.executescript(
        """
        -- One slot may only ever own one publication. This is the database-level
        -- reinforcement of slot→publication one-to-one lineage (section 15).
        CREATE UNIQUE INDEX IF NOT EXISTS idx_publishing_slots_publication
            ON publishing_slots (publication_id)
            WHERE publication_id IS NOT NULL;

        CREATE INDEX IF NOT EXISTS idx_publishing_slots_publish_status
            ON publishing_slots (channel_id, publish_status);
        """
    )
    conn.commit()


def _apply_v50_visual_quality(conn: sqlite3.Connection) -> None:
    """Visual Quality Intelligence (Phase 18E).

    Adds one canonical table, render_visual_assessments, plus the realized
    visual-composition columns on content_feature_snapshots that let Phase 12C
    cross-publication learning see what a video actually LOOKED like.

    Three design decisions encoded here:

    1. The assessment is keyed on the RENDER manifest, not the publication.
       A render is the artifact whose visual composition is being measured, it
       exists before anything is published, and preflight must be able to block
       a render that will never become a publication.  publication_id is a
       nullable backfill so learning can join without a second identity.

    2. Planned and realized composition are stored SEPARATELY
       (planned_meaningful_beats vs meaningful_beat_count, and the
       creative/provider split of fallback_beat_count).  A creative decision to
       show a diagram and a provider failure that forced a text card produce
       the same pixels; conflating them would teach the learner that the
       creative strategy caused the outcome.

    3. UNIQUE on render_manifest_id makes reassessment idempotent by
       construction: the same render always upserts one row, so a restart
       mid-preflight can never accumulate duplicate verdicts.

    Idempotent — safe to run against a database at any prior version.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS render_visual_assessments (
            id                          INTEGER PRIMARY KEY AUTOINCREMENT,

            -- Identity / lineage
            render_manifest_id          INTEGER NOT NULL
                                            REFERENCES render_manifests(id) ON DELETE CASCADE,
            scene_manifest_id           INTEGER NOT NULL,
            workspace_id                TEXT,
            channel_id                  TEXT,
            experiment_id               TEXT,
            publication_id              INTEGER REFERENCES publications(id),

            assessment_version          TEXT    NOT NULL,
            composition_version         TEXT    NOT NULL,
            policy_version              TEXT    NOT NULL,

            -- Verdict
            status                      TEXT    NOT NULL
                                            CHECK (status IN (
                                                'pass', 'pass_with_warnings', 'blocked'
                                            )),

            -- Scale
            total_beat_count            INTEGER NOT NULL DEFAULT 0,
            total_duration_ms           INTEGER NOT NULL DEFAULT 0,
            scene_count                 INTEGER NOT NULL DEFAULT 0,

            -- Realized composition
            meaningful_beat_count       INTEGER NOT NULL DEFAULT 0,
            meaningful_runtime_ms       INTEGER NOT NULL DEFAULT 0,
            text_card_beat_count        INTEGER NOT NULL DEFAULT 0,
            text_card_runtime_ms        INTEGER NOT NULL DEFAULT 0,
            unresolved_beat_count       INTEGER NOT NULL DEFAULT 0,
            family_runtime_json         TEXT    NOT NULL DEFAULT '{}',
            family_beat_count_json      TEXT    NOT NULL DEFAULT '{}',
            dominant_family             TEXT,
            dominant_family_share       REAL    NOT NULL DEFAULT 0.0,
            family_diversity            REAL    NOT NULL DEFAULT 0.0,

            -- Assets
            distinct_asset_count        INTEGER NOT NULL DEFAULT 0,
            reused_asset_beat_count     INTEGER NOT NULL DEFAULT 0,
            asset_reuse_ratio           REAL    NOT NULL DEFAULT 0.0,

            -- Cadence
            visual_change_count         INTEGER NOT NULL DEFAULT 0,
            visual_changes_per_minute   REAL    NOT NULL DEFAULT 0.0,
            avg_meaningful_gap_ms       REAL    NOT NULL DEFAULT 0.0,
            max_meaningful_gap_ms       INTEGER NOT NULL DEFAULT 0,
            opening_meaningful_visual   INTEGER NOT NULL DEFAULT 0
                                            CHECK (opening_meaningful_visual IN (0, 1)),

            -- Planned intent vs realized outcome
            visual_style                TEXT,
            planned_meaningful_beats    INTEGER NOT NULL DEFAULT 0,
            intentional_text_beats      INTEGER NOT NULL DEFAULT 0,
            fallback_beat_count         INTEGER NOT NULL DEFAULT 0,
            fallback_runtime_ms         INTEGER NOT NULL DEFAULT 0,
            provider_fallback_beats     INTEGER NOT NULL DEFAULT 0,
            creative_fallback_beats     INTEGER NOT NULL DEFAULT 0,
            provider_fallback_rate      REAL    NOT NULL DEFAULT 0.0,
            fallback_reasons_json       TEXT    NOT NULL DEFAULT '{}',

            -- Evidence
            findings_json               TEXT    NOT NULL DEFAULT '[]',
            scene_diagnostics_json      TEXT    NOT NULL DEFAULT '[]',

            -- Bounded remediation bookkeeping
            remediation_attempts        INTEGER NOT NULL DEFAULT 0,
            remediated                  INTEGER NOT NULL DEFAULT 0
                                            CHECK (remediated IN (0, 1)),

            input_hash                  TEXT    NOT NULL,
            created_at                  TEXT    NOT NULL,
            updated_at                  TEXT    NOT NULL,

            UNIQUE (render_manifest_id)
        );

        CREATE INDEX IF NOT EXISTS idx_visual_assessment_channel
            ON render_visual_assessments (channel_id, status);
        CREATE INDEX IF NOT EXISTS idx_visual_assessment_publication
            ON render_visual_assessments (publication_id)
            WHERE publication_id IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_visual_assessment_scene_manifest
            ON render_visual_assessments (scene_manifest_id);
        """
    )

    # Realized visual composition as learnable content features. Nullable with
    # no default: a publication produced before this phase has no visual
    # assessment, and NULL ("we do not know") must never be read as 0.0
    # ("it had no meaningful visuals").
    feature_columns = {
        "visual_style": "TEXT",
        "visual_quality_status": "TEXT",
        "visual_meaningful_runtime_pct": "REAL",
        "visual_text_card_runtime_pct": "REAL",
        "visual_generated_diagram_runtime_pct": "REAL",
        "visual_retrieved_imagery_runtime_pct": "REAL",
        "visual_changes_per_minute": "REAL",
        "visual_max_meaningful_gap_s": "REAL",
        "visual_distinct_assets": "INTEGER",
        "visual_asset_reuse_ratio": "REAL",
        "visual_dominant_family": "TEXT",
        "visual_opening_meaningful": "INTEGER",
        "visual_provider_fallback_rate": "REAL",
    }
    existing = {
        r["name"] for r in conn.execute("PRAGMA table_info('content_feature_snapshots')").fetchall()
    }
    if existing:
        for col, ddl in feature_columns.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE content_feature_snapshots ADD COLUMN {col} {ddl}")

    conn.commit()


def _apply_v51_slot_retirement(conn: sqlite3.Connection) -> None:
    """Terminal retirement for deterministically unpublishable slots (Phase 18E).

    Phase 18E's visual-quality gate created a queue deadlock: a blocked render
    reaches publish_status='failed', 'failed' is not in
    TERMINAL_PUBLISH_STATUSES, so list_active_slots kept counting it and a
    channel with queue_target=1 could never queue anything again. That is
    Phase 18D defect #4 re-entered through a new door.

    Retirement is modelled as its OWN column rather than a seventh value of
    publish_status, for two reasons:

    1. It is a different axis. publish_status tracks progress toward being
       published; retirement records that progress stopped permanently and
       why. A slot can be retired without ever having progressed anywhere,
       and overloading a progress enum to say that muddles both.

    2. publish_status carries a CHECK constraint, and SQLite cannot alter one
       in place — extending it means rebuilding publishing_slots, the table
       holding all live publishing state, complete with its self-referencing
       FK and partial unique index. An additive nullable column achieves the
       same semantics with none of that risk on a live operational database.

    Terminality stays defined in exactly one place: _NOT_TERMINAL_SQL in
    app.intelligence.autonomy.repository now tests `retired_at IS NULL`
    alongside the terminal status list, so every queue and eligibility query
    picks it up together and none can be updated in isolation.

    Idempotent — safe to run against a database at any prior version.
    """
    columns = {
        "retired_at": "TEXT",
        "retirement_reason": "TEXT",
    }
    existing = {r["name"] for r in conn.execute("PRAGMA table_info('publishing_slots')").fetchall()}
    if not existing:
        return
    for col, ddl in columns.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE publishing_slots ADD COLUMN {col} {ddl}")

    conn.executescript(
        """
        -- Retired slots are excluded from every queue/eligibility scan, so the
        -- partial index keeps those scans cheap as retirements accumulate.
        CREATE INDEX IF NOT EXISTS idx_publishing_slots_retired
            ON publishing_slots (channel_id, retired_at)
            WHERE retired_at IS NOT NULL;
        """
    )

    # Phase 18E — topic specificity / visual groundability, cached alongside
    # the semantic-fit result it is produced with.
    #
    # These live on the SAME row, not a new table, because they come from the
    # same structured LLM response about the same opportunity. Splitting them
    # would mean two cache keys for one call and a way for the two halves to
    # disagree about which evaluation they came from.
    #
    # Nullable with no default: a row cached under prompt v1 has no specificity
    # answer, and NULL ("not evaluated") must not read as 0.0 ("a category").
    fit_columns = {
        "topic_specificity": "REAL",
        "specificity_label": "TEXT",
        "visual_groundability": "REAL",
        "concrete_subjects_json": "TEXT",
        "viewer_promise": "TEXT",
        "refined_topic": "TEXT",
    }
    fit_existing = {
        r["name"]
        for r in conn.execute("PRAGMA table_info('opportunity_semantic_fit_results')").fetchall()
    }
    if fit_existing:
        for col, ddl in fit_columns.items():
            if col not in fit_existing:
                conn.execute(f"ALTER TABLE opportunity_semantic_fit_results ADD COLUMN {col} {ddl}")

    conn.commit()


def _apply_v48_autonomous_production(conn: sqlite3.Connection) -> None:
    """Autonomous production tracking on publishing_slots (Phase 18B).

    Adds production_automation_enabled to autonomy_policies — a THIRD,
    independent control alongside decision_automation_enabled and the
    (unrelated, env-var-only) publishing gates. Enabling it only permits
    spending resources to generate media artifacts; it grants no upload
    authority whatsoever — that remains structurally impossible from this
    code path (see app.intelligence.autonomy.production_cycle's module
    docstring).

    Adds production state directly to publishing_slots rather than a new
    table: production is a property of filling a specific slot, not an
    independent entity, and this keeps the one-slot-one-production
    invariant trivially enforceable (no second FK to reconcile).

    production_status is intentionally nullable (no default) — NULL means
    "production not yet started for this slot", distinct from any of the
    four real in-flight/terminal states.

    Idempotent — safe to run against a database at any prior version.
    """
    if not conn.execute(
        "SELECT 1 FROM pragma_table_info('autonomy_policies') "
        "WHERE name='production_automation_enabled'"
    ).fetchone():
        conn.execute(
            "ALTER TABLE autonomy_policies ADD COLUMN production_automation_enabled "
            "INTEGER NOT NULL DEFAULT 0 CHECK (production_automation_enabled IN (0, 1))"
        )

    slot_columns = {
        "experiment_id": "TEXT REFERENCES experiments(id)",
        "production_status": "TEXT CHECK (production_status IN ("
        "'queued', 'producing', 'ready', 'failed') OR production_status IS NULL)",
        "production_pipeline_id": "TEXT",
        "production_publishing_plan_id": "INTEGER",
        "production_started_at": "TEXT",
        "production_ready_at": "TEXT",
        "production_failed_at": "TEXT",
        "production_failed_stage": "TEXT",
        "production_error": "TEXT",
        "production_retry_count": "INTEGER NOT NULL DEFAULT 0",
    }
    existing_cols = {
        r["name"] for r in conn.execute("PRAGMA table_info('publishing_slots')").fetchall()
    }
    for col, ddl in slot_columns.items():
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE publishing_slots ADD COLUMN {col} {ddl}")

    conn.executescript(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_publishing_slots_experiment
            ON publishing_slots (experiment_id)
            WHERE experiment_id IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_publishing_slots_production_status
            ON publishing_slots (channel_id, production_status);
        """
    )
    conn.commit()


def _apply_v47_autonomy_orchestrator(conn: sqlite3.Connection) -> None:
    """Autonomy decision cycle: per-channel policy + publishing slot reservations (Phase 18A).

    autonomy_policies: one mutable row per channel (like analytics_observation_state,
    not versioned like cp_strategy_profiles — this is an operator toggle/setting, not
    an audited strategy decision). decision_automation_enabled cannot be true without
    an explicit timezone — enforced at the schema level so no code path can silently
    activate a channel's live cadence on a guessed timezone.

    publishing_slots: a future publish-time reservation, deliberately upstream of any
    `experiments` row (Phase 14E's own design keeps Experiment creation "downstream,
    human-gated") — a slot's reserved content is represented by an
    experiment_strategy_briefs.id, the existing artifact for "a selected, concrete,
    market-grounded idea," not yet a committed Experiment. One slot per (channel,
    slot_key) — UNIQUE constraint. One active slot per brief — partial UNIQUE index.

    Idempotent — safe to run against a database at any prior version.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS autonomy_policies (
            channel_id                          TEXT    PRIMARY KEY REFERENCES cp_channels(id),
            workspace_id                         TEXT    NOT NULL REFERENCES cp_workspaces(id),
            decision_automation_enabled          INTEGER NOT NULL DEFAULT 0
                                                        CHECK (decision_automation_enabled IN
                                                            (0, 1)),
            cadence_type                         TEXT    NOT NULL DEFAULT 'daily'
                                                        CHECK (cadence_type IN (
                                                            'every_12h', 'daily', 'every_n_days',
                                                            'weekly', 'custom_cron'
                                                        )),
            cadence_interval_days                INTEGER,
            cadence_cron                         TEXT,
            preferred_local_hour                 INTEGER NOT NULL DEFAULT 9
                                                        CHECK (preferred_local_hour
                                                            BETWEEN 0 AND 23),
            timezone                             TEXT,
            queue_target                         INTEGER NOT NULL DEFAULT 1
                                                        CHECK (queue_target BETWEEN 1 AND 2),
            market_refresh_max_age_hours         INTEGER NOT NULL DEFAULT 12,
            semantic_fit_max_evaluations_per_run INTEGER NOT NULL DEFAULT 5,
            last_decision_at                     TEXT,
            last_decision_outcome                TEXT,
            actor                                TEXT    NOT NULL,
            created_at                           TEXT    NOT NULL,
            updated_at                           TEXT    NOT NULL,
            CHECK (decision_automation_enabled = 0 OR timezone IS NOT NULL)
        );

        CREATE TABLE IF NOT EXISTS publishing_slots (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id              TEXT    NOT NULL REFERENCES cp_channels(id),
            workspace_id            TEXT    NOT NULL REFERENCES cp_workspaces(id),
            slot_key                TEXT    NOT NULL,
            scheduled_for_local     TEXT    NOT NULL,
            timezone                TEXT    NOT NULL,
            scheduled_for_utc       TEXT    NOT NULL,
            state                   TEXT    NOT NULL DEFAULT 'reserved'
                                            CHECK (state IN (
                                                'reserved', 'filled', 'cancelled', 'expired'
                                            )),
            brief_id                TEXT    REFERENCES experiment_strategy_briefs(id),
            selection_decision_id   INTEGER REFERENCES experiment_selection_decisions(id),
            opportunity_id          INTEGER,
            reserved_at             TEXT    NOT NULL,
            filled_at               TEXT,
            cancelled_at            TEXT,
            cancellation_reason     TEXT,
            created_at              TEXT    NOT NULL,
            updated_at              TEXT    NOT NULL,
            UNIQUE (channel_id, slot_key)
        );
        CREATE INDEX IF NOT EXISTS idx_publishing_slots_channel_state
            ON publishing_slots (channel_id, state, scheduled_for_utc);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_publishing_slots_brief_active
            ON publishing_slots (brief_id)
            WHERE brief_id IS NOT NULL AND state IN ('reserved', 'filled');
        """
    )
    conn.commit()


def _apply_v46_semantic_fit_cache(conn: sqlite3.Connection) -> None:
    """Persisted semantic-fit evaluation cache (Phase 17G).

    Caches the result of a real LLM semantic-fit call (assess_semantic_fit)
    keyed by a deterministic input_hash over the opportunity content and the
    channel profile version in effect at evaluation time. A new profile
    version (channel constraints changed) or different opportunity content
    naturally produces a different hash, so the cache self-invalidates
    without any explicit invalidation logic.

    Only successful evaluations are persisted — a failed/timed-out LLM call
    is never cached, so it keeps resolving to UNRESOLVED on retry rather than
    silently freezing into a wrong answer.

    Append-only: a superseded hash's row is left in place as audit history,
    never deleted or updated. Idempotent — safe to run at any prior version.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS opportunity_semantic_fit_results (
            id                          INTEGER PRIMARY KEY AUTOINCREMENT,
            opportunity_id              INTEGER NOT NULL,
            channel_id                  INTEGER NOT NULL,
            channel_profile_version_id  INTEGER,
            prompt_version              TEXT    NOT NULL,
            input_hash                  TEXT    NOT NULL,
            score                       REAL    NOT NULL,
            fit_label                   TEXT    NOT NULL DEFAULT '',
            rationale                   TEXT    NOT NULL DEFAULT '',
            provider_name               TEXT    NOT NULL,
            model                       TEXT    NOT NULL DEFAULT '',
            evaluated_at                TEXT    NOT NULL,
            UNIQUE (opportunity_id, input_hash)
        );

        CREATE INDEX IF NOT EXISTS idx_semantic_fit_opportunity
            ON opportunity_semantic_fit_results (opportunity_id, input_hash);
        CREATE INDEX IF NOT EXISTS idx_semantic_fit_channel
            ON opportunity_semantic_fit_results (channel_id);
        """
    )
    conn.commit()


def _apply_v45_analytics_observation_state(conn: sqlite3.Connection) -> None:
    """Per-publication observation lifecycle state (Phase 16D.4).

    Tracks scheduling, outcome state, and retry counters for automatic
    analytics observation. One row per publication_id; keyed separately from
    app_schedule_definitions so schedule cadence and observation outcome state
    have clean separation.

    Idempotent — safe to run against a database at any prior version.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS analytics_observation_state (
            publication_id          INTEGER PRIMARY KEY,
            workspace_id            TEXT    NOT NULL,
            channel_id              TEXT,
            platform_account_id     TEXT,
            schedule_id             TEXT,
            observation_status      TEXT    NOT NULL DEFAULT 'active'
                                            CHECK (observation_status IN (
                                                'active', 'inactive', 'paused'
                                            )),
            last_attempted_at       TEXT,
            last_success_at         TEXT,
            latest_snapshot_id      INTEGER,
            retention_acquired      INTEGER NOT NULL DEFAULT 0,
            consecutive_no_data     INTEGER NOT NULL DEFAULT 0,
            failure_count           INTEGER NOT NULL DEFAULT 0,
            created_at              TEXT    NOT NULL,
            updated_at              TEXT    NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_obs_state_status
            ON analytics_observation_state (observation_status, publication_id);
        """
    )
    conn.commit()


def _apply_v44_visual_intelligence(conn: sqlite3.Connection) -> None:
    """Semantic visual beats + channel-aware asset memory (Phase 16D.3.2).

    Two tables:

    visual_beats
        The persisted visual plan.  A beat is strictly narrower than a scene:
        the scene owns the narration segment and its audio asset, the beat owns
        a stretch of the visual track inside it.  Keeping beats in their own
        table means visuals can be re-planned without touching narration,
        caption, or render lineage.

    visual_asset_usage
        Deterministic answer to "has this asset been used before, on which
        channel, how recently, and for how long".  Keyed on the provider-scoped
        asset identity so a query collision can never merge two assets.

    Idempotent — safe to run against a database at any prior version.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS visual_beats (
            id                          INTEGER PRIMARY KEY AUTOINCREMENT,
            scene_manifest_id           INTEGER NOT NULL,
            scene_id                    INTEGER,
            beat_index                  INTEGER NOT NULL,
            scene_index                 INTEGER NOT NULL,
            segment_id                  INTEGER NOT NULL,
            start_ms                    INTEGER NOT NULL,
            end_ms                      INTEGER NOT NULL,
            duration_ms                 INTEGER NOT NULL,
            narration_text              TEXT NOT NULL DEFAULT '',
            keywords_json               TEXT NOT NULL DEFAULT '[]',
            entities_json               TEXT NOT NULL DEFAULT '[]',
            visual_intent               TEXT NOT NULL,
            media_type_preferences_json TEXT NOT NULL DEFAULT '[]',
            search_queries_json         TEXT NOT NULL DEFAULT '[]',
            avoid_terms_json            TEXT NOT NULL DEFAULT '[]',
            claim_ids_json              TEXT NOT NULL DEFAULT '[]',
            preferred_motion            TEXT NOT NULL DEFAULT 'none',
            importance                  REAL NOT NULL DEFAULT 0.5,
            confidence                  REAL NOT NULL DEFAULT 0.5,
            resolved_media_type         TEXT,
            resolved_provider           TEXT,
            resolved_asset_key          TEXT,
            resolved_local_path         TEXT,
            resolved_score              REAL,
            resolved_motion             TEXT,
            license_status              TEXT,
            attribution_text            TEXT,
            fallback_reason             TEXT,
            engine_version              TEXT NOT NULL,
            planner_version             TEXT NOT NULL,
            created_at                  TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE (scene_manifest_id, beat_index),
            FOREIGN KEY (scene_manifest_id) REFERENCES scene_manifests (id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_visual_beats_manifest
            ON visual_beats (scene_manifest_id, beat_index);
        CREATE INDEX IF NOT EXISTS idx_visual_beats_asset
            ON visual_beats (resolved_asset_key);

        CREATE TABLE IF NOT EXISTS visual_asset_usage (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_key          TEXT NOT NULL,
            provider           TEXT NOT NULL,
            provider_asset_id  TEXT NOT NULL,
            media_type         TEXT NOT NULL,
            channel_key        TEXT,
            workspace_id       TEXT,
            topic_id           INTEGER,
            experiment_id      TEXT,
            scene_manifest_id  INTEGER,
            render_manifest_id INTEGER,
            publication_id     INTEGER,
            beat_index         INTEGER,
            scene_index        INTEGER,
            duration_ms        INTEGER NOT NULL DEFAULT 0,
            used_at            TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_visual_usage_channel_asset
            ON visual_asset_usage (channel_key, asset_key, used_at);
        CREATE INDEX IF NOT EXISTS idx_visual_usage_asset
            ON visual_asset_usage (asset_key, used_at);
        CREATE INDEX IF NOT EXISTS idx_visual_usage_manifest
            ON visual_asset_usage (scene_manifest_id);
        """
    )


def _apply_v43_eligibility_provenance(conn: sqlite3.Connection) -> None:
    """Add semantic_fit_disposition column to experiment_candidate_scores (Phase 16D.1.1).

    Records HOW the semantic fit score was obtained — "provider_called" (real Anthropic),
    "replay_prior_real_call" (ReplayEligibilityProvider), "fake_provider_test" (FakeProvider),
    "deterministic_bypass", "skipped_hard_block", or "provider_unavailable_unresolved".

    Idempotent — no-op if the table does not exist or the column is already present.
    """
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='experiment_candidate_scores'"
    ).fetchone():
        return
    if conn.execute(
        "SELECT 1 FROM pragma_table_info('experiment_candidate_scores') "
        "WHERE name='semantic_fit_disposition'"
    ).fetchone():
        return
    conn.execute("ALTER TABLE experiment_candidate_scores ADD COLUMN semantic_fit_disposition TEXT")


def _apply_v36_opportunities_topic_dedup_partial(conn: sqlite3.Connection) -> None:
    """Replace the table-level UNIQUE(channel_id, normalized_topic) with a partial index.

    Identity hierarchy after v36:
      canonical Opportunities (canonical_cluster_id IS NOT NULL):
        primary identity = (channel_id, canonical_cluster_id)
          [enforced by uq_opps_channel_canonical]
        normalized_topic = display label only; multiple canonical rows may share a topic
      legacy Opportunities (canonical_cluster_id IS NULL):
        primary identity = normalized_topic
          [enforced by uq_opps_channel_topic_legacy, active rows only]
        terminal (rejected/archived) legacy rows do not block replacement

    The old table-level UNIQUE(channel_id, normalized_topic) covered ALL rows regardless of
    lifecycle state or canonical identity, blocking:
      - rediscovery of a canonical cluster whose old Opportunity was rejected/archived
      - two different canonical clusters that share a label (distinct canonical identities)
      - a new active replacement with the same topic as a terminal legacy Opportunity

    Requires table recreation (SQLite cannot drop inline UNIQUE constraints via ALTER TABLE).
    Uses the same PRAGMA foreign_keys = OFF/ON pattern established in _apply_v34_market_bridge.

    Idempotency: if no SQLite autoindex exists on opportunities (the autoindex is created by the
    inline UNIQUE in the DDL), the table was already rebuilt. The function still ensures both
    partial indexes exist via IF NOT EXISTS and returns.
    Guard: no-op if the opportunities table does not exist.
    """
    opp_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='opportunities'"
    ).fetchone()
    if not opp_exists:
        return

    autoindex = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='index' AND tbl_name='opportunities' AND name LIKE 'sqlite_autoindex%'"
    ).fetchone()

    if autoindex is None:
        # Already rebuilt (or created fresh without inline UNIQUE). Ensure indexes exist.
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_opps_channel_canonical "
            "ON opportunities(channel_id, canonical_cluster_id) "
            "WHERE canonical_cluster_id IS NOT NULL "
            "  AND current_lifecycle_state NOT IN ('rejected', 'archived')"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_opps_channel_topic_legacy "
            "ON opportunities(channel_id, normalized_topic) "
            "WHERE canonical_cluster_id IS NULL "
            "  AND current_lifecycle_state NOT IN ('rejected', 'archived')"
        )
        return

    # Table rebuild: remove inline UNIQUE, preserve all columns and data.
    conn.executescript("""
PRAGMA foreign_keys = OFF;

CREATE TABLE opportunities_v36_new (
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
    canonical_cluster_id    INTEGER REFERENCES market_canonical_clusters(id),
    market_signal_snapshot_id INTEGER
);

INSERT INTO opportunities_v36_new
    SELECT id, channel_id, discovery_run_id, normalized_topic, raw_topic,
           title, topic_summary, format_recommendation, strategic_role,
           current_lifecycle_state, created_at, updated_at,
           canonical_cluster_id, market_signal_snapshot_id
    FROM opportunities;

DROP TABLE opportunities;
ALTER TABLE opportunities_v36_new RENAME TO opportunities;

PRAGMA foreign_keys = ON;
""")

    # Recreate partial indexes on the new table.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_opps_channel_canonical "
        "ON opportunities(channel_id, canonical_cluster_id) "
        "WHERE canonical_cluster_id IS NOT NULL "
        "  AND current_lifecycle_state NOT IN ('rejected', 'archived')"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_opps_channel_topic_legacy "
        "ON opportunities(channel_id, normalized_topic) "
        "WHERE canonical_cluster_id IS NULL "
        "  AND current_lifecycle_state NOT IN ('rejected', 'archived')"
    )


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
        _apply_v24_analytics_observation(conn)
        _apply_v25_generator_skipped_status(conn)
        _apply_v26_recommendation_applications(conn)
        _apply_v27_content_features(conn)
        _apply_v28_cross_pub_learning(conn)
        _apply_v29_market_intelligence(conn)
        _apply_v30_velocity(conn)
        _apply_v31_exploration(conn)
        _apply_v32_interpretation(conn)
        _apply_v33_canonical_clusters(conn)
        _apply_v34_market_bridge(conn)
        _apply_v35_active_opportunity_identity(conn)
        _apply_v36_opportunities_topic_dedup_partial(conn)
        _apply_v37_experiment_ledger(conn)
        _apply_v38_experiment_planning(conn)
        _apply_v39_strategy_brief(conn)
        _apply_v40_execution_contract(conn)
        _apply_v41_experiment_outcomes(conn)
        _apply_v42_cp_channel_bridge(conn)
        _apply_v43_eligibility_provenance(conn)
        _apply_v44_visual_intelligence(conn)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
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
        _apply_v24_analytics_observation(conn)
        _apply_v25_generator_skipped_status(conn)
        _apply_v26_recommendation_applications(conn)
        _apply_v27_content_features(conn)
        _apply_v28_cross_pub_learning(conn)
        _apply_v29_market_intelligence(conn)
        _apply_v30_velocity(conn)
        _apply_v31_exploration(conn)
        _apply_v32_interpretation(conn)
        _apply_v33_canonical_clusters(conn)
        _apply_v34_market_bridge(conn)
        _apply_v35_active_opportunity_identity(conn)
        _apply_v36_opportunities_topic_dedup_partial(conn)
        _apply_v37_experiment_ledger(conn)
        _apply_v38_experiment_planning(conn)
        _apply_v39_strategy_brief(conn)
        _apply_v40_execution_contract(conn)
        _apply_v41_experiment_outcomes(conn)
        _apply_v42_cp_channel_bridge(conn)
        _apply_v43_eligibility_provenance(conn)
        _apply_v44_visual_intelligence(conn)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
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
        _apply_v24_analytics_observation(conn)
        _apply_v25_generator_skipped_status(conn)
        _apply_v26_recommendation_applications(conn)
        _apply_v27_content_features(conn)
        _apply_v28_cross_pub_learning(conn)
        _apply_v29_market_intelligence(conn)
        _apply_v30_velocity(conn)
        _apply_v31_exploration(conn)
        _apply_v32_interpretation(conn)
        _apply_v33_canonical_clusters(conn)
        _apply_v34_market_bridge(conn)
        _apply_v35_active_opportunity_identity(conn)
        _apply_v36_opportunities_topic_dedup_partial(conn)
        _apply_v37_experiment_ledger(conn)
        _apply_v38_experiment_planning(conn)
        _apply_v39_strategy_brief(conn)
        _apply_v40_execution_contract(conn)
        _apply_v41_experiment_outcomes(conn)
        _apply_v42_cp_channel_bridge(conn)
        _apply_v43_eligibility_provenance(conn)
        _apply_v44_visual_intelligence(conn)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
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
        _apply_v24_analytics_observation(conn)
        _apply_v25_generator_skipped_status(conn)
        _apply_v26_recommendation_applications(conn)
        _apply_v27_content_features(conn)
        _apply_v28_cross_pub_learning(conn)
        _apply_v29_market_intelligence(conn)
        _apply_v30_velocity(conn)
        _apply_v31_exploration(conn)
        _apply_v32_interpretation(conn)
        _apply_v33_canonical_clusters(conn)
        _apply_v34_market_bridge(conn)
        _apply_v35_active_opportunity_identity(conn)
        _apply_v36_opportunities_topic_dedup_partial(conn)
        _apply_v37_experiment_ledger(conn)
        _apply_v38_experiment_planning(conn)
        _apply_v39_strategy_brief(conn)
        _apply_v40_execution_contract(conn)
        _apply_v41_experiment_outcomes(conn)
        _apply_v42_cp_channel_bridge(conn)
        _apply_v43_eligibility_provenance(conn)
        _apply_v44_visual_intelligence(conn)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
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
        _apply_v24_analytics_observation(conn)
        _apply_v25_generator_skipped_status(conn)
        _apply_v26_recommendation_applications(conn)
        _apply_v27_content_features(conn)
        _apply_v28_cross_pub_learning(conn)
        _apply_v29_market_intelligence(conn)
        _apply_v30_velocity(conn)
        _apply_v31_exploration(conn)
        _apply_v32_interpretation(conn)
        _apply_v33_canonical_clusters(conn)
        _apply_v34_market_bridge(conn)
        _apply_v35_active_opportunity_identity(conn)
        _apply_v36_opportunities_topic_dedup_partial(conn)
        _apply_v37_experiment_ledger(conn)
        _apply_v38_experiment_planning(conn)
        _apply_v39_strategy_brief(conn)
        _apply_v40_execution_contract(conn)
        _apply_v41_experiment_outcomes(conn)
        _apply_v42_cp_channel_bridge(conn)
        _apply_v43_eligibility_provenance(conn)
        _apply_v44_visual_intelligence(conn)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
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
        _apply_v24_analytics_observation(conn)
        _apply_v25_generator_skipped_status(conn)
        _apply_v26_recommendation_applications(conn)
        _apply_v27_content_features(conn)
        _apply_v28_cross_pub_learning(conn)
        _apply_v29_market_intelligence(conn)
        _apply_v30_velocity(conn)
        _apply_v31_exploration(conn)
        _apply_v32_interpretation(conn)
        _apply_v33_canonical_clusters(conn)
        _apply_v34_market_bridge(conn)
        _apply_v35_active_opportunity_identity(conn)
        _apply_v36_opportunities_topic_dedup_partial(conn)
        _apply_v37_experiment_ledger(conn)
        _apply_v38_experiment_planning(conn)
        _apply_v39_strategy_brief(conn)
        _apply_v40_execution_contract(conn)
        _apply_v41_experiment_outcomes(conn)
        _apply_v42_cp_channel_bridge(conn)
        _apply_v43_eligibility_provenance(conn)
        _apply_v44_visual_intelligence(conn)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
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
        _apply_v24_analytics_observation(conn)
        _apply_v25_generator_skipped_status(conn)
        _apply_v26_recommendation_applications(conn)
        _apply_v27_content_features(conn)
        _apply_v28_cross_pub_learning(conn)
        _apply_v29_market_intelligence(conn)
        _apply_v30_velocity(conn)
        _apply_v31_exploration(conn)
        _apply_v32_interpretation(conn)
        _apply_v33_canonical_clusters(conn)
        _apply_v34_market_bridge(conn)
        _apply_v35_active_opportunity_identity(conn)
        _apply_v36_opportunities_topic_dedup_partial(conn)
        _apply_v37_experiment_ledger(conn)
        _apply_v38_experiment_planning(conn)
        _apply_v39_strategy_brief(conn)
        _apply_v40_execution_contract(conn)
        _apply_v41_experiment_outcomes(conn)
        _apply_v42_cp_channel_bridge(conn)
        _apply_v43_eligibility_provenance(conn)
        _apply_v44_visual_intelligence(conn)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
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
        _apply_v24_analytics_observation(conn)
        _apply_v25_generator_skipped_status(conn)
        _apply_v26_recommendation_applications(conn)
        _apply_v27_content_features(conn)
        _apply_v28_cross_pub_learning(conn)
        _apply_v29_market_intelligence(conn)
        _apply_v30_velocity(conn)
        _apply_v31_exploration(conn)
        _apply_v32_interpretation(conn)
        _apply_v33_canonical_clusters(conn)
        _apply_v34_market_bridge(conn)
        _apply_v35_active_opportunity_identity(conn)
        _apply_v36_opportunities_topic_dedup_partial(conn)
        _apply_v37_experiment_ledger(conn)
        _apply_v38_experiment_planning(conn)
        _apply_v39_strategy_brief(conn)
        _apply_v40_execution_contract(conn)
        _apply_v41_experiment_outcomes(conn)
        _apply_v42_cp_channel_bridge(conn)
        _apply_v43_eligibility_provenance(conn)
        _apply_v44_visual_intelligence(conn)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
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
        _apply_v24_analytics_observation(conn)
        _apply_v25_generator_skipped_status(conn)
        _apply_v26_recommendation_applications(conn)
        _apply_v27_content_features(conn)
        _apply_v28_cross_pub_learning(conn)
        _apply_v29_market_intelligence(conn)
        _apply_v30_velocity(conn)
        _apply_v31_exploration(conn)
        _apply_v32_interpretation(conn)
        _apply_v33_canonical_clusters(conn)
        _apply_v34_market_bridge(conn)
        _apply_v35_active_opportunity_identity(conn)
        _apply_v36_opportunities_topic_dedup_partial(conn)
        _apply_v37_experiment_ledger(conn)
        _apply_v38_experiment_planning(conn)
        _apply_v39_strategy_brief(conn)
        _apply_v40_execution_contract(conn)
        _apply_v41_experiment_outcomes(conn)
        _apply_v42_cp_channel_bridge(conn)
        _apply_v43_eligibility_provenance(conn)
        _apply_v44_visual_intelligence(conn)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
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
        _apply_v24_analytics_observation(conn)
        _apply_v25_generator_skipped_status(conn)
        _apply_v26_recommendation_applications(conn)
        _apply_v27_content_features(conn)
        _apply_v28_cross_pub_learning(conn)
        _apply_v29_market_intelligence(conn)
        _apply_v30_velocity(conn)
        _apply_v31_exploration(conn)
        _apply_v32_interpretation(conn)
        _apply_v33_canonical_clusters(conn)
        _apply_v34_market_bridge(conn)
        _apply_v35_active_opportunity_identity(conn)
        _apply_v36_opportunities_topic_dedup_partial(conn)
        _apply_v37_experiment_ledger(conn)
        _apply_v38_experiment_planning(conn)
        _apply_v39_strategy_brief(conn)
        _apply_v40_execution_contract(conn)
        _apply_v41_experiment_outcomes(conn)
        _apply_v42_cp_channel_bridge(conn)
        _apply_v43_eligibility_provenance(conn)
        _apply_v44_visual_intelligence(conn)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
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
        _apply_v24_analytics_observation(conn)
        _apply_v25_generator_skipped_status(conn)
        _apply_v26_recommendation_applications(conn)
        _apply_v27_content_features(conn)
        _apply_v28_cross_pub_learning(conn)
        _apply_v29_market_intelligence(conn)
        _apply_v30_velocity(conn)
        _apply_v31_exploration(conn)
        _apply_v32_interpretation(conn)
        _apply_v33_canonical_clusters(conn)
        _apply_v34_market_bridge(conn)
        _apply_v35_active_opportunity_identity(conn)
        _apply_v36_opportunities_topic_dedup_partial(conn)
        _apply_v37_experiment_ledger(conn)
        _apply_v38_experiment_planning(conn)
        _apply_v39_strategy_brief(conn)
        _apply_v40_execution_contract(conn)
        _apply_v41_experiment_outcomes(conn)
        _apply_v42_cp_channel_bridge(conn)
        _apply_v43_eligibility_provenance(conn)
        _apply_v44_visual_intelligence(conn)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
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
        _apply_v24_analytics_observation(conn)
        _apply_v25_generator_skipped_status(conn)
        _apply_v26_recommendation_applications(conn)
        _apply_v27_content_features(conn)
        _apply_v28_cross_pub_learning(conn)
        _apply_v29_market_intelligence(conn)
        _apply_v30_velocity(conn)
        _apply_v31_exploration(conn)
        _apply_v32_interpretation(conn)
        _apply_v33_canonical_clusters(conn)
        _apply_v34_market_bridge(conn)
        _apply_v35_active_opportunity_identity(conn)
        _apply_v36_opportunities_topic_dedup_partial(conn)
        _apply_v37_experiment_ledger(conn)
        _apply_v38_experiment_planning(conn)
        _apply_v39_strategy_brief(conn)
        _apply_v40_execution_contract(conn)
        _apply_v41_experiment_outcomes(conn)
        _apply_v42_cp_channel_bridge(conn)
        _apply_v43_eligibility_provenance(conn)
        _apply_v44_visual_intelligence(conn)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
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
        _apply_v24_analytics_observation(conn)
        _apply_v25_generator_skipped_status(conn)
        _apply_v26_recommendation_applications(conn)
        _apply_v27_content_features(conn)
        _apply_v28_cross_pub_learning(conn)
        _apply_v29_market_intelligence(conn)
        _apply_v30_velocity(conn)
        _apply_v31_exploration(conn)
        _apply_v32_interpretation(conn)
        _apply_v33_canonical_clusters(conn)
        _apply_v34_market_bridge(conn)
        _apply_v35_active_opportunity_identity(conn)
        _apply_v36_opportunities_topic_dedup_partial(conn)
        _apply_v37_experiment_ledger(conn)
        _apply_v38_experiment_planning(conn)
        _apply_v39_strategy_brief(conn)
        _apply_v40_execution_contract(conn)
        _apply_v41_experiment_outcomes(conn)
        _apply_v42_cp_channel_bridge(conn)
        _apply_v43_eligibility_provenance(conn)
        _apply_v44_visual_intelligence(conn)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
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
        _apply_v24_analytics_observation(conn)
        _apply_v25_generator_skipped_status(conn)
        _apply_v26_recommendation_applications(conn)
        _apply_v27_content_features(conn)
        _apply_v28_cross_pub_learning(conn)
        _apply_v29_market_intelligence(conn)
        _apply_v30_velocity(conn)
        _apply_v31_exploration(conn)
        _apply_v32_interpretation(conn)
        _apply_v33_canonical_clusters(conn)
        _apply_v34_market_bridge(conn)
        _apply_v35_active_opportunity_identity(conn)
        _apply_v36_opportunities_topic_dedup_partial(conn)
        _apply_v37_experiment_ledger(conn)
        _apply_v38_experiment_planning(conn)
        _apply_v39_strategy_brief(conn)
        _apply_v40_execution_contract(conn)
        _apply_v41_experiment_outcomes(conn)
        _apply_v42_cp_channel_bridge(conn)
        _apply_v43_eligibility_provenance(conn)
        _apply_v44_visual_intelligence(conn)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
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
        _apply_v24_analytics_observation(conn)
        _apply_v25_generator_skipped_status(conn)
        _apply_v26_recommendation_applications(conn)
        _apply_v27_content_features(conn)
        _apply_v28_cross_pub_learning(conn)
        _apply_v29_market_intelligence(conn)
        _apply_v30_velocity(conn)
        _apply_v31_exploration(conn)
        _apply_v32_interpretation(conn)
        _apply_v33_canonical_clusters(conn)
        _apply_v34_market_bridge(conn)
        _apply_v35_active_opportunity_identity(conn)
        _apply_v36_opportunities_topic_dedup_partial(conn)
        _apply_v37_experiment_ledger(conn)
        _apply_v38_experiment_planning(conn)
        _apply_v39_strategy_brief(conn)
        _apply_v40_execution_contract(conn)
        _apply_v41_experiment_outcomes(conn)
        _apply_v42_cp_channel_bridge(conn)
        _apply_v43_eligibility_provenance(conn)
        _apply_v44_visual_intelligence(conn)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
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
        _apply_v24_analytics_observation(conn)
        _apply_v25_generator_skipped_status(conn)
        _apply_v26_recommendation_applications(conn)
        _apply_v27_content_features(conn)
        _apply_v28_cross_pub_learning(conn)
        _apply_v29_market_intelligence(conn)
        _apply_v30_velocity(conn)
        _apply_v31_exploration(conn)
        _apply_v32_interpretation(conn)
        _apply_v33_canonical_clusters(conn)
        _apply_v34_market_bridge(conn)
        _apply_v35_active_opportunity_identity(conn)
        _apply_v36_opportunities_topic_dedup_partial(conn)
        _apply_v37_experiment_ledger(conn)
        _apply_v38_experiment_planning(conn)
        _apply_v39_strategy_brief(conn)
        _apply_v40_execution_contract(conn)
        _apply_v41_experiment_outcomes(conn)
        _apply_v42_cp_channel_bridge(conn)
        _apply_v43_eligibility_provenance(conn)
        _apply_v44_visual_intelligence(conn)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
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
        _apply_v24_analytics_observation(conn)
        _apply_v25_generator_skipped_status(conn)
        _apply_v26_recommendation_applications(conn)
        _apply_v27_content_features(conn)
        _apply_v28_cross_pub_learning(conn)
        _apply_v29_market_intelligence(conn)
        _apply_v30_velocity(conn)
        _apply_v31_exploration(conn)
        _apply_v32_interpretation(conn)
        _apply_v33_canonical_clusters(conn)
        _apply_v34_market_bridge(conn)
        _apply_v35_active_opportunity_identity(conn)
        _apply_v36_opportunities_topic_dedup_partial(conn)
        _apply_v37_experiment_ledger(conn)
        _apply_v38_experiment_planning(conn)
        _apply_v39_strategy_brief(conn)
        _apply_v40_execution_contract(conn)
        _apply_v41_experiment_outcomes(conn)
        _apply_v42_cp_channel_bridge(conn)
        _apply_v43_eligibility_provenance(conn)
        _apply_v44_visual_intelligence(conn)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
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
        _apply_v24_analytics_observation(conn)
        _apply_v25_generator_skipped_status(conn)
        _apply_v26_recommendation_applications(conn)
        _apply_v27_content_features(conn)
        _apply_v28_cross_pub_learning(conn)
        _apply_v29_market_intelligence(conn)
        _apply_v30_velocity(conn)
        _apply_v31_exploration(conn)
        _apply_v32_interpretation(conn)
        _apply_v33_canonical_clusters(conn)
        _apply_v34_market_bridge(conn)
        _apply_v35_active_opportunity_identity(conn)
        _apply_v36_opportunities_topic_dedup_partial(conn)
        _apply_v37_experiment_ledger(conn)
        _apply_v38_experiment_planning(conn)
        _apply_v39_strategy_brief(conn)
        _apply_v40_execution_contract(conn)
        _apply_v41_experiment_outcomes(conn)
        _apply_v42_cp_channel_bridge(conn)
        _apply_v43_eligibility_provenance(conn)
        _apply_v44_visual_intelligence(conn)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 18:
        logger.info("Migrating schema from version 18 to %d", SCHEMA_VERSION)
        conn.executescript(_DDL_V19_APPLICATION)
        conn.executescript(_DDL_V20_AUTH_STORAGE)
        _apply_v21_topic_workspace(conn)
        _apply_v22_analytics_retention(conn)
        _apply_v23_publication_ownership(conn)
        _apply_v24_analytics_observation(conn)
        _apply_v25_generator_skipped_status(conn)
        _apply_v26_recommendation_applications(conn)
        _apply_v27_content_features(conn)
        _apply_v28_cross_pub_learning(conn)
        _apply_v29_market_intelligence(conn)
        _apply_v30_velocity(conn)
        _apply_v31_exploration(conn)
        _apply_v32_interpretation(conn)
        _apply_v33_canonical_clusters(conn)
        _apply_v34_market_bridge(conn)
        _apply_v35_active_opportunity_identity(conn)
        _apply_v36_opportunities_topic_dedup_partial(conn)
        _apply_v37_experiment_ledger(conn)
        _apply_v38_experiment_planning(conn)
        _apply_v39_strategy_brief(conn)
        _apply_v40_execution_contract(conn)
        _apply_v41_experiment_outcomes(conn)
        _apply_v42_cp_channel_bridge(conn)
        _apply_v43_eligibility_provenance(conn)
        _apply_v44_visual_intelligence(conn)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")
    elif current == 19:
        logger.info("Migrating schema from version 19 to %d", SCHEMA_VERSION)
        conn.executescript(_DDL_V20_AUTH_STORAGE)
        _apply_v21_topic_workspace(conn)
        _apply_v22_analytics_retention(conn)
        _apply_v23_publication_ownership(conn)
        _apply_v24_analytics_observation(conn)
        _apply_v25_generator_skipped_status(conn)
        _apply_v26_recommendation_applications(conn)
        _apply_v27_content_features(conn)
        _apply_v28_cross_pub_learning(conn)
        _apply_v29_market_intelligence(conn)
        _apply_v30_velocity(conn)
        _apply_v31_exploration(conn)
        _apply_v32_interpretation(conn)
        _apply_v33_canonical_clusters(conn)
        _apply_v34_market_bridge(conn)
        _apply_v35_active_opportunity_identity(conn)
        _apply_v36_opportunities_topic_dedup_partial(conn)
        _apply_v37_experiment_ledger(conn)
        _apply_v38_experiment_planning(conn)
        _apply_v39_strategy_brief(conn)
        _apply_v40_execution_contract(conn)
        _apply_v41_experiment_outcomes(conn)
        _apply_v42_cp_channel_bridge(conn)
        _apply_v43_eligibility_provenance(conn)
        _apply_v44_visual_intelligence(conn)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 20:
        logger.info("Migrating schema from version 20 to %d", SCHEMA_VERSION)
        _apply_v21_topic_workspace(conn)
        _apply_v22_analytics_retention(conn)
        _apply_v23_publication_ownership(conn)
        _apply_v24_analytics_observation(conn)
        _apply_v25_generator_skipped_status(conn)
        _apply_v26_recommendation_applications(conn)
        _apply_v27_content_features(conn)
        _apply_v28_cross_pub_learning(conn)
        _apply_v29_market_intelligence(conn)
        _apply_v30_velocity(conn)
        _apply_v31_exploration(conn)
        _apply_v32_interpretation(conn)
        _apply_v33_canonical_clusters(conn)
        _apply_v34_market_bridge(conn)
        _apply_v35_active_opportunity_identity(conn)
        _apply_v36_opportunities_topic_dedup_partial(conn)
        _apply_v37_experiment_ledger(conn)
        _apply_v38_experiment_planning(conn)
        _apply_v39_strategy_brief(conn)
        _apply_v40_execution_contract(conn)
        _apply_v41_experiment_outcomes(conn)
        _apply_v42_cp_channel_bridge(conn)
        _apply_v43_eligibility_provenance(conn)
        _apply_v44_visual_intelligence(conn)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 21:
        logger.info("Migrating schema from version 21 to %d", SCHEMA_VERSION)
        _apply_v22_analytics_retention(conn)
        _apply_v23_publication_ownership(conn)
        _apply_v24_analytics_observation(conn)
        _apply_v25_generator_skipped_status(conn)
        _apply_v26_recommendation_applications(conn)
        _apply_v27_content_features(conn)
        _apply_v28_cross_pub_learning(conn)
        _apply_v29_market_intelligence(conn)
        _apply_v30_velocity(conn)
        _apply_v31_exploration(conn)
        _apply_v32_interpretation(conn)
        _apply_v33_canonical_clusters(conn)
        _apply_v34_market_bridge(conn)
        _apply_v35_active_opportunity_identity(conn)
        _apply_v36_opportunities_topic_dedup_partial(conn)
        _apply_v37_experiment_ledger(conn)
        _apply_v38_experiment_planning(conn)
        _apply_v39_strategy_brief(conn)
        _apply_v40_execution_contract(conn)
        _apply_v41_experiment_outcomes(conn)
        _apply_v42_cp_channel_bridge(conn)
        _apply_v43_eligibility_provenance(conn)
        _apply_v44_visual_intelligence(conn)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 22:
        logger.info("Migrating schema from version 22 to %d", SCHEMA_VERSION)
        _apply_v23_publication_ownership(conn)
        _apply_v24_analytics_observation(conn)
        _apply_v25_generator_skipped_status(conn)
        _apply_v26_recommendation_applications(conn)
        _apply_v27_content_features(conn)
        _apply_v28_cross_pub_learning(conn)
        _apply_v29_market_intelligence(conn)
        _apply_v30_velocity(conn)
        _apply_v31_exploration(conn)
        _apply_v32_interpretation(conn)
        _apply_v33_canonical_clusters(conn)
        _apply_v34_market_bridge(conn)
        _apply_v35_active_opportunity_identity(conn)
        _apply_v36_opportunities_topic_dedup_partial(conn)
        _apply_v37_experiment_ledger(conn)
        _apply_v38_experiment_planning(conn)
        _apply_v39_strategy_brief(conn)
        _apply_v40_execution_contract(conn)
        _apply_v41_experiment_outcomes(conn)
        _apply_v42_cp_channel_bridge(conn)
        _apply_v43_eligibility_provenance(conn)
        _apply_v44_visual_intelligence(conn)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 23:
        logger.info("Migrating schema from version 23 to %d", SCHEMA_VERSION)
        _apply_v24_analytics_observation(conn)
        _apply_v25_generator_skipped_status(conn)
        _apply_v26_recommendation_applications(conn)
        _apply_v27_content_features(conn)
        _apply_v28_cross_pub_learning(conn)
        _apply_v29_market_intelligence(conn)
        _apply_v30_velocity(conn)
        _apply_v31_exploration(conn)
        _apply_v32_interpretation(conn)
        _apply_v33_canonical_clusters(conn)
        _apply_v34_market_bridge(conn)
        _apply_v35_active_opportunity_identity(conn)
        _apply_v36_opportunities_topic_dedup_partial(conn)
        _apply_v37_experiment_ledger(conn)
        _apply_v38_experiment_planning(conn)
        _apply_v39_strategy_brief(conn)
        _apply_v40_execution_contract(conn)
        _apply_v41_experiment_outcomes(conn)
        _apply_v42_cp_channel_bridge(conn)
        _apply_v43_eligibility_provenance(conn)
        _apply_v44_visual_intelligence(conn)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 24:
        logger.info("Migrating schema from version 24 to %d", SCHEMA_VERSION)
        _apply_v25_generator_skipped_status(conn)
        _apply_v26_recommendation_applications(conn)
        _apply_v27_content_features(conn)
        _apply_v28_cross_pub_learning(conn)
        _apply_v29_market_intelligence(conn)
        _apply_v30_velocity(conn)
        _apply_v31_exploration(conn)
        _apply_v32_interpretation(conn)
        _apply_v33_canonical_clusters(conn)
        _apply_v34_market_bridge(conn)
        _apply_v35_active_opportunity_identity(conn)
        _apply_v36_opportunities_topic_dedup_partial(conn)
        _apply_v37_experiment_ledger(conn)
        _apply_v38_experiment_planning(conn)
        _apply_v39_strategy_brief(conn)
        _apply_v40_execution_contract(conn)
        _apply_v41_experiment_outcomes(conn)
        _apply_v42_cp_channel_bridge(conn)
        _apply_v43_eligibility_provenance(conn)
        _apply_v44_visual_intelligence(conn)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 25:
        logger.info("Migrating schema from version 25 to %d", SCHEMA_VERSION)
        _apply_v26_recommendation_applications(conn)
        _apply_v27_content_features(conn)
        _apply_v28_cross_pub_learning(conn)
        _apply_v29_market_intelligence(conn)
        _apply_v30_velocity(conn)
        _apply_v31_exploration(conn)
        _apply_v32_interpretation(conn)
        _apply_v33_canonical_clusters(conn)
        _apply_v34_market_bridge(conn)
        _apply_v35_active_opportunity_identity(conn)
        _apply_v36_opportunities_topic_dedup_partial(conn)
        _apply_v37_experiment_ledger(conn)
        _apply_v38_experiment_planning(conn)
        _apply_v39_strategy_brief(conn)
        _apply_v40_execution_contract(conn)
        _apply_v41_experiment_outcomes(conn)
        _apply_v42_cp_channel_bridge(conn)
        _apply_v43_eligibility_provenance(conn)
        _apply_v44_visual_intelligence(conn)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 26:
        logger.info("Migrating schema from version 26 to %d", SCHEMA_VERSION)
        _apply_v27_content_features(conn)
        _apply_v28_cross_pub_learning(conn)
        _apply_v29_market_intelligence(conn)
        _apply_v30_velocity(conn)
        _apply_v31_exploration(conn)
        _apply_v32_interpretation(conn)
        _apply_v33_canonical_clusters(conn)
        _apply_v34_market_bridge(conn)
        _apply_v35_active_opportunity_identity(conn)
        _apply_v36_opportunities_topic_dedup_partial(conn)
        _apply_v37_experiment_ledger(conn)
        _apply_v38_experiment_planning(conn)
        _apply_v39_strategy_brief(conn)
        _apply_v40_execution_contract(conn)
        _apply_v41_experiment_outcomes(conn)
        _apply_v42_cp_channel_bridge(conn)
        _apply_v43_eligibility_provenance(conn)
        _apply_v44_visual_intelligence(conn)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 27:
        logger.info("Migrating schema from version 27 to %d", SCHEMA_VERSION)
        _apply_v28_cross_pub_learning(conn)
        _apply_v29_market_intelligence(conn)
        _apply_v30_velocity(conn)
        _apply_v31_exploration(conn)
        _apply_v32_interpretation(conn)
        _apply_v33_canonical_clusters(conn)
        _apply_v34_market_bridge(conn)
        _apply_v35_active_opportunity_identity(conn)
        _apply_v36_opportunities_topic_dedup_partial(conn)
        _apply_v37_experiment_ledger(conn)
        _apply_v38_experiment_planning(conn)
        _apply_v39_strategy_brief(conn)
        _apply_v40_execution_contract(conn)
        _apply_v41_experiment_outcomes(conn)
        _apply_v42_cp_channel_bridge(conn)
        _apply_v43_eligibility_provenance(conn)
        _apply_v44_visual_intelligence(conn)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 28:
        logger.info("Migrating schema from version 28 to %d", SCHEMA_VERSION)
        _apply_v29_market_intelligence(conn)
        _apply_v30_velocity(conn)
        _apply_v31_exploration(conn)
        _apply_v32_interpretation(conn)
        _apply_v33_canonical_clusters(conn)
        _apply_v34_market_bridge(conn)
        _apply_v35_active_opportunity_identity(conn)
        _apply_v36_opportunities_topic_dedup_partial(conn)
        _apply_v37_experiment_ledger(conn)
        _apply_v38_experiment_planning(conn)
        _apply_v39_strategy_brief(conn)
        _apply_v40_execution_contract(conn)
        _apply_v41_experiment_outcomes(conn)
        _apply_v42_cp_channel_bridge(conn)
        _apply_v43_eligibility_provenance(conn)
        _apply_v44_visual_intelligence(conn)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 29:
        logger.info("Migrating schema from version 29 to %d", SCHEMA_VERSION)
        _apply_v30_velocity(conn)
        _apply_v31_exploration(conn)
        _apply_v32_interpretation(conn)
        _apply_v33_canonical_clusters(conn)
        _apply_v34_market_bridge(conn)
        _apply_v35_active_opportunity_identity(conn)
        _apply_v36_opportunities_topic_dedup_partial(conn)
        _apply_v37_experiment_ledger(conn)
        _apply_v38_experiment_planning(conn)
        _apply_v39_strategy_brief(conn)
        _apply_v40_execution_contract(conn)
        _apply_v41_experiment_outcomes(conn)
        _apply_v42_cp_channel_bridge(conn)
        _apply_v43_eligibility_provenance(conn)
        _apply_v44_visual_intelligence(conn)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 30:
        logger.info("Migrating schema from version 30 to %d", SCHEMA_VERSION)
        _apply_v31_exploration(conn)
        _apply_v32_interpretation(conn)
        _apply_v33_canonical_clusters(conn)
        _apply_v34_market_bridge(conn)
        _apply_v35_active_opportunity_identity(conn)
        _apply_v36_opportunities_topic_dedup_partial(conn)
        _apply_v37_experiment_ledger(conn)
        _apply_v38_experiment_planning(conn)
        _apply_v39_strategy_brief(conn)
        _apply_v40_execution_contract(conn)
        _apply_v41_experiment_outcomes(conn)
        _apply_v42_cp_channel_bridge(conn)
        _apply_v43_eligibility_provenance(conn)
        _apply_v44_visual_intelligence(conn)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 31:
        logger.info("Migrating schema from version 31 to %d", SCHEMA_VERSION)
        _apply_v32_interpretation(conn)
        _apply_v33_canonical_clusters(conn)
        _apply_v34_market_bridge(conn)
        _apply_v35_active_opportunity_identity(conn)
        _apply_v36_opportunities_topic_dedup_partial(conn)
        _apply_v37_experiment_ledger(conn)
        _apply_v38_experiment_planning(conn)
        _apply_v39_strategy_brief(conn)
        _apply_v40_execution_contract(conn)
        _apply_v41_experiment_outcomes(conn)
        _apply_v42_cp_channel_bridge(conn)
        _apply_v43_eligibility_provenance(conn)
        _apply_v44_visual_intelligence(conn)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 32:
        logger.info("Migrating schema from version 32 to %d", SCHEMA_VERSION)
        _apply_v33_canonical_clusters(conn)
        _apply_v34_market_bridge(conn)
        _apply_v35_active_opportunity_identity(conn)
        _apply_v36_opportunities_topic_dedup_partial(conn)
        _apply_v37_experiment_ledger(conn)
        _apply_v38_experiment_planning(conn)
        _apply_v39_strategy_brief(conn)
        _apply_v40_execution_contract(conn)
        _apply_v41_experiment_outcomes(conn)
        _apply_v42_cp_channel_bridge(conn)
        _apply_v43_eligibility_provenance(conn)
        _apply_v44_visual_intelligence(conn)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 33:
        logger.info("Migrating schema from version 33 to %d", SCHEMA_VERSION)
        _apply_v34_market_bridge(conn)
        _apply_v35_active_opportunity_identity(conn)
        _apply_v36_opportunities_topic_dedup_partial(conn)
        _apply_v37_experiment_ledger(conn)
        _apply_v38_experiment_planning(conn)
        _apply_v39_strategy_brief(conn)
        _apply_v40_execution_contract(conn)
        _apply_v41_experiment_outcomes(conn)
        _apply_v42_cp_channel_bridge(conn)
        _apply_v43_eligibility_provenance(conn)
        _apply_v44_visual_intelligence(conn)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 34:
        logger.info("Migrating schema from version 34 to %d", SCHEMA_VERSION)
        _apply_v35_active_opportunity_identity(conn)
        _apply_v36_opportunities_topic_dedup_partial(conn)
        _apply_v37_experiment_ledger(conn)
        _apply_v38_experiment_planning(conn)
        _apply_v39_strategy_brief(conn)
        _apply_v40_execution_contract(conn)
        _apply_v41_experiment_outcomes(conn)
        _apply_v42_cp_channel_bridge(conn)
        _apply_v43_eligibility_provenance(conn)
        _apply_v44_visual_intelligence(conn)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 35:
        logger.info("Migrating schema from version 35 to %d", SCHEMA_VERSION)
        _apply_v36_opportunities_topic_dedup_partial(conn)
        _apply_v37_experiment_ledger(conn)
        _apply_v38_experiment_planning(conn)
        _apply_v39_strategy_brief(conn)
        _apply_v40_execution_contract(conn)
        _apply_v41_experiment_outcomes(conn)
        _apply_v42_cp_channel_bridge(conn)
        _apply_v43_eligibility_provenance(conn)
        _apply_v44_visual_intelligence(conn)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 36:
        logger.info("Migrating schema from version 36 to %d", SCHEMA_VERSION)
        _apply_v37_experiment_ledger(conn)
        _apply_v38_experiment_planning(conn)
        _apply_v39_strategy_brief(conn)
        _apply_v40_execution_contract(conn)
        _apply_v41_experiment_outcomes(conn)
        _apply_v42_cp_channel_bridge(conn)
        _apply_v43_eligibility_provenance(conn)
        _apply_v44_visual_intelligence(conn)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 37:
        logger.info("Migrating schema from version 37 to %d", SCHEMA_VERSION)
        _apply_v38_experiment_planning(conn)
        _apply_v39_strategy_brief(conn)
        _apply_v40_execution_contract(conn)
        _apply_v41_experiment_outcomes(conn)
        _apply_v42_cp_channel_bridge(conn)
        _apply_v43_eligibility_provenance(conn)
        _apply_v44_visual_intelligence(conn)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 38:
        logger.info("Migrating schema from version 38 to %d", SCHEMA_VERSION)
        _apply_v39_strategy_brief(conn)
        _apply_v40_execution_contract(conn)
        _apply_v41_experiment_outcomes(conn)
        _apply_v42_cp_channel_bridge(conn)
        _apply_v43_eligibility_provenance(conn)
        _apply_v44_visual_intelligence(conn)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 39:
        logger.info("Migrating schema from version 39 to %d", SCHEMA_VERSION)
        _apply_v40_execution_contract(conn)
        _apply_v41_experiment_outcomes(conn)
        _apply_v42_cp_channel_bridge(conn)
        _apply_v43_eligibility_provenance(conn)
        _apply_v44_visual_intelligence(conn)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 40:
        logger.info("Migrating schema from version 40 to %d", SCHEMA_VERSION)
        _apply_v41_experiment_outcomes(conn)
        _apply_v42_cp_channel_bridge(conn)
        _apply_v43_eligibility_provenance(conn)
        _apply_v44_visual_intelligence(conn)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 41:
        logger.info("Migrating schema from version 41 to %d", SCHEMA_VERSION)
        _apply_v42_cp_channel_bridge(conn)
        _apply_v43_eligibility_provenance(conn)
        _apply_v44_visual_intelligence(conn)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 42:
        logger.info("Migrating schema from version 42 to %d", SCHEMA_VERSION)
        _apply_v43_eligibility_provenance(conn)
        _apply_v44_visual_intelligence(conn)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 43:
        logger.info("Migrating schema from version 43 to %d", SCHEMA_VERSION)
        _apply_v44_visual_intelligence(conn)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 44:
        logger.info("Migrating schema from version 44 to %d", SCHEMA_VERSION)
        _apply_v45_analytics_observation_state(conn)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 45:
        logger.info("Migrating schema from version 45 to %d", SCHEMA_VERSION)
        _apply_v46_semantic_fit_cache(conn)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 46:
        logger.info("Migrating schema from version 46 to %d", SCHEMA_VERSION)
        _apply_v47_autonomy_orchestrator(conn)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 47:
        logger.info("Migrating schema from version 47 to %d", SCHEMA_VERSION)
        _apply_v48_autonomous_production(conn)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 48:
        logger.info("Migrating schema from version 48 to %d", SCHEMA_VERSION)
        _apply_v49_scheduled_publishing(conn)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 49:
        logger.info("Migrating schema from version 49 to %d", SCHEMA_VERSION)
        _apply_v50_visual_quality(conn)
        _apply_v51_slot_retirement(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 50:
        logger.info("Migrating schema from version 50 to %d", SCHEMA_VERSION)
        _apply_v51_slot_retirement(conn)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    else:
        raise RuntimeError(
            f"Unsupported schema version {current}; expected <= {SCHEMA_VERSION}. "
            "Manual migration required."
        )


def open_db(path: Path) -> sqlite3.Connection:
    """Open (or create) the SQLite database, enforce FK constraints, and run migrations."""
    # Phase 18E — the lowest layer at which the live database can be reached.
    #
    # Every caller funnels through here, including CLI commands under
    # CliRunner and any test that reads get_config().db_path. Putting the
    # check at startup only was not enough: a pytest run with ACE_DB_PATH set
    # but EMPTY resolved to the operational database and MIGRATED it, because
    # nothing between the environment variable and sqlite3.connect looked.
    #
    # open_db also runs _migrate(), so this is specifically the call that can
    # rewrite live schema. Guarding it makes "a test cannot mutate the
    # operational database" true by construction rather than by convention.
    from app.core.runtime_mode import assert_runtime_isolation

    assert_runtime_isolation(path)

    path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: FastAPI runs sync handlers in a threadpool via anyio;
    # the generator dependency teardown (conn.close) can execute in a different thread
    # than where the connection was opened.  The connection is never shared across
    # concurrent requests — it is per-request — so disabling the check is safe.
    # timeout: how long SQLite waits for a contended lock before raising
    # "database is locked". The default is 5s; a slow migration or checkpoint on
    # a busy database can legitimately exceed that, and an exception is far
    # better than the caller assuming the write landed.
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row

    # Only switch journal mode when it is not already WAL.
    #
    # `PRAGMA journal_mode=WAL` acquires an EXCLUSIVE database lock even when it
    # is a no-op transition. open_db runs once per HTTP request, so issuing it
    # unconditionally made every request contend for an exclusive lock. Under
    # concurrency that serialized the whole API, and it could deadlock outright:
    # a connection being closed on an anyio teardown thread would sit in
    # sqlite3WalClose -> unixLock holding SQLite's global mutex, while every
    # other request blocked in sqlite3BtreeOpen behind it. Reading the mode
    # first takes only a shared lock.
    if str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower() != "wal":
        conn.execute("PRAGMA journal_mode=WAL")

    # Belt and braces alongside the connect timeout: applies to lock waits that
    # occur after the connection is established.
    conn.execute("PRAGMA busy_timeout=30000")
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
