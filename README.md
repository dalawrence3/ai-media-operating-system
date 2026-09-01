# Orvella — Autonomous Media Operating System

Orvella (internally `ai-content-engine`) is an autonomous content operating
system for YouTube Shorts: it researches a topic, generates and critiques a
script against a fixed rubric, produces narration and a vertical video,
publishes privately/unlisted, ingests analytics, and — once an operator has
explicitly authorized it — makes independent decisions about what to try
next, produces it, and releases it publicly. A closed decision → production
→ publishing → analytics → learning loop is implemented and under test
(Phase 18D); every step that leaves the local machine or goes public sits
behind its own fail-closed authorization gate, off by default.

This is a portfolio project as much as a product: every phase is built
incrementally, fully tested, and documented before the next begins. See
`PROJECT_SPEC.md` for scope and non-goals, `ARCHITECTURE.md` for the system
design, `TASKS.md` for current status, and `DECISIONS.md` for the reasoning
behind key technical choices.

## Product Showcase

The AI Media OS provides a unified interface for managing Orvella's automated content lifecycle—from production and publishing to performance analytics, experimentation, and continuous learning.

### Dashboard

The dashboard provides an executive overview of channel performance, autonomous publishing status, recent videos, active learning signals, and content pipeline health.

[![Orvella performance and publishing dashboard](product-showcase/Orvella-%20Dashboard%201.png)](product-showcase/Orvella-%20Dashboard%201.png)

The lower dashboard view connects recent publications with emerging system recommendations and summarizes the current state of the content pipeline.

[![Orvella recent content and learning dashboard](product-showcase/Orvella-Dashboard%202.png)](product-showcase/Orvella-Dashboard%202.png)

[View Dashboard screenshots at full size](product-showcase/Orvella-%20Dashboard%201.png)

### Content Library

The Content tab centralizes generated and published media, displaying each video's thumbnail, topic, duration, publication date, and platform status. Content can be filtered by publishing state, including videos currently on YouTube, publishing, failed, or archived.

[![Orvella automated content library](product-showcase/Orvella-%20Content%20tab.png)](product-showcase/Orvella-%20Content%20tab.png)

[View the Content tab at full size](product-showcase/Orvella-%20Content%20tab.png)

### Analytics

The Analytics tab ingests platform data and presents channel- and video-level performance across views, watch time, average percentage viewed, engaged views, likes, and subscribers gained. Interactive metric selection and video-level comparisons help identify differences in performance across publications.

[![Orvella channel analytics](product-showcase/Orvella-%20Analytics%201.png)](product-showcase/Orvella-%20Analytics%201.png)

[View the Analytics tab at full size](product-showcase/Orvella-%20Analytics%201.png)

### Learning and Optimization

The Learn tab compares publications, tracks experiments and creative-factor patterns, and converts observed performance into evidence-based recommendations. Recommendations remain labeled as exploratory until sufficient evidence exists, helping prevent the system from treating early signals as proven conclusions.

[![Orvella learning and optimization overview](product-showcase/Orvella-%20Learn%201.png)](product-showcase/Orvella-%20Learn%201.png)

The recommendation feed explains the evidence behind each observation, identifies the associated performance metrics, assigns a confidence level, and proposes an action for future content strategy.

[![Orvella evidence-based recommendations](product-showcase/Orvella-%20Learn%202.png)](product-showcase/Orvella-%20Learn%202.png)

[View Learning screenshots at full size](product-showcase/Orvella-%20Learn%201.png)

### Channel Strategy and Automation

The Channel tab manages the connection between Orvella and YouTube, including permissions to upload videos, read analytics, and make publications public. It also displays the channel's strategy mode, exploration progress, evidence maturity, and weighting between market intelligence and channel-specific evidence.

[![Orvella channel connection and strategy](product-showcase/Orvella-%20Channel%201.png)](product-showcase/Orvella-%20Channel%201.png)

The automation policy controls autonomous decision-making, production cadence, queue depth, production readiness, publishing slots, and public-publishing authorization.

[![Orvella automation and publishing policy](product-showcase/Orvella-%20Channel%202.png)](product-showcase/Orvella-%20Channel%202.png)

The readiness view evaluates decision automation, production, analytics and learning, provider connectivity, and publishing authorization independently. This fail-closed approach prevents a working production pipeline from publishing unless every required operational condition is satisfied.

[![Orvella operational readiness checks](product-showcase/Orvella-%20Channel%203.png)](product-showcase/Orvella-%20Channel%203.png)

[View Channel screenshots at full size](product-showcase/Orvella-%20Channel%201.png)

## Current status

**Phases 1–18 are complete.** Phases 1–15 built the deterministic production
pipeline and its production infrastructure (auth, RBAC, observability,
Docker). Phases 16–18 turned that pipeline into a multi-channel, self-driving
system: channel-isolated market intelligence and experimentation, an
autonomous decision → production → publishing cycle, cross-publication
learning, visual quality scoring, and a redesigned frontend built around
Content / Analytics / Learn / Channel rather than a single publishing queue.

The system is production-deployable via Docker Compose. JWT authentication (HS256, 15-min access tokens; Argon2id password hashing; SHA-256-hashed refresh tokens), RBAC (owner/admin/operator/reviewer/analyst; deny-by-default), provider stage-class gating (A/B/C fail-closed), structlog JSON with 27-key sensitive-field redaction, Prometheus metrics on an isolated registry, liveness/readiness health endpoints, security response headers, and a multi-stage Dockerfile with non-root runtime user are all in place. GitHub Actions CI covers backend (ruff + pytest), frontend (npm), Docker build (no push), and migration check; production deployment is intentionally manual. Backup script: `pg_dump | gzip` with 14-backup retention.

Backend: 6784 tests pass (1 skipped), ruff clean. SCHEMA_VERSION 51. Frontend: 404 tests, typecheck clean, lint clean. Cross-workspace and cross-channel isolation enforced on every command and query path.

**Development mode vs. deployed capability vs. operator authorization.**
Passing tests and a green build mean a capability is *implemented*, not that
it is running unattended against a real channel:
- **Fake by default.** `ACE_AI_PROVIDER`, `ACE_TTS_PROVIDER` default to
  in-process fake providers; the app starts and every command runs with zero
  API keys. Live providers (Claude, ElevenLabs) are opt-in per key.
- **Two independent, fail-closed gates stand between the system and the
  public internet:** `ACE_PUBLISHING_LIVE_ENABLED` (any live upload) and
  `ACE_RELEASE_PUBLIC_ENABLED` (making an uploaded video public) — both
  default `false` and must be set explicitly. The standalone analytics
  observer daemon hard-wires both off in its own process regardless of
  environment, so it can never trigger an upload.
- **Autonomy is per-channel and per-capability, not global.** Each channel's
  `decision_automation_enabled` / `production_automation_enabled` flags are
  independently off by default; the readiness view (Channel tab, above)
  evaluates decision automation, production, analytics/learning, provider
  connectivity, and publishing authorization as five separate checks and
  withholds autonomy unless all five pass — a working pipeline does not
  imply authorization to run it unattended.
- **`ACE_ENV=production`** (the default) disables the development-only auth
  bypass and requires a real `ACE_SECRET_KEY`; `ACE_ENV=development` is for
  local work only.

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
- **Phase 6 M6.3A** — Caption and timing artifacts:
  - `src/app/captions/` package: constants, errors, hashing, models,
    segmentation, timing, validation, exporters, storage, repository,
    orchestrator
  - SCHEMA_VERSION 12: `caption_runs`, `caption_cues`, `caption_review_events`
  - Sentence-aware segmentation (abbreviation handling, 2-line/42-char limits,
    text integrity invariant); proportional timing by display-char count
  - Immutable `caption_cues` rows; append-only `caption_review_events`
  - Exports: SRT, WebVTT, JSON — written atomically; SHA-256 hashes stored
  - `generate_captions()`: idempotent; failed-run rule (no auto-restart)
  - Exception-based review: `cue_rejected` events block run approval
  - CLI: `ace captions generate/runs/approve/reject/reject-cue/events`

- **Phase 7** — Visual Intelligence Engine (`src/app/scenes/`):
  - SCHEMA_VERSION 13: `scene_manifests`, `scene_manifest_scenes`,
    `scene_manifest_assets`, `scene_manifest_review_events`
  - Deterministic scene manifests: shot types, camera movements, transitions,
    visual objectives and rationale — all reproducible from same inputs
  - Full licensing metadata per asset: `license_status`, `attribution_required`,
    `commercial_safe`, `verification_status`, `usage_rights`; AI generation
    fields; evidence linkage via `claim_ids`/`evidence_ids`
  - Approve/reject/supersession workflow; scene-level rejection as training
    signal; immutable append-only review event history
  - `ApprovedSceneManifest` handoff: typed boundary for future rendering phase
  - `asset_strategy.py` as Phase 8 seam module for provider integration
  - CLI: `ace scenes plan/list/show/approve/reject/reject-scene/events/manifest`

- **Phase 12** — Media Operations Control Plane (`src/app/control_plane/`):
  - SCHEMA_VERSION 18: 19 `cp_` prefixed tables: `cp_workspaces`, `cp_channels`,
    `cp_platforms`, `cp_credential_profiles`, `cp_platform_accounts`,
    `cp_automation_policies`, `cp_strategy_profiles`, `cp_events`,
    `cp_event_processing`, `cp_workflows`, `cp_workflow_runs`, `cp_experiments`,
    `cp_experiment_variants`, `cp_experiment_assignments`, `cp_operation_executions`,
    `cp_cost_records`, `cp_budget_policies`, `cp_health_records`, `cp_provider_registry`
  - Permanent identity model: Workspace → Channel → PlatformAccount (never collapsed;
    distinct from Phase 3 intelligence `channels` table)
  - Credential profiles: `external_ref` vault pointer only — no OAuth tokens, no secrets
  - Automation levels: MANUAL / SUPERVISED / AUTONOMOUS; effective = most restrictive
    across hierarchy; defaults to MANUAL when no policy is set
  - Durable idempotent in-process event bus: `UNIQUE(event_id, handler_key)`;
    dead-letter after `MAX_DELIVERY_ATTEMPTS` (3); replay-safe
  - Structured workflow engine: trigger → conditions (8 operators, dot-notation fields)
    → actions (6 types); no eval, no arbitrary code execution
  - Experiments immutable once activated; `ExperimentAlreadyActiveError` on any
    mutation attempt against active/concluded/cancelled experiments
  - Budget enforcement: three-tier check (workspace/channel/account); warn / pause /
    block actions; `BudgetExceededError` on block
  - Actor-aware mutations throughout for future RBAC
  - CLI: `ace control workspace/channel/account/policy/experiment/events review-queue costs`
- **Phase 13** — Backend Integration & System Architecture (`src/app/application/`):
  - Application layer: typed command bus, 11-step dispatch pipeline
  - Canonical pipeline controller: research → script_generation → production_plan →
    narration → captions → visual_intelligence → rendering → publishing → analytics → learning
  - Prerequisite graph, review-gate stages, waiting_for_review parking
  - Scheduler: cron / interval / once / after_event with next_run_at tracking
  - Recovery engine: recover from failed/blocked/paused pipelines; safe event replay allowlist
  - Workspace health projection: budget + dead-letters + stuck-ops + active pipelines
  - Unified review queue: CP review items + pipeline waiting_for_review
  - Extension registry: typed, versioned, no eval/exec/dynamic imports
  - `ApplicationService` bounded facade for Phase 14 consumption
  - CONTRACT_VERSION "13.0.0"; semver compatibility checking
  - Cross-workspace isolation enforced on every command and query

- **Phase 15** — Deployment, Infrastructure & Production Operations:
  - SCHEMA_VERSION 20: `auth_users`, `auth_refresh_tokens`, `auth_workspace_roles`, `obj_storage_objects`
  - Auth: JWT access tokens (HS256, 15-min TTL, PyJWT); Argon2id password hashing (`pwdlib[argon2]`); SHA-256-hashed refresh tokens (raw token never stored or logged)
  - RBAC: 5 roles (owner/admin/operator/reviewer/analyst); deny-by-default `_PERMISSION_MATRIX`
  - Provider stage-class gating: Class A (local/deterministic) always allowed; Class B (live AI/TTS) requires provider + key; Class C (live publishing) requires Class B + `publishing_live_enabled`; unknown stages → C (fail-closed)
  - RQ worker layer: `JSONSerializer` enforced; no pickle; JSON-safe payloads only; workers reload state from PostgreSQL
  - Observability: structlog JSON + 27-key `_redact_sensitive` processor; isolated Prometheus `CollectorRegistry`; `/health` (liveness) + `/ready` (DB + Redis ping); security response headers (nosniff, DENY, CSP, `no-store`)
  - Docker: multi-stage Dockerfile (builder + runtime), non-root user `ace` (uid 1000), `HEALTHCHECK` against `/health`
  - Docker Compose: postgres 16, redis 7, migrate (one-shot), api, worker, scheduler; `ACE_SECRET_KEY` required; all live-provider flags default false
  - CI: GitHub Actions — backend (ruff + pytest), frontend (npm), Docker build (no push), migration check; CD boundary is intentionally manual
  - Backup: `scripts/backup.sh` (`pg_dump | gzip`, 14-backup retention); `docs/runbooks/backup-restore.md`
  - N+1 fix: `list_pipelines` now bulk-fetches all stages in a single `IN (…)` query
  - Integration tests: 13 end-to-end tests covering N+1 regression, auth→pipeline, provider boundary, sensitive-field redaction, refresh token storage invariant

- **Phase 16** — Multi-channel foundation & operational hardening:
  - Channel isolation enforced end-to-end: intelligence, learning, market, and experiment data for one channel is never visible to another (`src/app/intelligence/channel_bridge.py`, `src/app/intelligence/onboarding.py`)
  - Channel onboarding and identity bridge between the control-plane channel and the intelligence-layer channel record
  - Analytics auto-observer (`src/app/analytics/auto_observer.py`, `observation.py`): reconciles unobserved publications and dispatches due observation ticks on a schedule, independent of the API request path
  - Account/credential health recovery and observer supervision
  - Visual intelligence defect fixes carried forward from Phase 7
- **Phase 17** — Channel strategy, market refresh & frontend redesign:
  - Channel strategy profile: bootstrap vs. steady-state weighting between market intelligence and channel-specific evidence, with an explicit exploration/exploitation split (`src/app/intelligence/experiments/strategy_policy.py`)
  - Market refresh scheduling (`src/app/intelligence/market/refresh_service.py`) and semantic fit resolution for niche-adjacent opportunities
  - Autonomy readiness evaluation (`src/app/application/autonomy_readiness.py`)
  - Frontend redesign: primary navigation rebuilt around **Content / Analytics / Learn / Channel** (replacing the old Publishing-centric layout); legacy `/publishing`, `/channels`, `/learning` routes redirect rather than 404
- **Phase 18** — Closed-loop autonomy & visual quality:
  - 18A **Decision cycle** (`src/app/intelligence/autonomy/decision_cycle.py`): schedule-driven, queue-based experiment selection per channel; `custom_cron` cadence is accepted and stored but not yet computed (raises `NotImplementedError`, tested)
  - 18B **Production cycle** (`production_cycle.py`): autonomous rendering and production-plan drafting for a filled, decision-approved slot; publications stay private until the publishing cycle authorizes release
  - 18C **Publishing cycle** (`publishing_cycle.py`): autonomous upload and public-release, gated by `ACE_PUBLISHING_LIVE_ENABLED` and `ACE_RELEASE_PUBLIC_ENABLED` (see *Current status* above)
  - 18D **Closed-loop integration**: an end-to-end audit against the live database found and fixed nine cross-cutting defects (broken publication→experiment handoff, a channel-id namespace mismatch that left coverage permanently empty, a queue deadlock, an ignored schedule interval, double-counted analytics windows, among others) so the full loop actually closes — see `docs/phase-18d-closed-loop-contract.md` for the complete defect list and the lifecycle contract that resolves it
  - 18E **Visual quality intelligence** (`src/app/visuals/quality.py`, `qa.py`): automated quality scoring and QA for generated visuals, plus topic refinement and credential-recovery hardening — see `docs/phase-18e-visual-quality-contract.md`
  - Underlying market-intelligence and experimentation infrastructure built to support 16–18: exploration (cold-start, adjacent-concept expansion, niche selection, semantic clustering), experiment lineage/eligibility/planning, execution contracts with fidelity checking, and strategy briefs (`src/app/intelligence/market/`, `src/app/intelligence/experiments/`)

End-to-end workflow (manual/reviewed path, Phases 1–15): channel strategy →
discovery → scoring → topic promotion → source ingestion → claim extraction
→ script generation → human approval → production plan creation → human
review (approve/reject) → narration synthesis → narration review → caption
generation → caption review → scene manifest planning → scene review →
rendering → render review → publishing → analytics ingestion → optimization
recommendations → human recommendation review → control plane coordination
(workspaces, accounts, policies, experiments, costs).

Closed autonomous loop (Phase 18, when a channel's automation flags and
authorization gates are on): decision cycle selects the next experiment from
the channel's queue → production cycle renders and drafts a private
publication → publishing cycle uploads and, once authorized, releases it
publicly → the analytics observer ingests performance on schedule →
cross-publication learning folds the outcome into channel evidence → the
next decision cycle sees the updated evidence. Every transition in this loop
is the same reviewable data the manual path produces; autonomy changes who
initiates each step, not what gets recorded.


## Production deployment (Docker Compose)

```bash
# 1. Create a .env file with required secrets (never committed)
echo "ACE_SECRET_KEY=$(python -c 'import secrets; print(secrets.token_hex(32))')" > .env
echo "POSTGRES_PASSWORD=$(python -c 'import secrets; print(secrets.token_hex(16))')" >> .env

# 2. Start the stack (postgres → redis → migrate → api/worker/scheduler)
docker compose up -d

# 3. Verify readiness
curl http://localhost:8000/api/ready
# {"status":"ready","checks":{"db":true,"redis":true},...}

# 4. View structured JSON logs
docker compose logs -f api
```

All live-provider and autonomy flags (`ACE_TTS_LIVE_ENABLED`, `ACE_PUBLISHING_LIVE_ENABLED`, `ACE_RELEASE_PUBLIC_ENABLED`, and each channel's `decision_automation_enabled` / `production_automation_enabled`) default to `false`. No live API calls, uploads, or public releases happen until these are explicitly set. Production deployment is always a manual operator step; CI does not push to production.

See `docs/runbooks/backup-restore.md` for backup/restore procedures. See `DECISIONS.md` for rationale behind every infrastructure choice.

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
