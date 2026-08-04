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

### Phase 5 — Content Generation

**Objective:** Generate original, source-grounded scripts, hooks, titles,
descriptions, and YouTube metadata with LLM critique and human approval.

**Business value:** This is the primary content output. Quality here
determines watch time, retention, and algorithmic distribution.

**Technical scope:**
- `src/app/content/` package
- Content brief: structured summary of topic, angle, target audience,
  format (Shorts/long-form), available claims, key sources
- Hook generation: 3–5 hook options per brief, scored against hook rubric
  (clarity, curiosity, specificity, platform fit)
- Script generation: structured output — hook, body sections, call to action;
  target word count based on format (Shorts ≈ 60–90 words spoken)
- Script critique: LLM evaluates against fixed rubric (accuracy, originality,
  hook strength, pacing, claim support, platform fit); returns structured
  critique with pass/fail per criterion
- Revision loop: up to N attempts (configurable) if critique fails; each
  attempt stored as a new script version
- Human approval: CLI presents script + critique; human approves, rejects,
  or requests manual revision
- Title generation: 3–5 options; scored against YouTube title rubric (≤60
  chars, front-loaded keyword, curiosity gap, no clickbait)
- Description generation: structured description with hook sentence, key
  points, call to action, source credits, hashtags
- Tags: keyword list respecting YouTube tag guidelines
- Originality check: compare script against recent published scripts in DB
  using token overlap; flag if similarity exceeds threshold
- Prompt versioning: all generation prompts tracked via Phase 2 registry

**Dependencies:** Phase 1, Phase 2 (LLM), Phase 4 (claims and sources).

**Database changes:**
- Extend `scripts` table: `format`, `word_count`, `hook_id`,
  `critique_passed`, `critique_json`, `originality_score`
- New `hooks` table: `(id, topic_id, hook_text, score, selected, created_at)`
- New `metadata_drafts` table: `(id, script_id, title, description, tags_json,
  created_at, approved_at)`

**Interfaces:**
- `ace content brief <topic_id>` — generate and display content brief
- `ace content generate <topic_id>` — run full generation pipeline
- `ace content approve <script_id>` — human approval gate
- `ace content metadata <script_id>` — generate title/description/tags

**Tests:** Mock LLM; test brief assembly; test critique pass/fail logic;
test revision loop termination; test originality threshold; test metadata
schema validation.

**Human approval gates:** Script approval (after critique); metadata review
before proceeding to production.

**Risks:** LLM producing non-grounded claims; revision loop not converging;
originality check being too strict or too lenient; Shorts scripts being too
long.

**Definition of done:** Full pipeline runs from topic to approved script and
metadata; critique failures trigger revision; originality flags fire at
threshold; all tests pass; ruff clean.

**Demonstrable capability:** `ace content generate <topic_id>` → human
reviews critique → `ace content approve <script_id>` → metadata generated.

**What waits:** Narration, video production, publishing.

---

### Phase 6 — Narration and Captions

**Objective:** Convert approved scripts to audio narration and generate
accurate captions.

**Business value:** Narration quality directly affects watch time and
retention. Accurate captions improve accessibility and on-screen text
engagement (critical for Shorts).

**Technical scope:**
- `src/app/media/narration.py`
- TTS provider abstraction (same pattern as LLM provider): protocol,
  concrete provider (ElevenLabs or equivalent), mock provider for tests
- Voice configuration: voice ID, speed, stability settings stored per channel
- Audio output: WAV or MP3, normalised to YouTube's preferred loudness
  standard (−14 LUFS integrated)
- Duration validation: generated audio duration consistent with script word
  count; flag if materially outside expected range
- Caption generation: word-level timestamps from TTS provider (if available)
  or forced alignment; output as SRT and VTT
- Caption schema validation: verify line length, reading speed, sync accuracy
- Cost tracking: TTS characters billed, cost per narration stored in a new
  `tts_calls` table

**Dependencies:** Phase 1, Phase 5 (approved script).

**Database changes:**
- New `narrations` table: `(id, script_id, provider, voice_id, audio_path,
  duration_s, lufs, cost_usd, created_at)`
- New `captions` table: `(id, narration_id, format, file_path, created_at)`
- New `tts_calls` table: cost tracking parallel to `llm_calls`

**Interfaces:**
- `ace narration generate <script_id>` — produce audio and captions
- `ace narration play <narration_id>` — open audio file in system player

**Tests:** Mock TTS provider; test loudness validation logic; test duration
range check; test caption schema; no live TTS calls in CI.

**Human approval gates:** Listen and approve narration before video assembly.

**Risks:** TTS provider API changes; voice quality degrading between provider
versions; loudness normalisation edge cases; caption sync quality.

**Definition of done:** Mock TTS produces valid audio fixture; loudness
check and duration check pass; captions written as SRT and VTT; cost stored;
real provider smoke test succeeds; ruff clean.

**Demonstrable capability:** `ace narration generate <script_id>` → audio
file and SRT produced; duration and loudness validated.

**What waits:** Scene manifests, video rendering.

---

### Phase 7 — Licensed Assets and Scene Manifests

**Objective:** Select or generate properly licensed visual assets and produce
a scene manifest that drives video rendering.

**Business value:** Visual quality and licensing compliance protect revenue
and channel standing. Asset reuse without attribution is a common channel-
killing mistake.

**Technical scope:**
- `src/app/media/assets.py`, `src/app/media/manifest.py`
- Supported asset categories (all four are part of the finished product):
  1. Owned assets (uploaded to local asset library)
  2. Public-domain assets (US government works, expired copyright, etc.)
  3. Licensed stock (CC0, Creative Commons, paid stock APIs — Pexels,
     Pixabay, Storyblocks, Shutterstock)
  4. AI-generated visuals — **deferred to a later sub-phase** until the
     pipeline supports: provider/model tracking, commercial-use term
     confirmation, prompt and output provenance, YouTube disclosure flags,
     likeness/trademark risk checks, and quality validation
  — Never use assets without a verified licence record
- Phase 7 implements categories 1–3 only; category 4 is a named future
  capability, not an exclusion
- Asset library: local directory + DB records (licence, source URL,
  attribution, asset_category, provider, provenance_json)
- Asset rights enforcement: block scene manifest creation if any asset has
  `commercial_ok=False` and channel is monetised
- Asset selection criteria (implemented progressively): licence confidence,
  visual quality score, originality signal, production cost, historical
  performance correlation
- Shorts scene manifest: sequence of scenes, each with:
  `(scene_index, duration_s, narration_segment_id, asset_id, text_overlay,
  transition)`
- Manifest validation: total duration matches narration ± tolerance;
  all assets resolved; licences verified
- Shorts template: vertical 9:16, 1080×1920, max 60 seconds, text overlay
  positions respecting safe zones

**Dependencies:** Phase 6 (narration, captions).

**Database changes:**
- New `assets` table: `(id, channel_id, file_path, asset_type,
  asset_category, source_url, licence_type, attribution_text, commercial_ok,
  provider, provenance_json, quality_score, created_at)`
- New `scene_manifests` table: `(id, script_id, narration_id, template,
  scenes_json, validated, created_at)`

**Interfaces:**
- `ace assets add <file_path> --licence <type>` — register owned asset
- `ace assets search <query>` — search stock providers
- `ace manifest create <script_id>` — assemble scene manifest
- `ace manifest validate <manifest_id>` — check durations and licences

**Tests:** Mock stock API responses; test licence enforcement; test duration
validation; test manifest schema; test safe-zone bounds in template.

**Human approval gates:** Review manifest before rendering; confirm all
assets and licences are acceptable.

**Risks:** Stock API pricing or availability changes; licence verification
being incomplete for edge cases; Shorts safe-zone requirements changing.

**Definition of done:** Manifest assembles correctly from narration and
assets; licence enforcement blocks commercial-ok=False assets; duration
validated; all tests pass; ruff clean.

**Demonstrable capability:** `ace manifest create <script_id>` produces a
valid, licence-verified manifest ready for rendering.

**What waits:** Video rendering.

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

### Phase 12 — Experimentation and Optimisation

**Objective:** Propose and track controlled experiments to improve content
performance, with safeguards against premature or spurious conclusions.

**Business value:** Systematic experimentation compounds gains over time.
Without it, optimisation is based on anecdote.

**Technical scope:**
- `src/app/experiments/` package
- Experiment types: hook variant, title variant, posting-time variant,
  video-length variant, format variant (Shorts vs. long-form eventually)
- Experiment design: hypothesis, treatment vs. control definition,
  primary metric, minimum sample size (using a simple power calculation),
  maximum duration
- Sample-size enforcement: system refuses to declare a winner until minimum
  sample is reached
- Statistical note: store p-value and effect size for reference; display
  with a disclaimer that these are observational signals, not RCTs; avoid
  treating correlation as causation
- Result actions: promote winning variant cautiously (update channel
  defaults); retire consistently unprofitable formats (flag for human
  confirmation, do not auto-retire)
- Experiment log: every experiment and its outcome recorded permanently

**Dependencies:** Phase 11 (analytics data required before any experiment
can be evaluated).

**Database changes:**
- New `experiments` table: `(id, channel_id, experiment_type, hypothesis,
  primary_metric, min_sample_size, max_duration_days, status, winner_id,
  created_at, concluded_at)`
- New `experiment_arms` table: `(id, experiment_id, arm_name, video_ids_json,
  result_json)`

**Interfaces:**
- `ace experiments create` — define a new experiment
- `ace experiments status <id>` — check progress and sample size
- `ace experiments conclude <id>` — record outcome (human-confirmed)

**Tests:** Test sample-size enforcement; test premature-conclusion block;
test result storage.

**Human approval gates:** Concluding an experiment and promoting a winner
both require human confirmation.

**Risks:** YouTube not being an RCT environment; confounders (seasonality,
algorithm changes, trending topics); small channels not reaching sample
sizes quickly; over-optimising on noise.

**Definition of done:** Experiment lifecycle works end to end; sample-size
gate enforced; all tests pass; ruff clean.

**Demonstrable capability:** Create a hook-variant experiment, collect data,
attempt to conclude before sample size reached (blocked), reach sample size,
conclude with human confirmation.

**What waits:** Reduced-oversight operation.

---

### Phase 13 — Reduced-Oversight Operation and Publishing Mode Graduation

**Objective:** Allow the system to run scheduled production cycles with
minimal human intervention; implement the publishing-mode graduation path
from manual approval through to qualified autonomous publishing.

**Business value:** Reduces operating cost per video. Human time becomes the
bottleneck only at high-risk decisions, not at routine coordination. Channels
that demonstrate consistent quality, compliance, and cost control can
graduate to autonomous publishing, enabling the commercial scale the product
requires.

**Technical scope:**
- `src/app/scheduler/` package
- Approval queues: pending gate items delivered via CLI notification and
  optional email/webhook (provider TBD at implementation time)
- Scheduling: APScheduler or cron-compatible; configurable production
  cadence per channel
- Circuit breakers: automatic pause if any of these thresholds are exceeded:
  — daily API spend above limit
  — LLM error rate above threshold
  — TTS error rate above threshold
  — YouTube upload rejection rate above threshold
  — consecutive critique failures above threshold
  — consecutive performance anomalies (view rate drop, retention drop)
- Spending limits: per-day and per-video caps; hard block, not a soft warning
- Failure recovery: failed pipeline stages auto-retry up to N times, then
  pause and notify
- Audit log: every autonomous action recorded with timestamp, actor
  (system or user), decision, and cost
- Content-quality thresholds: configurable minimum critique score;
  auto-pauses if recent videos fall below threshold
- Publishing mode graduation engine:
  — Evaluates a channel against qualification thresholds using Phase 11
    analytics data and Phase 12 experiment outcomes
  — Produces a qualification report the operator reviews before promoting
  — Records the promotion decision, thresholds met, and qualifying window
  — Monitors for threshold breach and demotes automatically; notifies
    operator
  — High-risk content category list maintained here; overrides channel mode
- Kill switch: `ace publish pause <channel_id>` halts all autonomous
  publishing immediately, regardless of mode

**Dependencies:** Phase 12 (analytics data and experiment outcomes required
to evaluate qualification thresholds).

**Database changes:**
- New `audit_log` table: `(id, action, actor, entity_type, entity_id,
  detail_json, cost_usd, created_at)`
- New `circuit_breaker_events` table: trigger, threshold, value, resolved_at
- New `schedules` table: channel_id, cadence, next_run_at, paused, config_json
- New `mode_transitions` table: channel_id, from_mode, to_mode, reason,
  qualification_report_json, operator, created_at

**Interfaces:**
- `ace schedule set <channel_id> --cadence <cron>`
- `ace schedule pause <channel_id>`
- `ace channel qualify <channel_id>` — run qualification check, print report
- `ace channel promote <channel_id>` — promote to next publishing mode
  (operator-confirmed; shows qualification report first)
- `ace channel demote <channel_id>` — manually demote publishing mode
- `ace publish pause <channel_id>` — kill switch
- `ace audit log` — display recent audit log entries
- `ace status` — overall system health, active circuit breakers, queue depth,
  channels by publishing mode

**Tests:** Test circuit-breaker triggers; test spending-limit hard block;
test audit log completeness; test schedule cadence; test qualification check
correctly evaluates all thresholds; test automatic demotion on breach; test
kill switch; test high-risk category override.

**Human approval gates:** Creative gates (script, narration, manifest)
remain in all modes. Publishing gates depend on channel mode per Phase 10.
Mode promotion always requires operator confirmation. Circuit breakers
trigger human intervention.

**Risks:** Qualification thresholds set too low, graduating channels
prematurely; threshold breach detection delay if analytics polling is
infrequent; circuit breakers not covering all edge cases; notification
delivery failure leaving queues unattended.

**Definition of done:** Scheduled run produces a video through all stages;
circuit breakers fire correctly; audit log complete; spending limit blocks
overage; qualification report correctly evaluates thresholds; mode promotion
and automatic demotion both work; kill switch halts autonomous publishing;
all tests pass; ruff clean.

**Demonstrable capability:** Schedule a channel, let it run a full cycle
autonomously through all automated stages, pause at human gates, resume
after approval.

**What waits:** Multi-channel scaling, additional platforms.

---

### Phase 14 — Multi-Channel Scaling

**Objective:** Operate multiple YouTube channels independently, with channel-
specific configuration, niche separation, and account-level budget controls.

**Business value:** Revenue scales with channel count once a single channel
is proven. Niche separation prevents cannibalisation.

**Technical scope:**
- Channel-specific configs already in DB from Phase 3; this phase activates
  independent scheduling and budget tracking per channel
- Account-level budget: aggregate daily spend cap across all channels
- Cross-channel duplicate prevention: content originality check spans all
  channels in the same account
- Niche isolation: channels in the same niche flagged; content similarity
  above threshold blocks production
- Role-based permissions: optional, if multiple human operators manage
  different channels (simple password or API-key-based; no OAuth user
  management needed initially)
- Reporting: cross-channel profitability comparison

**Dependencies:** Phase 13 (single-channel reduced-oversight operation).

**Database changes:**
- `accounts` table: account-level config and budget
- Extend existing tables to enforce account-level foreign keys

**Interfaces:**
- `ace accounts add/list`
- `ace channels list --account <id>`
- `ace report cross-channel`

**Tests:** Test budget enforcement across channels; test cross-channel
duplicate detection; test niche isolation flag.

**Human approval gates:** Adding a new channel requires human confirmation.

**Risks:** Complexity growing faster than value; YouTube per-account API
quotas; niche isolation being overly conservative.

**Definition of done:** Two channels run independently; account-level budget
enforced; cross-channel duplicate detection fires; all tests pass; ruff clean.

**Demonstrable capability:** Two independent channels producing content,
with a cross-channel profitability report.

**What waits:** Additional platform adapters.

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
