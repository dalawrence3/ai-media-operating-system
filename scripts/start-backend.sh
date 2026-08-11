#!/usr/bin/env bash
# Start the FastAPI backend in development mode.
# Loaded by 'make dev' — do not run directly in production.

set -euo pipefail

# Load .env.local if present (git-ignored, never committed).
ENV_FILE="${ENV_FILE:-.env.local}"
if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  set -a; source "$ENV_FILE"; set +a
fi

# Development defaults — only applied if not already set by .env.local.
export ACE_ENV="${ACE_ENV:-development}"
export ACE_DEV_AUTH="${ACE_DEV_AUTH:-enabled}"
export ACE_AI_PROVIDER="${ACE_AI_PROVIDER:-fake}"
export ACE_TTS_PROVIDER="${ACE_TTS_PROVIDER:-fake}"
export ACE_TTS_LIVE_ENABLED="${ACE_TTS_LIVE_ENABLED:-false}"
export ACE_PUBLISHING_LIVE_ENABLED="${ACE_PUBLISHING_LIVE_ENABLED:-false}"
export ACE_DRY_RUN="${ACE_DRY_RUN:-1}"
export ACE_LOG_FORMAT="${ACE_LOG_FORMAT:-console}"
export ACE_FRONTEND_URL="${ACE_FRONTEND_URL:-http://localhost:5173}"

VENV="${VENV:-.venv}"
exec "$VENV/bin/python" -m uvicorn app.api.main:app \
  --reload \
  --host 127.0.0.1 \
  --port 8000 \
  --log-level info
