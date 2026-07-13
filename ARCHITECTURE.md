# Architecture

## Overview

A single modular Python application (not a distributed system). Deterministic
code handles file I/O, database access, scheduling, validation, and uploads;
an LLM is used only where language understanding, creative judgment, or
qualitative analysis is genuinely needed (script generation, critique,
performance analysis).

## Package layout

```
ai-content-engine/
├── pyproject.toml
├── .env.example
├── .gitignore
├── README.md / PROJECT_SPEC.md / ARCHITECTURE.md / TASKS.md / DECISIONS.md
├── src/
│   └── app/
│       ├── __init__.py          # package version
│       ├── __main__.py          # `python -m app` entry point
│       ├── cli.py               # Typer CLI (version, doctor, topics, sources, scripts, runs)
│       └── core/
│           ├── __init__.py
│           ├── config.py        # Config singleton (ACE_DB_PATH, ACE_LOG_LEVEL)
│           ├── database.py      # SQLite connection, schema init, version guard
│           ├── logging.py       # Structured logging configuration
│           ├── models.py        # Pydantic domain models (Topic, Source, Script, Run)
│           └── repository.py    # Typed CRUD functions over sqlite3.Connection
└── tests/
    ├── conftest.py              # db fixture
    ├── test_cli.py
    ├── test_database.py
    ├── test_models.py
    └── test_repository.py
```

As later phases add functionality, new modules will be added under `src/app/`
(e.g. `core/` for data models and the database layer, `llm/` for the LLM
provider interface, `content/` for the Creator and Critic roles, `media/`
for asset tracking and rendering, `publish/` for platform uploads,
`analytics/` for the Performance Analyst). Nothing beyond the CLI skeleton
exists yet — this section fills in phase by phase rather than being
speculatively built now.

## Why `src/` layout

A flat layout (`app/` at the repo root) lets `import app` succeed from the
working directory even if the package was never actually installed, which
can mask real packaging bugs until first deployment. Putting the package
under `src/` and installing it in editable mode (`pip install -e .`) forces
every import — including in tests — through the same path a real install
would use, catching packaging mistakes immediately instead of later.

## Why no database, LLM, or rendering code yet

Per the project's incremental-build rule, each phase's code depends only on
what's actually implemented and tested in prior phases. Phase 0 exists to
prove the harness (package, venv, tests, linting) works before anything
depends on it.

## Current state (Phase 1)

- SQLite database at `~/.local/share/ai-content-engine/content.db` (override
  via `ACE_DB_PATH`). WAL journal mode, foreign keys enforced.
- Versioned schema (integer `schema_version` table guards against accidental
  re-init or future downgrade).
- Four entities: `Topic`, `Source`, `Script`, `Run` — validated by Pydantic,
  persisted via a typed repository layer.
- Typer CLI with `topics`, `sources`, `scripts`, and `runs` subcommand groups
  plus the Phase 0 `version` and `doctor` diagnostics.
- Stdlib structured logging controlled by `ACE_LOG_LEVEL` (default `WARNING`).
- No LLM calls, no external integrations.
