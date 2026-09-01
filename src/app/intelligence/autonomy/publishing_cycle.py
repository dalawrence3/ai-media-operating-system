"""Phase 18C — the autonomous publishing cycle.

Consumes a slot that Phase 18B left in READY state and, if and only if every
authorization layer permits it, uploads the video privately and then releases
it publicly at its reserved time.

Unlike Phases 18A and 18B, this module CAN cause irreversible public side
effects. Its design is therefore organized around three rules:

1. AUTHORIZATION IS RE-CHECKED BEFORE EVERY EXTERNAL SIDE EFFECT.
   Not once per cycle — before the upload, and again before the release.
   An operator revoking authorization while a video sits uploaded-private
   must prevent it going public, and the gap between those two calls is
   precisely where that revocation needs to land (section 18).

2. UPLOAD IS WRITE-AHEAD LOGGED.
   An intent row is committed before the provider call and resolved after.
   A crash in between leaves an unresolved attempt that BLOCKS retries until
   reconciled against the provider. Refusing to publish is recoverable; a
   duplicate public video on a real channel is not (section 13).

3. THE SLOT DECIDES WHEN, NOT THE ARTIFACT.
   A READY video does not publish because it is ready. It publishes because
   its reserved slot is due, within its grace window, and everything else
   agrees (sections 9 and 10).

The upload/release split mirrors the provider's own two-step API and gives
the state machine a durable resting point at `uploaded` — the one state from
which recovery must never re-upload.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from app.intelligence.autonomy.models import (
    DeadlineStatus,
    PublishFailureCategory,
    PublishingCycleResult,
    PublishOutcome,
    PublishStatus,
)
from app.intelligence.autonomy.repository import (
    MAX_PUBLISH_RETRIES,
    find_slot_ready_to_publish,
    get_slot,
    mark_slot_missed,
    mark_slot_publish_blocked,
    mark_slot_publish_failed,
    mark_slot_released,
    mark_slot_uploaded,
    start_slot_publishing,
)
from app.publishing.authorization import (
    DEFAULT_MISSED_SLOT_GRACE_MINUTES,
    evaluate_publishing_authorization,
    get_channel_publishing_authorization,
)

logger = logging.getLogger(__name__)

# Provider exception substrings that prove no video was created. Anything not
# matching is treated as UNCERTAIN — the conservative default, because
# wrongly concluding "no video exists" is what creates duplicates.
_DEFINITELY_NOT_UPLOADED = (
    "file not found",
    "no such file",
    "is empty",
    "does not appear to be a supported video format",
    "invalid_grant",
    "insufficient permission",
    "forbidden",
    "quotaexceeded",
)


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _now_iso() -> str:
    return _now_utc().strftime("%Y-%m-%dT%H:%M:%S")


def _parse_utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def _deadline_status(scheduled_for_utc: str, now: datetime) -> DeadlineStatus:
    try:
        deadline = _parse_utc(scheduled_for_utc)
    except ValueError:
        return DeadlineStatus.comfortably_ahead
    remaining = (deadline - now).total_seconds()
    if remaining < 0:
        return DeadlineStatus.missed
    if remaining < 2 * 3600:
        return DeadlineStatus.late
    if remaining < 12 * 3600:
        return DeadlineStatus.approaching
    return DeadlineStatus.comfortably_ahead


def _classify_upload_exception(exc: Exception) -> PublishFailureCategory:
    """Decide whether a failed upload provably created no video.

    Defaults to UPLOAD_STATE_UNCERTAIN. Only errors that demonstrably occur
    before or instead of the byte transfer are classified as terminal —
    everything else (timeouts, resets, unexpected errors mid-transfer) must
    be reconciled against the provider, never blindly retried.
    """
    message = str(exc).lower()
    for marker in _DEFINITELY_NOT_UPLOADED:
        if marker in message:
            return PublishFailureCategory.UPLOAD_FAILED_TERMINAL
    return PublishFailureCategory.UPLOAD_STATE_UNCERTAIN


def run_publishing_cycle(
    conn: sqlite3.Connection,
    *,
    cp_channel_id: str,
    workspace_id: str,
    actor: str = "system:autonomy-publishing",
    provider_factory: Any = None,
    yt_client_factory: Any = None,
    now: datetime | None = None,
) -> PublishingCycleResult:
    """Run one channel-scoped autonomous publishing cycle.

    Handles at most ONE slot per call, so a bug can never cascade into a
    burst of publications.

    `provider_factory` and `yt_client_factory` are injection points: in
    production they build a gated, authenticated YouTube provider/client; in
    tests they supply fakes. Leaving them as None is not a way to publish
    "safely by default" — it is an error, because a cycle that reached the
    upload step without a provider has a configuration bug and must say so.
    """
    started = _now_iso()
    current_time = now or _now_utc()
    result = PublishingCycleResult(
        channel_id=cp_channel_id,
        workspace_id=workspace_id,
        slot_id=None,
        started_at=started,
    )

    # ── Cheapest possible check: is there anything at all to publish? ─────
    slot = find_slot_ready_to_publish(conn, cp_channel_id)
    if slot is None:
        result.outcome = PublishOutcome.NO_SLOT_TO_PUBLISH
        result.reason = "No READY slot is awaiting publication for this channel."
        result.completed_at = _now_iso()
        return result

    result.slot_id = slot.id
    result.experiment_id = slot.experiment_id
    result.publishing_plan_id = slot.production_publishing_plan_id
    result.retry_count = slot.publish_retry_count
    result.deadline_status = _deadline_status(slot.scheduled_for_utc, current_time)

    # ── Due-ness and the missed-slot policy (sections 9 and 10) ──────────
    auth_row = get_channel_publishing_authorization(conn, cp_channel_id)
    grace_minutes = (
        auth_row.missed_slot_grace_minutes if auth_row else DEFAULT_MISSED_SLOT_GRACE_MINUTES
    )
    try:
        scheduled = _parse_utc(slot.scheduled_for_utc)
    except ValueError:
        result.outcome = PublishOutcome.FAILED
        result.failure_category = PublishFailureCategory.PREUPLOAD_VALIDATION_FAILED.value
        result.reason = f"Slot {slot.id} has an unparseable scheduled_for_utc."
        result.completed_at = _now_iso()
        return result

    if current_time < scheduled:
        # Not due. This is the overwhelmingly common outcome for a frequent
        # scheduler tick, and it must stay cheap and side-effect free.
        result.outcome = PublishOutcome.NOT_DUE
        result.reason = (
            f"Slot {slot.id} is reserved for {slot.scheduled_for_utc}Z; not publishing early."
        )
        result.completed_at = _now_iso()
        return result

    grace_deadline = scheduled + timedelta(minutes=grace_minutes)
    if current_time > grace_deadline and slot.publish_status != PublishStatus.uploaded:
        # Past the grace window and nothing has been uploaded yet. Publishing
        # now would put out a video hours or days late with no human aware of
        # it. Mark it and require an explicit reschedule.
        reason = (
            f"Slot {slot.id} was scheduled for {slot.scheduled_for_utc}Z and its "
            f"{grace_minutes}-minute grace window has elapsed. Not publishing late; "
            "the produced artifact is preserved and can be rescheduled to a new slot."
        )
        mark_slot_missed(conn, slot.id, reason=reason)
        result.outcome = PublishOutcome.MISSED
        result.failure_category = PublishFailureCategory.MISSED_SLOT.value
        result.reason = reason
        result.completed_at = _now_iso()
        logger.info("publishing cycle: %s", reason)
        return result

    # ── Authorization gate #1, before any external side effect ───────────
    # A slot resuming after its own earlier upload already owns a Publication
    # row; excluding it keeps the slot from rate-limiting itself out of
    # finishing work it legitimately started (same reasoning as gate #2).
    decision = evaluate_publishing_authorization(
        conn, channel_id=cp_channel_id, exclude_publication_id=slot.publication_id
    )
    result.global_publishing_enabled = decision.global_publishing_enabled
    result.global_release_enabled = decision.global_release_enabled
    result.channel_authorized = decision.channel_authorized
    result.publications_last_24h = decision.publications_last_24h
    result.max_publications_per_24h = decision.max_publications_per_24h
    result.blocked_by = [r.value for r in decision.blocked_by]

    if not decision.allowed:
        category = _authorization_block_category(decision)
        mark_slot_publish_blocked(conn, slot.id, category=category.value, reason=decision.detail)
        result.outcome = PublishOutcome.BLOCKED
        result.failure_category = category.value
        result.reason = decision.detail
        result.completed_at = _now_iso()
        logger.info("publishing cycle: slot %d blocked — %s", slot.id, decision.detail)
        return result

    # ── Concurrency lease, slot-scoped (section 15) ──────────────────────
    from app.control_plane.jobs import complete_operation, fail_operation
    from app.control_plane.jobs import start_operation as _start_operation

    idempotency_key = f"autonomy_publishing:{cp_channel_id}:{slot.id}"
    result.idempotency_key = idempotency_key

    existing_op = conn.execute(
        "SELECT id, status FROM cp_operation_executions WHERE idempotency_key = ?",
        (idempotency_key,),
    ).fetchone()
    if existing_op is not None and existing_op["status"] in ("pending", "running"):
        result.outcome = PublishOutcome.ALREADY_RUNNING
        result.already_running = True
        result.operation_id = existing_op["id"]
        result.reason = f"Another worker already holds the publishing lease for slot {slot.id}."
        result.completed_at = _now_iso()
        return result

    # A prior terminal operation for this slot is fine to supersede; mint a
    # distinct key so the lease primitive stays append-only.
    effective_key = idempotency_key
    if existing_op is not None:
        attempt = slot.publish_retry_count + 1
        effective_key = f"{idempotency_key}:retry{attempt}"
        result.idempotency_key = effective_key

    operation = _start_operation(
        conn,
        operation_type="autonomy_publishing_cycle",
        workspace_id=workspace_id,
        actor=actor,
        channel_id=cp_channel_id,
        idempotency_key=effective_key,
        input_data={"slot_id": slot.id},
    )
    result.operation_id = operation.id

    try:
        _run_locked_publishing(
            conn,
            slot_id=slot.id,
            cp_channel_id=cp_channel_id,
            workspace_id=workspace_id,
            actor=actor,
            provider_factory=provider_factory,
            yt_client_factory=yt_client_factory,
            result=result,
        )
        complete_operation(conn, operation.id, output_data={"outcome": result.outcome.value})
        # update_operation_status() does not commit. Without this the lease is
        # left 'pending' when the process exits, and every later cycle for this
        # slot returns ALREADY_RUNNING forever — a resume after a successful
        # upload would be permanently blocked.
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - last-resort guard
        logger.error("publishing cycle failed unexpectedly: %s", exc, exc_info=True)
        result.outcome = PublishOutcome.FAILED
        result.error_message = str(exc)
        result.errors.append(str(exc))
        try:
            fail_operation(conn, operation.id, str(exc))
            conn.commit()
        except Exception:  # pragma: no cover
            pass

    result.completed_at = _now_iso()
    return result


def _authorization_block_category(decision: Any) -> PublishFailureCategory:
    """Map the first blocking layer onto its canonical failure category."""
    from app.publishing.authorization import BlockReason

    first = decision.primary_reason
    if first in (BlockReason.rate_limit_reached,):
        return PublishFailureCategory.RATE_LIMIT_BLOCKED
    if first in (BlockReason.account_unhealthy, BlockReason.no_account):
        return PublishFailureCategory.PROVIDER_HEALTH_BLOCKED
    return PublishFailureCategory.AUTHORIZATION_BLOCKED


def _run_locked_publishing(
    conn: sqlite3.Connection,
    *,
    slot_id: int,
    cp_channel_id: str,
    workspace_id: str,
    actor: str,
    provider_factory: Any,
    yt_client_factory: Any,
    result: PublishingCycleResult,
) -> None:
    """The publish state machine, executed under the slot lease."""
    from app.publishing.upload_reconciliation import (
        find_succeeded_attempt_for_slot,
        find_unresolved_attempt_for_slot,
        reconcile_uncertain_attempt,
    )

    slot = get_slot(conn, slot_id)
    assert slot is not None

    plan_id = slot.production_publishing_plan_id
    if plan_id is None:
        _fail(
            conn,
            slot_id,
            result,
            category=PublishFailureCategory.PREUPLOAD_VALIDATION_FAILED,
            message=f"Slot {slot_id} is READY but carries no publishing plan.",
        )
        return

    from app.publishing.repository import get_publishing_plan

    plan = get_publishing_plan(conn, plan_id)
    if plan is None:
        _fail(
            conn,
            slot_id,
            result,
            category=PublishFailureCategory.PREUPLOAD_VALIDATION_FAILED,
            message=f"Publishing plan {plan_id} not found.",
        )
        return

    # ── An unresolved prior attempt outranks everything else ─────────────
    unresolved = find_unresolved_attempt_for_slot(conn, slot_id)
    if unresolved is not None:
        yt_client = _build_yt_client(
            conn,
            cp_channel_id=cp_channel_id,
            workspace_id=workspace_id,
            yt_client_factory=yt_client_factory,
        )
        if yt_client is None:
            result.outcome = PublishOutcome.NEEDS_RECONCILIATION
            result.failure_category = PublishFailureCategory.UPLOAD_STATE_UNCERTAIN.value
            result.reason = (
                f"Slot {slot_id} has an unresolved upload attempt and no provider client "
                "is available to reconcile it. Refusing to upload again."
            )
            return
        unresolved = reconcile_uncertain_attempt(
            conn, unresolved, yt_client=yt_client, expected_title=plan.title
        )
        if unresolved.state in ("intent_recorded", "uncertain"):
            result.outcome = PublishOutcome.NEEDS_RECONCILIATION
            result.failure_category = PublishFailureCategory.UPLOAD_STATE_UNCERTAIN.value
            result.reason = (
                f"Slot {slot_id} has an upload attempt whose outcome could not be "
                "determined. Refusing to upload again until it is resolved."
            )
            _fail(
                conn,
                slot_id,
                result,
                category=PublishFailureCategory.UPLOAD_STATE_UNCERTAIN,
                message=result.reason,
                increment_retry=False,
                set_outcome=False,
            )
            return

    # ── Deterministic artifact-quality gate (Phase 18E) ──────────────────
    # Placed BEFORE start_slot_publishing, and therefore before any lease,
    # any retry accounting and any provider client is built.
    #
    # This is the "retrying cannot change the answer" case: the render's
    # visual composition is fixed, the policy that judged it is fixed, so the
    # verdict on the next tick is the verdict on this one. Running it through
    # the normal failure path would spend three retries re-deriving a known
    # result and leave the slot in a non-terminal 'failed' state that pins the
    # channel's queue forever.
    #
    # It sits after the unresolved-attempt reconciliation above deliberately:
    # if a previous run may already have put a video on the provider, the
    # truth about that video outranks everything, including this gate.
    quality_block = _deterministic_quality_block(conn, plan)
    if quality_block is not None:
        _retire(
            conn,
            slot_id,
            result,
            category=PublishFailureCategory.ARTIFACT_QUALITY_BLOCKED,
            reason=quality_block,
        )
        _reject_plan_permanently(conn, plan=plan, reason=quality_block, actor=actor)
        return

    start_slot_publishing(conn, slot_id)

    # ── Pre-upload revalidation: READY yesterday is not READY today ──────
    ok, errors = _revalidate_for_publishing(conn, slot=slot, plan=plan)
    result.preflight_passed = ok
    result.preflight_errors = errors
    result.visual_quality_status = _visual_quality_status(conn, plan.render_manifest_id)
    if not ok:
        _fail(
            conn,
            slot_id,
            result,
            category=PublishFailureCategory.PREUPLOAD_VALIDATION_FAILED,
            message="Pre-upload revalidation failed: " + "; ".join(errors),
        )
        return

    # ── Upload (unless a previous run already did it) ────────────────────
    already = find_succeeded_attempt_for_slot(conn, slot_id)
    slot = get_slot(conn, slot_id)
    assert slot is not None

    if slot.publish_status == PublishStatus.uploaded and slot.publish_provider_video_id:
        provider_video_id = slot.publish_provider_video_id
        publication_id = slot.publication_id
        result.uploaded = True
        logger.info(
            "publishing cycle: slot %d already uploaded (%s); resuming at release",
            slot_id,
            provider_video_id,
        )
    elif already is not None and already.provider_video_id:
        provider_video_id = already.provider_video_id
        publication_id = _publication_id_for_video(conn, provider_video_id)
        mark_slot_uploaded(
            conn, slot_id, publication_id=publication_id, provider_video_id=provider_video_id
        )
        result.uploaded = True
        logger.info(
            "publishing cycle: slot %d adopted reconciled upload %s; resuming at release",
            slot_id,
            provider_video_id,
        )
    else:
        uploaded = _do_upload(
            conn,
            slot_id=slot_id,
            plan_id=plan_id,
            plan=plan,
            cp_channel_id=cp_channel_id,
            workspace_id=workspace_id,
            actor=actor,
            provider_factory=provider_factory,
            result=result,
        )
        if uploaded is None:
            return
        provider_video_id, publication_id = uploaded

    result.provider_video_id = provider_video_id
    result.publication_id = publication_id

    # Backfill the visual assessment's publication id (Phase 18E). Learning
    # joins on it, and doing this at upload — not at release — means an
    # uploaded-but-never-released video is still traceable to what it looked
    # like. Idempotent: the update is a no-op once the id is set.
    if publication_id is not None:
        try:
            from app.visuals.assessment_repository import (
                attach_publication as attach_visual_assessment,
            )

            attach_visual_assessment(
                conn,
                render_manifest_id=plan.render_manifest_id,
                publication_id=publication_id,
            )
        except Exception as exc:  # noqa: BLE001 — lineage bookkeeping, never fatal to a release
            logger.warning(
                "Could not attach publication %s to its visual assessment: %s",
                publication_id,
                exc,
            )

    # ── Authorization gate #2: re-check between upload and release ───────
    # This is the whole reason upload and release are separate states. An
    # operator who revokes authorization while the video sits private must
    # stop it here, with the video still unlisted to the world.
    #
    # The upload just created this slot's own Publication row, so it must be
    # excluded from the rate-limit count — otherwise the cycle would
    # rate-limit itself out of releasing the very video it uploaded.
    decision = evaluate_publishing_authorization(
        conn, channel_id=cp_channel_id, exclude_publication_id=publication_id
    )
    result.global_publishing_enabled = decision.global_publishing_enabled
    result.global_release_enabled = decision.global_release_enabled
    result.channel_authorized = decision.channel_authorized
    result.blocked_by = [r.value for r in decision.blocked_by]
    if not decision.allowed:
        category = _authorization_block_category(decision)
        mark_slot_publish_blocked(
            conn,
            slot_id,
            category=category.value,
            reason=(
                "Authorization was withdrawn after upload but before release. "
                f"The video remains PRIVATE on the provider. {decision.detail}"
            ),
        )
        result.outcome = PublishOutcome.UPLOADED_PENDING_RELEASE
        result.failure_category = category.value
        result.reason = (
            "Uploaded privately, then authorization was withdrawn before release. "
            f"Video stays private. {decision.detail}"
        )
        logger.warning(
            "publishing cycle: slot %d uploaded but release blocked — %s",
            slot_id,
            decision.detail,
        )
        return

    # ── Release ──────────────────────────────────────────────────────────
    _do_release(
        conn,
        slot_id=slot_id,
        cp_channel_id=cp_channel_id,
        workspace_id=workspace_id,
        actor=actor,
        publication_id=publication_id,
        provider_video_id=provider_video_id,
        yt_client_factory=yt_client_factory,
        result=result,
    )


def _publication_id_for_video(conn: sqlite3.Connection, provider_video_id: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM publications WHERE provider_video_id = ? AND deleted_at IS NULL",
        (provider_video_id,),
    ).fetchone()
    return row["id"] if row else None


def _deterministic_quality_block(conn: sqlite3.Connection, plan: Any) -> str | None:
    """Return a reason when this artifact can never be published, else None.

    Deterministic means: derived from the artifact itself, so the answer does
    not depend on when it is asked. A missing assessment is NOT a block — an
    unmeasured render is not a bad render, and blocking on absence would make
    every pre-18E artifact permanently unpublishable.
    """
    try:
        from app.visuals.assessment_repository import get_assessment

        assessment = get_assessment(conn, plan.render_manifest_id)
    except Exception as exc:  # noqa: BLE001 — an unreadable assessment is not a verdict
        logger.warning(
            "Could not read the visual assessment for render manifest %s; "
            "deferring to the ordinary preflight path: %s",
            plan.render_manifest_id,
            exc,
        )
        return None

    if assessment is None or not assessment.blocked:
        return None

    reasons = "; ".join(f.get("message", f.get("code", "")) for f in assessment.blocking_findings)
    return f"visual_quality_blocked: {reasons}"


def _retire(
    conn: sqlite3.Connection,
    slot_id: int,
    result: PublishingCycleResult,
    *,
    category: PublishFailureCategory,
    reason: str,
) -> None:
    """Retire a slot terminally without consuming retry budget."""
    from app.intelligence.autonomy.repository import retire_slot

    retire_slot(conn, slot_id, category=category.value, reason=reason)
    result.outcome = PublishOutcome.RETIRED
    result.retired = True
    result.retirement_reason = reason
    result.failure_category = category.value
    result.reason = reason
    logger.warning(
        "publishing cycle: slot %d RETIRED (%s) — no provider call, no retry consumed. %s",
        slot_id,
        category.value,
        reason,
    )


def _reject_plan_permanently(
    conn: sqlite3.Connection, *, plan: Any, reason: str, actor: str
) -> None:
    """Mark the publishing plan rejected so no path can publish it later.

    Defence in depth, and the point of it: the retirement above stops the
    autonomous cycle, but an operator CLI call or a future code path could
    still reach a DRAFT plan. `rejected` is the pre-existing canonical marker
    for "this must not be published", it is checked independently of the
    visual assessment, and it survives the assessment row being deleted.

    Never fatal — a slot that is retired but whose plan could not be marked is
    still retired, and saying so loudly beats failing the cycle.
    """
    from app.publishing.repository import reject_publishing_plan

    if getattr(plan, "status", None) == "rejected":
        return
    try:
        reject_publishing_plan(
            conn,
            plan.id,
            reason_code="visual_quality_blocked",
            notes=reason[:2000],
            actor=actor,
        )
        conn.commit()
        logger.warning(
            "publishing cycle: publishing plan %d rejected — permanently ineligible "
            "for provider upload.",
            plan.id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Slot retired but publishing plan %s could not be marked rejected; "
            "it remains %s and needs operator attention: %s",
            getattr(plan, "id", "?"),
            getattr(plan, "status", "?"),
            exc,
        )


def _visual_quality_status(conn: sqlite3.Connection, render_manifest_id: int) -> str | None:
    """The stored visual-quality verdict, or None when none was recorded."""
    try:
        from app.visuals.assessment_repository import get_assessment

        assessment = get_assessment(conn, render_manifest_id)
    except Exception:  # noqa: BLE001 — reporting only; the gate above already ran
        return None
    return assessment.status if assessment else None


def _revalidate_for_publishing(
    conn: sqlite3.Connection, *, slot: Any, plan: Any
) -> tuple[bool, list[str]]:
    """Re-run the canonical preflight immediately before upload (section 11).

    Reuses `validate_approved_render_for_publishing` — the same check Phase
    18B used to declare the artifact READY — rather than inventing a second,
    weaker set of rules that could drift from it.
    """
    from app.media.repository import get_approved_render, get_render_manifest
    from app.publishing.validation import (
        validate_approved_render_for_publishing,
        validate_publishing_metadata,
    )

    errors: list[str] = []

    manifest = get_render_manifest(conn, plan.render_manifest_id)
    if manifest is None:
        return False, [f"Render manifest {plan.render_manifest_id} no longer exists."]
    if manifest.status != "approved":
        errors.append(
            f"Render manifest {plan.render_manifest_id} status is {manifest.status!r}, "
            "expected 'approved'."
        )

    approved = get_approved_render(
        conn, manifest.scene_manifest_id, experiment_id=plan.experiment_id
    )
    if approved is None:
        return False, errors + [
            f"No approved render remains for scene manifest {manifest.scene_manifest_id}."
        ]

    try:
        validate_approved_render_for_publishing(approved)
    except Exception as exc:
        errors.append(f"Render failed publishing validation: {exc}")

    try:
        from app.publishing.models import PublishingMetadataDraft

        validate_publishing_metadata(
            PublishingMetadataDraft(
                title=plan.title,
                description=plan.description,
                tags=list(plan.tags or []),
                language=plan.language,
                visibility=plan.visibility,
                category=plan.category,
                made_for_kids=bool(plan.made_for_kids),
            )
        )
    except Exception as exc:
        errors.append(f"Publishing metadata failed validation: {exc}")

    # Lineage consistency: the slot and the plan must describe the same work.
    if slot.experiment_id and plan.experiment_id and slot.experiment_id != plan.experiment_id:
        errors.append(
            f"Lineage mismatch: slot {slot.id} references experiment "
            f"{slot.experiment_id!r} but plan {plan.id} references {plan.experiment_id!r}."
        )

    if not os_path_exists(approved.output_path):
        errors.append(f"Rendered video file is missing: {approved.output_path!r}")

    # ── Visual quality floor (Phase 18E) ────────────────────────────────────
    # Re-checked here for the same reason everything else in this function is:
    # the render was declared READY at production time, possibly days ago, and
    # the policy that judged it may have been revised since. Reading the stored
    # verdict (rather than re-measuring) keeps this consistent with the gate
    # that produced the READY state in the first place.
    try:
        from app.visuals.assessment_repository import get_assessment

        assessment = get_assessment(conn, plan.render_manifest_id)
    except Exception as exc:  # noqa: BLE001 — a read failure is not a pass
        assessment = None
        errors.append(f"Visual quality assessment could not be read: {exc}")

    if assessment is not None and assessment.blocked:
        reasons = "; ".join(
            f.get("message", f.get("code", "")) for f in assessment.blocking_findings
        )
        errors.append(f"Render is below the visual quality floor: {reasons}")

    return (not errors), errors


def os_path_exists(path: str | None) -> bool:
    import os

    return bool(path) and os.path.isfile(path)  # type: ignore[arg-type]


def _build_yt_client(
    conn: sqlite3.Connection,
    *,
    cp_channel_id: str,
    workspace_id: str,
    yt_client_factory: Any,
) -> Any:
    """Build a YouTube API client for read/release operations, or None."""
    if yt_client_factory is not None:
        return yt_client_factory(conn, channel_id=cp_channel_id, workspace_id=workspace_id)
    return None


def _do_upload(
    conn: sqlite3.Connection,
    *,
    slot_id: int,
    plan_id: int,
    plan: Any,
    cp_channel_id: str,
    workspace_id: str,
    actor: str,
    provider_factory: Any,
    result: PublishingCycleResult,
) -> tuple[str, int | None] | None:
    """Upload the video privately, write-ahead logging the attempt.

    Returns (provider_video_id, publication_id) on success, or None after
    recording the failure on the result and the slot.
    """
    from app.media.repository import get_approved_render, get_render_manifest
    from app.publishing.upload_reconciliation import (
        mark_attempt_failed,
        mark_attempt_succeeded,
        mark_attempt_uncertain,
        record_upload_intent,
    )

    manifest = get_render_manifest(conn, plan.render_manifest_id)
    assert manifest is not None
    approved = get_approved_render(
        conn, manifest.scene_manifest_id, experiment_id=plan.experiment_id
    )
    assert approved is not None

    if provider_factory is None:
        _fail(
            conn,
            slot_id,
            result,
            category=PublishFailureCategory.PREUPLOAD_VALIDATION_FAILED,
            message=(
                "No publishing provider factory was supplied to the publishing cycle. "
                "This is a configuration error, not an authorization state."
            ),
        )
        return None

    try:
        provider = provider_factory(conn, channel_id=cp_channel_id, workspace_id=workspace_id)
    except Exception as exc:
        _fail(
            conn,
            slot_id,
            result,
            category=PublishFailureCategory.PROVIDER_HEALTH_BLOCKED,
            message=f"Could not build an authenticated publishing provider: {exc}",
        )
        return None

    attempt_key = f"slot{slot_id}:plan{plan_id}:attempt{result.retry_count + 1}"

    # WRITE-AHEAD: committed before the provider call, so a crash on the very
    # next line still leaves evidence that an upload may have occurred.
    record_upload_intent(
        conn,
        attempt_key=attempt_key,
        slot_id=slot_id,
        publishing_plan_id=plan_id,
        channel_id=cp_channel_id,
        workspace_id=workspace_id,
        provider=plan.provider,
    )

    from app.publishing.protocol import UploadPackage

    package = UploadPackage(
        plan_id=plan_id,
        file_path=approved.output_path,
        file_sha256=approved.output_sha256,
        title=plan.title,
        description=plan.description,
        tags=list(plan.tags or []),
        language=plan.language,
        category=plan.category,
        # Always upload PRIVATE regardless of what the plan says. Public
        # visibility is exclusively the release step's business, and this is
        # the line that guarantees an upload can never itself publish.
        visibility="private",
        made_for_kids=bool(plan.made_for_kids),
        scheduled_at=None,
        captions_path=plan.captions_path,
        playlist_id=plan.playlist_id,
    )

    try:
        package = provider.prepare_package(package)
        upload_result = provider.upload(package)
    except Exception as exc:
        category = _classify_upload_exception(exc)
        if category is PublishFailureCategory.UPLOAD_STATE_UNCERTAIN:
            mark_attempt_uncertain(conn, attempt_key, error=str(exc))
            _fail(
                conn,
                slot_id,
                result,
                category=category,
                message=(
                    f"Upload failed with an indeterminate outcome: {exc}. "
                    "The attempt is recorded as uncertain and will be reconciled "
                    "against the provider before any retry."
                ),
            )
        else:
            mark_attempt_failed(conn, attempt_key, error=str(exc))
            _fail(
                conn,
                slot_id,
                result,
                category=category,
                message=f"Upload failed before any video was created: {exc}",
            )
        return None

    provider_video_id = upload_result.provider_video_id

    # Persist the provider ID immediately, in its own commit, before anything
    # else can fail. From here a duplicate upload is impossible.
    mark_attempt_succeeded(conn, attempt_key, provider_video_id=provider_video_id)
    mark_slot_uploaded(conn, slot_id, publication_id=None, provider_video_id=provider_video_id)
    result.uploaded = True
    result.provider_video_id = provider_video_id
    logger.info("publishing cycle: slot %d uploaded privately as %s", slot_id, provider_video_id)

    publication_id = _create_publication_row(
        conn,
        plan=plan,
        plan_id=plan_id,
        slot_id=slot_id,
        upload_result=upload_result,
        approved=approved,
        provider=provider,
        cp_channel_id=cp_channel_id,
        workspace_id=workspace_id,
        result=result,
    )
    return provider_video_id, publication_id


def _create_publication_row(
    conn: sqlite3.Connection,
    *,
    plan: Any,
    plan_id: int,
    slot_id: int,
    upload_result: Any,
    approved: Any,
    provider: Any,
    cp_channel_id: str,
    workspace_id: str,
    result: PublishingCycleResult,
) -> int | None:
    """Create the canonical Publication row for a completed upload.

    Idempotent against the provider video ID: the DB already enforces
    UNIQUE(provider, provider_video_id), so a resumed cycle adopts the
    existing row rather than colliding with it.
    """
    from app.publishing.authorization import get_publishing_account
    from app.publishing.constants import PUB_STATUS_UPLOADED
    from app.publishing.repository import create_publication, create_publishing_job

    existing_id = _publication_id_for_video(conn, upload_result.provider_video_id)
    if existing_id is not None:
        conn.execute(
            "UPDATE publishing_slots SET publication_id = ?, updated_at = ? WHERE id = ?",
            (existing_id, _now_iso(), slot_id),
        )
        conn.commit()
        return existing_id

    account_id, _status = get_publishing_account(conn, cp_channel_id)

    try:
        job = create_publishing_job(
            conn, plan_id, 1, provider.provider_name, provider.provider_version
        )
        publication = create_publication(
            conn,
            publishing_plan_id=plan_id,
            publishing_job_id=job.id,
            provider=provider.provider_name,
            provider_version=provider.provider_version,
            provider_video_id=upload_result.provider_video_id,
            provider_url=upload_result.provider_url,
            provider_status=upload_result.provider_response,
            visibility="private",
            scheduled_at=None,
            input_hash=plan.input_hash,
            output_sha256=approved.output_sha256,
            initial_status=PUB_STATUS_UPLOADED,
            workspace_id=workspace_id,
            channel_id=cp_channel_id,
            platform_account_id=account_id,
        )
        conn.execute(
            "UPDATE publishing_slots SET publication_id = ?, updated_at = ? WHERE id = ?",
            (publication.id, _now_iso(), slot_id),
        )
        conn.commit()
        return publication.id
    except Exception as exc:
        # The video EXISTS on the provider. Losing the publication row is bad,
        # but re-uploading would be far worse — the upload attempt record
        # already holds the provider video ID, so the next cycle recovers.
        logger.critical(
            "CRITICAL: upload for slot %d succeeded (video %s) but the Publication row "
            "could not be created: %s. The upload attempt record holds the video ID; "
            "the next cycle will reconcile. Do NOT re-upload.",
            slot_id,
            upload_result.provider_video_id,
            exc,
        )
        result.errors.append(f"Publication row creation failed after upload: {exc}")
        return None


def _do_release(
    conn: sqlite3.Connection,
    *,
    slot_id: int,
    cp_channel_id: str,
    workspace_id: str,
    actor: str,
    publication_id: int | None,
    provider_video_id: str,
    yt_client_factory: Any,
    result: PublishingCycleResult,
) -> None:
    """Make the uploaded video public and hand off to analytics."""
    from app.publishing.release_service import ReleaseOutcome, release_publication_to_public

    if publication_id is None:
        publication_id = _publication_id_for_video(conn, provider_video_id)
    if publication_id is None:
        _fail(
            conn,
            slot_id,
            result,
            category=PublishFailureCategory.RELEASE_FAILED_RETRYABLE,
            message=(
                f"Video {provider_video_id} is uploaded but has no Publication row; "
                "cannot release until local state is reconciled."
            ),
            set_outcome=False,
        )
        result.outcome = PublishOutcome.UPLOADED_PENDING_RELEASE
        return

    yt_client = _build_yt_client(
        conn,
        cp_channel_id=cp_channel_id,
        workspace_id=workspace_id,
        yt_client_factory=yt_client_factory,
    )
    if yt_client is None:
        _fail(
            conn,
            slot_id,
            result,
            category=PublishFailureCategory.RELEASE_FAILED_RETRYABLE,
            message="No provider client available to perform the public release.",
            set_outcome=False,
        )
        result.outcome = PublishOutcome.UPLOADED_PENDING_RELEASE
        return

    account_id = _account_for_publication(conn, publication_id)
    release = release_publication_to_public(
        conn,
        publication_id=publication_id,
        provider_video_id=provider_video_id,
        workspace_id=workspace_id,
        platform_account_id=account_id,
        actor=actor,
        yt_client=yt_client,
    )

    if not release.is_public:
        category = (
            PublishFailureCategory.RELEASE_FAILED_TERMINAL
            if release.outcome is ReleaseOutcome.video_not_found
            else PublishFailureCategory.RELEASE_FAILED_RETRYABLE
        )
        _fail(
            conn,
            slot_id,
            result,
            category=category,
            message=release.detail,
            set_outcome=False,
        )
        result.outcome = PublishOutcome.UPLOADED_PENDING_RELEASE
        return

    # Public from here on, even if local bookkeeping had trouble.
    _mark_publication_published(conn, publication_id)
    mark_slot_released(conn, slot_id, publication_id=publication_id)
    result.released = True
    result.outcome = PublishOutcome.RELEASED
    result.reason = release.detail

    if release.outcome is ReleaseOutcome.local_persist_failed:
        result.errors.append(release.detail)

    _handoff_to_analytics(
        conn,
        publication_id=publication_id,
        workspace_id=workspace_id,
        cp_channel_id=cp_channel_id,
        account_id=account_id,
        result=result,
    )

    # Analytics registration comes first so the ledger can land directly on
    # `observing` rather than pausing at `published` until the next
    # reconciliation pass. Ordering is an optimisation only — either state is
    # correct and the reconciler repairs a missed promotion.
    _handoff_to_experiment(conn, publication_id=publication_id, actor=actor, result=result)

    logger.info("publishing cycle: slot %d released publicly as %s", slot_id, provider_video_id)


def _account_for_publication(conn: sqlite3.Connection, publication_id: int) -> str | None:
    row = conn.execute(
        "SELECT platform_account_id FROM publications WHERE id = ?", (publication_id,)
    ).fetchone()
    return row["platform_account_id"] if row else None


def _mark_publication_published(conn: sqlite3.Connection, publication_id: int) -> None:
    """Move the publication to its terminal 'published' status.

    The analytics observer only adopts publications that are both public and
    'published', so this transition is what actually opens the learning loop.
    """
    from app.publishing.constants import PUB_STATUS_PUBLISHED
    from app.publishing.repository import update_publication_status

    row = conn.execute("SELECT status FROM publications WHERE id = ?", (publication_id,)).fetchone()
    if row is None or row["status"] == PUB_STATUS_PUBLISHED:
        return
    try:
        update_publication_status(
            conn, publication_id, PUB_STATUS_PUBLISHED, published_at=_now_iso()
        )
        conn.commit()
    except Exception as exc:
        logger.warning(
            "publishing cycle: could not mark publication %d as published: %s",
            publication_id,
            exc,
        )


def _handoff_to_analytics(
    conn: sqlite3.Connection,
    *,
    publication_id: int,
    workspace_id: str,
    cp_channel_id: str,
    account_id: str | None,
    result: PublishingCycleResult,
) -> None:
    """Register the new publication with the existing observer (section 19).

    Reuses Phase 16D.4's idempotent registration rather than duplicating any
    analytics logic. A failure here is logged but never fails the cycle — the
    video is already public, and the observer's own orphan-adoption
    reconciliation is the safety net.
    """
    from app.analytics.observation import register_publication_for_observation

    try:
        row = conn.execute(
            "SELECT published_at FROM publications WHERE id = ?", (publication_id,)
        ).fetchone()
        schedule_id = register_publication_for_observation(
            conn,
            publication_id=publication_id,
            workspace_id=workspace_id,
            channel_id=cp_channel_id,
            platform_account_id=account_id,
            published_at=row["published_at"] if row else None,
        )
        result.observation_schedule_id = schedule_id
        logger.info(
            "publishing cycle: publication %d registered for analytics observation (%s)",
            publication_id,
            schedule_id,
        )
    except Exception as exc:
        logger.warning(
            "publishing cycle: analytics observation registration failed for "
            "publication %d (non-fatal, orphan adoption will retry): %s",
            publication_id,
            exc,
        )
        result.errors.append(f"Analytics registration failed: {exc}")


def _handoff_to_experiment(
    conn: sqlite3.Connection,
    *,
    publication_id: int,
    actor: str,
    result: PublishingCycleResult,
) -> None:
    """Advance the experiment ledger now that the video is confirmed PUBLIC.

    The experiment is derived from the publication's own lineage rather than
    taken from the slot, so this cannot attach the wrong experiment even if
    slot state is stale. Failure here is never fatal — the video is already
    public, and reconcile_experiment_lifecycle() repairs anything missed.
    """
    from app.intelligence.experiments.lifecycle import advance_experiment_for_publication

    advance = advance_experiment_for_publication(
        conn, publication_id, actor=actor, reason="autonomous publishing cycle released"
    )
    result.experiment_status = advance.to_status
    if advance.error:
        result.errors.append(f"Experiment lifecycle advance failed: {advance.error}")
    elif advance.changed:
        logger.info(
            "publishing cycle: experiment %s advanced to %s",
            advance.experiment_id,
            advance.to_status,
        )


def _fail(
    conn: sqlite3.Connection,
    slot_id: int,
    result: PublishingCycleResult,
    *,
    category: PublishFailureCategory,
    message: str,
    increment_retry: bool = True,
    set_outcome: bool = True,
) -> None:
    """Record a publishing failure on both the slot and the result."""
    mark_slot_publish_failed(
        conn,
        slot_id,
        category=category.value,
        error=message,
        increment_retry=increment_retry,
    )
    if set_outcome:
        result.outcome = PublishOutcome.FAILED
    result.failure_category = category.value
    result.error_message = message
    result.reason = message
    result.errors.append(message)
    logger.warning("publishing cycle: slot %d failed (%s) — %s", slot_id, category.value, message)


__all__ = ["run_publishing_cycle", "MAX_PUBLISH_RETRIES"]
