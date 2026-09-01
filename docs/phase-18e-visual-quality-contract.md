# Phase 18E — Visual Quality Intelligence, Preflight & Test Isolation

Implementation contract. Written against the live repository and database, not
against assumptions.

---

## 1. What the audit found

### The visual pipeline already had most of the machinery

`app/visuals/` is a complete semantic visual engine: it segments narration into
beats, retrieves candidates across Pexels / Pixabay / Wikimedia / AI-image,
scores them deterministically, and falls back to a locally generated graphic
when nothing clears the bar. It persists every decision to `visual_beats` —
including `visual_intent`, `media_type_preferences_json` (what was wanted),
`resolved_media_type` / `resolved_provider` / `resolved_asset_key` (what was
got), and `fallback_reason` (why they differ).

**The lineage needed to measure visual composition already existed and was
already being written.** Nothing was reading it.

### Three gaps caused the observed quality problem

**Gap 1 — the QA gate could not see the difference between a diagram and a
wall of text.**

`app/visuals/qa.py` audits asset diversity, dominance, pacing and licensing. It
has no concept of what a visual *is*. Every locally generated image is
`media_type == "graphic"`, whether it is a labelled timeline or the narration
typeset over a colour field. A video that was 84% typeset narration scored
16 distinct assets across 18 beats at 13.8 changes/minute and **passed**.

**Gap 2 — the QA report was computed and then discarded.**

`stage_executors.py:982` called `audit_visual_plan(...)` and wrote the result
to a log line. It was never persisted, never returned, never consulted by
preflight, and never reached learning.

**Gap 3 — visual treatment was never chosen.**

`_build_effective_config` in `production_cycle.py` set tone and audience and
nothing else. `policy_from_config` therefore resolved the balanced default for
every autonomous video ever produced. Visual treatment was not a decision the
system made; it was a decision the system never made.

### The test-isolation defect was structural, not incidental

The Phase 18C incident — a Playwright test revoking live publishing
authorization — had four independent causes, any one of which was sufficient:

| # | Cause |
|---|---|
| 1 | `scripts/start-backend.sh` ran `set -a; source .env.local`, which **overwrote** every safety variable Playwright had exported. The E2E env block was inert. |
| 2 | `playwright.config.ts` set no `ACE_DB_PATH`, so the backend used the operational database by default. |
| 3 | `reuseExistingServer: !process.env.CI` meant a live backend already on :8000 was simply adopted, and the env block never applied at all. |
| 4 | E2E and live shared ports :8000/:5173, and Vite's proxy target was hardcoded to `localhost:8000`. |

It was fixed with a `test.skip()` guard inside the one test that tripped. That
is a list that must stay complete forever, maintained by whoever writes the
next test — not a safety property.

---

## 2. The visual quality model

### Visual families

Derived from two persisted columns, `resolved_media_type` and `visual_intent`:

| Family | Condition | Meaningful |
|---|---|---|
| `motion_footage` | `media_type == video` | ✅ |
| `photographic` | `media_type == photo` | ✅ |
| `illustration` | `media_type == illustration` | ✅ |
| `generated_diagram` | `media_type == graphic` AND intent has a structural renderer | ✅ |
| `text_card` | `media_type == graphic` AND intent has no structural renderer | ❌ |
| `unresolved` | no resolved asset file | ❌ |

### The meaningful-visual definition

> A beat is **meaningful** when its realized family shows the viewer something
> beyond the narration re-typeset: retrieved footage, a photograph, an
> illustration, or a generated graphic whose intent has a structural renderer.

The structural-intent set is `{number, comparison, process, timeline, diagram}`
— **read directly from `app.visuals.graphics._RENDERERS`**, the dispatch table
the renderer itself uses, so the definition cannot drift from what is actually
drawn. Every other intent falls through to `_render_statement`, which draws
words on a colour field.

This is a deterministic property of two columns. No model judges it.

### Metrics

All computed in `app/visuals/composition.py`, all duration-weighted where a
percentage is reported:

- `meaningful_runtime_pct`, `text_card_runtime_pct`
- per-family runtime and beat counts; `dominant_family` + share
- `family_diversity` — Shannon entropy over family runtime shares, normalised
  by the number of families *present* (so a 50/50 two-family video scores 1.0,
  not 0.4 for lacking families it never wanted)
- `distinct_asset_count`, `reused_asset_beat_count`, `asset_reuse_ratio`
- `visual_change_count` / `visual_changes_per_minute` — counts when the
  **picture** changes, not when a beat boundary passes: consecutive beats
  sharing an asset are one continuous visual
- `max_meaningful_gap_ms`, `avg_meaningful_gap_ms` — maximal contiguous runs of
  non-meaningful runtime
- `opening_meaningful_visual` — a meaningful visual starts within 4s

Metrics deliberately **not** added: text density (not derivable from persisted
lineage without re-rendering), on-screen motion magnitude (same), and anything
requiring a model to look at pixels.

---

## 3. Planned intent vs realized outcome

Three separations, all persisted:

**Per beat:** `planned_family` (head of `media_type_preferences_json`) vs
`realized_family` (from `resolved_media_type`), plus `fallback_reason` and its
attribution class.

**Fallback attribution:**

| Reason | Class |
|---|---|
| `structural_intent_prefers_graphic` | `creative` — the planner chose a diagram |
| `all_candidates_rejected` | `provider` |
| `no_candidates_returned` | `provider` |
| `download_failed` | `provider` |
| *anything unrecognised* | `provider` |

An unknown reason defaults to `provider`. Failing toward "the pipeline broke"
rather than "we meant it" means a new failure mode cannot masquerade as intent.

**At video level:** `planned_meaningful_beats` and `intentional_text_beats`
alongside `provider_fallback_beats` and `creative_fallback_beats`.

This is what lets the system say *"the planner wanted imagery for 14 of 18
beats and got it for 3"* rather than *"this video is mostly text"*.

---

## 4. Preflight policy

Verdicts: `pass` / `pass_with_warnings` / `blocked`. Only `blocked` stops
publication.

### Blocking floors

| Condition | Threshold |
|---|---|
| meaningful runtime below floor | `< 25%` |
| longest meaningful-visual gap | `> 20s` (videos ≥ 20s only) |
| provider-caused fallback rate | `> 50%` of beats |
| no meaningful visual at all | `meaningful_beat_count == 0` |
| unresolved beats | `> 0` |
| no beats to assess | empty render |

### Warnings

meaningful runtime `< 50%`; text-card runtime `> 35%`; no meaningful opening
visual; gap `> 12s`; single non-text family `> 70%`; `< 8` changes/min; asset
reuse `> 35%`; provider fallback `> 25%`.

### Threshold derivation

Not tuned to fail any particular video. A render whose retrieval *works* in
this architecture lands near 90%+ meaningful runtime with single-digit provider
fallback (measured: 93.6%, 6.7%). The floors sit far below that, at the point
where a video is weak on any reading rather than merely not to someone's taste.

### Intentional minimalism

A `minimalist` treatment lowers the meaningful-runtime floor from 25% to 15%
and suppresses the meaningful/text-card warnings.

It does **not** relax the gap, provider-failure, no-meaningful, or unresolved
blockers. Verified against the real queued render: under a minimalist
treatment it is still blocked, by the 50s gap and the 83% provider-failure
rate. Intentional minimalism is a decision about what to show; it is not a
claim that broken retrieval is fine.

---

## 5. Remediation

Bounded, and placed **before the render rather than after it**.

```
resolve beats → measure → if blocked, identify PROVIDER-fallback beats
              → re-resolve those with widened queries + relaxed floors
              → re-measure → render once
```

This was chosen over post-render repair because it needs no change to render
idempotency, discards no encoded video, and costs almost nothing: the engine
memoises provider searches for the whole run, so reconsidering an
already-issued query is free.

- **Targets only provider failures.** A beat that chose a diagram on purpose is
  never regenerated — that would be the system fighting its own plan.
- **Relaxed, not removed.** `min_evidence` 0.9 → 0.6, `min_score` 0.40 → 0.34.
  Two ordinary words in common beats another wall of text; an unrelated clip
  still cannot clear it. The relaxed context is a *copy*, so the first pass's
  verdicts are not retroactively reinterpreted.
- **Never destructive.** A beat that still finds nothing keeps its original
  card. Remediation can improve a plan, never damage one.
- **Widened queries.** From clause-shaped phrases ("history hundreds choices")
  to subject-shaped ones: beat entities → video entities → topic terms.
- **Restart-safe.** `remediation_attempts` is committed before provider work
  and is never reset by a reassessment.

---

## 6. Schema (v49 → v50)

`render_visual_assessments` — one row per render manifest, `UNIQUE` on
`render_manifest_id`, which makes reassessment idempotent by construction.

Keyed on the **render**, not the publication: a render is the artifact being
measured, it exists before anything is published, and preflight must be able to
block a render that will never become a publication. `publication_id` is a
nullable backfill written at upload time.

13 nullable columns added to `content_feature_snapshots`. Nullable with no
default: a publication produced before this phase has no assessment, and
`NULL` ("unknown") must never be read as `0.0` ("it had no meaningful
visuals") — that would teach the learner the exact opposite of the truth about
the back catalogue.

Verified on a **copy** of the live database: 49 → 50 upgrades cleanly.

---

## 7. Learning

13 features registered in `ALL_COMPARABLE_FEATURES`, classified by *who chose
the value* — because that determines what a correlation on it is allowed to
mean:

- **Planner-controlled:** `visual_style`
- **Realized-production:** `visual_meaningful_runtime_pct`,
  `visual_text_card_runtime_pct`, `visual_generated_diagram_runtime_pct`,
  `visual_retrieved_imagery_runtime_pct`, `visual_changes_per_minute`,
  `visual_max_meaningful_gap_s`, `visual_distinct_assets`,
  `visual_asset_reuse_ratio`, `visual_dominant_family`,
  `visual_opening_meaningful`
- **Provider-reliability:** `visual_provider_fallback_rate`,
  `visual_quality_status`

The third group describes the infrastructure, not the content. A correlation
there is evidence about pipeline health and must never be fed back as a
creative instruction.

Bucket widths are coarse (0.1 on a runtime fraction) deliberately: with
single-digit publications per channel, a narrow bucket yields one observation
per bucket and `insufficient` maturity for all of them — precision that
describes nothing.

Maturity semantics are unchanged: `insufficient` (n<2), `exploratory` (2–3),
`directional` (4–9), `actionable` (≥10). With 4 publications, every visual
feature is currently `insufficient` or `exploratory`. That is correct and the
existing gates enforce it.

---

## 8. Visual treatment intent

`visual_style` is now a `SAFE_CONTROLLABLE_FACTOR` with
`ControlCapability.ENFORCED` — and genuinely enforced, not aspirational:

```
brief.treatment_factors["visual_style"]
  → _build_effective_config()  →  effective_config["visual_style"]
  → policy_from_config()       →  VisualPolicy the engine actually runs under
  → render_visual_assessments.visual_style  (realized value, read back)
```

Safe values are `list(_PRESETS)` — the renderer's real capability surface — so
an experiment can never request a treatment the pipeline cannot produce. A test
asserts the two cannot drift apart. As a controlled factor it declares a
baseline of `balanced` (the genuine pipeline default) rather than `unknown`,
which Phase 18D established reads as an observation failure.

The registry expresses **no preference** among styles. Whether diagrams,
footage or minimalism perform on a channel is precisely what the visual
features exist to discover.

---

## 9. Test / runtime isolation

The invariant is not "tests avoid dangerous endpoints". It is:

> A process running in test mode and a process serving the live system cannot
> be pointed at the same database, and a process pointed at the wrong one
> refuses to start.

`app/core/runtime_mode.py` enforces it in **both** directions, from
application startup (`api/main.py` lifespan), the autonomous scheduler daemon,
and the E2E launcher:

- test mode + operational database → refuse to start
- no test mode + `e2e-test.db` → refuse to start (the reverse mistake, and the
  more destructive one: live daemons on a throwaway database look like total
  state loss)

Paths are compared **resolved**, so a symlink cannot smuggle the operational
database past the check.

Four separations now apply, none relying on any individual test:

| Axis | Live | E2E |
|---|---|---|
| database | `content.db` | `.e2e-data/e2e-test.db` |
| backend port | 8000 | 8100 |
| frontend port | 5173 | 5273 |
| mode | — | `ACE_TEST_MODE=e2e` |

Supporting fixes:

- `start-backend.sh` no longer does `set -a; source .env.local`. Values already
  in the environment win; the file only fills in what is unset.
- `start-e2e-backend.sh` never reads `.env.local` at all, and `unset`s every
  provider credential.
- `reuseExistingServer: false`, unconditionally.
- Vite's proxy target is `ACE_BACKEND_URL`-driven.
- `globalSetup` **asks the running backend** (`/api/meta` → `runtime`) which
  database and mode it actually has, and refuses to run a single test
  otherwise. A config that intends isolation is not evidence that the process
  answering on the port has it.

Database isolation does almost all the work. A residual list —
`assert_live_effect_allowed` — refuses operations whose effect escapes the
database entirely (provider upload, public release, authorization changes),
wired into `check_live_publishing_gate`, the release endpoint, and the
authorization endpoint. This is belt-and-braces, explicitly *not* the mechanism
the safety property rests on, because such a list can never be complete.

That list is enforced **only in `e2e` mode**, and the distinction is
deliberate. E2E drives a real server through real request flows, where an
upload could genuinely happen. Unit and integration runs inject fakes and call
these functions directly to assert their own behaviour — `test_enabled_passes`
exists precisely to check that `check_live_publishing_gate()` does *not* raise
when the gate is open. Refusing there prevents no upload (there is no provider)
and only makes the gate untestable, which is a worse safety outcome than a
testable gate. Those runs remain fully covered by database isolation.

### Two live-database incidents this phase caused and then closed

Both had the same shape: nothing between an environment variable and
`sqlite3.connect` ever looked at which database it was about to open.

**1. A pytest run migrated the live database.** `ACE_DB_PATH=` — set but
*empty* — falls through `Config`'s `Path(raw) if raw else _default_db_path()`
to the OPERATIONAL database. A backend suite run under that environment opened
it, `open_db` ran `_migrate()`, and the live schema went 49 → 50. The running
backend and the autonomous publisher were both still on v49 code, so every
DB-backed request returned HTTP 500 and the scheduler failed ~32 consecutive
ticks. Nothing surfaced it: `service.sh status` still reported LOADED/running
and `/api/health` still returned OK, because neither touches the database.

Fixed at three layers:
- `open_db` itself now calls `assert_runtime_isolation(path)`. It is the lowest
  layer, every caller funnels through it, and it is specifically the call that
  runs migrations.
- `tests/conftest.py` sets `ACE_TEST_MODE` and pins a temp `ACE_DB_PATH` at
  **import** time, before any test module's module-level code can run.
- A session fixture asserts the result still holds.

**2. `ace features extract` had been running against the live database.**
`tests/test_content_features.py` patched `app.core.config.get_config` to return
a stub — but `app.cli` binds `get_config` at import time, so the patch never
applied, and the stub carried a `database_path` attribute where `_get_db()`
reads `db_path`. The command therefore used the default path (live) and the
test "passed" only because the live database happens to contain a publication
with id=1. It survived because `extract_and_save` is idempotent and that
publication already had a snapshot; a different id would have written to
production. Rewritten to use the repo's own `ACE_DB_PATH` + `reset_config`
isolation pattern.

The guard found the second one. That is the argument for the guard.

---

## 10. Known limitations

1. **Remediation is untested against live providers.** The path is unit-tested
   and structurally bounded, but no autonomous run has exercised it yet.
2. **No visual feature can currently reach `directional` maturity.** Four
   publications is not evidence about visual causality, and the system
   correctly refuses to pretend otherwise.
3. **The planner does not yet act on visual evidence.** The bridge is built and
   the data flows; automated weighting is deliberately deferred until the
   evidence exists to weight.
4. **Thresholds are global, not per-channel.** `VisualQualityThresholds` is a
   frozen dataclass ready to be channel-scoped; nothing reads a per-channel row
   yet.
5. **Pre-18E renders have no assessment**, so the preflight gate treats them as
   unassessed (warn, do not block). Blocking on absence would make the entire
   back catalogue permanently unpublishable.
6. **The root cause of the fallback storm is upstream of this phase.** Abstract
   topics ("history and society") produce clause-shaped queries that no stock
   library can answer. Remediation mitigates it; query construction and topic
   specificity are the real fix.

7. **A visually blocked slot pins the queue.** `publish_status='failed'` is not
   in `TERMINAL_PUBLISH_STATUSES` (only `released` and `skipped_missed` are).
   Once a blocked slot exhausts `MAX_PUBLISH_RETRIES = 3`,
   `find_slot_ready_to_publish` correctly stops retrying it — but
   `list_active_slots` still counts it, so with `queue_target = 1` the decision
   cycle returns `QUEUE_ALREADY_SATISFIED` forever and no new video is ever
   queued. This is Phase 18D defect #4 re-entered through a new door.
   Verified against a copy of the live database, not inferred.

   A blocked render needs a terminal publish status (or the block needs to
   retire the slot and hand its lineage to a replacement, as the missed-slot
   path already does). That is a design decision, deliberately not made
   unilaterally here.

---

## 11. Closure pass — retirement, queue lifecycle, and topic specificity

### Render 9 retirement

Retired through the new canonical path, not by hand:

```
retire_slot(slot 3, category=ARTIFACT_QUALITY_BLOCKED,
            reason="visual_quality_blocked: <the actual measurements>")
reject_publishing_plan(plan 5, reason_code="visual_quality_blocked")
```

Preserved in full: render manifest 9 (still `approved`), publishing plan 5,
the visual assessment, the Aug 31 slot with its original `slot_key`,
`scheduled_for_utc` and `state='filled'`, experiment `exp-slot-3`, topic 6, and
all 18 beats. Nothing deleted, no history rewritten.

Provider-ineligibility rests on **two independent markers**, deliberately:

| Marker | Stops | Survives |
|---|---|---|
| `publishing_slots.retired_at` | the autonomous cycle selecting the slot | — |
| `publishing_plans.status = 'rejected'` | *any* path publishing that plan | the assessment row being deleted |

The visual-quality gate is a third layer. The plan rejection is what satisfies
"do not merely rely on the assessment being present": it is the pre-existing
canonical "must not be published" marker and is checked independently.

### Queue deadlock — root cause and fix

`publish_status='failed'` is not in `TERMINAL_PUBLISH_STATUSES`, so
`list_active_slots` kept counting a blocked slot and `queue_target=1` could
never be satisfied again.

Retirement is modelled as its **own column** (`retired_at`), not a seventh
`publish_status` value, for two reasons. It is a different axis — publish
status tracks progress toward publication, retirement records that progress
stopped permanently and why, and a slot retired for quality never progressed
anywhere. And `publish_status` carries a CHECK constraint that SQLite cannot
alter in place: extending it means rebuilding `publishing_slots`, the table
holding all live publishing state, on a live database. An additive nullable
column gives the same semantics with none of that risk.

Terminality stays defined **once**, in `_NOT_TERMINAL_SQL`, which now tests
both `retired_at IS NULL` and the terminal status list. The deadlock came from
exactly the kind of split that would reintroduce. `find_slot_ready_to_publish`
was also hand-rolling its own allow-list of in-flight states and now applies
the shared fragment too.

A second, subtler defect surfaced during rehearsal: `reserve_slot` returns any
existing row for a `slot_key`, including a retired one, and `fill_slot` then
refuses it because its state is already `filled`. So retirement freed queue
*occupancy* but the retired slot's key blocked its own replacement.
`_slot_key_is_spent` now makes cadence selection skip keys belonging to slots
that have left the pipeline — the same resolution `reschedule_slot_to_new_time`
reaches for a missed slot: history keeps its slot, the replacement gets its own.

### Retry semantics after the fix

| Failure | Provider call | Retry consumed | Terminal |
|---|---|---|---|
| Provider unavailable / network | yes | yes (max 3) | no |
| Pre-upload validation | no | yes (max 3) | no |
| **Artifact quality blocked** | **no** | **no** | **yes — retired** |

`DETERMINISTIC_ARTIFACT_FAILURES` is disjoint from
`RETRYABLE_PUBLISH_FAILURES`, and a test asserts it. The gate runs *before*
`start_slot_publishing`, so no lease is taken and no provider client is built.
It runs *after* unresolved-attempt reconciliation, because if a previous run
may already have put a video on the provider, the truth about that video
outranks everything.

### Topic specificity — the upstream root cause

`promote_opportunity(brief.opportunity_id)` turns an opportunity title directly
into a production topic. Opportunity 2's title was "history and society".

The revealing part is what the existing gate said about it: the semantic-fit
LLM scored it **0.8, `strong_fit`**. That judgement was *correct* — the topic
genuinely suits a channel whose audience is "curious adults seeking clear,
accessible explanations". Fit was never the failing axis, which is why
tightening the fit threshold would have been the wrong fix: it would have
rejected good concrete topics without touching the actual problem.

The missing question was: **is this a topic at all, or a category?**

Prompt `eligibility-semantic-fit` v2 asks both, in one call, returning
`topic_specificity`, `specificity_label`, `visual_groundability`,
`concrete_subjects`, `viewer_promise` and `refined_topic` alongside the
existing fit fields. Extending the existing structured output was preferred
over a second LLM call: same opportunity, same context, one more question.

**Where it runs:** in eligibility (`evaluate_opportunity_eligibility`), during
the decision cycle — before a slot is filled, and long before any script, TTS
or visual spend.

It also runs under the **deterministic niche bypass**, which is where it
matters most. That bypass skips semantic fit when an opportunity exactly
matches the channel's `primary_niche` — but a channel's niche string
("science and technology explained") is a category by construction, so the
bypass would otherwise exempt the single most category-shaped candidate.

**Explicitly not named-entity count.** The prompt says so directly: "why
airplane windows are rounded" names nothing and is excellent; "innovation in
the modern era" names nothing and is unusable. What separates them is whether
there are identifiable things to show. A test asserts the no-entity concrete
case is accepted, and another asserts by AST inspection that no topic literal
or allow-list exists in executable code.

**On failure:** the candidate is blocked (`topic_not_concrete` or
`topic_not_visually_groundable`) and the planner selects another eligible
candidate. The prompt's `refined_topic` suggestion is persisted alongside the
verdict but is not yet auto-applied — adopting a refinement changes what gets
produced, and that deserves its own increment rather than riding in on a gate.

**Fail-open on unknown**, exactly like a missing visual assessment: a prompt-v1
cached row or a response without the fields yields a warn, never a block.
NULL means "not evaluated", never 0.0.

