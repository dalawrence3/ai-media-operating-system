# Project State Snapshot

**Date:** 2026-08-07
**Latest implemented milestone:** Phase 14 — Frontend Studio & Dashboard
**Next milestone:** Phase 15 — Deployment, Infrastructure & Production Operations

---

## Milestone history

| Milestone | Status | Tests at completion | Schema version |
|---|---|---|---|
| Phase 0 — Environment and CLI | ✅ Complete | — | 1 |
| Phase 1 — Core data model | ✅ Complete | — | 1 |
| Phase 2 — LLM abstraction | ✅ Complete | — | 2 |
| Phase 3 M3.1 — Channel strategy foundation | ✅ Complete | — | 3 |
| Phase 3 M3.2 — Discovery foundation | ✅ Complete | — | 4 |
| Phase 3 M3.3 — Scoring and confidence engine | ✅ Complete | — | 5 |
| Phase 3 M3.4 — Opportunity promotion | ✅ Complete | — | 6 |
| Phase 4 M4.1 — Source ingestion | ✅ Complete | — | 7 |
| Phase 4 M4.2 — Evidence and claim extraction | ✅ Complete | — | 8 |
| Phase 5 — Script generation | ✅ Complete | — | 9 |
| Phase 6 M6.1 — Production plan | ✅ Complete | 1155 | 10 |
| Phase 6 M6.2 — Narration generation | ✅ Complete | 1326 | 11 |
| Phase 6 M6.3A — Caption and timing artifacts | ✅ Complete | 1563 | 12 |
| Phase 6 M6.3B — Narration provider infrastructure | ✅ Complete | 1810 | 12 |
| Phase 6 M6.3C — Live ElevenLabs provider integration | ✅ Complete | 1889 | 12 |
| Phase 7 — Visual Intelligence & Scene Planning | ✅ Complete | 2019 | 13 |
| Phase 8 — Rendering Engine | ✅ Complete | 2154 | 14 |
| Phase 9 — Publishing & Orchestration Engine | ✅ Complete | 2358 | 15 |
| Phase 10 — Platform Analytics Engine | ✅ Complete | 2588 | 16 |
| Phase 11 — Learning & Optimization Engine | ✅ Complete | 2846 | 17 |
| Phase 12 — Media Operations Control Plane | ✅ Complete | 3101 | 18 |
| Phase 13 — Backend Integration & System Architecture | ✅ Complete | 3368 | 19 |
| Phase 14 — Frontend Studio & Dashboard | ✅ Complete | 3368 + 111 frontend | 19 |

---

## Current codebase state

### Schema version
`SCHEMA_VERSION = 19`

### Test count
**Backend:** 3368 passed, 1 skipped (ruff lint clean; skipped test is the always-skipped live smoke test)
**Frontend:** 111 passed (13 test files; Vitest + RTL + MSW; typecheck clean; lint clean; build clean)

### Packages implemented

| Package | Path | Status |
|---|---|---|
| Core (DB, config, logging, models) | `src/app/core/` | ✅ |
| LLM abstraction | `src/app/ai/` | ✅ |
| Channel strategy + discovery + scoring | `src/app/intelligence/` | ✅ |
| Source ingestion + claim extraction | `src/app/research/` | ✅ |
| Script generation | `src/app/content/` | ✅ |
| Production plan | `src/app/production/` | ✅ |
| Narration (TTS pipeline + provider infrastructure) | `src/app/narration/` | ✅ |
| ElevenLabs TTS adapter | `src/app/narration/providers/` | ✅ |
| Captions | `src/app/captions/` | ✅ |
| Visual Intelligence Engine (scene manifests) | `src/app/scenes/` | ✅ |
| Rendering Engine (render manifests, jobs, review) | `src/app/media/` | ✅ |
| Publishing & Orchestration Engine | `src/app/publishing/` | ✅ |
| Platform Analytics Engine | `src/app/analytics/` | ✅ |
| Learning & Optimization Engine | `src/app/learning/` | ✅ |
| Media Operations Control Plane | `src/app/control_plane/` | ✅ |
| Application Layer (command bus, pipeline controller, scheduler, recovery, health) | `src/app/application/` | ✅ |

### CLI subcommand groups

```
ace topics          — topic management
ace sources         — source ingestion, claim extraction
ace scripts         — script generation, approval
ace runs            — pipeline run history
ace ai              — AI prompt management
ace channels        — channel strategy, mode management
ace discover        — discovery runs
ace intelligence    — scoring, policies
ace production      — production plan
ace narration       — TTS narration pipeline
ace captions        — caption and timing artifacts
ace scenes          — Visual Intelligence: plan and review scene manifests
ace render          — Rendering Engine: compose, render, validate, and review MP4s
ace publish         — Publishing Engine: prepare, approve, start, schedule, retry, cancel, review
ace analytics       — Analytics Engine: ingest, normalize, aggregate, and review publication metrics
ace learn           — Learning Engine: analyze publications, list/show/accept/reject recommendations
ace version         — version info
ace doctor          — environment diagnostics
```

### Key config environment variables

| Variable | Default | Purpose |
|---|---|---|
| `ACE_DB_PATH` | `~/.local/share/ai-content-engine/content.db` | SQLite database path |
| `ACE_ARTIFACTS_PATH` | `~/.local/share/ai-content-engine/artifacts` | Artifact file root |
| `ACE_TTS_PROVIDER` | `fake` | TTS provider (`fake` or `elevenlabs`) |
| `ACE_TTS_MODEL` | `fake/FAKE` | TTS model identifier |
| `ACE_ELEVENLABS_API_KEY` | *(unset)* | ElevenLabs API key (never logged or stored in DB) |
| `ACE_TTS_LIVE_ENABLED` | `false` | Safety gate: must be `true` for live ElevenLabs calls |
| `ACE_PUBLISHING_LIVE_ENABLED` | `false` | Safety gate: must be `true` for live provider uploads |
| `YOUTUBE_CLIENT_SECRETS_PATH` | *(unset)* | Path to YouTube OAuth client secrets JSON (never stored in DB) |
| `YOUTUBE_CREDENTIALS_PATH` | *(unset)* | Path to YouTube OAuth token file (never stored in DB) |
| `ACE_LOG_LEVEL` | `WARNING` | Structured logging level |

---

## Phase 6 M6.3A completion details

### New files (M6.3A)

**Source:**
- `src/app/captions/__init__.py`
- `src/app/captions/constants.py`
- `src/app/captions/errors.py`
- `src/app/captions/hashing.py`
- `src/app/captions/models.py`
- `src/app/captions/segmentation.py`
- `src/app/captions/timing.py`
- `src/app/captions/validation.py`
- `src/app/captions/exporters.py`
- `src/app/captions/storage.py`
- `src/app/captions/repository.py`
- `src/app/captions/orchestrator.py`

**Tests:**
- `tests/test_caption_segmentation.py` (23 tests)
- `tests/test_caption_timing.py` (12 tests)
- `tests/test_caption_validation.py` (17 tests)
- `tests/test_caption_exporters.py` (27 tests)
- `tests/test_caption_storage.py` (23 tests)
- `tests/test_caption_repository.py` (35 tests)
- `tests/test_caption_orchestrator.py` (12 tests)
- `tests/test_caption_cli.py` (26 tests)

### Modified files (M6.3A)

- `src/app/core/database.py` — SCHEMA_VERSION 11 → 12; `_DDL_V12_CAPTIONS`; v11 migration branch; all earlier migration branches updated
- `src/app/narration/models.py` — Added `ApprovedNarrationSegment`, `ApprovedNarrationRun` frozen dataclasses
- `src/app/narration/repository.py` — Added `get_approved_narration_run_full()`
- `src/app/cli.py` — Added `captions_app` sub-app with 8 commands
- `tests/test_database.py` — Updated version assertions; added 8 v12 schema tests

### Key architectural invariants (M6.3A)

1. **Every human correction is a future training signal** — cue rows are immutable; corrections live in `caption_review_events`
2. **Every published artifact must remain reproducible** — SHA-256 hashes of SRT/VTT/JSON stored in DB; exports regenerable from cue rows
3. **Every optimization decision must be attributable** — all version constants bound to `input_hash`; any algorithm change forces a new run row

---

## Phase 6 M6.3B completion details

### New files (M6.3B)

**Source:**
- `src/app/narration/capabilities.py` — `ProviderFeatureFlags`, `ProviderCapabilities`
- `src/app/narration/metadata.py` — `ProviderMetadata` (reproducibility record)
- `src/app/narration/versioning.py` — `ProviderVersion`, `ProviderVersionRegistry`
- `src/app/narration/config.py` — `ProviderConfig`, `ProviderConfigRegistry`
- `src/app/narration/lifecycle.py` — `ProviderLifecycle` Protocol, `ProviderLifecycleState`
- `src/app/narration/registry.py` — `ProviderRegistry`, `get_default_provider_registry()`
- `src/app/narration/selection.py` — `ProviderSelector` Protocol, `DefaultProviderSelector`
- `src/app/narration/validation.py` — `ProviderValidator` Protocol, `DefaultProviderValidator`
- `src/app/narration/routing.py` — `ProviderRouter` Protocol, `DefaultProviderRouter`
- `src/app/narration/accounting.py` — `UsageRecord`, `UsageAccumulator`
- `src/app/narration/benchmark.py` — `ProviderBenchmark` Protocol, `InMemoryProviderBenchmark`
- `src/app/narration/health.py` — `ProviderHealthCheck` Protocol, `InMemoryProviderHealthCheck`
- `src/app/narration/failover.py` — `FailoverPolicy` Protocol, `NoFailoverPolicy`, `ProviderFailoverChain`
- `src/app/narration/cache.py` — `ProviderResponseCache` Protocol, `NoOpResponseCache`, `InMemoryResponseCache`
- `src/app/narration/factory.py` — `ProviderFactory`, `ProviderLoader`, `DefaultProviderFactory`, `DefaultProviderLoader`, `RegisteringProviderLoader`

**Tests:**
- `tests/test_narration_capabilities.py` (39 tests)
- `tests/test_narration_metadata.py` (19 tests)
- `tests/test_narration_provider_config.py` (19 tests)
- `tests/test_narration_registry.py` (17 tests)
- `tests/test_narration_selection.py` (16 tests)
- `tests/test_narration_provider_validation.py` (17 tests)
- `tests/test_narration_lifecycle.py` (14 tests)
- `tests/test_narration_factory.py` (14 tests)
- `tests/test_narration_routing.py` (13 tests)
- `tests/test_narration_accounting.py` (16 tests)
- `tests/test_narration_benchmark.py` (19 tests)
- `tests/test_narration_health.py` (20 tests)
- `tests/test_narration_failover.py` (12 tests)
- `tests/test_narration_cache.py` (18 tests)
- `tests/test_narration_versioning.py` (18 tests)

### Modified files (M6.3B)

- `src/app/narration/errors.py` — 7 new error types under `ProviderInfrastructureError`
- `src/app/narration/constants.py` — `PROVIDER_FEATURE_*` string constants (10), `PROVIDER_LANGUAGE_WILDCARD`, `PROVIDER_INFRASTRUCTURE_VERSION`
- `src/app/narration/fake.py` — Implements `ProviderLifecycle`; adds `FAKE_CAPABILITIES`, `FAKE_FEATURE_FLAGS`, `FAKE_METADATA`, `FAKE_PROVIDER_VERSION`, `FAKE_PROVIDER_CONFIG` module-level constants; synthesis behaviour unchanged
- `src/app/narration/pricing.py` — Adds `ProviderPricingPolicy` Protocol (satisfied by `TTSPricingRegistry`)

### Key architectural invariants (M6.3B)

1. **No live provider SDK** — FakeTTSProvider is the only concrete implementation
2. **Every AI output must remain reproducible** — `ProviderMetadata.to_reproducibility_dict()` captures provider_name, model_id, version, SDK identity, and capabilities
3. **Lifecycle is observable, not gating** — `ProviderLifecycle.initialize()` transitions state but does not block synthesis (backward compatible)
4. **Orchestrator unchanged** — `narrate_plan()` and `regenerate_segment()` are untouched; the new infrastructure is additive
5. **Schema unchanged** — still SCHEMA_VERSION 12

---

## Phase 6 M6.3C completion details

### New files (M6.3C)

**Source:**
- `src/app/narration/providers/__init__.py` — package init
- `src/app/narration/providers/elevenlabs.py` — ElevenLabsTTSProvider (TTSProvider + ProviderLifecycle)

**Tests:**
- `tests/test_narration_elevenlabs.py` (79 tests, all mocked — no live calls)
- `tests/test_narration_elevenlabs_smoke.py` (1 test, always skipped in CI)

### Modified files (M6.3C)

- `pyproject.toml` — Added `elevenlabs>=2.61.0,<3.0` dependency (floor matches verified SDK 2.61.0)
- `src/app/narration/constants.py` — 4 new `PROVIDER_FEATURE_*` constants; `PROVIDER_FEATURE_NAMES` extended to 14 entries
- `src/app/narration/capabilities.py` — 4 new `ProviderFeatureFlags` boolean fields
- `src/app/narration/errors.py` — `ProviderCredentialError`, `ProviderRateLimitError`
- `src/app/narration/pricing.py` — `eleven_multilingual_v2=$0.10/1K`, `eleven_flash_v2_5=$0.05/1K`
- `src/app/core/config.py` — `elevenlabs_api_key`, `tts_live_enabled` config fields
- `src/app/narration/factory.py` — `DefaultProviderFactory` and `DefaultProviderLoader` now handle `"elevenlabs"`
- `src/app/narration/registry.py` — updated docstring; default registry remains fake-only (live providers require explicit registration)
- `src/app/cli.py` — `ace narration smoke-test` command (manually opt-in only)
- `.env.example` — `ACE_ELEVENLABS_API_KEY` and `ACE_TTS_LIVE_ENABLED` entries
- `tests/test_narration_factory.py` — Updated `test_factory_unknown_provider_raises` to use "google"; added `test_factory_creates_elevenlabs_provider`

### Architectural decisions (M6.3C)

1. **ElevenLabs only** — Only `eleven_multilingual_v2` (default) and `eleven_flash_v2_5` registered. No Google, Azure, Polly, Cartesia, or OpenAI TTS.
2. **Capability-driven** — Downstream code never checks `if provider == "elevenlabs"`. All behaviour is driven by `ProviderCapabilities` and `ProviderFeatureFlags`.
3. **No audio normalisation** — Loudness is measured as RMS amplitude level in dBFS (not LUFS, not EBU R128) and warned if outside the advisory range. Normalisation deferred to Phase 8 rendering.
4. **No schema changes** — SCHEMA_VERSION remains 12. Alignment data stored in `provider_metadata_json` (existing `TTSResponse` field).
5. **No caption pipeline changes** — `timing_source='estimated'` continues unchanged.
6. **Credentials never logged** — `ACE_ELEVENLABS_API_KEY` never appears in DB, logs, reproducibility dicts, or `provider_metadata_json`.
7. **Safety gate** — `ACE_TTS_LIVE_ENABLED` must be `true` AND `ACE_ELEVENLABS_API_KEY` must be set before any live call. CI always uses mock injection.
8. **Max 3 retries** — Bounded exponential backoff; retry only on 429/5xx; `ProviderRateLimitError` raised when 429 exhausts all attempts.
9. **Default registry unchanged** — `get_default_provider_registry()` still returns fake only. ElevenLabs is registered explicitly when the live provider is requested.
10. **Test isolation via injected client** — `ElevenLabsTTSProvider(_sdk_client=mock)` bypasses credential guard entirely.

### Key M6.3C module: `src/app/narration/providers/elevenlabs.py`

- `ElevenLabsTTSProvider` — implements `TTSProvider` and `ProviderLifecycle`
- `ELEVENLABS_CAPABILITIES` — `ProviderCapabilities` with 37 supported languages and 4 output formats
- `ELEVENLABS_FEATURE_FLAGS` — all 14 flags; supports_alignment/seed/voice_cloning/pronunciation_dictionary=True
- `ELEVENLABS_METADATA` — `ProviderMetadata` for reproducibility
- `ELEVENLABS_PROVIDER_VERSION` — bound to schema and algorithm versions
- `ELEVENLABS_PROVIDER_CONFIG` — default config (eleven_multilingual_v2, wav, 22050 Hz)
- Uses `/v1/text-to-speech/{voice_id}/with-timestamps` endpoint for character-level alignment
- `_measure_rms_dbfs()` — stdlib-only RMS amplitude measurement in dBFS (not LUFS; no numpy)
- `_check_duration_deviation()` — word-count heuristic; warns if deviation > 50%

---

## Phase 7 completion details

### New files (Phase 7)

**Source — Visual Intelligence Engine (`src/app/scenes/`):**
- `src/app/scenes/__init__.py` — package docstring exposing full VI Engine scope
- `src/app/scenes/constants.py` — shot types, camera movements, transitions, asset categories, license statuses, priorities, section→visual mappings
- `src/app/scenes/errors.py` — domain exceptions: `SceneManifestError`, `NoApprovedCaptionRunError`, `ManifestNotFoundError`, `IllegalManifestTransitionError`, `ManifestAlreadyExistsError`, `ManifestBuildError`
- `src/app/scenes/models.py` — `PlannedAssetDraft`, `PlannedSceneDraft`, `SceneManifestDraft` (mutable dataclasses); `SceneManifest`, `SceneManifestScene`, `SceneManifestAsset`, `SceneManifestReviewEvent` (frozen Pydantic); `ApprovedSceneManifest`, `ApprovedSceneScene` (handoff objects)
- `src/app/scenes/hashing.py` — `ManifestHashInput`, `compute_manifest_input_hash()` (SHA-256, order-sensitive)
- `src/app/scenes/asset_strategy.py` — `plan_assets()`: deterministic 1–3 asset recommendations per scene with licensing metadata and evidence linkage
- `src/app/scenes/planner.py` — `build_scene_manifest()`: orchestrates shot type, camera grammar, transitions, timing, visual objectives/rationale, confidence
- `src/app/scenes/repository.py` — full CRUD: create, get_or_create (idempotent), approve (with supersession), reject, scene-level rejection, review events, full handoff via `get_approved_scene_manifest_full()`
- `src/app/scenes/cli.py` — `scenes_app`: `plan`, `list`, `show`, `approve`, `reject`, `reject-scene`, `events`, `manifest` commands

**Tests:**
- `tests/test_scene_constants.py` (15 tests)
- `tests/test_scene_hashing.py` (9 tests)
- `tests/test_scene_models.py` (7 tests)
- `tests/test_scene_repository.py` (30 tests)
- `tests/test_scene_planner.py` (45 tests)
- `tests/test_scene_cli.py` (24 tests)

### Modified files (Phase 7)

- `src/app/core/database.py` — SCHEMA_VERSION 12 → 13; `_DDL_V13_SCENES` (4 tables: `scene_manifests`, `scene_manifest_scenes`, `scene_manifest_assets`, `scene_manifest_review_events`); v12 migration branch; all earlier migration branches updated
- `src/app/cli.py` — registered `scenes_app` sub-app
- `tests/test_database.py` — updated `SCHEMA_VERSION == 12` → `== 13` in 3 assertions; renamed `test_schema_version_is_12` → `test_schema_version_is_13`

### Key architectural invariants (Phase 7)

1. **Every human correction is a future training signal** — all approve/reject/scene-reject events stored immutably in `scene_manifest_review_events`; reason codes, severity, expected corrections, and actor preserved
2. **Every optimization decision is attributable** — `MANIFEST_SCHEMA_VERSION`, `PLANNER_VERSION` bound to `input_hash`; any algorithm change forces a new manifest row
3. **Every external provider is replaceable** — `provider` and `source_url` fields on every asset; `PlannedAssetDraft` decoupled from any provider implementation
4. **Every visual recommendation has a measurable reason** — `visual_rationale` records the specific section-type→shot-type→camera-movement mapping; `confidence` score is deterministically computed
5. **Every scene is independently reviewable** — scene-level rejection (`record_scene_rejection`) is append-only and does not change manifest status; scenes are FK-linked to `production_segments` for future targeted regeneration (independent scene replacement is a Phase 8+ capability)
6. **Canonical data is immutable** — `SceneManifest`, `SceneManifestScene`, `SceneManifestAsset`, `SceneManifestReviewEvent` are frozen Pydantic models
7. **Derived artifacts remain reproducible** — same `(caption_run_id, narration_run_id, plan_id, planner_version)` → same `input_hash` → idempotent via `get_or_create_scene_manifest`
8. **Provider independence** — asset planning (`asset_strategy.py`) is a separate module from scene orchestration (`planner.py`); providers slot in Phase 8 without touching planner logic

### Phase 7 — Architecture: Visual Intelligence Engine

The `src/app/scenes/` package is the root of the Visual Intelligence Engine. Current modules cover the scene manifest foundation. The package naturally supports future extension without rewrites:

| Future module | Natural home | Current seam |
|---|---|---|
| Stock footage providers | `src/app/scenes/providers/` | `provider` field on `PlannedAssetDraft` |
| AI image generation | `src/app/scenes/providers/` | `ai_generation_*` fields on `PlannedAssetDraft` |
| Licensing verification | `src/app/scenes/licensing.py` | `license_status`, `verification_status` fields |
| Retention optimization | `src/app/scenes/optimizer.py` | `confidence` field per scene |
| Camera grammar | `src/app/scenes/camera_grammar.py` | `SECTION_CAMERA_MAP` in constants |
| Transition grammar | `src/app/scenes/transition_grammar.py` | `TRANSITION_*` constants |
| Evidence visualization | `src/app/scenes/evidence_viz.py` | `claim_ids`, `evidence_ids` on every scene/asset |
| Analytics learning | `src/app/scenes/analytics.py` | `scene_manifest_review_events` table |
| Visual storytelling | `src/app/scenes/visual_storytelling.py` | `_visual_objective`, `_visual_rationale` helpers |
| Asset strategy enrichment | `src/app/scenes/asset_strategy.py` | Already a separate module |

---

---

## Phase 9 completion details

### New files (Phase 9)

**Source — Publishing & Orchestration Engine (`src/app/publishing/`):**
- `src/app/publishing/__init__.py`
- `src/app/publishing/constants.py` — plan/job/pub statuses, transition maps, MAX_RETRY_ATTEMPTS=3, 14 event types
- `src/app/publishing/errors.py` — PublishingError hierarchy (23 error classes including LivePublishingNotEnabledError)
- `src/app/publishing/hashing.py` — `PublishingHashInput`, `compute_publishing_input_hash()` (SHA-256)
- `src/app/publishing/models.py` — `PublishingPlan`, `PublishingJob`, `Publication`, `PublishingReviewEvent` (frozen Pydantic); `PublishingMetadataDraft`, `PublishingScheduleDraft` (mutable dataclasses)
- `src/app/publishing/protocol.py` — `PublishingProvider` `@runtime_checkable` Protocol, `UploadPackage`, `UploadResult`, `PublishResult`, `ProviderHealthReport`, `ProviderCapabilities`
- `src/app/publishing/state_machine.py` — `check_plan/job/publication_transition()` enforcement
- `src/app/publishing/metadata.py` — `build_metadata_draft()` helper
- `src/app/publishing/scheduler.py` — `validate_schedule()`, `is_scheduled_time_due()`
- `src/app/publishing/validation.py` — `validate_approved_render_for_publishing()`, `validate_publishing_metadata()`
- `src/app/publishing/repository.py` — full CRUD for publishing_plans, publishing_jobs, publications, publishing_review_events; approve/reject/supersede functions
- `src/app/publishing/orchestrator.py` — `prepare_publishing_plan()`, `start_publishing_job()`, `retry_publishing_job()`, `cancel_publishing_job()`, `update_plan_schedule()`
- `src/app/publishing/providers/__init__.py`
- `src/app/publishing/providers/fake.py` — `FakePublishingProvider` (zero network, deterministic, safe default)
- `src/app/publishing/providers/youtube.py` — `YouTubePublishingProvider` + `FakeYouTubeAPIClient` (injectable client boundary)
- `src/app/publishing/cli.py` — `publish_app` with 11 subcommands

**Tests:**
- `tests/test_publish_hashing.py` (10 tests)
- `tests/test_publish_state_machine.py` (16 tests)
- `tests/test_publish_validation.py` (16 tests)
- `tests/test_publish_models.py` (12 tests)
- `tests/test_publish_repository.py` (40 tests)
- `tests/test_publish_orchestrator.py` (25 tests)
- `tests/test_publish_youtube.py` (15 tests)
- `tests/test_publish_cli.py` (27 tests)

### Modified files (Phase 9)

- `src/app/core/database.py` — SCHEMA_VERSION 14→15; `_DDL_V15_PUBLISHING` (4 tables); v14 migration branch; all earlier branches updated
- `src/app/core/config.py` — `publishing_live_enabled` field (`ACE_PUBLISHING_LIVE_ENABLED`, default False)
- `src/app/cli.py` — registered `publish_app` (`ace publish`)
- `tests/test_database.py` — v14→v15 assertions
- `.env.example` — `YOUTUBE_CLIENT_SECRETS_PATH`, `YOUTUBE_CREDENTIALS_PATH`, `ACE_PUBLISHING_LIVE_ENABLED`

### Key architectural invariants (Phase 9)

1. **Three distinct lifecycles** — Publishing Plan, Publishing Job, and Publication have separate state machines and separate status enums. Supersession is field-based (`superseded_at`, `superseded_by_id`), not a status change.
2. **Live publishing requires five explicit gates** — `ACE_PUBLISHING_LIVE_ENABLED=true` + `--execute` CLI flag + approved Publishing Plan + approved Render with verified output hash + explicit non-fake provider selection. No single misconfiguration triggers a live upload.
3. **Publication created only after upload succeeds** — `provider_video_id TEXT` (nullable). A Publication row is never inserted as a placeholder before a real provider resource exists.
4. **Retry creates a distinct new job** — Prior failed jobs remain `failed`. `retry_publishing_job()` does not mutate the old job's status. `attempt_number` increments; `MAX_RETRY_ATTEMPTS=3` enforced.
5. **Append-only review events** — `publishing_review_events` rows are never updated in place (immutable training signals).
6. **No credential values in SQLite or logs** — OAuth client secrets, refresh tokens, and access tokens are never stored. Only file-path env-var names appear in code.
7. **FakePublishingProvider is the safe default** — All automated tests use `FakePublishingProvider` or `FakeYouTubeAPIClient`. Zero live network calls in any automated test.
8. **Scheduling records intent only** — `schedule_type` and `scheduled_at` store the operator's scheduling intent. No background daemon or unattended scheduler exists. The executor (future Phase) must call `is_scheduled_time_due()` to decide whether to start a job.

### YouTube adapter — actual verified scope

`YouTubePublishingProvider` implements the `PublishingProvider` Protocol with an injectable client boundary:

- **Implemented and tested:** The adapter class, `FakeYouTubeAPIClient` (zero network), `prepare_package()`, `upload()` (via fake client), `publish()` (via fake client), `health()`, `capabilities()`.
- **Automated tests:** All 15 `test_publish_youtube.py` tests use `FakeYouTubeAPIClient`. No real Google API calls are made in any test.
- **No Google SDK installed:** `google-api-python-client` and `google-auth` are not in `pyproject.toml` and are not imported. The module docstring notes SDK installation as a pending operator-approved step.
- **No verified OAuth flow:** No real OAuth 2.0 authorization flow has been executed. Credential paths are read from `YOUTUBE_CLIENT_SECRETS_PATH` / `YOUTUBE_CREDENTIALS_PATH` env vars — the files themselves are not present in this repository.
- **No verified live upload:** No video has been uploaded to YouTube. The real upload path exists in code but has not been tested against the live API.
- **Real Google client is a future integration step:** The actual `google-api-python-client` client is not instantiated until an operator explicitly installs the SDK, provides credentials, sets `ACE_PUBLISHING_LIVE_ENABLED=true`, selects `--provider=youtube`, and passes `--execute`. This is intentional.

### Not implemented in Phase 9

- Platform Analytics ingestion (Phase 11+)
- Analytics-driven optimization (Phase 11+)
- Background scheduler daemon or unattended publish runner (future phase)
- Real OAuth 2.0 flow verification
- Live YouTube upload verification
- TikTok, Instagram, or any non-YouTube provider

---

## Phase 10 completion details

### New files (Phase 10)

**Source — Platform Analytics Engine (`src/app/analytics/`):**
- `src/app/analytics/__init__.py`
- `src/app/analytics/constants.py` — 12 canonical metrics, METRIC_KIND, METRIC_AGGREGATION_OP (AGG_SUM / AGG_LAST only), MONETARY_METRICS, CALC_METHOD_SUM / CALC_METHOD_LATEST_OBSERVATION, calc_method_for(), PUBLICATION_ELIGIBLE_STATUSES
- `src/app/analytics/errors.py` — PublicationIneligibleError, MissingCurrencyError, CurrencyMismatchError, DuplicateSnapshotError, and 6 others
- `src/app/analytics/hashing.py` — `AnalyticsHashInput`, `compute_analytics_input_hash()` (SHA-256, deterministic)
- `src/app/analytics/models.py` — `AnalyticsSnapshot`, `AnalyticsMetric`, `AnalyticsAggregate`, `AnalyticsReviewEvent` (frozen Pydantic); `AnalyticsHandoff` (Phase 11 bundle); `AnalyticsIngestDraft`, `ReviewEventDraft` (mutable dataclasses)
- `src/app/analytics/normalization.py` — `validate_canonical_metrics()`
- `src/app/analytics/validation.py` — `validate_ingest_draft()`, `validate_review_severity()`, `validate_review_notes()`
- `src/app/analytics/protocol.py` — `AnalyticsProvider` `@runtime_checkable` Protocol, `ProviderMetrics`
- `src/app/analytics/metrics.py` — period key helpers: `daily_key()`, `weekly_key()`, `monthly_key()`, `LIFETIME_KEY`, `parse_iso_datetime()`
- `src/app/analytics/aggregation.py` — `aggregate_publication()`, `aggregate_all_periods()`, `_ReductionResult`, `_reduce_metrics()`, `_resolve_currency()`, `_build_snapshot_currency_map()`
- `src/app/analytics/repository.py` — `create_snapshot()`, `upsert_aggregate()`, `create_metric()`, `create_review_event()`, list/get functions; all append-only
- `src/app/analytics/orchestrator.py` — `AnalyticsOrchestrator` (ingest, aggregate, record_review, list_*); `_validate_publication_eligibility()` (7-check chain, no bypass)
- `src/app/analytics/cli.py` — `analytics_app` with 8 subcommands
- `src/app/analytics/providers/__init__.py`
- `src/app/analytics/providers/fake.py` — `FakeAnalyticsProvider` (deterministic, zero-network, default for all tests)
- `src/app/analytics/providers/youtube.py` — `YouTubeAnalyticsProvider` (fixture-tested normalization boundary, not live)

**Tests (230 analytics-specific tests):**
- `tests/test_analytics_constants.py` (18 tests)
- `tests/test_analytics_hashing.py` (12 tests)
- `tests/test_analytics_normalization.py` (12 tests)
- `tests/test_analytics_validation.py` (14 tests)
- `tests/test_analytics_metrics.py` (12 tests)
- `tests/test_analytics_models.py` (20 tests)
- `tests/test_analytics_repository.py` (28 tests)
- `tests/test_analytics_aggregation.py` (18 tests)
- `tests/test_analytics_orchestrator.py` (36 tests)
- `tests/test_analytics_providers.py` (20 tests)
- `tests/test_analytics_cli.py` (18 tests)
- `tests/test_analytics_migration.py` (8 tests)

### Modified files (Phase 10)

- `src/app/core/database.py` — SCHEMA_VERSION 15→16; `_DDL_V16_ANALYTICS` (4 tables: `analytics_snapshots`, `analytics_metrics`, `analytics_aggregates`, `analytics_review_events`); v16 DDL includes `is_period_complete`, `currency_code` on snapshots; `calculation_method`, `currency_code`, `source_snapshot_ids_json` on aggregates
- `src/app/cli.py` — registered `analytics_app` (`ace analytics`)
- `tests/test_database.py` — v15→v16 assertions

### Key architectural invariants (Phase 10)

1. **Append-only, no UPDATE or DELETE** — Every analytics table is strictly append-only. Corrections create new snapshot rows; old rows remain for audit.
2. **Deterministic SHA-256 hashing** — `AnalyticsHashInput` fields → `input_hash`; identical replay returns the existing snapshot without a new insert.
3. **No production bypass** — `_validate_publication_eligibility()` performs 7 checks (publication exists, status eligible, provider_video_id set, provider matches, render_manifest exists, approved, not superseded). No flag can skip this check.
4. **Metric semantics clearly labeled** — `calculation_method` field on every aggregate: `'sum'` (additive/monetary) vs `'latest_observation'` (gauge/ratio). Phase 11 must not treat a `latest_observation` as a recomputed aggregate.
5. **Deduplication for additive metrics** — For `AGG_SUM` metrics with explicit `period_start/end`, the most recently ingested snapshot per `(period_start, period_end)` base period wins. No double-counting on re-ingest.
6. **Gauge and ratio use AGG_LAST** — `likes`, `dislikes`, `comments`, `ctr`, `average_view_duration` all use AGG_LAST (latest provider observation). These are not summed.
7. **Currency contract** — `currency_code TEXT` (ISO 4217, 3-char) on snapshots and aggregates. Required when monetary metrics (`revenue_estimate`) are present; `MissingCurrencyError` raised at ingest time if absent. `CurrencyMismatchError` raised if aggregation spans mixed currencies.
8. **Source lineage** — `source_snapshot_ids_json TEXT` on every aggregate row; enables reproducible lineage tracing to source snapshots.
9. **Period completeness** — `is_period_complete INTEGER` on snapshots: 0=provisional (default), 1=provider-confirmed final.
10. **YouTube adapter is fixture-tested only** — `YouTubeAnalyticsProvider` is an injectable normalization boundary tested with deterministic fixtures. Not verified against the live YouTube Analytics API.
11. **Provider isolation** — YouTube API field names (camelCase) exist only inside `providers/youtube.py`. The rest of the codebase uses canonical snake_case metric names.
12. **`AnalyticsHandoff` for Phase 11** — Frozen Pydantic bundle carrying the full attribution chain, aggregates with calculation methods, source snapshot IDs, and currency context. Phase 11 reads; never writes back to analytics tables.
13. **Schema version stays at 16** — v16 DDL revised in-place (no version bump) to add `is_period_complete`, `currency_code`, `calculation_method`, and `source_snapshot_ids_json`.

---

## Phase 11 completion details

### New files (Phase 11)

**Source — Learning & Optimization Engine (`src/app/learning/`):**
- `src/app/learning/__init__.py` — package docstring listing constraints and public interface
- `src/app/learning/constants.py` — domain/subsystem taxonomy, confidence thresholds (LOW=0.4, HIGH=0.7), review statuses, analytics metric thresholds, `confidence_label()`; `STRENGTH_EXPLORATORY`/`STRENGTH_ACTIONABLE` with `MIN_UNIQUE_SNAPSHOTS_ACTIONABLE=2` and `MIN_CONFIDENCE_ACTIONABLE=0.4`; generator identifier constants (`GENERATOR_CTR`, etc.); `EVIDENCE_CLASSIFICATIONS` frozenset; `RUN_STATUS_PARTIAL`
- `src/app/learning/errors.py` — `LearningError` hierarchy: `LearningRunNotFoundError`, `RecommendationNotFoundError`, `InvalidRecommendationDomainError`, `ReviewerRequiredError`, `NotesRequiredError`, `RecommendationNotReviewableError`, `InsufficientAnalyticsDataError`, and others
- `src/app/learning/models.py` — `EvidenceItem` dataclass with `to_dict()`/`from_dict()`; `GeneratorResult`, `AllGeneratorResults` dataclasses; `RecommendationDraft` (with `evidence_classification` and `recommendation_strength` fields), `ReviewEventDraft` (mutable dataclasses); `LearningRun`, `OptimizationRecommendation` (with `evidence_classification` and `recommendation_strength`), `RecommendationReviewEvent`, `LearningRunGeneratorResult` (frozen Pydantic with `from_row()`); `ReviewedRecommendationItem`, `ReviewedOptimizationHandoff` (frozen Phase 12 handoff models)
- `src/app/learning/hashing.py` — `LearningRunHashInput`, `RecommendationHashInput`, `ReviewEventHashInput`; `compute_learning_run_hash()`, `compute_recommendation_hash()`, `compute_review_event_hash()` (all SHA-256, sorted snapshot IDs for determinism)
- `src/app/learning/scoring.py` — `_volume_score()` (log2 scale), `_effect_score()` (gap/threshold ratio), `_consistency_score()` (period diversity), `compute_confidence()`, `compute_confidence_from_evidence()`, `meets_minimum_confidence()`
- `src/app/learning/attribution.py` — `resolve_attribution()`: maps each domain to upstream `AnalyticsHandoff` FK field (topics, research, scripts, narration, captions, scenes, media, publishing, analytics)
- `src/app/learning/recommendations.py` — six deterministic generators: `generate_ctr_recommendations`, `generate_retention_recommendations`, `generate_engagement_recommendations`, `generate_watch_time_recommendations`, `generate_subscriber_recommendations`, `generate_shares_recommendations`; `generate_all_recommendations()` returns `AllGeneratorResults` (drafts + per-generator `GeneratorResult` records), catches per-generator exceptions; `_classify_evidence()` (always `observational`; `experiment_id` alone ≠ `controlled_experiment`); `_classify_strength()` (deterministic `exploratory`/`actionable` thresholds)
- `src/app/learning/validation.py` — `validate_domain`, `validate_subsystem`, `validate_confidence_level`, `validate_confidence_score`, `validate_recommendation_status`, `validate_review_event_type`, `validate_review_eligible`, `validate_review_event`
- `src/app/learning/repository.py` — append-only CRUD: `create_learning_run`, `complete_learning_run`, `partial_learning_run`, `fail_learning_run`, `get_learning_run`, `list_learning_runs`; `create_recommendation`, `get_recommendation`, `list_recommendations`, `find_active_recommendation_by_key`, `supersede_recommendation`, `update_recommendation_status`; `create_review_event`, `list_review_events`; `create_generator_result`, `list_generator_results`
- `src/app/learning/orchestrator.py` — `_build_handoff_from_db()` (assembles AnalyticsHandoff from DB); `analyze_publication()` (full pipeline → run_id; persists generator results; handles supersession; determines partial/completed/failed status); `accept_recommendation()`, `reject_recommendation()`; `build_reviewed_handoff()` (assembles `ReviewedOptimizationHandoff` for Phase 12); convenience wrappers `get_run`, `list_runs`, `list_recs`, `list_events`
- `src/app/learning/cli.py` — `learn_app` Typer app: `analyze`, `list`, `show`, `accept`, `reject`, `events`, `runs`

**Tests (258 new tests; 2846 total):**
- `tests/test_learning_constants.py` (12 tests)
- `tests/test_learning_hashing.py` (14 tests)
- `tests/test_learning_models.py` (18 tests)
- `tests/test_learning_scoring.py` (37 tests — includes 13 pathological-input tests)
- `tests/test_learning_attribution.py` (12 tests)
- `tests/test_learning_validation.py` (20 tests)
- `tests/test_learning_repository.py` (26 tests)
- `tests/test_learning_migration.py` (10 tests)
- `tests/test_learning_recommendations.py` (29 tests)
- `tests/test_learning_orchestrator.py` (18 tests)
- `tests/test_learning_cli.py` (20 tests)
- `tests/test_learning_contracts.py` (31 tests — causal-language enforcement, evidence/strength contracts, partial run semantics, supersession, ReviewedOptimizationHandoff)

### Modified files (Phase 11)

- `src/app/core/database.py` — SCHEMA_VERSION 16→17; `_DDL_V17_LEARNING` (4 tables: `learning_runs`, `optimization_recommendations`, `recommendation_review_events`, `learning_run_generator_results`); `learning_runs.status` CHECK includes `'partial'`; `optimization_recommendations` includes `evidence_classification` and `recommendation_strength` columns; v16 migration branch; all earlier branches updated
- `src/app/cli.py` — registered `learn_app` (`ace learn`)
- `tests/test_database.py` — version assertions updated 16→17
- `tests/test_analytics_migration.py` — version assertions updated 16→17

### Key architectural invariants (Phase 11)

1. **Observe, attribute, measure, explain, recommend — never act** — the engine never modifies prompts, parameters, or any production data. Recommendations are rows in `optimization_recommendations` until a human acts. Accepting a recommendation does not mutate any upstream engine table.
2. **No ML, no embeddings, no network, no autonomous optimization** — all logic is deterministic arithmetic over aggregate DB rows. No external calls, no fine-tuning, no reinforcement learning, no embeddings of any kind.
3. **Every recommendation is traceable** — `evidence_json` stores the exact `(metric_name, observed_value, comparison_value, period_type, period_key, snapshot_ids)` chain that produced it. SHA-256 `input_hash` (including `evidence_classification` and `recommendation_strength`) pins the recommendation to its inputs.
4. **Append-only, never updated or deleted** — supersession stamps `superseded_at` and `superseded_by_id`; old recommendations remain for audit. Supersession is not rejection. Review events are immutable.
5. **Confidence is a heuristic signal strength, not a statistical confidence interval** — three-factor score: volume (log2 of deduplicated snapshot count), effect (gap/threshold ratio), consistency (period diversity). All three capped at 1.0 and averaged equally. A single observation period yields zero consistency contribution. Duplicate snapshot IDs across evidence items are deduplicated before computing volume.
6. **Recommendation strength is deterministic** — `exploratory` (insufficient evidence) vs `actionable` (confidence ≥ 0.4 AND ≥ 2 unique snapshot IDs). Thresholds are named constants included in the hash payload.
7. **Evidence classification is always observational** — `_classify_evidence()` always returns `observational` in Phase 11. `experiment_id` alone does NOT qualify a recommendation for `controlled_experiment` classification.
8. **Observational recommendations must not make causal claims** — recommendation text must use associative language. Prohibited: causes, increases, decreases, improves, reduces, leads to, results in, because of.
9. **Human review gates all status changes** — `pending → accepted/rejected` requires a named reviewer. `pending → superseded` is system-only. No automated accept/reject exists. Accept/reject events are human review signals for recommendations themselves, not for upstream production artifacts.
10. **Generator failures are visible and attributable** — each generator records a `GeneratorResult` in `learning_run_generator_results`. A failed generator does not suppress recommendations from other generators and does not supersede prior active recommendations for its domain.
11. **Partial run status** — if some generators succeed and some fail, the run status is `partial` (distinct from `completed` and `failed`). A run where all generators fail is `failed`.
12. **Supersession is not rejection** — supersession is initiated by the system when new evidence produces a different recommendation for the same key. Human operators may only accept or reject. Superseded recommendations remain queryable.
13. **Attribution through AnalyticsHandoff** — every recommendation carries `affected_subsystem`, `subsystem_entity_type`, and `subsystem_entity_id` derived from the upstream AnalyticsHandoff FK fields. Phase 12+ can use these to target regeneration.
14. **Phase 11 consumes only AnalyticsHandoff** — upstream human-review signals (script approval/rejection, narration review events, scene manifest decisions, render approval, publishing review) are not ingested by Phase 11. This integration is deferred to a future phase.
15. **ReviewedOptimizationHandoff is the frozen Phase 12 boundary** — a frozen Pydantic model bundling accepted/rejected/pending recommendations with review histories, generator results, and version provenance. Phase 12 must consume this handoff and must not automatically apply recommendations.

---

## Phase 14 completion details

### New directory: `frontend/`

React 19 + TypeScript + Vite 8 Single-Page Application. All backend interaction goes through a centralized typed API client (`src/api/client.ts`) — no raw `fetch()` calls in components.

**Pages implemented:**
- `Dashboard` — workspace health, dead-letter count, cost tiles, recent operations
- `Pipelines` — pipeline list with stage bar, status states, blocked/failed detail, pause/recover/resume actions
- `Reviews` — review queue with item type filter, row selection, approve/reject modal (reject requires reason)
- `Channels` — channel list with multi-account view, account status/external-ID/credential per account
- `Analytics` — metric table with AGG_SUM/AGG_LAST calculation method display
- `Learning` — recommendation list with evidence classification and observational-only language contract
- `Exceptions` — exception center with category, severity badge, entity ID
- `Operations` — operation lifecycle with actor, correlation ID, retry-on-failure action
- `Experiments` / `Workflows` / `Automation` — placeholder pages (Phase 15 content)
- `AppShell` — sidebar nav, workspace selector, token-level authorization boundary

**Frontend boundary contract:**
- React / browser → `src/api/client.ts` (single point of entry) → FastAPI thin transport → ApplicationService → Control Plane / engines
- No backend imports, no sqlite3, no DB paths in frontend source
- Dev actor header (`X-Dev-Actor: dev:studio-user`) centralized in client.ts, labeled DEV-ONLY; replaced by JWT in Phase 15
- `SCHEMA_VERSION` and `APPLICATION_CONTRACT_VERSION` remain 19 and "13.0.0" respectively (no backend schema changes in Phase 14)

**Frontend test suite (M14.11):**
- 13 test files, 111 tests — Vitest 4 + React Testing Library + MSW v2
- Coverage: Architecture boundary, API client transport, AppShell, Dashboard, Pipelines, Reviews, Channels, Analytics, Learning, Exceptions, Operations, Experiments/Workflows/Automation, Accessibility
- MSW handlers use absolute `http://localhost:5173/api/v1/...` URLs; jsdom URL set to match
- `process.cwd()` used in architecture test (not `import.meta.url`) for reliable filesystem path

**Quality gate results:**
- `npm test`: 13/13 files, 111/111 tests pass
- `npm run test:coverage`: 73.5% statements, 75.4% branches, 62.7% functions, 76.2% lines
- `npm run typecheck`: clean (0 errors)
- `npm run lint`: clean (oxlint)
- `npm run build`: clean (326 kB JS bundle, 17.6 kB CSS)
- `python -m ruff check src/`: all checks passed
- `python -m pytest`: 3368 passed, 1 skipped
- `git diff --check`: clean

### What waits

Phase 15 — Deployment, Infrastructure & Production Operations.
