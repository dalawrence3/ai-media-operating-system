# Project State Snapshot

**Date:** 2026-08-05
**Latest implemented milestone:** Phase 6 M6.3C — Live ElevenLabs Provider Integration
**Next milestone:** Phase 7 — Licensed Assets and Scene Manifests

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

---

## Current codebase state

### Schema version
`SCHEMA_VERSION = 12`

### Test count
**1889 passing, 1 skipped** (ruff clean; skipped test is the always-skipped live smoke test)

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

## What waits

### Immediate next: Phase 7 — Licensed Assets and Scene Manifests

Dependencies: Phase 6 complete (M6.3C ✅).
