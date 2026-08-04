# Phase 3: Opportunity Intelligence Engine — Design Specification

> **Status:** Implemented — M3.1 through M3.4 complete. 513 tests pass.
> This document describes the design and rationale; the implementation may
> differ in detail from earlier sections. See `TASKS.md` for the canonical
> milestone record and `DECISIONS.md` for architectural decisions.
>
> **Revision:** v2 — incorporates architectural corrections to persistence
> model, scoring reproducibility, decision flow, channel strategy, business
> evaluation, competition scoring, discovery adapters, and production
> capacity. See the closing section for a full summary of changes.

---

## Table of Contents

1. [High-Level Architecture](#1-high-level-architecture)
2. [Persistence Model](#2-persistence-model)
3. [Channel Strategy Foundation](#3-channel-strategy-foundation)
4. [Opportunity Discovery](#4-opportunity-discovery)
5. [Scoring Engine](#5-scoring-engine)
6. [Feasibility and Business Analysis](#6-feasibility-and-business-analysis)
7. [Recommendation Engine](#7-recommendation-engine)
8. [Portfolio Allocation and Production Plan](#8-portfolio-allocation-and-production-plan)
9. [Content Lifecycle](#9-content-lifecycle)
10. [Learning System](#10-learning-system)
11. [Knowledge and Memory](#11-knowledge-and-memory)
12. [Multi-Channel Architecture](#12-multi-channel-architecture)
13. [Future Extensibility](#13-future-extensibility)
14. [Risks](#14-risks)
15. [Implementation Roadmap](#15-implementation-roadmap)
16. [Closing: Changes, Decisions, MVP Scope, and Deferred Capabilities](#16-closing)

---

## 1. High-Level Architecture

### System Identity

The Opportunity Intelligence Engine (OIE) is the strategic decision layer of
the AI Media Operating System. Its responsibility is answering one question
better than any competing system:

> **"What should this media company produce next, in what order, and why?"**

It does not produce content. It does not publish content. It decides what is
worth producing — and it must be able to explain every decision in terms that
are auditable, reproducible, and traceable to real evidence.

### Operating Mode

The OIE respects the same three-mode progression used throughout the system:

| Mode | Behavior in OIE |
|---|---|
| `manual` | Every opportunity promotion to production requires explicit human approval. Phase 3 defaults to this mode for all channels. |
| `supervised` | The system stages a ranked production plan and notifies the operator; human confirms or adjusts before promotion. |
| `autonomous` | Opportunities meeting all configured criteria are promoted automatically; every action is audit-logged; anomaly detection and pause controls are active. |

**Phase 3 implements manual mode only.** The architecture does not
permanently prohibit supervised or autonomous promotion — the data model,
audit trail, and guardrail fields are designed to support them. Autonomous
behavior is not activated until a channel explicitly qualifies under the
same progressive oversight framework used for publishing mode promotion
(Phase 10+).

### Subsystems and Responsibilities

```
┌────────────────────────────────────────────────────────────────────────┐
│                    Opportunity Intelligence Engine                      │
│                                                                        │
│  ┌─────────────────────┐    ┌──────────────────────────────────────┐   │
│  │  Channel Strategy   │    │          Discovery System            │   │
│  │  Foundation         │───▶│  (adapters: manual, YouTube Data     │   │
│  │  (versioned profile,│    │   API; optional: competitor,         │   │
│  │   monetization      │    │   Google Trends)                     │   │
│  │   strategy, maturity│    │                                      │   │
│  │   stage, capacity)  │    └──────────────┬───────────────────────┘   │
│  └─────────────────────┘                   │ observations +             │
│                                            │ source evidence            │
│  ┌─────────────────────┐                   ▼                           │
│  │  Knowledge &        │    ┌──────────────────────────────────────┐   │
│  │  Memory Store       │───▶│  Scoring Engine                      │   │
│  │  (coverage, satura- │    │  (versioned policy, factor registry, │   │
│  │   tion, anomalies)  │    │   missing-data policies,             │   │
│  └─────────────────────┘    │   immutable score records)           │   │
│                             └──────────────┬───────────────────────┘   │
│                                            │ immutable scores           │
│                                            ▼                           │
│                             ┌──────────────────────────────────────┐   │
│                             │  Feasibility & Business Analysis     │   │
│                             │  (brand gates, budget gates,         │   │
│                             │   multi-dimensional cost analysis)   │   │
│                             └──────────────┬───────────────────────┘   │
│                                            │ feasibility verdict        │
│                                            ▼                           │
│                             ┌──────────────────────────────────────┐   │
│                             │  Recommendation Engine               │   │
│                             │  (produce_now / research_further /   │   │
│                             │   monitor / reject — opportunity     │   │
│                             │   quality only)                      │   │
│                             └──────────────┬───────────────────────┘   │
│                                            │ preliminary recommendation │
│                                            ▼                           │
│                             ┌──────────────────────────────────────┐   │
│                             │  Portfolio Allocator                 │   │
│                             │  (capacity slots, content type       │   │
│                             │   distribution, production plan)     │   │
│                             └──────────────┬───────────────────────┘   │
│                                            │ ranked production plan     │
│                                            ▼                           │
│                             ┌──────────────────────────────────────┐   │
│                             │  Mode-Aware Approval Gate            │   │
│                             │  (manual: human confirms             │   │
│                             │   supervised: human reviews plan     │   │
│                             │   autonomous: criteria check + audit)│   │
│                             └──────────────┬───────────────────────┘   │
│                                            │                           │
└────────────────────────────────────────────│───────────────────────────┘
                                             │ approved opportunity
                                             ▼
                               Topic record created → Content Pipeline
                                             (Phase 4+)
```

### Decision Flow

The OIE processes every opportunity through eight sequential stages. No stage
performs the responsibility of another.

```
Stage 1: Discovery and Evidence Collection
  Adapters collect signals. Observations and source evidence stored.
  Deduplication runs. Raw candidates created.

Stage 2: Deterministic Factor Scoring
  Scoring Engine applies versioned policy to collected evidence.
  Each factor scored independently. Missing-data policy applied per factor.

Stage 3: Confidence Calculation
  Confidence computed from source quality, data completeness, freshness,
  and corroboration. Stored with score record. Immutable after creation.

Stage 4: Feasibility, Brand, and Budget Gates
  Brand rules checked. Budget limits applied. Safety rules enforced.
  Blocking factors recorded. Does not modify scores.

Stage 5: Preliminary Recommendation
  Recommendation Engine evaluates opportunity quality only.
  Assigns: produce_now / research_further / monitor / reject.
  Does not consider portfolio state or capacity.

Stage 6: Portfolio and Production-Capacity Allocation
  Portfolio Allocator checks content-type distribution targets.
  Checks available production slots. Assigns portfolio_fit verdict.
  May defer a produce_now recommendation to next cycle if no slot available.
  Does not change the preliminary recommendation — records allocation result
  separately.

Stage 7: Final Ranked Production Plan
  Combines preliminary recommendation, feasibility verdict, and allocation
  result into a ranked, capacity-constrained production plan.
  Every entry in the plan carries its full explanation chain.

Stage 8: Mode-Aware Approval
  In manual mode: operator reviews plan and approves each item.
  In supervised mode: plan is staged; operator confirms or adjusts.
  In autonomous mode (future): criteria verified; approval recorded
  automatically; audit log entry created; anomaly detection active.
```

### Separation of Concerns

| Concern | Owned By |
|---|---|
| Is this opportunity worthwhile? | Recommendation Engine (Stage 5) |
| Does it fit the portfolio? | Portfolio Allocator (Stage 6) |
| Is there a production slot available? | Portfolio Allocator (Stage 6) |
| What is the final ranked order? | Portfolio Allocator (Stage 7) |
| Is it approved for production? | Mode-Aware Approval (Stage 8) |
| What content should it produce? | Content Pipeline (Phase 4+) |

---

## 2. Persistence Model

### Design Principles

The original design placed mutable state, historical evaluations, and
operational logs inside a single `Opportunity` record. This prevents audit,
reproducibility, and independent re-scoring without altering history.

The revised model separates:
- **Enduring identity** — what the content idea is (append-once)
- **Time-sensitive evidence** — what signals were collected and when (append-only)
- **Evaluation results** — what the engine concluded (immutable after creation)
- **Operational events** — what happened to the record (append-only)

JSON blobs are retained where they are appropriate: raw external payloads,
flexible metadata, and non-critical explanatory details. They are replaced
with normalized records wherever operational history must be queryable,
auditable, or reproducible.

### Table Definitions and Cardinality

---

#### `discovery_runs`

One record per execution of a discovery cycle for a channel.

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| channel_id | FK → channels | |
| adapter_name | TEXT | `manual` / `youtube_data_api` / `competitor_research` / `google_trends` |
| query_parameters_json | JSON | Adapter-specific query config snapshot |
| quota_cost | INTEGER | API units consumed (0 for manual) |
| status | TEXT | `running` / `completed` / `failed` / `partial` |
| candidate_count | INTEGER | Raw candidates produced |
| started_at | TIMESTAMP | |
| completed_at | TIMESTAMP | NULL if still running |
| error_message | TEXT | NULL on success |

**Cardinality:** One channel has many discovery runs. Each run belongs to
one channel and one adapter. Multiple runs may contribute evidence to the
same opportunity.

---

#### `opportunities`

The enduring normalized content idea. Represents the **what**, not the
evaluation results. Core fields are effectively append-once; only
human-editable fields (title, summary) may be updated.

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| channel_id | FK → channels | |
| normalized_topic | TEXT | Lowercased, stemmed topic key for dedup matching |
| title | TEXT | Human-editable proposed title |
| topic_summary | TEXT | 1–3 sentence description; human-editable |
| category | TEXT | Primary niche category |
| subcategory | TEXT | Optional finer classification |
| format_recommendation | TEXT | `short` / `long_form` / `both` / `content_package` / `undecided` |
| strategic_role | TEXT | `discovery` / `monetization` / `subscriber_growth` / `authority` / `retention` / `affiliate_conversion` / `experimentation` |
| current_lifecycle_state | TEXT | Denormalized current state for efficient querying; source of truth is `opportunity_state_events` |
| promoted_to_topic_id | FK → topics | NULL until approved and promoted |
| expires_at | TIMESTAMP | NULL = no expiry; set for monitoring opportunities |
| created_at | TIMESTAMP | UTC timestamp of first discovery |
| updated_at | TIMESTAMP | Updated when human edits title/summary only |

**Cardinality:** One channel has many opportunities. One opportunity belongs
to exactly one channel. One opportunity may have many observations, many
scores, and many state events.

---

#### `opportunity_observations`

Time-sensitive source data collected for an opportunity. Each row represents
one evidence-collection event from one adapter. Immutable after creation.

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| opportunity_id | FK → opportunities | |
| discovery_run_id | FK → discovery_runs | Which run collected this |
| adapter_name | TEXT | Source adapter |
| collected_at | TIMESTAMP | When signals were fetched |
| signal_age_days | REAL | Age of the underlying signal at collection time |
| is_stale | BOOLEAN | Set to TRUE when signal_age_days > channel staleness threshold |
| source_quality_tier | TEXT | `high` / `medium_high` / `medium` / `variable` |
| raw_payload_json | JSON | Unmodified API response; acceptable as JSON |
| collection_notes | TEXT | Free text; e.g. rate-limit warnings |

**Cardinality:** One opportunity has many observations over time. Each
observation belongs to one opportunity and one discovery run.

---

#### `opportunity_source_evidence`

Individual typed data points extracted from observations. These are the
inputs to the scoring engine. Structured as rows rather than JSON so that
evidence can be queried, compared, and audited independently.

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| observation_id | FK → opportunity_observations | |
| opportunity_id | FK → opportunities | Denormalized for query convenience |
| evidence_type | TEXT | `view_count` / `video_count_in_niche` / `trend_score` / `incumbent_subscriber_count` / `top_video_age_days` / `manual_demand_note` / … |
| evidence_value | REAL | Numeric value (NULL if not numeric) |
| evidence_text | TEXT | Text value (NULL if not text) |
| evidence_unit | TEXT | e.g. `views` / `normalized_0_1` / `days` / `count` |
| source_label | TEXT | e.g. `youtube_data_api:search.list` / `manual:operator` |
| collected_at | TIMESTAMP | Inherited from observation |
| is_stale | BOOLEAN | Inherited from observation |

**Cardinality:** One observation has many source evidence rows. The Scoring
Engine queries evidence rows for a given opportunity to compute each factor.

**Why not JSON:** Source evidence is the audit trail for every score. An
analyst must be able to query "which view counts informed this score" without
parsing JSON blobs. Keeping evidence as rows enables this. The raw payload
remains in `opportunity_observations.raw_payload_json` for full fidelity.

---

#### `scoring_policies`

Versioned, immutable definitions of the scoring formula parameters. A score
record references the policy version that produced it, enabling full
reproducibility.

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| version | TEXT | Semver string e.g. `1.0.0` |
| description | TEXT | Human-readable summary of what changed |
| factor_weights_json | JSON | Per-factor weights; acceptable as JSON (not critical history) |
| thresholds_json | JSON | Recommendation thresholds |
| missing_data_policies_json | JSON | Per-factor missing-data handling rules |
| normalization_baselines_json | JSON | Baseline values for normalization |
| applicable_maturity_stages | TEXT | Comma-separated list or `all` |
| active_from | TIMESTAMP | When this version became active |
| superseded_at | TIMESTAMP | NULL = currently active; set when a newer version activates |
| created_by | TEXT | Operator who activated this version |

**Policy activation:** A new policy version is activated by an explicit
operator command (`ace intelligence policy activate <version>`). The
previous version's `superseded_at` is set to the activation timestamp.
Old versions remain in the table and are never deleted — they are the
audit record for all historical scores.

**Cardinality:** One policy is referenced by many score records. Changing
a policy creates a new row; it does not modify existing rows.

---

#### `channel_profile_versions`

Immutable snapshots of the channel's full configuration at a given point in
time. Scores and recommendations reference this, not the live channel profile.
This ensures that updating a channel's strategy does not retroactively alter
the meaning of previous decisions.

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| channel_id | FK → channels | |
| version | INTEGER | Monotonically incrementing per channel |
| profile_snapshot_json | JSON | Full snapshot of channel config at this version; acceptable as JSON (not critical history — the relational channel record is the live truth) |
| monetization_strategy_id | FK → channel_monetization_strategies | Active strategy at this version |
| maturity_stage | TEXT | `validation` / `growth` / `monetization` / `optimization` / `scaling` |
| active_from | TIMESTAMP | |
| superseded_at | TIMESTAMP | NULL = currently active |
| created_by | TEXT | |

**Cardinality:** One channel has many profile versions. Scores reference
the version active at scoring time.

---

#### `opportunity_scores`

Immutable record of one scoring run for one opportunity under one policy
and one channel profile version. Created once; never updated.

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| opportunity_id | FK → opportunities | |
| scoring_policy_id | FK → scoring_policies | |
| channel_profile_version_id | FK → channel_profile_versions | |
| observation_ids_json | JSON | List of observation IDs used as input |
| composite_score | REAL | 0.0–1.0 |
| confidence | REAL | 0.0–1.0 |
| factor_scores_json | JSON | Per-factor breakdown snapshot; acceptable as JSON |
| confidence_breakdown_json | JSON | Per-component confidence factors; acceptable as JSON |
| missing_data_summary_json | JSON | Which factors had missing data and which policy was applied |
| scored_at | TIMESTAMP | |

**Score comparability constraint:** Scores from the same channel, under
the same `scoring_policy_id`, scored within a reasonably similar time
window (configurable; default 90 days), are meaningfully comparable for
ranking purposes. Scores across different channels, different policy
versions, or widely different time windows are not directly comparable and
must not be aggregated into a single cross-channel ranking. Cross-channel
resource allocation is a future account-level concern (see Section 13).

**Reproducibility:** Given `scoring_policy_id`, `channel_profile_version_id`,
and `observation_ids_json`, the score can be reproduced by re-running the
Scoring Engine with the same inputs. Changing the live channel profile or
activating a new policy does not alter this record.

**Cardinality:** One opportunity has many scores over time (each discovery
cycle may produce a new score as evidence refreshes). One score belongs to
one opportunity, one policy version, and one channel profile version.

---

#### `opportunity_recommendations`

Append-only record of each recommendation event. The current recommendation
is the most recent row for a given `opportunity_id`. Old rows are preserved
for audit.

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| opportunity_id | FK → opportunities | |
| score_id | FK → opportunity_scores | Score this recommendation is based on |
| recommendation | TEXT | `produce_now` / `research_further` / `monitor` / `reject` |
| rationale | TEXT | Human-readable deterministic explanation |
| blocking_factors_json | JSON | List of specific gates that prevented a higher recommendation |
| feasibility_verdict | TEXT | `feasible` / `budget_blocked` / `brand_blocked` / `safety_blocked` |
| recommended_at | TIMESTAMP | |
| policy_version | TEXT | Scoring policy version at recommendation time |

**Cardinality:** One opportunity has many recommendation records over time.
The current recommendation is the most recent. Historical recommendations
remain for audit and learning.

---

#### `opportunity_state_events`

Append-only log of lifecycle state transitions. The current state is
denormalized to `opportunities.current_lifecycle_state` for query
performance; the source of truth for history is this table.

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| opportunity_id | FK → opportunities | |
| from_state | TEXT | NULL for the initial `discovered` transition |
| to_state | TEXT | See lifecycle states in Section 9 |
| actor | TEXT | `system` / operator username / `autonomous_engine` |
| reason | TEXT | Free text or structured code e.g. `scored` / `approved_by_operator` |
| operating_mode_at_transition | TEXT | `manual` / `supervised` / `autonomous` |
| created_at | TIMESTAMP | |

**Cardinality:** One opportunity has many state events. Every state change
appends a row; existing rows are never modified.

---

#### `opportunity_approvals`

Append-only record of every approval or rejection decision, including the
full set of checks that were evaluated at decision time. Supports manual,
supervised, and future autonomous modes.

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| opportunity_id | FK → opportunities | |
| recommendation_id | FK → opportunity_recommendations | Which recommendation was acted upon |
| operating_mode | TEXT | `manual` / `supervised` / `autonomous` |
| decision | TEXT | `approved` / `rejected` / `deferred` |
| decided_by | TEXT | Operator name for manual/supervised; `autonomous_engine` for autonomous |
| decision_reason | TEXT | Free text for human decisions; structured code for autonomous |
| criteria_checks_json | JSON | For autonomous mode: all criteria evaluated and their pass/fail; NULL for manual |
| anomaly_flags_json | JSON | Any anomalies detected at decision time |
| promoted_to_topic_id | FK → topics | Set on approval; NULL on reject/defer |
| created_at | TIMESTAMP | |

**Cardinality:** One opportunity may have multiple approval records over
time (e.g. rejected, then reconsidered and approved). The most recent
record is current.

---

### JSON vs Relational: Decision Summary

| Data | Storage | Reason |
|---|---|---|
| Raw API payloads | JSON in `opportunity_observations` | Preserves full fidelity; not operationally queried |
| Factor score breakdown | JSON in `opportunity_scores` | Explanation snapshot; not queried relationally |
| Confidence breakdown | JSON in `opportunity_scores` | Same rationale |
| Scoring policy parameters | JSON in `scoring_policies` | Config snapshot; policy row is the relational anchor |
| Channel profile snapshot | JSON in `channel_profile_versions` | Immutable snapshot; relational `channels` is live |
| Missing data summary | JSON in `opportunity_scores` | Explanatory; not queried relationally |
| Criteria checks (autonomous) | JSON in `opportunity_approvals` | Variable structure per policy version |
| **State transitions** | **Relational rows in `opportunity_state_events`** | Must be queryable; operational history |
| **Approval decisions** | **Relational rows in `opportunity_approvals`** | Must be auditable and queryable |
| **Recommendation events** | **Relational rows in `opportunity_recommendations`** | Must support historical comparison |
| **Source evidence** | **Relational rows in `opportunity_source_evidence`** | Must be queryable per scoring audit |
| **Operating mode transitions** | **Relational rows in `channel_operating_mode_events`** | Must be auditable |

---

## 3. Channel Strategy Foundation

### Design Principle

A channel's strategy is versioned, not live-mutable. Every discovery run,
scoring event, and recommendation references the strategy version that was
active at the time. Changing strategy creates a new version; it does not
retroactively alter prior decisions.

### Channel Identity

The `channels` table (introduced in Phase 1 / ARCHITECTURE.md Phase 3
planned additions) holds the channel's live operational state:

| Field | Notes |
|---|---|
| id | UUID PK |
| platform | `youtube` (future: `instagram`, `tiktok`) |
| channel_name | Human display name |
| platform_channel_id | YouTube channel ID; set after first OAuth link |
| operating_mode | `manual` (default) / `supervised` / `autonomous` |
| current_profile_version_id | FK → channel_profile_versions |
| current_strategy_id | FK → channel_monetization_strategies |
| current_maturity_stage | `validation` / `growth` / `monetization` / `optimization` / `scaling` |
| created_at | |
| updated_at | |

### Channel Profile

The live editable profile. Fields here drive the next version snapshot when
any field changes.

**Niche and Audience:**
- `primary_niche` — Single primary topic area
- `secondary_niches` — Up to 3 related areas
- `excluded_topics` — Topics the channel never covers; checked by brand gate
- `audience_description` — Free text
- `audience_demographics` — Optional: age range, geography, expertise level
- `audience_intent` — `educational` / `entertainment` / `mixed`

**Brand and Voice:**
- `brand_voice` — `authoritative` / `conversational` / `energetic` / `calm` / `humorous`
- `tone_notes` — Free text
- `brand_rules` — Structured list of always/never rules
- `content_style` — `story-driven` / `list-based` / `explainer` / `mixed`

**Content Strategy:**
- `primary_format` — `short` / `long_form` / `both` / `content_package`
- `posting_cadence_per_week` — Target posts per week
- `portfolio_targets` — `{evergreen: 0.60, trending: 0.20, seasonal: 0.10, experimental: 0.10}` (must sum to 1.0)

**Discovery Settings:**
- `allowed_discovery_adapters` — Subset of configured adapters
- `max_candidates_per_run` — Quota guard
- `min_opportunity_score_to_surface` — Minimum score for human review
- `duplicate_similarity_threshold` — 0.0–1.0
- `signal_staleness_days` — Observations older than this are stale

**Publishing Integration:**
- `publishing_mode` — Mirrors the publishing mode from Phase 10
- `mode_qualified_at` — Timestamp of last promotion

### Channel Operating Mode Events

Replaces `mode_history_json`. Each mode change appends a row:

| Column | Notes |
|---|---|
| id | UUID PK |
| channel_id | FK → channels |
| from_mode | Previous mode |
| to_mode | New mode |
| operator | Who made the change |
| reason | Free text |
| qualification_report_json | Snapshot of qualification checks |
| created_at | |

### Channel Monetization Strategy

A versioned entity expressing a combination of business objectives and their
relative priorities. Replaces the single `primary_goal` field.

**Pre-monetization objectives** (used when `monetization_status = pre`):

| Objective | Description |
|---|---|
| `qualified_subscriber_growth` | Growing subscribers who match the target audience |
| `watch_hour_progress` | Accumulating public watch hours toward monetization threshold |
| `returning_viewer_rate` | Building a loyal returning audience |
| `audience_fit` | Ensuring content attracts the right audience, not just any audience |
| `publishing_consistency` | Demonstrating sustainable production cadence |

**Post-monetization objectives** (available when `monetization_status = active`):

| Objective | Description |
|---|---|
| `ad_revenue` | Advertising revenue from long-form and Shorts |
| `rpm_optimization` | Revenue per thousand impressions |
| `contribution_margin` | Revenue minus production cost |
| `affiliate_conversion` | Conversions from affiliate links |
| `sponsorship_potential` | Audience quality attractive to sponsors |
| `digital_product_revenue` | Revenue from owned products |
| `membership_revenue` | Channel membership income |
| `lifetime_content_value` | Long-tail evergreen content compounding over time |
| `audience_growth` | Continued subscriber growth (valid at all stages) |
| `authority_building` | Domain authority and expert positioning |

**Strategy entity fields:**

| Field | Notes |
|---|---|
| id | UUID PK |
| channel_id | FK → channels |
| version | Integer, monotonically incrementing per channel |
| monetization_status | `pre` / `active` |
| objective_weights_json | `{objective: weight, ...}` normalized to sum to 1.0 |
| description | Human explanation of this strategy version |
| active_from | Timestamp |
| superseded_at | NULL = currently active |
| created_by | Operator |

**No fabricated values:** The `objective_weights_json` expresses *priorities*,
not predicted revenues. Revenue estimates are never invented. Before Phase 11
analytics data exists, strategy influences scoring weights and portfolio
targets but produces no revenue predictions.

**How strategy influences downstream decisions:**

- **Scoring:** The active strategy's `objective_weights_json` maps to scoring
  factor weight adjustments. A strategy heavily weighted toward
  `watch_hour_progress` boosts `evergreen_score` weight (long-form, durable
  content builds watch hours). A strategy weighted toward
  `qualified_subscriber_growth` boosts `audience_fit_score` weight.
- **Format selection:** Strategy + maturity stage together inform the
  `format_recommendation` field on new opportunities. Pre-monetization with
  `watch_hour_progress` priority favors `long_form` or `content_package`.
  A `Shorts`-first approach during `validation` stage is valid if
  `audience_fit` is the priority signal.
- **Portfolio allocation:** Strategy weights influence the portfolio target
  percentages. A `lifetime_content_value` strategy skews the evergreen target
  upward. A `sponsorship_potential` strategy may increase the `authority`
  strategic role quota.
- **Analytics (Phase 11+):** The active strategy at publication time is
  recorded alongside analytics data. Learning signals are calibrated against
  the strategy objectives that were active, not the current strategy.

### Channel Maturity Stage

Maturity stage is a versioned strategy input, not a hardcoded behavior
switch. Changing stage creates a new `channel_profile_version`. The stage
influences defaults but does not irreversibly lock any behavior.

| Stage | Description | Typical Priorities |
|---|---|---|
| `validation` | Channel is new; testing audience fit and production process | Audience fit, publishing consistency, low cost per video |
| `growth` | Audience is forming; optimizing for subscriber growth | Subscriber growth, returning viewers, watch hours |
| `monetization` | Approaching or recently passed monetization threshold | Watch hours, subscriber count, audience quality |
| `optimization` | Monetized; optimizing content economics | RPM, contribution margin, evergreen value |
| `scaling` | System is proven; scaling production and channels | Efficiency, cost per video, cross-channel learning |

**Stage effects on OIE decisions:**

| Setting | validation | growth | monetization | optimization | scaling |
|---|---|---|---|---|---|
| Default evergreen target | 40% | 50% | 55% | 65% | 60% |
| Default trending target | 30% | 25% | 20% | 15% | 20% |
| Default experimental target | 20% | 15% | 10% | 10% | 10% |
| Min confidence for produce_now | 0.50 | 0.55 | 0.60 | 0.60 | 0.65 |
| Default format preference | short | both | both | long_form | both |
| Acceptable cost per video | low–medium | low–medium | medium | medium–high | low–medium |
| Automation eligibility | No | No | Supervised only | Supervised | Eligible |

These are defaults from the scoring policy, not hardcoded logic. They can be
overridden per channel.

### Production Capacity Policy

Budget is not the only scarce resource. Each channel has a configurable
capacity policy that the Portfolio Allocator uses to constrain the production
plan to realistic limits.

| Field | Notes |
|---|---|
| channel_id | FK → channels |
| slots_per_week | Total production slots available (default: 3) |
| max_concurrent_productions | Maximum simultaneously active productions (default: 2) |
| review_time_budget_hours_per_week | Total human review hours available (default: 4.0) |
| estimated_review_hours_per_short | Operator review time per Short (default: 0.5) |
| estimated_review_hours_per_long_form | Operator review time per long-form (default: 1.5) |
| estimated_review_hours_per_package | Operator review time per content package (default: 2.5) |
| content_package_capacity_per_week | Max content packages per week (default: 1) |
| trend_reservation_slots | Slots reserved for urgent trend content (default: 1) |

The final production plan never exceeds `slots_per_week` minus
`trend_reservation_slots` for non-trend content, and never exceeds the
review time budget.

---

## 4. Opportunity Discovery

### Responsibility

The Discovery System ingests external signals and produces raw `Opportunity`
candidates with attached observations and source evidence. It does not score,
rank, or recommend. Deduplication happens here before scoring is invoked.

### Adapter Classification

Adapters are classified by their role in Phase 3:

| Adapter | Classification | Reason |
|---|---|---|
| `ManualSignalAdapter` | **Required launch adapter** | Works offline; no quota; operator domain knowledge; always available |
| `YouTubeDataAPIAdapter` | **Required launch adapter** | Official source; quota-managed; core platform signal |
| `CompetitorResearchAdapter` | **Optional adapter** (Phase 3 sub-milestone 3.2b) | Uses YouTube Data API; adds competitor signals; deferrable without blocking MVP |
| `GoogleTrendsAdapter` | **Optional adapter** | Non-YouTube data; brittle rate limits; the OIE functions correctly without it |
| `InternalSuggestionAdapter` | **Future adapter** (Phase 9+) | Requires Phase 11 analytics data; not available in Phase 3 |

**The OIE must function correctly when only `ManualSignalAdapter` and
`YouTubeDataAPIAdapter` are enabled.** Scoring policies must define
missing-data handling for all factors that depend on Google Trends.

No live external API calls occur in automated tests. All adapters must
support an injectable stub/fake client following the same pattern as
`AIProvider` (Phase 2).

### Required Launch Adapters

#### ManualSignalAdapter

- **Input:** Operator provides topic ideas, keyword lists, competitor channel
  IDs, or seasonal notes via CLI
- **Output:** `Opportunity` + `opportunity_observations` with
  `adapter_name = manual` + `opportunity_source_evidence` rows of type
  `manual_demand_note`, `manual_keyword`, `manual_seasonal_flag`
- **Provenance:** Operator ID, input date, free-text notes
- **Quota cost:** 0
- **Failure mode:** None — always available

#### YouTubeDataAPIAdapter

- **Input:** Channel niche keywords, category filters
- **Output:** `Opportunity` candidates + observations with view counts,
  video counts, upload dates, channel statistics
- **Evidence types produced:** `view_count`, `video_count_in_niche`,
  `top_video_age_days`, `niche_channel_subscriber_count`,
  `search_result_position`
- **Quota cost:** 100 units per `search.list` call; tracked per
  `discovery_run`
- **Quota guard:** Run aborts before exceeding `max_candidates_per_run`
  quota budget; remaining quota tracked and stored
- **Provenance:** API name, query string, result rank, collection date,
  video IDs sampled
- **Failure mode:** Graceful degradation — partial results stored with
  `status = partial`; run marked incomplete; operator notified

### Optional Adapters

#### CompetitorResearchAdapter (Phase 3.2b)

- **Mechanism:** For a list of configured competitor channel IDs, queries
  `channels.list` and `videos.list` via YouTube Data API v3.
- **Identifies:** High-performing videos relative to channel subscriber
  baseline; topic patterns in recent uploads
- **Constraint:** Official YouTube API only. No scraping. Do not infer
  competitor revenue.
- **Evidence types produced:** `incumbent_subscriber_count`,
  `incumbent_video_view_count`, `incumbent_channel_authority_tier`,
  `competitor_topic_coverage`
- **Classification note:** The Competition Scoring factor uses this data
  to assess incumbent strength (see Section 5). This is distinct from
  topic saturation.

#### GoogleTrendsAdapter (optional)

- **Mechanism:** `pytrends` or official endpoint
- **Provides:** Rising queries, interest-over-time for niche terms
- **Constraint:** Not a YouTube product. All signals labelled
  `source = google_trends` with a displayed caveat.
- **Rate limits:** Undocumented; requests must be spaced; failures are
  non-fatal
- **Missing-data policy:** When absent, the `trend_velocity` factor uses
  the `reweight_available` policy (see Section 5). The OIE does not
  substitute 0.5 automatically.
- **Evidence types produced:** `trend_interest_score`,
  `trend_is_rising_flag`, `trend_variance_12mo`, `trend_seasonality_flag`

### Deduplication

Every candidate is checked against:
1. Opportunities already in `in_production` or `published` states for this channel
2. All non-archived opportunities for this channel with `current_lifecycle_state`
   not in `{rejected, archived}`
3. Topics already in the Phase 1 `topics` table for this channel

**Method:** Jaccard similarity on stemmed, stopword-removed keyword sets
extracted from `normalized_topic`. Candidates exceeding
`duplicate_similarity_threshold` are linked to the existing record via a
`duplicate_of_opportunity_id` field rather than creating a new opportunity.
The new observation is still stored against the existing opportunity, so
evidence accumulates over time.

**Optional LLM-assisted deduplication:** Off by default. When enabled per
channel, an LLM classification pass runs as a second check for semantic
duplicates (e.g. "5 ways to save money" vs. "how to spend less"). This is
advisory only — a human-editable flag on the opportunity. The deterministic
Jaccard check is always the primary gate.

### Freshness

Observations with `signal_age_days > signal_staleness_days` (channel config)
have `is_stale = TRUE`. The Scoring Engine checks staleness before using
evidence in a score. If only stale observations exist:
- Score proceeds with confidence reduced (stale data policy applied per factor)
- Score explanation notes which factors used stale evidence
- Opportunity is flagged `needs_refresh` in `current_lifecycle_state`

### Source Quality Tiers

| Adapter | Tier | Notes |
|---|---|---|
| `youtube_data_api` | `high` | Official; reflects real platform data |
| `competitor_research` | `medium_high` | Real data; interpretation is indirect |
| `google_trends` | `medium` | Not YouTube-specific; general web signal |
| `manual` | `variable` | Quality depends on operator knowledge and notes |
| `internal_suggestion` | `high` (future) | Based on own historical performance |

Source quality tier contributes to confidence calculation (Stage 3).

---

## 5. Scoring Engine

### Responsibility

The Scoring Engine converts collected evidence into an immutable, versioned,
reproducible score. It is entirely deterministic. No LLM is involved.

### Scoring Policy Versioning

Every scoring run references a `scoring_policy_id`. A policy defines:
- Factor weights (per-factor, per maturity stage defaults)
- Recommendation thresholds
- Missing-data policies per factor
- Normalization baselines (e.g. niche average view count)
- Which factors are mandatory for which recommendation levels

Changing any of these values requires creating a new policy version. Old
policies remain auditable. The Scoring Engine never modifies an existing
`opportunity_scores` row.

**Policy activation flow:**
1. Operator creates a new policy draft (`ace intelligence policy draft`)
2. Operator reviews the diff from the current active policy
3. Operator activates it (`ace intelligence policy activate <version>`)
4. The previous policy's `superseded_at` is set; all future scoring runs
   use the new policy
5. Existing scores remain associated with the policy that produced them

### Scoring Factors

Each factor is computed from typed `opportunity_source_evidence` rows.
The Scoring Engine queries evidence by `evidence_type` for the given
opportunity, using only observations with `is_stale = FALSE` unless
no fresh evidence exists (in which case the missing-data policy applies).

---

#### Factor 1: Trend Velocity (`trend_velocity`)

**What it measures:** Is audience interest in this topic actively rising?

**Evidence types used:**
- `trend_interest_score` (from `GoogleTrendsAdapter`, if available)
- `trend_is_rising_flag` (from `GoogleTrendsAdapter`, if available)
- `view_count` growth pattern across recent videos (from `YouTubeDataAPIAdapter`)

**Computation:**
```
If google_trends evidence is fresh:
  trend_raw = weighted_average(
      trend_interest_score * 0.40,
      youtube_recency_growth_proxy * 0.60
  )
  factor_status = "measured"

If google_trends evidence is stale:
  trend_raw = youtube_recency_growth_proxy
  factor_status = "stale_partial"
  → confidence reduced (stale data)

If google_trends evidence is absent:
  → apply missing-data policy for trend_velocity
  → default policy: reweight_available
     (redistribute trend_velocity weight to demand_score and evergreen_score)
  factor_status = "unavailable"
  → score explanation records: "trend_velocity unavailable; weight rebalanced to demand_score and evergreen_score"
```

**Note:** The `reweight_available` policy means the composite score still
sums to 1.0. It does not silently substitute 0.5 — it openly redistributes
the weight and documents that it did so.

---

#### Factor 2: Audience Demand (`audience_demand`)

**What it measures:** How much ongoing audience demand exists for this topic?

**Evidence types used:**
- `view_count` of top-performing videos (YouTube Data API)
- `manual_demand_note` (operator-supplied; treated as estimated, not measured)

**Computation:**
```
If youtube view evidence is fresh:
  niche_baseline = normalization_baselines_json["niche_avg_view_count"]
  demand_raw = log_normalize(median_top_video_views, niche_baseline)
  factor_status = "measured"

If only manual demand note exists:
  demand_raw = MANUAL_NOTE_PRIOR_MAP[note_category]
  factor_status = "estimated"
  → confidence reduced (estimated, not measured)

If no evidence:
  → missing-data policy: require_research for produce_now recommendations
    (factor is mandatory for produce_now; monitor/research_further can proceed)
  factor_status = "unavailable"
```

**Note:** Keyword search volume, if available from a configured third-party
provider, contributes as `external_search_volume` evidence type with
`source_label = "provider:<name>"`. It is never scraped. If unavailable,
it is absent from evidence — not substituted.

---

#### Factor 3: Competition Landscape (`competition_landscape`)

**What it measures:** How difficult is the competitive environment for this
topic, and is there a realistic path to competitive visibility?

This factor is intentionally multi-signal. A high view count concentration
among a few videos is not automatically a bonus — it may indicate either
a content gap (weak incumbent) or a dominated market (strong incumbent).
The design preserves this ambiguity and requires explanation.

**Evidence types used:**
- `video_count_in_niche` — topic saturation signal
- `incumbent_subscriber_count` — incumbent channel strength
- `incumbent_channel_authority_tier` — categorized authority
- `top_video_age_days` — content freshness
- `search_result_position` — where new content can realistically appear

**Sub-signals computed:**

```
topic_saturation = log_normalize(video_count_in_niche, niche_avg_video_count)
  → 1.0 = highly saturated; 0.0 = uncrowded

incumbent_strength = normalize(max(incumbent_subscriber_counts), authority_scale)
  → 1.0 = very strong incumbent; 0.0 = no strong incumbent

content_freshness = decay(min(top_video_age_days), halflife=180)
  → 1.0 = very stale existing content (opportunity); 0.0 = very fresh

result_concentration = gini_coefficient(top_video_view_counts)
  → stored as-is; NOT automatically treated as a bonus
```

**Competition assessment (deterministic, not a score):**

```
If incumbent_strength >= 0.75:
  assessment = "dominated"  # Strong authoritative incumbents present
  → competition_landscape score is LOWER
  → explanation: "High-authority incumbent detected. Entry difficulty is high
     regardless of content gaps."

Elif incumbent_strength >= 0.40:
  assessment = "contested"
  → moderate penalty

Elif topic_saturation >= 0.70 AND incumbent_strength < 0.30:
  assessment = "saturated_weak"  # Many videos, but no strong incumbent
  → moderate opportunity signal

Elif topic_saturation < 0.30 AND incumbent_strength < 0.30:
  assessment = "open"
  → strongest opportunity signal

Else:
  assessment = "mixed"
```

**Result concentration interpretation:**

High result concentration (`gini >= 0.70`) is recorded as an explanatory
signal, not a score adjustment. The explanation notes whether the dominant
videos belong to high-authority incumbents (→ `dominated`) or low-authority
creators (→ potential gap). The scoring engine does not automatically assign
a bonus for high concentration.

**Composite competition score:**
```
competition_score = weighted_combination(
    (1.0 - topic_saturation)     * 0.30,
    (1.0 - incumbent_strength)   * 0.40,
    content_freshness            * 0.20,
    channel_maturity_fit         * 0.10  # can our channel realistically compete here?
)

channel_maturity_fit = compatibility(channel.current_maturity_stage,
                                     required_authority_to_compete(incumbent_strength))
```

**Confidence:** Competition scoring confidence is reduced when:
- `incumbent_subscriber_count` is absent (CompetitorResearchAdapter not enabled)
- View data is stale
- Assessment is `mixed` (ambiguous signals)

Every score explanation records the `assessment` value and the sub-signals.

---

#### Factor 4: Evergreen Value (`evergreen_value`)

**What it measures:** How durable is this topic? Will it generate value in
12–24 months?

**Evidence types used:**
- `trend_variance_12mo` (Google Trends; if available)
- `trend_seasonality_flag` (Google Trends; if available)
- `top_video_age_days` (old top videos still ranking = evergreen signal)
- Topic category classification (from channel profile taxonomy)

**Computation:**
```
If trend_variance data is fresh:
  stability_score = 1.0 - normalize(trend_variance_12mo, 0, MAX_VARIANCE)
  factor_status = "measured"
Else:
  stability_score = CATEGORY_STABILITY_PRIOR[category]
  factor_status = "estimated" (category-based prior; documented)

age_signal = normalize(median(top_video_age_days), 0, 730)  # 2-year horizon
category_durability = EVERGREEN_CATEGORY_WEIGHTS[category]  # lookup table

evergreen_value = weighted_average(
    stability_score    * 0.40,
    category_durability * 0.40,
    age_signal         * 0.20
)
```

If Google Trends evidence is absent, `stability_score` uses the
`apply_prior` missing-data policy (category-based prior). The score
explanation records this.

---

#### Factor 5: Audience Fit (`audience_fit`)

**What it measures:** How well does this topic match the channel's specific
audience and strategic objectives?

**Evidence types used:**
- Channel profile: `primary_niche`, `secondary_niches`, `audience_intent`
- Active monetization strategy: `objective_weights_json`
- Channel maturity stage
- Topic `category`, `subcategory`

**Computation:**
```
niche_match =
    1.0  if category == primary_niche
    0.60 if category in secondary_niches
    0.10 otherwise

intent_match = INTENT_COMPATIBILITY[topic_intent][channel_audience_intent]

strategy_alignment = dot_product(
    topic_objective_signals,        # derived from topic category and format
    channel_strategy_weights        # from active monetization strategy
)

audience_fit = weighted_average(
    niche_match         * 0.50,
    intent_match        * 0.25,
    strategy_alignment  * 0.25
)
```

`audience_fit` never uses a missing-data policy because all its inputs come
from the channel profile, which is always present. It always has
`factor_status = "measured"`.

---

#### Factor 6: Content Novelty (`content_novelty`)

**What it measures:** How far is this topic from content the channel has
already produced? Prevents saturation and repetition.

**Evidence types used:**
- `normalized_topic` similarity against all non-archived, non-rejected
  opportunities for this channel (Jaccard similarity, computed by Knowledge
  Store)
- Content saturation map from Knowledge Store

**Computation:**
```
max_similarity = max(
    jaccard_similarity(candidate.normalized_topic, existing.normalized_topic)
    for existing in channel_active_opportunities
)
novelty_raw = 1.0 - max_similarity

saturation_penalty = SATURATION_DECAY[subcategory_recent_count]
content_novelty = max(novelty_raw - saturation_penalty, 0.0)
```

Candidates above `duplicate_similarity_threshold` are rejected before
reaching the Scoring Engine — they are flagged as duplicates in Stage 1.
`content_novelty` operates on the non-duplicate range.

---

### Composite Score and Weight Philosophy

```
opportunity_score = sum(
    factor_score_i * effective_weight_i
    for i in available_factors
)
```

Weights are drawn from the active `scoring_policy`. They are adjusted by
two sources:
1. **Strategy alignment:** The active monetization strategy's
   `objective_weights_json` adjusts factor weights within the policy bounds
   (e.g. a `watch_hour_progress` heavy strategy increases `evergreen_value`
   weight by up to 0.10, decreasing `trend_velocity` weight)
2. **Missing-data reweighting:** When `reweight_available` policy is applied,
   the absent factor's weight is distributed proportionally across remaining
   factors so the composite still sums to 1.0

**Default factor weights (maturity stage: `validation`, balanced strategy):**

| Factor | Default Weight | Rationale |
|---|---|---|
| `audience_fit` | 0.30 | Primary gate — wrong audience = wasted production |
| `evergreen_value` | 0.20 | Long-term business value priority |
| `audience_demand` | 0.20 | Real evidence of interest |
| `competition_landscape` | 0.15 | Realistic path to visibility |
| `content_novelty` | 0.10 | Prevents repetition |
| `trend_velocity` | 0.05 | Lowest weight by default |

**Weights are business parameters, not technical constants.** Any change
to defaults requires a new policy version with documented rationale.

### Missing-Data Policies

Each factor declares its missing-data policy in the scoring policy:

| Policy | Behavior |
|---|---|
| `reweight_available` | Redistribute this factor's weight proportionally to remaining available factors. Composite sum preserved. Score explanation documents the redistribution. |
| `apply_prior` | Use a documented prior value (from policy's `normalization_baselines_json`). Factor status = `estimated`. Confidence reduced. |
| `require_research` | Block `produce_now` recommendations for this opportunity until evidence is collected. `research_further` and `monitor` can still be assigned. |
| `reduce_confidence_only` | Score proceeds using the prior value but confidence is reduced proportionally to missing evidence. |
| `mandatory` | Factor must have `factor_status = measured` for any recommendation above `monitor`. |

Every score explanation records `factor_status` (`measured` / `estimated` /
`stale` / `unavailable`) and the missing-data policy applied for each factor.

### Score Comparability

Scores are meaningfully comparable:
- Within the same channel
- Under the same `scoring_policy_id`
- Within a time window where normalization baselines are stable (default: 90 days)

Scores are **not** directly comparable:
- Across different channels (different profiles, strategies, baselines)
- Across different scoring policy versions
- Across wide time windows where normalization baselines have shifted

Cross-channel ranking or resource allocation based on raw scores is not
supported in Phase 3. A future account-level capital allocation layer
(deferred; see Section 13) will address this as a separate concern.

### Score Explanation Format

Every `opportunity_scores` record includes `factor_scores_json`:

```json
{
  "scoring_policy_version": "1.0.0",
  "channel_profile_version": 3,
  "channel_maturity_stage": "validation",
  "scored_at": "2026-07-19T10:00:00Z",
  "composite_score": 0.71,
  "confidence": 0.74,
  "factors": [
    {
      "factor": "audience_fit",
      "factor_status": "measured",
      "raw_value": 0.90,
      "effective_weight": 0.30,
      "weighted_contribution": 0.27,
      "explanation": "Category 'personal_finance' matches primary niche exactly. Audience intent 'educational' compatible. Strategy weight on 'qualified_subscriber_growth' adds alignment."
    },
    {
      "factor": "trend_velocity",
      "factor_status": "unavailable",
      "raw_value": null,
      "effective_weight": 0.0,
      "weighted_contribution": 0.0,
      "missing_data_policy": "reweight_available",
      "explanation": "GoogleTrendsAdapter not enabled for this channel. Weight redistributed to audience_demand (+0.03) and evergreen_value (+0.02)."
    },
    {
      "factor": "competition_landscape",
      "factor_status": "measured",
      "raw_value": 0.58,
      "effective_weight": 0.15,
      "weighted_contribution": 0.087,
      "competition_assessment": "contested",
      "sub_signals": {
        "topic_saturation": 0.45,
        "incumbent_strength": 0.38,
        "content_freshness": 0.62,
        "result_concentration_gini": 0.52
      },
      "explanation": "Moderate incumbent presence (3 channels with 50k–200k subscribers). Content freshness is good (top videos 8+ months old). Assessment: contested — entry requires differentiated angle."
    }
  ],
  "confidence_breakdown": {
    "source_quality_multiplier": 0.90,
    "data_completeness_factor": 0.85,
    "data_freshness_factor": 0.97,
    "corroboration_bonus": 1.00
  }
}
```

CLI command `ace intelligence explain <opportunity_id>` renders this as a
human-readable table.

---

## 6. Feasibility and Business Analysis

### Responsibility

The Feasibility and Business Analysis stage (Stage 4) evaluates whether an
opportunity is viable for production given brand constraints, budget limits,
and capacity. It does not modify scores. It produces a `feasibility_verdict`
that flows into the Recommendation Engine.

### Brand and Safety Gates

Brand rules are evaluated from `channel.brand_rules` in the active profile
version:

- **Excluded topics check:** If `normalized_topic` overlaps with any
  `excluded_topics` entry above a strict threshold → `brand_blocked`
- **Content category safety:** If topic falls in a configured high-risk
  category (health claims, legal advice, financial predictions, content
  involving minors, etc.) → `safety_blocked`; requires explicit operator
  override even in future autonomous mode
- **Brand rule check:** Each `brand_rules` entry is evaluated deterministically
  against the topic's category, subcategory, and title

Any block is recorded in `opportunity_recommendations.blocking_factors_json`.
Blocks do not abort the scoring pipeline — the score is still computed and
stored. The feasibility verdict is recorded separately.

### Budget Gate

```
If estimated_production_cost > channel_capacity_policy.per_video_spend_limit_usd:
    feasibility_verdict = "budget_blocked"
    blocking_factors includes: cost estimate, limit, gap
```

Budget is evaluated against the active capacity policy version — not the
live channel record — so that a policy change mid-cycle does not
retroactively unblock a run.

### Multi-Dimensional Business Analysis

**Pre-data mode (Phase 3–Phase 10):**

No revenue is predicted. The analysis produces a set of decision inputs, not
a single ratio. These inputs inform the Portfolio Allocator's ranking (Stage 6)
but are never collapsed into a single score that obscures their meaning.

```
BusinessAnalysis record:
  estimated_cost_usd            — AI + TTS + asset cost estimate
  estimated_production_hours    — human time estimate (format-dependent)
  effort_tier                   — low / medium / high
  cost_estimate_basis           — "historical_average" / "formula" / "default"
  cost_estimate_confidence      — low / medium / high
  value_per_dollar_proxy        — (audience_fit * evergreen_value * confidence) / max(cost, 0.01)
  value_per_hour_proxy          — (audience_fit * evergreen_value * confidence) / max(hours, 0.1)
  confidence_adjusted_value     — opportunity_score * confidence
  portfolio_contribution        — how much this fills a gap in portfolio targets
  maturity_stage_fit            — compatibility of format+cost with current maturity stage
  funnel_role_alignment         — strategic_role alignment with active monetization strategy
```

**These are inputs, not a composite.** The Portfolio Allocator weighs them
against each other when ranking production slots. All components are stored
in `opportunity_recommendations.blocking_factors_json` (or a dedicated
`business_analysis_json` field) and visible in the explain output.

**Why not a single ratio:** A cheap but strategically weak opportunity
(high `value_per_dollar_proxy`, low `audience_fit`, low `evergreen_value`)
must not automatically outrank a more expensive but strategically valuable
one. The multi-dimensional model prevents this by keeping components visible
to the Portfolio Allocator.

**Post-data mode (Phase 11+):**

Once Phase 11 analytics are available, the Learning System adds calibrated
historical signals to this analysis:
- `historical_avg_watch_time_by_category` replaces `audience_demand` proxy
- `historical_avg_sub_conversion_by_format` refines `strategic_role` fit
- `actual_cost_accuracy` calibrates the `cost_estimate_confidence`

These additions do not change the structure of this analysis — they improve
the quality of the inputs without fabricating revenue values.

### Estimated Production Cost

```
estimated_production_cost = estimated_ai_cost
                          + estimated_tts_cost
                          + estimated_asset_cost

estimated_ai_cost = (
    sum(token_estimates_by_task) * ai_price_per_token
) * 1.20  # 20% margin for iteration

estimated_tts_cost = estimated_word_count * tts_price_per_character
  (word_count derived from format and effort tier)

estimated_asset_cost = 0.00  # if owned or CC0 only
                     = n_licensed_assets * avg_licensed_asset_cost
```

Derived from Phase 2 pricing registry. Effort tier is:

| Tier | Format + Complexity | Typical Cost |
|---|---|---|
| `low` | Short, single-angle, minimal research | $0.50–$2.50 |
| `medium` | Standard explainer or long-form, 2–3 sources | $2.50–$10.00 |
| `high` | Complex research, long-form, heavy AI iteration | $10.00–$30.00 |

---

## 7. Recommendation Engine

### Responsibility

The Recommendation Engine evaluates **opportunity quality only**. It does
not know about portfolio state, available production slots, or the final
ranked plan — those are the Portfolio Allocator's responsibility (Stage 6).

Its output is a preliminary recommendation stored in
`opportunity_recommendations`. This record is immutable after creation.
If conditions change and the opportunity is re-scored, a new recommendation
row is appended.

### Recommendation Values

| Value | Meaning |
|---|---|
| `produce_now` | Strong evidence, sufficient confidence, no feasibility blocks. Eligible for production. Subject to capacity allocation. |
| `research_further` | Promising but insufficient evidence or unresolved ambiguity. Gather more signals before committing a slot. |
| `monitor` | Genuine but low-urgency potential. Watch for signal improvement. No slot allocated. |
| `reject` | Not suitable for this channel. Reasons documented. Move to archived. |

### Decision Logic

```
# Step 1: Feasibility blocks
If feasibility_verdict in {brand_blocked, safety_blocked}:
    → reject (reasons from blocking_factors)

If feasibility_verdict == budget_blocked:
    → reject (over per-video limit; not a capacity question)

# Step 2: Audience fit floor
If audience_fit < 0.25:
    → reject (poor audience fit; primary filter)

# Step 3: Confidence gates
If confidence < 0.35:
    → research_further (insufficient evidence quality regardless of score)

# Step 4: Mandatory factor check
If any factor with policy="mandatory" has factor_status != "measured":
    If recommendation would be produce_now:
        → research_further (mandatory factor unsatisfied)

# Step 5: Score + confidence evaluation
If composite_score >= policy.produce_now_threshold
   AND confidence >= policy.min_confidence_for_produce_now:
    → produce_now (preliminary; subject to Stage 6 allocation)

If composite_score >= policy.monitor_threshold
   OR (composite_score >= policy.research_threshold
       AND evergreen_value >= policy.evergreen_floor_for_research):
    If missing data would materially change score (confidence_adjusted_value
       improves by > policy.material_improvement_threshold with more data):
        → research_further
    Else:
        → monitor

Else:
    → monitor  # Default — do not reject without documented reason
```

**Thresholds** are policy-versioned, not hardcoded. Defaults by maturity
stage (from scoring policy):

| Threshold | validation | growth | monetization | optimization | scaling |
|---|---|---|---|---|---|
| `produce_now_threshold` | 0.60 | 0.62 | 0.65 | 0.65 | 0.68 |
| `min_confidence_for_produce_now` | 0.50 | 0.55 | 0.60 | 0.60 | 0.65 |
| `monitor_threshold` | 0.40 | 0.42 | 0.45 | 0.45 | 0.48 |

### Rationale Generation

Every recommendation generates a `rationale` string deterministically from
factor values — not from an LLM. The rationale includes:
- Top contributing factors
- Any confidence limitations
- Specific blocking factors
- Which missing-data policies were applied

Example rationales:
- `"Recommended for production: strong audience fit (0.90/validation niche), good evergreen value (0.82), moderate competition in contested market — differentiated angle advised. Confidence: 0.74 from YouTube Data API. trend_velocity weight rebalanced (adapter not enabled)."`
- `"Research further: score 0.58 is promising but audience_demand factor is unavailable (mandatory for produce_now). Collect YouTube view data for this topic before progressing."`
- `"Rejected: topic matches excluded_topics entry 'financial predictions'. Brand rule: channel never makes specific price or return predictions."`

---

## 8. Portfolio Allocation and Production Plan

### Responsibility

The Portfolio Allocator is responsible for deciding which eligible
opportunities (those with `produce_now` preliminary recommendation) actually
receive a production slot in the current cycle. It is also responsible for
the ranked production plan presented at the approval gate.

The Recommendation Engine determines whether an opportunity is worthwhile.
The Portfolio Allocator determines whether it receives scarce capacity now.

### Content Type Classification

Before allocation, each opportunity is classified into a content type.
This classification is stored in `opportunity_recommendations`:

| Type | Definition | Signals |
|---|---|---|
| `evergreen` | Relevant for 12+ months; search-driven | High `evergreen_value`, stable trend variance, old top videos still ranking |
| `trending` | Peak interest 2–12 weeks | High `trend_velocity`, rising flag, low evergreen stability |
| `seasonal` | Predictably relevant at specific times of year | Seasonality flag from Trends or manual annotation, annual pattern |
| `experimental` | Testing new format, angle, or style | Linked to a Phase 12 experiment record (if available) or flagged manually |

A topic with mixed signals takes the dominant classification. The explanation
records the secondary signal.

### Content Package Format

When `format_recommendation = content_package`, the opportunity represents a
coordinated set of content assets:
- One long-form anchor video (primary)
- One or more Short clips (derived from anchor)
- Shared research and source evidence (efficiency)
- Coordinated release sequencing (anchor first, Shorts within 7 days)

Content packages consume more production slots and review hours but share
research cost. The capacity policy has a separate `content_package_capacity_per_week`
limit. Phase 3 records the package intent; Phase 5+ generates the actual content.
The handoff model: when a content package opportunity is promoted to a Topic
record, the `format_recommendation` field signals Phase 5 to generate a
multi-asset content brief.

### Strategic Content Role

Each opportunity carries a `strategic_role` that expresses its intended
contribution to the channel's strategy:

| Role | Description |
|---|---|
| `discovery` | Attracts new viewers via search or trending surface |
| `monetization` | Optimized for watch time and ad revenue potential |
| `subscriber_growth` | Designed to convert viewers to subscribers |
| `authority` | Builds domain expertise perception |
| `retention` | Serves existing subscribers; builds loyalty |
| `affiliate_conversion` | Includes product recommendations |
| `experimentation` | Tests a new format, angle, or hook style |

Strategic role influences:
- Portfolio allocation priority (roles that fill a strategy gap get preference)
- Content brief generation in Phase 5 (role-specific prompts)
- Analytics interpretation in Phase 11 (performance measured against role goal)

### Allocation Logic

```
# Inputs:
#   eligible_opportunities: all with preliminary_recommendation = produce_now
#   available_slots = capacity_policy.slots_per_week
#                   - trend_reservation_slots
#                   - currently_in_production_count
#   review_hours_remaining = review_budget - hours_already_committed
#   current_portfolio_distribution = counts by content_type in queue

# Step 1: Portfolio gap calculation
For each content_type in {evergreen, trending, seasonal, experimental}:
    target_count = floor(available_slots * portfolio_targets[content_type])
    current_count = current_portfolio_distribution[content_type]
    gap[content_type] = max(target_count - current_count, 0)

# Step 2: Rank eligible opportunities
For each opportunity in eligible_opportunities:
    compute priority_score:
      portfolio_contribution = gap[opportunity.content_type] > 0 ? 1.0 : 0.5
      strategic_value = confidence_adjusted_value  # from business analysis
      cost_fit = 1.0 if effort_tier == maturity_stage_preferred_effort else 0.8
      funnel_alignment = funnel_role_alignment  # from business analysis
      review_fit = 1.0 if review_hours_required <= review_hours_remaining else 0.0

      priority_score = weighted_sum(
          portfolio_contribution * 0.30,
          strategic_value        * 0.35,
          cost_fit               * 0.15,
          funnel_alignment       * 0.15,
          review_fit             * 0.05
      )

# Step 3: Assign slots greedily by priority_score
ranked_plan = []
remaining_slots = available_slots
remaining_review_hours = review_hours_remaining

For opportunity in sorted(eligible_opportunities, by=priority_score, desc):
    hours_needed = review_hours_by_format[opportunity.format_recommendation]
    If remaining_slots > 0 AND remaining_review_hours >= hours_needed:
        Add to ranked_plan with allocation_status = "allocated"
        remaining_slots -= 1
        remaining_review_hours -= hours_needed
    Else:
        Add to ranked_plan with allocation_status = "deferred_no_capacity"
        # Recommendation remains produce_now — it just didn't get a slot this cycle
```

**Priority score is explainable:** Every ranked plan entry carries the full
priority score components. The operator can inspect why opportunity A ranked
above opportunity B.

**Deferred but not downgraded:** An opportunity with `produce_now`
recommendation that receives `deferred_no_capacity` retains its
recommendation value. It is automatically re-considered in the next cycle
without needing to be re-scored.

### Trend Reservation Slots

`trend_reservation_slots` (default: 1) are held back from the normal
allocation pool. They may be filled by `trending` content type opportunities
at any time between cycles by operator command, bypassing the normal
allocation priority. This allows time-sensitive opportunities to enter
production without displacing already-planned work.

### Final Ranked Production Plan

The output of Stage 7 is a list of opportunities ordered by priority score,
with allocation status, full explanation chain, and mode-specific approval
requirements. Stored in a `production_plans` table (one plan per cycle per
channel) with a snapshot of the ranked list.

---

## 9. Content Lifecycle

### Lifecycle States

```
discovered
    │  Stage 1: deduplication passed
    ▼
needs_evidence        ← if mandatory factor has no fresh evidence
    │  (operator or system collects evidence)
    │
    ├──────────────────────────────┐
    │                              │ evidence collected
    ▼                              │
scoring_pending       ←────────────┘
    │  Stage 2–3: scoring engine runs
    ▼
scored
    │  Stage 4: feasibility analysis
    ▼
feasibility_assessed
    │  Stage 5: recommendation engine
    ▼
recommended           ← preliminary_recommendation set
    │
    ├── recommendation = reject ──────────────────────────────────► rejected
    │
    ├── recommendation = monitor ─────────────────────────────────► monitoring
    │
    ├── recommendation = research_further ──────────────────────► researching
    │                                                (re-enters scoring_pending
    │                                                 after evidence collected)
    │
    └── recommendation = produce_now
            │  Stage 6–7: portfolio allocation
            ▼
        allocated          ← slot assigned in production plan
            │              OR
        deferred           ← produce_now but no slot this cycle
            │  (auto-re-considered next cycle)
            │
            │  Stage 8: mode-aware approval
            ▼
        approved           ← approval record created
            │  promoted_to_topic_id set
            ▼
        in_production      ← Topic record created; pipeline takes over
            │
            ▼
        published          ← Phase 10 publishes; publication record linked
            │
            ▼
        measured           ← Phase 11 analytics collected
            │
            ▼
        archived           ← terminal state; record preserved
```

Additional states:
- `stale` — All observations have `is_stale = TRUE`; triggers re-discovery
- `monitoring` — Watching for signal improvement; has `expires_at`
- `researching` — Awaiting additional evidence collection
- `rejected` — Terminal for this cycle; operator may un-reject
- `archived` — Permanent terminal; not un-rejectable without explicit reason

### State Transition Rules

All transitions are recorded as rows in `opportunity_state_events`. The
`current_lifecycle_state` field on the `opportunities` table is a
denormalized read-optimized copy.

| From | To | Trigger | Guard |
|---|---|---|---|
| `discovered` | `scoring_pending` | Deduplication passed; fresh evidence exists | At least one non-stale observation |
| `discovered` | `needs_evidence` | Mandatory factor has no evidence | Mandatory factor policy check |
| `needs_evidence` | `scoring_pending` | Evidence collected via adapter | Mandatory factor now has evidence |
| `scoring_pending` | `scored` | Scoring engine completes | Score and confidence computed |
| `scored` | `feasibility_assessed` | Feasibility analysis completes | Feasibility verdict set |
| `feasibility_assessed` | `recommended` | Recommendation engine completes | Recommendation and rationale set |
| `recommended` | `allocated` | Portfolio allocator assigns slot | Slot available; capacity check passes |
| `recommended` | `deferred` | Portfolio allocator finds no slot | produce_now but no capacity |
| `recommended` | `monitoring` | Recommendation is `monitor` | `expires_at` set per policy |
| `recommended` | `researching` | Recommendation is `research_further` | Research action assigned |
| `recommended` | `rejected` | Recommendation is `reject` or brand block | Reason documented |
| `deferred` | `allocated` | Next cycle; slot becomes available | Auto-reconsidered |
| `researching` | `scoring_pending` | Research complete; new evidence added | New observation created |
| `monitoring` | `stale` | `expires_at` reached | Auto-transition |
| `stale` | `scoring_pending` | Operator triggers re-discovery | Old stale observations remain; new ones added |
| `allocated` | `approved` | Mode-aware gate passed | See approval logic below |
| `approved` | `in_production` | Topic record created | `promoted_to_topic_id` set |
| `in_production` | `published` | Phase 10 publishes | Publication FK set |
| `published` | `measured` | Phase 11 analytics collected | Metrics FK set |
| `measured` | `archived` | Retention policy or operator action | |
| Any (non-terminal) | `archived` | Operator explicit action | |

### Mode-Aware Approval

**Manual mode (Phase 3 only mode):**
- Operator reviews the ranked production plan
- Approves each item explicitly via `ace intelligence approve <opportunity_id>`
- Approval record created with `decided_by = operator_name`, `operating_mode = manual`
- No automated gate criteria checked; trust is in the human review

**Supervised mode (future; architecture reserved):**
- System stages the ranked production plan and sends notification
- Operator reviews within a configurable window
- Operator may approve the plan as-is, adjust priorities, or reject items
- Approval record created with `operating_mode = supervised`

**Autonomous mode (future; Phase 13+; not implemented in Phase 3):**
- All of the following criteria must pass for each opportunity:
  - `composite_score >= policy.autonomous_min_score`
  - `confidence >= policy.autonomous_min_confidence`
  - `feasibility_verdict = feasible`
  - `estimated_production_cost <= capacity_policy.per_video_spend_limit_usd`
  - Daily production count < daily limit
  - No anomaly flags on the channel (circuit breaker clear)
  - Topic not in high-risk content categories
  - Channel `operating_mode = autonomous` (explicitly qualified)
- `criteria_checks_json` in approval record documents every check with pass/fail
- Audit log entry created for every autonomous approval
- Anomaly detection active; automatic pause on anomaly

The fields supporting autonomous approval exist in `opportunity_approvals`
from Phase 3 forward. The autonomous code path is not implemented until
Phase 13.

---

## 10. Learning System

### Purpose

The Learning System closes the feedback loop between content performance
(measured in Phase 11+) and future OIE decisions. It converts analytics
into calibration signals that improve scoring, cost estimation, and
recommendations over time.

**Phase 3 role:** The Learning System's database schema is established in
Phase 3 (Milestone 3.5). No analytics data exists yet. The schema is ready
to receive data when Phase 11 is complete.

**What the Learning System is not:** It is not ML. It does not automatically
modify scoring weights. It produces calibration recommendations that humans
confirm before any parameter changes take effect.

### Learning Signal Schema (established in Phase 3, populated Phase 11+)

```
learning_signals table:
  id, channel_id, signal_type, category, content_type, format,
  metric_name, metric_value, sample_size, collection_window_days,
  min_sample_threshold, is_calibration_ready (= sample_size >= min_threshold),
  last_updated_at, source_phase

signal_type values:
  ctr_by_category, retention_by_category, sub_conversion_by_format,
  audience_loyalty_by_category, actual_cost_by_effort_tier,
  actual_ai_cost_accuracy, discovery_velocity_by_format
```

### Signal Types and Future Use

| Signal | From | Future Use |
|---|---|---|
| `ctr_by_category` | CTR from Phase 11 | Calibrate `audience_fit` weight per category |
| `retention_by_category` | Avg view duration | Calibrate `evergreen_value` weight |
| `sub_conversion_by_format` | Subscribers gained | Calibrate strategic role scoring |
| `audience_loyalty_by_category` | Returning viewer rate | Adjust `audience_fit` for loyal categories |
| `actual_cost_by_effort_tier` | Phase 5–8 cost records | Calibrate production cost estimates |
| `actual_ai_cost_accuracy` | Phase 5 AI cost actuals | Improve cost estimate confidence |
| `discovery_velocity_by_format` | Days to first 100 views | Calibrate trend-sensitivity recommendations |

### Minimum Sample Threshold

No signal is used for calibration until `sample_size >= min_sample_threshold`
(configurable per signal type; default: 10 videos). Below the threshold,
the signal is stored and marked `is_calibration_ready = FALSE`.

All threshold changes require human confirmation via
`ace intelligence calibrate confirm <calibration_id>`. This is consistent
with the progressive oversight reduction philosophy: the system proposes,
the human decides.

---

## 11. Knowledge and Memory

### Purpose

The Knowledge and Memory Store is the channel's institutional memory. It
ensures the OIE does not repeat itself, surfaces what has worked, and
maintains a map of the channel's content territory. All data is keyed by
`channel_id` — channels never share memory.

### Memory Tables (established Phase 3, enriched over time)

#### `topic_coverage_map`

Records every topic the channel has covered, declined, or archived.

| Column | Notes |
|---|---|
| id | UUID PK |
| channel_id | FK → channels |
| opportunity_id | FK → opportunities |
| normalized_topic | For fast similarity lookup |
| category / subcategory | |
| coverage_state | `produced` / `rejected` / `archived` / `declined` |
| coverage_date | When the topic entered a terminal or production state |
| outcome_summary | Brief note on outcome; populated from Phase 11 data later |

**Used by:** Deduplication (Stage 1) and `content_novelty` factor computation.

#### `content_saturation_map`

Tracks production density per subcategory over time.

| Column | Notes |
|---|---|
| channel_id | FK → channels |
| subcategory | |
| video_count_last_90_days | Rolling count |
| video_count_all_time | |
| last_produced_at | |
| saturation_tier | `low` / `moderate` / `high` / `saturated` |

**Used by:** `content_novelty` factor (saturation penalty) and portfolio
allocation (reduces priority of over-represented subcategories).

#### `production_anomaly_log`

Records patterns from past production that affect future cost and effort
estimates.

| Column | Notes |
|---|---|
| channel_id | FK → channels |
| category | |
| anomaly_type | `excessive_revision` / `quality_gate_failure` / `high_factual_risk` / `cost_overrun` |
| frequency | Count of occurrences |
| last_seen_at | |

**Used by:** Feasibility analysis (inflation of effort estimates for
anomaly-flagged categories).

#### `hook_performance_registry` (schema only in Phase 3)

| Column | Notes |
|---|---|
| channel_id | FK → channels |
| hook_style | `question` / `statement` / `statistic` / `story_open` / `challenge` |
| avg_retention_at_30s | NULL until Phase 11 data |
| sample_size | |
| last_updated | |

#### `title_performance_registry` (schema only in Phase 3)

| Column | Notes |
|---|---|
| channel_id | FK → channels |
| title_pattern | `number_list` / `how_to` / `mistake_warning` / `secret_reveal` / etc. |
| avg_ctr | NULL until Phase 11 data |
| sample_size | |

#### `audience_interest_signals` (schema only in Phase 3)

| Column | Notes |
|---|---|
| channel_id | FK → channels |
| category | |
| avg_sub_conversion | NULL until Phase 11 |
| avg_retention | NULL until Phase 11 |
| avg_watch_time_pct | NULL until Phase 11 |
| sample_size | |

**All Phase 11+ tables have schema created in Phase 3 with NULL values.**
The OIE never reads from these tables in Phase 3 decisions — it writes
structure now so Phase 11 can populate without schema migrations.

### Memory Influence on OIE

Memory is a signal, not a veto (except `excluded_topics`, which is a hard
brand rule):

| Memory | Influences | How |
|---|---|---|
| `topic_coverage_map` | Deduplication, `content_novelty` | Similarity check; prevents re-coverage |
| `content_saturation_map` | `content_novelty` penalty, portfolio allocation | Reduces priority of over-produced subcategories |
| `production_anomaly_log` | Feasibility analysis | Inflates effort estimate for anomaly categories |
| `hook_performance_registry` | Phase 5 content brief (future) | Hook style selection |
| `title_performance_registry` | Phase 5 metadata generation (future) | Title pattern selection |
| `audience_interest_signals` | Scoring calibration (future, Phase 11+) | `audience_fit` weight adjustment |

---

## 12. Multi-Channel Architecture

### Design Principle

Each channel is a fully independent strategic unit. Channels share code and
infrastructure but not data, strategy, scoring policies, or memory.

### Isolation Model

| Concern | Isolated? | Mechanism |
|---|---|---|
| Channel profile and strategy | Yes | All tables keyed by `channel_id`; FK enforced |
| Opportunity queue | Yes | `opportunities.channel_id`; no cross-channel queries |
| Knowledge and Memory | Yes | All memory tables keyed by `channel_id` |
| Learning signals | Yes | `learning_signals.channel_id` |
| Scoring policy | Shared code; per-channel activation | Policy is shared; which policy is active per channel is configurable |
| Production capacity policy | Yes | Per-channel capacity table |
| Analytics | Yes | `video_metrics.channel_id` (Phase 11) |
| Operating mode | Yes | `channels.operating_mode` |
| Budget limits | Yes | Per-channel capacity policy |
| AI provider | No | Shared infrastructure; cost tracked per channel |
| Scoring engine code | No | Shared; per-channel policy and profile as inputs |
| SQLite database | No | Single file; row-level isolation via channel_id |

### Adding a New Channel

```
ace channels add \
  --name "Finance Fundamentals" \
  --platform youtube \
  --primary-niche personal_finance \
  --audience-description "25–40 year olds learning basic investing" \
  --maturity-stage validation

# Creates channel with:
#   operating_mode = manual (always)
#   current_maturity_stage = validation
#   A channel_profile_version (version 1) is snapshotted
#   A default monetization strategy (pre-monetization, balanced) is created
#   A default capacity policy is created
#   No discovery runs until operator configures adapters
```

### Cross-Channel Concerns (Deferred)

Cross-channel resource allocation (deciding how to split production slots
across channels within an account budget) is a future account-level concern.
The architecture supports it because all channel data is in the same database
with `channel_id` foreign keys. A future `account_production_plans` table
can aggregate per-channel plans without requiring schema changes to existing
tables.

This is the **account-level capital allocation extension point.** It is
not implemented in Phase 3 or Phase 14. Score comparability limitations
documented in Section 5 apply here — cross-channel ranking requires
additional normalization that does not yet exist.

---

## 13. Future Extensibility

### Additional Platforms

`channels.platform` accepts `"youtube"` today. Future values:
`"instagram"`, `"tiktok"`. Platform-specific discovery adapters attach
without modifying the OIE core. Format recommendations and strategic roles
are platform-agnostic. Platform-specific scoring weight defaults belong in
the scoring policy, not hardcoded in any function.

**Constraint:** No platform-specific code enters the OIE until a real
channel on that platform exists. This is the YouTube-first principle from
the project spec applied to the OIE.

### Affiliate Marketing

`monetization_strategy.objective_weights_json` already includes
`affiliate_conversion` and `affiliate_revenue` objectives.
`strategic_role = "affiliate_conversion"` exists on the opportunity model.
Activating affiliate tracking requires connecting Phase 11 analytics to
these fields — no OIE model changes.

### Sponsorship, Digital Products, Memberships

All exist as valid `objective_weights_json` keys in the monetization
strategy. Activating them changes scoring and portfolio weighting without
requiring schema changes.

### Email Funnels

Reserved as a future `MonetizationStrategy` objective
(`email_funnel_conversion`). Would require a `funnel_stage` field on
opportunities and a `FunnelProfile` entity linked to the channel.

### Multilingual Content

`channel_profile_versions` can include language and geography in
`audience_demographics`. A future `LocalizationProfile` entity would
extend the channel without altering the opportunity model.

### Internal Suggestion Adapter

Registered as a future adapter (`InternalSuggestionAdapter`). Requires
Phase 11 analytics data to identify content gaps. When implemented, it
produces `discovery_source = "internal_suggestion"` observations via the
same `opportunity_observations` structure as other adapters.

### Account-Level Capital Allocation

Reserved as a future extension. The `channel_id` foreign key on all
opportunity and score tables enables a future `account_production_plans`
table to aggregate and rank across channels. Score comparability requires
a cross-channel normalization layer that is not designed here.

---

## 14. Risks

### Architectural Risks

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| Scoring policy proliferation making audits difficult | Low | Medium | Policy versions are numbered and described; `ace intelligence policy list` shows history; old versions never deleted |
| `opportunity_state_events` growing very large over time | Low (Phase 3) / Medium (Phase 14+) | Low | SQLite handles millions of rows efficiently; retention policy archives old events after terminal state is reached |
| Misuse of `deferred_no_capacity` as a soft-reject bypass | Medium | Medium | `deferred` status is visible in `ace intelligence queue`; deferred items auto-re-enter next cycle without operator action |
| Cross-channel score comparison producing invalid rankings | Medium | High | Score comparability limitations documented and enforced in CLI output; cross-channel views require explicit operator acknowledgement |
| Versioned channel profile creating snapshot bloat | Low | Low | Profiles are text; snapshots are small; no media stored; retention policy optional |

### Operational Risks

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| YouTube Data API quota exhausted mid-discovery | High | Medium | Quota tracker aborts run before limit; partial results stored with `status = partial`; operator notified |
| Google Trends rate limiting causing partial observations | High | Low | Graceful degradation via missing-data policy; `trend_velocity` uses `reweight_available`; score explanation notes absence |
| Operator neglects opportunity queue review | Medium | Medium | Phase 13 scheduler sends notifications; `ace intelligence status` shows queue depth; the system does not auto-promote in Phase 3 |
| Incorrect effort tier classification inflating cost estimates | Medium | Low | Cost estimates carry `cost_estimate_confidence`; actual costs recorded from Phase 5 onwards; Learning System calibrates |
| Competition scoring misclassifying a dominated market as an opportunity | Medium | Medium | `competition_assessment` field is human-readable; `dominated` classification produces lower score and explicit warning in rationale |

### Scaling Risks

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| SQLite write contention with many channels | Low (Phase 3–13) | Medium | WAL mode configured; Phase 14 is the correct point to re-evaluate |
| Opportunity table growing large with many discovery runs | Low | Low | `archived` state + retention policy; indexes on `(channel_id, current_lifecycle_state)` |

### AI Risks

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| LLM-assisted dedup producing incorrect similarity judgements | Medium | Medium | LLM dedup is advisory and off by default; operator sees both the LLM flag and the Jaccard score |
| Learning System calibrating on insufficient data | High (early) | High | Minimum sample thresholds strictly enforced; all calibration requires human confirmation; pre-data mode is the safe default until Phase 11+ |
| Scoring weights set incorrectly at channel launch | Medium | Medium | Defaults are conservative; scoring policy is versioned; the first production run is always in manual mode with full human review |

---

## 15. Implementation Roadmap

Phase 3 is decomposed into five milestones. Each is independently testable
and deliverable. The MVP (Milestones 3.1–3.4 core) is the minimum that
produces useful business decisions. Milestone 3.5 completes the memory
foundation.

### Phase 3 Scope Classification

**Phase 3 MVP (required for Phase 4 handoff):**
- Milestone 3.1: Versioned Channel Strategy Foundation
- Milestone 3.2: Opportunity, Evidence, and Discovery Foundation
  (ManualSignalAdapter + YouTubeDataAPIAdapter only)
- Milestone 3.3: Versioned Scoring and Confidence Engine
- Milestone 3.4: Recommendation, Feasibility, and Portfolio Allocation
  (manual mode only; production plan output)
- Milestone 3.5: Knowledge and Memory Foundation (coverage + saturation +
  anomaly tables; schema-only for Phase 11+ tables)

**Phase 3 Optional Enhancements (implement if time permits; do not block Phase 4):**
- Milestone 3.2b: CompetitorResearchAdapter (YouTube Data API; adds
  `incumbent_strength` evidence to competition scoring)
- Milestone 3.2c: GoogleTrendsAdapter (optional; isolated; OIE functions
  without it)
- LLM-assisted deduplication (advisory; off by default)
- Seasonal content calendar (pre-scoring of seasonal opportunities)

**Deferred to Later Phases:**
- Supervised and autonomous promotion modes (Phase 13)
- Cross-channel resource allocation (future account layer)
- Internal Suggestion Adapter (Phase 9+; requires Phase 11 data)
- Learning System calibration execution (Phase 11+)
- Hook, title, and audience interest signal population (Phase 11+)
- Cross-platform adapters (Phase 15)
- Account-level capital allocation (future)

---

### Milestone 3.1 — Versioned Channel Strategy Foundation

**Purpose:** Establish the channel configuration system that every subsequent
subsystem depends on. No discovery, scoring, or recommendation can run
without an active channel strategy.

**Scope:**
- Channel identity and operating mode (defaulting to `manual`)
- Versioned channel profiles with snapshot mechanism
- Channel monetization strategy (versioned, pre/post-monetization)
- Channel maturity stage
- Production capacity policy
- Channel operating mode event log (replaces `mode_history_json`)

**Exclusions:**
- Discovery adapters
- Scoring policies (3.3)
- Opportunity model (3.2)
- Publishing integration (Phase 10)

**Dependencies:** Phase 1 (database, models, config), Phase 2 (AI provider
— for optional LLM dedup only; not needed for this milestone)

**Deliverables:**
- `channels` table: identity, operating mode, current profile/strategy FKs
- `channel_profile_versions` table: immutable snapshots
- `channel_monetization_strategies` table: versioned strategy
- `channel_capacity_policies` table: production capacity config
- `channel_operating_mode_events` table: append-only mode history
- CLI: `ace channels add`, `ace channels list`, `ace channels show <id>`,
  `ace channels config <id>`, `ace channels strategy <id>`,
  `ace channels capacity <id>`
- Validation: portfolio targets sum to 1.0; budget limits positive; strategy
  objective weights sum to 1.0; capacity slots non-negative

**Database impact:**
- New tables: `channels`, `channel_profile_versions`,
  `channel_monetization_strategies`, `channel_capacity_policies`,
  `channel_operating_mode_events`
- SCHEMA_VERSION incremented

**Test strategy:**
- Unit: model validation; portfolio percentage sum enforcement; strategy
  weight sum enforcement; default derivations from maturity stage
- Unit: snapshot creation on profile update; old version preserved;
  `superseded_at` set correctly
- Integration: create channel → update config → verify new profile version
  created → verify old version unchanged
- No external API calls

**Acceptance criteria:**
- `ace channels add` creates a channel with `operating_mode = manual`,
  a profile version snapshot, a monetization strategy, and a capacity policy
- Updating any profile field creates a new `channel_profile_versions` row;
  the old row is unchanged with `superseded_at` set
- Portfolio target validation rejects values that do not sum to 1.0
- All tests pass; ruff clean

---

### Milestone 3.2 — Opportunity, Evidence, and Discovery Foundation

**Purpose:** Implement the normalized opportunity entity, observation-based
evidence model, and the two required launch adapters. After this milestone,
raw candidates can be discovered, deduplicated, and stored with full provenance.

**Scope:**
- `opportunities` table (enduring normalized entity)
- `discovery_runs` table
- `opportunity_observations` table
- `opportunity_source_evidence` table
- `opportunity_state_events` table (lifecycle history)
- `ManualSignalAdapter`
- `YouTubeDataAPIAdapter` (search + video details)
- Deterministic deduplication (Jaccard similarity)
- Quota tracking for YouTube Data API
- Freshness / staleness marking

**Exclusions:**
- Scoring (3.3)
- Recommendation (3.4)
- CompetitorResearchAdapter (optional sub-milestone 3.2b)
- GoogleTrendsAdapter (optional sub-milestone 3.2c)
- LLM-assisted deduplication
- `opportunity_scores`, `opportunity_recommendations`, `opportunity_approvals`
  (3.3, 3.4)

**Dependencies:** Milestone 3.1 (channel strategy must exist before
opportunities can be created)

**Deliverables:**
- Normalized opportunity model and tables as specified in Section 2
- Both required adapters with injectable stub clients for testing
- Deduplication engine with configurable threshold per channel
- `opportunity_state_events` rows created on every state transition
- CLI: `ace discover run --channel <id>`,
  `ace discover list --channel <id>`,
  `ace discover show <opportunity_id>`,
  `ace discover add-signal --channel <id>`

**Database impact:**
- New tables: `opportunities`, `discovery_runs`,
  `opportunity_observations`, `opportunity_source_evidence`,
  `opportunity_state_events`

**Test strategy:**
- Unit: deduplication at threshold boundaries; staleness calculation;
  quota tracking arithmetic; provenance field population per adapter
- Unit: state event creation on every transition; `current_lifecycle_state`
  updated consistently
- Integration: run ManualSignalAdapter → verify observations and evidence
  created; run YouTubeDataAPIAdapter with stub → verify evidence rows typed
  correctly; run deduplication against existing opportunity → verify
  duplicate linkage
- No live API calls in any test

**Acceptance criteria:**
- `ace discover run` with ManualSignalAdapter produces opportunities with
  observations and typed source evidence rows
- `ace discover run` with stubbed YouTubeDataAPIAdapter produces correct
  evidence types
- Deduplication correctly links near-duplicates above threshold
- Staleness flag set correctly based on channel `signal_staleness_days`
- `opportunity_state_events` has a row for every state transition
- All tests pass; ruff clean

---

### Milestone 3.3 — Versioned Scoring and Confidence Engine

**Purpose:** Implement the deterministic, versioned, reproducible scoring
system. After this milestone, every opportunity has an immutable score
record with a full explanation and a documented missing-data policy for
each factor.

**Scope:**
- `scoring_policies` table and version management
- Factor registry (all six factors from Section 5)
- Missing-data policies (all five types)
- Confidence calculation
- `opportunity_scores` table (immutable records)
- Score explanation JSON format
- Policy activation and audit trail
- CLI: `ace intelligence score <opportunity_id>`,
  `ace intelligence explain <opportunity_id>`,
  `ace intelligence policy list`,
  `ace intelligence policy activate <version>`

**Exclusions:**
- Recommendation Engine (3.4)
- Feasibility analysis (3.4)
- Portfolio allocation (3.4)

**Dependencies:** Milestones 3.1 and 3.2 (channel profile version and
observations required as scoring inputs)

**Deliverables:**
- Scoring engine with six factors and documented formulas
- Each factor explicitly handles all five data states:
  `measured`, `unavailable`, `stale`, `estimated`, `default`
- Missing-data policy applied and recorded in score explanation
- `scoring_policies` table with seed policy version 1.0.0
- Immutable `opportunity_scores` rows; no update path
- Score comparability documented in CLI output (channel + policy + window)
- `ace intelligence explain` renders factor breakdown as human-readable table

**Database impact:**
- New tables: `scoring_policies`, `opportunity_scores`

**Test strategy:**
- Unit: each factor in isolation with all five data states → correct
  behavior per missing-data policy
- Unit: composite score with known factor values → expected total (within
  floating-point tolerance)
- Unit: `reweight_available` policy redistributes weight correctly; sum
  still equals 1.0
- Unit: score explanation JSON contains all required fields;
  `missing_data_policy` recorded for each absent factor
- Unit: immutability — no code path modifies an existing score row
- Property: `composite_score` always in [0.0, 1.0]; `confidence` always
  in [0.0, 1.0]
- Integration: full score run from opportunity + observations → score record
  → explain output renders correctly
- No external API calls

**Acceptance criteria:**
- `ace intelligence score <id>` produces an immutable score record
  referencing the active policy version and channel profile version
- `ace intelligence explain <id>` shows every factor with
  `factor_status` and applied policy
- Changing the channel profile creates a new profile version; old score
  still references the old version (reproducibility)
- `ace intelligence policy activate <v>` creates a new run; old scores
  are unaffected
- All tests pass; ruff clean

---

### Milestone 3.4 — Recommendation, Feasibility, and Portfolio Allocation

**Purpose:** Complete the decision pipeline. After this milestone, the OIE
produces a ranked, capacity-constrained production plan and surfaces it for
mode-aware approval. Phase 4 can receive promoted topics.

**Scope:**
- Feasibility and business analysis (Section 6)
- Recommendation Engine (Section 7); preliminary recommendations
- Portfolio Allocator (Section 8); capacity-constrained ranked plan
- `opportunity_recommendations` table (append-only)
- `opportunity_approvals` table (append-only; manual mode only in Phase 3)
- Production plan output
- Mode-aware approval gate (manual mode)
- Opportunity-to-topic promotion (handoff to Phase 4+)

**Exclusions:**
- Supervised and autonomous modes (Phase 13)
- Content package generation (Phase 5+)
- Cross-channel allocation (future)

**Dependencies:** Milestones 3.1, 3.2, 3.3

**Deliverables:**
- Feasibility analysis with brand, safety, and budget gates
- Multi-dimensional business analysis (no single BVC ratio)
- Recommendation Engine with policy-versioned thresholds
- Portfolio Allocator with capacity slot management
- Ranked production plan per channel per cycle
- `opportunity_recommendations` rows (append-only)
- `opportunity_approvals` rows (manual mode)
- Topic record created on approval (handoff to Phase 4+)
- CLI:
  - `ace intelligence recommend --channel <id>` — run full pipeline
    through recommendation
  - `ace intelligence plan --channel <id>` — show ranked production plan
  - `ace intelligence explain <opportunity_id>` — full explanation chain
  - `ace intelligence approve <opportunity_id>` — manual approval gate
  - `ace intelligence reject <opportunity_id>` — reject with reason
  - `ace intelligence defer <opportunity_id>` — move to next cycle
  - `ace intelligence queue --channel <id>` — show approved queue
    and deferred items

**Database impact:**
- New tables: `opportunity_recommendations`, `opportunity_approvals`,
  `production_plans`

**Test strategy:**
- Unit: brand gate fires on excluded topic; safety gate fires on high-risk
  category; budget gate fires at per-video limit
- Unit: recommendation thresholds at boundary values (at, above, and below)
- Unit: confidence gating prevents `produce_now` below minimum
- Unit: mandatory factor check blocks `produce_now` when factor is
  `unavailable`
- Unit: portfolio allocator respects `slots_per_week` and review budget;
  allocates highest-priority first; records `deferred_no_capacity` correctly
- Unit: `produce_now` recommendation is not downgraded by allocator — only
  allocation status is set
- Unit: `opportunity_recommendations` rows are append-only; no update path
- Integration: full pipeline from discovery run → score → recommend →
  plan → approve → topic created; verify all tables populated correctly
- Integration: deferred opportunity re-enters next cycle automatically
- No external API calls

**Acceptance criteria:**
- Full pipeline runs: `ace discover run` → `ace intelligence recommend`
  → `ace intelligence plan` → `ace intelligence approve` → topic appears
  in `ace topics list`
- Budget gate blocks overage and records it in `blocking_factors_json`
- Recommendation Engine does not consider portfolio state; Portfolio
  Allocator does not modify the preliminary recommendation
- Production plan never exceeds `slots_per_week`
- Approval creates `opportunity_approvals` row with `operating_mode = manual`
- Promoted opportunity has `promoted_to_topic_id` set and state =
  `in_production`
- All tests pass; ruff clean

---

### Milestone 3.5 — Knowledge and Memory Foundation

**Purpose:** Establish the channel's institutional memory. After this
milestone, coverage and saturation are tracked in real time, anomalies are
flagged, and the schema is ready for Phase 11+ learning signals without
requiring further migrations.

**Scope:**
- `topic_coverage_map` table (updated on every promotion and terminal state)
- `content_saturation_map` table (updated on every promotion)
- `production_anomaly_log` table (populated by Phase 5+ events; schema created)
- `learning_signals` table (schema only; populated Phase 11+)
- `hook_performance_registry`, `title_performance_registry`,
  `audience_interest_signals` tables (schema only; populated Phase 11+)
- Integration of Knowledge Store with `content_novelty` factor
  (milestone 3.3 uses stub; 3.5 provides real values)
- CLI: `ace intelligence memory show --channel <id>`

**Exclusions:**
- Population of Phase 11+ tables (cannot validate pre-Phase 11)
- Learning System calibration execution
- Hook/title pattern recommendations

**Dependencies:** Milestones 3.1–3.4

**Deliverables:**
- All memory tables created and indexed
- Coverage map updated automatically on state transitions
- Saturation map incremented/decremented on production and archival
- `content_novelty` factor reads from Knowledge Store for saturation penalty
- `ace intelligence memory show` prints coverage map, saturation tiers,
  and any anomaly flags
- Handoff contract: documented interface for Phase 11 to populate
  performance tables (schema, expected data types, population cadence)

**Database impact:**
- New tables: `topic_coverage_map`, `content_saturation_map`,
  `production_anomaly_log`, `learning_signals`, `hook_performance_registry`,
  `title_performance_registry`, `audience_interest_signals`

**Test strategy:**
- Unit: coverage map updates correctly on promotion and rejection
- Unit: saturation map increments on production; decrements on archival
- Unit: `content_novelty` factor reads saturation penalty correctly
- Unit: memory tables are channel-isolated (no cross-channel queries)
- Integration: full pipeline → approve → verify coverage map and saturation
  map updated; re-score same topic → verify `content_novelty` lower

**Acceptance criteria:**
- `ace intelligence memory show` displays accurate coverage and saturation
  data after production events
- Saturation map correctly reflects rolling 90-day and all-time counts
- `content_novelty` factor uses Knowledge Store (not stub) after 3.5
- Phase 11+ table schemas are present with correct types and indexes;
  no data yet
- All tests pass; ruff clean

---

### Phase 3 Definition of Done

Phase 3 is complete when:
- All five milestones have passing tests and clean lint
- `ace discover run` on a real niche produces typed source evidence from
  ManualSignalAdapter and YouTubeDataAPIAdapter
- `ace intelligence score <id>` produces an immutable, reproducible score
  with full factor explanation
- `ace intelligence explain <id>` shows `factor_status` and
  missing-data policy for every factor
- `ace intelligence plan` produces a capacity-constrained ranked plan
  with full explanation chain for every entry
- `ace intelligence approve <id>` promotes an opportunity to a Topic
  record accessible to Phase 4
- Changing a channel profile creates a new version; existing scores
  reference the old version
- No production code depends on Phase 11 analytics data
- No fabricated revenue predictions appear anywhere
- All channel data is correctly isolated by `channel_id`
- All tests pass; ruff clean; no placeholder code

---

## 16. Closing

### Summary of Architectural Changes (v1 → v2)

**1. Progressive operating modes preserved.**
The v1 language permanently mandating human approval has been replaced with
the correct three-mode model (manual / supervised / autonomous). Phase 3
implements manual mode only. The persistence model, audit trail, and
approval tables support all three modes from day one.

**2. Opportunity persistence redesigned as normalized tables.**
The monolithic `Opportunity` record has been split into eight tables with
clear cardinality and append-only semantics for operational history. JSON
blobs are retained only where appropriate; critical history is relational.

**3. Critical history fields replaced with normalized records.**
`state_history_json`, `mode_history_json`, scoring history, recommendation
history, and approval history are now append-only relational tables
(`opportunity_state_events`, `channel_operating_mode_events`,
`opportunity_scores`, `opportunity_recommendations`, `opportunity_approvals`).

**4. Scoring reproducibility designed explicitly.**
`opportunity_scores` references both `scoring_policy_id` and
`channel_profile_version_id`. Changing a channel profile or activating a
new scoring policy creates new versioned records; old scores are unchanged.
A historical score can be reproduced by re-running the engine with the same
inputs.

**5. Score comparability limitation documented.**
Scores are comparable within the same channel, same policy version, and a
similar time window. Cross-channel ranking is explicitly out of scope and
reserved for a future account-level allocation layer.

**6. Circular responsibility between Portfolio Manager and Recommendation
Engine resolved.**
The decision flow now has eight sequential stages. The Recommendation Engine
(Stage 5) determines whether an opportunity is worthwhile. The Portfolio
Allocator (Stage 6) determines whether it receives a slot this cycle. The
Recommendation value is not modified by allocation decisions.

**7. Format types expanded.**
`format_recommendation` now supports `short`, `long_form`, `both`,
`content_package`, and `undecided`. Content package design and handoff
model defined.

**8. Strategic content role added.**
`strategic_role` field on opportunities supports seven roles. Influences
portfolio allocation priority, Phase 5 content brief generation, and
Phase 11 analytics interpretation.

**9. Channel Monetization Strategy replaces `primary_goal`.**
A versioned `channel_monetization_strategies` entity expresses a weighted
combination of objectives for both pre- and post-monetization stages. No
revenue values are fabricated.

**10. Channel maturity stages added.**
Five configurable stages (`validation` through `scaling`) influence scoring
weight defaults, format preferences, cost tolerance, confidence requirements,
and automation eligibility. Treated as versioned strategy inputs, not
hardcoded behavior.

**11. Business analysis redesigned as multi-dimensional.**
The single `bvc_ratio` (value proxy / cost) has been replaced with eight
named decision inputs. These are inputs to the Portfolio Allocator, not
collapsed into a single number. All components are preserved in the
explanation output.

**12. Competition scoring redesigned.**
A single `concentration_bonus` has been replaced with five named sub-signals
(topic saturation, incumbent strength, content freshness, result
concentration, channel maturity fit) and a deterministic `competition_assessment`
(`open` / `contested` / `dominated` / `saturated_weak` / `mixed`).
Concentrated views are no longer automatically a bonus.

**13. Missing-data policies replace automatic 0.5 substitution.**
Five explicit policies (`reweight_available`, `apply_prior`,
`require_research`, `reduce_confidence_only`, `mandatory`) replace the
previous behavior of substituting 0.5 for any absent factor. Each factor
records its `factor_status` and applied policy in the score explanation.

**14. Discovery adapter classification added.**
Adapters are classified as required launch, optional, or future.
ManualSignalAdapter and YouTubeDataAPIAdapter are required for MVP.
CompetitorResearchAdapter and GoogleTrendsAdapter are optional.
GoogleTrendsAdapter is isolated and never on the critical path.

**15. Production capacity model added.**
The channel capacity policy defines slots per week, concurrent production
limits, review hour budgets, content package limits, and trend reservation
slots. The final production plan allocates scarce capacity, not merely
sorts a list.

**16. Milestones revised.**
Five milestones revised to match the corrected architecture with explicit
scope, exclusions, dependencies, deliverables, database impact, test
strategy, and acceptance criteria.

**17. MVP / optional / deferred scope defined.**
Phase 3 MVP is Milestones 3.1–3.5 core. Optional enhancements (competitor
and Trends adapters, LLM dedup, seasonal calendar) do not block Phase 4.
Supervised/autonomous promotion, cross-channel allocation, and
Learning System execution are explicitly deferred.

---

### Unresolved Decisions Requiring Operator Approval

The following decisions are architectural choices this document cannot make
unilaterally. They require operator input before implementation begins.

**D1 — Default scoring policy weights.**
The document proposes defaults (audience_fit: 0.30, evergreen_value: 0.20,
etc.). These are starting points. The operator should review and adjust
for the specific channel's strategic context before the first production run.
*Decision required:* Accept defaults or specify initial weights.

**D2 — Initial channel maturity stage.**
The document uses `validation` as the default. If the first channel to be
configured is not truly in validation stage (e.g. an existing channel with
established content), this should be set appropriately.
*Decision required:* Confirm `validation` as the starting stage for the
first channel.

**D3 — Google Trends adapter: implement in MVP or defer.**
The document classifies GoogleTrendsAdapter as optional and defers it from
the MVP. If trend velocity signals are considered important for the first
production run, this should move to Milestone 3.2 scope.
*Decision required:* Defer Google Trends to optional or include in MVP.

**D4 — CompetitorResearchAdapter in Phase 3.**
Currently classified as an optional Phase 3.2b sub-milestone. Without it,
`incumbent_strength` evidence is absent, and competition scoring uses the
`apply_prior` missing-data policy. This may be acceptable for early runs.
*Decision required:* Include competitor research in Phase 3 MVP or defer.

**D5 — Duplicate similarity threshold.**
The document does not prescribe a default value. 0.70 is a common choice
for keyword-overlap deduplication; lower values are more aggressive.
*Decision required:* Set initial `duplicate_similarity_threshold` (0.60–0.80 range).

**D6 — Initial channel capacity policy values.**
`slots_per_week`, `review_time_budget_hours_per_week`, and
`trend_reservation_slots` need values based on the operator's actual
available time and production capacity.
*Decision required:* Set initial capacity policy values.

**D7 — Scoring policy for produce_now vs. monitor thresholds.**
The document proposes stage-specific defaults. These should be reviewed
against the operator's risk tolerance: conservative thresholds produce
fewer but higher-confidence recommendations; aggressive thresholds produce
more candidates for human review.
*Decision required:* Confirm threshold defaults or specify preferred values.

---

### Proposed Phase 3 MVP Scope

The Phase 3 MVP is the minimum system that delivers a useful business brain:

1. Configure a channel with versioned strategy, monetization objectives,
   maturity stage, and capacity policy
2. Ingest manual and YouTube Data API opportunities with full source evidence
3. Score opportunities deterministically under a versioned policy, handling
   missing data explicitly
4. Express confidence and explain every score at the factor level
5. Evaluate feasibility (brand, safety, budget)
6. Assign preliminary recommendations (produce_now / research_further /
   monitor / reject)
7. Allocate a capacity-constrained production plan
8. Approve individual opportunities in manual mode, creating Topic records
   for Phase 4

**MVP is Milestones 3.1 + 3.2 (required adapters only) + 3.3 + 3.4 + 3.5 core.**

---

### Proposed Deferred Capabilities

| Capability | Reason for Deferral |
|---|---|
| CompetitorResearchAdapter | Useful but not required for first runs; missing-data policy handles its absence |
| GoogleTrendsAdapter | Optional and isolated; OIE functions without it |
| LLM-assisted deduplication | Advisory only; Jaccard is sufficient for MVP |
| Supervised and autonomous promotion | Requires proven channel with qualifying data (Phase 13) |
| Seasonal content calendar | Value cannot be validated until at least one seasonal cycle completes |
| Learning System calibration execution | Requires Phase 11 analytics data |
| Hook, title, audience interest memory population | Requires Phase 11 analytics data |
| Cross-channel resource allocation | Requires account-level design not in scope |
| Account-level capital allocation | Future account layer; requires cross-channel score normalization |
| Internal Suggestion Adapter | Requires Phase 11 data to identify content gaps |
| Cross-platform discovery adapters | Phase 15; YouTube-first principle applies |
| Autonomous mode guardrails and audit execution | Phase 13 |

---

### Revised Milestone Sequence

| Milestone | Title | MVP? | Blocker for Phase 4? |
|---|---|---|---|
| 3.1 | Versioned Channel Strategy Foundation | Yes | Yes |
| 3.2 | Opportunity, Evidence, and Discovery Foundation | Yes | Yes |
| 3.2b | CompetitorResearchAdapter | Optional | No |
| 3.2c | GoogleTrendsAdapter | Optional | No |
| 3.3 | Versioned Scoring and Confidence Engine | Yes | Yes |
| 3.4 | Recommendation, Feasibility, and Portfolio Allocation | Yes | Yes |
| 3.5 | Knowledge and Memory Foundation | Yes (core) | No (but completes Phase 3) |

Dependencies: 3.1 → 3.2 → 3.3 → 3.4 → 3.5. Optional milestones 3.2b and
3.2c can be implemented after 3.2 completes and before or after 3.3.

---

### Validation

The following commands were used to inspect the document after revision:

```bash
# Confirm file exists at expected path
ls -lh docs/phase3_opportunity_intelligence_design.md

# Check line count
wc -l docs/phase3_opportunity_intelligence_design.md

# Verify all section headers are present
grep "^## " docs/phase3_opportunity_intelligence_design.md

# Verify all table names are used consistently
grep -o '`[a-z_]*`' docs/phase3_opportunity_intelligence_design.md \
  | sort | uniq -c | sort -rn | head -30

# Verify lifecycle states are consistent
grep -E "(discovered|scoring_pending|scored|recommended|allocated|deferred|approved|in_production|published|measured|archived|monitoring|researching|rejected|stale|needs_evidence)" \
  docs/phase3_opportunity_intelligence_design.md | head -40

# Verify operating modes are consistent
grep -E "(manual|supervised|autonomous)" \
  docs/phase3_opportunity_intelligence_design.md | grep -v "^#" | head -20

# Verify no fabricated revenue predictions
grep -iE "(revenue prediction|guaranteed|expected revenue|predicted revenue)" \
  docs/phase3_opportunity_intelligence_design.md

# Verify channel_id isolation is present
grep "channel_id" docs/phase3_opportunity_intelligence_design.md | wc -l

# Verify no production code
ls src/app/intelligence/ 2>/dev/null || echo "No intelligence package — correct"
```

---

### Confirmation

- No production code has been written.
- No database migrations have been executed.
- No external API requests have been made.
- This document is a design specification only.
- Implementation begins only after operator review and approval.
