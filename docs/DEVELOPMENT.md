# Development Guide

AI Content Engine — local development, testing, and contribution workflow.

---

## Prerequisites

| Tool | Version | Notes |
|------|---------|-------|
| Python | 3.13+ | `python3 --version` |
| Node.js | 22+ | `node --version` |
| npm | 10+ | Comes with Node |

---

## First-time setup

```bash
# 1. Clone and enter the repo
git clone <repo-url>
cd ai-content-engine

# 2. Create virtualenv and install Python deps
python3 -m venv .venv
pip install -e ".[dev]"

# 3. Install frontend deps + Playwright
cd frontend && npm install && npx playwright install --with-deps
cd ..

# 4. Create your local env file (never committed)
cp .env.local.example .env.local
# Edit .env.local — the defaults work for local dev with no external services.

# 5. Verify your environment
make doctor

# 6. Seed development data
make seed-dev

# 7. Start the app
make dev
```

Open [http://localhost:5173](http://localhost:5173) and select "Dev Studio".

---

## make dev

Starts both servers in the background:

- **Backend** — FastAPI on `http://127.0.0.1:8000` (uvicorn `--reload`)
- **Frontend** — Vite on `http://localhost:5173` (hot reload)
- **API docs** — `http://127.0.0.1:8000/docs`

```bash
make dev
make stop      # kill background servers
make restart   # stop + dev
```

Both servers log to the terminal that ran `make dev`. Press `Ctrl-C` in that
terminal or run `make stop` in another to shut down.

---

## Environment variables

Copy `.env.local.example` → `.env.local`. Key variables for local development:

| Variable | Default | Notes |
|----------|---------|-------|
| `ACE_ENV` | `production` | Set to `development` to enable dev auth |
| `ACE_DEV_AUTH` | `enabled` | Dev actor header accepted when `ACE_ENV=development` |
| `ACE_AI_PROVIDER` | `fake` | No API key needed; set to `claude` for real calls |
| `ACE_TTS_PROVIDER` | `fake` | No API key needed |
| `ACE_PUBLISHING_LIVE_ENABLED` | `false` | Never set to `true` locally |
| `ACE_DRY_RUN` | `1` | Forces fake provider |
| `ACE_FRONTEND_URL` | `http://localhost:5173` | Required for OAuth redirect |

**Never commit `.env.local` or any credential file.**

---

## make doctor

Checks your environment and prints PASS / WARN / FAIL for each prerequisite:

```bash
make doctor
```

Covers: Python version, virtualenv, required packages, Node, npm,
frontend `node_modules`, Playwright install, database path, port availability,
env vars, OAuth config presence (without exposing values).

---

## make seed-dev

Creates deterministic local development data:

```bash
make seed-dev
```

Creates (idempotent — safe to run multiple times):
- Workspace: `[DEV] Dev Studio` (slug: `dev-studio`)
- Channels: `[DEV] Tech Shorts`, `[DEV] Finance Clips`
- Fake YouTube platform accounts (no real tokens)

**Never seeds real OAuth tokens or credentials.**

---

## make check

Fast pre-commit quality check (seconds):

```bash
make check
```

Runs: ruff lint → ruff format check → frontend typecheck → frontend lint → frontend unit tests.

---

## make verify

Full CI-equivalent suite (matches `.github/workflows/ci.yml`):

```bash
make verify
```

Runs: ruff → pytest (all backend tests) → frontend lint → typecheck → unit tests → production build.

Use this before opening a PR.

---

## End-to-end tests

Playwright E2E tests live in `frontend/e2e/`. They start both servers automatically.

```bash
make e2e          # headless, all browsers
make e2e-ui       # interactive UI mode
make e2e-report   # view last test report
```

E2E tests use **dev auth** (`X-Dev-Actor: dev:studio-user`) — no real Google
OAuth, no real YouTube calls, no live publishing.

The `seed-dev` workspace must exist before running E2E tests. Run `make seed-dev` first.

---

## Credential handling

| File | Location | Notes |
|------|----------|-------|
| `client_secret*.json` | **Never in repo** | gitignored |
| OAuth tokens | `~/.local/share/ai-content-engine/oauth_tokens/` | Per-account, 0o600 |
| `.env.local` | **Never in repo** | gitignored |

To test real YouTube OAuth locally:
1. Set `YOUTUBE_CLIENT_SECRETS_PATH` in `.env.local`
2. Set `ACE_YOUTUBE_REDIRECT_URI=http://localhost:8000/api/v1/oauth/youtube/callback`
3. Ensure your Google Cloud Console has `http://localhost:8000/...` as an authorized redirect

---

## Safe real-service testing

For real API calls (Claude, ElevenLabs, YouTube):
1. Keep `ACE_PUBLISHING_LIVE_ENABLED=false` unless you intend to publish
2. Keep `ACE_TTS_LIVE_ENABLED=false` unless you intend to synthesize audio
3. Set `ACE_DRY_RUN=0` and the appropriate provider + API key
4. Never share tokens or keys — keep them only in `.env.local`

---

## Git workflow

```bash
# Always branch from main
git checkout main && git pull
git checkout -b feat/my-feature

# Before committing
make check

# Before PR
make verify

# Never commit to main directly
```

Branch naming: `feat/...`, `fix/...`, `chore/...`.

PR target: `main` (or `phase-2-ai-foundation` for in-progress phase work).

---

## CI alignment

`make verify` mirrors `.github/workflows/ci.yml` step-for-step:

| CI step | Local equivalent |
|---------|-----------------|
| `ruff check src/ tests/` | `make verify` (step 1) |
| `ruff format --check` | `make verify` (step 2) |
| `pytest -x -q` | `make verify` (step 3) |
| `npm run lint` | `make verify` (step 4) |
| `npm run typecheck` | `make verify` (step 5) |
| `npm test -- --run` | `make verify` (step 6) |
| `npm run build` | `make verify` (step 7) |

E2E tests are not in CI yet — they run locally via `make e2e`.

---

## Architecture notes

```
Browser → FastAPI (api/routes/) → ApplicationService → Control Plane → Engines
```

- All state mutations go through `ApplicationService` — no direct DB writes in routes
- Frontend: `src/api/client.ts` is the only HTTP boundary — no `fetch()` in pages
- Dev auth: `X-Dev-Actor` header accepted when `ACE_ENV=development && ACE_DEV_AUTH=enabled`
- Production auth: JWT Bearer token required; `ACE_SECRET_KEY` must be set (≥32 bytes)
- Schema version managed by migration branches in `src/app/core/database.py`

See `ARCHITECTURE.md` and `DECISIONS.md` for full design documentation.
