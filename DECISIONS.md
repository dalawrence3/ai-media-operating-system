# Decisions

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
