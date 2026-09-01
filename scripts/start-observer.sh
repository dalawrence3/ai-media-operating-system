#!/usr/bin/env bash
# Start the analytics observation scheduler daemon in development mode.
# Loaded by 'make dev' — do not run directly in production (use docker-compose).
#
# The daemon:
#   1. Reconciles any already-public publications into the observation schedule.
#   2. Polls every 60s for due analytics_observation schedules and runs them inline.
#   3. Restarts automatically if it crashes (via the PID-tracking in the Makefile).
#   4. Dispatches the autonomous decision, production and publishing cycles.
#
# This daemon IS the autonomous publisher — the publishing cycle runs here.
# The publishing safety gates therefore default to false but are not
# hard-wired: authorizing autonomous publishing means setting them in the
# git-ignored .env.local, alongside a per-channel authorization grant. With
# no override present this process starts unable to publish anything.

set -euo pipefail

ENV_FILE="${ENV_FILE:-.env.local}"
if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  set -a; source "$ENV_FILE"; set +a
fi

# Development defaults.
export ACE_ENV="${ACE_ENV:-development}"

# Safety gates — default OFF. These remain explicit runtime controls: the value
# is never hardcoded true in source, and with no override present the service
# starts with live publishing and public release disabled. Enabling requires a
# deliberate, operator-set value in .env.local (git-ignored), so an enabled gate
# can never be committed to the repository.
export ACE_PUBLISHING_LIVE_ENABLED="${ACE_PUBLISHING_LIVE_ENABLED:-false}"
export ACE_RELEASE_PUBLIC_ENABLED="${ACE_RELEASE_PUBLIC_ENABLED:-false}"
export ACE_LOG_FORMAT="${ACE_LOG_FORMAT:-console}"
export ACE_LOG_LEVEL="${ACE_LOG_LEVEL:-INFO}"

VENV="${VENV:-.venv}"
exec "$VENV/bin/python" -m app.workers.scheduler
