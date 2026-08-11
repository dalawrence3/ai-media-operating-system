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
from dataclasses import dataclass
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

from app.core.config import get_config, reset_config  # noqa: E402
from app.core.database import open_db  # noqa: E402
from app.control_plane import repository as repo  # noqa: E402
from app.control_plane.models import (  # noqa: E402
    ChannelDraft,
    PlatformAccountDraft,
    WorkspaceDraft,
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
    {"slug": "dev-tech-shorts",   "name": "[DEV] Tech Shorts",   "desc": "Dev seed — technology"},
    {"slug": "dev-finance-clips", "name": "[DEV] Finance Clips",  "desc": "Dev seed — finance"},
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
conn.close()

print("")
print("[seed] ✓ Done.")
print(f"[seed]   Workspace slug:  {WS_SLUG}  id: {ws.id}")
print(f"[seed]   Channels: {len(channels)}")
print("")
print("[seed]   Start the app with 'make dev' and select '[DEV] Dev Studio'.")
