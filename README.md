# AI Content Production Engine

An AI-assisted content production pipeline: research a topic, generate a
structured short-form video script, critique it against a fixed rubric,
get human approval, generate narration, assemble a vertical video, and
(eventually) publish privately/unlisted to YouTube Shorts — with metrics
feedback driving one controlled experiment at a time.

This is a portfolio project as much as a product: every phase is built
incrementally, fully tested, and documented before the next begins. See
`PROJECT_SPEC.md` for scope and non-goals, `ARCHITECTURE.md` for the system
design, `TASKS.md` for current status, and `DECISIONS.md` for the reasoning
behind key technical choices.

## Current status

**Phase 3 (YouTube Opportunity Intelligence) is complete.**

The system can discover content opportunities, score them, and promote them
to active topics through an explicit operator approval step. 513 tests pass.

Implemented phases:
- **Phase 0** — environment, diagnostic CLI
- **Phase 1** — core data model (`Topic`, `Source`, `Script`, `Run`; SQLite; CLI)
- **Phase 2** — LLM abstraction (`FakeProvider`, `ClaudeProvider`, prompt
  registry, structured output, cost tracking)
- **Phase 3** — YouTube Opportunity Intelligence:
  - M3.1: versioned channel strategy (profile snapshots, monetization
    strategy, capacity policy, operating mode log)
  - M3.2: discovery foundation (discovery runs, opportunities, observations,
    source evidence, lifecycle state events, adapters, dedup)
  - M3.3: scoring and confidence engine (versioned policies, 6 factors,
    missing-data policies, confidence calculation, append-only score records)
  - M3.4: opportunity promotion (`ace topics promote`; SAVEPOINT atomicity;
    idempotent; score prerequisite; lifecycle guard)

End-to-end workflow: channel strategy → discovery run → evidence collection
→ deterministic scoring → explicit operator promotion → active Topic.

## Requirements

- Python 3.13 (see `DECISIONS.md` for why this version)
- Developed primarily on macOS

## Setup

```bash
python3.13 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

## Usage

```bash
python -m app version
python -m app doctor
python -m app --help
```

## Running tests

```bash
pytest
```

## Linting / formatting

```bash
ruff check .
ruff format .
```
