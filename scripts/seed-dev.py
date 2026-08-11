#!/usr/bin/env python3
"""Deterministic development data seed.

Usage:
    make seed-dev
    OR: python scripts/seed-dev.py

Creates:
  - 1 workspace ("Dev Studio")
  - 2 channels ("Tech Shorts", "Finance Clips")
  - 1 fake platform account per channel (YouTube stub, no real tokens)

Idempotent: safe to run multiple times — skips records that already exist.
All records are labeled [DEV] in display names.

STOP: Never run against a database with real OAuth tokens or real credentials.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

# Load .env.local if present.
env_file = Path(".env.local")
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

ace_env = os.environ.get("ACE_ENV", "production")
if ace_env != "development":
    print(
        "ERROR: ACE_ENV is not 'development'. "
        "Set ACE_ENV=development in .env.local before seeding.",
        file=sys.stderr,
    )
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from app.control_plane import repository as repo  # noqa: E402
from app.control_plane.models import (  # noqa: E402
    ChannelDraft,
    PlatformAccountDraft,
    WorkspaceDraft,
)
from app.core.config import get_config, reset_config  # noqa: E402
from app.core.database import open_db  # noqa: E402
from app.learning.constants import (  # noqa: E402
    LEARNING_ENGINE_VERSION,
    LEARNING_SCHEMA_VERSION,
    STATUS_PENDING,
)

reset_config()
config = get_config()
config.db_path.parent.mkdir(parents=True, exist_ok=True)

print(f"[seed] DB: {config.db_path}")

conn = open_db(config.db_path)

ACTOR = "system:seed-dev"
WS_SLUG = "dev-studio"

# ── Workspace ──────────────────────────────────────────────────────────────────
existing = [w for w in repo.list_workspaces(conn) if w.slug == WS_SLUG]
if existing:
    ws = existing[0]
    print(f"[seed] Workspace already exists: {ws.name} ({ws.id})")
else:
    draft = WorkspaceDraft(
        id=str(uuid.uuid4()),
        name="[DEV] Dev Studio",
        slug=WS_SLUG,
        actor=ACTOR,
    )
    ws = repo.create_workspace(conn, draft)
    print(f"[seed] Created workspace: {ws.name} ({ws.id})")

# ── Channels ───────────────────────────────────────────────────────────────────
CHANNELS = [
    {"slug": "dev-tech-shorts", "name": "[DEV] Tech Shorts", "desc": "Dev seed — technology"},
    {"slug": "dev-finance-clips", "name": "[DEV] Finance Clips", "desc": "Dev seed — finance"},
]

channels = []
existing_channels = repo.list_channels_by_workspace(conn, ws.id)
existing_slugs = {c.slug: c for c in existing_channels}

for ch_def in CHANNELS:
    if ch_def["slug"] in existing_slugs:
        ch = existing_slugs[ch_def["slug"]]
        print(f"[seed] Channel already exists: {ch.name} ({ch.id})")
        channels.append(ch)
    else:
        draft = ChannelDraft(
            id=str(uuid.uuid4()),
            workspace_id=ws.id,
            name=ch_def["name"],
            slug=ch_def["slug"],
            actor=ACTOR,
            description=ch_def["desc"],
        )
        ch = repo.create_channel(conn, draft)
        print(f"[seed] Created channel: {ch.name} ({ch.id})")
        channels.append(ch)

# ── Platform accounts (fake YouTube stubs — no real tokens) ────────────────────
for ch in channels:
    existing_accounts = repo.list_platform_accounts_by_channel(conn, ch.id)
    if existing_accounts:
        print(f"[seed] Account already exists for {ch.name} — skipping")
        continue

    # Ensure the youtube platform entry exists.
    repo.ensure_platform(conn, "youtube", "youtube", "YouTube")

    draft = PlatformAccountDraft(
        id=str(uuid.uuid4()),
        channel_id=ch.id,
        platform_id="youtube",
        platform_key="youtube",
        external_account_id=f"UC{uuid.uuid4().hex[:22]}",
        display_name=f"{ch.name} — YouTube (dev stub)",
        actor=ACTOR,
        status="disconnected",
    )
    acct = repo.create_platform_account(conn, draft)
    print(f"[seed] Created account: {acct.display_name} ({acct.id})")

conn.commit()

# ── Dev topic (required for analytics / learning workspace link) ───────────────
NOW = "2026-08-01T00:00:00"

existing_topics = conn.execute(
    "SELECT id FROM topics WHERE title = '[DEV] AI Content Strategy'"
).fetchone()

if existing_topics:
    topic_id = existing_topics[0]
    print(f"[seed] Topic already exists: id={topic_id}")
else:
    cur = conn.execute(
        "INSERT INTO topics (title, angle, status, created_at, updated_at) VALUES (?,?,?,?,?)",
        ("[DEV] AI Content Strategy", "Focus on practical use cases", "active", NOW, NOW),
    )
    topic_id = cur.lastrowid
    print(f"[seed] Created topic: id={topic_id}")

# ── Pipeline execution linking workspace → topic ───────────────────────────────
existing_exec = conn.execute(
    "SELECT id FROM app_pipeline_executions WHERE workspace_id = ? AND topic_id = ?",
    (ws.id, topic_id),
).fetchone()

if existing_exec:
    print("[seed] Pipeline execution already exists for workspace→topic link")
else:
    exec_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO app_pipeline_executions
               (id, workspace_id, channel_id, platform_account_id, topic_id,
                correlation_id, idempotency_key, status, end_stage, actor,
                created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            exec_id,
            ws.id,
            None,
            None,
            topic_id,
            f"corr-seed-{exec_id[:8]}",
            f"idem-seed-{exec_id[:8]}",
            "completed",
            "learning",
            ACTOR,
            NOW,
            NOW,
        ),
    )
    print(f"[seed] Created pipeline execution linking workspace → topic {topic_id}")

# ── Analytics aggregates ───────────────────────────────────────────────────────
existing_aggs = conn.execute(
    "SELECT COUNT(*) FROM analytics_aggregates WHERE topic_id = ?", (topic_id,)
).fetchone()[0]

if existing_aggs:
    print(f"[seed] Analytics aggregates already exist for topic {topic_id} ({existing_aggs} rows)")
else:
    SEED_AGG_HASH_PREFIX = "seed-dev-agg"
    # (period_type, period_key, metric_name, value, snap_count, calc_method, currency)
    agg_rows = [
        ("lifetime", "lifetime", "views", 42_000.0, 12, "sum", None),
        ("lifetime", "lifetime", "impressions", 210_000.0, 12, "sum", None),
        ("lifetime", "lifetime", "subscribers_gained", 320.0, 12, "sum", None),
        ("lifetime", "lifetime", "ctr", 0.047, 12, "latest_observation", None),
        ("lifetime", "lifetime", "average_view_duration", 184.0, 12, "latest_observation", None),
        ("lifetime", "lifetime", "likes", 1_850.0, 12, "latest_observation", None),
        ("monthly", "2026-08", "views", 8_400.0, 3, "sum", None),
        ("monthly", "2026-08", "impressions", 40_200.0, 3, "sum", None),
        ("monthly", "2026-07", "views", 11_200.0, 4, "sum", None),
        ("monthly", "2026-07", "ctr", 0.051, 4, "latest_observation", None),
    ]
    for i, (ptype, pkey, mname, mval, sc, calc, curr) in enumerate(agg_rows):
        input_hash = f"{SEED_AGG_HASH_PREFIX}-{ptype}-{mname}-{i}"
        conn.execute(
            """INSERT INTO analytics_aggregates
                   (publication_id, topic_id, provider, period_type, period_key,
                    metric_name, metric_value, snapshot_count, calculation_method,
                    currency_code, source_snapshot_ids_json, input_hash, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                1,
                topic_id,
                "youtube_dev_seed",
                ptype,
                pkey,
                mname,
                mval,
                sc,
                calc,
                curr,
                "[]",
                input_hash,
                NOW,
            ),
        )
    print(f"[seed] Created {len(agg_rows)} analytics aggregates for topic {topic_id}")

# ── Learning run + recommendations ────────────────────────────────────────────
existing_recs = conn.execute(
    "SELECT COUNT(*) FROM optimization_recommendations WHERE topic_id = ?", (topic_id,)
).fetchone()[0]

if existing_recs:
    print(f"[seed] Recommendations already exist for topic {topic_id} ({existing_recs} rows)")
else:
    run_cur = conn.execute(
        """INSERT INTO learning_runs
               (topic_id, publication_id, publication_count, recommendation_count,
                status, engine_version, schema_version, input_hash, created_at, completed_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            topic_id,
            None,
            12,
            3,
            "completed",
            LEARNING_ENGINE_VERSION,
            LEARNING_SCHEMA_VERSION,
            "seed-dev-run-001",
            NOW,
            NOW,
        ),
    )
    run_id = run_cur.lastrowid

    RECS = [
        {
            "domain": "scripts",
            "subsystem": "hook",
            "measure": "ctr",
            "title": "[DEV] Hook length is associated with higher CTR",
            "explanation": (
                "Topics with hooks under 5 seconds show CTR 0.047 vs 0.031"
                " for longer hooks across 12 lifetime snapshots."
            ),
            "expected_improvement": (
                "Shortening hooks to under 5 seconds is associated with improved CTR."
            ),
            "confidence": "medium",
            "confidence_score": 0.62,
            "recommendation_strength": "actionable",
            "status": STATUS_PENDING,
        },
        {
            "domain": "narration",
            "subsystem": "pacing",
            "measure": "average_view_duration",
            "title": "[DEV] Slower narration pacing associated with longer retention",
            "explanation": (
                "Videos with narration pace under 140 wpm show"
                " average_view_duration 184s vs 142s for faster pacing."
            ),
            "expected_improvement": (
                "Reducing narration pace is associated with longer average view duration."
            ),
            "confidence": "medium",
            "confidence_score": 0.55,
            "recommendation_strength": "actionable",
            "status": STATUS_PENDING,
        },
        {
            "domain": "topics",
            "subsystem": "angle",
            "measure": "subscribers_gained",
            "title": "[DEV] Practical use-case framing associated with subscriber growth",
            "explanation": (
                "Topics framed around practical use cases show higher"
                " subscribers_gained (320 lifetime) vs abstract framing."
            ),
            "expected_improvement": (
                "Practical framing is associated with higher subscriber acquisition."
            ),
            "confidence": "low",
            "confidence_score": 0.35,
            "recommendation_strength": "exploratory",
            "status": STATUS_PENDING,
        },
    ]

    for i, rec in enumerate(RECS):
        conn.execute(
            """INSERT INTO optimization_recommendations
                   (learning_run_id, topic_id, publication_id, domain, subsystem, measure,
                    title, explanation, expected_improvement,
                    evidence_json, evidence_classification, recommendation_strength,
                    confidence, confidence_score,
                    affected_subsystem, subsystem_entity_type, subsystem_entity_id,
                    experiment_id, engine_version, schema_version, input_hash,
                    status, superseded_at, superseded_by_id, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run_id,
                topic_id,
                None,
                rec["domain"],
                rec["subsystem"],
                rec["measure"],
                rec["title"],
                rec["explanation"],
                rec["expected_improvement"],
                "[]",
                "observational",
                rec["recommendation_strength"],
                rec["confidence"],
                rec["confidence_score"],
                "",
                "",
                None,
                None,
                LEARNING_ENGINE_VERSION,
                LEARNING_SCHEMA_VERSION,
                f"seed-dev-rec-{i:03d}",
                rec["status"],
                None,
                None,
                NOW,
            ),
        )
    print(f"[seed] Created learning run {run_id} with {len(RECS)} recommendations")

conn.commit()
conn.close()

print("")
print("[seed] ✓ Done.")
print(f"[seed]   Workspace slug:  {WS_SLUG}  id: {ws.id}")
print(f"[seed]   Channels: {len(channels)}")
print(f"[seed]   Topic id: {topic_id}")
print("")
print("[seed]   Start the app with 'make dev' and select '[DEV] Dev Studio'.")
