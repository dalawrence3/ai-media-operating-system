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

---

## Phase 3 Milestone 3.4: Opportunity Promotion (D-M3.4-1 through D-M3.4-6)

**D-M3.4-1 — `topics.channel_id` omitted; channel derived via FK chain**

**Decision:** The `promoted_opportunity_id` FK on `topics` links back to
`opportunities`, which already owns `channel_id`. Channel is not
denormalised onto `topics`.

**Reasoning:** Single source of truth. For manually-created topics there is
no current mechanism to assign a channel, so the column would always be NULL
for them. Any future query needing both topic and channel can JOIN
`opportunities` via `promoted_opportunity_id`.

---

**D-M3.4-2 — SCHEMA_VERSION 5 → 6; partial unique index instead of UNIQUE column**

**Decision:** `_DDL_V6_PROMOTE` uses two statements:
`ALTER TABLE topics ADD COLUMN promoted_opportunity_id INTEGER REFERENCES opportunities(id)`
followed by
`CREATE UNIQUE INDEX uq_topics_promoted_opportunity ON topics(promoted_opportunity_id) WHERE promoted_opportunity_id IS NOT NULL`.

**Reasoning:** `ALTER TABLE ADD COLUMN ... UNIQUE` is not reliably enforced
as an actual unique index in all SQLite versions. A partial `WHERE NOT NULL`
index enforces uniqueness for promoted topics while allowing any number of
manually-created topics (`promoted_opportunity_id = NULL`).

---

**D-M3.4-3 — Idempotency check runs before lifecycle guard**

**Decision:** `promote_opportunity()` calls `get_topic_by_promoted_opportunity()`
first. If a linked topic exists, the function returns immediately with that
topic and the last state event, regardless of the opportunity's current
lifecycle state.

**Reasoning:** The idempotent case is the success case — a topic already
exists. Checking lifecycle state first would cause the second call to fail
on a now-`approved` opportunity, breaking idempotency for the most common
re-promotion scenario.

---

**D-M3.4-4 — "Scored" means at least one `opportunity_scores` row; no policy requirement**

**Decision:** The score prerequisite check queries
`SELECT 1 FROM opportunity_scores WHERE opportunity_id = ? LIMIT 1`.
Any score row satisfies the check, regardless of which policy produced it.

**Reasoning:** The operator is trusted to have run an appropriate scoring
pass. Restricting to a specific policy would force re-scoring after policy
changes and block legitimate promotion flows.

---

**D-M3.4-5 — One SAVEPOINT per promotion; `create_topic()` bypassed**

**Decision:** `promote_opportunity()` inserts directly into `topics` within
a SAVEPOINT, then calls `transition_opportunity_state()`, then commits once.
It does not call `create_topic()`.

**Reasoning:** `create_topic()` calls `conn.commit()` eagerly, which would
commit the topics INSERT before the lifecycle transition, breaking atomicity.
The SAVEPOINT pattern (established in `discovery.py`) provides rollback of
both writes on any exception.

---

**D-M3.4-6 — Promoted opportunities are permanent records; no `--force` deletion-recovery flag**

**Decision:** Once a topic is promoted and downstream artifacts reference it,
deleting the topic cascades to those artifacts. The opportunity record and
its full history (state events, observations, evidence) must not be modified
or deleted after reaching `approved`. No `--force` flag is provided for the
deleted-topic recovery scenario.

**Reasoning:** The `promoted_opportunity_id` column creates a permanent,
inspectable audit chain: `topic → opportunity → observations → evidence →
discovery_run`. Recovery tooling for the exceptional case (a promoted topic
deleted while the opportunity remained) is deferred until the scenario
arises in practice.

---

**D-M4.1-1 — Validate URL/path before creating any Source identity row**

**Decision:** `validate_url()` and `validate_file_path()` are called before
`get_or_create_source()`. A SecurityError or ValueError during validation
creates no DB rows.

**Reasoning:** Source identity rows represent a commitment that a resource
was assessed. Creating a Source row for a URL that fails SSRF validation
would pollute the sources table with records that can never have valid
content, and would make the deduplication logic (same URL → same Source)
incorrect for future legitimate fetches after the block was lifted (e.g. if
the URL were changed to a public address). Rows are only created when the
resource passes pre-fetch validation.

---

**D-M4.1-2 — HTTPS→HTTP redirect is a hard SecurityError, not a warning**

**Decision:** Any redirect that changes scheme from https to http — whether
in the redirect history or the final URL — raises `SecurityError` and
terminates the request. No SourceContent row is written for the content that
would have been fetched over HTTP.

**Reasoning:** HTTPS→HTTP downgrades expose the connection to MitM attacks.
A warning-only approach would allow extracting and storing potentially tampered
content. The operator's explicit decision to block (not warn) is recorded here
so future maintainers do not soften this to a log line.

---

**D-M4.1-3 — Pre-resolution SSRF is the documented MVP boundary**

**Decision:** SSRF protection uses `socket.getaddrinfo()` to pre-resolve
the hostname and checks the resolved IP against blocked ranges. Connect-time
IP pinning (verifying the connection actually goes to the pre-resolved IP) is
deferred.

**Reasoning:** Pre-resolution blocks the most common SSRF attack surface
(hostnames resolving to RFC 1918 or loopback addresses). It does not prevent
DNS rebinding attacks, where a hostname resolves to a public IP at validation
time and then to a private IP at connection time. This limitation is documented
here so it is not forgotten: connect-time IP pinning is the hardening step,
not a change to this design.

---

## Phase 4 Milestone 4.2 — Evidence & Claim Extraction

---

**D-M4.2-1 — Supersession via columns, not status**

**Decision:** When a newer completed run replaces a prior one, the prior run's
`superseded_at` and `superseded_by_run_id` columns are set. The run's `status`
column (running/completed/partial/failed) records only the execution outcome
and is not changed on supersession.

**Reasoning:** Separating lifecycle metadata (superseded?) from execution
outcome (what happened?) prevents overloading the status field and keeps the
CHECK constraint to 4 values. A superseded run can be completed or partial;
its execution outcome is still accurate and useful for audit.

---

**D-M4.2-2 — Active evidence excludes no_quote and unsupported**

**Decision:** `list_active_evidence_for_topic()` and the active evidence query
apply `quote_support_status IN ('exact', 'normalized')`. Claims with
`no_quote` or `unsupported` status are never returned as active evidence.

**Reasoning:** Claims without a locatable supporting quote cannot be verified
against the source text. Serving them as active evidence would allow
ungrounded claims to reach script generation. They remain queryable via
`list_claims(include_unsupported=True)` for review, but are never surfaced
as evidence without a quote.

---

**D-M4.2-3 — Chunk offset invariant: separator belongs to preceding paragraph**

**Decision:** `_build_paragraph_spans()` sets each paragraph span's end to
`m.end()` (the end of the `\n\n+` separator match), not `m.start()`. This
means the trailing separator is included in the preceding span, not the next.

**Reasoning:** The invariant `chunk.text == raw_text[start:end]` requires
spans to tile the raw text without gaps. Including the separator in the
preceding span ensures no characters are dropped between spans. The chunker
never strips or modifies source characters.

---

**D-M4.2-4 — NFC offsets are NULL when character count changes**

**Decision:** When Unicode NFC normalization changes the character count of
the raw text, `classify_quote_support()` returns `(normalized, None, None)`
rather than attempting to map positions through the NFC change.

**Reasoning:** NFC normalization can merge or split code points (e.g.
combining characters). A character-level index map built over NFC positions
is not reliable for mapping back to raw positions when lengths differ. NULL
offsets signal approximate provenance rather than presenting incorrect offsets
as exact.

---

**D-M4.2-5 — record_ai_call() called for every chunk regardless of outcome**

**Decision:** `_process_chunk()` calls `record_ai_call()` in its `finally`
block for every chunk, even on failure. `response=None` is passed on failure;
`status='failed'` is set.

**Reasoning:** Token and cost accounting must reflect all API interaction
attempts, including failures. A missing ai_calls row would undercount usage.
The `record_ai_call()` function accepts `response=None` for exactly this case.

---

**D-M4.2-6 — finalize_claim_extraction_run uses one SAVEPOINT**

**Decision:** All claim INSERTs, the optional supersession UPDATE, and the run
status UPDATE are wrapped in a single SAVEPOINT. On failure, a separate
transaction marks the run as failed (not inside the rolled-back SAVEPOINT).

**Reasoning:** Atomicity requires that a partial set of claims is never
committed without the corresponding run status update. Using one SAVEPOINT
ensures either all claims and the status land together or none do. The
fallback `UPDATE ... SET status='failed'` runs outside the SAVEPOINT so it
can still commit even after the SAVEPOINT rolls back.

---

## Phase 5 — Script Generation

---

**D-P5-1 — Single canonical sort_evidence() used everywhere**

**Decision:** `sort_evidence()` (5-key: quality DESC NULL-last, requires_date_review ASC, claim_type ASC, source_id ASC, claim_id ASC) is the sole ordering function for evidence. It is called by `compute_evidence_hash()` internally, by the prompt-context formatter, and by the evidence IDs JSON in generation runs.

**Reasoning:** Any drift between the ordering used for hashing and the ordering used for prompt construction would break reproducibility and idempotency. A single function enforced everywhere eliminates that class of bug.

---

**D-P5-2 — body_json as canonical script representation**

**Decision:** `body_json` stores the full `GeneratedScript` as a JSON blob (Pydantic model_dump_json). `body` is produced only by deterministic `render_body()` and is derived from `body_json`. Manual scripts retain `body_json = NULL`.

**Reasoning:** Downstream phases (Phase 6 narration, Phase 10 publishing) need structured access to sections and cited_claim_ids. Storing the raw JSON allows re-rendering, re-validation, and structured access without re-parsing free text.

---

**D-P5-3 — approve_script() supersedes via SAVEPOINT, no partial unique index**

**Decision:** There is no partial unique index on `scripts(topic_id) WHERE status='approved'`. Approval uniqueness is enforced transactionally: `approve_script()` sets `superseded_at` on all prior active approved Scripts within a SAVEPOINT before approving the new one. Prior approved Scripts keep `status='approved'`.

**Reasoning:** A partial unique index would reject historical data (v8 had multiple approved scripts per topic) and would conflict with the non-destructive supersession model. Transactional enforcement is sufficient and more flexible.

---

**D-P5-4 — Evidence hash independent of prompt hash and generation settings**

**Decision:** `compute_evidence_hash()` depends only on claim fields. `compute_prompt_hash()` depends only on prompt name/version/system/user_template. `compute_script_input_hash()` combines both plus all behavior-affecting settings (model, temperature, versions, tone, audience, target_duration_s).

**Reasoning:** Independence allows detecting which axis changed when a re-run is triggered. If only settings changed, the evidence hash is unchanged, which is useful for audit. Combined into input_hash for idempotency lookup.

---

**D-P5-5 — record_ai_call() called outside any SAVEPOINT**

**Decision:** In `generate_script()`, `record_ai_call()` is always called before `finalize_generation_run()` and outside any SAVEPOINT. The `ai_call_id` is passed into `finalize_generation_run()` as a parameter.

**Reasoning:** `record_ai_call()` auto-commits, which would implicitly release any open SAVEPOINT. Calling it inside a SAVEPOINT would silently commit partial state. Calling it first ensures the call is recorded even if finalization fails.

---

**D-P5-6 — UnstructuredApprovedScriptError at Phase 6 boundary**

**Decision:** `get_active_approved_generated_script()` raises `UnstructuredApprovedScriptError` if the active approved Script has `body_json=NULL` (manually created via `ace scripts add`). It does not attempt to parse `body` as JSON.

**Reasoning:** Phase 6 narration requires structured section data that only exists in `body_json`. A manual script's `body` field is free text with no guaranteed structure. Failing loudly at the boundary forces the operator to generate a new script rather than silently producing malformed narration.


---

## Phase 6 Milestone 6.1 — Production Plan

---

**D-M6.1-1 — Unclamped per-segment duration**

**Decision:** `_segment_duration_s()` uses `max(1, ceil(word_count / 150 * 60))` with no upper bound. The content renderer's `compute_duration_s()` clamps to [15, 90] s but is NOT used for individual segments.

**Reasoning:** A 3-word CTA genuinely takes ~1 second, not 15. The [15,90] clamp was designed for whole-script duration validation, not per-segment estimation. Applying it per-segment would produce incorrect retention attribution: a 3-word segment shown as 15 s in analytics would mislead platform optimization. Total plan duration remains unclamped and is expected to fall within the script's validated [15,90] range via the sum of realistic per-segment values.

---

**D-M6.1-2 — Two partial unique indexes (not one) for active-plan isolation**

**Decision:** Two partial unique indexes enforce at most one active approved plan: `idx_pp_one_active_normal` (WHERE experiment_id IS NULL) and `idx_pp_one_active_experiment` (WHERE experiment_id IS NOT NULL, additionally unique on experiment_id). Normal and experiment plans do not interfere with each other.

**Reasoning:** A single index on `(topic_id)` would prevent having any active experiment alongside a normal plan. The two-index design is the minimal change that enables future A/B testing without a schema migration. All M6.1 plans have `experiment_id = NULL`.

---

**D-M6.1-3 — Normalized production_segment_citations table**

**Decision:** `production_segment_citations` is a separate table with `UNIQUE(segment_id, claim_id)` and `UNIQUE(segment_id, citation_order)`. No `citation_ids_json` column on segments.

**Reasoning:** Normalization enables `claim_id REFERENCES claims(id) ON DELETE RESTRICT` (training-label preservation), `idx_psc_claim` for reverse lookup (all segments citing a claim), and independent citation ordering enforcement. A JSON column would require application-layer parsing for every FK operation and would make claim deletion dangerously silent.

---

**D-M6.1-4 — Denormalized training-label fields on review events**

**Decision:** `production_plan_review_events` copies `topic_id`, `script_id`, `evidence_hash`, `model`, `prompt_hash`, `experiment_id` from the plan row at event creation time. Both `topic_id` and `script_id` use `ON DELETE RESTRICT`.

**Reasoning:** Review events are training labels. If the plan row is deleted, the label must survive. `ON DELETE RESTRICT` prevents accidental destruction. Denormalizing means queries for "all rejections for this model/prompt pair" need no join and remain efficient as data grows. The extra storage cost is negligible.

---

**D-M6.1-5 — Platform-neutral analytics terminology**

**Decision:** `production_segment.id` is the granular analytics unit. Tables and columns use "platform" not "YouTube". No YouTube-specific columns exist in the production schema.

**Reasoning:** YouTube is the first delivery platform, but the production pipeline (plan → narration → rendering) is platform-agnostic. Baking YouTube into the production schema would require a migration when TikTok or Instagram support is added. Platform-specific retention data belongs in a future `platform_analytics` table with a `platform` discriminator column, not in `production_segments`.

---

**D-M6.1-6 — experiment_id nullable on production_plans**

**Decision:** `experiment_id TEXT` is nullable and defaults to NULL for all M6.1 plans. It is included now rather than added in a future migration.

**Reasoning:** Adding a nullable column via `ALTER TABLE` is a schema change that requires a new SCHEMA_VERSION and affects all migration branches. Adding it now, with a known-safe NULL default, is the minimal incremental cost. The alternative (adding it in M6.2 or a dedicated A/B phase) would require re-testing the entire migration path. NULL is a valid state, not a placeholder.

---

## Phase 6 M6.2 Decisions

---

**D-ARCH-1 — Every human correction is a future training signal (permanent architectural principle)**

**Decision:** The system must preserve every approval, rejection, structured reason, optional note, corrective action, and future performance outcome in append-only history. Human feedback must never be silently discarded, overwritten, or reduced to only the current state. Future optimization phases will combine operator feedback with Platform Analytics to learn preferences, quality standards, and performance patterns while retaining complete provenance and rollback capability.

This principle applies across: Scripts, Production Plans, Narration, Captions, Visual assets, Scene manifests, Rendered videos, Publishing decisions, Platform Analytics, Experiment results, and future preference and optimization models.

Operationally: review event tables are insert-only; no application `UPDATE` or `DELETE` may target review events; rejected artifacts and their feedback remain permanently inspectable; regeneration creates a new artifact rather than rewriting the rejected one; supersession is tracked via `superseded_at` timestamps, not by deletion.

**Reasoning:** A content production system that learns requires complete, unambiguous history. A single rejected narration segment, with its reason code, severity, expected correction, voice profile ID, and provider, is one data point toward learning which voices suit which topics. Discarding that data — even for "convenience" — destroys the future optimizer's training set. Append-only history also enables rollback: if a preference model produces bad recommendations, the prior approved artifacts and human decisions are still present and can be restored or reanalyzed.

---

**D-M6.2-1 — Exception-based narration review workflow**

**Decision:** Synthesized segment assets are provisionally acceptable unless explicitly rejected. Run approval does not require per-segment approval events. Segment rejection is explicit, structured, and append-only. Rejected segments must be regenerated before run approval. Run approval inserts one run-level event and atomically supersedes the prior active approved run.

**Reasoning:** Requiring explicit per-segment approval for a 4-segment narration run creates 4 unnecessary clicks for the common case where synthesis is acceptable. Exception-based review is faster for the operator, reduces review event noise, and preserves the same audit trail since rejections are still fully recorded. If a segment is not rejected, the run-level approval event serves as the documented acceptance of all active synthesized assets.

---

**D-M6.2-2 — Segment-level audio assets**

**Decision:** One narration asset per Production Segment. No full-plan combined audio asset in M6.2. Every asset references its `production_segment.id` as the Platform Analytics granular unit.

**Reasoning:** Platform Analytics attribution to individual segments is a first-class requirement (D-M6.1-5). Segment-level assets enable regenerating only a single failing segment rather than the whole narration. Phase 7 scene manifests reference `narration_segment_id` per scene. Phase 8 rendering will concatenate segments. The concat step belongs in Phase 8, not M6.2.

---

**D-M6.2-3 — Separate tts_calls table (not ai_calls)**

**Decision:** TTS cost records go in a dedicated `tts_calls` table. The existing `ai_calls` table is not reused.

**Reasoning:** `ai_calls` is token-oriented (input_tokens, output_tokens). TTS is character-billed. Forcing TTS into `ai_calls` would require NULL-filling the token columns, polluting the table with semantically wrong data. A dedicated table with `characters_submitted`, `characters_billed`, and `price_per_1k_chars` (stored at call time) accurately represents TTS billing and remains correct even when the pricing registry changes.

---

**D-M6.2-4 — WAV output format for MVP audio validation**

**Decision:** Default audio format is WAV. Audio validation uses the Python stdlib `wave` module. LUFS normalisation, MP3 parsing, and FFmpeg are deferred to Phase 8.

**Reasoning:** WAV can be fully validated (sample rate, channel count, frame count, duration) without any new dependency using the stdlib `wave` module. MP3 duration measurement requires frame-counting or a third-party library. Since Phase 8 already introduces FFmpeg for rendering, LUFS normalisation and format conversion are natural Phase 8 concerns. WAV files are larger but the operator audience is a single-machine portfolio system where storage cost is negligible compared to correctness guarantees.

---

**D-M6.2-5 — No API keys or secrets in voice_profiles**

**Decision:** `voice_profiles.provider_voice_id` stores a non-secret provider identifier (e.g., `"alloy"` for OpenAI, a UUID for ElevenLabs). API keys are never stored in the database; they are read from environment variables at runtime.

**Reasoning:** Database rows are logged, backed up, and inspected. An API key in a voice_profiles row would be visible in any DB dump, log, or migration script. Provider voice IDs are not credentials; they are reference strings that identify which voice to request. The distinction between "voice identity" and "authentication" must be maintained permanently.

**D-M6.2-6 — Narration run supersession is not rejection**

**Decision:** When `approve_narration_run()` supersedes a prior active approved run, the prior run keeps `status='approved'`. Only `superseded_at` and `superseded_by_run_id` are set on the prior run. The partial unique index (`WHERE status='approved' AND superseded_at IS NULL`) enforces at-most-one active approved run per plan without touching the prior run's status.

**Reasoning:** Supersession and rejection are orthogonal lifecycle events. A superseded run was legitimately approved; it was replaced by a newer run, not found to be defective. Changing its status to `rejected` would break audit queries ("what was approved and when"), contaminate rejection analytics with non-defective events, and misrepresent operator decisions in the training dataset. The `superseded_at` timestamp is the canonical marker. Four status values (`running/completed/failed/approved/rejected`) are sufficient; no `superseded` status is needed.

**D-M6.2-7 — Review events preserve immutable training context at insert time**

**Decision:** Every row in `narration_review_events` stores a denormalized snapshot of the full provenance at the moment the event is recorded: `plan_id`, `script_id`, `topic_id`, `voice_profile_id`, `provider`, `model`, `voice_id`, `experiment_id`. These fields are frozen at INSERT and never updated.

**Reasoning:** Voice profiles are versioned and supersedeable; topics and scripts can be edited after narration is approved. If review events only stored foreign keys, a future query reconstructing "which voice/model/topic led to this rejection" would return current state, not historical state — making the event useless for learning. Denormalized provenance ensures that each review event is a self-contained training datum regardless of how the referenced entities evolve.

**D-M6.2-8 — FakeTTSProvider is the only TTS provider in M6.2**

**Decision:** M6.2 ships with `FakeTTSProvider` only. No ElevenLabs, OpenAI TTS, Google, AWS Polly, or Azure Cognitive Services SDK is added. Provider selection is deferred to M6.3 pending explicit operator approval of a concrete production provider.

**Reasoning:** Selecting a provider requires cost negotiation, API key management, latency profiling, and quality evaluation. Those decisions are out of scope for M6.2, which is concerned with establishing the pipeline architecture. `FakeTTSProvider` generates valid WAV bytes deterministically, covering the full pipeline end-to-end without incurring cost or introducing provider-specific dependencies.

**D-M6.2-9 — Severity validated before any SAVEPOINT**

**Decision:** `_validate_severity()` is called before any database SAVEPOINT is opened in `reject_narration_run()` and `reject_narration_segment_asset()`. An out-of-range severity (outside 1–5) raises `InvalidNarrationSeverityError` immediately without touching the database.

**Reasoning:** A SAVEPOINT that fails mid-execution due to a programmer error (bad severity value) would leave the connection in a partial transaction state. Validating inputs at the function boundary, before any side effects, is the correct contract: either the call succeeds atomically or it fails with no side effects.

**D-M6.2-10 — `actor` field; no `reviewer` field**

**Decision:** The human identity field in `narration_review_events` and in all review API signatures is named `actor`, not `reviewer`.

**Reasoning:** Future review events may be generated by automated systems, not human reviewers. `actor` is neutral and correct in both contexts. `reviewer` implies human review only and would require a rename or a second field when machine-generated events are introduced.

---

## Phase 6 M6.3A — Caption and Timing Artifacts

**D-M6.3A-1 — SQLite is canonical; SRT/VTT/JSON are derived exports**

**Decision:** `caption_cues` rows in SQLite are the source of truth. SRT, WebVTT, and JSON files are derived exports written atomically to disk and their SHA-256 hashes stored in `caption_runs`. Any export can be regenerated from the DB at any time by re-running the exporters against the cue rows.

**Reasoning:** Files on disk can be deleted, moved, or corrupted. The DB provides durable, queryable, version-trackable storage with the full provenance context (run ID, narration asset ID, narration text hash, audio SHA-256). Treating files as the canonical source would make reproduction of prior approved artifacts impossible after any filesystem disruption. The DB-canonical pattern matches the narration pipeline's WAV-on-disk / metadata-in-DB contract established in M6.2.

**D-M6.3A-2 — `caption_cues` rows are immutable after insert**

**Decision:** No application-level UPDATE or DELETE is ever issued against `caption_cues`. Human corrections are recorded in `caption_review_events` with the corrected text and reason code. A new caption run (new input hash via a version bump) produces new cue rows.

**Reasoning:** Every human correction to a cue is a future training signal. If cue rows were mutable, the original model output would be overwritten and the signal would be lost. Immutable rows preserve the full "predicted vs. corrected" record permanently. This is the same pattern used by `narration_review_events`: events are appended, not edited.

**D-M6.3A-3 — Supersession is not rejection for caption runs**

**Decision:** When `approve_caption_run()` supersedes a prior active approved caption run, the prior run keeps `status='approved'`. Only `superseded_at` and `superseded_by_run_id` are set on the prior run. The partial unique index (`WHERE status='approved' AND superseded_at IS NULL`) enforces at-most-one active approved run per narration run.

**Reasoning:** Identical to D-M6.2-6 for narration runs. A superseded caption run was legitimately approved; it was replaced by a newer run, not found to be defective. Changing its status would corrupt audit queries and misrepresent operator intent in the training dataset.

**D-M6.3A-4 — `timing_source='estimated'` in M6.3A; no forced alignment**

**Decision:** All caption cue timestamps in M6.3A are set to `timing_source='estimated'`. No Whisper, WhisperX, or other alignment model is used. Timing is allocated proportionally by display-character count across each segment's known `duration_ms`.

**Reasoning:** Forced alignment requires network access or bundled models and introduces a dependency on models that may change their output across versions (breaking reproducibility). Proportional estimation from known audio duration is deterministic, reproducible without external tools, and sufficient for Shorts where caption timing tolerance is coarser than broadcast. The `timing_source` column is a forward-compatibility hook: M6.3B can replace `'estimated'` with `'aligned'` for runs using a real TTS provider without any schema change.

**D-M6.3A-5 — Failed-run rule: no auto-restart; retry requires new input**

**Decision:** If `generate_captions()` finds an existing caption run with `status='failed'` for the same `input_hash`, it raises `FailedCaptionRunError` rather than reusing or restarting the failed run. A retry with the same input requires changing at least one version constant (which produces a different input hash and a fresh run row).

**Reasoning:** A failed run may have partial disk artifacts (temp files, partial cue inserts). Automatically restarting it would require detecting and cleaning up partial state, which is complex and error-prone. Forcing a new input hash ensures a clean slate. The failed run row is preserved as an audit record. This matches the narration pipeline's behavior: `narrate_plan()` resumes a `running` run but not a `failed` one.

**D-M6.3A-6 — Text integrity invariant across segmentation**

**Decision:** `segment_narration_text()` must preserve the invariant `normalize_for_integrity(joined_cue_texts) == normalize_for_integrity(narration_text)` where `normalize_for_integrity` strips whitespace and lowercases. This invariant is also enforced by `validate_caption_cues()`.

**Reasoning:** Caption cues must cover the full narration text without dropping or duplicating words. If cues were missing words, the video's spoken audio and on-screen text would diverge. The integrity check catches any segmentation bug before cues are persisted.

**D-M6.3A-7 — Five version constants bound to `input_hash`**

**Decision:** `CAPTION_SCHEMA_VERSION`, `CAPTION_SEGMENTATION_VERSION`, `CAPTION_TIMING_ALGORITHM_VERSION`, `CAPTION_STYLE_VERSION`, and `CAPTION_EXPORTER_VERSION` are all included in the caption `input_hash`. Any change to any version constant produces a different hash and forces a new caption run row.

**Reasoning:** Captions are part of the published artifact. If the segmentation or timing algorithm changes, the previously generated cues are no longer consistent with the new algorithm. The version-in-hash pattern ensures that algorithm changes are automatically detected and result in a fresh run rather than silently returning stale cues. This mirrors the narration pipeline's hash-over-versions contract.

**D-M6.3A-8 — Handoff via frozen dataclasses, not direct DB queries**

**Decision:** The narration → captions boundary is crossed via `ApprovedNarrationRun` and `ApprovedNarrationSegment` frozen dataclasses. The orchestrator never queries narration tables directly; it receives the assembled handoff from `get_approved_narration_run_full()` in the narration repository.

**Reasoning:** The captions package must not depend on narration DB schema details. Encoding the boundary as typed frozen dataclasses makes the contract explicit and testable. Any change to the narration schema is isolated to `get_approved_narration_run_full()`; the captions orchestrator only sees the typed handoff interface. Frozen dataclasses prevent accidental mutation of handoff data during the pipeline.

---

## Phase 7 — Visual Intelligence Engine

**D-P7-1 — `src/app/scenes/` as root of Visual Intelligence Engine, not `src/app/visual/`**

**Decision:** The Visual Intelligence Engine lives under `src/app/scenes/`, not a top-level `src/app/visual_intelligence/` package.

**Reasoning:** `scenes` is the natural name for the output artifact (scene manifests). Future modules (`asset_strategy`, `providers/`, `analytics`, `optimizer`) slot naturally as siblings inside `scenes/`. The `__init__.py` declares the full Visual Intelligence scope explicitly. Renaming to `visual_intelligence` would create a long import path (`app.visual_intelligence.planner`) with no benefit over `app.scenes.planner`. The `src/app/intelligence/` package is already used for content discovery intelligence (Phase 3) — coexistence is unambiguous.

**D-P7-2 — asset_strategy.py as Phase 8 seam module**

**Decision:** `_plan_assets()` is extracted from `planner.py` into a standalone `asset_strategy.py` module with a public `plan_assets()` entry point.

**Reasoning:** Scene orchestration (`planner.py`) and asset selection strategy (`asset_strategy.py`) are different responsibilities. Phase 8 will introduce real asset providers (stock footage APIs, AI image generation) that enrich `PlannedAssetDraft` objects. Keeping asset planning in its own module means Phase 8 can replace or augment `plan_assets()` without touching scene orchestration logic. The module boundary is explicit, not just a convention.

**D-P7-3 — Immutable input hash for scene manifest idempotency**

**Decision:** `compute_manifest_input_hash()` encodes `caption_run_id`, `narration_run_id`, `plan_id`, all version constants, and the ordered segment tuple list (segment_id, text_hash, duration_ms per segment). The resulting SHA-256 hex digest is UNIQUE-constrained in `scene_manifests`. Calling `get_or_create_scene_manifest()` with the same inputs always returns the existing manifest.

**Reasoning:** Scene manifest planning is deterministic — the same upstream artifacts must always produce the same scene plan. The input hash enforces this at the DB level, preventing duplicate manifests and making the idempotency guarantee verifiable. If any upstream artifact changes (new narration run, updated caption run), the hash changes and a new manifest is created.

**D-P7-4 — Supersession on approve, not on create**

**Decision:** Approving a scene manifest supersedes the previously approved manifest for the same `topic_id`. Supersession happens atomically inside `approve_scene_manifest()`. Rejected manifests are never superseded.

**Reasoning:** A topic should have at most one active approved scene manifest at a time. Supersession is the correct state transition — the old manifest was valid, it is now replaced by a better one, not deleted. Superseded manifests are retained for audit and training-signal purposes. Performing supersession at approval time (not creation time) allows multiple draft manifests to coexist, giving the operator a choice before committing.

**D-P7-5 — Scene-level rejection as training signal, not manifest state change**

**Decision:** `record_scene_rejection()` records a `scene_rejected` review event and does NOT change the manifest's `status`. Manifest status only changes via `approve_scene_manifest()` or `reject_scene_manifest()`.

**Reasoning:** Scene-level rejections are granular operator feedback — they indicate which specific scene needs improvement without invalidating the full manifest. This feedback is a training signal for future planner improvements. If a manifest has one bad scene, the operator can note it without blocking the rest of the manifest from being approved. The immutable event log preserves all corrections for future learning.

**D-P7-6 — Handoff via ApprovedSceneManifest frozen dataclass**

**Decision:** The scenes → rendering boundary is crossed via `ApprovedSceneManifest` (frozen dataclass containing `ApprovedSceneScene` objects with resolved `SceneManifestAsset` lists). Downstream consumers never query scene tables directly.

**Reasoning:** Mirrors the narration → captions handoff pattern. Encoding the boundary as typed frozen dataclasses makes the contract explicit, prevents mutation, and decouples rendering from DB schema details. Any schema change is isolated to `get_approved_scene_manifest_full()`; downstream sees only the typed handoff.

---

## Phase 9 — Publishing & Orchestration Engine

**D-P9-1 — Provider-neutral PublishingProvider Protocol**

**Decision:** Publishing providers implement a `PublishingProvider` `@runtime_checkable` Protocol (not an ABC). The first concrete provider is YouTube; `FakePublishingProvider` is the test double used in all automated tests.

**Reasoning:** Mirrors the `RenderBackend` and `TTSProvider` patterns already established in Phases 6 and 8. Protocol-based design allows any class to satisfy the interface without inheritance, making third-party provider adapters easier to add.

**D-P9-2 — OAuth credentials: path-only references, never secret values in SQLite**

**Decision:** The YouTube adapter reads OAuth secrets from files specified by `YOUTUBE_CLIENT_SECRETS_PATH` and `YOUTUBE_CREDENTIALS_PATH` environment variables. Only the paths are referenced in code. Token values (client secret, refresh token, access token) are NEVER stored in SQLite, logs, provider metadata, review events, hashes, or any reproducibility record.

**Reasoning:** Storing credentials in SQLite would make the database a secret store — a significant security risk. File-based credentials follow the established OAuth installed-application flow and can be managed via OS keychain, secret managers, or mounted secrets without any code change.

**D-P9-3 — Supersession via fields, not status**

**Decision:** `superseded_at` and `superseded_by_id` are dedicated fields on `publishing_plans`. A superseded plan retains its `draft`/`approved`/`rejected` status; only the supersession fields change.

**Reasoning:** Supersession and approval/rejection are orthogonal concerns. A plan can be superseded while still in `draft` (when a better version is created). Conflating these into a single status field would require a `superseded_draft`, `superseded_approved`, etc. explosion. Separate fields keep the state machine simple and queries straightforward.

**D-P9-4 — Dry-run as safe default; live publishing requires six explicit gates**

**Decision:** `start_publishing_job()` defaults to `dry_run=True`. Live provider execution requires all of: live-publishing enablement, configured credentials, an approved plan, an approved render, verified output hash, and explicit non-dry-run flag. Provider selection is also explicit and never silently switched.

**Reasoning:** The cost of an accidental live upload (public YouTube video, consumed quota) is irreversible. Requiring multiple explicit gates prevents any single misconfiguration from causing an accidental publish. This mirrors the `ACE_TTS_LIVE_ENABLED` pattern from Phase 6.

**D-P9-5 — retry_scheduled is a transitional state, not a blocking active state**

**Decision:** `start_publishing_job()` only blocks on `queued` or `running` active jobs. A `retry_scheduled` job is transitional — it signals that the previous failed attempt is being replaced — and does not block a new job from being created.

**Reasoning:** The retry flow is: `failed` → `retry_scheduled` (mark old job) → create new `queued` job → run. If `retry_scheduled` blocked new job creation, the retry flow would deadlock. The `get_active_publishing_job()` function returns `retry_scheduled` for reporting purposes, but `start_publishing_job()`'s duplicate-prevention guard only applies to genuinely concurrent jobs.

**D-P9-6 — provider_video_id pending placeholder for partial uniqueness**

**Decision:** When creating a `Publication` record before the upload begins, `provider_video_id` is set to `__pending_job_{job_id}__`. After upload succeeds, it is updated to the real provider-assigned ID via a raw SQL UPDATE.

**Reasoning:** The `publications` table has a partial unique index on `(provider, provider_video_id) WHERE deleted_at IS NULL` to prevent duplicate live publications. Storing an empty string as the initial `provider_video_id` would violate this constraint on the second attempt (e.g. retry). A job-ID-scoped placeholder is guaranteed unique per job and is replaced atomically before any downstream reads.

---

## Phase 11: Learning & Optimization Engine decisions

**D-P11-1 — No automatic optimization; recommendations are read-only artifacts**

**Decision:** The engine never mutates any production data. It produces `optimization_recommendations` rows that a human must explicitly `accept` or `reject`. No code path in `src/app/learning/` modifies any table outside the four Phase 11 tables (`learning_runs`, `optimization_recommendations`, `recommendation_review_events`, `learning_run_generator_results`). Acceptance of a recommendation does not modify any upstream engine table (scripts, narration, scenes, rendering, publishing).

**Reasoning:** Automatic prompt mutation or parameter changes based on analytics signals would make production behavior non-reproducible and bypass the human approval gates that every other engine enforces. An observation engine that can only observe, attribute, measure, explain, and recommend is safe to run repeatedly; one that can act is not.

---

**D-P11-2 — Three-factor confidence scoring, not a single metric**

**Decision:** Confidence is the average of three capped sub-scores: volume (log2 of deduplicated snapshot count, cap 1.0), effect (gap/threshold ratio, cap 1.0), and consistency (unique period diversity, cap 1.0). All three have equal weight. The combined score maps to `low/medium/high` at thresholds 0.4 and 0.7. The returned score is a **heuristic signal strength**, not a statistical confidence interval — it reflects the quality and breadth of observational evidence, not a probability of a hypothesis being true. A single independent observation period yields zero consistency contribution (not a neutral 0.5). Duplicate snapshot IDs across multiple evidence items are deduplicated before computing the volume factor.

**Reasoning:** Any single metric misleads. High volume with a negligible effect is weak evidence. A large effect on a single data point is unreliable. Either alone could produce high confidence for an unsupported recommendation. The three-factor structure prevents each of these failure modes.

---

**D-P11-3 — Evidence stored as JSON blob, not normalized rows**

**Decision:** `optimization_recommendations.evidence_json` stores the full `EvidenceItem` list as a JSON array. Evidence is not stored in a separate normalized table.

**Reasoning:** Evidence is immutable once a recommendation is created — it is a snapshot of the aggregate values that justified the recommendation. Normalizing it would add joins without enabling any mutable update. A JSON blob is simpler, faster to read, and sufficient for the human-reviewable audit trail.

---

**D-P11-4 — SHA-256 hashes for all three entity types**

**Decision:** `learning_runs`, `optimization_recommendations`, and `recommendation_review_events` each have a dedicated `input_hash` (SHA-256). For recommendations, the hash incorporates the sorted `snapshot_ids` from all evidence items.

**Reasoning:** Deterministic hashes allow replay detection and enable future deduplication across runs. Sorting snapshot IDs before hashing ensures that the hash is stable regardless of the order in which evidence items were appended.

---

**D-P11-5 — Generator-level exception isolation**

**Decision:** `generate_all_recommendations()` wraps each of the six generators in its own `try/except`. A generator that raises does not abort the analysis run — the remaining generators still execute.

**Reasoning:** A defect in one generator (e.g., a metric with unexpected data shape) should not suppress potentially valid recommendations from the other five. Each generator is independent, and their outputs are independent. Isolation makes the engine more robust without hiding bugs — the exception is still logged.

---

**D-P11-6 — AnalyticsHandoff is the only cross-phase input; no analytics tables are joined directly**

**Decision:** `orchestrator._build_handoff_from_db()` assembles an `AnalyticsHandoff` from `analytics_snapshots` and `analytics_aggregates` and passes it to generators. Generators call `_get_lifetime_aggregate(conn, publication_id, metric_name)` which queries `analytics_aggregates` directly — but they do not access any other Phase 10 table, and they never write to Phase 10 tables.

**Reasoning:** The `AnalyticsHandoff` contract was defined in Phase 10 as the explicit Phase 11 handoff interface. Consuming it preserves the phase boundary. Direct DB queries for aggregates (not raw snapshots) are permitted because aggregates are the unit of analysis — raw snapshots are opaque to Phase 11.

---

**D-P11-7 — Evidence classification is always observational in Phase 11; experiment_id alone does not qualify**

**Decision:** `_classify_evidence()` always returns `observational`. The presence of `experiment_id` on an `AnalyticsHandoff` does not trigger `controlled_experiment` classification. A `controlled_experiment` classification requires validated A/B experiment attribution with explicit treatment/control semantics — which Phase 11 does not implement. The `evidence_classification` field is extensible for future phases.

**Reasoning:** Misclassifying observational data as experimental would mislead downstream consumers about the epistemological status of a recommendation. Observational data cannot support controlled-experiment claims regardless of whether an experiment_id happens to be present in the handoff.

---

**D-P11-8 — Recommendation strength: exploratory vs actionable**

**Decision:** Each recommendation carries `recommendation_strength: exploratory | actionable`. A recommendation is `actionable` only when confidence_score ≥ 0.4 AND the evidence contains ≥ 2 unique snapshot IDs. All other recommendations are `exploratory` (insufficient evidence; hypothesis only). The thresholds are named constants (`MIN_CONFIDENCE_ACTIONABLE`, `MIN_UNIQUE_SNAPSHOTS_ACTIONABLE`) and included in the SHA-256 hash payload.

**Reasoning:** Presenting every recommendation with equal weight is misleading. A recommendation backed by a single data point in one period is a hypothesis worth watching, not a call to action. Separating exploratory from actionable gives reviewers a clear signal about which recommendations have earned priority attention.

---

**D-P11-9 — Causal language is prohibited in observational recommendation text**

**Decision:** Recommendation `title` and `explanation` fields must not contain causal claims (causes, increases, decreases, improves, reduces, leads to, results in, because of). All recommendation text must use associative language (associated with, observed alongside, correlated with, whether X responds to Y). Contract tests enforce this at every generated recommendation.

**Reasoning:** Observational data from a single channel is not a controlled experiment. Causal claims in recommendation text would misrepresent the epistemological status of the evidence and could lead operators to over-invest in unvalidated hypotheses.

---

**D-P11-10 — Generator failures yield partial run status; failed generators do not supersede prior recommendations**

**Decision:** When some generators succeed and some fail during `analyze_publication()`, the learning run status is `partial` (not `failed`). A failed generator records a `GeneratorResult` with `status='failed'` and an error message in `learning_run_generator_results`. It does not suppress recommendations from successful generators, and it does not supersede any prior active recommendation for its domain.

**Reasoning:** A defect in one generator should not erase valid recommendations from the other five. Partial failure is distinguishable from total failure. Keeping the previous recommendation active when a generator fails prevents regression in recommendation coverage.

---

**D-P11-11 — Supersession is not rejection**

**Decision:** A recommendation is superseded when a new learning run produces a recommendation for the same `(topic_id, publication_id, domain, subsystem, measure)` key with a different `input_hash`. The old row receives `status='superseded'`, `superseded_at`, and `superseded_by_id`. It is never deleted. Supersession is system-initiated only. Human operators may only `accept` or `reject`; they cannot supersede.

**Reasoning:** A superseded recommendation was valid when created — its evidence has simply been updated. Keeping the history enables auditing of how recommendations evolved over time. Conflating supersession with rejection would destroy the attribution chain.

---

**D-P11-12 — Phase 11 does not ingest upstream human-review signals**

**Decision:** Phase 11 consumes only `AnalyticsHandoff` (Phase 10 output). Human review signals from upstream phases (script approval/rejection, narration review events, scene manifest decisions, render approval, publishing review) are not read by Phase 11 and are not used to weight or filter recommendations.

**Reasoning:** Upstream review signals represent qualitative production decisions, not quantitative performance outcomes. Mixing them with analytics-derived recommendations without a principled integration model would produce confounded results. This integration is deferred to a future phase where the signals can be properly attributed.

---

**D-P11-13 — ReviewedOptimizationHandoff is the frozen Phase 12 input boundary**

**Decision:** `ReviewedOptimizationHandoff` is a frozen Pydantic model (immutable after construction) that bundles the learning run metadata, accepted/rejected/pending recommendations with their review histories, generator results, and version provenance. Phase 12 MUST consume this handoff as its sole input from Phase 11. It must not query raw learning tables or apply recommendations automatically.

**Reasoning:** Defining an explicit frozen handoff at the phase boundary prevents Phase 12 from depending on internal Phase 11 implementation details and ensures that the hand-off contract is testable in isolation.

---

## Phase 12 — Media Operations Control Plane

**D-P12-1 — Permanent identity hierarchy: workspace ≠ channel ≠ platform ≠ platform_account ≠ credential_profile**

**Decision:** Five distinct identity concepts are never collapsed: `cp_workspaces` (the top-level organizational unit), `cp_channels` (a branded content channel within a workspace), `cp_platforms` (a platform type registry entry, e.g. "youtube"), `cp_platform_accounts` (a specific account on a platform connected to a channel), and `cp_credential_profiles` (a credential set referenced by vault pointer). These are separate tables with FK relationships, never merged or aliased. Additionally, these are distinct from the Phase 3 intelligence `channels` table, which tracks discovery/scoring data — that table is never referenced by the CP layer.

**Reasoning:** Collapsing identity concepts leads to schema rigidity and data integrity failures as the system grows (one channel can have multiple platform accounts; one workspace can have many channels; credentials are reusable across accounts). The Phase 3 `channels` name collision was resolved by prefixing all Phase 12 tables with `cp_`.

---

**D-P12-2 — Credential profiles store only external_ref vault pointers, never secrets**

**Decision:** `cp_credential_profiles` stores `external_ref` (a string pointer to the secret in an external vault, e.g. HashiCorp Vault or AWS Secrets Manager) and safe metadata (`display_name`, `credential_type`, `status`, `expires_at`). It never stores OAuth tokens, refresh tokens, API secrets, passwords, or any credential material.

**Reasoning:** Storing credential material in the application database creates a high-value exfiltration target, complicates rotation, and violates least-privilege. The external vault pattern ensures that even full database compromise does not expose live secrets.

---

**D-P12-3 — Automation levels: MANUAL is the default and most restrictive**

**Decision:** Three automation levels exist: `manual` (all operations require explicit human approval), `supervised` (system can propose, human must approve), `autonomous` (system can act without approval). The effective level for any operation is the minimum (most restrictive) across the workspace policy, channel policy (if any), and platform_account policy (if any). When no policy is set for a scope, that scope contributes `manual` to the resolution. `resolve_effective_level()` always returns `manual` when no policies exist.

**Reasoning:** Safe-by-default automation prevents runaway automation when policies are absent or misconfigured. Operators must explicitly grant autonomy; the system cannot accidentally become autonomous.

---

**D-P12-4 — In-process event bus: durable, idempotent, append-only, replay-safe**

**Decision:** The control plane event bus uses `cp_events` (one row per event, append-only) and `cp_event_processing` (one row per handler per event, with `UNIQUE(event_id, handler_key)` constraint). Handlers are registered in-process via `register_handler()`. Dispatch is synchronous. On failure, the existing `cp_event_processing` row is updated in place (attempt count incremented) rather than inserting a new row. After `MAX_DELIVERY_ATTEMPTS` (3), the row is marked `dead_lettered`. Completed or dead-lettered rows are never re-processed.

**Reasoning:** The UNIQUE constraint provides idempotency guarantees — replaying the same event cannot double-deliver to a completed handler. Updating in place (rather than inserting) on retry avoids UNIQUE violations while preserving the attempt audit trail. Dead-lettering after 3 attempts prevents infinite retry loops for persistently failing handlers.

---

**D-P12-5 — Structured workflow engine: no eval, no arbitrary code**

**Decision:** Workflows are defined as: `trigger_event_type` (string) + `conditions` (list of `{field, operator, value}`) + `actions` (list of `{action_type, params}`). Conditions use dot-notation field access against the event payload and support 8 operators: `equals`, `not_equals`, `greater_than`, `less_than`, `in`, `not_in`, `exists`, `boolean`. Actions are one of 6 types: `pause_account`, `resume_account`, `notify`, `update_policy`, `queue_review`, `trigger_workflow`. Conditions and actions are validated at creation time against these allowlists. No `eval()`, no `exec()`, no arbitrary Python or SQL injection is possible.

**Reasoning:** Allowing arbitrary code in workflow definitions would make the system exploitable via malicious workflow creation. The allowlist approach provides rich automation capability while maintaining a fully auditable, safely serializable workflow definition.

---

**D-P12-6 — Experiments are immutable once activated**

**Decision:** `EXPERIMENT_IMMUTABLE_STATUSES = frozenset({"active", "concluded", "cancelled"})`. Any attempt to modify an experiment (add variants, re-activate, change config) when its status is in this set raises `ExperimentAlreadyActiveError`. `conclude_experiment()` requires `status == "active"` or raises `ExperimentNotActiveError`. Variant assignment is deterministic hash-based and idempotent via `get_or_create_assignment()`.

**Reasoning:** Modifying an active experiment's definition invalidates already-collected assignments and corrupts causal attribution. Immutability once active is the standard practice in A/B testing platforms to ensure result validity.

---

**D-P12-7 — Budget enforcement: three-tier check with warn/pause/block actions**

**Decision:** `check_budget()` checks three tiers in order: workspace-scoped budget, channel-scoped budget, and platform_account-scoped budget. For each applicable active budget policy, it computes the spend in the current period and compares against the `limit_usd`. At `BUDGET_WARNING_THRESHOLD` (0.8) of the limit, a warning is appended to the result. At the limit, the configured `action` determines behavior: `warn` (warning only), `pause` (warning + recommended pause), `block` (raises `BudgetExceededError`). Budget periods: `daily`, `weekly`, `monthly`.

**Reasoning:** Three-tier checking ensures that channel-level or account-level overspend is caught even if the workspace aggregate is within budget. The `block` action with a typed error allows callers to enforce hard stops before committing spend.

---

**D-P12-8 — All CP writes carry an actor field**

**Decision:** Every create/update operation in the control plane accepts an `actor: str` parameter (CLI user, system process, API caller). This field is persisted in the relevant table (e.g. `cp_workspaces.created_by`, `cp_channels.created_by`, `cp_automation_policies.created_by`). No write bypasses actor attribution.

**Reasoning:** Actor attribution is the prerequisite for future RBAC (Role-Based Access Control) implementation. Adding it now to all writes costs nothing architecturally and avoids a future migration that would require retroactively attributing historical mutations.

---

**D-P12-9 — cp_ prefix for all Phase 12 tables to avoid Phase 3 name collision**

**Decision:** All 22 Phase 12 tables use the `cp_` prefix: `cp_organizations`, `cp_workspaces`, `cp_channels`, `cp_platforms`, etc. The Phase 3 `channels` table is not renamed and not referenced by the CP layer.

**Reasoning:** Phase 3's `channels` table predates the CP concept and tracks discovery/opportunity intelligence data — it is semantically distinct from the CP `cp_channels` identity table. Renaming Phase 3's table would be a breaking migration across hundreds of tests. The prefix cleanly namespaces the new layer without touching existing tables.

---

**D-P12-10 — Operation executions use idempotency keys; DuplicateIdempotencyKeyError on collision**

**Decision:** `cp_operation_executions` has a `UNIQUE(idempotency_key)` constraint. `start_operation()` checks for an existing row with the same key before inserting; if found, it returns the existing record unchanged (idempotent retry). If the caller passes a custom `idempotency_key` and the key already exists with a different operation type, `DuplicateIdempotencyKeyError` is raised. Default idempotency keys are computed from `(operation_type, workspace_id, timestamp-truncated-to-minute)`.

**Reasoning:** Idempotent operation start prevents duplicate operations when a caller retries after a network failure. The typed error on key collision lets callers distinguish "this exact operation already ran" from "a different operation used this key."

---

**D-P12-11 — Three additional identity layers: organization, publishing profile, analytics identity**

**Decision:** During the Phase 12 production-readiness review, three identity concepts were added to the v18 DDL: `cp_organizations` (top-level owner boundary above workspace), `cp_publishing_profiles` (account-scoped publishing defaults per platform account), and `cp_analytics_identities` (provider-side analytics identity per platform account, unique per `(platform_account_id, analytics_provider_key)`). These bring the total to 22 `cp_` tables. The schema version was not bumped (still v18); the tables were added in-place before commit.

**Reasoning:** The original v18 model left a gap in the identity hierarchy at the top (no organization concept above workspace) and at the account level (no explicit record of publishing preferences or analytics-provider identity). Without `cp_organizations`, multi-tenant and white-label scenarios have no owner boundary above workspace. Without `cp_publishing_profiles`, per-account publishing defaults must be passed through every operation. Without `cp_analytics_identities`, the analytics pipeline cannot distinguish which provider-side account is the source of metrics when a single platform account connects to multiple analytics providers.


---

**D-P14-1 — Single centralized typed API client as the only fetch() point**

**Decision:** All browser→backend communication goes through `frontend/src/api/client.ts`. No component or page may call `fetch()` directly. Architecture tests enforce this at CI time by scanning all non-test TypeScript source files for raw `fetch(` calls.

**Reasoning:** A single entry point enables: (1) consistent header injection (dev actor, future JWT); (2) uniform error handling and HTTP timeout; (3) typed return values for every endpoint via a single import; (4) easy Phase 15 swap of the dev actor for real auth without touching components.

---

**D-P14-2 — Dev actor header is DEV-ONLY and centralized**

**Decision:** The `X-Dev-Actor: dev:studio-user` header is a single named constant in `client.ts`, with both inline comments and the constant name itself marking it as development-only. It is replaced by `Authorization: Bearer <token>` in Phase 15.

**Reasoning:** Centralizing the header means Phase 15 changes exactly one file. Explicit labeling prevents the header from being treated as a permanent authorization mechanism. Having it in one place makes it impossible to miss in a security review.

---

**D-P14-3 — MSW v2 with absolute URLs for test HTTP interception**

**Decision:** All MSW handlers in the test suite use absolute `http://localhost:5173/api/v1/...` URLs. The jsdom environment is initialized with `url: 'http://localhost:5173'` so that `window.location.origin` resolves correctly and `new URL('/api/v1/...', window.location.origin)` in the client produces the expected origin.

**Reasoning:** MSW's `setupServer` (Node.js) cannot resolve relative paths against a document origin the way a real browser would. Without an explicit jsdom URL, `window.location.origin` is empty/null and all handler patterns would fail to match, causing every test to time out. Absolute URLs with a consistent origin are the minimal fix that requires no changes to the client under test.

---

**D-P14-4 — FastAPI routes remain thin transport; no business logic in routes**

**Decision:** FastAPI route handlers call `ApplicationService` methods and return typed Pydantic models. No domain logic (validation, state transitions, policy evaluation, authorization beyond the existing auth hook) is permitted in routes.

**Reasoning:** The `ApplicationService` facade (Phase 13) is the single coordinating boundary above the Control Plane and domain engines. Duplicating or fragmenting business logic in routes would create a maintenance split and undermine the typed command/query bus contract established in Phase 13.

---

## Phase 15: Deployment, Infrastructure & Production Operations (D-P15-1–D-P15-9)

**D-P15-1 — Argon2id via pwdlib[argon2], not bcrypt or PBKDF2**

**Decision:** Password hashing uses `PasswordHash((Argon2Hasher(),))` from `pwdlib[argon2]`. bcrypt and PBKDF2 were not adopted.

**Reasoning:** Argon2id is the winner of the Password Hashing Competition and is memory-hard (resistant to GPU/ASIC brute-force). bcrypt lacks memory hardness; PBKDF2 lacks memory-hardness and is vulnerable to massively parallel attacks. pwdlib provides a clean, dependency-minimal wrapper with algorithm negotiation and rehash detection built in.

---

**D-P15-2 — Refresh tokens stored as SHA-256 hash only; raw token returned once**

**Decision:** When a refresh token is issued, the raw 64-char hex value is returned to the client and then discarded. Only its SHA-256 hash is persisted in `auth_refresh_tokens.token_hash`. Lookup at refresh time hashes the incoming raw token and queries by hash.

**Reasoning:** A stolen DB backup would yield no usable refresh tokens. The cost is one SHA-256 operation per refresh (microseconds). This follows the same principle as password hashing: the server needs to verify the value, not reproduce it. Raw tokens must never appear in logs, backups, or the DB.

---

**D-P15-3 — RQ with JSONSerializer; pickle forbidden**

**Decision:** The RQ job queue uses `rq.serializers.JSONSerializer` exclusively. The default pickle serializer is never used for application jobs. Queue payloads contain only safe primitive identifiers (pipeline_id, stage, actor, workspace_id). Workers reload canonical state from PostgreSQL via `ApplicationService`.

**Reasoning:** Pickle deserialization is an arbitrary code-execution vector. A compromised message in Redis could execute attacker-controlled code on the worker host. JSON payloads are safe and auditable. Pushing data through the queue instead of state references would make payloads large and stale; pulling from PostgreSQL is correct.

---

**D-P15-4 — Stage-class A/B/C with fail-closed classification**

**Decision:** Pipeline stages are classified A (local/deterministic), B (live AI/TTS provider), or C (live publishing). Unknown stages classify as C. `ProviderBoundary.check_stage()` raises `ProviderBoundaryError` if the required gate is not met.

**Reasoning:** An explicit class system makes the security boundary auditable and testable. Classifying unknowns as C (most restrictive) ensures that any new stage added without an explicit classification is blocked in CI and staging until the operator consciously classifies it. This is the same principle as deny-by-default firewalls.

---

**D-P15-5 — Structlog with _redact_sensitive processor; no allowlist approach**

**Decision:** The log processor blocklist (`_SENSITIVE_KEYS`) contains 27 key patterns. Any key whose `.lower()` is in the set is replaced with `"<redacted>"` before emission. An allowlist (log only known-safe keys) was considered but rejected.

**Reasoning:** A blocklist applied to every event dict requires no per-callsite discipline. An allowlist would require every log call to pre-filter its dict, which is error-prone and requires developer training to maintain. The blocklist is a defense-in-depth measure; the primary defense is that sensitive values should not reach log calls at all. The 27-key set is broad enough to catch the most common mistakes without being so broad it suppresses useful diagnostic data.

---

**D-P15-6 — Isolated Prometheus CollectorRegistry, not the global default**

**Decision:** All metrics are registered on a custom `CollectorRegistry` instance, not the `prometheus_client.REGISTRY` global.

**Reasoning:** The global registry causes test pollution: metrics registered during one test bleed into subsequent tests and can cause duplicate-registration errors when tests are re-run in the same process. An isolated registry can be created fresh per test module without side effects. The tradeoff is that standard exporters (PushGateway) need the registry passed explicitly, which is one extra argument.

---

**D-P15-7 — Multi-stage Dockerfile; non-root user ace (uid 1000)**

**Decision:** The Dockerfile uses a builder stage (full dev deps) and a runtime stage (slim, no build tools). The runtime image drops to user `ace` (uid 1000) before the entrypoint. The base image is `python:3.13-slim`.

**Reasoning:** The builder stage keeps build tools (gcc, pip, wheel) out of the runtime image, reducing attack surface and image size. Running as a non-root user prevents container breakout escalation in most kernel exploits. UID 1000 is conventional for application users in Alpine/Debian images.

---

**D-P15-8 — CD boundary is intentionally manual; no automated push-to-production**

**Decision:** The GitHub Actions CI workflow builds and tests the Docker image but does not push it to a registry or deploy to production. Production deployment requires manual operator action.

**Reasoning:** Automated push-to-production requires a production environment, credentials, and runbooks that are not yet in place. The cost of an unintended production deploy is higher than the cost of a manual step. The CI/CD split is explicit in the workflow file with a comment block describing the three-step manual process.

---

**D-P15-9 — Backup retention at 14 dumps (6-hour cadence); Redis not backed up**

**Decision:** PostgreSQL is backed up every 6 hours via `pg_dump | gzip`. The script keeps the 14 most recent backups (3.5 days of coverage). Redis is not backed up.

**Reasoning:** Redis is used only as a job-queue transport. Any jobs in-flight during a Redis failure can be re-enqueued from the schedule definitions in PostgreSQL. Redis state is ephemeral by design. PostgreSQL holds all canonical state; 6-hour RPO is acceptable for the current operational scale. 14-backup retention is overridable via `BACKUP_RETAIN_COUNT`.
