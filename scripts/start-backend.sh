#!/usr/bin/env bash
# Start the FastAPI backend in development mode.
# Loaded by 'make dev' — do not run directly in production.

set -euo pipefail

# Load .env.local if present (git-ignored, never committed).
#
# Phase 18E: values already present in the environment WIN over the file.
#
# This used to be `set -a; source "$ENV_FILE"; set +a`, which assigns
# unconditionally — so every safety variable a caller had deliberately exported
# (ACE_PUBLISHING_LIVE_ENABLED=false, ACE_TTS_PROVIDER=fake, ACE_DB_PATH=...)
# was silently overwritten by the live operator config. That is how the
# Playwright suite, whose config sets exactly those variables, ended up running
# against the live backend with live publishing enabled.
ENV_FILE="${ENV_FILE:-.env.local}"
if [ -f "$ENV_FILE" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      ''|'#'*) continue ;;
    esac
    key="${line%%=*}"
    # Skip anything that is not a plain KEY=VALUE assignment.
    case "$key" in
      *[!A-Za-z0-9_]*|'') continue ;;
    esac
    # Only apply the file's value when the variable is not already set.
    if [ -z "${!key+x}" ]; then
      value="${line#*=}"
      export "$key=$value"
    fi
  done < "$ENV_FILE"
fi

# Development defaults — only applied if not already set.
export ACE_ENV="${ACE_ENV:-development}"
export ACE_DEV_AUTH="${ACE_DEV_AUTH:-enabled}"
export ACE_AI_PROVIDER="${ACE_AI_PROVIDER:-fake}"
export ACE_TTS_PROVIDER="${ACE_TTS_PROVIDER:-fake}"
export ACE_TTS_LIVE_ENABLED="${ACE_TTS_LIVE_ENABLED:-false}"
export ACE_PUBLISHING_LIVE_ENABLED="${ACE_PUBLISHING_LIVE_ENABLED:-false}"
export ACE_DRY_RUN="${ACE_DRY_RUN:-1}"
export ACE_LOG_FORMAT="${ACE_LOG_FORMAT:-console}"
export ACE_BACKEND_PORT="${ACE_BACKEND_PORT:-8000}"
export ACE_FRONTEND_URL="${ACE_FRONTEND_URL:-http://localhost:5173}"

VENV="${VENV:-.venv}"
exec "$VENV/bin/python" -m uvicorn app.api.main:app \
  --reload \
  --host 127.0.0.1 \
  --port "$ACE_BACKEND_PORT" \
  --log-level info
