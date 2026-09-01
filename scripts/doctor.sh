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

# ── Autonomous publishing posture (Phase 18C/18D) ─────────────────────────────
#
# The global gates are NOT "must always be false". Since Phase 18C they are two
# of FOUR independent authorization layers, and a channel running normal
# autonomous operation has them deliberately enabled:
#
#   1. ACE_PUBLISHING_LIVE_ENABLED   global kill switch (env)
#   2. ACE_RELEASE_PUBLIC_ENABLED    global kill switch (env)
#   3. per-channel authorization     persisted, revocable, re-checked between
#                                    upload and release
#   4. runtime safety checks         rate ceiling, account health, scopes
#
# Treating an enabled gate as inherently invalid — as this check used to — is a
# false alarm on a correctly authorized system, and worse, it trains an
# operator to ignore the one check that would catch a genuinely unsafe
# configuration. So: report the posture, and fail only on combinations that are
# actually incoherent or unbounded.
#
# Strictly read-only. doctor never grants, revokes, or changes anything.

LIVE_ENABLED="${ACE_PUBLISHING_LIVE_ENABLED:-false}"
REL_ENABLED="${ACE_RELEASE_PUBLIC_ENABLED:-false}"

DB_PATH="${ACE_DB_PATH:-$HOME/.local/share/ai-content-engine/content.db}"

# Read-only helper. Prints nothing and returns non-zero when the DB or the
# sqlite3 client is unavailable, so every caller must handle "unknown".
db_query() {
  if ! command -v sqlite3 &>/dev/null || [ ! -f "$DB_PATH" ]; then
    return 1
  fi
  sqlite3 "file:$DB_PATH?mode=ro" "$1" 2>/dev/null
}

if [ "$LIVE_ENABLED" != "true" ] && [ "$REL_ENABLED" != "true" ]; then
  pass "Publishing gates both off — no process can upload or release (stood down)"
elif [ "$LIVE_ENABLED" = "true" ] && [ "$REL_ENABLED" != "true" ]; then
  warn "ACE_PUBLISHING_LIVE_ENABLED=true but ACE_RELEASE_PUBLIC_ENABLED=false — uploads may occur; nothing can be made public"
elif [ "$LIVE_ENABLED" != "true" ] && [ "$REL_ENABLED" = "true" ]; then
  warn "ACE_RELEASE_PUBLIC_ENABLED=true but ACE_PUBLISHING_LIVE_ENABLED=false — release gate has no effect while uploads are disabled"
else
  pass "Publishing gates both on — autonomous publishing is globally permitted"

  # With the global gates open, the remaining layers are what actually bound
  # the system. Rather than reimplement those checks in shell — where they
  # would drift from the real ones — ask the canonical evaluator. It is
  # read-only, and it is the exact function the publishing cycle consults
  # before every external side effect, so what doctor reports is what the
  # system will actually do.
  if [ ! -x ".venv/bin/python" ] || [ ! -f "$DB_PATH" ]; then
    warn "Cannot evaluate publishing posture (no .venv/bin/python or no database at $DB_PATH)"
  else
    POSTURE=$(ACE_DB_PATH="$DB_PATH" .venv/bin/python - <<'PYEOF' 2>/dev/null
from pathlib import Path
import os

from app.core.database import open_db
from app.publishing.authorization import (
    BLOCKING_ACCOUNT_STATUSES,
    _has_release_scope,
    evaluate_publishing_authorization,
    get_publishing_account,
)

conn = open_db(Path(os.environ["ACE_DB_PATH"]))
rows = conn.execute(
    "SELECT channel_id, max_publications_per_24h FROM channel_publishing_authorizations "
    "WHERE authorized = 1"
).fetchall()

if not rows:
    print("PASS|No channel is authorized for autonomous publishing — layer 3 holds despite open gates")
else:
    print(f"PASS|{len(rows)} channel(s) authorized for autonomous publishing")

for row in rows:
    ch = row["channel_id"]
    short = ch[:8]
    ceiling = row["max_publications_per_24h"]

    if ceiling is None or ceiling <= 0:
        print(f"FAIL|Channel {short} is authorized but has no positive publication "
              "ceiling — autonomous publishing would be unbounded")
    else:
        print(f"PASS|Channel {short} publication ceiling: {ceiling} per trailing 24h")

    account_id, status = get_publishing_account(conn, ch)
    if account_id is None:
        print(f"FAIL|Channel {short} is authorized but has no connected YouTube account")
    elif status in BLOCKING_ACCOUNT_STATUSES:
        print(f"FAIL|Channel {short} is authorized but account status '{status}' blocks publishing")
    else:
        print(f"PASS|Channel {short} account status: {status}")

        # Upload and public release are separate OAuth grants. Authorized,
        # gates open, no release scope means uploads strand private.
        if _has_release_scope(conn, account_id=account_id, channel_id=ch):
            print(f"PASS|Channel {short} holds the public-release scope (youtube.force-ssl)")
        else:
            print(f"FAIL|Channel {short} is authorized to publish but lacks youtube.force-ssl "
                  "— uploads would be stranded private")

    decision = evaluate_publishing_authorization(conn, channel_id=ch)
    if decision.allowed:
        print(f"PASS|Channel {short} may publish now "
              f"({decision.publications_last_24h}/{decision.max_publications_per_24h} used in 24h)")
    else:
        reasons = ", ".join(r.value for r in decision.blocked_by)
        if reasons == "rate_limit_reached":
            print(f"PASS|Channel {short} is at its 24h ceiling — the limit is working, not a fault")
        else:
            print(f"WARN|Channel {short} cannot publish right now: {reasons}")

    sched = conn.execute(
        "SELECT is_active FROM app_schedule_definitions "
        "WHERE operation_type = 'autonomous_publishing_cycle' AND channel_id = ? LIMIT 1",
        (ch,),
    ).fetchone()
    if sched is None:
        print(f"WARN|Channel {short} has no publishing-cycle schedule defined")
    elif sched["is_active"]:
        print(f"PASS|Channel {short} publishing scheduler is active")
    else:
        print(f"WARN|Channel {short} is authorized but its publishing scheduler is inactive")
PYEOF
    )

    if [ -z "$POSTURE" ]; then
      warn "Publishing posture could not be evaluated — verify channel authorization manually"
    else
      while IFS='|' read -r level message; do
        [ -z "$level" ] && continue
        case "$level" in
          PASS) pass "$message" ;;
          WARN) warn "$message" ;;
          FAIL) fail "$message" ;;
        esac
      done <<< "$POSTURE"
    fi
  fi
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
