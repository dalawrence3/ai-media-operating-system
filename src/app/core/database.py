"""SQLite connection management and schema initialization."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.core.logging import get_logger

logger = get_logger(__name__)

# Increment when the schema changes; add a migration branch in _migrate().
SCHEMA_VERSION = 4

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
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Schema ready at version %d", SCHEMA_VERSION)

    elif current == 1:
        logger.info("Migrating schema from version 1 to %d", SCHEMA_VERSION)
        conn.executescript(_DDL_V2)
        conn.executescript(_DDL_V3)
        conn.executescript(_DDL_V4_NEW_TABLES)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 2:
        logger.info("Migrating schema from version 2 to %d", SCHEMA_VERSION)
        conn.executescript(_DDL_V3)
        conn.executescript(_DDL_V4_NEW_TABLES)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    elif current == 3:
        logger.info("Migrating schema from version 3 to %d", SCHEMA_VERSION)
        conn.executescript(_DDL_V4_NEW_TABLES)
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
