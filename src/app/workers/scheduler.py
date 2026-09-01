"""DB-driven scheduler: reads app_schedule_definitions and enqueues due jobs.

Design:
  - Canonical schedule state lives in app_schedule_definitions (PostgreSQL / SQLite).
  - Redis/RQ is execution transport only; it is never the scheduling source of truth.
  - Scheduler process runs as a daemon (e.g., `rq worker --with-scheduler` or standalone).
  - Due-job determination is fully deterministic, timezone-aware, and idempotent.
  - Duplicate enqueue protection: last_run_at is updated atomically before enqueue.

Usage (standalone daemon):
  ACE_DATABASE_URL=postgresql://... ACE_REDIS_URL=redis://... python -m app.workers.scheduler

Usage (with RQ worker built-in scheduler):
  rq worker --with-scheduler  (uses rq.Scheduler under the hood for periodic jobs)

The standalone daemon polls at a configurable interval and is restart-safe.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


def get_due_schedules(conn: Any, now: datetime | None = None) -> list[dict]:
    """Return schedule definitions whose next_run_at is due (or NULL and active).

    A schedule is due when:
      - is_active = 1
      - next_run_at IS NULL  (never run)
      - OR next_run_at <= now (in ISO8601 UTC string comparison)
    """
    if now is None:
        now = datetime.now(UTC)
    now_str = now.strftime("%Y-%m-%dT%H:%M:%S")
    rows = conn.execute(
        """
        SELECT id, workspace_id, channel_id, name, operation_type,
               schedule_type, schedule_config_json, timezone,
               last_run_at, next_run_at, actor
        FROM app_schedule_definitions
        WHERE is_active = 1
          AND (next_run_at IS NULL OR next_run_at <= ?)
        ORDER BY next_run_at ASC
        """,
        (now_str,),
    ).fetchall()
    return [dict(row) for row in rows]


def compute_next_run_at(schedule_config: dict, schedule_type: str, tz: str = "UTC") -> str | None:
    """Compute the next execution time for a schedule definition.

    Returns an ISO8601 UTC string, or None if the schedule is exhausted.
    Currently supports: @daily, @hourly, @weekly shortcuts and interval seconds.
    """
    now = datetime.now(UTC)

    if schedule_type == "interval":
        # 'interval_seconds' is the canonical key written by
        # app.application.scheduler.create_schedule and by every producer of
        # app_schedule_definitions rows. 'seconds' is accepted only as a
        # legacy alias: reading it first meant every real schedule silently
        # fell through to the 86400 default, so an hourly decision cycle ran
        # daily and a 10-minute publishing cycle could miss its slot's whole
        # grace window.
        raw = schedule_config.get("interval_seconds", schedule_config.get("seconds"))
        seconds = int(raw) if raw is not None else 86400
        from datetime import timedelta

        next_dt = now + timedelta(seconds=seconds)
        return next_dt.strftime("%Y-%m-%dT%H:%M:%S")

    # Named shortcuts map to fixed intervals.
    shortcuts = {"@daily": 86400, "@hourly": 3600, "@weekly": 604800}
    shortcut_key = schedule_config.get("shortcut", "")
    if shortcut_key in shortcuts:
        from datetime import timedelta

        next_dt = now + timedelta(seconds=shortcuts[shortcut_key])
        return next_dt.strftime("%Y-%m-%dT%H:%M:%S")

    # once-type schedules do not repeat.
    if schedule_type == "once":
        return None

    # Default: daily
    from datetime import timedelta

    return (now + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")


def mark_schedule_ran(conn: Any, schedule_id: str, next_run_at: str | None) -> None:
    """Update last_run_at and next_run_at for a schedule. Called before enqueue."""
    now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    conn.execute(
        """
        UPDATE app_schedule_definitions
        SET last_run_at = ?, next_run_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (now_str, next_run_at, now_str, schedule_id),
    )
    conn.commit()


def _resolve_publishing_account(conn: Any, channel_id: str) -> tuple[str, str]:
    """Return (account_id, external_account_id) for a channel's YouTube account."""
    from app.publishing.authorization import get_publishing_account

    account_id, _status = get_publishing_account(conn, channel_id)
    if account_id is None:
        raise RuntimeError(f"Channel {channel_id} has no YouTube platform account.")
    return account_id, channel_id


def _build_live_publishing_provider(conn: Any, *, channel_id: str, workspace_id: str) -> Any:
    """Build a fully gated, authenticated YouTube publishing provider.

    Routes through `build_authenticated_youtube_provider`, which enforces the
    pre-existing upload gate sequence (ACE_PUBLISHING_LIVE_ENABLED, token
    refresh, youtube.upload scope, live channel-identity match) and raises
    before any network call if any of it fails. Phase 18C deliberately does
    not reimplement or relax that gate — it layers channel authorization on
    top of it.
    """
    from app.api.routes.oauth import get_oauth_client
    from app.publishing.upload_gate import build_authenticated_youtube_provider

    account_id, _ = _resolve_publishing_account(conn, channel_id)
    return build_authenticated_youtube_provider(
        conn,
        account_id=account_id,
        workspace_id=workspace_id,
        channel_id=channel_id,
        oauth_client=get_oauth_client(),
    )


def _build_live_youtube_client(conn: Any, *, channel_id: str, workspace_id: str) -> Any:
    """Build a raw YouTube API client for release and reconciliation reads.

    Checks the release scope specifically — the release step needs
    youtube.force-ssl, which the upload scope alone does not grant.
    """
    from app.api.routes.oauth import get_oauth_client
    from app.publishing.providers.youtube import RealYouTubeAPIClient
    from app.publishing.upload_gate import (
        _load_client_secrets,
        check_release_scope,
        resolve_upload_token,
    )

    account_id, _ = _resolve_publishing_account(conn, channel_id)
    oauth_client = get_oauth_client()
    stored = resolve_upload_token(
        conn,
        account_id=account_id,
        workspace_id=workspace_id,
        channel_id=channel_id,
        oauth_client=oauth_client,
    )
    check_release_scope(stored)
    secrets = _load_client_secrets(oauth_client)
    return RealYouTubeAPIClient(
        access_token=stored.access_token,
        refresh_token=stored.refresh_token,
        token_uri=secrets.get("token_uri"),
        client_id=secrets.get("client_id"),
        client_secret=secrets.get("client_secret"),
    )


def run_scheduler_tick(conn: Any, queue=None, oauth_client: Any = None) -> list[str]:
    """Single scheduler tick: find due schedules, update DB, enqueue or execute inline.

    analytics_observation, market_refresh, and autonomy_decision_cycle
    schedules are executed inline (no Redis needed). All other schedule
    types are enqueued to RQ.

    Returns list of RQ job IDs (empty if only inline operations ran).
    Idempotent: mark_schedule_ran() is called before execution so concurrent
    ticks skip already-claimed schedules.
    """
    import json
    from datetime import timedelta

    from app.workers.jobs import enqueue_scheduled_operations

    due = get_due_schedules(conn)
    if not due:
        return []

    now = datetime.now(UTC)
    enqueued_ids: list[str] = []
    for sched in due:
        schedule_id = sched["id"]
        workspace_id = sched["workspace_id"]
        operation_type = sched.get("operation_type", "")
        schedule_type = sched["schedule_type"]
        schedule_config = json.loads(sched.get("schedule_config_json") or "{}")
        tz = sched.get("timezone") or "UTC"

        if operation_type == "analytics_observation":
            pub_id = int(schedule_config.get("publication_id", 0))
            if pub_id == 0:
                logger.warning(
                    "Scheduler: analytics_observation schedule %s missing publication_id",
                    schedule_id,
                )
                continue

            # Idempotency lock: advance next_run_at before executing so a concurrent
            # tick won't pick up the same schedule.  run_observation() will overwrite
            # next_run_at with the precise age-aware interval at the end of its tick.
            interval_seconds = int(schedule_config.get("interval_seconds", 3600))
            lock_next = (now + timedelta(seconds=interval_seconds)).strftime("%Y-%m-%dT%H:%M:%S")
            mark_schedule_ran(conn, schedule_id, lock_next)

            try:
                from app.analytics.auto_observer import run_observation

                run_observation(
                    conn,
                    publication_id=pub_id,
                    schedule_id=schedule_id,
                    oauth_client=oauth_client,
                )
            except Exception as exc:
                # run_observation handles its own errors; this is a last-resort guard.
                logger.error(
                    "Scheduler: inline observation for schedule %s raised unexpectedly: %s",
                    schedule_id,
                    exc,
                    exc_info=True,
                )
            continue

        if operation_type == "market_refresh":
            cp_channel_id = sched.get("channel_id")

            # Idempotency lock first, same pattern as analytics_observation —
            # a concurrent tick must never start a second refresh for the
            # same channel while one is still "in flight" from this tick's
            # perspective. run_market_refresh_cycle's own stages are each
            # individually safe to re-run (idempotent job creation, append-
            # only scoring), but the lock still prevents wasted duplicate
            # work and duplicate external calls within one poll interval.
            next_run = compute_next_run_at(schedule_config, schedule_type, tz)
            mark_schedule_ran(conn, schedule_id, next_run)

            if not cp_channel_id:
                logger.warning(
                    "Scheduler: market_refresh schedule %s missing channel_id", schedule_id
                )
                continue
            try:
                from app.core.config import get_config
                from app.intelligence.channel_bridge import get_intelligence_channel_id
                from app.intelligence.market.refresh_service import run_market_refresh_cycle

                intel_channel_id = get_intelligence_channel_id(conn, cp_channel_id)
                if intel_channel_id is None:
                    logger.info(
                        "Scheduler: market_refresh schedule %s — channel %s has no "
                        "bootstrapped intelligence identity, skipping this tick",
                        schedule_id,
                        cp_channel_id,
                    )
                    continue

                cfg = get_config()
                result = run_market_refresh_cycle(
                    conn,
                    channel_id=intel_channel_id,
                    workspace_id=workspace_id,
                    api_key=getattr(cfg, "youtube_data_api_key", "") or "",
                    max_velocity_videos=int(schedule_config.get("max_velocity_videos", 50)),
                )
                logger.info(
                    "Scheduler: market_refresh for channel %s — velocity_new=%d "
                    "clusters=%d sync(created=%d refreshed=%d scored=%d) errors=%s",
                    cp_channel_id,
                    result.velocity_observations_new,
                    result.clusters_produced,
                    result.sync_created,
                    result.sync_refreshed,
                    result.sync_scored,
                    result.errors or "none",
                )
            except Exception as exc:
                # A market-data hiccup must never break the tick loop —
                # run_market_refresh_cycle already isolates its own stage
                # errors; this is a last-resort guard around channel
                # resolution and config loading.
                logger.error(
                    "Scheduler: market_refresh for schedule %s raised unexpectedly: %s",
                    schedule_id,
                    exc,
                    exc_info=True,
                )
            continue

        if operation_type == "autonomy_decision_cycle":
            cp_channel_id = sched.get("channel_id")

            # Same idempotency-lock pattern as market_refresh: advance
            # next_run_at before executing. run_decision_cycle has its own
            # internal hour-bucketed lock (cp_operation_executions) as the
            # real concurrency guard — this lock just prevents the *next*
            # tick from re-polling within the same interval.
            next_run = compute_next_run_at(schedule_config, schedule_type, tz)
            mark_schedule_ran(conn, schedule_id, next_run)

            if not cp_channel_id:
                logger.warning(
                    "Scheduler: autonomy_decision_cycle schedule %s missing channel_id",
                    schedule_id,
                )
                continue
            try:
                from app.core.config import get_config
                from app.intelligence.autonomy.decision_cycle import run_decision_cycle
                from app.intelligence.autonomy.repository import get_autonomy_policy

                # Cheapest possible check first (section 17): a channel with
                # automation disabled costs nothing beyond one SELECT.
                policy = get_autonomy_policy(conn, cp_channel_id)
                if policy is None or not policy.decision_automation_enabled:
                    continue

                cfg = get_config()
                result = run_decision_cycle(
                    conn,
                    cp_channel_id=cp_channel_id,
                    workspace_id=workspace_id,
                    youtube_api_key=getattr(cfg, "youtube_data_api_key", "") or "",
                    anthropic_api_key=getattr(cfg, "anthropic_api_key", "") or "",
                )
                logger.info(
                    "Scheduler: autonomy_decision_cycle for channel %s — outcome=%s reason=%s",
                    cp_channel_id,
                    result.outcome.value,
                    result.reason,
                )
            except Exception as exc:
                # run_decision_cycle isolates its own step errors internally;
                # this is a last-resort guard around channel/config loading.
                logger.error(
                    "Scheduler: autonomy_decision_cycle for schedule %s raised unexpectedly: %s",
                    schedule_id,
                    exc,
                    exc_info=True,
                )
            continue

        if operation_type == "autonomous_production_cycle":
            cp_channel_id = sched.get("channel_id")

            next_run = compute_next_run_at(schedule_config, schedule_type, tz)
            mark_schedule_ran(conn, schedule_id, next_run)

            if not cp_channel_id:
                logger.warning(
                    "Scheduler: autonomous_production_cycle schedule %s missing channel_id",
                    schedule_id,
                )
                continue
            try:
                from app.core.config import get_config
                from app.intelligence.autonomy.repository import (
                    find_slot_needing_production,
                    get_autonomy_policy,
                )

                # Cheap check first (section 18): production is disabled, or
                # there's nothing to produce — either way, cost is one or two
                # SELECTs, never a pipeline stage invocation.
                policy = get_autonomy_policy(conn, cp_channel_id)
                if policy is None or not policy.production_automation_enabled:
                    continue
                if find_slot_needing_production(conn, cp_channel_id) is None:
                    continue

                from app.intelligence.autonomy.production_cycle import run_production_cycle

                cfg = get_config()
                result = run_production_cycle(
                    conn,
                    cp_channel_id=cp_channel_id,
                    workspace_id=workspace_id,
                    anthropic_api_key=getattr(cfg, "anthropic_api_key", "") or "",
                    elevenlabs_api_key=getattr(cfg, "elevenlabs_api_key", "") or "",
                )
                logger.info(
                    "Scheduler: autonomous_production_cycle for channel %s — outcome=%s reason=%s",
                    cp_channel_id,
                    result.outcome.value,
                    result.reason,
                )
            except Exception as exc:
                # run_production_cycle isolates its own step errors internally;
                # this is a last-resort guard around channel/config loading.
                logger.error(
                    "Scheduler: autonomous_production_cycle for schedule %s "
                    "raised unexpectedly: %s",
                    schedule_id,
                    exc,
                    exc_info=True,
                )
            continue

        if operation_type == "autonomous_publishing_cycle":
            cp_channel_id = sched.get("channel_id")

            next_run = compute_next_run_at(schedule_config, schedule_type, tz)
            mark_schedule_ran(conn, schedule_id, next_run)

            if not cp_channel_id:
                logger.warning(
                    "Scheduler: autonomous_publishing_cycle schedule %s missing channel_id",
                    schedule_id,
                )
                continue
            try:
                from app.intelligence.autonomy.repository import find_slot_ready_to_publish
                from app.publishing.authorization import (
                    get_channel_publishing_authorization,
                )

                # Cheap checks first (section 20). Publishing ticks run far more
                # often than the daily publication cadence, so the common case —
                # nothing authorized, or nothing due — must cost only a couple of
                # SELECTs and never touch a provider.
                auth = get_channel_publishing_authorization(conn, cp_channel_id)
                if auth is None or not auth.authorized:
                    continue
                if find_slot_ready_to_publish(conn, cp_channel_id) is None:
                    continue

                from app.core.config import get_config
                from app.intelligence.autonomy.publishing_cycle import run_publishing_cycle

                cfg = get_config()
                # The global gates are the emergency stop: if either is off there
                # is nothing for this cycle to do, and we skip before building any
                # authenticated provider.
                if not (cfg.publishing_live_enabled and cfg.release_public_enabled):
                    continue

                result = run_publishing_cycle(
                    conn,
                    cp_channel_id=cp_channel_id,
                    workspace_id=workspace_id,
                    provider_factory=_build_live_publishing_provider,
                    yt_client_factory=_build_live_youtube_client,
                )
                logger.info(
                    "Scheduler: autonomous_publishing_cycle for channel %s — outcome=%s reason=%s",
                    cp_channel_id,
                    result.outcome.value,
                    result.reason,
                )
            except Exception as exc:
                # run_publishing_cycle isolates its own step errors internally;
                # this is a last-resort guard around channel/config loading.
                logger.error(
                    "Scheduler: autonomous_publishing_cycle for schedule %s raised "
                    "unexpectedly: %s",
                    schedule_id,
                    exc,
                    exc_info=True,
                )
            continue

        # Standard RQ path for all other operation types.
        next_run = compute_next_run_at(schedule_config, schedule_type, tz)
        mark_schedule_ran(conn, schedule_id, next_run)
        job_ids = enqueue_scheduled_operations(
            workspace_id=workspace_id,
            due_schedule_ids=[schedule_id],
            actor=sched.get("actor") or "system:scheduler",
            queue=queue,
        )
        enqueued_ids.extend(job_ids)
        logger.info(
            "Scheduler enqueued job for schedule %s (%s) next=%s",
            schedule_id,
            sched.get("name"),
            next_run,
        )

    return enqueued_ids


def run_scheduler_daemon(poll_interval_seconds: int = 60) -> None:  # pragma: no cover
    """Run the scheduler as a blocking daemon process.

    Startup:
      1. Build OAuth client from config (best-effort; analytics observations degrade
         gracefully if credentials are not configured).
      2. Reconcile any public publications that have no active observation schedule.

    Each tick:
      - Re-reconcile (idempotent) so newly-released publications are adopted.
      - run_scheduler_tick() dispatches analytics_observation inline and other
        operation types via RQ.

    Restart-safe: mark_schedule_ran() is written before execution.
    Provider/analytics failures never kill the daemon loop.
    """
    from app.analytics.observation import reconcile_unobserved_publications
    from app.application.scheduler import reconcile_schedule_next_runs
    from app.core.config import get_config
    from app.core.database import get_db_connection
    from app.intelligence.experiments.lifecycle import reconcile_experiment_lifecycle

    cfg = get_config()

    # Phase 18E — refuse to run against a database this runtime must not touch.
    # This daemon IS the autonomous publisher; starting it against the E2E test
    # database would make the live system appear to have lost all its state,
    # and starting a test runtime against the operational database is the
    # incident this guard exists to prevent.
    from app.core.runtime_mode import assert_runtime_isolation

    assert_runtime_isolation(cfg.db_path)

    logger.info("Scheduler daemon starting (poll interval: %ds)", poll_interval_seconds)

    # Build OAuth client once for analytics observation ticks.
    oauth_client: Any = None
    try:
        from app.oauth.client_google import RealGoogleOAuthClient

        oauth_client = RealGoogleOAuthClient(
            client_secrets_path=cfg.youtube_client_secrets_path,
            redirect_uri=cfg.youtube_redirect_uri,
        )
        logger.info("Scheduler: OAuth client ready for analytics observations")
    except Exception as exc:
        logger.warning(
            "Scheduler: OAuth client unavailable (%s); "
            "analytics observations will fail gracefully until credentials are configured",
            exc,
        )

    # Startup reconciliation: register any already-public publications.
    try:
        conn = get_db_connection(db_path=cfg.db_path)
        try:
            adopted = reconcile_unobserved_publications(conn)
            if adopted:
                logger.info(
                    "Scheduler startup: adopted %d publication(s) for observation: %s",
                    len(adopted),
                    adopted,
                )
            else:
                logger.info("Scheduler startup: no unobserved publications found")

            # Bring schedules back onto their configured cadence. next_run_at
            # is persisted, so rows written while the interval computation was
            # wrong stay wrong until they expire — an hourly cycle would have
            # waited out a stale daily timestamp before its first correct tick.
            # One-directional and idempotent: only rows sitting later than
            # their own cadence allows are touched.
            cadence_repairs = [r for r in reconcile_schedule_next_runs(conn) if r.repaired]
            if cadence_repairs:
                logger.info(
                    "Scheduler startup: reconciled %d schedule next-run time(s): %s",
                    len(cadence_repairs),
                    [
                        (r.operation_type, r.previous_next_run_at, r.canonical_next_run_at)
                        for r in cadence_repairs
                    ],
                )

            # Repair any experiment whose publication went public without the
            # ledger following — a crash between release and handoff, or a
            # publish that predates the handoff existing at all.
            repaired = reconcile_experiment_lifecycle(conn)
            if repaired:
                logger.info(
                    "Scheduler startup: repaired %d experiment lifecycle state(s): %s",
                    len(repaired),
                    [(r.experiment_id, r.to_status) for r in repaired],
                )
        finally:
            conn.close()
    except Exception as exc:
        logger.error("Scheduler: startup reconciliation failed: %s", exc, exc_info=True)

    while True:
        try:
            conn = get_db_connection(db_path=cfg.db_path)
            try:
                # Re-reconcile every tick so newly-released publications are
                # adopted and any lagging experiment ledger catches up.
                reconcile_unobserved_publications(conn)
                reconcile_experiment_lifecycle(conn)
                run_scheduler_tick(conn, oauth_client=oauth_client)
            finally:
                conn.close()
        except Exception as exc:
            logger.error("Scheduler tick failed: %s", exc, exc_info=True)

        time.sleep(poll_interval_seconds)


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    run_scheduler_daemon()
