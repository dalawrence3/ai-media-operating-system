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
	@echo "  make dev        Start backend (:8000) + frontend (:5173) in background"
	@echo "  make stop       Stop background servers"
	@echo "  make restart    stop + dev"
	@echo "  make check      Fast pre-commit suite (ruff + typecheck + frontend tests)"
	@echo "  make verify     Full quality suite (matches CI)"
	@echo "  make e2e        Playwright end-to-end tests (starts servers automatically)"
	@echo "  make seed-dev   Populate local DB with deterministic dev data"
	@echo "  make doctor     Check environment prerequisites"
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
# Playwright starts its own dev server via webServer config — no 'make dev' needed.

.PHONY: e2e
e2e:
	@echo "[e2e] Running Playwright tests…"
	@cd frontend && npx playwright test

.PHONY: e2e-ui
e2e-ui:
	@cd frontend && npx playwright test --ui

.PHONY: e2e-report
e2e-report:
	@cd frontend && npx playwright show-report

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
