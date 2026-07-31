## Phase 3 Milestone 3.1: Operator decisions (D1–D7)

**D1 — Default scoring weights are configurable, not constants**

**Decision:** The initial scoring weights (`audience_demand: 0.30`,
`competition: 0.25`, `channel_fit: 0.25`, `production_feasibility: 0.20`)
are stored as versioned constants in `DEFAULT_SCORING_WEIGHTS` and referenced
by scoring policy version `"1.0.0"`. They must not be treated as permanent
universal constants.

**Reasoning:** Scoring weights are business judgment, not physical constants.
They will need tuning once real analytics data is available (Phase 11). The
scoring policy versioning mechanism (Milestone 3.3) exists precisely to
support this without breaking historical score comparability.

---

**D2 — Initial channel maturity stage: `validation`**

**Decision:** New channels default to `MaturityStage.validation`. The
operator sets the stage explicitly when advancing (via `ace channels new-version
--stage`).

**Reasoning:** A new channel has not yet validated its niche, audience fit,
or content format. Defaulting to `validation` signals this and informs how
discovery, scoring, and content decisions should be weighted for early-stage
channels.

---

**D3 — GoogleTrendsAdapter deferred from MVP**

**Decision:** `google_trends` is a valid entry in `VALID_ADAPTERS` (schema
reservation) but is not implemented in Phase 3 MVP. It is not included in
any default `allowed_discovery_adapters` list.

**Reasoning:** Google Trends integration introduces rate-limiting, unofficial
API risks, and global vs. niche-specific demand mismatches. The OIE functions
correctly with `manual` and `youtube_data_api` adapters. Trends can be
added as an optional adapter in Milestone 3.2c without changing the channel
strategy schema.

---

**D4 — CompetitorResearchAdapter deferred (optional Milestone 3.2b)**

**Decision:** `competitor_research` is a valid adapter key but not
implemented in Phase 3 MVP. Deferred to optional Milestone 3.2b.

**Reasoning:** Competitor research adds meaningful signal but requires the
YouTube Data API client (Milestone 3.2a) to be complete first. Building
the adapter interface before the underlying client exists creates
placeholder code, which is explicitly excluded.

---

**D5 — `duplicate_similarity_threshold` default: 0.70, configurable per channel**

**Decision:** `ChannelProfileVersion.duplicate_similarity_threshold` defaults
to `0.70` (stored as `REAL NOT NULL DEFAULT 0.70` in the DB). The operator
can lower or raise this per channel by creating a new profile version.

**Reasoning:** 0.70 is a conservative threshold that prevents near-duplicate
content while allowing legitimate variations on proven topics. Channels with
a narrower niche may need a lower threshold; channels covering broad topics
may tolerate a higher one.

---

**D6 — Capacity defaults are ceilings, not quotas**

**Decision:** Default capacity policy values: `long_form_slots_per_week=2`,
`short_slots_per_week=4`, `content_package_slots_per_week=1`,
`max_concurrent_productions=2`, `review_hours_per_week=3.0`.

These are hard **ceilings**. The system must never fill capacity with weak
opportunities merely because capacity is available. Producing content that
does not meet the minimum opportunity score wastes budget and review time
without contributing to business objectives.

**Reasoning:** Filling slots with low-quality content is worse than producing
fewer, higher-quality pieces. The capacity ceiling prevents runaway production;
the `min_opportunity_score` threshold (default 0.40) prevents weak
opportunities from reaching the production queue.

---

**D7 — Recommendation thresholds are conservative, configurable, and auditable**

**Decision:** Default `min_opportunity_score=0.40`. Thresholds must remain
configurable via channel profile versions and must never be overridden in
application logic. Every threshold check must be auditable (stored with
the scoring record that triggered it).

**Reasoning:** Conservative defaults protect against the OIE promoting
mediocre content early in a channel's life when signal data is sparse.
Auditability is required to diagnose cases where the threshold rejects
an opportunity the operator considers valuable, or accepts one that
performs poorly.

---

## Phase 3 Milestone 3.1: Architectural decisions

**Circular FK pattern: channels ↔ profile versions**

**Decision:** `channels.current_profile_version_id` and
`channels.current_strategy_id` are nullable INTEGER columns with FK
references. They are set to NULL on channel creation and updated
immediately after the first profile version and strategy are inserted.
All of this happens within `create_channel_full()` before the transaction
commits.

**Reasoning:** SQLite with FK enforcement only checks FK validity at DML
time, not DDL time. The NULL → INSERT → UPDATE pattern satisfies FK
constraints at every step. Channel deletion is not supported in Phase 3,
so cascade ordering is not a current concern.

---

**Scoring policy version stored as TEXT reference, not FK**

**Decision:** `channel_profile_versions.scoring_policy_version` is a
`TEXT NOT NULL DEFAULT '1.0.0'` column, not a FK to a `scoring_policies`
table. The `scoring_policies` table and its seed data belong to
Milestone 3.3.

**Reasoning:** Establishing a FK to a table that doesn't exist yet would
require either placeholder seed data (which violates the no-placeholder-code
rule) or deferring the entire field to 3.3 (which loses the schema
reservation). Storing the version as a named TEXT string is a clean contract
that the 3.3 scoring engine will fulfill.

---

**Profile versions are immutable: no update path**

**Decision:** The repository exposes no `update_profile_version()` function.
The only write paths are `create_profile_version()` and
`supersede_profile_version()`. Changing any profile field requires
`create_new_profile_version()`, which creates a new row and supersedes
the old one.

**Reasoning:** Profile versions serve as the audit trail for how channel
strategy evolved over time. Allowing in-place updates would destroy the
history needed to explain why a given opportunity was scored the way it
was under a given configuration.

---

## Phase 2: Strict extra-field rejection for AI output schemas

**Decision:** All Pydantic schemas used to validate machine-consumed AI
provider responses must set `model_config = ConfigDict(extra="forbid")`.
Unexpected fields must raise a validation error.

**Reasoning:** AI providers can add fields to responses at any time. Without
`extra="forbid"`, schema drift is silent — the application keeps running
while the data contract degrades. Strict rejection surfaces the breakage
immediately, making it debuggable and auditable. Human-facing models (Phase
1 CLI inputs, etc.) are not affected unless there is a specific reason.

---

## Phase 2: Unknown production model cost must not be silently zero

**Decision:** `PricingRegistry.estimate_cost` raises `UnknownModelPricingError`
for any model that is not the built-in fake provider and is not in the
registry. `record_ai_call` catches this and stores `estimated_cost_usd = NULL`
with a warning log entry. The three cost states in `ai_calls` are:
- `estimated_cost_usd = 0.0` — explicitly free (fake/test provider)
- `estimated_cost_usd > 0.0` — calculated from registry pricing
- `estimated_cost_usd IS NULL` (with `status = 'success'`) — unknown; model
  not in registry

**Reasoning:** Silently reporting `$0` for an unknown production model masks
real spend. The distinction between "free by design" and "cost unknown" must
be unambiguous in the DB and in log output. No code path should present an
unknown production model as free.

---

## Phase 2: Provider abstraction via Protocol (not ABC)

**Decision:** `AIProvider` is a `@runtime_checkable Protocol` rather than an
abstract base class.

**Reasoning:** Protocol lets `FakeProvider` and `ClaudeProvider` satisfy the
interface without inheriting from a shared base. This keeps the fake provider
completely self-contained and avoids coupling test infrastructure to the
production class hierarchy. `isinstance` checks still work at runtime via
`@runtime_checkable`.

---

## Phase 2: TOML prompt files, not Python strings

**Decision:** Prompts are stored as versioned `.toml` files under
`src/app/ai/prompts/<name>/v<version>.toml`, not as Python string constants.

**Reasoning:** Separating prompt text from Python code makes prompt iteration
visible in git diffs, keeps prompt history in version control, and allows
non-developer editing without touching source. TOML gives structured
metadata (name, version, description) with validation on load.

---

## Phase 2: Injected client for ClaudeProvider test isolation

**Decision:** `ClaudeProvider.__init__` accepts an optional `client` parameter.
Tests pass a `MagicMock()` directly; the production path instantiates
`anthropic.Anthropic()`.

**Reasoning:** Avoids patching the `anthropic` module at import time. The
mock is explicit, type-safe, and scoped to the test — no global state is
modified. This was preferred over `unittest.mock.patch` which is fragile
against import path changes.

---

## Phase 2: No live API calls in any automated test

**Decision:** All tests run without contacting Anthropic or any external
service. `ClaudeProvider` tests use an injected mock client. `FakeProvider`
is deterministic. No test requires `ACE_ANTHROPIC_API_KEY` to be set.

**Reasoning:** Live API calls in tests introduce flakiness (rate limits,
network latency, cost, key rotation), make CI non-reproducible, and risk
leaking credentials. The boundary between the application and the Anthropic
SDK is tested via mock; SDK behaviour is tested by the SDK's own suite.

---

## Phase 2: Pricing registry with version + effective date

**Decision:** The built-in pricing table carries `registry_version` and
`effective_date` fields and can be overridden via `ACE_AI_PRICING_FILE`
(path to a JSON file with the same schema).

**Reasoning:** Model pricing changes frequently. Hardcoding values without
version metadata makes it impossible to audit when a cost estimate was
computed. The file-override mechanism lets operators update pricing without
a code deploy. `fake`-prefix models always return $0 so tests never
accidentally charge.

---

## Phase 2: Retry policy — retryable vs non-retryable errors

**Decision:** Only `RateLimitError` and `TransientProviderError` (5xx) are
retried. `MissingCredentialsError`, `RequestTimeoutError`, and
`ProviderUnavailableError` (connection errors) are not retried.

**Reasoning:** Rate limits and transient server errors are expected to
resolve with back-off. Credential errors will never resolve by waiting.
Timeouts and connection errors could resolve, but retrying them
automatically risks doubling latency on already-slow requests — callers
should decide. This boundary is explicit and testable.

---

## Product direction: YouTube Content Operating System

**Decision:** Redesign the product as a YouTube-first content operating
system rather than a generic multi-platform content generator.

**Alternatives considered:** Remaining platform-agnostic from the start;
building TikTok and Instagram support in parallel with YouTube.

**Reasoning:** A platform-agnostic design at this scale is an abstraction
in search of a problem. The three major short-form platforms have
meaningfully different APIs, analytics capabilities, content formats,
monetisation models, and algorithm behaviours. Building for all three
simultaneously would produce shallow support for each. YouTube has the
most mature API, the most complete analytics, the clearest monetisation
path (AdSense + RPM), and the largest long-term content library value.
Proving the system on YouTube first, then adapting, is lower risk and
higher leverage. Instagram and TikTok remain in the plan as Phase 15
adapters.

---

## Data-driven experimentation, not algorithm prediction

**Decision:** The system operates as a controlled-experimentation platform,
not as an algorithm predictor or virality guarantee engine.

**Reasoning:** No public API exposes YouTube's ranking signals. Any model
claiming to predict algorithm behaviour is either wrong or based on
scraped data that violates ToS. The correct posture is: propose hypotheses,
run controlled experiments (with sample-size safeguards), measure outcomes,
and promote winning patterns cautiously. Statistical results are reported
with disclaimers; the system never presents correlation as causation.

---

## Publishing modes and progressive oversight reduction

**Decision:** The system supports three publishing modes per channel:
**manual approval**, **supervised automation**, and **qualified autonomous
publishing**. Promotion between modes is explicit, reversible, and recorded.
Autonomous publishing is a goal, not a concession.

**When human approval is always required (regardless of mode):**
- Development, initial production, new-channel onboarding
- New content format onboarding
- After material system changes (prompt updates, provider changes, schema
  migrations)
- Any content flagged as a high-risk category (health, finance, politics,
  legal advice, content involving minors, or categories added to this list
  by the operator)
- Mode promotion decisions

**Qualification thresholds for autonomous publishing (all must be met):**
- Minimum meaningful sample of published videos with measured outcomes
- Quality score above configurable threshold on all recent videos
- Zero policy-risk flags in the qualifying window
- Zero licence or duplicate violations in the qualifying window
- Production cost per video within configured limits
- No unresolved circuit-breaker events in the qualifying window
- Thresholds are configurable; defaults are conservative

**Autonomous publishing must include (all mandatory, not optional):**
- Configurable daily and weekly publishing limits
- Content-quality threshold check (blocks publication if below threshold)
- Factual-risk threshold check
- Asset-licence verification (all assets confirmed commercial-ok)
- Duplicate-content check against all published content
- Spending limit enforcement (per-video and per-day)
- Full audit log entry for every autonomous publish decision
- Operator notification on every autonomous publish
- Automatic mode demotion on: any check failure, policy-risk flag,
  unusual cost spike, or consecutive performance anomalies
- Immediate manual kill switch (halts all autonomous publishing for the
  channel with a single command)

**Reasoning:** Permanent human approval conflicts with the product goal
of becoming a low-oversight, commercially viable operation. A system that
requires human intervention for every publish cannot scale. The correct
design is not "always human" or "always autonomous" but a well-defined
graduation path with hard safety conditions at every level. A channel-
killing mistake from a miscategorised or policy-violating video is the
risk being managed — the answer is comprehensive automated checks and
immediate automatic demotion on failure, not a permanent gate that
prevents the product from achieving its business goal.

---

## Prohibited data sources

**Decision:** The following data sources are prohibited regardless of
technical feasibility:
- Scraping YouTube or Google in violation of their Terms of Service
- Keyword search volume from unofficial scraping tools or browser-extension
  data exports
- Competitor revenue data (not publicly available)
- YouTube algorithm ranking signals (not publicly available)

**Reasoning:** ToS violations risk account bans, legal liability, and loss
of API access. Inferred or scraped data that appears authoritative but is
unreliable is worse than acknowledged uncertainty. Where a signal is not
available from a permitted source, the system records it as `source=manual`
or defers to a permitted third-party provider (noted explicitly in Phase 3).

---

## YouTube Analytics API metric availability — confirmed constraints

**Decision:** The implementation must respect the following known constraints
and verify remaining uncertainties at Phase 11 implementation time.

**Confirmed available (monetised channel, YouTube Analytics API):**
- views, impressions, impressionClickThroughRate
- averageViewDuration, averageViewPercentage
- estimatedMinutesWatched
- subscribersGained, subscribersLost
- estimatedRevenue, estimatedAdRevenue, rpm
- likes, comments (via Data API)

**Available for Shorts specifically:** confirm dimension/filter support in
YouTube Analytics API at Phase 11 — the API has expanded Shorts support
since 2023 but field availability should be verified against current docs.

**Not available:**
- Competitor revenue
- Click-through rates broken down by impression source for Shorts (verify)
- Algorithm ranking factors

**Reasoning:** Building analytics schemas around unconfirmed metric
availability leads to empty columns, misleading dashboards, and wasted
development. The schema is designed conservatively; fields are added only
after confirming availability in the API.

---

## Deterministic scoring over ML models for topic opportunity

**Decision:** Phase 3 topic scoring uses a deterministic composite formula,
not a trained ML model.

**Reasoning:** A trained model requires a labelled dataset of topic
outcomes that doesn't exist until Phase 11 delivers real analytics data.
A deterministic formula — trend velocity, competitor saturation, recency,
niche fit — is interpretable, adjustable by the operator, and doesn't
require training data. Machine learning for topic scoring can be revisited
in a later phase once sufficient performance data is available.

---

## APScheduler over n8n for Phase 13 scheduling

**Decision:** Use APScheduler (in-process) for Phase 13 reduced-oversight
scheduling, not n8n or an external orchestration system.

**Reasoning:** n8n requires a running server, adds infrastructure overhead,
and introduces an external system dependency before the product is proven.
APScheduler runs in-process, requires no separate deployment, and is
sufficient for a single-machine operation running one channel's cadence.
Revisit if multi-machine operation or cross-system integration is needed
after Phase 14.

---

## Local deployment until profitability is demonstrated

**Decision:** The system runs locally until Phase 11 demonstrates measurable
profitability from a real YouTube channel.

**Reasoning:** Cloud infrastructure adds cost, operational complexity, and
maintenance burden before the core product is validated. A local system
is sufficient to produce and publish videos. Moving to cloud is justified
only after the unit economics are confirmed positive.

---

## LLM for generation and critique; deterministic for everything else

**Decision:** LLMs are used for: claim extraction, content brief, hook
generation, script generation, script critique, title/description/tags,
and optional niche classification. Everything else — scoring, validation,
scheduling, publishing decisions, analytics, cost tracking — is
deterministic Python.

**Reasoning:** LLMs are expensive, non-deterministic, and occasionally
wrong. Every use adds latency and cost. The discipline of asking "can this
be deterministic?" before reaching for an LLM keeps the system cheaper,
faster, and more auditable. The answer is often yes.

---

## Visual asset strategy: four supported categories; AI-generated deferred

**Decision:** The asset pipeline supports four categories of visual assets:
owned media, public-domain media, properly licensed stock, and properly
licensed AI-generated visuals. Phase 7 implements the first three. AI-
generated visuals are deferred until the asset pipeline meets additional
requirements. AI-generated visuals are not categorically excluded from the
finished product.

**Phase 7 implementation (owned, public-domain, licensed stock):**
- Easier to verify; licences are well-understood
- Avoids unsettled legal and platform-policy territory while the asset
  pipeline is being built

**AI-generated visual assets: deferred until the following are in place:**
- Provider and model tracking (which provider, which model version)
- Commercial-use terms confirmed for the specific provider and use case
- Prompt and output provenance stored (prompt text, seed, generation date)
- Disclosure flags (ability to mark a video as using AI-generated visuals,
  consistent with YouTube's AI disclosure requirements)
- Likeness and trademark risk checks (block generation prompts referencing
  real people or protected brands)
- Quality validation (resolution, aspect ratio, motion artefact detection)

**Long-term asset selection criteria (Phase 7+ optimisation):**
Originality signal, licensing confidence, visual quality score, production
cost, and historical performance correlation. The system should eventually
select assets that maximise this composite, not just minimise cost.

**Reasoning:** Licensed stock is the correct starting point because it is
verifiable and sufficient for initial production. Excluding AI-generated
visuals permanently would limit the system's long-term production efficiency
and originality potential. The deferred requirements above address the
genuine risks (legal exposure, platform policy, disclosure obligations)
without making a categorical decision the product cannot change later.

---

## Python 3.13 over 3.14

**Decision:** Target Python 3.13.

**Alternatives considered:** Python 3.14 (newest stable release as of
mid-2026).

**Reasoning:** Third-party SDKs (Anthropic, google-api-python-client, TTS
providers) typically take a release cycle to support new Python versions.
3.13 has broad wheel availability. Revisit if a dependency requires 3.14+.

---

## `src/` layout over flat package layout

**Decision:** Package code lives under `src/app/`, installed in editable
mode.

**Reasoning:** The `src/` layout forces all imports — including in tests —
through the installed path, catching packaging bugs immediately instead of
at first clean-machine install.

---

## Typer for Phase 1+ CLI (replacing Phase 0 argparse)

**Decision:** Switched from `argparse` to Typer starting in Phase 1.

**Reasoning:** Phase 1 adds four subcommand groups with typed arguments.
Hand-written `argparse` subparsers at that scale are verbose. Typer
generates help text, validates types, and handles optional arguments with
zero boilerplate. The Phase 0 decision to defer this was correct; revisited
at the right time.

---

## argparse (stdlib) for Phase 0 only

**Decision:** Used `argparse` for Phase 0's two trivial diagnostic commands.

**Reasoning:** Phase 0 had exactly two argument-free commands. A
third-party dependency before CLI complexity justified it was
over-engineering. Revisited in Phase 1 per plan.

---

## pyproject.toml-only dependency management

**Decision:** Declare dependencies in `pyproject.toml`, not separate
requirements files.

**Reasoning:** Two files drift out of sync. `pyproject.toml` is a single
source of truth, still using only `pip` and `venv` with no extra tooling.

---

## Ruff for linting and formatting

**Decision:** Use Ruff for both linting and formatting instead of
Black + Flake8 + isort separately.

**Reasoning:** Ruff reimplements all three in one fast tool with one config
block. No benefit to three tools for the same outcome.

---

## Phase 3 Milestone 3.3: Versioned Scoring and Confidence Engine (D-M3.3-1 through D-M3.3-8)

**D-M3.3-1 — Score rows are append-only; skip-on-rescore uses a 4-tuple + input_hash match**

**Decision:** `opportunity_scores` is an insert-only table. Re-scoring an
opportunity with identical inputs returns the existing score row unchanged.
The 4-tuple key is `(opportunity_id, scoring_policy_id,
channel_profile_version_id, input_hash)`. `--force` bypasses the check and
always writes a new row.

**Reasoning:** Immutable historical records make score comparability
reliable. The 4-tuple collapses identical scoring runs without discarding
prior scores produced under different policies or profile versions.

---

**D-M3.3-2 — Activated policy content is immutable; update is draft-only**

**Decision:** Once a `ScoringPolicy` transitions from `draft` to `active`,
its weights and missing-data policy fields are frozen. `update_scoring_policy`
raises `ValueError` for any non-draft policy.

**Reasoning:** Score rows store `scoring_policy_id`, not a copy of the
weights at the time of scoring. Mutable active policies would make the FK
reference ambiguous for historical interpretation.

---

**D-M3.3-3 — `MissingDataPolicy.reweight_available` and `require_research` both enter the redistribution pool**

**Decision:** Absent factors under `reweight_available` *and* under
`require_research` both contribute their nominal weight to the redistribution
pool. `apply_prior` retains its effective weight and contributes a fixed
score of 0.5.

**Reasoning:** `require_research` signals a data gap that must be surfaced
(via the `requires_research` flag) without silently zeroing the composite
score. The redistribution pool ensures the remaining factors fill the full
0–1 scoring range while the flag alerts callers to the gap.

---

**D-M3.3-4 — Deterministic latest-score rule: ORDER BY scored_at DESC, id DESC**

**Decision:** Every function that retrieves "the latest score" uses a
correlated `NOT EXISTS` subquery with `ORDER BY scored_at DESC, id DESC`
tiebreaker. `list_scored_opportunities` uses the same ORDER clause in its
correlated subquery so deduplication is deterministic even if two rows share
a timestamp.

**Reasoning:** Wall-clock ties are possible in tests (same second) and in
batch scoring. The auto-increment `id` provides a stable tiebreaker without
requiring a higher-resolution timestamp column.

---

**D-M3.3-5 — `audience_fit` absent when niche token set is empty; zero overlap returns score 0.0 (present)**

**Decision:** If `normalize_topic(primary_niche)` produces an empty token
set, `compute_audience_fit` returns `FactorStatus.absent`. A non-empty niche
with zero keyword overlap returns `FactorStatus.present` with `raw_score=0.0`.

**Reasoning:** Empty niche tokens mean the operator has not configured a
niche (or configured only stopwords). This is a *data-quality gap*, not a
score of zero. Zero-overlap with a configured niche is legitimate content
that genuinely does not fit the channel — the score should reflect that.

---

**D-M3.3-6 — Input snapshot excludes the policy; policy_id is compared separately**

**Decision:** `build_input_snapshot` does not serialize the `ScoringPolicy`
object. The skip-on-rescore check compares `policy_id` as a separate column
alongside `input_hash`.

**Reasoning:** Active policies are immutable, so their content cannot change
between two score runs with the same `policy_id`. Serializing the policy
would add weight to the hash computation and snapshot storage for no
additional correctness benefit.

---

**D-M3.3-7 — SCHEMA_VERSION bumped to 5; all migration paths execute v5 DDL**

**Decision:** `SCHEMA_VERSION = 5` adds `scoring_policies` and
`opportunity_scores`. Every prior migration path (v0–v4 → v5) executes the
same `_DDL_V5_SCORING` block.

**Reasoning:** A single DDL block for v5 is simpler and easier to audit
than branched DDL. Fresh databases also get v5 tables on first open.

---

**D-M3.3-8 — `get_max_similarity_to_existing` is a dedicated function, not reuse of `find_existing_opportunity`**

**Decision:** Similarity scoring uses a separate `get_max_similarity_to_existing`
function that scans all non-rejected/archived sibling opportunities and
returns `(similarity_score, opportunity_id, normalized_topic) | None`.

**Reasoning:** `find_existing_opportunity` implements duplicate detection for
ingestion (strict deduplication semantics). Scoring novelty requires a
different query scope (all siblings, not just duplicates) and a different
return shape. Reusing the same function would have coupled two distinct
concerns.
