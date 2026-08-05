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

**Phase 6 Milestone 6.2 (Narration Generation) is complete.**

The system can produce audio narration for every segment of an approved
production plan: voice profiles, TTS synthesis (FakeTTSProvider for tests),
atomic WAV writing, cost tracking, and exception-based review governance.
1307 tests pass. SCHEMA_VERSION 11.

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
- **Phase 4 M4.2** — Evidence and claim extraction:
  - Paragraph-aware chunking with exact offset invariant and input hash
  - LLM-driven claim extraction (Pydantic strict output, per-chunk AI calls)
  - Quote support classification: exact → normalized → unsupported → no_quote
  - Deterministic date-review risk flags (4 rules)
  - Atomic finalization with supersession model (`superseded_at` + `superseded_by_run_id`)
  - SCHEMA_VERSION 8: `claim_extraction_runs`, `claim_extraction_run_calls`, `claims`
  - CLI: `ace sources extract-claims`, `ace sources list-claims`, `ace sources claim-runs`
- **Phase 5** — Script generation:
  - `src/app/content/` package: constants, errors, schemas, hashing, renderer,
    validator, models, repository, generator
  - SCHEMA_VERSION 9: `scripts` extended with `body_json`, `format`, `approved_at`,
    `superseded_at`; new `script_generation_runs` and `script_citations` tables
  - Canonical `sort_evidence()` ordering used for prompt context, evidence hash,
    and reproducibility
  - 12-step `validate_script()` pipeline: malformed-marker rejection, bidirectional
    citation marker/ID equivalence, claim existence, duration bounds (15–90 s),
    zero-evidence mode
  - Atomic `finalize_generation_run()`: SAVEPOINT covers script insert, citation
    insert, run completion, and optional prior-run supersession
  - Atomic `approve_script()`: supersedes prior active approved script before
    approving; prior scripts retain `status='approved'` and receive `superseded_at`
  - Idempotency via SHA-256 `input_hash` (evidence + prompt + all settings)
  - Phase 6 handoff: `get_active_approved_generated_script()` raises
    `UnstructuredApprovedScriptError` for manually created scripts
  - Prompt: `src/app/ai/prompts/script-generation/v1.toml`
  - CLI: `ace scripts generate`, `ace scripts approve`, `ace scripts show`,
    `ace scripts runs`, `ace scripts citations`
- **Phase 6 M6.1** — Production plan:
  - `src/app/production/` package: constants, errors, hashing, models,
    renderer, repository
  - SCHEMA_VERSION 10: `production_plans`, `production_segments`,
    `production_segment_citations`, `production_plan_review_events`
  - Pure `build_production_plan()` renderer: deterministic segment breakdown,
    unclamped per-segment durations, narration text = `strip_markers(section.text)`
  - Atomic SAVEPOINT for creation (plan+segments+citations), approval
    (supersede prior → approve → review event), rejection (reject → review event)
  - `UNIQUE(script_id, input_hash)` idempotency; two partial unique indexes
    for normal/experiment active-plan isolation
  - `ApprovedProductionPlan` frozen handoff for M6.2 narration
  - CLI: `ace production plan/show/list/approve/reject/feedback`
- **Phase 6 M6.2** — Narration generation:
  - `src/app/narration/` package: constants, errors, hashing, models, protocol,
    fake, pricing, storage, repository, orchestrator
  - SCHEMA_VERSION 11: `voice_profiles`, `narration_runs`,
    `narration_segment_assets`, `tts_calls`, `narration_review_events`
  - `TTSProvider` `@runtime_checkable` Protocol; `FakeTTSProvider` (stdlib
    `wave`, no new deps); `TTSPricingRegistry` (character-based, $0 for fake)
  - Segment and run input hashes (SHA-256, compact sorted JSON); any field
    change forces re-synthesis
  - Atomic WAV write: `.tmp` → validate → SHA-256 → `os.replace()`
  - `narrate_plan()`: idempotent; resumes crashed runs; TTS call outside DB
    transaction; `record_tts_call()` auto-commits outside SAVEPOINT
  - Exception-based review: segments start `synthesized`; operator rejects only
  - `ACE_ARTIFACTS_PATH` config; `/artifacts/` excluded from Git
  - CLI: `ace narration voices/add-voice/narrate/runs/approve/reject-run/
    reject-segment/events`

End-to-end workflow: channel strategy → discovery → scoring → topic promotion
→ source ingestion → claim extraction → script generation → human approval
→ production plan creation → human review (approve/reject) → narration
synthesis → narration review (approve/reject segments).

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
