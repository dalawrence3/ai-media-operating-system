"""Autonomy readiness projection for a channel (Phase 17G, extended in 18D).

Answers one question honestly: what can this channel actually do right now,
and what is stopping it?

Phase 17G answered a narrower version — "is the upstream machinery wired
up?" — with a flat list of booleans. That shape had two problems Phase 18D
has to fix before the surface can be trusted as an operations view:

  * It reported publishing readiness inverted. The check was literally
    "Publishing authorization NOT enabled", green when publishing was off.
    The moment a channel is legitimately authorized to publish, that check
    turns red — the readiness page would show a correctly-configured
    autonomous channel as broken.

  * A boolean cannot distinguish "not configured" from "configured and
    failing". An analytics observer that exists but has been paused after
    repeated provider failures is not the same as no observer at all, and
    an operator needs to see which one they have.

So checks now carry a tri-state status (ready / degraded / blocked) and a
category, and the view rolls categories up. `ready` is retained as
`status == ready` so existing callers keep working.

Read-only: computes from existing tables and config, spends no LLM or
YouTube calls (eligibility is read via the persisted-cache path only,
ai_provider=None), and never mutates anything.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

# ── Status vocabulary ────────────────────────────────────────────────────────
#
# READY    — this works and is doing its job.
# DEGRADED — configured, but not fully healthy; the loop still turns, more
#            slowly or with less evidence. Never blocks activation on its own.
# BLOCKED  — this cannot do its job at all. A blocked safety-critical
#            category means autonomous publishing must not be activated.
STATUS_READY = "ready"
STATUS_DEGRADED = "degraded"
STATUS_BLOCKED = "blocked"

_STATUS_RANK = {STATUS_READY: 0, STATUS_DEGRADED: 1, STATUS_BLOCKED: 2}

# Category keys. Kept stable — the UI groups by these.
CAT_DECISION = "decision"
CAT_PRODUCTION = "production"
CAT_ANALYTICS = "analytics_learning"
CAT_PROVIDER = "provider_oauth"
CAT_PUBLISHING = "publishing_authorization"
CAT_SCHEDULER = "scheduler"

_CATEGORY_LABELS = {
    CAT_DECISION: "Decision readiness",
    CAT_PRODUCTION: "Production readiness",
    CAT_ANALYTICS: "Analytics & learning readiness",
    CAT_PROVIDER: "OAuth / provider readiness",
    CAT_PUBLISHING: "Autonomous public publishing",
    CAT_SCHEDULER: "Scheduler health",
}


class AutonomyReadinessCheck(BaseModel, frozen=True):
    key: str
    label: str
    ready: bool
    detail: str
    # Phase 18D additions. Defaulted so any caller constructing a check
    # positionally or from an older payload still validates.
    status: str = STATUS_READY
    category: str = CAT_DECISION


class AutonomyReadinessCategory(BaseModel, frozen=True):
    """One category's rolled-up verdict — the worst status among its checks."""

    key: str
    label: str
    status: str
    check_keys: list[str]


class AutonomyReadinessView(BaseModel, frozen=True):
    channel_id: str
    checks: list[AutonomyReadinessCheck]
    # True only when every decision-category check is ready. This means the
    # upstream pipeline (market intelligence, eligibility, strategy) is
    # operational enough that a planning decision would have real input to
    # work with — it is NOT permission to publish anything.
    ready_for_decision_automation: bool
    # True only when every layer that gates an unattended public publish
    # passes: both global env gates, the per-channel authorization grant,
    # and the runtime account/rate checks. Deliberately independent of
    # ready_for_decision_automation — a fully wired decision pipeline never
    # implies publishing authorization.
    authorized_for_public_publishing: bool
    # Phase 18D additions.
    categories: list[AutonomyReadinessCategory] = []
    overall_status: str = STATUS_READY


def _check(
    *,
    key: str,
    label: str,
    status: str,
    detail: str,
    category: str,
) -> AutonomyReadinessCheck:
    return AutonomyReadinessCheck(
        key=key,
        label=label,
        ready=(status == STATUS_READY),
        detail=detail,
        status=status,
        category=category,
    )


# ── Decision category ────────────────────────────────────────────────────────


def _market_intelligence_configured_check(cfg: Any) -> AutonomyReadinessCheck:
    ok = bool(cfg.youtube_data_api_key)
    return _check(
        key="market_intelligence_configured",
        label="Market intelligence configured",
        status=STATUS_READY if ok else STATUS_BLOCKED,
        detail=(
            "YouTube Data API key configured (ACE_YOUTUBE_API_KEY)"
            if ok
            else "No YouTube Data API key configured — set ACE_YOUTUBE_API_KEY "
            "to enable live market collection"
        ),
        category=CAT_DECISION,
    )


def _recurring_refresh_check(
    conn: Any, workspace_id: str, cp_channel_id: str
) -> AutonomyReadinessCheck:
    from app.application.scheduler import list_schedules

    schedules = list_schedules(conn, workspace_id, is_active=True)
    match = next(
        (
            s
            for s in schedules
            if s.operation_type == "market_refresh" and s.channel_id == cp_channel_id
        ),
        None,
    )
    return _check(
        key="recurring_market_refresh",
        label="Recurring market refresh active",
        status=STATUS_READY if match else STATUS_BLOCKED,
        detail=(
            f"Recurring market_refresh schedule active (next run {match.next_run_at})"
            if match
            else "No active market_refresh schedule for this channel"
        ),
        category=CAT_DECISION,
    )


def _strategy_profile_check(conn: Any, cp_channel_id: str) -> AutonomyReadinessCheck:
    from app.control_plane import services as cp

    profile = cp.get_channel_strategy(conn, cp_channel_id)
    return _check(
        key="strategy_profile_active",
        label="Strategy profile active",
        status=STATUS_READY if profile else STATUS_BLOCKED,
        detail=(
            f"Active strategy profile v{profile.version}"
            if profile
            else "No active Channel Strategy Profile assigned"
        ),
        category=CAT_DECISION,
    )


def _eligible_opportunities_check(
    conn: Any, cp_channel_id: str, *, max_checked: int = 20
) -> AutonomyReadinessCheck:
    from app.intelligence.channel_bridge import get_intelligence_channel_id
    from app.intelligence.experiments.eligibility import (
        ExperimentEligibilityClassification,
    )
    from app.intelligence.experiments.eligibility_service import (
        assess_experiment_eligibility,
    )

    intel_channel_id = get_intelligence_channel_id(conn, cp_channel_id)
    if intel_channel_id is None:
        return _check(
            key="eligible_opportunities_available",
            label="Eligible opportunities available",
            status=STATUS_BLOCKED,
            detail="Channel has no intelligence-domain identity bridge yet",
            category=CAT_DECISION,
        )

    rows = conn.execute(
        """SELECT id FROM opportunities
           WHERE channel_id = ?
             AND current_lifecycle_state NOT IN ('rejected', 'archived', 'produced')
           ORDER BY id DESC LIMIT ?""",
        (intel_channel_id, max_checked),
    ).fetchall()

    eligible_count = 0
    for row in rows:
        # ai_provider=None: reuses any persisted semantic-fit cache for free,
        # never spends a live LLM call from a readiness check.
        a = assess_experiment_eligibility(conn, row["id"], intel_channel_id, ai_provider=None)
        if a.classification in (
            ExperimentEligibilityClassification.GENERAL_ELIGIBLE,
            ExperimentEligibilityClassification.EXPLORATION_ONLY,
        ):
            eligible_count += 1

    return _check(
        key="eligible_opportunities_available",
        label="Eligible opportunities available",
        status=STATUS_READY if eligible_count > 0 else STATUS_BLOCKED,
        detail=(
            f"{eligible_count} of {len(rows)} checked opportunities are eligible"
            if rows
            else "No active opportunities found for this channel"
        ),
        category=CAT_DECISION,
    )


def _decision_automation_check(conn: Any, cp_channel_id: str) -> AutonomyReadinessCheck:
    """Whether the operator has actually switched decision automation on.

    Distinct from every other decision check: those ask whether the machinery
    could work, this asks whether it is allowed to run unattended.
    """
    from app.intelligence.autonomy.repository import get_autonomy_policy

    policy = get_autonomy_policy(conn, cp_channel_id)
    if policy is None:
        # Degraded, not blocked: nothing is broken, an operator simply has
        # not configured the policy yet. Blocked is reserved for "this
        # cannot do its job", which would misrepresent an unconfigured
        # channel as a faulty one.
        return _check(
            key="decision_automation_enabled",
            label="Decision automation enabled",
            status=STATUS_DEGRADED,
            detail="No autonomy policy configured for this channel",
            category=CAT_DECISION,
        )
    on = bool(policy.decision_automation_enabled)
    return _check(
        key="decision_automation_enabled",
        label="Decision automation enabled",
        status=STATUS_READY if on else STATUS_DEGRADED,
        detail=(
            f"Enabled — cadence {policy.cadence_type.value}, queue target "
            f"{policy.queue_target}, {policy.timezone}"
            if on
            else "Decision automation is off; no experiment will be selected automatically"
        ),
        category=CAT_DECISION,
    )


# ── Production category ──────────────────────────────────────────────────────


def _production_automation_check(conn: Any, cp_channel_id: str) -> AutonomyReadinessCheck:
    from app.intelligence.autonomy.repository import get_autonomy_policy

    policy = get_autonomy_policy(conn, cp_channel_id)
    on = bool(policy and policy.production_automation_enabled)
    return _check(
        key="production_automation_enabled",
        label="Production automation enabled",
        status=STATUS_READY if on else STATUS_DEGRADED,
        detail=(
            "Enabled — filled slots are produced without per-video approval"
            if on
            else "Production automation is off; a filled slot waits for an operator"
        ),
        category=CAT_PRODUCTION,
    )


def _production_queue_check(conn: Any, cp_channel_id: str) -> AutonomyReadinessCheck:
    """Whether the bounded queue is healthy — and specifically not stuck.

    A slot that has exhausted its production retries occupies queue capacity
    without any prospect of progressing, which stalls the whole channel. That
    is degraded, not merely informational.
    """
    from app.intelligence.autonomy.repository import get_autonomy_policy, list_active_slots

    policy = get_autonomy_policy(conn, cp_channel_id)
    target = policy.queue_target if policy else 1
    slots = list_active_slots(conn, cp_channel_id)
    filled = [s for s in slots if s.state.value == "filled"]
    stuck = [
        s
        for s in filled
        if s.production_status is not None
        and s.production_status.value == "failed"
        and s.production_retry_count >= 2
    ]

    if stuck:
        status = STATUS_DEGRADED
        detail = (
            f"{len(stuck)} slot(s) have exhausted their production retries and "
            "are holding queue capacity; they need rescheduling or cancelling"
        )
    else:
        status = STATUS_READY
        detail = (
            f"{len(filled)} of {target} queue slot(s) filled, {len(slots) - len(filled)} reserved"
        )

    return _check(
        key="production_queue_healthy",
        label="Production queue healthy",
        status=status,
        detail=detail,
        category=CAT_PRODUCTION,
    )


# ── Analytics / learning category ────────────────────────────────────────────


def _analytics_observer_check(
    conn: Any, workspace_id: str, cp_channel_id: str
) -> AutonomyReadinessCheck:
    from app.application.scheduler import list_schedules

    schedules = list_schedules(conn, workspace_id, is_active=True)
    active = [
        s
        for s in schedules
        if s.operation_type == "analytics_observation" and s.channel_id == cp_channel_id
    ]
    paused = conn.execute(
        "SELECT COUNT(*) AS n FROM analytics_observation_state "
        "WHERE channel_id = ? AND observation_status = 'paused'",
        (cp_channel_id,),
    ).fetchone()
    paused_n = paused["n"] if paused else 0

    if not active:
        status, detail = STATUS_BLOCKED, "No active analytics_observation schedule for this channel"
    elif paused_n:
        status = STATUS_DEGRADED
        detail = (
            f"{len(active)} schedule(s) active, but {paused_n} publication(s) are "
            "paused after repeated observation failures"
        )
    else:
        status = STATUS_READY
        detail = f"{len(active)} publication(s) under active analytics observation"

    return _check(
        key="analytics_observer_active",
        label="Analytics observer active",
        status=status,
        detail=detail,
        category=CAT_ANALYTICS,
    )


def _learning_evidence_check(conn: Any, cp_channel_id: str) -> AutonomyReadinessCheck:
    """Whether the channel has its own measured evidence yet.

    Immature evidence is expected during bootstrap and is reported as
    degraded rather than blocked — the loop is working, it just has not
    accumulated enough publications yet. Reporting anything else would be
    exactly the false maturity this phase forbids.
    """
    from app.learning.cross_publication import get_channel_baselines

    try:
        baselines = get_channel_baselines(conn, channel_id=cp_channel_id)
    except Exception:
        baselines = []

    if not baselines:
        return _check(
            key="channel_learning_evidence",
            label="Channel learning evidence",
            status=STATUS_DEGRADED,
            detail=(
                "No cross-publication baselines yet — the channel is in bootstrap "
                "and planning relies on market intelligence"
            ),
            category=CAT_ANALYTICS,
        )

    best = max(baselines, key=lambda b: b.publication_count)
    mature = best.sample_maturity in ("directional", "actionable")
    return _check(
        key="channel_learning_evidence",
        label="Channel learning evidence",
        status=STATUS_READY if mature else STATUS_DEGRADED,
        detail=(
            f"{len(baselines)} baseline metric(s) over {best.publication_count} "
            f"publication(s); maturity '{best.sample_maturity}'"
        ),
        category=CAT_ANALYTICS,
    )


def _experiment_ledger_check(conn: Any, cp_channel_id: str) -> AutonomyReadinessCheck:
    """Whether any public publication has an experiment stuck behind it.

    This is the Phase 18D regression guard made visible: an experiment left
    in `in_production` after its video went public means the publication →
    experiment handoff did not run, and the learning loop is open.
    """
    row = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM publications pub
        JOIN publishing_plans pp ON pp.id = pub.publishing_plan_id
        LEFT JOIN production_plans prod ON prod.id = pp.production_plan_id
        JOIN experiments e ON e.id = COALESCE(pp.experiment_id, prod.experiment_id)
        WHERE pub.channel_id = ?
          AND pub.deleted_at IS NULL
          AND pub.visibility = 'public'
          AND pub.status = 'published'
          AND e.status IN ('draft', 'planned', 'in_production')
        """,
        (cp_channel_id,),
    ).fetchone()
    stale = row["n"] if row else 0
    return _check(
        key="experiment_ledger_current",
        label="Experiment ledger current",
        status=STATUS_READY if stale == 0 else STATUS_DEGRADED,
        detail=(
            "Every public publication's experiment has advanced past production"
            if stale == 0
            else f"{stale} public publication(s) have an experiment still in "
            "production state; reconciliation will repair them on the next tick"
        ),
        category=CAT_ANALYTICS,
    )


# ── Provider / OAuth category ────────────────────────────────────────────────


def _provider_account_check(conn: Any, cp_channel_id: str) -> AutonomyReadinessCheck:
    from app.publishing.authorization import BLOCKING_ACCOUNT_STATUSES, get_publishing_account

    try:
        account_id, status_str = get_publishing_account(conn, cp_channel_id)
    except Exception as exc:
        return _check(
            key="provider_account_healthy",
            label="Provider account healthy",
            status=STATUS_BLOCKED,
            detail=f"Could not resolve a platform account: {exc}",
            category=CAT_PROVIDER,
        )

    if account_id is None:
        return _check(
            key="provider_account_healthy",
            label="Provider account healthy",
            status=STATUS_BLOCKED,
            detail="No connected YouTube account for this channel",
            category=CAT_PROVIDER,
        )
    if status_str in BLOCKING_ACCOUNT_STATUSES:
        return _check(
            key="provider_account_healthy",
            label="Provider account healthy",
            status=STATUS_BLOCKED,
            detail=f"Account status '{status_str}' blocks autonomous provider operations",
            category=CAT_PROVIDER,
        )
    if status_str == "credential_expiring":
        return _check(
            key="provider_account_healthy",
            label="Provider account healthy",
            status=STATUS_DEGRADED,
            detail="Account credential is expiring; the refresh path still works",
            category=CAT_PROVIDER,
        )
    return _check(
        key="provider_account_healthy",
        label="Provider account healthy",
        status=STATUS_READY,
        detail=f"Connected YouTube account (status '{status_str}')",
        category=CAT_PROVIDER,
    )


def _release_scope_check(conn: Any, cp_channel_id: str) -> AutonomyReadinessCheck:
    """Whether the granted OAuth scopes include public release.

    Upload and release are separate grants: a credential that can upload
    privately cannot necessarily change a video's privacy status, and the
    remedy for a missing release scope is a specific re-consent rather than
    a reconnect.
    """
    from app.publishing.authorization import _has_release_scope, get_publishing_account

    try:
        account_id, _status = get_publishing_account(conn, cp_channel_id)
        if account_id is None:
            granted = False
        else:
            granted = _has_release_scope(conn, account_id=account_id, channel_id=cp_channel_id)
    except Exception:
        granted = False

    return _check(
        key="release_scope_granted",
        label="Public-release OAuth scope granted",
        status=STATUS_READY if granted else STATUS_BLOCKED,
        detail=(
            "youtube.force-ssl granted — the credential can make a video public"
            if granted
            else "youtube.force-ssl not granted — uploads can only stay private "
            "until the account re-consents"
        ),
        category=CAT_PROVIDER,
    )


# ── Publishing authorization category ────────────────────────────────────────


def _publishing_authorization_check(conn: Any, cp_channel_id: str) -> AutonomyReadinessCheck:
    """The real four-layer publishing decision, reported as it is.

    Replaces Phase 17G's inverted "Publishing authorization NOT enabled"
    check, which went red precisely when the channel became correctly
    authorized. Here `ready` means "this channel may publish autonomously
    right now", which is the thing an operator actually needs to know.
    """
    from app.publishing.authorization import BlockReason, evaluate_publishing_authorization

    try:
        decision = evaluate_publishing_authorization(conn, channel_id=cp_channel_id)
    except Exception as exc:
        return _check(
            key="public_publishing_authorized",
            label="Autonomous public publishing authorized",
            status=STATUS_BLOCKED,
            detail=f"Authorization could not be evaluated: {exc}",
            category=CAT_PUBLISHING,
        )

    if decision.allowed:
        return _check(
            key="public_publishing_authorized",
            label="Autonomous public publishing authorized",
            status=STATUS_READY,
            detail=(
                f"Authorized — {decision.publications_last_24h}/"
                f"{decision.max_publications_per_24h} publications in the last 24h"
            ),
            category=CAT_PUBLISHING,
        )

    # A rate limit is the system working as designed, not a fault: the
    # channel is authorized and simply already published its allowance. It is
    # therefore `degraded` (cannot publish this instant) rather than
    # `blocked` (cannot publish at all), and the view's
    # authorized_for_public_publishing flag counts it as authorized.
    only_rate_limited = decision.blocked_by == [BlockReason.rate_limit_reached]
    if only_rate_limited:
        detail = (
            f"Authorized — currently at the 24h ceiling "
            f"({decision.publications_last_24h}/{decision.max_publications_per_24h}); "
            "the next slot publishes once the window clears"
        )
    else:
        detail = decision.detail or (
            "Blocked by: " + ", ".join(r.value for r in decision.blocked_by)
        )
    return _check(
        key="public_publishing_authorized",
        label="Autonomous public publishing authorized",
        status=STATUS_DEGRADED if only_rate_limited else STATUS_BLOCKED,
        detail=detail,
        category=CAT_PUBLISHING,
    )


def _emergency_stop_check(cfg: Any) -> AutonomyReadinessCheck:
    """Reports the two global kill switches as state, not as pass/fail.

    Both being off is a perfectly good operating state (stood down), and both
    being on is also a perfectly good operating state (activated). Neither is
    an error, so this check is always ready — it exists to make the current
    position unambiguous on the page.
    """
    live = bool(cfg.publishing_live_enabled)
    public = bool(cfg.release_public_enabled)
    if live and public:
        detail = "Both global gates ON — emergency stop available by unsetting either"
    elif not live and not public:
        detail = "Both global gates OFF — no process can publish or release"
    else:
        detail = (
            f"ACE_PUBLISHING_LIVE_ENABLED={str(live).lower()}, "
            f"ACE_RELEASE_PUBLIC_ENABLED={str(public).lower()} — "
            "uploads may occur but nothing can be made public"
        )
    return _check(
        key="global_publishing_gates",
        label="Global publishing gates",
        status=STATUS_READY,
        detail=detail,
        category=CAT_PUBLISHING,
    )


# ── Scheduler category ───────────────────────────────────────────────────────


def _scheduler_health_check(
    conn: Any, workspace_id: str, cp_channel_id: str
) -> AutonomyReadinessCheck:
    """Whether the schedules that drive the loop exist and are running on time.

    An overdue schedule is the signature of a stopped daemon: the row says
    it should have run and nothing did. That is degraded rather than
    blocked, because the configuration is right and only the process is
    missing.
    """
    from datetime import UTC, datetime

    from app.application.scheduler import list_schedules

    required = {
        "autonomy_decision_cycle",
        "autonomous_production_cycle",
        "autonomous_publishing_cycle",
        "market_refresh",
    }
    schedules = [
        s
        for s in list_schedules(conn, workspace_id, is_active=True)
        if s.channel_id == cp_channel_id
    ]
    present = {s.operation_type for s in schedules}
    missing = sorted(required - present)

    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    overdue = sorted(
        s.operation_type
        for s in schedules
        if s.operation_type in required and s.next_run_at and s.next_run_at < now
    )

    if missing:
        status = STATUS_DEGRADED
        detail = f"Inactive or missing schedule(s): {', '.join(missing)}"
    elif overdue:
        status = STATUS_DEGRADED
        detail = (
            f"Overdue schedule(s): {', '.join(overdue)} — the scheduler daemon may not be running"
        )
    else:
        status = STATUS_READY
        detail = f"All {len(required)} autonomy schedules active and on time"

    return _check(
        key="autonomy_schedules_healthy",
        label="Autonomy schedules healthy",
        status=status,
        detail=detail,
        category=CAT_SCHEDULER,
    )


# ── Aggregation ──────────────────────────────────────────────────────────────


def _worst(statuses: list[str]) -> str:
    return max(statuses, key=lambda s: _STATUS_RANK.get(s, 0)) if statuses else STATUS_READY


def get_autonomy_readiness(
    conn: Any, workspace_id: str, cp_channel_id: str
) -> AutonomyReadinessView:
    """Compute the full autonomy-readiness projection for one channel.

    Every check is defensive: a check that cannot be computed reports
    blocked with the reason rather than raising, so one broken subsystem
    never hides the state of the others.
    """
    from app.core.config import get_config

    cfg = get_config()

    builders = [
        # Decision
        lambda: _market_intelligence_configured_check(cfg),
        lambda: _recurring_refresh_check(conn, workspace_id, cp_channel_id),
        lambda: _strategy_profile_check(conn, cp_channel_id),
        lambda: _eligible_opportunities_check(conn, cp_channel_id),
        lambda: _decision_automation_check(conn, cp_channel_id),
        # Production
        lambda: _production_automation_check(conn, cp_channel_id),
        lambda: _production_queue_check(conn, cp_channel_id),
        # Analytics / learning
        lambda: _analytics_observer_check(conn, workspace_id, cp_channel_id),
        lambda: _learning_evidence_check(conn, cp_channel_id),
        lambda: _experiment_ledger_check(conn, cp_channel_id),
        # Provider
        lambda: _provider_account_check(conn, cp_channel_id),
        lambda: _release_scope_check(conn, cp_channel_id),
        # Publishing
        lambda: _publishing_authorization_check(conn, cp_channel_id),
        lambda: _emergency_stop_check(cfg),
        # Scheduler
        lambda: _scheduler_health_check(conn, workspace_id, cp_channel_id),
    ]

    checks: list[AutonomyReadinessCheck] = []
    for build in builders:
        try:
            checks.append(build())
        except Exception as exc:  # noqa: BLE001 — one bad check must not hide the rest
            checks.append(
                _check(
                    key="readiness_check_failed",
                    label="Readiness check failed",
                    status=STATUS_BLOCKED,
                    detail=str(exc),
                    category=CAT_DECISION,
                )
            )

    categories: list[AutonomyReadinessCategory] = []
    for cat_key, cat_label in _CATEGORY_LABELS.items():
        members = [c for c in checks if c.category == cat_key]
        if not members:
            continue
        categories.append(
            AutonomyReadinessCategory(
                key=cat_key,
                label=cat_label,
                status=_worst([c.status for c in members]),
                check_keys=[c.key for c in members],
            )
        )

    # Decision readiness deliberately excludes the operator's own on/off
    # switch: a channel whose pipeline has real input to work with is
    # decision-ready whether or not automation happens to be enabled today.
    decision_inputs = [
        c for c in checks if c.category == CAT_DECISION and c.key != "decision_automation_enabled"
    ]
    ready_for_decision_automation = all(c.ready for c in decision_inputs)

    # Authorization is a durable posture, not a moment-to-moment permission.
    #
    # A channel that has published its allowance for the day is fully
    # authorized and simply at its ceiling — reporting that as "Not
    # authorized" would be the same conflation this module already removed
    # from the inverted Phase 17G gate check, and it would tell an operator
    # their correctly-activated channel was switched off. The check's own
    # status stays `degraded` while rate-limited, because "can it publish
    # right now" genuinely is no; this flag answers the different question
    # "is it authorized to publish at all".
    authorized = next((c for c in checks if c.key == "public_publishing_authorized"), None)
    authorized_for_public_publishing = bool(
        authorized and authorized.status in (STATUS_READY, STATUS_DEGRADED)
    )

    return AutonomyReadinessView(
        channel_id=cp_channel_id,
        checks=checks,
        ready_for_decision_automation=ready_for_decision_automation,
        authorized_for_public_publishing=authorized_for_public_publishing,
        categories=categories,
        overall_status=_worst([c.status for c in checks]),
    )
