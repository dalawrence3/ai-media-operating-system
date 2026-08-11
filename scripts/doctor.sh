#!/usr/bin/env bash
# AI Content Engine — environment health check
# Usage: make doctor  OR  bash scripts/doctor.sh
#
# Prints PASS / WARN / FAIL for each prerequisite.
# Exit code 0 = all PASS/WARN, 1 = any FAIL.

set -uo pipefail

RED='\033[0;31m'
YEL='\033[0;33m'
GRN='\033[0;32m'
RST='\033[0m'

PASS=0; WARN=0; FAIL=0

pass() { echo -e "  ${GRN}PASS${RST}  $1"; ((PASS++)); }
warn() { echo -e "  ${YEL}WARN${RST}  $1"; ((WARN++)); }
fail() { echo -e "  ${RED}FAIL${RST}  $1"; ((FAIL++)); }

echo ""
echo "  AI Content Engine — doctor"
echo "  ────────────────────────────────────────"

# ── Python ────────────────────────────────────────────────────────────────────
PY_MIN="3.13"
if command -v python3 &>/dev/null; then
  PY_VER=$(python3 --version 2>&1 | awk '{print $2}')
  if python3 -c "import sys; sys.exit(0 if sys.version_info >= (3,13) else 1)" 2>/dev/null; then
    pass "Python $PY_VER (≥ $PY_MIN required)"
  else
    fail "Python $PY_VER is too old — need ≥ $PY_MIN"
  fi
else
  fail "python3 not found — install Python 3.13+"
fi

# ── Virtual environment ────────────────────────────────────────────────────────
if [ -d ".venv" ]; then
  pass "Virtualenv .venv exists"
else
  fail ".venv not found — run: python3 -m venv .venv && pip install -e '.[dev]'"
fi

# ── Python packages ───────────────────────────────────────────────────────────
REQUIRED_PKGS=(fastapi uvicorn pytest ruff pydantic typer)
for pkg in "${REQUIRED_PKGS[@]}"; do
  if .venv/bin/python -c "import $pkg" 2>/dev/null || \
     .venv/bin/python -c "import importlib; importlib.import_module('${pkg//-/_}')" 2>/dev/null; then
    pass "Python package: $pkg"
  else
    fail "Python package '$pkg' not installed — run: pip install -e '.[dev]'"
  fi
done

# ── Node / npm ────────────────────────────────────────────────────────────────
if command -v node &>/dev/null; then
  NODE_VER=$(node --version)
  NODE_MAJOR=$(echo "$NODE_VER" | sed 's/v//' | cut -d. -f1)
  if [ "$NODE_MAJOR" -ge 22 ] 2>/dev/null; then
    pass "Node $NODE_VER (≥ v22 required)"
  else
    warn "Node $NODE_VER — v22+ recommended (CI uses v22)"
  fi
else
  fail "node not found — install Node.js 22+"
fi

if command -v npm &>/dev/null; then
  pass "npm $(npm --version)"
else
  fail "npm not found"
fi

# ── Frontend dependencies ─────────────────────────────────────────────────────
if [ -d "frontend/node_modules" ]; then
  pass "frontend/node_modules present"
else
  fail "frontend/node_modules missing — run: cd frontend && npm install"
fi

if [ -d "frontend/node_modules/@playwright" ]; then
  pass "Playwright npm package installed"
  # Verify browser binaries are actually downloaded (not just the package).
  if cd frontend && npx playwright --version &>/dev/null && \
     node -e "require('@playwright/test'); process.exit(0)" 2>/dev/null; then
    CHROME_DIR=$(ls "${HOME}/Library/Caches/ms-playwright/" 2>/dev/null | grep "^chromium" | head -1 || true)
    if [ -n "$CHROME_DIR" ]; then
      pass "Playwright browser binaries present ($CHROME_DIR)"
    else
      warn "Playwright browser binaries missing — run: cd frontend && npx playwright install"
    fi
  fi
  cd - &>/dev/null || true
else
  warn "Playwright not installed — run: cd frontend && npm install && npx playwright install"
fi

# ── Database ──────────────────────────────────────────────────────────────────
# Detect configured DB path (SQLite local default).
ACE_DB_PATH="${ACE_DB_PATH:-$HOME/.local/share/ai-content-engine/content.db}"
if [ -f "$ACE_DB_PATH" ]; then
  pass "SQLite database: $ACE_DB_PATH"
else
  warn "SQLite database not found at $ACE_DB_PATH — will be created on first run"
fi

# ── .env.local ────────────────────────────────────────────────────────────────
if [ -f ".env.local" ]; then
  pass ".env.local present"
else
  warn ".env.local not found — copy .env.local.example and fill in values"
fi

# ── Port availability ─────────────────────────────────────────────────────────
for port in 8000 5173; do
  if lsof -ti ":$port" &>/dev/null; then
    warn "Port $port is in use — run 'make stop' if a previous dev server is running"
  else
    pass "Port $port available"
  fi
done

# ── Required env vars (development) ──────────────────────────────────────────
ENV_FILE=".env.local"
if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  set -a; source "$ENV_FILE"; set +a
fi

ACE_ENV="${ACE_ENV:-}"
if [ "$ACE_ENV" = "development" ]; then
  pass "ACE_ENV=development"
else
  warn "ACE_ENV not set to 'development' — dev auth will not be available"
fi

# ── Safety gate: ACE_PUBLISHING_LIVE_ENABLED must be false in dev ─────────────
LIVE_ENABLED="${ACE_PUBLISHING_LIVE_ENABLED:-false}"
if [ "$LIVE_ENABLED" = "true" ]; then
  fail "ACE_PUBLISHING_LIVE_ENABLED=true — THIS MUST NOT BE SET IN LOCAL DEV. Unset it or set to 'false'."
else
  pass "ACE_PUBLISHING_LIVE_ENABLED=false (safety gate OK)"
fi

# ── Optional OAuth config (presence only, never expose values) ────────────────
YT_PATH="${YOUTUBE_CLIENT_SECRETS_PATH:-}"
if [ -n "$YT_PATH" ] && [ -f "$YT_PATH" ]; then
  pass "YouTube client_secrets.json present (path from env)"
else
  warn "YOUTUBE_CLIENT_SECRETS_PATH not set or file not found — YouTube OAuth unavailable"
fi

YT_TOKEN_DIR="${ACE_YOUTUBE_TOKEN_DIR:-$HOME/.local/share/ai-content-engine/oauth_tokens}"
if [ -d "$YT_TOKEN_DIR" ]; then
  TOKEN_COUNT=$(find "$YT_TOKEN_DIR" -name "*.json" 2>/dev/null | wc -l | tr -d ' ')
  pass "OAuth token directory: $YT_TOKEN_DIR ($TOKEN_COUNT token file(s))"
else
  warn "OAuth token directory not found: $YT_TOKEN_DIR — will be created when first token is stored"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo "  ────────────────────────────────────────"
echo -e "  ${GRN}PASS${RST} $PASS   ${YEL}WARN${RST} $WARN   ${RED}FAIL${RST} $FAIL"
echo ""

if [ "$FAIL" -gt 0 ]; then
  echo "  ✗ Fix the FAIL items above before running 'make dev'."
  echo ""
  exit 1
elif [ "$WARN" -gt 0 ]; then
  echo "  ⚠  WARNs are non-blocking but may limit functionality."
  echo ""
  exit 0
else
  echo "  ✓ Environment is ready. Run 'make dev' to start."
  echo ""
  exit 0
fi
