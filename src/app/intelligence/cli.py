"""Typer sub-apps for channel strategy and discovery commands (Phase 3)."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from app.intelligence.models import (
    AdapterName,
    AudienceIntent,
    BrandVoice,
    MaturityStage,
    MonetizationStatus,
    OperatingMode,
    Platform,
    PrimaryFormat,
)

channels_app = typer.Typer(
    help="Manage channels and channel strategy.",
    no_args_is_help=True,
)

discover_app = typer.Typer(
    help="Run and inspect content opportunity discovery.",
    no_args_is_help=True,
)


def _get_db():
    from app.core.config import get_config
    from app.core.database import open_db
    from app.core.logging import configure_logging

    cfg = get_config()
    configure_logging(cfg.log_level)
    return open_db(cfg.db_path)


# ---------------------------------------------------------------------------
# channels add
# ---------------------------------------------------------------------------


@channels_app.command("add")
def channels_add(
    name: Annotated[str, typer.Option("--name", "-n", help="Channel display name.")],
    niche: Annotated[str, typer.Option("--niche", help="Primary content niche.")],
    platform: Annotated[Platform, typer.Option("--platform", help="Platform.")] = Platform.youtube,
    stage: Annotated[
        MaturityStage, typer.Option("--stage", help="Channel maturity stage.")
    ] = MaturityStage.validation,
    audience: Annotated[str, typer.Option("--audience", help="Audience description.")] = "",
    intent: Annotated[
        AudienceIntent, typer.Option("--intent", help="Audience intent.")
    ] = AudienceIntent.educational,
    fmt: Annotated[
        PrimaryFormat, typer.Option("--format", help="Primary content format.")
    ] = PrimaryFormat.short,
    cadence: Annotated[int, typer.Option("--cadence", help="Target posts per week (1–21).")] = 3,
    voice: Annotated[
        BrandVoice, typer.Option("--voice", help="Brand voice.")
    ] = BrandVoice.conversational,
    operator: Annotated[str, typer.Option("--operator", help="Operator name for audit log.")] = "",
) -> None:
    """Create a channel with initial profile, strategy, and capacity policy."""
    from app.intelligence.repository import create_channel_full

    conn = _get_db()
    channel, profile, strategy, capacity = create_channel_full(
        conn,
        channel_name=name,
        primary_niche=niche,
        platform=platform,
        maturity_stage=stage,
        audience_description=audience,
        audience_intent=intent,
        primary_format=fmt,
        posting_cadence_per_week=cadence,
        brand_voice=voice,
        operator=operator,
    )
    typer.echo(f"Created channel id={channel.id} name={channel.channel_name!r}")
    typer.echo(f"  Platform:        {channel.platform.value}")
    typer.echo(f"  Maturity stage:  {channel.current_maturity_stage.value}")
    typer.echo(f"  Operating mode:  {channel.operating_mode.value}")
    typer.echo(f"  Profile version: {profile.version}  (id={profile.id})")
    typer.echo(f"  Strategy version: {strategy.version}  (id={strategy.id})")
    typer.echo(f"  Primary niche:   {profile.primary_niche!r}")
    typer.echo(f"  Format:          {profile.primary_format.value}")
    typer.echo(f"  Cadence:         {profile.posting_cadence_per_week}/week")


# ---------------------------------------------------------------------------
# channels list
# ---------------------------------------------------------------------------


@channels_app.command("list")
def channels_list() -> None:
    """List all channels."""
    from app.intelligence.repository import list_channels

    conn = _get_db()
    items = list_channels(conn)
    if not items:
        typer.echo("No channels found.")
        return
    for ch in items:
        typer.echo(
            f"[{ch.id}] {ch.channel_name!r}"
            f"  platform={ch.platform.value}"
            f"  mode={ch.operating_mode.value}"
            f"  stage={ch.current_maturity_stage.value}"
        )


# ---------------------------------------------------------------------------
# channels show
# ---------------------------------------------------------------------------


@channels_app.command("show")
def channels_show(
    channel_id: Annotated[int, typer.Argument(help="Channel ID.")],
) -> None:
    """Show a channel's active profile, strategy, and capacity policy."""
    from app.intelligence.repository import (
        get_active_monetization_strategy,
        get_active_profile_version,
        get_capacity_policy,
        get_channel,
    )

    conn = _get_db()
    channel = get_channel(conn, channel_id)
    if channel is None:
        typer.echo(f"Channel {channel_id} not found.", err=True)
        raise typer.Exit(1)

    typer.echo(f"Channel [{channel.id}] {channel.channel_name!r}")
    typer.echo(f"  Platform:        {channel.platform.value}")
    typer.echo(f"  Operating mode:  {channel.operating_mode.value}")
    typer.echo(f"  Maturity stage:  {channel.current_maturity_stage.value}")
    typer.echo(f"  Created:         {channel.created_at}")

    profile = get_active_profile_version(conn, channel_id)
    if profile:
        typer.echo("")
        typer.echo(f"Active profile (v{profile.version}, id={profile.id}):")
        typer.echo(f"  Primary niche:    {profile.primary_niche!r}")
        typer.echo(f"  Format:           {profile.primary_format.value}")
        typer.echo(f"  Cadence:          {profile.posting_cadence_per_week}/week")
        typer.echo(f"  Audience intent:  {profile.audience_intent.value}")
        typer.echo(f"  Brand voice:      {profile.brand_voice.value}")
        typer.echo(f"  Content style:    {profile.content_style.value}")
        pt = profile.portfolio_targets
        typer.echo(
            f"  Portfolio:        evergreen={pt.evergreen:.0%}"
            f"  trending={pt.trending:.0%}"
            f"  seasonal={pt.seasonal:.0%}"
            f"  experimental={pt.experimental:.0%}"
        )
        typer.echo(f"  Min score:        {profile.min_opportunity_score}")
        typer.echo(f"  Dedup threshold:  {profile.duplicate_similarity_threshold}")
        typer.echo(f"  Active from:      {profile.active_from}")
    else:
        typer.echo("  (no active profile version)")

    strategy = get_active_monetization_strategy(conn, channel_id)
    if strategy:
        typer.echo("")
        typer.echo(f"Active strategy (v{strategy.version}, id={strategy.id}):")
        typer.echo(f"  Monetization:  {strategy.monetization_status.value}")
        for obj, weight in sorted(strategy.objective_weights.items(), key=lambda x: -x[1]):
            typer.echo(f"    {obj}: {weight:.0%}")
    else:
        typer.echo("  (no active strategy)")

    capacity = get_capacity_policy(conn, channel_id)
    if capacity:
        typer.echo("")
        typer.echo("Capacity policy (ceilings — not quotas):")
        typer.echo(f"  Long-form slots/week:    {capacity.long_form_slots_per_week}")
        typer.echo(f"  Short slots/week:        {capacity.short_slots_per_week}")
        typer.echo(f"  Package slots/week:      {capacity.content_package_slots_per_week}")
        typer.echo(f"  Max concurrent:          {capacity.max_concurrent_productions}")
        typer.echo(f"  Review hours/week:       {capacity.review_hours_per_week}")
        typer.echo(f"  Daily budget:            ${capacity.daily_budget_usd:.2f}")
        typer.echo(f"  Monthly budget:          ${capacity.monthly_budget_usd:.2f}")


# ---------------------------------------------------------------------------
# channels versions
# ---------------------------------------------------------------------------


@channels_app.command("versions")
def channels_versions(
    channel_id: Annotated[int, typer.Argument(help="Channel ID.")],
) -> None:
    """Show profile and strategy version history for a channel."""
    from app.intelligence.repository import (
        get_channel,
        list_monetization_strategies,
        list_profile_versions,
    )

    conn = _get_db()
    channel = get_channel(conn, channel_id)
    if channel is None:
        typer.echo(f"Channel {channel_id} not found.", err=True)
        raise typer.Exit(1)

    typer.echo(f"Channel [{channel.id}] {channel.channel_name!r} — version history")

    profiles = list_profile_versions(conn, channel_id)
    typer.echo(f"\nProfile versions ({len(profiles)}):")
    for p in profiles:
        typer.echo(
            f"  v{p.version} (id={p.id})"
            f"  niche={p.primary_niche!r}"
            f"  stage={p.maturity_stage.value}"
            f"  [{p.status.value}]"
        )

    strategies = list_monetization_strategies(conn, channel_id)
    typer.echo(f"\nStrategy versions ({len(strategies)}):")
    for s in strategies:
        typer.echo(f"  v{s.version} (id={s.id})  {s.monetization_status.value}  [{s.status.value}]")


# ---------------------------------------------------------------------------
# channels new-version
# ---------------------------------------------------------------------------


@channels_app.command("new-version")
def channels_new_version(
    channel_id: Annotated[int, typer.Argument(help="Channel ID.")],
    niche: Annotated[
        str | None, typer.Option("--niche", help="New primary niche (carry forward if omitted).")
    ] = None,
    stage: Annotated[
        MaturityStage | None,
        typer.Option("--stage", help="New maturity stage (carry forward if omitted)."),
    ] = None,
    audience: Annotated[
        str | None, typer.Option("--audience", help="New audience description.")
    ] = None,
    intent: Annotated[
        AudienceIntent | None, typer.Option("--intent", help="New audience intent.")
    ] = None,
    fmt: Annotated[
        PrimaryFormat | None, typer.Option("--format", help="New primary format.")
    ] = None,
    cadence: Annotated[int | None, typer.Option("--cadence", help="New posts per week.")] = None,
    voice: Annotated[BrandVoice | None, typer.Option("--voice", help="New brand voice.")] = None,
    operator: Annotated[str, typer.Option("--operator", help="Operator name for audit log.")] = "",
) -> None:
    """Create a DRAFT profile version (does not replace the active version until activated)."""
    from app.intelligence.repository import create_new_profile_version

    conn = _get_db()
    try:
        draft = create_new_profile_version(
            conn,
            channel_id,
            primary_niche=niche,
            maturity_stage=stage,
            audience_description=audience,
            audience_intent=intent,
            primary_format=fmt,
            posting_cadence_per_week=cadence,
            brand_voice=voice,
            operator=operator,
        )
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None

    typer.echo(
        f"Channel {channel_id}: draft profile v{draft.version} (id={draft.id}) created."
        f" Not yet active."
    )
    typer.echo(f"  Niche:   {draft.primary_niche!r}")
    typer.echo(f"  Stage:   {draft.maturity_stage.value}")
    typer.echo(f"  Format:  {draft.primary_format.value}")
    typer.echo(f"  Cadence: {draft.posting_cadence_per_week}/week")
    typer.echo(f"  Run 'ace channels activate-version {channel_id} {draft.version}' to activate.")


# ---------------------------------------------------------------------------
# channels activate-version
# ---------------------------------------------------------------------------


@channels_app.command("activate-version")
def channels_activate_version(
    channel_id: Annotated[int, typer.Argument(help="Channel ID.")],
    version: Annotated[int, typer.Argument(help="Profile version number to activate.")],
    operator: Annotated[str, typer.Option("--operator", help="Operator name for audit log.")] = "",
    reason: Annotated[
        str, typer.Option("--reason", help="Reason for activation (required for audit).")
    ] = "",
) -> None:
    """Activate a draft profile version, superseding the current active one."""
    from app.intelligence.repository import (
        activate_profile_version,
        get_channel,
        list_profile_versions,
    )

    conn = _get_db()
    channel = get_channel(conn, channel_id)
    if channel is None:
        typer.echo(f"Channel {channel_id} not found.", err=True)
        raise typer.Exit(1)

    profiles = list_profile_versions(conn, channel_id)
    target = next((p for p in profiles if p.version == version), None)
    if target is None:
        typer.echo(f"Profile version {version} not found for channel {channel_id}.", err=True)
        raise typer.Exit(1)

    try:
        activated = activate_profile_version(
            conn,
            target.id,
            operator=operator,
            reason=reason,  # type: ignore[arg-type]
        )
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None

    typer.echo(f"Channel {channel_id}: profile v{activated.version} (id={activated.id}) activated.")
    typer.echo(f"  Niche:        {activated.primary_niche!r}")
    typer.echo(f"  Stage:        {activated.maturity_stage.value}")
    typer.echo(f"  Activated by: {activated.activated_by or '(unset)'}")
    typer.echo(f"  Activated at: {activated.activated_at}")


# ---------------------------------------------------------------------------
# channels new-strategy
# ---------------------------------------------------------------------------


@channels_app.command("new-strategy")
def channels_new_strategy(
    channel_id: Annotated[int, typer.Argument(help="Channel ID.")],
    weights: Annotated[
        str,
        typer.Option(
            "--weights",
            help="JSON object of objective weights, e.g. '{\"qualified_subscriber_growth\": 1.0}'",
        ),
    ],
    status: Annotated[
        MonetizationStatus,
        typer.Option("--status", help="Monetization status (pre or active)."),
    ] = MonetizationStatus.pre,
    description: Annotated[
        str, typer.Option("--description", help="Human-readable description of this strategy.")
    ] = "",
    operator: Annotated[str, typer.Option("--operator", help="Operator name for audit log.")] = "",
) -> None:
    """Create a DRAFT strategy version (does not replace the active strategy until activated)."""
    from app.intelligence.repository import create_new_strategy_version

    try:
        parsed_weights: dict[str, float] = json.loads(weights)
    except json.JSONDecodeError as exc:
        typer.echo(f"Invalid JSON for --weights: {exc}", err=True)
        raise typer.Exit(1) from None

    conn = _get_db()
    try:
        draft = create_new_strategy_version(
            conn,
            channel_id,
            objective_weights=parsed_weights,
            monetization_status=status,
            description=description,
            operator=operator,
        )
    except (ValueError, Exception) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None

    typer.echo(
        f"Channel {channel_id}: draft strategy v{draft.version} (id={draft.id}) created."
        f" Not yet active."
    )
    typer.echo(f"  Monetization status: {draft.monetization_status.value}")
    for obj, weight in sorted(draft.objective_weights.items(), key=lambda x: -x[1]):
        typer.echo(f"    {obj}: {weight:.0%}")
    typer.echo(f"  Run 'ace channels activate-strategy {channel_id} {draft.version}' to activate.")


# ---------------------------------------------------------------------------
# channels activate-strategy
# ---------------------------------------------------------------------------


@channels_app.command("activate-strategy")
def channels_activate_strategy(
    channel_id: Annotated[int, typer.Argument(help="Channel ID.")],
    version: Annotated[int, typer.Argument(help="Strategy version number to activate.")],
    operator: Annotated[str, typer.Option("--operator", help="Operator name for audit log.")] = "",
    reason: Annotated[
        str, typer.Option("--reason", help="Reason for activation (required for audit).")
    ] = "",
) -> None:
    """Activate a draft strategy version, superseding the current active one."""
    from app.intelligence.repository import (
        activate_strategy_version,
        get_channel,
        list_monetization_strategies,
    )

    conn = _get_db()
    channel = get_channel(conn, channel_id)
    if channel is None:
        typer.echo(f"Channel {channel_id} not found.", err=True)
        raise typer.Exit(1)

    strategies = list_monetization_strategies(conn, channel_id)
    target = next((s for s in strategies if s.version == version), None)
    if target is None:
        typer.echo(f"Strategy version {version} not found for channel {channel_id}.", err=True)
        raise typer.Exit(1)

    try:
        activated = activate_strategy_version(
            conn,
            target.id,
            operator=operator,
            reason=reason,  # type: ignore[arg-type]
        )
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None

    typer.echo(
        f"Channel {channel_id}: strategy v{activated.version} (id={activated.id}) activated."
    )
    typer.echo(f"  Monetization status: {activated.monetization_status.value}")
    typer.echo(f"  Activated by:        {activated.activated_by or '(unset)'}")
    typer.echo(f"  Activated at:        {activated.activated_at}")


# ---------------------------------------------------------------------------
# channels set-mode
# ---------------------------------------------------------------------------


_PHASE3_MODE_NOTE = (
    "Only 'manual' mode is supported in Phase 3. "
    "'supervised' and 'autonomous' are reserved for future phases."
)


@channels_app.command("set-mode")
def channels_set_mode(
    channel_id: Annotated[int, typer.Argument(help="Channel ID.")],
    mode: Annotated[OperatingMode, typer.Argument(help="Target operating mode.")],
    reason: Annotated[
        str, typer.Option("--reason", help="Reason for the mode change (required).")
    ] = "",
    operator: Annotated[str, typer.Option("--operator", help="Operator name for audit log.")] = "",
) -> None:
    """Set the channel operating mode. Phase 3 supports 'manual' only."""
    if mode != OperatingMode.manual:
        typer.echo(
            f"Cannot set mode to '{mode.value}'. {_PHASE3_MODE_NOTE}",
            err=True,
        )
        raise typer.Exit(1)

    from app.intelligence.repository import get_channel, set_channel_operating_mode

    conn = _get_db()
    channel = get_channel(conn, channel_id)
    if channel is None:
        typer.echo(f"Channel {channel_id} not found.", err=True)
        raise typer.Exit(1)

    if channel.operating_mode == mode:
        typer.echo(f"Channel {channel_id} is already in '{mode.value}' mode.")
        return

    set_channel_operating_mode(conn, channel_id, mode, operator=operator, reason=reason)
    typer.echo(f"Channel {channel_id}: mode {channel.operating_mode.value!r} → {mode.value!r}")


# ---------------------------------------------------------------------------
# channels capacity
# ---------------------------------------------------------------------------


@channels_app.command("capacity")
def channels_capacity(
    channel_id: Annotated[int, typer.Argument(help="Channel ID.")],
) -> None:
    """Show the capacity policy for a channel."""
    from app.intelligence.repository import get_capacity_policy, get_channel

    conn = _get_db()
    channel = get_channel(conn, channel_id)
    if channel is None:
        typer.echo(f"Channel {channel_id} not found.", err=True)
        raise typer.Exit(1)

    policy = get_capacity_policy(conn, channel_id)
    if policy is None:
        typer.echo(f"No capacity policy found for channel {channel_id}.", err=True)
        raise typer.Exit(1)

    typer.echo(f"Capacity policy for channel [{channel_id}] {channel.channel_name!r}")
    typer.echo("  (All values are ceilings — not production quotas)")
    typer.echo(f"  Long-form slots/week:    {policy.long_form_slots_per_week}")
    typer.echo(f"  Short slots/week:        {policy.short_slots_per_week}")
    typer.echo(f"  Package slots/week:      {policy.content_package_slots_per_week}")
    typer.echo(f"  Max concurrent:          {policy.max_concurrent_productions}")
    typer.echo(f"  Trend reservation slots: {policy.trend_reservation_slots}")
    typer.echo(f"  Review hours/week:       {policy.review_hours_per_week}")
    typer.echo(f"    Per short:             {policy.review_hours_per_short}")
    typer.echo(f"    Per long-form:         {policy.review_hours_per_long_form}")
    typer.echo(f"    Per package:           {policy.review_hours_per_package}")
    typer.echo(f"  Daily budget:            ${policy.daily_budget_usd:.2f}")
    typer.echo(f"  Per-video budget:        ${policy.per_video_budget_usd:.2f}")
    typer.echo(f"  Monthly budget:          ${policy.monthly_budget_usd:.2f}")


# ---------------------------------------------------------------------------
# channels set-capacity
# ---------------------------------------------------------------------------


@channels_app.command("set-capacity")
def channels_set_capacity(
    channel_id: Annotated[int, typer.Argument(help="Channel ID.")],
    long_form_slots: Annotated[
        int | None, typer.Option("--long-form-slots", help="Long-form slots/week ceiling.")
    ] = None,
    short_slots: Annotated[
        int | None, typer.Option("--short-slots", help="Short slots/week ceiling.")
    ] = None,
    package_slots: Annotated[
        int | None, typer.Option("--package-slots", help="Content-package slots/week ceiling.")
    ] = None,
    concurrent: Annotated[
        int | None, typer.Option("--concurrent", help="Max concurrent productions ceiling.")
    ] = None,
    review_hours: Annotated[
        float | None, typer.Option("--review-hours", help="Review hours/week ceiling.")
    ] = None,
    daily_budget: Annotated[
        float | None, typer.Option("--daily-budget", help="Daily budget ceiling (USD).")
    ] = None,
    monthly_budget: Annotated[
        float | None, typer.Option("--monthly-budget", help="Monthly budget ceiling (USD).")
    ] = None,
) -> None:
    """Update one or more capacity policy ceilings for a channel."""
    from app.intelligence.repository import (
        create_or_update_capacity_policy,
        get_capacity_policy,
        get_channel,
    )

    conn = _get_db()
    channel = get_channel(conn, channel_id)
    if channel is None:
        typer.echo(f"Channel {channel_id} not found.", err=True)
        raise typer.Exit(1)

    policy = get_capacity_policy(conn, channel_id)
    if policy is None:
        typer.echo(f"No capacity policy found for channel {channel_id}.", err=True)
        raise typer.Exit(1)

    if long_form_slots is not None:
        policy.long_form_slots_per_week = long_form_slots
    if short_slots is not None:
        policy.short_slots_per_week = short_slots
    if package_slots is not None:
        policy.content_package_slots_per_week = package_slots
    if concurrent is not None:
        policy.max_concurrent_productions = concurrent
    if review_hours is not None:
        policy.review_hours_per_week = review_hours
    if daily_budget is not None:
        policy.daily_budget_usd = daily_budget
    if monthly_budget is not None:
        policy.monthly_budget_usd = monthly_budget

    try:
        updated = create_or_update_capacity_policy(conn, policy)
        conn.commit()
    except Exception as exc:
        typer.echo(f"Error updating capacity policy: {exc}", err=True)
        raise typer.Exit(1) from None

    typer.echo(f"Capacity policy for channel {channel_id} updated.")
    typer.echo(f"  Long-form slots/week: {updated.long_form_slots_per_week}")
    typer.echo(f"  Short slots/week:     {updated.short_slots_per_week}")
    typer.echo(f"  Max concurrent:       {updated.max_concurrent_productions}")
    typer.echo(f"  Review hours/week:    {updated.review_hours_per_week}")
    typer.echo(f"  Daily budget:         ${updated.daily_budget_usd:.2f}")
    typer.echo(f"  Monthly budget:       ${updated.monthly_budget_usd:.2f}")


# ---------------------------------------------------------------------------
# discover run
# ---------------------------------------------------------------------------


@discover_app.command("run")
def discover_run(
    channel_id: Annotated[int, typer.Option("--channel", "-c", help="Channel ID.")],
    topic: Annotated[
        list[str] | None,
        typer.Option("--topic", "-t", help="Topic string (repeatable; ManualSignalAdapter)."),
    ] = None,
    adapter: Annotated[
        AdapterName,
        typer.Option("--adapter", help="Adapter to use."),
    ] = AdapterName.manual,
    youtube_query: Annotated[
        str | None,
        typer.Option("--youtube-query", help="Search query for YouTubeDataAPIAdapter."),
    ] = None,
    youtube_api_key: Annotated[
        str,
        typer.Option("--youtube-api-key", envvar="ACE_YOUTUBE_API_KEY", help="YouTube API key."),
    ] = "",
    operator: Annotated[
        str, typer.Option("--operator", help="Operator name for audit.")
    ] = "system",
) -> None:
    """Run a discovery cycle and persist all candidates."""
    from app.intelligence.discovery import run_discovery

    conn = _get_db()
    topics: list[str] = list(topic) if topic else []
    if adapter == AdapterName.youtube_data_api and youtube_query:
        topics = [youtube_query, *topics]

    if not topics:
        typer.echo("No topics supplied. Use --topic or --youtube-query.", err=True)
        raise typer.Exit(1)

    try:
        run = run_discovery(
            conn,
            channel_id,
            adapter,
            topics,
            operator=operator,
            youtube_api_key=youtube_api_key,
        )
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None

    typer.echo(f"Discovery run {run.id} [{run.adapter_name.value}] — {run.status.value}")
    typer.echo(f"  New opportunities: {run.new_opportunity_count}")
    typer.echo(f"  Deduplicated:      {run.dedup_count}")
    typer.echo(f"  Failed:            {run.failed_count}")
    typer.echo(f"  Quota units used:  {run.quota_units_consumed}")
    if run.error_message:
        typer.echo(f"  Error: {run.error_message}", err=True)


# ---------------------------------------------------------------------------
# discover list
# ---------------------------------------------------------------------------


@discover_app.command("list")
def discover_list(
    channel_id: Annotated[int, typer.Option("--channel", "-c", help="Channel ID.")],
    status: Annotated[
        str | None,
        typer.Option("--status", help="Filter by lifecycle state (new/under_review/…)."),
    ] = None,
) -> None:
    """List opportunities for a channel."""
    from app.intelligence.models import LifecycleState
    from app.intelligence.repository import get_channel, list_opportunities

    conn = _get_db()
    channel = get_channel(conn, channel_id)
    if channel is None:
        typer.echo(f"Channel {channel_id} not found.", err=True)
        raise typer.Exit(1)

    try:
        state = LifecycleState(status) if status else None
    except ValueError:
        typer.echo(f"Unknown lifecycle state: {status!r}", err=True)
        raise typer.Exit(1) from None

    opps = list_opportunities(conn, channel_id, state=state)
    if not opps:
        typer.echo("No opportunities found.")
        return
    for opp in opps:
        typer.echo(
            f"[{opp.id}] {opp.raw_topic!r}"
            f"  state={opp.current_lifecycle_state.value}"
            f"  format={opp.format_recommendation.value}"
            f"  created={opp.created_at}"
        )


# ---------------------------------------------------------------------------
# discover show
# ---------------------------------------------------------------------------


@discover_app.command("show")
def discover_show(
    opportunity_id: Annotated[int, typer.Argument(help="Opportunity ID.")],
) -> None:
    """Show full detail for an opportunity including observations and evidence."""
    from app.intelligence.repository import (
        get_opportunity,
        list_evidence,
        list_observations,
        list_state_events,
    )

    conn = _get_db()
    opp = get_opportunity(conn, opportunity_id)
    if opp is None:
        typer.echo(f"Opportunity {opportunity_id} not found.", err=True)
        raise typer.Exit(1)

    typer.echo(f"Opportunity [{opp.id}]")
    typer.echo(f"  Topic (raw):     {opp.raw_topic!r}")
    typer.echo(f"  Topic (normed):  {opp.normalized_topic!r}")
    typer.echo(f"  State:           {opp.current_lifecycle_state.value}")
    typer.echo(f"  Format:          {opp.format_recommendation.value}")
    typer.echo(f"  Strategic role:  {opp.strategic_role.value}")
    typer.echo(f"  Created:         {opp.created_at}")

    observations = list_observations(conn, opportunity_id)
    typer.echo(f"\nObservations ({len(observations)}):")
    for obs in observations:
        dedup_note = (
            f"  [dedup from {obs.candidate_topic!r}, similarity={obs.dedup_similarity_score:.2f}]"
            if obs.was_deduplicated
            else ""
        )
        typer.echo(
            f"  [{obs.id}] adapter={obs.adapter_name.value}"
            f"  collected={obs.collected_at}"
            f"  age={obs.signal_age_days}d"
            f"{dedup_note}"
        )
        for ev in list_evidence(conn, obs.id):
            val = (
                f"{ev.evidence_value} {ev.evidence_unit}"
                if ev.evidence_value is not None
                else ev.evidence_text
            )
            typer.echo(f"    {ev.evidence_type}: {val}  [{ev.source_label}]")

    events = list_state_events(conn, opportunity_id)
    typer.echo(f"\nState history ({len(events)}):")
    for evt in events:
        from_s = evt.from_state.value if evt.from_state else "—"
        typer.echo(f"  {from_s} → {evt.to_state.value}  actor={evt.actor}  {evt.created_at}")
