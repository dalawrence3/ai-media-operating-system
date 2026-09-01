"""Phase 18B — the autonomous production cycle.

Connects a FILLED Phase 18A publishing slot to the EXISTING experiment and
production pipeline, driving it through:

  research -> script_generation -> production_plan -> narration -> captions
  -> visual_intelligence -> rendering

then a read-only publishing preflight and an internal DRAFT publishing plan.
It never runs the pipeline's own 'publishing' stage.

STRUCTURAL HARD STOP — read this before touching this file:
    This module imports ONLY app.publishing.repository (create_publishing_plan,
    a DRAFT row — no job, no provider call) and app.publishing.validation
    (pure, read-only checks). It NEVER imports app.publishing.orchestrator,
    app.publishing.upload_gate, or anything under app.publishing.providers —
    the modules that actually construct a publishing_jobs row or call a live
    provider. The pipeline itself is started with end_stage="rendering", so
    the shared pipeline framework never even instantiates the 'publishing'
    stage's executor (a stub requiring a live provider). There is no code
    path reachable from run_production_cycle() that can upload to YouTube —
    this is an architectural property, not a runtime `if gate == false` check.

Two existing human gates get bridged here, deliberately, only when
production_automation_enabled is true for this channel:
  1. Phase 14E kept Experiment creation "downstream, human-gated" — this
     module is that explicitly-authorized downstream path. Manual/CLI
     experiment creation is untouched.
  2. The pipeline's REVIEW_REQUIRED_STAGES mechanism assumes a human calls
     both the artifact-specific approve_* function (approve_script,
     approve_production_plan, approve_narration_run, approve_caption_run,
     approve_scene_manifest, approve_render_manifest — each a distinct,
     pre-existing, idempotent primitive) AND the pipeline-level
     approve_review_item. This module calls both, as this channel's own
     autonomous actor, for exactly the stages already run this cycle —
     never speculatively, never for a stage a human hasn't actually seen
     the artifact of (there is no human in this mode; the artifact IS the
     thing being "reviewed" by the same policy that authorized production).
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from datetime import UTC, datetime
from typing import Any

from app.intelligence.autonomy.models import (
    DeadlineStatus,
    ProductionCycleResult,
    ProductionOutcome,
)
from app.intelligence.autonomy.repository import (
    find_slot_needing_production,
    get_autonomy_policy,
    mark_slot_production_failed,
    mark_slot_production_ready,
    reset_slot_production_for_retry,
    start_slot_production,
)

logger = logging.getLogger(__name__)

_PIPELINE_END_STAGE = "rendering"
_MAX_PRODUCTION_RETRIES = 2

# stage -> (artifact table module, approve_fn name, needs actor kwarg)
# stage -> (approve_fn module, approve_fn name, needs actor kwarg, artifact table)
_STAGE_APPROVERS: dict[str, tuple[str, str, bool, str]] = {
    "script_generation": ("app.core.repository", "approve_script", False, "scripts"),
    "production_plan": (
        "app.production.repository",
        "approve_production_plan",
        True,
        "production_plans",
    ),
    "narration": ("app.narration.repository", "approve_narration_run", True, "narration_runs"),
    "captions": ("app.captions.repository", "approve_caption_run", True, "caption_runs"),
    "visual_intelligence": (
        "app.scenes.repository",
        "approve_scene_manifest",
        True,
        "scene_manifests",
    ),
    "rendering": ("app.media.repository", "approve_render_manifest", True, "render_manifests"),
}


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _now_iso() -> str:
    return _now_utc().strftime("%Y-%m-%dT%H:%M:%S")


def _deadline_status(scheduled_for_utc: str, now: datetime) -> DeadlineStatus:
    try:
        deadline = datetime.fromisoformat(scheduled_for_utc.replace("Z", "+00:00"))
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=UTC)
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


def run_production_cycle(
    conn: sqlite3.Connection,
    *,
    cp_channel_id: str,
    workspace_id: str,
    actor: str = "system:autonomy-production",
    anthropic_api_key: str = "",
    elevenlabs_api_key: str = "",
    voice_profile_id: int | None = None,
) -> ProductionCycleResult:
    """Run one channel-scoped autonomous production cycle.

    Consumes at most ONE filled slot per call (section 9: production must
    not let the decision cycle's bounded queue balloon). Restart-safe: every
    decision about "what to do next" is re-derived from persisted pipeline
    and slot state at the top of this call, never carried in memory across
    invocations.
    """
    started_at = _now_iso()
    policy = get_autonomy_policy(conn, cp_channel_id)
    result = ProductionCycleResult(
        channel_id=cp_channel_id,
        workspace_id=workspace_id,
        slot_id=None,
        started_at=started_at,
    )

    if policy is None or not policy.production_automation_enabled:
        result.outcome = ProductionOutcome.DISABLED
        result.reason = "Production automation is not enabled for this channel."
        result.completed_at = _now_iso()
        return result

    slot = find_slot_needing_production(conn, cp_channel_id, max_retries=_MAX_PRODUCTION_RETRIES)
    if slot is None:
        result.outcome = ProductionOutcome.NO_SLOT_TO_PRODUCE
        result.reason = (
            "No filled slot is waiting on production (or the only candidates "
            "already exhausted their retry bound)."
        )
        result.completed_at = _now_iso()
        return result
    result.slot_id = slot.id
    result.deadline_status = _deadline_status(slot.scheduled_for_utc, _now_utc())
    result.retry_count = slot.production_retry_count

    # Concurrency lock — slot-scoped (production is long-running and
    # resumable across many calls, unlike the decision cycle's hour-bucketed
    # lock). A 'completed'/'failed' prior operation for this exact slot is
    # fine to reuse the key for again; only a still in-flight one blocks.
    from app.control_plane.jobs import complete_operation, fail_operation
    from app.control_plane.jobs import start_operation as _start_operation
    from app.control_plane.repository import get_operation_by_idempotency_key

    idempotency_key = f"autonomy_production:{cp_channel_id}:{slot.id}"
    result.idempotency_key = idempotency_key
    existing_op = get_operation_by_idempotency_key(conn, idempotency_key)
    if existing_op is not None and existing_op.status in ("pending", "running"):
        result.already_running = True
        result.operation_id = existing_op.id
        result.outcome = ProductionOutcome.ALREADY_RUNNING
        result.reason = (
            f"Production for slot {slot.id} is already running (operation {existing_op.id})."
        )
        result.completed_at = _now_iso()
        return result

    # A terminal (completed/failed) operation with this exact key is reused
    # in place by start_operation's own idempotency check — but we always
    # want THIS call's attempt reflected, so mint a fresh key per attempt
    # once a prior attempt has concluded.
    if existing_op is not None:
        idempotency_key = f"{idempotency_key}:retry{slot.production_retry_count}"
        result.idempotency_key = idempotency_key

    operation = _start_operation(
        conn,
        operation_type="autonomous_production_cycle",
        workspace_id=workspace_id,
        actor=actor,
        channel_id=cp_channel_id,
        idempotency_key=idempotency_key,
        input_data={"slot_id": slot.id},
    )
    result.operation_id = operation.id

    try:
        _run_locked_production(
            conn,
            slot_id=slot.id,
            cp_channel_id=cp_channel_id,
            workspace_id=workspace_id,
            actor=actor,
            anthropic_api_key=anthropic_api_key,
            elevenlabs_api_key=elevenlabs_api_key,
            voice_profile_id=voice_profile_id,
            result=result,
        )
        complete_operation(conn, operation.id, output_data={"outcome": result.outcome.value})
        # update_operation_status() does not commit. Without this the lease is
        # left 'pending' when the process exits, and a later cycle needing to
        # retry this slot would see ALREADY_RUNNING forever.
        conn.commit()
    except Exception as exc:  # noqa: BLE001 — must never propagate out of a scheduler tick
        result.outcome = ProductionOutcome.FAILED
        result.reason = f"Unhandled exception in production cycle: {exc}"
        result.errors.append(str(exc))
        mark_slot_production_failed(
            conn,
            slot.id,
            stage=result.failed_stage or "orchestrator",
            error=str(exc),
        )
        fail_operation(conn, operation.id, str(exc))
        conn.commit()

    result.completed_at = _now_iso()
    return result


def _run_locked_production(
    conn: sqlite3.Connection,
    *,
    slot_id: int,
    cp_channel_id: str,
    workspace_id: str,
    actor: str,
    anthropic_api_key: str,
    elevenlabs_api_key: str,
    voice_profile_id: int | None,
    result: ProductionCycleResult,
) -> None:
    from app.intelligence.autonomy.repository import get_slot

    slot = get_slot(conn, slot_id)
    if slot is None:
        raise RuntimeError(f"Slot {slot_id} disappeared mid-cycle")

    if slot.production_status == "failed":
        slot = reset_slot_production_for_retry(conn, slot.id)

    # ── 1. Materialize experiment (idempotent via create_experiment's own input_hash) ──
    experiment, brief = _materialize_experiment(conn, slot=slot, actor=actor)
    result.experiment_id = experiment.id

    # ── 2. Materialize topic (idempotent via promote_opportunity) ──────────
    topic = _materialize_topic(conn, slot=slot, brief=brief, workspace_id=workspace_id)
    result.topic_id = topic.id

    # ── 3. Find-or-create the bounded pipeline (research..rendering only) ──
    from app.application import state as pipeline_state
    from app.application.commands import StartPipelineCommand
    from app.application.errors import PipelineAlreadyExistsError
    from app.application.pipeline import start_pipeline

    # Never resume a pipeline that has already reached a terminal state.
    #
    # _drive_pipeline's first act is to read the pipeline and return
    # immediately when its status is 'failed' or 'blocked'. Reusing one
    # across retries therefore made every retry inert: it re-reported the
    # original error without re-executing anything, and the slot burned its
    # whole retry budget standing still. Observed live — slot 3 failed at
    # narration, the cause was fixed, and the retry reproduced the identical
    # stale error.
    #
    # Deliberately keyed on the pipeline's ACTUAL state rather than on the
    # slot's retry counter: the counter can be reset by an operator repairing
    # a slot, and a key derived from it would then silently resume a dead
    # pipeline again. An in-flight (or completed) pipeline is still resumed,
    # which is what keeps a restart mid-production from starting over.
    #
    # Failed pipelines are left intact as history rather than rewritten,
    # consistent with how missed slots and superseded recommendations are
    # handled elsewhere.
    _TERMINAL_PIPELINE_STATUSES = ("failed", "blocked")
    base_key = f"autonomy_production_pipeline:{slot.id}"
    pv = None
    for attempt in range(_MAX_PRODUCTION_RETRIES + 2):
        key = base_key if attempt == 0 else f"{base_key}:retry{attempt}"
        try:
            pv = start_pipeline(
                conn,
                StartPipelineCommand(
                    workspace_id=workspace_id,
                    channel_id=cp_channel_id,
                    actor=actor,
                    idempotency_key=key,
                    topic_id=topic.id,
                    start_stage="research",
                    end_stage=_PIPELINE_END_STAGE,
                    experiment_id=experiment.id,
                ),
            )
            break
        except PipelineAlreadyExistsError as exc:
            existing = pipeline_state.get_pipeline(conn, exc.existing_id)
            if existing.status not in _TERMINAL_PIPELINE_STATUSES:
                pv = existing
                break
            # Terminal: leave it as history and try the next attempt key.
    if pv is None:
        raise RuntimeError(
            f"Slot {slot.id}: every production pipeline attempt is in a terminal "
            "state; the slot needs operator attention rather than another retry."
        )
    result.pipeline_id = pv.id

    start_slot_production(conn, slot.id, experiment_id=experiment.id, pipeline_id=pv.id)
    _ensure_experiment_status(conn, experiment.id, "planned", actor=actor)
    _ensure_experiment_status(conn, experiment.id, "in_production", actor=actor)
    _ensure_execution_contract(conn, experiment=experiment, brief=brief, result=result)

    # An explicit caller-supplied voice wins; otherwise resolve the channel's
    # own. Without this the scheduler-driven path reaches narration with no
    # voice at all and fails the stage every time.
    effective_voice_profile_id = voice_profile_id
    if effective_voice_profile_id is None:
        effective_voice_profile_id = resolve_voice_profile_id(conn, brief.channel_id)
        if effective_voice_profile_id is None:
            result.errors.append(
                "No voice profile is configured for this channel and no global "
                "default exists; narration cannot run."
            )
    result.voice_profile_id = effective_voice_profile_id

    effective_config = _build_effective_config(
        brief=brief,
        anthropic_api_key=anthropic_api_key,
        elevenlabs_api_key=elevenlabs_api_key,
        voice_profile_id=effective_voice_profile_id,
    )

    # ── 4. Drive the pipeline stage by stage, autonomously approving each ──
    ok = _drive_pipeline(
        conn,
        pipeline_id=pv.id,
        workspace_id=workspace_id,
        actor=actor,
        effective_config=effective_config,
        result=result,
    )
    if not ok:
        mark_slot_production_failed(
            conn,
            slot.id,
            stage=result.failed_stage or "unknown",
            error=result.error_message or "pipeline did not reach rendering",
        )
        result.outcome = ProductionOutcome.FAILED
        result.reason = (
            f"Production failed at stage {result.failed_stage!r}: {result.error_message}"
        )
        return

    # ── 5. Preflight + internal DRAFT publishing plan ───────────────────────
    _run_preflight_and_create_plan(
        conn,
        slot=slot,
        experiment=experiment,
        topic=topic,
        pipeline_id=pv.id,
        workspace_id=workspace_id,
        actor=actor,
        result=result,
    )


# ---------------------------------------------------------------------------
# 1. Experiment materialization
# ---------------------------------------------------------------------------


def _materialize_experiment(conn: sqlite3.Connection, *, slot: Any, actor: str) -> tuple[Any, Any]:
    from app.intelligence.experiments.brief_service import get_strategy_brief
    from app.intelligence.experiments.models import ExperimentType, FactorRole, MetricDirection
    from app.intelligence.experiments.repository import (
        add_factor,
        add_metric_target,
        create_experiment,
    )

    brief = get_strategy_brief(conn, slot.brief_id)
    if brief is None:
        raise RuntimeError(f"Strategy brief {slot.brief_id} referenced by slot {slot.id} not found")

    intel_channel_id = brief.channel_id
    experiment_id = f"exp-slot-{slot.id}"
    experiment = create_experiment(
        conn,
        experiment_id=experiment_id,
        channel_id=intel_channel_id,
        experiment_type=ExperimentType(brief.experiment_type),
        hypothesis=brief.hypothesis,
        opportunity_id=brief.opportunity_id,
        hypothesis_metric=brief.target_metric,
        policy_snapshot={"brief_id": brief.id, "slot_id": slot.id},
        actor=actor,
    )

    direction = (
        MetricDirection.higher_is_better
        if brief.target_direction == "higher_is_better"
        else MetricDirection.lower_is_better
    )
    add_metric_target(
        conn,
        experiment.id,
        metric_name=brief.target_metric,
        direction=direction,
        is_primary=True,
    )
    for tf in brief.treatment_factors:
        add_factor(
            conn,
            experiment.id,
            factor_name=tf.factor_name,
            factor_role=FactorRole.treatment,
            intended_value=tf.intended_value,
            value_type=tf.value_type,
        )
    for cf in brief.controlled_factors:
        add_factor(
            conn,
            experiment.id,
            factor_name=cf.factor_name,
            factor_role=FactorRole.controlled,
            intended_value=cf.baseline_value,
            value_type="string",
        )
    conn.commit()
    return experiment, brief


def _ensure_execution_contract(
    conn: sqlite3.Connection, *, experiment: Any, brief: Any, result: ProductionCycleResult
) -> None:
    """Create the experiment's execution contract if it does not exist yet.

    Phase 14G's outcome evaluator refuses to score an experiment with no
    execution contract (INVALID_EXECUTION), so without this the autonomous
    path could never produce an outcome at all — the contract was only ever
    created by the operator CLI.

    Idempotent (create_execution_contract returns any existing row) and
    never fatal: a contract that cannot be built means outcomes stay
    honestly unevaluable, which is strictly better than failing a
    production run that is otherwise fine.
    """
    from app.intelligence.experiments.execution_service import (
        ExecutionMode,
        create_execution_contract,
    )

    try:
        create_execution_contract(
            conn,
            experiment.id,
            brief.id,
            mode=ExecutionMode.REAL,
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 — outcome scoring is not worth failing production for
        result.errors.append(f"execution contract: {exc}")
        logger.warning(
            "production cycle: could not create execution contract for %s "
            "(non-fatal; outcomes will report unevaluable): %s",
            experiment.id,
            exc,
        )


_LIFECYCLE_ORDER = (
    "draft",
    "planned",
    "in_production",
    "published",
    "observing",
    "mature",
    "analyzed",
    "completed",
)


def _ensure_experiment_status(
    conn: sqlite3.Connection, experiment_id: str, target: str, *, actor: str
) -> None:
    """Advance the experiment to `target` if it hasn't reached it yet.

    On a resumed cycle the experiment may already be at or past `target`
    (e.g. already 'in_production' from a prior, crashed attempt) — in that
    case this is a no-op, never an attempted backward transition (which
    ALLOWED_TRANSITIONS correctly rejects as invalid)."""
    from app.intelligence.experiments.models import ExperimentStatus
    from app.intelligence.experiments.repository import get_experiment, transition_experiment_state

    exp = get_experiment(conn, experiment_id)
    current = exp.status.value
    if current not in _LIFECYCLE_ORDER or target not in _LIFECYCLE_ORDER:
        return  # cancelled or another terminal/unknown state — nothing to advance
    if _LIFECYCLE_ORDER.index(current) >= _LIFECYCLE_ORDER.index(target):
        return
    transition_experiment_state(
        conn, experiment_id, ExperimentStatus(target), actor=actor, reason="autonomous_production"
    )
    conn.commit()


# ---------------------------------------------------------------------------
# 2. Topic materialization
# ---------------------------------------------------------------------------


def _build_topic_angle(*, brief: Any, decision: Any) -> str:
    """Compose the topic's angle, carrying forward semantic context we paid for.

    `topics.angle` already reaches script generation as the prompt's
    `topic_angle` variable, so this forwards the evaluation's viewer promise
    and concrete subjects through the EXISTING mechanism rather than widening
    the script-generation contract. Nothing downstream changes shape.

    It is worth doing because the autonomous path currently wastes this field:
    for the live example it held "universe edge boundaries cosmology" — a
    verbatim copy of the title, telling the script generator nothing it did not
    already have.

    Falls back to the brief's own canonical topic / market theme, which is the
    pre-18E.1 behaviour, whenever the evaluation offers nothing better.
    """
    base = brief.canonical_topic or brief.market_theme or ""
    parts: list[str] = []

    promise = (getattr(decision, "viewer_promise", None) or "").strip()
    if promise:
        parts.append(promise)
    elif base:
        parts.append(base)

    subjects = [
        s.strip() for s in (getattr(decision, "concrete_subjects", None) or []) if s.strip()
    ]
    if subjects:
        parts.append("Concrete subjects to show: " + "; ".join(subjects[:6]) + ".")

    return " ".join(parts).strip() or base


def _materialize_topic(
    conn: sqlite3.Connection, *, slot: Any, brief: Any, workspace_id: str
) -> Any:
    """Promote the brief's opportunity to a Topic (idempotent — see
    promote_opportunity's own docstring), then set the topic's
    workspace_id explicitly.

    This is the exact column the shared pipeline framework's own
    validate_start_command() checks for cross-workspace access — the
    phase's warning is against RELYING on it being already correct from
    older/other data, not against setting it correctly here. Since we know
    precisely which workspace this topic belongs to (the channel we're
    producing for), setting it explicitly is unambiguous and correct;
    promote_opportunity itself never sets it, deliberately, since it has
    no workspace context of its own to draw on.
    """
    from app.intelligence.experiments.eligibility_service import select_production_topic
    from app.intelligence.repository import get_opportunity, promote_opportunity

    # ── Phase 18E.1: use the refinement the evaluation already produced ─────
    #
    # Phase 18E generated a concrete framing for every narrow_theme candidate
    # and persisted it, then materialized the opportunity's own title anyway.
    # The live example: source "universe edge boundaries cosmology" against a
    # generated "Does the universe have an edge, and what would it mean if it
    # did?" — the system knew the better answer and did not use it.
    #
    # The opportunity row is left untouched; only the topic's production-facing
    # title changes, and only when the policy says so.
    opportunity = get_opportunity(conn, brief.opportunity_id)
    source_topic = ""
    if opportunity is not None:
        source_topic = opportunity.title or opportunity.raw_topic or ""

    decision = select_production_topic(
        conn, opportunity_id=brief.opportunity_id, source_topic=source_topic
    )
    if decision.changed:
        logger.info(
            "Topic refinement applied for opportunity %s: %r -> %r (%s)",
            brief.opportunity_id,
            decision.source_topic,
            decision.production_topic,
            decision.reason,
        )
    else:
        logger.info(
            "Topic refinement not applied for opportunity %s (%s)",
            brief.opportunity_id,
            decision.reason,
        )

    angle = _build_topic_angle(brief=brief, decision=decision)
    topic, _event = promote_opportunity(
        conn,
        brief.opportunity_id,
        angle_override=angle,
        title_override=decision.production_topic if decision.changed else None,
        operator="system:autonomy-production",
        allow_unscored=True,
    )
    conn.execute(
        "UPDATE topics SET workspace_id = ? WHERE id = ? "
        "AND (workspace_id IS NULL OR workspace_id = ?)",
        (workspace_id, topic.id, workspace_id),
    )
    conn.commit()
    return topic


# ---------------------------------------------------------------------------
# 3. effective_config from the brief's content constraints
# ---------------------------------------------------------------------------


def resolve_voice_profile_id(conn: sqlite3.Connection, intel_channel_id: int | None) -> int | None:
    """The voice this channel narrates with, or None if none is configured.

    Narration cannot run without one — NarrationExecutor requires
    `effective_config['voice_profile_id']` and fails the stage outright
    otherwise. Nothing in the scheduler-driven path ever supplied it, so
    autonomous production failed at narration every time. That went unnoticed
    because the interval defect meant the production cycle almost never ran,
    and the one Phase 18C production was invoked manually with an explicit id.

    Resolution prefers a profile bound to this channel over the global
    default, and never returns a superseded one. Returning None rather than
    guessing keeps "no voice configured" a legible failure instead of a
    silently wrong voice.
    """
    if intel_channel_id is not None:
        row = conn.execute(
            """SELECT id FROM voice_profiles
               WHERE channel_id = ? AND superseded_by_id IS NULL
               ORDER BY is_default DESC, version DESC, id DESC LIMIT 1""",
            (intel_channel_id,),
        ).fetchone()
        if row is not None:
            return int(row["id"])

    row = conn.execute(
        """SELECT id FROM voice_profiles
           WHERE channel_id IS NULL AND superseded_by_id IS NULL
           ORDER BY is_default DESC, version DESC, id DESC LIMIT 1"""
    ).fetchone()
    return int(row["id"]) if row is not None else None


def _build_effective_config(
    *,
    brief: Any,
    anthropic_api_key: str,
    elevenlabs_api_key: str,
    voice_profile_id: int | None,
) -> dict[str, Any]:
    cc = brief.content_constraints
    config: dict[str, Any] = {}
    if anthropic_api_key:
        config["anthropic_api_key"] = anthropic_api_key
    if elevenlabs_api_key:
        config["elevenlabs_api_key"] = elevenlabs_api_key
    if voice_profile_id is not None:
        config["voice_profile_id"] = voice_profile_id
    tone_parts = [p for p in (cc.brand_voice, cc.content_style) if p]
    if tone_parts:
        config["tone"] = ", ".join(tone_parts)
    if cc.audience_description:
        config["audience"] = cc.audience_description

    # ── Visual treatment (Phase 18E) ────────────────────────────────────────
    # Without this the visual engine ran the balanced default for every
    # autonomous video regardless of what the experiment declared, so
    # `visual_style` was an experiment factor nothing enforced. Reading it from
    # the brief's treatment factors — and validating it against the styles the
    # renderer actually implements — is what makes the factor ENFORCED rather
    # than aspirational.
    style = _visual_style_from_brief(brief)
    if style:
        config["visual_style"] = style
    return config


def _visual_style_from_brief(brief: Any) -> str | None:
    """The visual style this experiment's treatment asked for, if any.

    Treatment factors win over controlled ones: a controlled `visual_style`
    records the baseline the experiment is holding constant, which is the
    pipeline default and so needs no override.
    """
    from app.visuals.policy import VISUAL_STYLE_SAFE_VALUES

    for factor in getattr(brief, "treatment_factors", None) or []:
        if getattr(factor, "factor_name", None) != "visual_style":
            continue
        value = (getattr(factor, "intended_value", None) or "").strip().lower()
        if value in VISUAL_STYLE_SAFE_VALUES:
            return value
        if value:
            logger.warning(
                "Brief %s requested unknown visual style %r; the renderer will use "
                "its default rather than a style it cannot produce.",
                getattr(brief, "id", "?"),
                value,
            )
        return None
    return None


# ---------------------------------------------------------------------------
# 4. Stage-by-stage drive loop
# ---------------------------------------------------------------------------


def _drive_pipeline(
    conn: sqlite3.Connection,
    *,
    pipeline_id: str,
    workspace_id: str,
    actor: str,
    effective_config: dict[str, Any],
    result: ProductionCycleResult,
) -> bool:
    from app.application import state as pipeline_state
    from app.application.commands import ExecutePipelineStageCommand
    from app.application.executor import get_default_executor_registry, register_default_executors
    from app.application.pipeline import execute_pipeline_stage
    from app.application.review import approve_review_item

    # The shared executor registry is normally populated once by the API/CLI
    # composition root (build_application_service). This orchestrator can run
    # from a scheduler tick before any request has touched that path, so it
    # ensures registration itself — idempotent (register_default_executors
    # no-ops on an already-registered same-class/version executor).
    register_default_executors(get_default_executor_registry(), replace=False)

    # Each review-required stage costs two loop iterations (execute, then
    # approve); research costs one (it never parks for review). Bounded, not
    # infinite — a stage that keeps returning waiting_for_review/running
    # without ever reaching completed/failed/blocked still terminates here.
    max_iterations = 2 * len(_STAGE_APPROVERS) + 4
    for _ in range(max_iterations):
        pv = pipeline_state.get_pipeline(conn, pipeline_id)

        if pv.status == "completed":
            result.stages_completed = [s.stage for s in pv.stages if s.status == "completed"]
            return True
        if pv.status in ("failed", "blocked"):
            failed = next((s for s in pv.stages if s.status == "failed"), None)
            result.failed_stage = failed.stage if failed else pv.current_stage
            result.error_category = "execution_error" if pv.status == "failed" else "blocked"
            result.error_message = (
                failed.error_message if failed else pv.blocked_reason
            ) or pv.status
            return False

        waiting = next((s for s in pv.stages if s.status == "waiting_for_review"), None)
        if waiting is not None:
            _approve_artifact(
                conn,
                stage=waiting.stage,
                artifact_type=waiting.artifact_type,
                artifact_id=waiting.artifact_id,
                actor=actor,
            )
            approve_review_item(conn, "pipeline_review", pipeline_id, workspace_id, actor)
            result.stages_completed.append(waiting.stage)
            continue

        current = pv.current_stage
        if current is None:
            result.failed_stage = "unknown"
            result.error_message = (
                "pipeline has no current_stage but is not completed/failed/blocked"
            )
            return False

        pv2 = execute_pipeline_stage(
            conn,
            ExecutePipelineStageCommand(
                pipeline_id=pipeline_id,
                workspace_id=workspace_id,
                stage=current,
                actor=actor,
                effective_config=effective_config,
            ),
        )
        if pv2.status == "failed":
            failed = next((s for s in pv2.stages if s.status == "failed"), None)
            result.failed_stage = current
            result.error_category = "execution_error"
            result.error_message = (failed.error_message if failed else None) or "stage failed"
            return False
        if pv2.status == "blocked":
            result.failed_stage = current
            result.error_category = "blocked"
            result.error_message = pv2.blocked_reason or "stage blocked"
            return False
        _account_external_calls(current, result)

    result.failed_stage = result.failed_stage or "unknown"
    result.error_message = (
        result.error_message or "pipeline did not converge within the stage budget"
    )
    return False


def _account_external_calls(stage: str, result: ProductionCycleResult) -> None:
    if stage == "script_generation":
        result.llm_calls += 1
    elif stage == "narration":
        result.tts_runs += 1
    elif stage == "rendering":
        result.visual_provider_calls += 1


def _approve_artifact(
    conn: sqlite3.Connection,
    *,
    stage: str,
    artifact_type: str | None,
    artifact_id: str | None,
    actor: str,
) -> None:
    """Approve the underlying content artifact for a stage waiting for
    review — the content-level counterpart to the pipeline's own
    waiting_for_review bookkeeping (see module docstring, gate #2). A
    missing mapping or artifact is a hard error, not a silent skip: an
    unapproved artifact would make every downstream stage's own
    'get_active_approved_*' lookup fail anyway, so failing loudly here is
    strictly more diagnosable."""
    import importlib

    mapping = _STAGE_APPROVERS.get(stage)
    if mapping is None or artifact_id is None:
        return
    module_name, fn_name, needs_actor, table = mapping
    module = importlib.import_module(module_name)
    fn = getattr(module, fn_name)

    if stage == "rendering":
        # The pipeline's own artifact_id for rendering is the output file
        # path, not a row id — approve_render_manifest needs the render
        # manifest's own integer id, resolved via the render job it produced.
        row = conn.execute(
            "SELECT render_manifest_id FROM render_jobs "
            "WHERE output_path = ? ORDER BY id DESC LIMIT 1",
            (artifact_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"No render_job found for output_path {artifact_id!r}")
        target_id: int | str = row["render_manifest_id"]
    else:
        target_id = int(artifact_id)

    # Idempotency: a crash can happen after the content-level approve_*
    # succeeds but before the pipeline's own waiting_for_review bookkeeping
    # commits — a resumed cycle would then see the SAME stage still
    # "waiting_for_review" and attempt to approve an already-approved
    # artifact, which every approve_* function's own state machine
    # correctly rejects. Checking first makes the resume a genuine no-op
    # instead of a spurious failure.
    current_status = conn.execute(
        f"SELECT status FROM {table} WHERE id = ?",
        (target_id,),
    ).fetchone()
    if current_status is not None and current_status["status"] == "approved":
        return

    if needs_actor:
        fn(conn, target_id, actor=actor)
    else:
        fn(conn, target_id)
    conn.commit()


# ---------------------------------------------------------------------------
# 5. Preflight + draft publishing plan
# ---------------------------------------------------------------------------


def _check_visual_quality(
    conn: sqlite3.Connection,
    *,
    approved_render: Any,
    experiment: Any,
    result: ProductionCycleResult,
) -> tuple[bool, list[str]]:
    """Read this render's persisted visual-quality verdict and enforce it.

    Deliberately a READ, not a re-measurement. The assessment was computed and
    stored by the rendering stage against the beats it actually resolved; a
    second measurement here could only disagree with it, and the render is what
    it is either way.

    A MISSING assessment is a warning, not a block. Blocking on absence would
    make every render produced before this phase — and every render whose
    assessment write failed for an unrelated reason — permanently unpublishable,
    which trades one silent failure for a louder one. The condition this phase
    exists to catch is a render that was measured and found wanting.
    """
    from app.visuals.assessment_repository import get_assessment

    try:
        assessment = get_assessment(conn, approved_render.render_manifest_id)
    except Exception as exc:  # noqa: BLE001 — a read failure must not masquerade as a pass
        result.errors.append(f"visual quality assessment unreadable: {exc}")
        logger.warning(
            "Visual quality assessment could not be read for render manifest %s: %s",
            approved_render.render_manifest_id,
            exc,
        )
        return True, []

    if assessment is None:
        result.errors.append(
            f"No visual quality assessment exists for render manifest "
            f"{approved_render.render_manifest_id}; publishing proceeded unassessed."
        )
        logger.warning(
            "No visual quality assessment for render manifest %s (experiment %s)",
            approved_render.render_manifest_id,
            getattr(experiment, "id", None),
        )
        return True, []

    result.visual_quality_status = assessment.status
    result.visual_quality_findings = [
        f"[{f.get('severity')}] {f.get('code')}: {f.get('message')}" for f in assessment.findings
    ]

    if assessment.blocked:
        return False, [f.get("message", f.get("code", "")) for f in assessment.blocking_findings]

    if assessment.warning_findings:
        logger.info(
            "Render manifest %s passed the visual quality floor with %d warning(s)",
            approved_render.render_manifest_id,
            len(assessment.warning_findings),
        )
    return True, []


def _run_preflight_and_create_plan(
    conn: sqlite3.Connection,
    *,
    slot: Any,
    experiment: Any,
    topic: Any,
    pipeline_id: str,
    workspace_id: str,
    actor: str,
    result: ProductionCycleResult,
) -> None:
    from app.media.repository import get_approved_render
    from app.publishing.constants import PUBLISHING_ENGINE_VERSION
    from app.publishing.errors import PublishingValidationError
    from app.publishing.models import PublishingMetadataDraft, PublishingScheduleDraft
    from app.publishing.repository import create_publishing_plan
    from app.publishing.validation import validate_approved_render_for_publishing

    scene_manifest_row = conn.execute(
        "SELECT artifact_id FROM app_pipeline_stage_log "
        "WHERE pipeline_id = ? AND stage = 'visual_intelligence' AND status = 'completed'",
        (pipeline_id,),
    ).fetchone()
    if scene_manifest_row is None or scene_manifest_row["artifact_id"] is None:
        result.preflight_passed = False
        result.preflight_errors.append("No scene manifest artifact recorded for this pipeline.")
        result.outcome = ProductionOutcome.FAILED
        result.reason = "Could not resolve scene manifest for preflight."
        mark_slot_production_failed(conn, slot.id, stage="preflight", error=result.reason)
        return
    scene_manifest_id = int(scene_manifest_row["artifact_id"])

    approved_render = get_approved_render(conn, scene_manifest_id, experiment_id=experiment.id)
    if approved_render is None:
        result.preflight_passed = False
        result.preflight_errors.append(
            "No approved render found for this scene manifest/experiment."
        )
        result.outcome = ProductionOutcome.FAILED
        result.reason = "Preflight found no approved render."
        mark_slot_production_failed(conn, slot.id, stage="preflight", error=result.reason)
        return

    try:
        validate_approved_render_for_publishing(approved_render)
        result.preflight_passed = True
    except PublishingValidationError as exc:
        result.preflight_passed = False
        result.preflight_errors.append(str(exc))
        result.outcome = ProductionOutcome.FAILED
        result.reason = f"Preflight failed: {exc}"
        mark_slot_production_failed(conn, slot.id, stage="preflight", error=str(exc))
        return

    # ── Visual quality floor (Phase 18E) ────────────────────────────────────
    # A render that FFmpeg produced successfully and that carries valid
    # metadata can still be a wall of typeset narration. This gate is the one
    # that refuses to let that reach autonomous publishing, and it fails
    # honestly rather than silently: the slot is marked failed with the actual
    # measurements in the error, and no publishing plan is created at all.
    visual_ok, visual_errors = _check_visual_quality(
        conn, approved_render=approved_render, experiment=experiment, result=result
    )
    if not visual_ok:
        result.preflight_passed = False
        result.preflight_errors.extend(visual_errors)
        result.outcome = ProductionOutcome.FAILED
        result.reason = "Visual quality preflight blocked the render: " + "; ".join(visual_errors)
        mark_slot_production_failed(
            conn, slot.id, stage="visual_quality_preflight", error=result.reason
        )
        return

    metadata = PublishingMetadataDraft(
        title=topic.title[:100],
        description=(
            f"Autonomously produced for experiment {experiment.id}. "
            f"Hypothesis: {experiment.hypothesis}"
        )[:5000],
        tags=[],
        visibility="private",
    )
    schedule = PublishingScheduleDraft(
        schedule_type="scheduled",
        scheduled_at=slot.scheduled_for_utc,
        timezone=slot.timezone,
    )
    input_hash = hashlib.sha256(
        f"{approved_render.render_manifest_id}:{topic.id}:{experiment.id}".encode()
    ).hexdigest()[:32]

    existing_plan_row = conn.execute(
        "SELECT id FROM publishing_plans WHERE input_hash = ?",
        (input_hash,),
    ).fetchone()
    if existing_plan_row is not None:
        plan_id = existing_plan_row["id"]
    else:
        plan = create_publishing_plan(
            conn,
            render_manifest_id=approved_render.render_manifest_id,
            render_job_id=approved_render.render_job_id,
            topic_id=topic.id,
            production_plan_id=approved_render.plan_id,
            script_id=approved_render.script_id,
            scene_manifest_id=approved_render.scene_manifest_id,
            narration_run_id=approved_render.narration_run_id,
            caption_run_id=approved_render.caption_run_id,
            experiment_id=experiment.id,
            input_hash=input_hash,
            provider="youtube",
            provider_version=PUBLISHING_ENGINE_VERSION,
            metadata=metadata,
            schedule=schedule,
        )
        conn.commit()
        plan_id = plan.id

    from app.intelligence.experiments.repository import bind_experiment_to_production_plan

    bind_experiment_to_production_plan(conn, experiment.id, approved_render.plan_id, actor=actor)
    conn.commit()

    result.publishing_plan_id = plan_id
    mark_slot_production_ready(conn, slot.id, publishing_plan_id=plan_id)
    result.outcome = ProductionOutcome.READY
    result.reason = (
        f"Rendered, validated, and drafted publishing plan {plan_id} "
        "(still private, not scheduled for upload)."
    )
