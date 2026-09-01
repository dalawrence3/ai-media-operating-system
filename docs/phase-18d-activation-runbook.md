# Phase 18D — Activation Runbook

The Phase 18D readiness audit passed 20/20 (A–T) with no BLOCKED category and
no safety-critical block. Everything below is verified and ready. The final
activation was deliberately **not** performed automatically: enabling
autonomous public publishing to a live YouTube channel is an outward-facing
action that should be taken by a person.

Run these four steps in order. Each is independently verifiable, and the system
cannot publish until all four are complete.

---

## Before you start — read this

**The rate-limit collision described in the first version of this runbook is
fixed.** Slot reservation is now rate-aware: the decision cycle walks its own
cadence and takes the first slot the publication ceiling does not already
guarantee to refuse. It has already done so on this channel — the Aug 30 09:00
slot was skipped and `2026-08-31 09:00 America/New_York` reserved instead, and
that slot is produced and READY. There is no longer any reason to time
activation around the ceiling window.

Two things to know:

1. **The publishing schedule still carries a stale `next_run_at`.** Schedule
   reconciliation only touches active schedules, and this one is inactive by
   design, so it was left alone. After enabling it in Step 3, restart (or the
   next daemon start) reconciles it onto its 600-second cadence. Without that
   it would wait out a timestamp inherited from the interval defect.

2. **Slot 3 is already produced and ready** for 2026-08-31 09:00 local. On
   activation it becomes the first autonomous publication, at its scheduled
   time — not immediately.

## Step 1 — Global gates

Edit `.env.local`:

```
ACE_PUBLISHING_LIVE_ENABLED=true
ACE_RELEASE_PUBLIC_ENABLED=true
```

Then restart so the daemons pick them up:

```bash
bash scripts/service.sh restart
```

Verify the system is still blocked, now only by the channel layer:

```bash
curl -s -H "X-Dev-Actor: dev:studio-user" "http://127.0.0.1:8000/api/v1/workspaces/local-dev/channels/623a13aa-eaf6-4b3c-b546-6f4b1a666fa5/publishing-authorization" | python3 -m json.tool
```

Expect `allowed: false` with `channel_not_authorized` among `blocked_by`.

## Step 2 — Channel authorization

Use the canonical API. Never write `channel_publishing_authorizations`
directly — the API is what emits the `cp_events` audit record.

```bash
curl -s -X PUT -H "Content-Type: application/json" -H "X-Dev-Actor: operator:activation" -d '{"authorized": true, "confirm": true, "max_publications_per_24h": 1, "missed_slot_grace_minutes": 120}' "http://127.0.0.1:8000/api/v1/workspaces/local-dev/channels/623a13aa-eaf6-4b3c-b546-6f4b1a666fa5/publishing-authorization" | python3 -m json.tool
```

Re-run the verification from Step 1. Expect `allowed: false` with
`rate_limit_reached` as the **only** remaining blocker — that is the ceiling
doing its job, not a fault.

## Step 3 — Publishing scheduler

```bash
sqlite3 ~/.local/share/ai-content-engine/content.db "UPDATE app_schedule_definitions SET is_active = 1 WHERE operation_type = 'autonomous_publishing_cycle' AND channel_id = '623a13aa-eaf6-4b3c-b546-6f4b1a666fa5';"
```

Then restart so the newly-active schedule is reconciled onto its real cadence.
Its persisted `next_run_at` predates the interval fix, and reconciliation skips
inactive schedules, so without this it would sit idle for up to a day:

```bash
bash scripts/service.sh restart
```

Confirm it is now on a 10-minute cadence:

```bash
sqlite3 ~/.local/share/ai-content-engine/content.db "SELECT operation_type, is_active, last_run_at, next_run_at FROM app_schedule_definitions WHERE operation_type='autonomous_publishing_cycle';"
```

## Step 4 — Confirm no premature provider operation

```bash
sqlite3 ~/.local/share/ai-content-engine/content.db "SELECT 'publications=' || COUNT(*) FROM publications; SELECT 'upload_attempts=' || COUNT(*) FROM publishing_upload_attempts; SELECT id || ' ' || provider_video_id || ' ' || visibility FROM publications;"
```

Expect **exactly** `publications=4`, `upload_attempts=1`, publication 2 still
`private`, publication 4 still `public`. If any of those changed, stand the
system down immediately (below) and investigate before doing anything else.

Slot 3 is READY for 2026-08-31 09:00 local, so the publishing cycle will
return `NOT_DUE` until that time rather than acting immediately. The
due-ness check runs before any provider client is constructed.

---

## Standing the system down

Any **one** of these is sufficient. They are independent by design.

| Stop | How | Effect |
|------|-----|--------|
| Global publish gate | `ACE_PUBLISHING_LIVE_ENABLED=false` + restart | Halts every channel; no upload possible |
| Global release gate | `ACE_RELEASE_PUBLIC_ENABLED=false` + restart | Uploads may occur but nothing becomes public |
| Channel authorization | `PUT .../publishing-authorization` with `authorized: false` | Immediate, no restart; re-checked *between upload and release*, so it stops a video mid-flight while it is still private |
| Publishing scheduler | set `is_active = 0` on the schedule row | The cycle stops being dispatched |

The channel-authorization revoke is the fastest and the only one that takes
effect without a restart.

---

## What to watch in the first 48 hours

- `experiment_outcomes` should gain a row for `exp-slot-1` once YouTube reports
  analytics for `LTbHn3SAjks`. Until then `insufficient_analytics` is the
  correct, honest state — not a fault.
- `channel_performance_baselines.sample_maturity` should move off
  `insufficient` as publication count rises. Until it reaches `directional` the
  planner stays in bootstrap and keeps exploring, which is intended.
- The readiness surface on the Channel page should show
  **Autonomous public publishing: ready** once the ceiling window clears.
- No slot should ever show `publish_status = 'blocked'` for more than its grace
  window without an operator noticing.
