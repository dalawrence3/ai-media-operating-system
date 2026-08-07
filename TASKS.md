# Tasks

---

## Completed

- **Phase 0: Planning & environment** — repository structure, documentation,
  virtual environment, diagnostic CLI (`version`, `doctor`), pytest and Ruff
  configured. All tests pass.

- **Phase 1: Core data model** — SQLite persistence with versioned schema;
  `Topic`, `Source`, `Script`, `Run` Pydantic models; full repository layer;
  Typer CLI with `topics`, `sources`, `scripts`, `runs` subcommands;
  structured logging; configuration module. 63 tests pass.

- **Phase 2: AI Foundation** — Provider-independent LLM abstraction
  (`src/app/ai/`); `FakeProvider` (deterministic); `ClaudeProvider`
  (Anthropic SDK, injectable client for test isolation); versioned TOML
  prompt registry; structured output via Pydantic; bounded retry with
  injectable sleep; token/cost tracking with configurable pricing registry;
  `ai_calls` DB table (SCHEMA_VERSION 2); CLI: `ace ai prompts list/show`,
  `ace ai demo`; no live API calls in any test. 149 tests pass.

- **Phase 3 Milestone 3.1: Versioned Channel Strategy Foundation** —
  `src/app/intelligence/` package; 5 new DB tables (SCHEMA_VERSION 3):
  `channels`, `channel_monetization_strategies`, `channel_profile_versions`,
  `channel_capacity_policies`, `channel_operating_mode_events`; versioned
  immutable profile snapshots (niche, audience, format, discovery config,
  portfolio targets, D5 dedup threshold 0.70, scoring policy reference);
  versioned monetization strategy (16 named objectives, weights validated to
  sum 1.0, pre/active status); capacity policy with D6 ceilings (not quotas);
  append-only operating mode event log; Phase 3 restricts set-mode to
  `manual` only (`supervised`/`autonomous` are schema reservations); operator
  decisions D1–D7 implemented and recorded; CLI: `ace channels add/list/show/
  versions/new-version/activate-version/new-strategy/activate-strategy/
  set-mode/capacity/set-capacity`; no external API integrations; no scoring
  execution; no opportunity discovery; no placeholder code. 259 tests pass.

- **Phase 3 Milestone 3.2: Discovery Foundation** —
  `discovery_runs`, `opportunities`, `opportunity_observations`,
  `opportunity_source_evidence`, `opportunity_state_events` DB tables
  (SCHEMA_VERSION 4); opportunity lifecycle with append-only state event log
  and denormalized `current_lifecycle_state`; adapter abstraction
  (`adapters/base.py`) with injectable stub; `ManualAdapter`;
  `YouTubeDataAPIAdapter` (injectable client, no live calls in tests);
  Jaccard dedup against active opportunities (`dedup.py`); discovery
  orchestrator (`discovery.py`); CLI: `ace discover run/list/show`;
  no placeholder code.

- **Phase 3 Milestone 3.3: Versioned Scoring and Confidence Engine** —
  `scoring_policies`, `opportunity_scores` DB tables (SCHEMA_VERSION 5);
  versioned immutable scoring policies (6 factors, weights validated to
  sum 1.0); active-policy enforcement via partial unique index; six scoring
  factors: trend_strength, audience_demand, competition, evergreen_value,
  audience_fit, content_novelty; confidence engine (source quality, data
  completeness, freshness, corroboration); `FactorStatus` per factor;
  missing-data policies (`reweight_available`, `apply_prior`,
  `reduce_confidence_only`) with weight rebalancing so composite sums to
  1.0; score rows append-only; latest score by `scored_at DESC, id DESC`;
  `scoring/` sub-package (engine, factors, weights, confidence, snapshot);
  CLI: `ace intelligence score/score-all/explain` and
  `ace intelligence policy list/show/create/clone/update/activate`;
  no placeholder code. 476 tests pass.

- **Phase 3 Milestone 3.4: Opportunity Promotion Workflow** —
  `topics.promoted_opportunity_id` nullable FK column with partial unique
  index (SCHEMA_VERSION 6); `promote_opportunity()`: score prerequisite
  guard; lifecycle guard (`new`/`under_review` only); SAVEPOINT atomicity
  (topics INSERT + lifecycle transition in one savepoint); idempotent (returns
  existing topic on second call); `get_topic_by_promoted_opportunity()`;
  CLI: `ace topics promote <opportunity_id> [--angle] [--operator]
  [--allow-unscored]`; architectural decisions D-M3.4-1 through D-M3.4-6
  recorded; no placeholder code. 513 tests pass. **Phase 3 complete.**

- **Phase 4 Milestone 4.1: Source Ingestion Foundation** —
  `src/app/research/` package (10 modules: constants, errors, hashing,
  models, validate, extract, quality, fetch, repository, plus `__init__.py`);
  `source_contents` table (SCHEMA_VERSION 7); append-only per-attempt rows;
  v6→v7 migration path. URL fetch with SSRF protection (pre-resolution +
  23 blocked IPv4/IPv6 ranges), HTTPS→HTTP redirect blocking (SecurityError),
  MIME allowlist and 5 MB size enforcement. Local file ingest (.txt, .md,
  .pdf); null-byte rejection, `Path.resolve()`, `S_ISREG` check, extension
  allowlist, 10 MB size limit. HTML extraction (BeautifulSoup4 + html.parser;
  title, author, publication date). PDF extraction (pypdf; page separators;
  up to 200 pages; partial extraction on failure). Deterministic quality
  scoring (7 factors, weights asserted to sum 1.0; linear recency decay;
  `quality-v1` scorer version). SHA-256 `retrieval_hash` (raw bytes) and
  `normalized_text_hash` (NFC-normalized extracted text). Idempotency via
  `normalized_text_hash` comparison; `--force` to override. SAVEPOINT
  atomicity throughout. CLI: `ace sources fetch`, `ace sources ingest-file`,
  `ace sources quality`. New dependencies: `beautifulsoup4>=4.12,<5.0`,
  `pypdf>=4.0,<6.0`. No LLM calls; no Phase 4.2 claim extraction; no live
  network or live LLM in any test. 696 tests pass.

- **Phase 4 Milestone 4.2: Evidence & Claim Extraction** —
  Three new DB tables (`claim_extraction_runs`, `claim_extraction_run_calls`,
  `claims`) at SCHEMA_VERSION 8; v7→v8 migration. Supersession model via
  `superseded_at`/`superseded_by_run_id` columns (not a status value);
  4-value status CHECK (`running`, `completed`, `partial`, `failed`).
  Paragraph-aware chunker (`chunking.py`): greedy accumulation with sentence
  splitting and hard-cut fallback; exact chunk offset invariant
  `chunk.text == raw_text[start:end]` enforced. Quote support classifier
  (`claim_support.py`): exact → normalized (NFC + CRLF + whitespace map) →
  unsupported → no_quote; offsets NULL when NFC changes character count.
  PDF page derivation from `--- Page N ---\n` separators. Deterministic
  date-review risk flags (`claim_risk.py`): 4 rules, Rule 4 suppressed by
  historical-year pattern. Repository layer additions: `create_claim_extraction_run`,
  `create_claim_extraction_run_call`, `update_claim_extraction_run_call`,
  `finalize_claim_extraction_run` (single SAVEPOINT for all claim INSERTs +
  optional supersession + status update), `list_claims`, `get_latest_completed_run`,
  `list_active_evidence_for_topic`. Active evidence: `status='completed' AND
  superseded_at IS NULL AND quote_support_status IN ('exact','normalized')`.
  Prompt TOML: `src/app/ai/prompts/claim-extraction/v1.toml`. Extractor
  orchestrator (`extractor.py`): idempotent (returns existing run on same
  input_hash unless `--replace`); per-chunk AI call recorded regardless of
  outcome; atomic finalization with failure-injection recovery. CLI: `ace
  sources extract-claims`, `ace sources list-claims`, `ace sources claim-runs`.
  No new runtime dependencies. No live LLM or live HTTP in any test.
  825 tests pass.

- **Phase 5–6 M6.3C:** Script generation → production plan → narration TTS →
  caption artifacts → ElevenLabs provider integration. 1889 tests. SCHEMA_VERSION 12.

- **Phase 7 — Visual Intelligence & Scene Planning:** `src/app/scenes/` package
  (8 modules); SCHEMA_VERSION 12 → 13 (4 new tables); deterministic scene
  manifests with immutable input hash, evidence linkage, full licensing metadata,
  approve/reject/supersession review workflow, operator CLI; `asset_strategy.py`
  extracted as Phase 8 seam. 130 new tests. 2019 total. Ruff clean.

- **Phase 8: Rendering Engine** — `src/app/media/` package with
  `RenderBackend` Protocol, `FFmpegRenderBackend`, render manifests, render
  jobs, `ApprovedRender` handoff, append-only review events, full state
  machine (draft→approved/rejected), SHA-256 output hash, reproducibility
  provenance; `ace render` CLI (compose/start/list/show/approve/reject/retry/
  cancel/events/doctor); SCHEMA_VERSION 13→14; 204 new tests. 2154 total.
  Ruff clean.

- **Phase 9: Publishing & Orchestration Engine** — `src/app/publishing/`
  package with `PublishingProvider` Protocol, `FakePublishingProvider`,
  `YouTubePublishingProvider` (injectable `FakeYouTubeAPIClient` for tests);
  three distinct lifecycles (plan/job/publication) with separate state
  machines; supersession via `superseded_at`/`superseded_by_id` fields;
  SHA-256 idempotency hash; `MAX_RETRY_ATTEMPTS=3` guard; dry-run safe
  default; append-only review events; SCHEMA_VERSION 14→15 (4 new tables:
  `publishing_plans`, `publishing_jobs`, `publications`,
  `publishing_review_events`); `ace publish` CLI (11 subcommands); OAuth
  credential file design: path-only env vars, never secrets in SQLite;
  204 new tests. 2358 total. Ruff clean.

- **Phase 10: Platform Analytics Engine** — `src/app/analytics/` package
  with `AnalyticsProvider` Protocol, `FakeAnalyticsProvider`,
  `YouTubeAnalyticsProvider` (fixture-tested boundary, no live calls);
  AGG_SUM/AGG_LAST metric semantics; deterministic SHA-256 hashing;
  7-check publication eligibility guard (no bypass); deduplication for
  additive metrics; currency contract; source lineage via
  `source_snapshot_ids_json`; `AnalyticsHandoff` Phase 11 bundle;
  SCHEMA_VERSION 15→16 (4 new tables: `analytics_snapshots`,
  `analytics_metrics`, `analytics_aggregates`, `analytics_review_events`);
  `ace analytics` CLI (8 subcommands); 230 new tests. 2588 total. Ruff clean.

- **Phase 12: Media Operations Control Plane** — `src/app/control_plane/` package;
  22-table SCHEMA_VERSION 18 (`cp_organizations`, `cp_workspaces`, `cp_channels`,
  `cp_platforms`, `cp_credential_profiles`, `cp_platform_accounts`,
  `cp_publishing_profiles`, `cp_analytics_identities`, `cp_automation_policies`,
  `cp_strategy_profiles`, `cp_events`, `cp_event_processing`, `cp_workflows`,
  `cp_workflow_runs`, `cp_experiments`, `cp_experiment_variants`,
  `cp_experiment_assignments`, `cp_operation_executions`, `cp_cost_records`,
  `cp_budget_policies`, `cp_health_records`, `cp_provider_registry`);
  permanent identity hierarchy (organization → workspace → channel → platform →
  platform_account → credential_profile; publishing_profile and analytics_identity
  are account-scoped); credentials store `external_ref` only (no plaintext secrets);
  MANUAL / SUPERVISED / AUTONOMOUS automation levels (most restrictive wins);
  durable append-only event bus with `UNIQUE(event_id, handler_key)` idempotency
  and dead-letter after 3 attempts; structured workflow engine (8 operators, 6
  action types; no eval/exec); experiment immutability (active/concluded/cancelled
  cannot be mutated); three-tier budget enforcement (workspace/channel/account;
  warn/pause/block); `concurrency.py` (`check_concurrency_limit()`,
  `ConcurrencyLimitExceededError`); `workspace_control_center_status()` unified
  dashboard projection; `workspace_audit_timeline()` chronological audit log;
  actor-aware mutations throughout; cross-workspace isolation enforced at query
  boundary; CP service layer (`services.py`) for frontend consumption without
  direct SQLite access; `ace cp` CLI subcommand group; 255 new tests.
  3101 total. Ruff clean.

- **Phase 11: Learning & Optimization Engine** — `src/app/learning/` package;
  deterministic, explainable recommendations from analytics history with no
  ML, no embeddings, no network calls, no automatic application; six recommendation
  generators (CTR, retention, engagement, watch-time, subscribers, shares) each with
  named `GeneratorResult` tracking; three-factor confidence scoring (volume × effect
  × consistency) — scores are deterministic heuristic signal strength, not statistical
  confidence intervals; single observation period yields zero consistency contribution;
  duplicate snapshot IDs across evidence items do not inflate volume; recommendations
  classified as `exploratory` (insufficient evidence) or `actionable` (≥2 unique
  snapshots AND confidence ≥ 0.4); all Phase 11 evidence is `observational` —
  `experiment_id` alone does NOT qualify as `controlled_experiment`; append-only
  `optimization_recommendations` with full evidence JSON and SHA-256 hashes;
  hashes include `evidence_classification` and `recommendation_strength`; human-only
  review lifecycle (pending → accepted/rejected); supersession is not rejection —
  superseded recommendations remain inspectable; generator failures recorded in
  `learning_run_generator_results` (visible and attributable); mixed generator
  success/failure yields `partial` run status; attribution through AnalyticsHandoff
  FK chain; Phase 11 consumes only `AnalyticsHandoff` — upstream human-review
  signals (scripts, narration, scenes, rendering, publishing) are not ingested;
  `ReviewedOptimizationHandoff` is the frozen Phase 12 input boundary;
  SCHEMA_VERSION 16→17 (4 new tables: `learning_runs`,
  `optimization_recommendations`, `recommendation_review_events`,
  `learning_run_generator_results`); `ace learn` CLI (7 subcommands);
  258 new tests. 2846 total. Ruff clean.

---

## Roadmap

Each phase entry states: objective, business value, technical scope,
dependencies, database changes, interfaces, tests, human approval gates,
risks, definition of done, demonstrable capability, and what waits.

---

### Phase 4 — Research and Source Management

**Objective:** Ingest source material, extract claims, score source quality,
and preserve citation records so generated content is grounded and verifiable.

**Business value:** Factual accuracy and proper attribution protect channel
credibility, reduce copyright risk, and distinguish content from generic
AI output.

**Technical scope:**
- `src/app/research/` package
- Source ingestion: URL fetch (requests + BeautifulSoup or trafilatura),
  local file read (PDF via pypdf, plain text, markdown), manual notes
- Content extraction: title, author, publication date, body text
- Claim extraction via LLM: given source text → list of factual claims with
  direct quotes; structured output validated against schema
- Source quality scoring (deterministic heuristics): publication recency,
  domain type (academic, government, news, blog, unknown), author credibility
  signals present/absent, original reporting vs. aggregation
- Claim-to-source mapping: each claim records source_id, quote, page/
  timestamp if applicable
- Factual-risk flags: claims with no corroborating source, claims that
  contradict other sources, outdated statistics
- Citation preservation: exportable citation record per script (for potential
  description or pinned comment use)
- Asset rights metadata: for any media asset attached as a source, record
  licence type and attribution requirements

**Dependencies:** Phase 1, Phase 2 (LLM for claim extraction).

**Database changes:**
- Extend `sources` table: `url`, `fetched_at`, `raw_text`, `title`,
  `author`, `published_at`, `domain_type`, `quality_score`
- New `claims` table: `(id, source_id, topic_id, claim_text, quote,
  risk_flag, created_at)`
- New `asset_rights` table: `(id, source_id, licence_type, attribution_text,
  commercial_ok, modification_ok)`

**Interfaces:**
- `ace sources fetch <url>` — ingest and extract
- `ace sources extract-claims <source_id>` — run LLM claim extraction
- `ace sources list-claims <topic_id>` — view claims for a topic
- `ace sources quality <source_id>` — print quality score breakdown

**Tests:** Mock HTTP responses; verify extraction for URL and file types;
verify claim schema validation; verify risk-flag logic; no live network in CI.

**Human approval gates:** None (automated research step); factual-risk flags
surface to human at script-approval gate.

**Risks:** Paywalled sources; extraction quality on heavily formatted pages;
LLM hallucinating claims not in source text.

**Definition of done:** URL and file sources ingest correctly; claims
extracted and stored with source mapping; risk flags raised on uncorroborated
claims; all tests pass; ruff clean.

**Demonstrable capability:** `ace sources fetch <url>` → `ace sources
extract-claims <id>` → `ace sources list-claims <topic_id>` shows grounded
claims with source provenance.

**What waits:** Script generation (needs claims as input), media production.

---

### Phase 5 — Script Generation ✅ COMPLETE

**Objective:** Generate source-grounded short-form scripts with deterministic
validation, citation tracking, and atomic approval.

**Business value:** This is the primary content output for Shorts. Quality
here — grounding in evidence, citation accuracy, duration fit — determines
watch time, retention, and algorithmic distribution.

**Technical scope (implemented):**
- `src/app/content/` package: `constants`, `errors`, `schemas`, `hashing`,
  `renderer`, `validator`, `models`, `repository`, `generator`
- SCHEMA_VERSION 9: `scripts` extended with `body_json`, `format`,
  `approved_at`, `superseded_at`; new `script_generation_runs` and
  `script_citations` tables; v8→v9 migration
- `sort_evidence()`: canonical 5-key ordering used for prompt context,
  evidence hash, reproducibility (invariant 4)
- `validate_script()`: 12-step pipeline; bidirectional marker/ID equivalence,
  duration bounds (15–90 s), zero-evidence mode (invariant 11)
- `finalize_generation_run()`: SAVEPOINT — INSERT script → INSERT citations →
  UPDATE run → optional supersede prior run → RELEASE → commit once
- `approve_script()`: SAVEPOINT — supersede prior active approved Scripts →
  set `approved_at` → RELEASE; prior scripts retain `status='approved'` and
  receive `superseded_at` (invariants 18–20)
- Idempotency: `find_completed_run_by_input_hash()` returns existing completed
  non-superseded run; `was_idempotent=True` in result
- Phase 6 handoff: `get_active_approved_generated_script()` raises
  `UnstructuredApprovedScriptError` for manual scripts (invariants 24–25)
- Prompt: `src/app/ai/prompts/script-generation/v1.toml`

**Dependencies:** Phase 1, Phase 2 (LLM), Phase 4 (claims and sources).

**Interfaces:**
- `ace scripts generate <topic_id>` — generate from active evidence
- `ace scripts approve <script_id>` — atomic approval with supersession
- `ace scripts show <script_id>` — display script body and metadata
- `ace scripts runs <topic_id>` — list generation run history
- `ace scripts citations <script_id>` — list evidence citations

**Tests:** 999 passing (17 generator tests, 22 content-repository tests,
34 validator tests, 25 renderer tests, 31 hashing tests, 20 schema tests,
6 constant tests, plus extended database and core repository tests).

**Definition of done:** ✅ All 26 invariants enforced, Ruff clean, 999 tests
passing, documentation updated.

**What waits:** Phase 6 M6.1 Production Plan (complete), Phase 6 M6.2
Narration (complete — see below).

---

### Phase 6 Milestone 6.1 — Production Plan ✅ COMPLETE

**Objective:** Convert an approved script into a production plan: a structured,
segment-by-segment breakdown with estimated durations, word counts, and citation
mappings, ready for narration generation in M6.2.

**Business value:** The production plan is the atomic unit for platform analytics
(each segment is the granular retention attribution target), review governance
(approval/rejection workflow with structured review events), and the M6.2
narration handoff.

**Technical scope (implemented):**
- `src/app/production/` package: `constants`, `errors`, `hashing`, `models`,
  `renderer`, `repository`
- SCHEMA_VERSION 10: four new tables — `production_plans`, `production_segments`,
  `production_segment_citations`, `production_plan_review_events`; v9→v10
  migration applied to all existing migration branches
- Two partial unique indexes for active-plan isolation (normal vs. experiment):
  `idx_pp_one_active_normal` (WHERE experiment_id IS NULL),
  `idx_pp_one_active_experiment` (WHERE experiment_id IS NOT NULL)
- Three version constants bound to `input_hash`; any version change invalidates
  old plans and forces a new plan row
- `build_production_plan(approved_script) → ProductionPlanDraft` — pure
  deterministic function; hard invariant: `segment.narration_text ==
  strip_markers(section.text)`; unclamped segment duration (no [15,90] clamp)
- `create_production_plan()` SAVEPOINT (plan → segments → citations);
  `UNIQUE(script_id, input_hash)` enforces idempotency
- `approve_production_plan()` SAVEPOINT (supersede prior active normal plan →
  set approved → insert approved review event)
- `reject_production_plan()` SAVEPOINT (set rejected → insert rejected review
  event); prior approved plan is NOT touched
- `experiment_id TEXT` nullable: all M6.1 plans NULL; enables A/B testing
  without future migration
- `production_plan_review_events`: denormalized training-label fields
  (topic_id, script_id, evidence_hash, model, prompt_hash, experiment_id) for
  platform-neutral analytics
- `ApprovedProductionPlan` — frozen Pydantic handoff for M6.2 narration
- `require_active_approved_production_plan()` raises `NoApprovedProductionPlanError`;
  `get_active_approved_production_plan()` returns None
- CLI: `ace production plan/show/list/approve/reject/feedback`

**Tests:** 1155 passing (+156 new: 7 constants, 18 hashing, 22 models, 46
renderer, 36 repository, 15 CLI, plus extended database tests for v10 schema).

**Definition of done:** ✅ All 39 frozen invariants enforced, Ruff clean, 1155
tests passing, documentation updated.

**What waits:** Phase 6 M6.2 — narration generation (complete — see below).

---

### Phase 6 Milestone 6.2 — Narration Generation ✅ COMPLETE

**Objective:** Synthesise audio narration for every segment in an approved
production plan using an injectable TTS provider abstraction. No live TTS
provider integrated; `FakeTTSProvider` used for all tests (M6.3 wires a real
provider).

**Business value:** Narration is the audio backbone of the finished Short.
The M6.2 pipeline establishes cost tracking, idempotency, exception-based
review governance, and artifact management so M6.3 can simply swap in a live
provider.

**Technical scope (implemented):**
- `src/app/narration/` package: `constants`, `errors`, `hashing`, `models`,
  `protocol`, `fake`, `pricing`, `storage`, `repository`, `orchestrator`
- SCHEMA_VERSION 11: five new tables — `voice_profiles`, `narration_runs`,
  `narration_segment_assets`, `tts_calls`, `narration_review_events`;
  v10→v11 migration applied to all existing migration branches
- `TTSProvider` `@runtime_checkable` Protocol — same pattern as `AIProvider`;
  `FakeTTSProvider` deterministic silence WAV via stdlib `wave`; no new deps
- `TTSPricingRegistry`: character-based pricing; fake model = $0.00;
  `get_default_registry()` singleton; `register()` for extension
- Two input hashes: segment hash (19 fields), run hash (14 fields) — both
  SHA-256 of compact sorted JSON; any field change forces re-synthesis
- `NARRATION_SCHEMA_VERSION = "Narration-v1"`;
  `NARRATION_ALGORITHM_VERSION = "narration-segment-v1"`
- `ACE_ARTIFACTS_PATH` config; WAV files under
  `{artifacts_path}/narration/{plan_id}/{run_id}/segment_{id}.wav`;
  relative paths in DB; `/artifacts/` in `.gitignore`
- Atomic audio write: `.tmp` → validate WAV (stdlib `wave`) → SHA-256 →
  `os.replace()`; `AudioValidationError` on corrupt bytes
- `narrate_plan()`: idempotent on completed/approved run; resumes running
  run; TTS call OUTSIDE DB transaction; `record_tts_call()` auto-commits
  outside SAVEPOINT; SAVEPOINTs: `create_vp`, `approve_nr`, `reject_nr`,
  `finalize_nsa`, `reject_nsa`
- Exception-based review: segments start `synthesized`; operator rejects
  only; approval requires zero rejected segments and all plan segments covered;
  severity validated 1–5 BEFORE SAVEPOINT (`InvalidNarrationSeverityError`)
- Supersession contract: prior approved run keeps `status='approved'`;
  `superseded_at` + `superseded_by_run_id` mark it historical; partial unique
  index `WHERE status='approved' AND superseded_at IS NULL` enforces at-most-one
- `regenerate_segment()`: rejected asset → pending asset (committed) → TTS
  OUTSIDE SAVEPOINT → WAV write → `finalize_narration_segment_asset()` SAVEPOINT;
  pending asset deleted + committed on TTS or finalization failure
- `narration_review_events`: 21 cols; denormalized training context frozen at
  insert (`plan_id`, `script_id`, `topic_id`, `voice_profile_id`, `provider`,
  `model`, `voice_id`, `experiment_id`); `actor` field; `severity` 1–5 CHECK;
  `expected_correction`; `replacement_asset_id` for `segment_regenerated`
- `dry_run=True`: skips all synthesis; returns segment IDs as skipped
- `experiment_id` threading from run to segment assets
- Config: `ACE_TTS_PROVIDER` (default `fake`), `ACE_TTS_MODEL`
  (default `fake/FAKE`)
- CLI: `ace narration voices/add-voice/narrate/runs/approve/reject-run/
  reject-segment/events/regenerate-segment`

**Tests:** 1326 passing (+171 new vs baseline: including 19 corrections-phase
tests covering supersession contract, severity boundary, immutable event context,
and regenerate-segment CLI).

**Definition of done:** ✅ Ruff clean, 1326 tests passing, documentation
updated. FakeTTSProvider only; no paid-provider SDK added; no live TTS calls
in any test.

**What waits:** Phase 6 M6.3A — Caption and Timing Artifacts (complete — see below).

---

### Phase 6 Milestone 6.3A — Caption and Timing Artifacts ✅ COMPLETE

**Objective:** Generate SRT, WebVTT, and JSON caption artifacts for every
approved narration run using deterministic text segmentation and proportional
timing estimation. No live TTS provider, no forced alignment, no network access.

**Business value:** Captions are required for Shorts discoverability and
accessibility. M6.3A delivers production-ready caption files (SRT/VTT/JSON)
with a full review governance model, enabling human correction before M6.3B
wires real TTS timestamps.

**Technical scope (implemented):**
- `src/app/captions/` package: `constants`, `errors`, `hashing`, `models`,
  `segmentation`, `timing`, `validation`, `exporters`, `storage`,
  `repository`, `orchestrator`
- SCHEMA_VERSION 12: three new tables — `caption_runs` (34 cols;
  `UNIQUE(narration_run_id, input_hash)`; two partial unique indexes for
  normal/experiment active-run isolation), `caption_cues` (17 cols; immutable
  after insert; segment-relative timestamps in integer milliseconds;
  `timing_source='estimated'`), `caption_review_events` (10 cols; append-only;
  `event_type` CHECK: `run_approved/run_rejected/cue_rejected`)
- Five version constants (`CAPTION_SCHEMA_VERSION`, `CAPTION_SEGMENTATION_VERSION`,
  `CAPTION_TIMING_ALGORITHM_VERSION`, `CAPTION_STYLE_VERSION`,
  `CAPTION_EXPORTER_VERSION`) bound to `input_hash`; any version change
  forces a new caption run row
- `segment_narration_text()`: sentence-aware segmentation; abbreviation
  handling (Dr., Mr., U.S., etc.); decimal number protection; max 2 lines
  per cue; 42 char/line limit; text integrity invariant enforced
- `allocate_timing()`: cumulative proportional allocation by display-char
  count; all timestamps integer milliseconds; first cue starts at 0,
  last cue ends at `duration_ms`; no overlap
- `validate_caption_cues()`: per-cue geometry, non-negative start,
  start<end, duration bounds, overlap, index gaps, text integrity,
  timing source, asset/hash consistency; `ValidationResult` dataclass
- `render_srt()`: 1-based index, HH:MM:SS,mmm → HH:MM:SS,mmm
- `render_vtt()`: WEBVTT header, HH:MM:SS.mmm → HH:MM:SS.mmm
- `render_json()`: provenance document with all version constants, cue array
- Atomic export write: `.tmp` → `os.replace()`; SHA-256 of UTF-8 content;
  export paths stored in DB; SQLite is canonical, files are derived
- `generate_captions()` orchestrator: 9-step pipeline; idempotent (returns
  existing completed/approved run on matching input hash); failed-run rule
  (no auto-restart; raise `FailedCaptionRunError`); marks run `failed` on
  any exception; `conn.commit()` only on clean completion
- `ApprovedNarrationRun` + `ApprovedNarrationSegment` frozen dataclasses
  added to `src/app/narration/models.py` as handoff models
- `get_approved_narration_run_full()` added to narration repository;
  assembles full handoff with all segments ordered by `segment_index`
- Exception-based review: `cue_rejected` events block approval
  (`CueRejectionBlocksApprovalError`); supersession is not rejection
  (prior approved runs keep `status='approved'`)
- CLI: `ace captions generate/runs/approve/reject/reject-cue/events`

**Tests:** 1563 passing (+81 new vs M6.2 baseline: 27 exporters, 23 storage,
35 repository, 12 orchestrator, 11 CLI, plus extended database tests for v12
schema; segmentation/timing/validation tests from earlier stages).

**Definition of done:** ✅ Ruff clean, 1563 tests passing, documentation
updated. No live TTS provider added; no network access in any test; no
forced alignment.

---

### Phase 6 M6.3C — Live ElevenLabs Provider Integration ✅ Complete

**Objective:** Integrate ElevenLabs as the first live TTS provider; wire it into
the provider infrastructure built in M6.3B; deliver credential-safe, test-isolated
synthesis with character-level alignment, loudness measurement, and bounded retry.

**Technical scope (implemented):**
- `src/app/narration/providers/elevenlabs.py` — ElevenLabsTTSProvider satisfying
  TTSProvider + ProviderLifecycle; uses `/with-timestamps` endpoint; lazy SDK import;
  3-retry bounded backoff; no audio normalisation; RMS dBFS measurement; credential guard
- 4 new capability flags: `supports_alignment`, `supports_seed`, `supports_voice_cloning`,
  `supports_pronunciation_dictionary`
- 2 new error types: `ProviderCredentialError`, `ProviderRateLimitError`
- Pricing registered: `eleven_multilingual_v2=$0.10/1K`, `eleven_flash_v2_5=$0.05/1K`
- Factory/loader updated to handle `"elevenlabs"`; default registry unchanged
- CLI: `ace narration smoke-test` (manually opt-in; skipped in CI)
- New dependency: `elevenlabs>=2.61.0,<3.0` (floor matches verified SDK 2.61.0)

**Tests:** 1889 passing, 1 skipped (79 new unit tests in `test_narration_elevenlabs.py`;
always-skipped live smoke test in `test_narration_elevenlabs_smoke.py`; factory test updated).

**Definition of done:** ✅ Ruff clean, 1889 passing, git diff --check clean,
no live network calls in CI, credentials never appear in logs or DB.

---

### Phase 6 M6.3B — Narration Provider Infrastructure ✅ Complete

**Objective:** Implement the complete provider infrastructure for narration —
every abstraction required for future live providers while intentionally
leaving FakeTTSProvider as the only concrete implementation.

**Technical scope (20 abstractions delivered):**
- **provider registry** — `ProviderRegistry`; `get_default_provider_registry()`
- **capability model** — `ProviderCapabilities`; `accepts_*` helpers
- **provider metadata** — `ProviderMetadata.to_reproducibility_dict()`; captures
  every field needed to reproduce or verify any generated artifact
- **provider configuration objects** — `ProviderConfig`, `ProviderConfigRegistry`
- **provider discovery** — `ProviderRegistry.discover()` (sorted provider names)
- **provider selection** — `ProviderSelector` Protocol; `DefaultProviderSelector`
- **provider validation** — `ProviderValidator` Protocol; `DefaultProviderValidator`;
  `ProviderCompatibilityResult`
- **provider factory** — `ProviderFactory` Protocol; `DefaultProviderFactory`
- **provider loading** — `ProviderLoader` Protocol; `DefaultProviderLoader`;
  `RegisteringProviderLoader`
- **provider lifecycle** — `ProviderLifecycle` Protocol; `ProviderLifecycleState`
  enum; FakeTTSProvider implements lifecycle without gating synthesis
- **provider routing abstraction** — `ProviderRouter` Protocol; `DefaultProviderRouter`
- **provider pricing abstraction** — `ProviderPricingPolicy` Protocol (satisfied by
  existing `TTSPricingRegistry`)
- **provider usage accounting abstraction** — `UsageRecord`, `UsageAccumulator`
- **provider benchmark abstraction** — `ProviderBenchmark` Protocol;
  `InMemoryProviderBenchmark`; `BenchmarkSample`, `BenchmarkResult`
- **provider health abstraction** — `ProviderHealthCheck` Protocol;
  `InMemoryProviderHealthCheck`; `ProviderHealthStatus`, `ProviderHealthReport`
- **provider failover abstraction** — `FailoverPolicy` Protocol; `NoFailoverPolicy`;
  `ProviderFailoverChain`
- **provider caching abstraction** — `ProviderResponseCache` Protocol; `CacheKey`,
  `CacheEntry`; `NoOpResponseCache` (default); `InMemoryResponseCache`
- **provider versioning** — `ProviderVersion`, `ProviderVersionRegistry`;
  schema/algorithm compatibility checking
- **provider feature flags** — `ProviderFeatureFlags`; string constants in
  `PROVIDER_FEATURE_*`; `has_feature()` string-keyed lookup
- **provider compatibility checking** — `DefaultProviderValidator` validates
  language, format, sample rate, speaking rate, and character count

**FakeTTSProvider changes:** Implements `ProviderLifecycle`; exposes
`FAKE_CAPABILITIES`, `FAKE_FEATURE_FLAGS`, `FAKE_METADATA`, `FAKE_PROVIDER_VERSION`,
`FAKE_PROVIDER_CONFIG` module-level constants. Synthesis behaviour unchanged.

**Narration orchestration:** unchanged — no modifications to `orchestrator.py`,
`protocol.py`, `hashing.py`, `repository.py`, `storage.py`, or any DB schema.

**New errors:** 7 new error types under `ProviderInfrastructureError`.

**New constants:** `PROVIDER_FEATURE_*` names (10), `PROVIDER_LANGUAGE_WILDCARD`,
`PROVIDER_INFRASTRUCTURE_VERSION`.

**Tests:** 1810 passing (+247 new: 15 new test files covering all 20 abstractions).

**Definition of done:** ✅ Ruff clean, 1810 tests passing, git diff --check clean.
No live provider SDK added. No external API calls. No network tests.
No DB schema changes. FakeTTSProvider synthesis behaviour unchanged.

**What waits:** Phase 6 M6.3C — Live TTS Provider Integration (requires
operator approval of provider selection).

---

### Phase 7 — Visual Intelligence & Scene Planning ✅ COMPLETE

**Objective:** Build the Visual Intelligence Engine foundation: deterministic
scene manifests that describe every second of future video, with licensing
metadata, evidence linkage, immutable review history, and natural extension
points for Phase 8 asset providers.

**Status:** Complete. 2019 tests pass. SCHEMA_VERSION = 13. Ruff clean.

**What was built:**
- `src/app/scenes/` — Visual Intelligence Engine root package
- `scenes/constants.py` — shot types, camera grammar, transitions, 14 asset categories, license statuses, priorities
- `scenes/models.py` — `PlannedAssetDraft`, `PlannedSceneDraft`, `SceneManifestDraft` (mutable); frozen Pydantic DB projections; handoff objects
- `scenes/hashing.py` — SHA-256 immutable input hash (order-sensitive, segment-level)
- `scenes/asset_strategy.py` — deterministic 1–3 asset recommendations per scene (Phase 8 seam)
- `scenes/planner.py` — orchestrates shot type, camera grammar, transitions, timing, visual objectives, confidence
- `scenes/repository.py` — full CRUD + approve (with supersession) + reject + scene-level rejection + review events + full handoff
- `scenes/cli.py` — `ace scenes plan/list/show/approve/reject/reject-scene/events/manifest`
- 4 new DB tables: `scene_manifests`, `scene_manifest_scenes`, `scene_manifest_assets`, `scene_manifest_review_events`
- 130 Phase 7-specific tests

**What waits:** Phase 8 — Asset Provider Integration.

---

### Phase 8 — Video Rendering

**Objective:** Render a validated Shorts video locally using FFmpeg.

**Business value:** Closes the local production loop. After this phase,
the system can produce a real, publishable video file end to end.

**Technical scope:**
- `src/app/media/renderer.py`
- FFmpeg wrapper (subprocess, not a Python binding to avoid version conflicts)
- Render from scene manifest: concatenate asset clips, overlay narration,
  burn captions, apply text overlays
- Shorts output spec: 1080×1920, H.264 (libx264), AAC audio, ≤60 seconds
- Output validation: FFprobe checks resolution, codec, duration, audio
  loudness, file integrity
- Render cost: wall-clock time tracked; no API cost but stored for
  profitability calculation
- Render retries: configurable; failed renders logged with FFmpeg stderr
- Thumbnail extraction: extract a candidate frame for each scene

**Dependencies:** Phase 7 (scene manifest).

**Database changes:**
- New `renders` table: `(id, manifest_id, output_path, duration_s,
  file_size_bytes, render_time_s, validated, error, created_at)`
- New `thumbnails` table: `(id, render_id, file_path, scene_index,
  selected, created_at)`

**Interfaces:**
- `ace render <manifest_id>` — render video
- `ace render validate <render_id>` — run FFprobe validation
- `ace render thumbnail <render_id>` — list and select thumbnail candidates

**Tests:** Mock FFmpeg and FFprobe subprocess calls; test validation logic;
test error capture; test thumbnail selection.

**Human approval gates:** Watch rendered video and approve before upload.

**Risks:** FFmpeg not installed; render time on slow hardware; audio sync
drift on long renders (Shorts should be fine); caption burn quality.

**Definition of done:** Render produces a spec-compliant MP4; FFprobe
validation passes; thumbnails extracted; all tests pass; ruff clean.

**Demonstrable capability:** `ace render <manifest_id>` → valid MP4 playable
in any video player, ≤60 seconds, correct aspect ratio.

**What waits:** YouTube publishing.

---

### Phase 9 — End-to-End Local Pipeline

**Objective:** Connect every phase into a single supervised pipeline run that
produces a complete, approved, ready-to-upload Shorts video.

**Business value:** Validates that every component integrates correctly before
live API calls or money changes hands. This is the last gate before going
online.

**Technical scope:**
- `src/app/pipeline/` package
- `PipelineRun` entity (extends Phase 1 `Run`): tracks stage-by-stage
  progress, cost accumulation, and human gate outcomes
- Stage runner: executes each stage in dependency order; halts at human gates
- Resume support: a paused run can be resumed from the last completed stage
- Rollback: if a stage fails after side effects, record the failure and halt
  (no automatic rollback of DB state)
- Cost summary: at pipeline completion, print total LLM cost, TTS cost,
  render time, and estimated production cost
- Pipeline status CLI: show current stage and gate status for any run

**Dependencies:** Phases 2–8 all complete.

**Database changes:**
- Extend `runs` table: `stage`, `cost_summary_json`, `gate_outcomes_json`

**Interfaces:**
- `ace pipeline run <topic_id>` — start a supervised pipeline run
- `ace pipeline status <run_id>` — show stage progress
- `ace pipeline resume <run_id>` — continue from last gate

**Tests:** Integration test running the full pipeline with mocked LLM, TTS,
FFmpeg, and YouTube API; verify gate halts; verify cost accumulation.

**Human approval gates:** All gates from Phases 5, 6, 7, 8 active within
the pipeline run.

**Risks:** Stage interaction bugs not caught by unit tests; cost accumulation
incorrect; resume leaving DB in inconsistent state.

**Definition of done:** Full pipeline run produces a render-validated video
with correct cost summary; all gates fire at the right stages; integration
test passes; ruff clean.

**Demonstrable capability:** `ace pipeline run <topic_id>` takes a topic
through research → generation → production → a playable local video, with
human gates at each approval point.

**What waits:** Live publishing, analytics.

---

### Phase 10 — YouTube Publishing

**Objective:** Upload approved videos to YouTube, manage scheduling and
metadata, and record publication outcomes.

**Business value:** This is the revenue-generating step. Everything before
it is production; this is deployment.

**Technical scope:**
- `src/app/publish/youtube.py`
- OAuth 2.0 flow: `google-auth-oauthlib`; credentials stored encrypted
  locally (keyring or secrets file outside the repo)
- Upload: YouTube Data API v3 `videos.insert`; resumable upload for
  reliability; initial status always `private` or `unlisted`
- Metadata submission: title, description, tags, category, language,
  `madeForKids=False` unless explicitly set
- Scheduling: set `publishAt` for future public release
- Quota handling: YouTube Data API upload costs ~1,600 units; daily limit
  10,000 units; track remaining quota
- Upload retries: exponential back-off on transient errors
- Thumbnail upload: `thumbnails.set` after video upload
- Publishing mode enforcement: channel's current mode (`manual`,
  `supervised`, `autonomous`) read from DB before any publish action
- In `manual` / `supervised` mode: explicit `ace publish approve`
  required to set status to public
- In `autonomous` mode: pre-publish checks run automatically (quality
  score ≥ threshold, licence verified, no duplicates, factual-risk below
  threshold, daily/weekly limit not reached, spending limit not reached,
  no open circuit-breaker events); publish proceeds if all pass; any
  failure halts, logs, notifies, and may demote the channel
- Kill switch: `ace publish pause <channel_id>` halts all autonomous
  publishing immediately
- Publication records: video_id, upload_time, scheduled_at,
  published_at, publishing_mode, checks_passed_json, operator

**Dependencies:** Phase 9 (end-to-end local pipeline complete).

**Database changes:**
- New `publications` table: `(id, render_id, yt_video_id, channel_id,
  publishing_mode, checks_passed_json, upload_status, scheduled_at,
  published_at, quota_cost, operator, created_at)`
- `channels` table: add `publishing_mode`, `mode_qualified_at`,
  `mode_history_json`

**Interfaces:**
- `ace publish upload <render_id>` — upload private/unlisted
- `ace publish schedule <publication_id> <datetime>` — set publish time
- `ace publish approve <publication_id>` — explicitly set to public
  (manual/supervised modes)
- `ace publish status <publication_id>` — check upload and publish status
- `ace publish pause <channel_id>` — kill switch: halt autonomous publishing
- `ace channel set-mode <channel_id> <mode>` — promote or demote mode

**Tests:** Mock YouTube API responses; test quota tracking; test retry logic;
test that autonomous mode runs all checks before publishing; test that any
failed check halts and notifies; test kill switch; test mode demotion
trigger; test that `manual` mode still requires explicit approve.

**Human approval gates:** Explicit `ace publish approve` required in manual
and supervised modes. Mode promotion requires explicit operator command.
High-risk content categories always require manual approval regardless of
channel mode.

**Risks:** OAuth token expiry; quota exhaustion during testing; YouTube
rejecting uploads for policy violations; metadata policy changes; autonomous
mode pre-publish checks missing an edge case.

**Definition of done:** All three publishing modes work correctly; pre-
publish checks enforce all thresholds in autonomous mode; kill switch halts
autonomous publishing; mode promotion and demotion recorded; publication
records include mode and checks; all tests pass; ruff clean.

**Demonstrable capability:** Channel in manual mode: local video → uploaded
privately → human approves → public. Channel in autonomous mode: video
passes all checks → published automatically; one check fails → halts and
notifies.

**What waits:** Analytics collection, experimentation.

---

### Phase 11 — Analytics and Business Intelligence

**Objective:** Collect YouTube performance and revenue metrics, calculate
production profitability, and surface actionable signals.

**Business value:** Without measurement, optimisation is guesswork. This
phase closes the production feedback loop.

**Technical scope:**
- `src/app/analytics/` package
- YouTube Analytics API: requires OAuth (same credentials as publishing)
- Metrics collected for **Shorts** (confirmed available):
  — views, impressions, impressionClickThroughRate (CTR)
  — averageViewDuration, averageViewPercentage (retention)
  — estimatedMinutesWatched (watch time)
  — subscribersGained, subscribersLost
  — likes, comments (via Data API)
  — estimatedRevenue, estimatedAdRevenue, estimatedRedPartnerRevenue (monetised channels only)
  — rpm (where available for monetised channels)
- Metrics **not available** for Shorts (or availability varies):
  — Audience retention curve at segment level: confirm at implementation
    (Shorts may have limited retention breakdown)
  — CTR by impression type: check current API docs at Phase 11
- All metrics stored with collection date and report date range
- Production cost per video: sum of LLM cost + TTS cost + any paid asset
  cost (from `llm_calls`, `tts_calls`, `assets` tables)
- Profit per video: estimated revenue − production cost
- Channel-level profitability dashboard: aggregate cost vs. revenue
- Metric collection schedule: daily poll via a cron-compatible scheduler
- Historical backfill: collect up to 90 days of history on first run
  (YouTube Analytics API limit)

**Dependencies:** Phase 10 (publications with `yt_video_id`); monetised
channel for revenue metrics.

**Database changes:**
- New `video_metrics` table: `(id, publication_id, report_date,
  collected_at, views, impressions, ctr, avg_view_duration_s,
  avg_view_pct, watch_time_min, subscribers_gained, likes, comments,
  est_revenue_usd, rpm_usd, source)`
- New `channel_metrics` table: channel-level aggregates by date
- New `cost_records` table: production cost per video with line-item
  breakdown

**Interfaces:**
- `ace analytics collect <channel_id>` — poll and store latest metrics
- `ace analytics report <video_id>` — print per-video performance summary
- `ace analytics profit <channel_id>` — print profitability summary
- `ace analytics top <channel_id> --by <metric>` — rank videos by metric

**Tests:** Mock Analytics API responses; test metric storage; test
profitability calculation; test backfill boundary conditions.

**Human approval gates:** None (data collection).

**Risks:** Analytics API quota; monetisation eligibility required for
revenue metrics; Shorts analytics availability may change; 90-day
historical limit means early data is permanently unavailable.

**Definition of done:** Metrics collected and stored; per-video and
channel-level profit calculated; report CLI commands work; all tests pass.

**Demonstrable capability:** `ace analytics collect` → `ace analytics profit`
shows real cost vs. revenue data for at least one video.

**What waits:** Experimentation, reduced-oversight operation.

---

### Phase 12 — Media Operations Control Plane (IN PROGRESS)

**Objective:** Transform the collection of specialized engines into a coherent
Media Operating System by introducing a central Control Plane that owns identity,
orchestration, multi-account management, policies, workflows, events, experiments,
resource governance, review queues, and cross-engine visibility.

**Business value:** No single engine can coordinate the full production lifecycle
across multiple brands, platform accounts, or automation tiers. The Control Plane
provides the coherent operational layer that a future frontend and production
infrastructure can build on.

**Technical scope:**
- `src/app/control_plane/` package (20+ modules)
- First-class identity model: workspace → channel → platform → platform_account →
  credential_profile (these are permanently distinct; see DECISIONS.md)
- Multi-account/multi-platform: many workspaces, many channels per workspace,
  many accounts on the same platform, many platforms per channel
- Credential profiles: safe metadata only — never OAuth tokens, refresh tokens,
  or API secrets; external_ref points to future secret store
- Universal provider registry: indexes provider capabilities across all domains
  (AI, TTS, publishing, analytics, asset, storage, notifications)
- Durable internal event bus: append-only domain events (ResearchCompleted,
  ScriptApproved, PublicationCompleted, LearningRunCompleted, etc.); idempotent
  dispatch; correlation/causation IDs; replay-safe; dead-letter state
- Strategy profiles: versioned, channel-assigned operational intent
- Automation policies: MANUAL / SUPERVISED / AUTONOMOUS with explicit allowed
  actions, cost limits, publishing limits, risk limits, emergency-stop
- Structured workflow engine: trigger → conditions (equals/not_equals/
  greater_than/less_than/in/not_in/exists/boolean) → actions; no eval, no
  arbitrary code; auditable and reproducible
- Experimentation infrastructure: experiments, variants, assignments; immutable
  once activated; attribution links to Phase 11 evidence classification
- Resource/cost management: normalized cost records attributable to workspace/
  channel/account/engine/job/provider; budget policies with hard limits
- Health/monitoring: centralized health records for engines, providers, platform
  accounts, credential profiles, jobs, workflows; stuck-job detection
- Review queue, exception queue: centralized queryable views over existing engine
  review work and Control Plane exceptions
- Pause/resume/emergency-stop: workspace, channel, platform-account, workflow scope
- Job registry: provider-neutral reference index over existing engine jobs
- Control Plane service/API boundary: application-service interfaces for future
  frontend (list workspaces/channels, get pipeline status, get account health, etc.)
- Actor-aware mutation contracts: all writes carry actor identity for future RBAC
- Cross-engine idempotency: every Control Plane operation carries correlation_id,
  idempotency_key, source_event_id; duplicate delivery → no duplicate work
- Unified audit timeline: reconstruction of who/what/where/why for any action

**Schema:** SCHEMA_VERSION 16→17→18; 19 new `cp_` prefixed tables

**CLI:** `ace control workspace|channel|accounts|providers|health|jobs|reviews|
exceptions|events|pause|resume|policies|strategies|workflows|experiments|costs|
doctor`

**Dependencies:** Phases 1–11 complete; all existing engine public interfaces
remain unchanged.

**Internal milestones:**
- M12.1 Identity & Multi-Account Foundation
- M12.2 Universal Provider Registry & Credential References
- M12.3 Durable Event Bus
- M12.4 Strategy, Policy & Automation Levels
- M12.5 Workflow Automation Engine
- M12.6 Experimentation Infrastructure
- M12.7 Resource / Cost / Budget Management
- M12.8 Monitoring / Health / Reliability
- M12.9 Jobs, Review Queue, Exception Queue, Pause/Resume
- M12.10 Control Plane Service/API Boundary
- M12.11 Integration, Isolation & Cross-Engine Idempotency
- M12.12 Documentation / Final Validation

**Testing:** Subsystem-based test organization; 12+ test files; covers identity
separation, workspace/channel/account isolation, credential references, provider
registry, event idempotency/replay, workflow evaluation, policy resolution,
experiment immutability/assignment, cost/budget, health, queues, pause/resume,
job registry, concurrency, cross-engine idempotency, audit timeline, CLI,
migrations.

**Constraints:** No network. No live providers. No frontend. No production
infrastructure (Phase 14). No Phase 13 scope.

---

### Phase 13 — Frontend / Studio / Dashboard

**Objective:** Build the operator frontend — Studio UI, dashboard, review
interfaces, and analytics views — consuming the Control Plane service boundary
established in Phase 12.

**Business value:** Replaces CLI-only operation with a visual interface for
non-technical operators and enables at-a-glance channel health and queue management.

**Technical scope:**
- Studio web interface (technology TBD at implementation time)
- Consumes Phase 12 Control Plane service/API boundary
- Workspace, channel, platform-account management UI
- Production pipeline status and review queues
- Analytics and recommendation review
- Experiment management
- Cost and budget dashboards
- Health and alert monitoring

**Dependencies:** Phase 12 (Control Plane service boundary complete)

**What waits:** Phase 14 (Deployment & Production Infrastructure)

---

### Phase 14 — Deployment & Production Infrastructure

**Objective:** Harden the system for production operation.

**Business value:** Enables commercial-scale operation with production reliability.

**Technical scope (may include):**
- PostgreSQL migration from SQLite
- Object storage (S3 or compatible) for artifacts
- Job queues / workers (Celery, RQ, or similar)
- Docker / container deployment
- Production secret management (Vault, AWS Secrets Manager, or similar)
- Database backups and point-in-time recovery
- Monitoring and alerting (Prometheus, Grafana, or similar)
- Horizontal scaling
- Disaster recovery procedures
- Deployment configuration management

**Dependencies:** Phase 13 (frontend complete; application stable)

**What waits:** Multi-tenant SaaS (future consideration)

---

### Phase 15 — Instagram and TikTok Adapters (deferred)

**Objective:** Add optional adapters for Instagram Reels and TikTok after
YouTube is stable and profitable.

**Constraint:** This phase does not begin until Phase 14 is complete and the
YouTube operation is demonstrably profitable.

**Technical scope:**
- `src/app/publish/instagram.py`, `src/app/publish/tiktok.py`
- Platform-specific metadata, aspect ratios, duration limits
- Platform analytics adapters (separate tables, not merged with YouTube data)
- Adapter interface mirrors Phase 10 publishing contract

**Dependencies:** Phase 14. Official API access for both platforms confirmed
before this phase begins.

**What waits:** Nothing — this is the final planned phase.

---

## Deferred items

- CI/CD (GitHub Actions): add after the repo moves to GitHub and the test
  suite is stable. Not blocking any phase.
- Long-form video production: deliberately deferred until Shorts workflow is
  reliable and profitable. Templates and manifest changes are isolated.
- Keyword search-volume integration from paid providers (SEMrush, Ahrefs):
  optional enhancement in Phase 3; not required for MVP discovery.
- n8n or external orchestration: APScheduler in-process is sufficient for
  Phase 13; external orchestration is only justified if multi-machine
  operation is needed later.
- Cloud deployment: local operation is the target until the system is
  profitable enough to justify infrastructure cost.
