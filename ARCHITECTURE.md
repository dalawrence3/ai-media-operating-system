# Architecture

## Overview

A single modular Python application (not a distributed system). Deterministic
code handles discovery, file I/O, database access, scheduling, validation,
and uploads. An LLM is used only where language understanding, creative
judgment, or qualitative analysis is genuinely needed. Autonomous agents are
not used where deterministic pipelines are more reliable.

The system is designed as a YouTube Content Operating System — opinionated
around the YouTube platform and its APIs, with other platforms deferred as
optional adapters after YouTube is stable.

## Design principles

- **YouTube-first.** Platform abstractions are not generalised prematurely.
  YouTube API behaviour, quota limits, and analytics availability drive the
  design. Other platforms attach as adapters in a later phase.
- **Deterministic where possible.** Scoring, validation, cost tracking,
  scheduling, and publishing logic are deterministic Python. LLMs handle
  generation and critique only.
- **Repository-first.** Source code is the source of truth. Chat is for
  decisions and summaries.
- **Incremental.** Each phase depends only on what prior phases have
  implemented and tested.

## Current state (Phase 2 complete)

- SQLite database at `~/.local/share/ai-content-engine/content.db`
  (override via `ACE_DB_PATH`). WAL journal mode, foreign keys enforced.
- Versioned schema (SCHEMA_VERSION=2): `topics`, `sources`, `scripts`,
  `runs`, `ai_calls`.
- Four domain entities: `Topic`, `Source`, `Script`, `Run` — Pydantic
  models, typed repository layer.
- Typer CLI with `topics`, `sources`, `scripts`, `runs`, `ai` subcommand
  groups and diagnostic `version`, `doctor` commands.
- Stdlib structured logging via `ACE_LOG_LEVEL`.
- `src/app/ai/` package: provider-independent LLM abstraction (`AIProvider`
  Protocol), `FakeProvider` (deterministic, no API calls), `ClaudeProvider`
  (Anthropic SDK, injected client for testing), versioned TOML prompt
  registry, structured output validation via Pydantic, bounded retry with
  injectable sleep, token/cost tracking, `ai_calls` DB table.

## Package layout (target — populated phase by phase)

```
src/app/
├── __init__.py
├── __main__.py
├── cli.py                    # Top-level Typer app; subcommands registered per phase
├── core/                     # Phase 0–1: DB, models, config, logging
│   ├── config.py
│   ├── database.py
│   ├── logging.py
│   ├── models.py
│   └── repository.py
├── ai/                       # Phase 2: LLM abstraction (implemented)
│   ├── errors.py             # Typed exception hierarchy
│   ├── provider.py           # AIProvider Protocol + AIRequest/AIResponse dataclasses
│   ├── fake.py               # FakeProvider (deterministic, no API)
│   ├── claude.py             # ClaudeProvider (Anthropic SDK, injectable client)
│   ├── registry.py           # PromptRegistry — versioned TOML prompt loading
│   ├── schemas.py            # Pydantic output schemas (EchoOutput for demo)
│   ├── pricing.py            # Versioned pricing registry, cost estimation
│   ├── retry.py              # Bounded retry with injectable sleep
│   ├── usage.py              # record_ai_call() → ai_calls table
│   └── prompts/              # Named, versioned prompt template files (TOML)
├── intelligence/             # Phase 3: YouTube opportunity intelligence
│   ├── channel.py            # Channel and niche configuration
│   ├── youtube_client.py     # YouTube Data API v3 wrapper
│   ├── trends.py             # Google Trends / external signal integration
│   ├── scoring.py            # Topic scoring (deterministic)
│   ├── dedup.py              # Duplicate-topic protection
│   └── discovery.py          # Orchestrates a discovery run
├── research/                 # Phase 4: Source management and fact checking
│   ├── ingest.py             # URL fetch, file read, note capture
│   ├── extract.py            # LLM claim extraction
│   ├── quality.py            # Source quality scoring
│   └── provenance.py         # Citation and rights records
├── content/                  # Phase 5: Content generation
│   ├── brief.py              # Content brief assembly
│   ├── hooks.py              # Hook generation and scoring
│   ├── generator.py          # Script generation
│   ├── critique.py           # Script critique
│   ├── revision.py           # Revision loop
│   ├── metadata.py           # Title, description, tags
│   └── originality.py        # Originality check
├── media/                    # Phases 6–8: Production
│   ├── narration.py          # TTS provider abstraction and audio generation
│   ├── captions.py           # SRT/VTT caption generation
│   ├── assets.py             # Asset library, licence enforcement
│   ├── manifest.py           # Scene manifest assembly and validation
│   └── renderer.py           # FFmpeg rendering and validation
├── pipeline/                 # Phase 9: End-to-end pipeline orchestration
│   ├── runner.py             # Stage runner with human gate support
│   └── cost.py               # Production cost accumulation
├── publish/                  # Phase 10: YouTube publishing
│   ├── youtube.py            # YouTube Data API upload, scheduling, approval
│   ├── instagram.py          # Phase 15: Instagram adapter (deferred)
│   └── tiktok.py             # Phase 15: TikTok adapter (deferred)
├── analytics/                # Phase 11: YouTube Analytics API
│   ├── collector.py          # Metric collection and storage
│   ├── profitability.py      # Cost vs. revenue calculation
│   └── reports.py            # CLI report generation
├── experiments/              # Phase 12: Controlled experimentation
│   ├── design.py             # Experiment design and sample-size enforcement
│   └── outcomes.py           # Result recording and promotion logic
└── scheduler/                # Phase 13: Reduced-oversight operation
    ├── runner.py             # Scheduled pipeline execution
    ├── circuit_breaker.py    # Automatic pause logic
    ├── audit.py              # Audit log
    └── notifications.py      # Approval queue notifications
```

## Database schema (target — evolved per phase)

The schema is versioned (integer in `schema_version`). Each phase's
migration is applied on first `open_db()` call if the version is lower
than `SCHEMA_VERSION`. A version higher than expected raises immediately.

Phase 1 tables: `schema_version`, `topics`, `sources`, `scripts`, `runs`

Planned additions per phase:
- Phase 2: `llm_calls`
- Phase 3: `channels`, `discovery_runs`, `opportunity_scores`; extend `topics`
- Phase 4: extend `sources`; add `claims`, `asset_rights`
- Phase 5: extend `scripts`; add `hooks`, `metadata_drafts`
- Phase 6: `narrations`, `captions`, `tts_calls`
- Phase 7: `assets`, `scene_manifests`
- Phase 8: `renders`, `thumbnails`
- Phase 9: extend `runs`
- Phase 10: `publications`
- Phase 11: `video_metrics`, `channel_metrics`, `cost_records`
- Phase 12: `experiments`, `experiment_arms`
- Phase 13: `audit_log`, `circuit_breaker_events`, `schedules`
- Phase 14: `accounts`; extend channel tables

## External integrations and data-source constraints

| Integration | Phase | Notes |
|---|---|---|
| Anthropic SDK (Claude) | 2 | LLM generation and critique |
| YouTube Data API v3 | 3, 10 | Official; 10k units/day default quota |
| Google Trends (pytrends) | 3 | Not a YouTube product; rate limits apply |
| TTS provider (ElevenLabs or equiv.) | 6 | Provider TBD at Phase 6 |
| Stock asset APIs (Pexels, Pixabay, etc.) | 7 | Licence verification required |
| YouTube Analytics API | 11 | OAuth; channel owner only; revenue requires monetisation |
| Instagram API | 15 | Deferred |
| TikTok API | 15 | Deferred |

**Prohibited data sources:**
- Scraping YouTube or Google in violation of their Terms of Service
- Keyword volume data from unofficial scraping tools
- Competitor revenue data (not available through any official source)
- Algorithm ranking signals (not publicly exposed)

## LLM usage policy

LLMs are used for:
- Claim extraction from source text
- Content brief, hook, script, and metadata generation
- Script critique
- Niche classification (optional, Phase 3)

LLMs are **not** used for:
- Topic scoring (deterministic formula)
- Asset licence enforcement (rule-based)
- Publishing decisions (deterministic + human gate)
- Analytics calculation (arithmetic)
- Scheduling (cron/APScheduler)
- Any decision where a deterministic function is sufficient

## Publishing modes

Each channel has a publishing mode stored in the DB:

| Mode | Description |
|---|---|
| `manual` | Every public publish requires explicit human approval |
| `supervised` | Pipeline runs automatically; human approves before publish |
| `autonomous` | Pre-publish checks run automatically; publish if all pass |

Promotion between modes is explicit (operator command), recorded in the
audit log, and reversible. Automatic demotion occurs on any check failure,
policy-risk flag, unusual cost, or consecutive performance anomaly.

## Human approval gates

| Gate | Mode applicability | Can it be bypassed? |
|---|---|---|
| Topic selection | All modes | No (bulk pre-approval allowed) |
| Script approval (after critique) | All modes | No |
| Metadata review | All modes | No |
| Narration approval | All modes | No |
| Manifest review | All modes | No |
| Video review before upload | Manual, supervised | No |
| Public publication approval | Manual, supervised | No |
| Publishing mode promotion | All modes | No |
| High-risk content category | All modes | No — ever |
| Experiment conclusion | All modes | No |
| New channel addition | All modes | No |

In `autonomous` mode, video review and publication approval are replaced
by automated pre-publish checks: quality score, licence verification,
duplicate check, factual-risk threshold, daily/weekly publishing limits,
spending limit. Any failure halts, notifies, and may demote the channel.
The operator always has an immediate kill switch.

## Why no cloud infrastructure yet

Local operation is correct until the system produces profitable content
reliably. Cloud infrastructure adds cost, complexity, and operational
risk before the core product is validated. Revisit after Phase 11
demonstrates measurable profitability.

## Why src/ layout

A flat layout lets `import app` succeed from the working directory even
without a real install, masking packaging bugs. The `src/` layout forces
all imports — including in tests — through an installed path, catching
issues immediately.
