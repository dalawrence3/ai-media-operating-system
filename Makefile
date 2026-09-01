# AI Content Engine — developer workflow
#
# Prerequisites: Python 3.13 + .venv, Node 22 + frontend/node_modules
# Run 'make doctor' first to verify your environment.
# Copy .env.local.example → .env.local and fill in values before 'make dev'.

.DEFAULT_GOAL := help
SHELL         := /usr/bin/env bash -euo pipefail
PID_DIR       := .pids

# ── Environment loading ───────────────────────────────────────────────────────
# .env.local is git-ignored; override any value without touching .env.example.
# Never store real credentials in the repository.
ENV_FILE := .env.local

define load_env
$(if $(wildcard $(ENV_FILE)),set -a; source ./$(ENV_FILE); set +a;,)
endef

# ── Help ──────────────────────────────────────────────────────────────────────

.PHONY: help
help:
	@echo ""
	@echo "  AI Content Engine — development commands"
	@echo ""
	@echo "  make dev              Start backend (:8000) + frontend (:5173) + observer in background"
	@echo "  make stop             Stop background servers"
	@echo "  make restart          stop + dev"
	@echo "  make check            Fast pre-commit suite (ruff + typecheck + frontend tests)"
	@echo "  make verify           Full quality suite (matches CI)"
	@echo "  make e2e              Playwright E2E tests (isolated backend + test DB)"
	@echo "  make seed-e2e         Seed the isolated E2E database"
	@echo "  make e2e-reset        Delete the disposable E2E data directory"
	@echo "  make seed-dev         Populate local DB with deterministic dev data"
	@echo "  make doctor           Check environment prerequisites"
	@echo ""
	@echo "  Persistent macOS services (launchd — start at login, auto-restart):"
	@echo "  make service-install  Install + start backend/observer/frontend as LaunchAgents"
	@echo "  make service-status   Show launchd state + HTTP health"
	@echo "  make service-restart  Restart all LaunchAgents"
	@echo "  make service-stop     Stop all LaunchAgents (keep plists)"
	@echo "  make service-uninstall Remove all LaunchAgent plists"
	@echo ""
	@echo "  NOTE: Do NOT run 'make dev' and 'make service-install' simultaneously —"
	@echo "        both bind to ports 8000 and 5173."
	@echo ""

# ── Local development servers ─────────────────────────────────────────────────

.PHONY: dev
dev:
	@mkdir -p $(PID_DIR)
	@if [ -f $(PID_DIR)/backend.pid ] && kill -0 "$$(cat $(PID_DIR)/backend.pid)" 2>/dev/null; then \
	  echo "[dev] Backend already running (pid $$(cat $(PID_DIR)/backend.pid))"; \
	else \
	  echo "[dev] Starting backend on http://127.0.0.1:8000 …"; \
	  bash scripts/start-backend.sh & echo $$! > $(PID_DIR)/backend.pid; \
	fi
	@sleep 1
	@if [ -f $(PID_DIR)/frontend.pid ] && kill -0 "$$(cat $(PID_DIR)/frontend.pid)" 2>/dev/null; then \
	  echo "[dev] Frontend already running (pid $$(cat $(PID_DIR)/frontend.pid))"; \
	else \
	  echo "[dev] Starting frontend on http://localhost:5173 …"; \
	  bash scripts/start-frontend.sh & echo $$! > $(PID_DIR)/frontend.pid; \
	fi
	@if [ -f $(PID_DIR)/observer.pid ] && kill -0 "$$(cat $(PID_DIR)/observer.pid)" 2>/dev/null; then \
	  echo "[dev] Observer already running (pid $$(cat $(PID_DIR)/observer.pid))"; \
	else \
	  echo "[dev] Starting analytics observer (reconcile + 60s tick) …"; \
	  bash scripts/start-observer.sh & echo $$! > $(PID_DIR)/observer.pid; \
	fi
	@echo ""
	@echo "  Backend:  http://127.0.0.1:8000"
	@echo "  Frontend: http://localhost:5173"
	@echo "  API docs: http://127.0.0.1:8000/docs"
	@echo ""
	@echo "  Run 'make stop' to shut down."

.PHONY: stop
stop:
	@echo "[stop] Stopping servers…"
	@if [ -f $(PID_DIR)/backend.pid ]; then \
	  PID=$$(cat $(PID_DIR)/backend.pid); \
	  if kill -0 "$$PID" 2>/dev/null; then \
	    kill "$$PID" && echo "[stop] Backend stopped (pid $$PID)"; \
	  fi; \
	  rm -f $(PID_DIR)/backend.pid; \
	fi
	@if [ -f $(PID_DIR)/frontend.pid ]; then \
	  PID=$$(cat $(PID_DIR)/frontend.pid); \
	  if kill -0 "$$PID" 2>/dev/null; then \
	    kill "$$PID" && echo "[stop] Frontend stopped (pid $$PID)"; \
	  fi; \
	  rm -f $(PID_DIR)/frontend.pid; \
	fi
	@if [ -f $(PID_DIR)/observer.pid ]; then \
	  PID=$$(cat $(PID_DIR)/observer.pid); \
	  if kill -0 "$$PID" 2>/dev/null; then \
	    kill "$$PID" && echo "[stop] Observer stopped (pid $$PID)"; \
	  fi; \
	  rm -f $(PID_DIR)/observer.pid; \
	fi
	@# Also clean up any stray uvicorn/vite processes on our ports
	@lsof -ti :8000 | xargs -r kill 2>/dev/null || true
	@lsof -ti :5173 | xargs -r kill 2>/dev/null || true
	@echo "[stop] Done."

.PHONY: restart
restart: stop dev

# ── Fast pre-commit check ──────────────────────────────────────────────────────

.PHONY: check
check:
	@echo "[check] Ruff lint…"
	@.venv/bin/python -m ruff check src/ tests/
	@echo "[check] Ruff format…"
	@.venv/bin/python -m ruff format --check src/ tests/
	@echo "[check] Frontend typecheck…"
	@cd frontend && npm run typecheck --if-present
	@echo "[check] Frontend lint…"
	@cd frontend && npm run lint --if-present
	@echo "[check] Frontend unit tests…"
	@cd frontend && npm test -- --run
	@echo "[check] ✓ All checks passed."

# ── Full CI-equivalent verification suite ─────────────────────────────────────

.PHONY: verify
verify:
	@echo "[verify] Ruff lint…"
	@.venv/bin/python -m ruff check src/ tests/
	@echo "[verify] Ruff format check…"
	@.venv/bin/python -m ruff format --check src/ tests/
	@echo "[verify] Backend tests (pytest)…"
	@ACE_DRY_RUN=1 \
	 ACE_AI_PROVIDER=fake \
	 ACE_TTS_PROVIDER=fake \
	 ACE_TTS_LIVE_ENABLED=false \
	 ACE_PUBLISHING_LIVE_ENABLED=false \
	 .venv/bin/python -m pytest -x -q --tb=short --no-header tests/
	@echo "[verify] Frontend lint…"
	@cd frontend && npm run lint --if-present
	@echo "[verify] Frontend typecheck…"
	@cd frontend && npm run typecheck --if-present
	@echo "[verify] Frontend unit tests…"
	@cd frontend && npm test -- --run --reporter=verbose
	@echo "[verify] Frontend build…"
	@cd frontend && npm run build
	@echo "[verify] ✓ Full suite passed."

# ── Playwright end-to-end tests ────────────────────────────────────────────────
# Playwright starts its OWN isolated backend (:8100, e2e-test.db) and frontend
# (:5273) via webServer config. It never touches the live :8000/:5173 stack or
# the operational database — see scripts/start-e2e-backend.sh and
# src/app/core/runtime_mode.py.

.PHONY: e2e
e2e:
	@echo "[e2e] Running Playwright tests (isolated backend :8100 / e2e-test.db)…"
	@cd frontend && npx playwright test

.PHONY: e2e-ui
e2e-ui:
	@cd frontend && npx playwright test --ui

.PHONY: e2e-report
e2e-report:
	@cd frontend && npx playwright show-report

.PHONY: seed-e2e
seed-e2e:
	@echo "[e2e] Seeding the isolated E2E database…"
	@ACE_TEST_MODE=e2e ACE_ENV=development \
	 ACE_DB_PATH=$(CURDIR)/.e2e-data/e2e-test.db \
	 ACE_ARTIFACTS_PATH=$(CURDIR)/.e2e-data/artifacts \
	 .venv/bin/python scripts/seed-dev.py

.PHONY: e2e-reset
e2e-reset:
	@echo "[e2e] Removing the disposable E2E data directory…"
	@rm -rf $(CURDIR)/.e2e-data
	@echo "[e2e] Done. The next 'make e2e' will recreate and reseed it."

# ── Development data seeding ──────────────────────────────────────────────────

.PHONY: seed-dev
seed-dev:
	@echo "[seed] Seeding local development database…"
	@$(load_env) .venv/bin/python scripts/seed-dev.py
	@echo "[seed] Done. Run 'make dev' to start the app."

# ── Environment health check ──────────────────────────────────────────────────

.PHONY: doctor
doctor:
	@bash scripts/doctor.sh

# ── Convenience targets ───────────────────────────────────────────────────────

.PHONY: install
install:
	@echo "[install] Installing Python dependencies…"
	@pip install -e ".[dev]"
	@echo "[install] Installing frontend dependencies…"
	@cd frontend && npm install
	@echo "[install] Installing Playwright browsers…"
	@cd frontend && npx playwright install --with-deps
	@echo "[install] ✓ Done. Run 'make doctor' to verify."

.PHONY: clean
clean:
	@echo "[clean] Removing build artifacts…"
	@rm -rf frontend/dist frontend/playwright-report frontend/test-results
	@find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@find . -name "*.pyc" -delete 2>/dev/null || true
	@echo "[clean] Done."

# ── Persistent macOS LaunchAgent services ─────────────────────────────────────
# Uses launchd (no Docker, Homebrew services, PM2, or supervisord required).
# Services start at login, restart after crashes, and log to:
#   ~/.local/share/ai-content-engine/logs/
#
# IMPORTANT: Do NOT run 'make dev' while launchd services are active.
# Both bind to ports 8000 and 5173. 'make service-install' calls 'make stop'
# automatically to prevent conflicts.
#
# Services are:
#   com.aicontentengine.backend   — FastAPI :8000 (no --reload)
#   com.aicontentengine.observer  — analytics scheduler daemon
#   com.aicontentengine.frontend  — Vite dev server :5173
#
# Sleep / power semantics:
#   Services are active while the macOS user session is open. launchd restarts
#   them after login or crash. They pause during system sleep and are not
#   available while the Mac is powered off.

.PHONY: service-install
service-install:
	@bash scripts/service.sh install

.PHONY: service-status
service-status:
	@bash scripts/service.sh status

.PHONY: service-restart
service-restart:
	@bash scripts/service.sh restart

.PHONY: service-stop
service-stop:
	@bash scripts/service.sh stop

.PHONY: service-uninstall
service-uninstall:
	@bash scripts/service.sh uninstall
