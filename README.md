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

**Phase 4 Milestone 4.1 (Source Ingestion) is complete.**

The system can fetch URLs, ingest local files, extract content, compute
deterministic quality scores, and persist source content with full
idempotency and SSRF protection. 696 tests pass.

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
- **Phase 4 M4.1** — Source ingestion foundation:
  - `src/app/research/` package (constants, errors, hashing, models,
    validate, extract, quality, fetch, repository)
  - `source_contents` table (SCHEMA_VERSION 7); append-only per-attempt rows
  - URL fetch with SSRF protection (pre-resolution + blocked IP ranges),
    HTTPS→HTTP redirect blocking, MIME and size enforcement
  - Local file ingest (.txt, .md, .pdf); null-byte and extension allowlist
  - HTML extraction (BeautifulSoup4, title/author/date metadata)
  - PDF extraction (pypdf, page separators, partial extraction support)
  - Deterministic quality scoring (7 factors, weights sum to 1.0)
  - Idempotency via `normalized_text_hash`; `--force` to override
  - CLI: `ace sources fetch`, `ace sources ingest-file`, `ace sources quality`

End-to-end workflow: channel strategy → discovery → scoring → topic
promotion → source ingestion → quality review.

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
