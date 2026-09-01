#!/usr/bin/env bash
# Start the FastAPI backend as a persistent local service (no --reload).
# Used by the launchd LaunchAgent (make service-install).
# For interactive development use 'make dev' instead (includes hot-reload).

set -euo pipefail

# Load .env.local so runtime config (API keys, DB path, etc.) is available.
# WorkingDirectory in the LaunchAgent plist ensures the relative path resolves.
ENV_FILE="${ENV_FILE:-.env.local}"
if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  set -a; source "$ENV_FILE"; set +a
fi

# Development defaults — applied only if not already set by .env.local.
export ACE_ENV="${ACE_ENV:-development}"
export ACE_DEV_AUTH="${ACE_DEV_AUTH:-enabled}"
export ACE_AI_PROVIDER="${ACE_AI_PROVIDER:-fake}"
export ACE_TTS_PROVIDER="${ACE_TTS_PROVIDER:-fake}"
export ACE_TTS_LIVE_ENABLED="${ACE_TTS_LIVE_ENABLED:-false}"
export ACE_LOG_FORMAT="${ACE_LOG_FORMAT:-console}"
export ACE_FRONTEND_URL="${ACE_FRONTEND_URL:-http://localhost:5173}"

# Safety gates — default OFF. These remain explicit runtime controls: the value
# is never hardcoded true in source, and with no override present the service
# starts with live publishing and public release disabled. Enabling requires a
# deliberate, operator-set value in .env.local (git-ignored), so an enabled gate
# can never be committed to the repository.
export ACE_PUBLISHING_LIVE_ENABLED="${ACE_PUBLISHING_LIVE_ENABLED:-false}"
export ACE_RELEASE_PUBLIC_ENABLED="${ACE_RELEASE_PUBLIC_ENABLED:-false}"

VENV="${VENV:-.venv}"
exec "$VENV/bin/python" -m uvicorn app.api.main:app \
  --host 127.0.0.1 \
  --port 8000 \
  --log-level info
