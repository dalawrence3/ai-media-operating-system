# Phase 18D — Closed-Loop Autonomy: Implementation Contract

Status: implemented. Companion to `ARCHITECTURE.md` and `DECISIONS.md`.

Phase 18C proved a video could be produced and published autonomously. It did
not prove the loop closed. This document records what the audit found, the
lifecycle contract that resolves it, and where each transition is owned.

---

## 1. What the audit found

Nine defects, all confirmed against the live Orvella database before any code
was changed. Five of them independently broke the loop; four more would have
corrupted it.

| # | Defect | Effect |
|---|--------|--------|
| 1 | Publication → experiment handoff never existed | `attach_publication` and every transition past `in_production` were reachable only from tests and the CLI. `exp-slot-1` sat in `in_production` with its video public. |
| 2 | Content features never extracted autonomously | `extract_and_save` was CLI-only. Cross-publication learning only considers publications that have a feature snapshot, so no autonomously produced video could ever contribute to channel evidence. |
| 3 | Learning → planner channel-id namespace mismatch | `build_portfolio_plan` called `get_exploration_coverage(channel_id=str(intel_id))` — `"1"` — but the table is keyed by the control-plane UUID. Coverage was **always empty**. |
| 4 | Queue deadlock | A released slot kept `state='filled'` forever. With `queue_target=1` and two terminal slots, the decision cycle returned `QUEUE_ALREADY_SATISFIED` permanently. Orvella could never plan another experiment. |
| 5 | Scheduler interval ignored | `compute_next_run_at` read `schedule_config["seconds"]`; every stored schedule writes `interval_seconds`. Every interval schedule silently fell back to 86400s. This is why the 18C slot went MISSED. |
| 6 | Retired missed slot re-eligible for production | The missed-slot lineage handoff clears the old slot's production columns, making it look like fresh work on every tick. |
| 7 | Cumulative windows double-counted | The observer always queries `published_at → today`, so successive observations produce nested windows sharing a `period_start`. `AGG_SUM` summed them: a 474-view video aggregated to 948. |
| 8 | Seed analytics scored as evidence | `outcome_service` did not exclude `input_hash LIKE 'seed-%'` (cross-publication learning does), so dev fixtures could be scored as observations. |
| 9 | Market-exploration fidelity permanently UNRESOLVED | Two causes: no `market_theme_evaluator` was ever injected, and a controlled factor with no declared baseline was treated as an observation failure. Both made outcomes `INVALID_EXECUTION` forever. |

### Audit answers

1. **What creates an experiment?** `production_cycle._materialize_experiment` → `create_experiment` (draft), from the slot's strategy brief. Idempotent via `input_hash`.
2. **What sets `in_production`?** `_run_locked_production` → `_ensure_experiment_status`.
3. **What *should* happen at public?** → `published`, then → `observing` once observation is registered.
4. **What did happen?** Nothing. Confirmed for both `exp-slot-1` and `d0e57f27`.
5. **How are analytics tied back?** `analytics_snapshots.experiment_id` from `publishing_plans.experiment_id`. Data lineage was fine; the ledger was not.
6. **When is an experiment observing?** When its publication is public + published **and** an active observation schedule exists.
7. **When is an outcome mature?** By `outcome_service`'s own thresholds, unchanged: age ≥ 48h **and** views ≥ 10.
8. **What invokes cross-publication learning?** `decision_cycle` step 5, before planning.
9. **Does the next decision consume it?** It does now. It did not — see defect 3.
10. **Do market + channel evidence combine per the strategy profile?** Now yes. Previously both weighted terms were fed by market data, so the split was meaningless.
11. **Can a completed publication block production?** It did — defects 4 and 6.
12. **Can channels contaminate each other?** No. Architecturally sound; defect 3 produced *no* data rather than another channel's.
13. **Can restart recover every transition?** Upload/release and observation, yes. Experiment lifecycle, features, outcome, learning — no, because they did not exist.

---

## 2. Canonical experiment lifecycle

The Phase 14A schema already had the full state set. Nothing was renamed and no
state was added.

```
draft → planned → in_production → published → observing → mature → analyzed → completed
                                                    ↘ (any) → cancelled
```

The four concepts stay distinct, because collapsing them makes restart
ambiguous:

| Concept | State | Why separate |
|---------|-------|--------------|
| PUBLICATION is an event | `published` | The moment the provider confirmed PUBLIC. |
| OBSERVATION is a period | `observing` | `published` alone cannot tell you whether observation was ever registered. |
| OUTCOME MATURITY is evidence quality | `mature` / `analyzed` | Certified by the Phase 14G evaluator, never asserted by the bridge. |
| LEARNING is downstream | `completed` | Means the evidence reached cross-publication learning — which is also what releases the opportunity from the eligibility conflict gate. |

### Transition ownership

| Transition | Owner | Trigger | Idempotency | Restart |
|-----------|-------|---------|-------------|---------|
| `→ planned`, `→ in_production` | `production_cycle` | production run | `_ensure_experiment_status` no-ops when already past | slot lease |
| `→ published` | `lifecycle.advance_experiment_for_publication` | publishing cycle confirms PUBLIC | re-reads state; forward-only | `reconcile_experiment_lifecycle` |
| `→ observing` | `lifecycle.mark_experiment_observing` | observer tick with an active schedule | same | every tick re-checks |
| `→ mature → analyzed` | `outcome_bridge.run_outcome_bridge` | `EVALUABLE_MATURE` from the evaluator | outcome keyed by `input_hash`; advance is forward-only | every tick re-evaluates |
| `→ completed` | `outcome_bridge` | publication appears in a channel baseline | forward-only | every tick re-checks |

**Invariants.** The experiment is always derived from the publication's own
lineage (`publication → publishing_plan.experiment_id`, falling back to
`production_plan.experiment_id`) — no caller may name one. Nothing advances a
`cancelled` or `completed` experiment. Nothing advances on a non-public
publication. Every transition emits a `cp_events` record. No bridge is ever
fatal: the video is already public, so bookkeeping failure logs and defers to
reconciliation.

---

## 3. Honesty rules

These are the rules that decide what the system is allowed to claim.

- A newly published video with no analytics is `insufficient_analytics`. No
  outcome row is written and the experiment stays `observing`.
- `EVALUABLE_PROVISIONAL` persists a real outcome but does **not** claim
  maturity — the experiment stays `observing` while evidence accumulates.
- `mature` is reached only when the Phase 14G evaluator says
  `EVALUABLE_MATURE`. The bridge has no opinion of its own.
- Maturity depends on wall-clock age as well as metrics, so the bridge runs on
  **every successful observation tick**, not only on ticks that brought new
  data. Gating on new data would strand a video that collected its views on day
  one and none after.
- Seed fixtures (`input_hash LIKE 'seed-%'`) are never evidence.
- A cluster we cannot identify is `unresolved`; a script that does not exist yet
  is `not_yet_available`. Neither is a match.
- A control factor with **no declared baseline** is a warning, not an
  unresolved execution — a claim that was never made cannot have been violated.
  A control factor **with** a declared baseline that cannot be read is still
  unresolved.

---

## 4. Learning reaches planning

```
observation tick
  → features extracted (outcome_bridge.ensure_content_features)
  → aggregates (seed-excluded, cumulative windows collapsed)
  → cross_publication learning  → channel_performance_baselines
                                 → feature_performance_observations
  → decision cycle step 5 re-runs cross-publication learning
  → build_portfolio_plan
      ├─ _cp_channel_id_for()          crosses the identity bridge
      ├─ get_exploration_coverage()    → treatment-factor choice, information gain
      └─ _channel_evidence_maturity()  → internal_evidence_strength, planning intent
  → strategy profile weights market vs channel evidence
  → candidate ranking → selection decision → strategy brief → next experiment
```

The two evidence terms now come from genuinely different sources:

- `opportunity_attractiveness` — the wider market (Phase 13F composite score)
- `internal_evidence_strength` — this channel's own published videos (Phase 12C
  baselines)

Previously **both** were fed by market cluster maturity, so the strategy
profile's `market_intelligence_weight` / `channel_evidence_weight` split had
nothing real to weigh, and a strong YouTube-wide signal could push a channel
into exploitation before it had published anything.

**Bootstrap behaviour is preserved and now actually enforced.** With no channel
baselines, `internal_evidence_strength` is 0.0 and planning intent stays
EXPLORATION regardless of market maturity. Diversity guards
(`max_cluster_share`, `max_consecutive_same_cluster`) are untouched. No topic is
hardcoded anywhere.

---

## 5. Queue continuity

`slot.state` stays `filled` for terminal slots — the historical record must not
be rewritten, and a missed slot has to remain visible as a missed slot. "Does
this slot still occupy the queue?" is therefore answered by `publish_status`,
via `TERMINAL_PUBLISH_STATUSES = {released, skipped_missed}`:

- `list_active_slots` — queue capacity; excludes terminal.
- `find_slot_needing_production` — production eligibility; excludes terminal.
- `find_slot_ready_to_publish` — already excluded terminal via its whitelist.
- `list_slots_for_channel` — new; the full historical view the operator UI uses.

Steady state is one publication collecting analytics plus roughly one future
artifact being selected or produced — not "any nonterminal experiment blocks
another", and not a burst.

---

## 6. Readiness model

`get_autonomy_readiness` now returns tri-state checks (`ready` / `degraded` /
`blocked`) grouped into six categories: decision, production,
analytics_learning, provider_oauth, publishing_authorization, scheduler.

The Phase 17G check `publishing_not_enabled` ("Publishing authorization NOT
enabled", green when publishing was off) is gone. It was inverted: it would
have turned red exactly when a channel became correctly authorized. It is
replaced by `public_publishing_authorized`, which reports the real four-layer
decision, plus `global_publishing_gates`, which reports the kill-switch
position as state rather than as pass/fail — both off and both on are valid
operating positions.

`degraded` exists so that "configured and failing" is distinguishable from "not
configured", and so that expected bootstrap immaturity does not render as a
fault.

---

## 7. Files

New:

- `src/app/intelligence/experiments/lifecycle.py` — publication → experiment handoff, reconciliation
- `src/app/intelligence/experiments/outcome_bridge.py` — features → fidelity → outcome → ledger
- `src/app/intelligence/experiments/market_theme_fidelity.py` — deterministic market-theme evaluator
- `tests/test_closed_loop_autonomy_18d.py`

Changed:

- `analytics/auto_observer.py` — mark observing; run the outcome bridge every tick
- `analytics/aggregation.py` — collapse nested cumulative windows before summing
- `autonomy/publishing_cycle.py` — experiment handoff after confirmed PUBLIC
- `autonomy/production_cycle.py` — create the execution contract
- `autonomy/repository.py`, `autonomy/models.py` — terminal-slot queue semantics
- `experiments/planning_service.py` — channel-evidence bridge and intent
- `experiments/execution_service.py` — no-baseline vs unobservable
- `experiments/outcome_service.py` — seed exclusion, deterministic metric pick
- `workers/scheduler.py` — interval key; lifecycle reconciliation
- `application/autonomy_readiness.py` — categories and tri-state
- `api/routes/channels.py` — slot listing shows history
- frontend `Channels.tsx`, `types.ts` — categorised readiness
