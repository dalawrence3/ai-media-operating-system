#!/usr/bin/env bash
# Start a backend dedicated to the Playwright E2E suite (Phase 18E).
#
# This script never reads .env.local. That is the point: the operator config
# holds live API keys and live publishing gates, and an E2E run must not be one
# `source` away from any of them.
#
# The E2E runtime is separated from the live Media OS along four axes at once,
# so no single mistake is enough to reach production state:
#
#   database  ACE_DB_PATH is a dedicated e2e-test.db, and app.core.runtime_mode
#             refuses to start if it ever resolves to the operational database
#   port      :8100, so a live backend on :8000 is never adopted by accident
#   mode      ACE_TEST_MODE=e2e, which refuses provider-effect operations
#   providers fake AI/TTS, live publishing and release gates hard off

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

E2E_DATA_DIR="${ACE_E2E_DATA_DIR:-$REPO_ROOT/.e2e-data}"
mkdir -p "$E2E_DATA_DIR" "$E2E_DATA_DIR/artifacts" "$E2E_DATA_DIR/oauth_tokens"

# Isolation — not overridable from the environment. Anything a caller exported
# is deliberately ignored here; this script has exactly one job.
export ACE_TEST_MODE=e2e
export ACE_DB_PATH="$E2E_DATA_DIR/e2e-test.db"
export ACE_ARTIFACTS_PATH="$E2E_DATA_DIR/artifacts"
export ACE_YOUTUBE_TOKEN_DIR="$E2E_DATA_DIR/oauth_tokens"

# Safety gates — every live-effect switch explicitly off.
export ACE_PUBLISHING_LIVE_ENABLED=false
export ACE_RELEASE_PUBLIC_ENABLED=false
export ACE_TTS_LIVE_ENABLED=false
export ACE_AI_PROVIDER=fake
export ACE_TTS_PROVIDER=fake
export ACE_DRY_RUN=1

# Credentials that must not exist in this process at all. Unsetting beats
# setting them empty: a provider that reads an empty key may still attempt a
# call and fail confusingly at the network boundary.
unset ACE_ELEVENLABS_API_KEY ACE_ANTHROPIC_API_KEY ANTHROPIC_API_KEY \
      ACE_YOUTUBE_API_KEY ACE_PEXELS_API_KEY ACE_PIXABAY_API_KEY \
      YOUTUBE_CLIENT_SECRETS_PATH YOUTUBE_CREDENTIALS_PATH 2>/dev/null || true

export ACE_ENV=development
export ACE_DEV_AUTH=enabled
export ACE_LOG_FORMAT=console
export ACE_BACKEND_PORT="${ACE_E2E_BACKEND_PORT:-8100}"
export ACE_FRONTEND_URL="${ACE_E2E_FRONTEND_URL:-http://localhost:5273}"

VENV="${VENV:-.venv}"

# Fail loudly BEFORE uvicorn binds a port, so a misconfiguration is a clear
# error message rather than a server that starts and then misbehaves.
"$VENV/bin/python" - <<'PYCHECK'
import os, sys
from pathlib import Path
sys.path.insert(0, "src")
from app.core.runtime_mode import RuntimeIsolationError, assert_runtime_isolation
try:
    assert_runtime_isolation(Path(os.environ["ACE_DB_PATH"]))
except RuntimeIsolationError as exc:
    print(f"\n  ✗  E2E ISOLATION CHECK FAILED\n\n{exc}\n", file=sys.stderr)
    raise SystemExit(1)
print(f"  ✓  E2E isolation: mode=e2e db={os.environ['ACE_DB_PATH']}")
PYCHECK

exec "$VENV/bin/python" -m uvicorn app.api.main:app \
  --host 127.0.0.1 \
  --port "$ACE_BACKEND_PORT" \
  --log-level info
