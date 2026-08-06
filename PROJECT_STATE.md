# Project State Snapshot

**Date:** 2026-08-06
**Latest implemented milestone:** Phase 8 — Rendering Engine
**Next milestone:** Phase 9 — Publishing and Orchestration Engine

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

---

## Current codebase state

### Schema version
`SCHEMA_VERSION = 14`

### Test count
**2154 passing, 1 skipped** (ruff clean; skipped test is the always-skipped live smoke test)

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

## What waits

### Immediate next: Phase 8 — Asset Provider Integration

Dependencies: Phase 7 complete ✅.
Likely scope: stock footage API adapters, AI image generation provider, licensing verification, provider registry pattern (mirrors `src/app/narration/providers/`).
