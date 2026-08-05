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

## Current state (Phase 6 M6.2 complete)

- SQLite database at `~/.local/share/ai-content-engine/content.db`
  (override via `ACE_DB_PATH`). WAL journal mode, foreign keys enforced.
- Versioned schema (SCHEMA_VERSION=11): `topics`, `sources`, `scripts`,
  `runs`, `ai_calls`, Phase 3 intelligence tables, `source_contents`,
  Phase 4.2 claim extraction tables, Phase 5 `script_generation_runs`
  and `script_citations` tables, Phase 6 M6.1 `production_plans`,
  `production_segments`, `production_segment_citations`, and
  `production_plan_review_events` tables, plus Phase 6 M6.2
  `voice_profiles`, `narration_runs`, `narration_segment_assets`,
  `tts_calls`, and `narration_review_events` tables.
- Phase 1 domain entities: `Topic`, `Source`, `Script`, `Run` — Pydantic
  models, typed repository layer.
- Phase 2: `src/app/ai/` package — provider-independent LLM abstraction
  (`AIProvider` Protocol), `FakeProvider` (deterministic, no API calls),
  `ClaudeProvider` (Anthropic SDK, injectable client), versioned TOML prompt
  registry, structured output via Pydantic, bounded retry with injectable
  sleep, token/cost tracking, `ai_calls` DB table.
- Phase 3 Milestone 3.1 — Versioned Channel Strategy Foundation:
  - New DB tables: `channels`, `channel_monetization_strategies`,
    `channel_profile_versions`, `channel_capacity_policies`,
    `channel_operating_mode_events`
  - Versioned, immutable channel profile snapshots (niche, audience, format,
    discovery settings, portfolio targets, scoring policy reference)
  - Versioned monetization strategy (16 named objectives, weights validated
    to sum 1.0, pre/active status); capacity policy with operator-approved
    ceilings (not quotas); append-only operating mode event log
  - Phase 3 runtime restriction: only `manual` mode permitted
  - CLI: `ace channels add/list/show/versions/new-version/activate-version/
    new-strategy/activate-strategy/set-mode/capacity/set-capacity`
- Phase 3 Milestone 3.2 — Discovery Foundation:
  - New DB tables: `discovery_runs`, `opportunities`,
    `opportunity_observations`, `opportunity_source_evidence`,
    `opportunity_state_events`
  - Opportunity lifecycle (discovered → approved → in_production);
    append-only state event log; denormalized current_lifecycle_state
  - Adapter abstraction (`adapters/base.py`): injectable stub for tests;
    `ManualAdapter`; `YouTubeDataAPIAdapter` (injectable client, no live
    calls in tests)
  - Jaccard dedup against active opportunities (`dedup.py`)
  - `discovery.py` orchestrator: single-adapter discovery run with FK-safe
    observation and evidence persistence
  - CLI: `ace discover run/list/show`
- Phase 3 Milestone 3.3 — Scoring and Confidence Engine:
  - New DB tables: `scoring_policies`, `opportunity_scores`
  - Versioned, immutable scoring policies (6 factors, weights validated to
    sum 1.0); active-policy enforcement via partial unique index
  - Six scoring factors: trend_strength, audience_demand, competition,
    evergreen_value, audience_fit, content_novelty
  - Confidence engine: source quality, data completeness, freshness,
    corroboration; `FactorStatus` per factor
  - Missing-data policies: `reweight_available`, `apply_prior`,
    `reduce_confidence_only`; weights rebalanced so composite sums to 1.0
  - Score rows are append-only; latest score determined by
    `scored_at DESC, id DESC`
  - CLI: `ace intelligence score/score-all/explain` and
    `ace intelligence policy list/show/create/clone/update/activate`
- Phase 3 Milestone 3.4 — Opportunity Promotion Workflow:
  - `topics.promoted_opportunity_id` column (FK → opportunities) with
    partial unique index (`WHERE promoted_opportunity_id IS NOT NULL`)
  - `promote_opportunity()`: score prerequisite guard; lifecycle guard
    (new/under_review only); SAVEPOINT atomicity (topics INSERT +
    lifecycle transition); idempotent (returns existing topic if already
    promoted)
  - CLI: `ace topics promote <opportunity_id> [--angle] [--operator]
    [--allow-unscored]`
- Phase 4 Milestone 4.1 — Source Ingestion Foundation:
  - New DB table: `source_contents` (append-only; one row per fetch/ingest
    attempt; never updated; `fetch_status` ok/failed, `extraction_status`
    ok/partial/failed)
  - `src/app/research/` package: `constants`, `errors`, `hashing`, `models`,
    `validate`, `extract`, `quality`, `fetch`, `repository`
  - SSRF protection: pre-resolution via `socket.getaddrinfo` + 23 blocked
    IPv4/IPv6 ranges (RFC 1918, loopback, link-local, multicast, etc.)
  - HTTPS→HTTP redirect blocking: raises `SecurityError` (not a warning)
  - HTML extraction: BeautifulSoup4 with html.parser; title/author/date
  - PDF extraction: pypdf; 200-page cap; page separators; partial on error
  - Quality scoring: 7 deterministic factors, weights summing to 1.0
  - `retrieval_hash`: SHA-256 of raw fetched bytes
  - `normalized_text_hash`: SHA-256 of NFC-normalized extracted text
  - Idempotency: compare `normalized_text_hash`; skip if unchanged
  - CLI: `ace sources fetch <url>`, `ace sources ingest-file <path>`,
    `ace sources quality <source_id>`
- Phase 4 Milestone 4.2 — Evidence & Claim Extraction:
  - New DB tables: `claim_extraction_runs`, `claim_extraction_run_calls`,
    `claims` (SCHEMA_VERSION 8); v7→v8 migration
- Phase 5 — Script Generation:
  - SCHEMA_VERSION 9: `scripts` extended with `body_json`, `format`,
    `approved_at`, `superseded_at`; new `script_generation_runs` (26 cols)
    and `script_citations` (6 cols) tables; v8→v9 migration
  - `src/app/content/` package: `constants`, `errors`, `schemas` (Pydantic
    strict models for LLM output + internal representations), `hashing`
    (SHA-256 evidence/prompt/input hashes; canonical `sort_evidence()`),
    `renderer` (deterministic `render_body()`; word count; duration),
    `validator` (12-step validation pipeline; `ValidationResult`),
    `models` (`ScriptGenerationRun`, `ScriptGenerationRunStatus`),
    `repository` (generation run CRUD; atomic `finalize_generation_run()`
    SAVEPOINT; `get_active_approved_generated_script()` Phase 6 handoff),
    `generator` (`generate_script()` orchestrator)
  - Prompt: `src/app/ai/prompts/script-generation/v1.toml`
  - Evidence hash is independent of prompt/settings; input hash combines all
    behavior-affecting versions; idempotency via `input_hash` lookup
  - `approve_script()` in core repository: atomic SAVEPOINT supersession of
    prior active approved Scripts; `superseded_at` set on prior Scripts
  - CLI: `ace scripts generate <topic_id>`, `ace scripts approve <script_id>`,
    `ace scripts show <script_id>`, `ace scripts runs <topic_id>`,
    `ace scripts citations <script_id>`
  - Phase 6 boundary: `get_active_approved_generated_script()` raises
    `UnstructuredApprovedScriptError` for manually created scripts
- Phase 6 Milestone 6.1 — Production Plan:
  - SCHEMA_VERSION 10: four new tables — `production_plans` (24 cols,
    `UNIQUE(script_id, input_hash)`, two partial unique indexes for
    active-plan enforcement per normal/experiment isolation),
    `production_segments` (9 cols, `UNIQUE(plan_id, segment_index)`),
    `production_segment_citations` (5 cols, normalized, `UNIQUE(segment_id,
    claim_id)` + `UNIQUE(segment_id, citation_order)`),
    `production_plan_review_events` (14 cols with denormalized training-label
    fields for platform-neutral analytics); v9→v10 migration
  - `src/app/production/` package: `constants`, `errors`, `hashing`,
    `models`, `renderer`, `repository`
  - Three version constants (`PRODUCTION_PLAN_SCHEMA_VERSION`,
    `PRODUCTION_PLAN_RENDERER_VERSION`, `PRODUCTION_DURATION_VERSION`);
    any change bumps `input_hash`
  - `compute_script_body_hash()`: SHA-256 of canonical Pydantic body_json
  - `compute_production_plan_input_hash()`: SHA-256 of compact sorted JSON
    of 9 fields (script_id, version, body_hash, schema_version,
    renderer_version, duration_version, format, evidence_hash,
    requires_evidence_review)
  - `build_production_plan()`: pure deterministic function; hard invariant
    `segment.narration_text == strip_markers(section.text)`; unclamped
    segment duration `max(1, ceil(word_count / 150 * 60))` (no [15,90] clamp)
  - Lifecycle: draft → approved (supersedes prior active normal plan) or
    draft → rejected (terminal); rejected leaves approved plan untouched
  - SAVEPOINTs: `create_pp` (plan+segments+citations), `approve_pp`
    (supersede+approve+review event), `reject_pp` (reject+review event)
  - `experiment_id TEXT` nullable: all M6.1 plans NULL; isolation by
    NULL/non-NULL so normal and experiment plans don't supersede each other
  - `ApprovedProductionPlan`: frozen Pydantic handoff for M6.2
  - `require_active_approved_production_plan()` / `get_active_approved_production_plan()`
  - CLI: `ace production plan/show/list/approve/reject/feedback`
  - Supersession model: `superseded_at` + `superseded_by_run_id` columns on
    runs (not a status value); status CHECK: `running/completed/partial/failed`
  - `src/app/research/chunking.py`: paragraph-aware chunker with exact offset
    invariant (`chunk.text == raw_text[start:end]`); deduplication; input hash
  - `src/app/research/claim_support.py`: exact → normalized (NFC + CRLF +
    whitespace character-level map) → unsupported → no_quote classification;
    PDF page derivation from `--- Page N ---\n` separators
  - `src/app/research/claim_risk.py`: 4 deterministic date-review rules; Rule
    4 suppressed by historical-year pattern `\bin (19|20)\d{2}\b`
  - `src/app/research/schemas.py`: `ExtractedClaim`, `ClaimExtractionOutput`
    (Pydantic, `extra="forbid"`)
  - `src/app/research/extractor.py`: `extract_claims()` orchestrator;
    idempotent (same input_hash returns existing run); `--replace` supersedes
    prior; per-chunk AI call recorded regardless of outcome; atomic
    finalization via single SAVEPOINT
  - `src/app/ai/prompts/claim-extraction/v1.toml`: system + user_template
  - Active evidence: `status='completed' AND superseded_at IS NULL AND
    quote_support_status IN ('exact','normalized')`
  - CLI: `ace sources extract-claims <source_id>`, `ace sources list-claims
    <topic_id>`, `ace sources claim-runs <source_id>`
- Phase 6 Milestone 6.2 — Narration Generation:
  - SCHEMA_VERSION 11: five new tables — `voice_profiles` (17 cols; nullable
    `channel_id` scoping; versioned; `is_default` enforced via `is_default=1`
    uniqueness constraint per channel/global scope), `narration_runs` (25 cols;
    `UNIQUE(plan_id, input_hash)` hard idempotency; status CHECK:
    `running/completed/failed/approved/rejected`; supersession via
    `superseded_at` + `superseded_by_run_id`; partial unique index
    `WHERE status='approved' AND superseded_at IS NULL` — superseded runs
    retain `status='approved'`; one active approved run per plan at a time),
    `narration_segment_assets` (27 cols; partial unique index
    `WHERE status != 'rejected' AND superseded_at IS NULL`; supersession via
    `superseded_at` timestamps), `tts_calls` (20 cols; auto-commits outside
    SAVEPOINT, same pattern as `ai_calls`), `narration_review_events` (21 cols;
    event_type CHECK: `run_approved/run_rejected/segment_rejected/
    segment_regenerated`; denormalized training context — `plan_id`,
    `script_id`, `topic_id`, `voice_profile_id`, `provider`, `model`,
    `voice_id`, `experiment_id` — frozen at insert time; `actor` field;
    `severity` 1–5 CHECK; `expected_correction`; `replacement_asset_id` for
    `segment_regenerated` events); v10→v11 migration
  - `src/app/narration/` package: `constants`, `errors`, `hashing`, `models`,
    `protocol`, `fake`, `pricing`, `storage`, `repository`, `orchestrator`
  - `TTSProvider` `@runtime_checkable` Protocol: `synthesize(TTSRequest) →
    TTSResponse`; `provider_name: str`; `default_model: str`
  - `FakeTTSProvider`: deterministic silence WAV bytes via stdlib `wave`;
    word-count-based duration; `fail_on: set[int]` for test injection; no
    new runtime dependencies; only TTS provider in M6.2
  - `TTSPricingRegistry`: character-based pricing (`price_per_1k_chars`);
    `estimate_cost()`; singleton `get_default_registry()`; fake model = $0.00
  - Segment input hash: SHA-256 of compact sorted JSON of 19 fields; run input
    hash: SHA-256 of compact sorted JSON of 14 fields
  - `NARRATION_SCHEMA_VERSION = "Narration-v1"`;
    `NARRATION_ALGORITHM_VERSION = "narration-segment-v1"` — both bound to
    hashes; any change invalidates existing runs
  - `ACE_ARTIFACTS_PATH` config: WAV files stored at
    `{artifacts_path}/narration/{plan_id}/{run_id}/segment_{id}.wav`;
    relative paths stored in DB; `/artifacts/` excluded from Git
  - Atomic audio write: `.tmp` → validate WAV (stdlib `wave`) → SHA-256 →
    `os.replace()` to final path; `AudioValidationError` on corrupt bytes
  - `narrate_plan()`: idempotent — returns existing completed/approved run
    without re-synthesis; resumes running run from last synthesized segment;
    TTS provider call OUTSIDE any DB transaction; `record_tts_call()` called
    after synthesis OUTSIDE any SAVEPOINT (auto-commits)
  - Exception-based review: all segments start `synthesized`; operator rejects
    only problematic segments; run approval requires no rejected segments;
    severity validated 1–5 BEFORE opening any SAVEPOINT
    (`InvalidNarrationSeverityError`)
  - Supersession contract: `approve_narration_run()` supersedes the prior
    active approved run by setting `superseded_at` + `superseded_by_run_id`;
    the prior run keeps `status='approved'` — supersession is NOT rejection
  - `regenerate_segment()`: creates pending asset (committed), calls TTS
    OUTSIDE SAVEPOINT, writes WAV atomically, calls
    `finalize_narration_segment_asset()` in SAVEPOINT (`finalize_nsa`);
    pending asset deleted + committed if TTS or finalization fails
  - SAVEPOINTs: `create_vp`, `approve_nr`, `reject_nr`, `finalize_nsa`,
    `reject_nsa`
  - CLI: `ace narration voices/add-voice/narrate/runs/approve/reject-run/
    reject-segment/events/regenerate-segment`
  - Config: `ACE_TTS_PROVIDER` (default `fake`), `ACE_TTS_MODEL` (default
    `fake/FAKE`)
- Typer CLI with `topics`, `sources`, `scripts`, `runs`, `ai`, `channels`,
  `discover`, `intelligence`, `production`, `narration` subcommand groups and
  diagnostic `version`, `doctor` commands.
- Stdlib structured logging via `ACE_LOG_LEVEL`.

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
├── intelligence/             # Phase 3: YouTube opportunity intelligence (implemented)
│   ├── models.py             # All Phase 3 Pydantic models and enums
│   ├── repository.py         # Channel, opportunity, score, promotion repository
│   ├── cli.py                # channels_app and discover_app Typer subcommands
│   ├── scoring_cli.py        # intelligence_app: score/explain/policy commands
│   ├── dedup.py              # Jaccard duplicate-topic protection
│   ├── discovery.py          # Discovery run orchestrator
│   ├── adapters/             # Adapter abstraction + ManualAdapter + YouTubeAdapter
│   │   ├── base.py           # DiscoveryAdapter Protocol and result types
│   │   ├── manual.py         # ManualSignalAdapter
│   │   └── youtube.py        # YouTubeDataAPIAdapter (injectable client)
│   └── scoring/              # Deterministic scoring engine
│       ├── engine.py         # score_opportunity() entry point
│       ├── factors.py        # Six factor computation functions
│       ├── weights.py        # Weight normalization and rebalancing
│       ├── confidence.py     # Confidence calculation
│       └── snapshot.py       # Score snapshot serialization
├── research/                 # Phase 4: Source management (M4.1 + M4.2 implemented)
│   ├── constants.py          # Named limits and configuration constants
│   ├── errors.py             # ResearchError, SecurityError, FetchError, ExtractionError, ClaimExtractionError
│   ├── hashing.py            # SHA-256 helpers; normalize_for_hash (NFC)
│   ├── models.py             # SourceContent, Claim, ClaimExtractionRun, EvidenceClaim + enums
│   ├── schemas.py            # ExtractedClaim, ClaimExtractionOutput (AI response schemas)
│   ├── validate.py           # SSRF/scheme URL validation; file-path validation
│   ├── extract.py            # HTML (BS4), PDF (pypdf), plaintext, markdown extraction
│   ├── quality.py            # Deterministic 7-factor quality scorer
│   ├── fetch.py              # HTTP acquisition with redirect and size enforcement
│   ├── chunking.py           # Paragraph-aware chunker; input hash; deduplication
│   ├── claim_support.py      # Quote support classification; PDF page derivation
│   ├── claim_risk.py         # Deterministic date-review risk flags
│   ├── extractor.py          # extract_claims() orchestrator
│   └── repository.py         # get_or_create_source, create/get source_contents, claim extraction layer
├── content/                  # Phase 5: Script generation (implemented)
│   ├── __init__.py
│   ├── constants.py          # Named constants: WPM, duration bounds, versions, defaults
│   ├── errors.py             # Typed exception hierarchy (ScriptGenerationError, etc.)
│   ├── schemas.py            # LLMGeneratedScript, GeneratedScript, ScriptCitation, ApprovedScript
│   ├── hashing.py            # sort_evidence(), compute_evidence/prompt/input_hash()
│   ├── renderer.py           # render_body(), strip_markers(), count_words(), compute_duration_s()
│   ├── validator.py          # validate_script() 12-step pipeline; ValidationResult
│   ├── models.py             # ScriptGenerationRun, ScriptGenerationRunStatus
│   ├── repository.py         # Generation run CRUD; finalize_generation_run() SAVEPOINT; Phase 6 handoff
│   └── generator.py          # generate_script() orchestrator; GenerationResult
├── production/               # Phase 6 M6.1: Production plan (implemented)
│   ├── __init__.py
│   ├── constants.py          # Version strings; REJECTION_REASON_CODES
│   ├── errors.py             # ProductionPlanError hierarchy (NoPlanError, IllegalTransitionError, etc.)
│   ├── hashing.py            # compute_script_body_hash(), compute_production_plan_input_hash()
│   ├── models.py             # Draft dataclasses; Pydantic DB models; ApprovedProductionPlan handoff
│   ├── renderer.py           # build_production_plan() pure function; plan_draft_to_json_summary()
│   └── repository.py         # CRUD + approve/reject SAVEPOINTs; get_approved_production_plan_full()
├── narration/                # Phase 6 M6.2: TTS narration pipeline (implemented)
│   ├── constants.py          # Version strings; stale-temp age; default format/sample rate
│   ├── errors.py             # NarrationError hierarchy (SynthesisError, AudioValidationError, etc.)
│   ├── hashing.py            # compute_narration_text/segment/run/settings_hash()
│   ├── models.py             # VoiceProfileCreate/NarrationRunDraft/NarrationSegmentAssetDraft dataclasses; Pydantic DB models
│   ├── protocol.py           # TTSProvider @runtime_checkable Protocol; TTSRequest/TTSResponse dataclasses
│   ├── fake.py               # FakeTTSProvider: deterministic WAV silence; fail_on injection
│   ├── pricing.py            # TTSPricingRegistry; character-based cost estimation; singleton
│   ├── storage.py            # Artifact path resolution; atomic WAV write; WAV validation; SHA-256
│   ├── repository.py         # Voice profile CRUD; narration run/segment CRUD; approve/reject SAVEPOINTs; record_tts_call
│   └── orchestrator.py       # narrate_plan() entry point; idempotency; per-segment synthesis; dry-run; regenerate_segment()
├── media/                    # Phases 6 M6.3+: Captions, assets, video rendering
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

Phase 2 tables: `ai_calls`

Phase 3 M3.1 tables: `channels`, `channel_monetization_strategies`,
`channel_profile_versions`, `channel_capacity_policies`,
`channel_operating_mode_events`

Phase 3 M3.2 tables: `discovery_runs`, `opportunities`,
`opportunity_observations`, `opportunity_source_evidence`,
`opportunity_state_events`

Phase 3 M3.3 tables: `scoring_policies`, `opportunity_scores`

Phase 3 M3.4: `topics.promoted_opportunity_id` column (nullable FK →
opportunities); partial unique index `uq_topics_promoted_opportunity`

Phase 4 M4.1: `source_contents` table — append-only per-attempt acquisition
and extraction records; indexes `sc_source_id` and `sc_normalized_text_hash`

Phase 4 M4.2: `claim_extraction_runs`, `claim_extraction_run_calls`, `claims`
tables; indexes on source_content_id, input_hash, extraction_run_id, chunk_index;
UNIQUE(claim_extraction_run_id, chunk_index) on run_calls

Phase 5: extend `scripts` with `body_json`, `format`, `approved_at`,
`superseded_at`; add `script_generation_runs` and `script_citations` tables

Phase 6 M6.1: `production_plans`, `production_segments`,
`production_segment_citations`, `production_plan_review_events` tables;
UNIQUE(script_id, input_hash) and two partial unique indexes for normal/
experiment active-plan isolation

Phase 6 M6.2: `voice_profiles`, `narration_runs`, `narration_segment_assets`,
`tts_calls`, `narration_review_events` tables; UNIQUE(plan_id, input_hash) on
narration_runs; partial unique index on segment assets
(WHERE status != 'rejected' AND superseded_at IS NULL)

Planned additions per phase:
- Phase 6 M6.3+: `captions`
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
