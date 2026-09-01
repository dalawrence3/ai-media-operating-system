"""Automatic analytics observation — ingest → aggregate → retention → learning.

This module is the single execution entry point for a scheduled observation
tick.  It is invoked by the worker job dispatcher for every due
analytics_observation schedule row.

Invariants:
- Publication visibility/status is NOT modified (analytics reads only).
- Publishing gates (ACE_PUBLISHING_LIVE_ENABLED, ACE_RELEASE_PUBLIC_ENABLED)
  are irrelevant — analytics reads are independent.
- Provider failure is recoverable; publication state is never touched.
- Unchanged response (same response_fingerprint) causes no downstream work.
- no_data observation is valid; it does NOT trigger aggregation or learning.
- InsufficientAnalyticsDataError from analyze_publication() is non-fatal.
- RetentionUnavailableError is non-fatal; observation is retried next tick.

Events emitted to cp_events (best-effort, failures are logged not raised):
    observation.activated       — on registration
    observation.attempted       — every tick attempt
    observation.unchanged       — same fingerprint as previous
    observation.no_data         — provider returned empty
    observation.new_data        — new metrics snapshot persisted
    observation.aggregated      — aggregation completed
    observation.retention_acquired  — retention curve persisted
    observation.retention_unavailable — retention API returned empty
    observation.learning_completed  — learning run finished
    observation.failed          — recoverable error; retry scheduled
    observation.paused          — max failures reached
    platform_account.resumed    — account restored to connected after degraded state
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from app.analytics.observation import (
    MAX_CONSECUTIVE_FAILURES,
    advance_schedule_next_run,
    compute_observation_interval_seconds,
    get_observation_state,
    pause_observation_schedule,
    upsert_observation_state,
)

logger = logging.getLogger(__name__)


# ── Lineage derivation ────────────────────────────────────────────────────────


def _derive_lineage(conn: Any, publication_id: int) -> dict | None:
    """Derive full analytics lineage from publication + publishing_plan.

    Returns a dict with all fields needed by AnalyticsOrchestrator.ingest(),
    or None if the publication or plan is not found.
    """
    pub = conn.execute(
        """
        SELECT p.id, p.provider, p.provider_video_id, p.publishing_plan_id,
               p.publishing_job_id, p.platform_account_id, p.workspace_id,
               p.channel_id, p.published_at, p.visibility, p.status
        FROM publications p
        WHERE p.id = ?
        """,
        (publication_id,),
    ).fetchone()
    if pub is None:
        return None

    plan = conn.execute(
        """
        SELECT id, render_manifest_id, scene_manifest_id, production_plan_id,
               script_id, topic_id, narration_run_id, caption_run_id, experiment_id
        FROM publishing_plans WHERE id = ?
        """,
        (pub["publishing_plan_id"],),
    ).fetchone()
    if plan is None:
        return None

    return {
        "provider_video_id": pub["provider_video_id"],
        "publication_id": publication_id,
        "publishing_plan_id": pub["publishing_plan_id"],
        "publishing_job_id": pub["publishing_job_id"],
        "render_manifest_id": plan["render_manifest_id"],
        "scene_manifest_id": plan["scene_manifest_id"],
        "production_plan_id": plan["production_plan_id"],
        "script_id": plan["script_id"],
        "topic_id": plan["topic_id"],
        "narration_run_id": plan["narration_run_id"],
        "caption_run_id": plan["caption_run_id"],
        "experiment_id": plan["experiment_id"],
        "platform_account_id": pub["platform_account_id"],
        "workspace_id": pub["workspace_id"],
        "channel_id": pub["channel_id"],
        "published_at": pub["published_at"],
    }


# ── Event emission (best-effort) ─────────────────────────────────────────────


def _emit(
    conn: Any,
    *,
    event_type: str,
    workspace_id: str,
    publication_id: int,
    payload: dict | None = None,
    platform_account_id: str | None = None,
) -> None:
    """Emit a cp_event; failures are logged but never re-raised."""
    try:
        import uuid

        from app.control_plane.models import ControlEventDraft
        from app.control_plane.repository import create_event

        full_payload = {"publication_id": publication_id, **(payload or {})}
        create_event(
            conn,
            ControlEventDraft(
                id=str(uuid.uuid4()),
                event_type=event_type,
                workspace_id=workspace_id,
                actor="system:auto_observer",
                platform_account_id=platform_account_id,
                source_entity_id=str(publication_id),
                payload=full_payload,
            ),
        )
        conn.commit()
    except Exception as exc:
        logger.debug("auto_observer: event %r emit failed (non-fatal): %s", event_type, exc)


# ── Account health recovery (best-effort) ────────────────────────────────────

# Re-exported for backward compatibility — the canonical definitions now live
# in app.control_plane.accounts (Phase 18E.2), shared with the OAuth
# re-verification recovery path so both reach identical outcomes.
from app.control_plane.accounts import NON_HEALTHY_HEALTH_STATUSES as _NON_HEALTHY_HEALTH_STATUSES  # noqa: E402,F401,I001
from app.control_plane.accounts import OPERATOR_INTENT_STATUSES as _OPERATOR_INTENT_STATUSES  # noqa: E402,F401,I001
from app.control_plane.accounts import RECOVERABLE_ACCOUNT_STATUSES as _RECOVERABLE_ACCOUNT_STATUSES  # noqa: E402,F401,I001


def _maybe_restore_account(
    conn: Any,
    *,
    platform_account_id: str | None,
    workspace_id: str,
    publication_id: int,
) -> None:
    """Heal platform account status and health record after a successful observation.

    Thin wrapper around app.control_plane.accounts.restore_account_health,
    preserving this function's own publication-scoped log line and event
    payload shape. See that function for the two-pass recovery semantics.
    """
    if not platform_account_id:
        return

    try:
        from app.control_plane import repository as _repo
        from app.control_plane.accounts import (
            RECOVERABLE_ACCOUNT_STATUSES,
            restore_account_health,
        )

        acct = _repo.get_platform_account(conn, platform_account_id)
        was_recoverable = acct.status in RECOVERABLE_ACCOUNT_STATUSES

        restore_account_health(
            conn,
            account_id=platform_account_id,
            workspace_id=workspace_id,
            recorded_by="system:auto_observer",
            detail="Health restored after successful analytics observation.",
            event_payload={
                "publication_id": publication_id,
                "reason": "successful_analytics_observation",
            },
        )

        if was_recoverable:
            logger.info(
                "auto_observer: restored platform account %s to connected "
                "after successful observation for publication %d",
                platform_account_id,
                publication_id,
            )

    except Exception as exc:
        logger.debug(
            "auto_observer: account recovery for %s failed (non-fatal): %s",
            platform_account_id,
            exc,
        )


# ── Retention (best-effort) ───────────────────────────────────────────────────


def _attempt_retention(
    conn: Any,
    *,
    provider: Any,
    lineage: dict,
    snapshot_id: int,
    period_start: str | None,
    period_end: str | None,
) -> bool:
    """Fetch and persist the retention curve for this observation tick.

    Returns True if retention data was acquired; False if unavailable.
    Raises on non-recoverable errors.
    """
    from app.analytics.retention import (
        RetentionCurve,
        attribute_retention_curve,
        fetch_retention_from_service,
        load_scene_catalog,
        parse_retention_rows,
        persist_retention_curve,
    )

    if period_start is None or period_end is None:
        return False

    try:
        service = provider._yt_analytics_service  # type: ignore[attr-defined]
    except AttributeError:
        return False  # provider doesn't expose service (fake/test)

    try:
        raw_rows = fetch_retention_from_service(
            service,
            lineage["provider_video_id"],
            period_start=period_start,
            period_end=period_end,
        )
    except Exception as exc:
        logger.warning("auto_observer: retention fetch failed (non-fatal): %s", exc)
        return False

    if not raw_rows:
        return False

    points = parse_retention_rows(raw_rows)
    scenes, duration_ms = load_scene_catalog(conn, lineage["scene_manifest_id"])
    curve = RetentionCurve(
        provider_video_id=lineage["provider_video_id"],
        scene_manifest_id=lineage["scene_manifest_id"],
        video_duration_ms=duration_ms,
        period_start=period_start,
        period_end=period_end,
        points=points,
    )
    attribute_retention_curve(curve, scenes)
    persist_retention_curve(
        conn,
        curve,
        snapshot_id=snapshot_id,
        publication_id=lineage["publication_id"],
    )
    return True


# ── Learning (best-effort) ────────────────────────────────────────────────────


def _attempt_learning(conn: Any, *, publication_id: int, topic_id: int) -> int | None:
    """Run analyze_publication(); returns run_id or None if ineligible/failed."""
    try:
        from app.learning.orchestrator import analyze_publication

        run_id = analyze_publication(conn, publication_id=publication_id, topic_id=topic_id)
        return run_id
    except Exception as exc:
        logger.info("auto_observer: learning skipped for publication %d: %s", publication_id, exc)
        return None


# ── Experiment ledger (best-effort) ──────────────────────────────────────────


def _attempt_mark_observing(conn: Any, *, publication_id: int, result: ObservationResult) -> None:
    """Promote the experiment to `observing`. Never fails the tick."""
    try:
        from app.intelligence.experiments.lifecycle import mark_experiment_observing

        advance = mark_experiment_observing(conn, publication_id)
        if advance.to_status:
            result.experiment_status = advance.to_status
    except Exception as exc:  # noqa: BLE001 — analytics must not fail on bookkeeping
        logger.info(
            "auto_observer: could not mark publication %d observing: %s",
            publication_id,
            exc,
        )


def _attempt_outcome_bridge(
    conn: Any,
    *,
    publication_id: int,
    workspace_id: str,
    platform_account_id: str | None,
    result: ObservationResult,
) -> None:
    """Run the outcome bridge and record what it concluded. Never fails the tick."""
    try:
        from app.intelligence.experiments.outcome_bridge import run_outcome_bridge

        bridge = run_outcome_bridge(conn, publication_id=publication_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "auto_observer: outcome bridge failed for publication %d (non-fatal): %s",
            publication_id,
            exc,
        )
        return

    result.outcome_readiness = bridge.outcome_readiness
    result.outcome_persisted = bridge.outcome_persisted
    if bridge.experiment_status:
        result.experiment_status = bridge.experiment_status

    if bridge.outcome_persisted or bridge.transitions:
        _emit(
            conn,
            event_type="observation.outcome_evaluated",
            workspace_id=workspace_id,
            publication_id=publication_id,
            payload={
                "experiment_id": bridge.experiment_id,
                "readiness": bridge.outcome_readiness,
                "classification": bridge.outcome_classification,
                "experiment_status": bridge.experiment_status,
                "transitions": bridge.transitions,
            },
            platform_account_id=platform_account_id,
        )


# ── Main observation tick ─────────────────────────────────────────────────────


class ObservationResult:
    """Structured result from a single observation tick."""

    def __init__(self) -> None:
        self.is_new_snapshot: bool = False
        self.observation_state: str = "no_data"
        self.snapshot_id: int | None = None
        self.aggregated: bool = False
        self.retention_acquired: bool = False
        self.learning_run_id: int | None = None
        self.error: str | None = None
        self.skipped: bool = False
        # Phase 18D — experiment ledger state and outcome verdict after this
        # tick. None when the publication carries no experiment lineage.
        self.experiment_status: str | None = None
        self.outcome_readiness: str | None = None
        self.outcome_persisted: bool = False


def run_observation(
    conn: Any,
    *,
    publication_id: int,
    schedule_id: str,
    oauth_client: Any | None = None,
    _provider_override: Any | None = None,  # test injection
) -> ObservationResult:
    """Execute one analytics observation tick for a publication.

    Derives lineage from the DB, builds a live provider, calls the orchestrator,
    then conditionally runs aggregation, retention, and learning.

    The schedule's next_run_at is advanced regardless of outcome.  On failure
    the failure_count is incremented; if it reaches MAX_CONSECUTIVE_FAILURES
    the schedule is paused.
    """
    result = ObservationResult()
    now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    state = get_observation_state(conn, publication_id) or {}
    workspace_id = state.get("workspace_id", "")
    platform_account_id = state.get("platform_account_id")
    # Bound up front so the except-block below can reference it even when
    # the failure happened before (or during) lineage derivation itself.
    lineage: dict | None = None

    _emit(
        conn,
        event_type="observation.attempted",
        workspace_id=workspace_id,
        publication_id=publication_id,
        payload={"schedule_id": schedule_id},
        platform_account_id=platform_account_id,
    )

    try:
        lineage = _derive_lineage(conn, publication_id)
        if lineage is None:
            raise RuntimeError(
                f"Cannot derive lineage for publication {publication_id}: "
                "publication or publishing_plan not found"
            )

        workspace_id = lineage["workspace_id"] or workspace_id
        platform_account_id = lineage["platform_account_id"] or platform_account_id
        topic_id: int = lineage["topic_id"]
        experiment_id: str | None = lineage["experiment_id"]
        published_at: str | None = lineage["published_at"]

        # An active observation schedule is what makes an experiment
        # `observing`. Doing this before the provider call means a publish
        # whose own ledger handoff was interrupted is repaired on the very
        # first tick, without waiting for a provider round trip to succeed.
        _attempt_mark_observing(conn, publication_id=publication_id, result=result)

        # Build provider.
        if _provider_override is not None:
            provider = _provider_override
        else:
            # Auto-build OAuth client from config when none supplied (daemon path).
            effective_client = oauth_client
            if effective_client is None:
                try:
                    from app.core.config import get_config as _get_cfg
                    from app.oauth.client_google import RealGoogleOAuthClient

                    _cfg = _get_cfg()
                    effective_client = RealGoogleOAuthClient(
                        client_secrets_path=_cfg.youtube_client_secrets_path,
                        redirect_uri=_cfg.youtube_redirect_uri,
                    )
                except Exception as exc:
                    raise RuntimeError(
                        f"Cannot build analytics OAuth client from config: {exc}"
                    ) from exc

            from app.analytics.gate import build_authenticated_analytics_provider

            provider = build_authenticated_analytics_provider(
                conn,
                account_id=platform_account_id or "",
                workspace_id=workspace_id,
                channel_id=lineage["channel_id"] or "",
                oauth_client=effective_client,
            )

        # Build period from published_at → today.
        period_start: str | None = None
        period_end: str | None = None
        if published_at:
            period_start = published_at[:10]
            period_end = datetime.now(UTC).strftime("%Y-%m-%d")

        from app.analytics.orchestrator import AnalyticsOrchestrator

        orchestrator = AnalyticsOrchestrator(conn, provider)

        snapshot, metrics = orchestrator.ingest(
            provider_video_id=lineage["provider_video_id"],
            publication_id=lineage["publication_id"],
            publishing_plan_id=lineage["publishing_plan_id"],
            publishing_job_id=lineage["publishing_job_id"],
            render_manifest_id=lineage["render_manifest_id"],
            scene_manifest_id=lineage["scene_manifest_id"],
            production_plan_id=lineage["production_plan_id"],
            script_id=lineage["script_id"],
            topic_id=topic_id,
            narration_run_id=lineage["narration_run_id"],
            caption_run_id=lineage["caption_run_id"],
            experiment_id=experiment_id,
            period_start=period_start,
            period_end=period_end,
        )

        result.snapshot_id = snapshot.id
        result.observation_state = snapshot.observation_state or "no_data"

        # Provider call succeeded — restore any degraded account status.
        _maybe_restore_account(
            conn,
            platform_account_id=platform_account_id,
            workspace_id=workspace_id,
            publication_id=publication_id,
        )

        # Detect whether this snapshot is new (different from last known).
        prev_snapshot_id: int | None = state.get("latest_snapshot_id")
        result.is_new_snapshot = (prev_snapshot_id is None) or (snapshot.id != prev_snapshot_id)

        if not result.is_new_snapshot:
            _emit(
                conn,
                event_type="observation.unchanged",
                workspace_id=workspace_id,
                publication_id=publication_id,
                payload={"snapshot_id": snapshot.id},
                platform_account_id=platform_account_id,
            )
        elif result.observation_state == "no_data":
            _emit(
                conn,
                event_type="observation.no_data",
                workspace_id=workspace_id,
                publication_id=publication_id,
                payload={"snapshot_id": snapshot.id},
                platform_account_id=platform_account_id,
            )
        else:
            _emit(
                conn,
                event_type="observation.new_data",
                workspace_id=workspace_id,
                publication_id=publication_id,
                payload={"snapshot_id": snapshot.id, "metric_count": len(metrics)},
                platform_account_id=platform_account_id,
            )

            # Aggregate only when new data arrived.
            try:
                orchestrator.aggregate(publication_id=publication_id, topic_id=topic_id)
                result.aggregated = True
                _emit(
                    conn,
                    event_type="observation.aggregated",
                    workspace_id=workspace_id,
                    publication_id=publication_id,
                    payload={"snapshot_id": snapshot.id},
                    platform_account_id=platform_account_id,
                )
            except Exception as exc:
                logger.warning(
                    "auto_observer: aggregation failed for publication %d (non-fatal): %s",
                    publication_id,
                    exc,
                )

            # Attempt retention (non-fatal).
            already_acquired = bool(state.get("retention_acquired"))
            if not already_acquired:
                try:
                    result.retention_acquired = _attempt_retention(
                        conn,
                        provider=provider,
                        lineage=lineage,
                        snapshot_id=snapshot.id,
                        period_start=period_start,
                        period_end=period_end,
                    )
                except Exception as exc:
                    logger.warning(
                        "auto_observer: retention failed for publication %d (non-fatal): %s",
                        publication_id,
                        exc,
                    )
            else:
                result.retention_acquired = True  # already has it

            if result.retention_acquired:
                _emit(
                    conn,
                    event_type="observation.retention_acquired",
                    workspace_id=workspace_id,
                    publication_id=publication_id,
                    payload={"snapshot_id": snapshot.id},
                    platform_account_id=platform_account_id,
                )
            else:
                _emit(
                    conn,
                    event_type="observation.retention_unavailable",
                    workspace_id=workspace_id,
                    publication_id=publication_id,
                    platform_account_id=platform_account_id,
                )

            # Learning: only when there is real new data.
            learning_run_id = _attempt_learning(
                conn, publication_id=publication_id, topic_id=topic_id
            )
            result.learning_run_id = learning_run_id
            if learning_run_id is not None:
                _emit(
                    conn,
                    event_type="observation.learning_completed",
                    workspace_id=workspace_id,
                    publication_id=publication_id,
                    payload={"learning_run_id": learning_run_id},
                    platform_account_id=platform_account_id,
                )

        # Experiment outcome bridge (Phase 18D): extract content features,
        # assess execution fidelity, evaluate the outcome, and move the
        # experiment ledger to match the evidence that now exists.
        #
        # Deliberately outside the new-data branch. Outcome maturity depends
        # on wall-clock publication age as well as on metrics, so a video
        # that collected its views on day one and nothing since still has to
        # be re-evaluated when it crosses the minimum-age threshold. Gating
        # this on a changed snapshot would leave exactly those experiments
        # stranded in `observing` forever. Every step inside is idempotent,
        # so running it on a quiet tick costs a few queries and changes
        # nothing.
        _attempt_outcome_bridge(
            conn,
            publication_id=publication_id,
            workspace_id=workspace_id,
            platform_account_id=platform_account_id,
            result=result,
        )

        # Update observation state on success.
        new_consecutive_no_data = (
            (state.get("consecutive_no_data") or 0) + 1
            if result.observation_state == "no_data"
            else 0
        )
        upsert_observation_state(
            conn,
            publication_id=publication_id,
            workspace_id=workspace_id,
            channel_id=lineage.get("channel_id"),
            platform_account_id=platform_account_id,
            schedule_id=schedule_id,
            observation_status="active",
            last_attempted_at=now_str,
            last_success_at=now_str,
            latest_snapshot_id=snapshot.id,
            retention_acquired=result.retention_acquired or bool(state.get("retention_acquired")),
            consecutive_no_data=new_consecutive_no_data,
            failure_count=0,
            commit=True,
        )

        # Advance next_run based on current age.
        interval_seconds = compute_observation_interval_seconds(published_at)
        advance_schedule_next_run(conn, schedule_id, interval_seconds=interval_seconds)

    except Exception as exc:
        result.error = str(exc)
        logger.error(
            "auto_observer: observation tick failed for publication %d: %s",
            publication_id,
            exc,
            exc_info=True,
        )

        failure_count = int(state.get("failure_count") or 0) + 1
        is_paused = failure_count >= MAX_CONSECUTIVE_FAILURES

        upsert_observation_state(
            conn,
            publication_id=publication_id,
            workspace_id=workspace_id,
            channel_id=state.get("channel_id"),
            platform_account_id=platform_account_id,
            schedule_id=schedule_id,
            observation_status="paused" if is_paused else "active",
            last_attempted_at=now_str,
            consecutive_no_data=int(state.get("consecutive_no_data") or 0),
            failure_count=failure_count,
            commit=True,
        )

        if is_paused:
            pause_observation_schedule(conn, publication_id)
            _emit(
                conn,
                event_type="observation.paused",
                workspace_id=workspace_id,
                publication_id=publication_id,
                payload={"failure_count": failure_count, "error": str(exc)},
                platform_account_id=platform_account_id,
            )
        else:
            _emit(
                conn,
                event_type="observation.failed",
                workspace_id=workspace_id,
                publication_id=publication_id,
                payload={"failure_count": failure_count, "error": str(exc)},
                platform_account_id=platform_account_id,
            )
            # Still advance next_run so the retry happens at normal cadence.
            # Best-effort: if this secondary step also fails, the schedule's
            # next_run_at simply isn't advanced and the tick is retried at
            # its previous cadence rather than crashing observation entirely.
            try:
                published_at_retry = lineage.get("published_at") if lineage else None
                interval_seconds = compute_observation_interval_seconds(published_at_retry)
                advance_schedule_next_run(conn, schedule_id, interval_seconds=interval_seconds)
            except Exception:
                logger.warning(
                    "auto_observer: failed to advance next_run after a failed tick "
                    "for publication %d",
                    publication_id,
                    exc_info=True,
                )

    return result


# ── Daemon entry point ────────────────────────────────────────────────────────


def run_observation_daemon(
    poll_interval_seconds: int = 60,
    *,
    oauth_client: Any | None = None,
) -> None:  # pragma: no cover
    """Blocking daemon: reconcile → poll due schedules → run observations."""
    import time

    from app.analytics.observation import reconcile_unobserved_publications
    from app.core.config import get_config
    from app.core.database import get_db_connection

    cfg = get_config()
    logger.info("auto_observer daemon starting (poll=%ds)", poll_interval_seconds)

    while True:
        try:
            conn = get_db_connection(db_path=cfg.db_path)
            try:
                # Reconcile any orphans on each tick (idempotent).
                adopted = reconcile_unobserved_publications(conn)
                if adopted:
                    logger.info("auto_observer: reconciled %d publication(s)", len(adopted))

                from app.analytics.observation import get_due_observation_schedules

                due = get_due_observation_schedules(conn)
                for row in due:
                    import json as _json

                    cfg_json = row.get("schedule_config_json") or "{}"
                    schedule_cfg = _json.loads(cfg_json)
                    pub_id = int(schedule_cfg.get("publication_id", 0))
                    if pub_id == 0:
                        continue
                    run_observation(
                        conn,
                        publication_id=pub_id,
                        schedule_id=row["id"],
                        oauth_client=oauth_client,
                    )
            finally:
                conn.close()
        except Exception as exc:
            logger.error("auto_observer daemon tick failed: %s", exc, exc_info=True)
        time.sleep(poll_interval_seconds)
