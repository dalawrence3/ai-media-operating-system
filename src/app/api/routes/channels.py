"""Channel routes — list, create, detail, accounts, strategy, policy."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from app.api.deps import get_actor, get_app_service, get_db
from app.application.commands import CreateChannelCommand, CreatePlatformAccountCommand
from app.application.queries import GetChannelSummaryQuery
from app.application.services import ApplicationService
from app.control_plane import services as cp

router = APIRouter(prefix="/workspaces/{workspace_id}/channels", tags=["channels"])


@router.get("")
def list_channels(
    workspace_id: str,
    db: Any = Depends(get_db),
) -> list[dict[str, Any]]:
    channels = cp.list_channels(db, workspace_id)
    return [c.model_dump() for c in channels]


@router.post("")
def create_channel(
    workspace_id: str,
    body: dict[str, Any] = Body(...),
    actor: str = Depends(get_actor),
    svc: ApplicationService = Depends(get_app_service),
) -> dict[str, Any]:
    try:
        ch = svc.create_channel(
            CreateChannelCommand(
                workspace_id=workspace_id,
                name=body["name"],
                slug=body["slug"],
                actor=actor,
                description=body.get("description"),
            )
        )
        return ch.model_dump()
    except PermissionError:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{channel_id}")
def get_channel_summary(
    workspace_id: str,
    channel_id: str,
    svc: ApplicationService = Depends(get_app_service),
) -> dict[str, Any]:
    try:
        view = svc.get_channel_summary(
            GetChannelSummaryQuery(workspace_id=workspace_id, channel_id=channel_id)
        )
        return view.model_dump()
    except PermissionError:
        raise
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{channel_id}/accounts")
def list_channel_accounts(
    workspace_id: str,
    channel_id: str,
    db: Any = Depends(get_db),
) -> list[dict[str, Any]]:
    accounts = cp.list_platform_accounts(db, channel_id)
    return [a.model_dump() for a in accounts]


@router.post("/{channel_id}/accounts")
def create_platform_account(
    workspace_id: str,
    channel_id: str,
    body: dict[str, Any] = Body(...),
    actor: str = Depends(get_actor),
    svc: ApplicationService = Depends(get_app_service),
) -> dict[str, Any]:
    try:
        acct = svc.create_platform_account(
            CreatePlatformAccountCommand(
                workspace_id=workspace_id,
                channel_id=channel_id,
                platform_id=body["platform_id"],
                external_account_id=body["external_account_id"],
                display_name=body["display_name"],
                actor=actor,
            )
        )
        return acct.model_dump()
    except PermissionError:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{channel_id}/strategy")
def get_channel_strategy(
    workspace_id: str,
    channel_id: str,
    db: Any = Depends(get_db),
) -> dict[str, Any]:
    """Active strategy profile plus its computed effective state.

    'effective' reflects the channel's ACTUAL first-party evidence maturity
    right now (via channel_performance_baselines) — it is never taken from
    the stored config, so the UI/planner never claim a maturity transition
    that hasn't really happened. Absent that data (the common case today),
    effective.effective_regime is honestly 'bootstrap'.
    """
    from app.intelligence.experiments.strategy_policy import (
        compute_effective_strategy_state,
        validate_strategy_config,
    )

    profile = cp.get_channel_strategy(db, channel_id)
    if profile is None:
        return {
            "status": "unavailable",
            "message": "No active strategy profile assigned",
            "profile": None,
            "effective": None,
        }

    config = profile.config
    errors = validate_strategy_config(config)
    effective = None if errors else compute_effective_strategy_state(db, channel_id, config)
    return {
        "status": "ok",
        "profile": profile.model_dump(),
        "effective": effective,
        "config_errors": errors or None,
    }


@router.get("/{channel_id}/strategy/history")
def list_channel_strategy_history(
    workspace_id: str,
    channel_id: str,
    db: Any = Depends(get_db),
) -> list[dict[str, Any]]:
    profiles = cp.list_channel_strategy_history(db, channel_id)
    return [p.model_dump() for p in profiles]


@router.post("/{channel_id}/strategy")
def create_channel_strategy_version(
    workspace_id: str,
    channel_id: str,
    body: dict[str, Any] = Body(...),
    actor: str = Depends(get_actor),
    db: Any = Depends(get_db),
) -> dict[str, Any]:
    """Create the next strategy profile version for this channel.

    Validated before persisting. Never overwrites a prior version — the
    repository layer only flips the previous row's is_active flag; version
    history is append-only, always readable via GET .../strategy/history.
    """
    from app.intelligence.experiments.strategy_policy import validate_strategy_config

    config = body.get("config")
    if not isinstance(config, dict):
        raise HTTPException(status_code=422, detail="Request body must include a 'config' object")

    errors = validate_strategy_config(config)
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})

    profile = cp.create_channel_strategy_version(db, channel_id, config, actor)
    db.commit()
    return profile.model_dump()


@router.get("/{channel_id}/policy")
def get_effective_policy(
    workspace_id: str,
    channel_id: str,
    db: Any = Depends(get_db),
) -> dict[str, Any]:
    level = cp.get_effective_policy(db, workspace_id, channel_id=channel_id)
    return {"effective_automation_level": level}


@router.get("/{channel_id}/automation-policy")
def get_channel_automation_policy(
    workspace_id: str,
    channel_id: str,
    db: Any = Depends(get_db),
) -> dict[str, Any]:
    """The channel's decision-automation configuration plus its current
    publishing-slot queue (Phase 18A). Distinct from /readiness: this is
    the operator-configurable policy and its live queue state, not a
    read-only checklist."""
    from app.intelligence.autonomy.repository import get_autonomy_policy, list_active_slots

    policy = get_autonomy_policy(db, channel_id)
    slots = list_active_slots(db, channel_id)
    return {
        "policy": policy.model_dump(mode="json") if policy else None,
        "active_slots": [s.model_dump(mode="json") for s in slots],
    }


@router.put("/{channel_id}/automation-policy")
def update_channel_automation_policy(
    workspace_id: str,
    channel_id: str,
    body: dict[str, Any] = Body(...),
    actor: str = Depends(get_actor),
    db: Any = Depends(get_db),
) -> dict[str, Any]:
    """Update (or create) a channel's decision-automation policy.

    Partial update: only keys present in the body are changed. Enabling
    decision_automation without a timezone is rejected with a clear 422 —
    the schema's own CHECK constraint is the last-resort guarantee, this is
    the friendly error path. This endpoint only toggles DECISION automation;
    it has no concept of public-publishing authorization at all.
    """
    from app.intelligence.autonomy.repository import (
        InvalidTimezoneError,
        get_autonomy_policy,
        upsert_autonomy_policy,
    )

    allowed_keys = {
        "decision_automation_enabled",
        "production_automation_enabled",
        "cadence_type",
        "cadence_interval_days",
        "cadence_cron",
        "preferred_local_hour",
        "timezone",
        "clear_timezone",
        "queue_target",
        "market_refresh_max_age_hours",
        "semantic_fit_max_evaluations_per_run",
    }
    unknown = set(body.keys()) - allowed_keys
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown fields: {sorted(unknown)}")

    existing = get_autonomy_policy(db, channel_id)
    wants_enabled = body.get(
        "decision_automation_enabled",
        existing.decision_automation_enabled if existing else False,
    )
    resulting_timezone = (
        None
        if body.get("clear_timezone")
        else body.get("timezone", existing.timezone if existing else None)
    )
    if wants_enabled and not resulting_timezone:
        raise HTTPException(
            status_code=422,
            detail=(
                "decision_automation_enabled requires a timezone to be set "
                "(e.g. 'America/New_York')."
            ),
        )

    try:
        policy = upsert_autonomy_policy(
            db,
            channel_id=channel_id,
            workspace_id=workspace_id,
            actor=actor,
            **body,
        )
    except InvalidTimezoneError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return policy.model_dump(mode="json")


@router.get("/{channel_id}/publishing-slots")
def list_channel_publishing_slots(
    workspace_id: str,
    channel_id: str,
    db: Any = Depends(get_db),
) -> list[dict[str, Any]]:
    """Every slot for this channel, newest first — terminal ones included.

    Deliberately not list_active_slots: that answers "what still occupies
    the queue", and a released or missed slot must stay visible here as the
    historical record of what the channel actually did.
    """
    from app.intelligence.autonomy.repository import list_slots_for_channel

    return [s.model_dump(mode="json") for s in list_slots_for_channel(db, channel_id)]


def _refuse_in_test_mode(operation: str) -> None:
    """Reject an operation with live consequences when running under test mode.

    409 rather than 403: the request is well-formed and the caller is
    authenticated — it conflicts with the state of the runtime it reached.
    """
    from app.core.runtime_mode import RuntimeIsolationError, assert_live_effect_allowed

    try:
        assert_live_effect_allowed(operation)
    except RuntimeIsolationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{channel_id}/publishing-authorization")
def get_channel_publishing_authorization_state(
    workspace_id: str,
    channel_id: str,
    db: Any = Depends(get_db),
) -> dict[str, Any]:
    """The channel's public-publishing authorization and its live evaluation.

    Returns both the persisted authorization row and the full four-layer
    decision (global gates, channel authorization, rate limit, account
    health) so the UI can show exactly what is and is not permitting
    autonomous publishing right now, rather than a single opaque boolean.
    """
    from dataclasses import asdict

    from app.publishing.authorization import (
        evaluate_publishing_authorization,
        get_channel_publishing_authorization,
    )

    auth = get_channel_publishing_authorization(db, channel_id)
    decision = evaluate_publishing_authorization(db, channel_id=channel_id)
    payload = asdict(decision)
    payload["blocked_by"] = [r.value for r in decision.blocked_by]

    return {
        "authorization": asdict(auth) if auth else None,
        "decision": payload,
    }


@router.put("/{channel_id}/publishing-authorization")
def set_channel_publishing_authorization(
    workspace_id: str,
    channel_id: str,
    body: dict[str, Any] = Body(...),
    actor: str = Depends(get_actor),
    db: Any = Depends(get_db),
) -> dict[str, Any]:
    """Grant or revoke a channel's authorization for unattended public publishing.

    Deliberately NOT part of the automation-policy endpoint: authorizing a
    channel to publish publicly with no per-video review must never be
    something that happens as a side effect of editing a cadence or a queue
    target. Granting requires an explicit `confirm: true` in the body, so a
    malformed or partial request cannot authorize anything.

    Revocation has no such requirement — turning publishing off must always
    be as easy as possible.
    """
    from dataclasses import asdict

    from app.publishing.authorization import (
        grant_channel_publishing_authorization,
        revoke_channel_publishing_authorization,
        update_publishing_limits,
    )

    # Phase 18E — belt-and-braces over database isolation. A test runtime
    # already has its own database, so this endpoint can only reach test state;
    # refusing outright means a test never even exercises the mutation path
    # that once ran against the live channel.
    _refuse_in_test_mode("publishing_authorization_grant")

    allowed_keys = {
        "authorized",
        "confirm",
        "reason",
        "max_publications_per_24h",
        "missed_slot_grace_minutes",
    }
    unknown = set(body.keys()) - allowed_keys
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown fields: {sorted(unknown)}")

    authorized = body.get("authorized")
    if authorized is None:
        # No authorization change requested — this is a limits-only update.
        try:
            auth = update_publishing_limits(
                db,
                channel_id=channel_id,
                workspace_id=workspace_id,
                actor=actor,
                max_publications_per_24h=body.get("max_publications_per_24h"),
                missed_slot_grace_minutes=body.get("missed_slot_grace_minutes"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return asdict(auth)

    if authorized:
        if body.get("confirm") is not True:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Granting public-publishing authorization requires an explicit "
                    '"confirm": true. This authorizes the system to publish future '
                    "videos publicly on this channel without per-video approval."
                ),
            )
        try:
            auth = grant_channel_publishing_authorization(
                db,
                channel_id=channel_id,
                workspace_id=workspace_id,
                actor=actor,
                max_publications_per_24h=body.get("max_publications_per_24h"),
                missed_slot_grace_minutes=body.get("missed_slot_grace_minutes"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return asdict(auth)

    auth = revoke_channel_publishing_authorization(
        db,
        channel_id=channel_id,
        workspace_id=workspace_id,
        actor=actor,
        reason=body.get("reason"),
    )
    return asdict(auth)


@router.get("/{channel_id}/readiness")
def get_channel_autonomy_readiness(
    workspace_id: str,
    channel_id: str,
    db: Any = Depends(get_db),
) -> dict[str, Any]:
    """Autonomy readiness projection (Phase 17G) — read-only, spends no LLM/YouTube calls.

    Distinguishes READY FOR DECISION AUTOMATION (the market intelligence /
    eligibility / strategy pipeline is operational) from AUTHORIZED FOR
    PUBLIC PUBLISHING (both live-publishing gates are actually enabled) —
    these are never the same thing, and the latter is false by default.
    """
    from app.application.autonomy_readiness import get_autonomy_readiness

    view = get_autonomy_readiness(db, workspace_id, channel_id)
    return view.model_dump()


@router.get("/{channel_id}/cross-publication")
def get_cross_publication_learning(
    workspace_id: str,
    channel_id: str,
    db: Any = Depends(get_db),
) -> dict[str, Any]:
    """Cross-publication learning — channel-scoped performance baselines and
    creative-factor observations (Phase 17D).

    Read-only. `channel_id` here is the control-plane UUID (cp_channels.id) —
    channel_performance_baselines and feature_performance_observations are
    already keyed by this same string, so no intelligence-domain bridge
    lookup is needed (unlike the /market/* routes).

    An empty result is a real, expected state — cross-publication learning
    (`ace learn cross-pub --channel <id>`) has not been run for this channel
    yet, most commonly because too few publications have both a content
    feature snapshot and observed analytics. This endpoint never fabricates
    baselines or observations to fill that gap.
    """
    from app.learning.cross_publication import get_channel_baselines, get_feature_observations

    baselines = get_channel_baselines(db, channel_id=channel_id)
    observations = get_feature_observations(db, channel_id=channel_id)
    return {
        "channel_id": channel_id,
        "baselines": [b.model_dump() for b in baselines],
        "feature_observations": [o.model_dump() for o in observations],
    }
