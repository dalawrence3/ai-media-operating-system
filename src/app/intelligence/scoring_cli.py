"""Typer sub-app for ace intelligence commands (M3.3 scoring)."""

from __future__ import annotations

from typing import Annotated

import typer

scoring_app = typer.Typer(
    help="Score opportunities and manage scoring policies.",
    no_args_is_help=True,
)

policy_app = typer.Typer(help="Manage scoring policies.", no_args_is_help=True)
scoring_app.add_typer(policy_app, name="policy")


def _get_db():
    from app.core.config import get_config
    from app.core.database import open_db
    from app.core.logging import configure_logging

    cfg = get_config()
    configure_logging(cfg.log_level)
    return open_db(cfg.db_path)


def _bar(value: float, width: int = 10) -> str:
    filled = round(value * width)
    return "█" * filled + "░" * (width - filled)


def _stars(value: float, width: int = 5) -> str:
    filled = round(value * width)
    return "★" * filled + "☆" * (width - filled)


# ---------------------------------------------------------------------------
# ace intelligence score
# ---------------------------------------------------------------------------


@scoring_app.command("score")
def intelligence_score(
    opportunity_id: Annotated[int, typer.Option("--opportunity", "-o", help="Opportunity ID.")],
    policy_id: Annotated[
        int | None,
        typer.Option("--policy", "-p", help="Scoring policy ID (uses channel default if omitted)."),
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", help="Re-score even if inputs are unchanged.")
    ] = False,
) -> None:
    """Score a single opportunity."""
    from app.intelligence.repository import (
        get_active_profile_version,
        get_default_scoring_policy,
        get_opportunity,
        get_scoring_policy,
    )
    from app.intelligence.scoring import score_opportunity

    conn = _get_db()
    opp = get_opportunity(conn, opportunity_id)
    if opp is None:
        typer.echo(f"Opportunity {opportunity_id} not found.", err=True)
        raise typer.Exit(1)

    if policy_id is not None:
        policy = get_scoring_policy(conn, policy_id)
        if policy is None:
            typer.echo(f"Scoring policy {policy_id} not found.", err=True)
            raise typer.Exit(1)
    else:
        policy = get_default_scoring_policy(conn, opp.channel_id)
        if policy is None:
            typer.echo(
                f"No default scoring policy found for channel {opp.channel_id}. "
                "Create and activate one first with 'ace intelligence policy create'.",
                err=True,
            )
            raise typer.Exit(1)

    profile = get_active_profile_version(conn, opp.channel_id)
    if profile is None:
        typer.echo(f"No active profile for channel {opp.channel_id}.", err=True)
        raise typer.Exit(1)

    try:
        result = score_opportunity(conn, opportunity_id, policy, profile, force=force)
        conn.commit()
    except Exception as exc:
        typer.echo(f"Scoring failed: {exc}", err=True)
        raise typer.Exit(1) from None

    typer.echo(f'Opportunity #{opp.id}: "{opp.raw_topic}"')
    typer.echo("")
    typer.echo(f"  Composite Score:  {result.composite_score:.2f}  {_bar(result.composite_score)}")
    typer.echo(f"  Confidence:       {result.confidence:.2f}  {_stars(result.confidence)}")
    if result.below_confidence_threshold:
        typer.echo("  ⚠ Below confidence threshold — consider gathering more data.")
    if result.requires_research:
        typer.echo("  ⚠ Requires research: audience_fit or another factor has no data.")

    factors = [
        (
            "audience_fit",
            result.score_audience_fit,
            result.eff_weight_audience_fit,
            result.status_audience_fit,
        ),
        (
            "evergreen_value",
            result.score_evergreen_value,
            result.eff_weight_evergreen_value,
            result.status_evergreen_value,
        ),
        (
            "audience_demand",
            result.score_audience_demand,
            result.eff_weight_audience_demand,
            result.status_audience_demand,
        ),
        (
            "content_novelty",
            result.score_content_novelty,
            result.eff_weight_content_novelty,
            result.status_content_novelty,
        ),
        (
            "trend_strength",
            result.score_trend_strength,
            result.eff_weight_trend_strength,
            result.status_trend_strength,
        ),
        (
            "competition",
            result.score_competition,
            result.eff_weight_competition,
            result.status_competition,
        ),
    ]
    typer.echo("")
    typer.echo("  Factor breakdown:")
    for name, score, ew, status in sorted(factors, key=lambda x: -(x[2] or 0)):
        score_str = f"{score:.2f}" if score is not None else " — "
        typer.echo(f"    {name:<18} {score_str}  eff_w={ew:.2f}  {status.value}")

    typer.echo("")
    obs_count = len(result.observation_ids)
    typer.echo(f"  Observations: {obs_count}  |  Input hash: {result.input_hash[:8]}...")
    typer.echo(f"  Scorer: {result.scorer_version}  |  Scored at: {result.scored_at}")
    default_tag = "  (default)" if policy.is_default else ""
    typer.echo(f'  Policy: v{policy.version} "{policy.label}"{default_tag}')


# ---------------------------------------------------------------------------
# ace intelligence score-all
# ---------------------------------------------------------------------------


@scoring_app.command("score-all")
def intelligence_score_all(
    channel_id: Annotated[int, typer.Option("--channel", "-c", help="Channel ID.")],
    policy_id: Annotated[
        int | None,
        typer.Option("--policy", "-p", help="Scoring policy ID (uses channel default if omitted)."),
    ] = None,
    min_confidence: Annotated[
        float | None,
        typer.Option("--min-confidence", help="Filter output to scores above this confidence."),
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", help="Re-score even if inputs are unchanged.")
    ] = False,
) -> None:
    """Score all new/under_review opportunities for a channel."""
    from app.intelligence.models import LifecycleState
    from app.intelligence.repository import (
        get_active_profile_version,
        get_channel,
        get_default_scoring_policy,
        get_scoring_policy,
        list_opportunities,
    )
    from app.intelligence.scoring import score_opportunity

    conn = _get_db()
    channel = get_channel(conn, channel_id)
    if channel is None:
        typer.echo(f"Channel {channel_id} not found.", err=True)
        raise typer.Exit(1)

    if policy_id is not None:
        policy = get_scoring_policy(conn, policy_id)
        if policy is None:
            typer.echo(f"Scoring policy {policy_id} not found.", err=True)
            raise typer.Exit(1)
    else:
        policy = get_default_scoring_policy(conn, channel_id)
        if policy is None:
            typer.echo(f"No default scoring policy for channel {channel_id}.", err=True)
            raise typer.Exit(1)

    profile = get_active_profile_version(conn, channel_id)
    if profile is None:
        typer.echo(f"No active profile for channel {channel_id}.", err=True)
        raise typer.Exit(1)

    opps = list_opportunities(conn, channel_id, state=LifecycleState.new) + list_opportunities(
        conn, channel_id, state=LifecycleState.under_review
    )

    if not opps:
        typer.echo("No scoreable opportunities found.")
        return

    results = []
    for opp in opps:
        assert opp.id is not None
        result = score_opportunity(conn, opp.id, policy, profile, force=force)
        conn.commit()
        if min_confidence is None or result.confidence >= min_confidence:
            results.append((opp, result))

    results.sort(key=lambda x: -x[1].composite_score)

    typer.echo(f'Scored {len(opps)} opportunities  (policy: v{policy.version} "{policy.label}")')
    typer.echo("")
    typer.echo(f"  {'#':<5} {'topic':<35} {'score':>6} {'conf':>6} {'flags'}")
    typer.echo("  " + "-" * 65)
    for opp, res in results:
        flags = ""
        if res.below_confidence_threshold:
            flags += "⚠conf "
        if res.requires_research:
            flags += "⚠research"
        row = (
            f"  [{opp.id:<4}] {opp.raw_topic[:33]:<35}"
            f" {res.composite_score:>5.2f} {res.confidence:>5.2f}  {flags}"
        )
        typer.echo(row)


# ---------------------------------------------------------------------------
# ace intelligence explain
# ---------------------------------------------------------------------------


@scoring_app.command("explain")
def intelligence_explain(
    opportunity_id: Annotated[int, typer.Argument(help="Opportunity ID.")],
    score_id: Annotated[
        int | None, typer.Option("--score", help="Specific score row ID (uses latest if omitted).")
    ] = None,
    policy_id: Annotated[
        int | None, typer.Option("--policy", help="Policy ID for latest-score lookup.")
    ] = None,
) -> None:
    """Show full scoring rationale for an opportunity's score."""
    from app.intelligence.repository import (
        get_default_scoring_policy,
        get_latest_score,
        get_opportunity,
        get_opportunity_score,
        get_scoring_policy,
    )

    conn = _get_db()
    opp = get_opportunity(conn, opportunity_id)
    if opp is None:
        typer.echo(f"Opportunity {opportunity_id} not found.", err=True)
        raise typer.Exit(1)

    if score_id is not None:
        result = get_opportunity_score(conn, score_id)
        if result is None:
            typer.echo(f"Score {score_id} not found.", err=True)
            raise typer.Exit(1)
    else:
        pid = policy_id
        if pid is None:
            default_policy = get_default_scoring_policy(conn, opp.channel_id)
            pid = default_policy.id if default_policy else None
        if pid is None:
            typer.echo("No default policy found. Pass --policy to specify one.", err=True)
            raise typer.Exit(1)
        result = get_latest_score(conn, opportunity_id, pid)
        if result is None:
            typer.echo(
                f"No score found for opportunity {opportunity_id} under policy {pid}.", err=True
            )
            raise typer.Exit(1)

    policy = get_scoring_policy(conn, result.scoring_policy_id)

    typer.echo(f'Opportunity #{opp.id}: "{opp.raw_topic}"')
    typer.echo(f"  Score row ID:  {result.id}")
    typer.echo(f"  Input hash:    {result.input_hash}")
    typer.echo(f"  Scorer:        {result.scorer_version}")
    typer.echo(f"  Scored at:     {result.scored_at}")
    if policy:
        typer.echo(f'  Policy:        v{policy.version} "{policy.label}"')
    typer.echo("")
    typer.echo(f"  Composite:  {result.composite_score:.4f}")
    conf_tag = "  ⚠ below threshold" if result.below_confidence_threshold else ""
    typer.echo(f"  Confidence: {result.confidence:.4f}{conf_tag}")
    if result.requires_research:
        typer.echo("  ⚠ requires_research=True")
    typer.echo("")
    typer.echo("  Factor detail:")
    factors = [
        (
            "trend_strength",
            result.score_trend_strength,
            result.eff_weight_trend_strength,
            result.status_trend_strength,
        ),
        (
            "audience_demand",
            result.score_audience_demand,
            result.eff_weight_audience_demand,
            result.status_audience_demand,
        ),
        (
            "competition",
            result.score_competition,
            result.eff_weight_competition,
            result.status_competition,
        ),
        (
            "evergreen_value",
            result.score_evergreen_value,
            result.eff_weight_evergreen_value,
            result.status_evergreen_value,
        ),
        (
            "audience_fit",
            result.score_audience_fit,
            result.eff_weight_audience_fit,
            result.status_audience_fit,
        ),
        (
            "content_novelty",
            result.score_content_novelty,
            result.eff_weight_content_novelty,
            result.status_content_novelty,
        ),
    ]
    for name, score, ew, status in factors:
        score_str = f"{score:.4f}" if score is not None else "    —   "
        typer.echo(f"    {name:<18} score={score_str}  eff_w={ew:.4f}  [{status.value}]")

    typer.echo(f"\n  Observations used: {result.observation_ids}")


# ---------------------------------------------------------------------------
# ace intelligence policy list
# ---------------------------------------------------------------------------


@policy_app.command("list")
def policy_list(
    channel_id: Annotated[int, typer.Option("--channel", "-c", help="Channel ID.")],
) -> None:
    """List all scoring policies for a channel."""
    from app.intelligence.repository import get_channel, list_scoring_policies

    conn = _get_db()
    channel = get_channel(conn, channel_id)
    if channel is None:
        typer.echo(f"Channel {channel_id} not found.", err=True)
        raise typer.Exit(1)

    policies = list_scoring_policies(conn, channel_id)
    if not policies:
        typer.echo("No scoring policies found.")
        return

    for p in policies:
        default_flag = "  [DEFAULT]" if p.is_default else ""
        typer.echo(f'[{p.id}] v{p.version} "{p.label}"  status={p.status.value}{default_flag}')


# ---------------------------------------------------------------------------
# ace intelligence policy show
# ---------------------------------------------------------------------------


@policy_app.command("show")
def policy_show(
    policy_id: Annotated[int, typer.Argument(help="Scoring policy ID.")],
) -> None:
    """Show full configuration of a scoring policy."""
    from app.intelligence.repository import get_scoring_policy

    conn = _get_db()
    policy = get_scoring_policy(conn, policy_id)
    if policy is None:
        typer.echo(f"Scoring policy {policy_id} not found.", err=True)
        raise typer.Exit(1)

    typer.echo(f'Scoring policy [{policy.id}] v{policy.version}: "{policy.label}"')
    typer.echo(f"  Status:      {policy.status.value}{'  [DEFAULT]' if policy.is_default else ''}")
    typer.echo(f"  Channel:     {policy.channel_id}")
    typer.echo(f"  Created:     {policy.created_at}  by {policy.created_by or '(unset)'}")
    if policy.activated_at:
        typer.echo(f"  Activated:   {policy.activated_at}")
    if policy.archived_at:
        typer.echo(f"  Archived:    {policy.archived_at}")
    if policy.description:
        typer.echo(f"  Description: {policy.description}")
    typer.echo("")
    typer.echo("  Factor weights:")
    typer.echo(
        f"    audience_fit:       {policy.weight_audience_fit:.2f}"
        f"  [{policy.missing_audience_fit.value}]"
    )
    typer.echo(
        f"    evergreen_value:    {policy.weight_evergreen_value:.2f}"
        f"  [{policy.missing_evergreen_value.value}]"
    )
    typer.echo(
        f"    audience_demand:    {policy.weight_audience_demand:.2f}"
        f"  [{policy.missing_audience_demand.value}]"
    )
    typer.echo(
        f"    competition:        {policy.weight_competition:.2f}"
        f"  [{policy.missing_competition.value}]"
    )
    typer.echo(
        f"    content_novelty:    {policy.weight_content_novelty:.2f}"
        f"  [{policy.missing_content_novelty.value}]"
    )
    typer.echo(
        f"    trend_strength:     {policy.weight_trend_strength:.2f}"
        f"  [{policy.missing_trend_strength.value}]"
    )
    typer.echo("")
    typer.echo("  Confidence parameters:")
    typer.echo(f"    min_confidence_threshold:       {policy.min_confidence_threshold}")
    typer.echo(f"    freshness_decay_days:           {policy.freshness_decay_days}")
    typer.echo(f"    max_corroboration_bonus:        {policy.max_corroboration_bonus}")
    typer.echo(f"    corroboration_bonus_per_source: {policy.corroboration_bonus_per_source}")


# ---------------------------------------------------------------------------
# ace intelligence policy create
# ---------------------------------------------------------------------------


@policy_app.command("create")
def policy_create(
    channel_id: Annotated[int, typer.Option("--channel", "-c", help="Channel ID.")],
    label: Annotated[str, typer.Option("--label", help="Policy label.")],
    description: Annotated[str | None, typer.Option("--description", help="Description.")] = None,
    created_by: Annotated[str, typer.Option("--created-by", help="Operator name.")] = "",
) -> None:
    """Create a new draft scoring policy with default weights."""
    from app.intelligence.models import ScoringPolicy
    from app.intelligence.repository import create_scoring_policy, get_channel

    conn = _get_db()
    channel = get_channel(conn, channel_id)
    if channel is None:
        typer.echo(f"Channel {channel_id} not found.", err=True)
        raise typer.Exit(1)

    try:
        policy = create_scoring_policy(
            conn,
            ScoringPolicy(
                channel_id=channel_id,
                version=1,
                label=label,
                description=description,
                created_by=created_by or None,
            ),
        )
        conn.commit()
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None

    typer.echo(f'Created draft scoring policy [{policy.id}] v{policy.version}: "{policy.label}"')
    typer.echo(f"  Run 'ace intelligence policy activate {policy.id} --confirm' to activate.")


# ---------------------------------------------------------------------------
# ace intelligence policy clone
# ---------------------------------------------------------------------------


@policy_app.command("clone")
def policy_clone(
    policy_id: Annotated[int, typer.Argument(help="Source policy ID.")],
    label: Annotated[str, typer.Option("--label", help="Label for the cloned policy.")],
) -> None:
    """Clone a policy into a new draft for editing."""
    from app.intelligence.repository import clone_scoring_policy

    conn = _get_db()
    try:
        cloned = clone_scoring_policy(conn, policy_id, label)
        conn.commit()
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None

    typer.echo(f'Cloned policy {policy_id} → [{cloned.id}] v{cloned.version}: "{cloned.label}"')
    typer.echo("  Status: draft (not yet active)")
    typer.echo(f"  Run 'ace intelligence policy update {cloned.id} --help' to change weights.")
    typer.echo(f"  Run 'ace intelligence policy activate {cloned.id} --confirm' to activate.")


# ---------------------------------------------------------------------------
# ace intelligence policy update
# ---------------------------------------------------------------------------


@policy_app.command("update")
def policy_update(
    policy_id: Annotated[int, typer.Argument(help="Draft policy ID to update.")],
    label: Annotated[str | None, typer.Option("--label", help="New label.")] = None,
    description: Annotated[
        str | None, typer.Option("--description", help="New description.")
    ] = None,
    weight_trend_strength: Annotated[float | None, typer.Option("--weight-trend-strength")] = None,
    weight_audience_demand: Annotated[
        float | None, typer.Option("--weight-audience-demand")
    ] = None,
    weight_competition: Annotated[float | None, typer.Option("--weight-competition")] = None,
    weight_evergreen_value: Annotated[
        float | None, typer.Option("--weight-evergreen-value")
    ] = None,
    weight_audience_fit: Annotated[float | None, typer.Option("--weight-audience-fit")] = None,
    weight_content_novelty: Annotated[
        float | None, typer.Option("--weight-content-novelty")
    ] = None,
    missing_trend_strength: Annotated[str | None, typer.Option("--missing-trend-strength")] = None,
    missing_audience_demand: Annotated[
        str | None, typer.Option("--missing-audience-demand")
    ] = None,
    missing_competition: Annotated[str | None, typer.Option("--missing-competition")] = None,
    missing_evergreen_value: Annotated[
        str | None, typer.Option("--missing-evergreen-value")
    ] = None,
    missing_audience_fit: Annotated[str | None, typer.Option("--missing-audience-fit")] = None,
    missing_content_novelty: Annotated[
        str | None, typer.Option("--missing-content-novelty")
    ] = None,
    min_confidence_threshold: Annotated[float | None, typer.Option("--min-confidence")] = None,
    freshness_decay_days: Annotated[float | None, typer.Option("--freshness-decay-days")] = None,
    max_corroboration_bonus: Annotated[
        float | None, typer.Option("--max-corroboration-bonus")
    ] = None,
    corroboration_bonus_per_source: Annotated[
        float | None, typer.Option("--corroboration-bonus-per-source")
    ] = None,
) -> None:
    """Update mutable fields on a DRAFT scoring policy. Rejects active/archived policies."""
    from app.intelligence.models import MissingDataPolicy
    from app.intelligence.repository import get_scoring_policy, update_scoring_policy

    conn = _get_db()
    kwargs: dict[str, object] = {}

    if label is not None:
        kwargs["label"] = label
    if description is not None:
        kwargs["description"] = description
    if weight_trend_strength is not None:
        kwargs["weight_trend_strength"] = round(weight_trend_strength, 10)
    if weight_audience_demand is not None:
        kwargs["weight_audience_demand"] = round(weight_audience_demand, 10)
    if weight_competition is not None:
        kwargs["weight_competition"] = round(weight_competition, 10)
    if weight_evergreen_value is not None:
        kwargs["weight_evergreen_value"] = round(weight_evergreen_value, 10)
    if weight_audience_fit is not None:
        kwargs["weight_audience_fit"] = round(weight_audience_fit, 10)
    if weight_content_novelty is not None:
        kwargs["weight_content_novelty"] = round(weight_content_novelty, 10)

    _mp_map = {
        "missing_trend_strength": missing_trend_strength,
        "missing_audience_demand": missing_audience_demand,
        "missing_competition": missing_competition,
        "missing_evergreen_value": missing_evergreen_value,
        "missing_audience_fit": missing_audience_fit,
        "missing_content_novelty": missing_content_novelty,
    }
    for field, value in _mp_map.items():
        if value is not None:
            try:
                kwargs[field] = MissingDataPolicy(value)
            except ValueError:
                typer.echo(
                    f"Invalid missing-data policy {value!r}. "
                    "Valid: reweight_available, apply_prior, require_research",
                    err=True,
                )
                raise typer.Exit(1) from None

    if min_confidence_threshold is not None:
        kwargs["min_confidence_threshold"] = min_confidence_threshold
    if freshness_decay_days is not None:
        kwargs["freshness_decay_days"] = freshness_decay_days
    if max_corroboration_bonus is not None:
        kwargs["max_corroboration_bonus"] = max_corroboration_bonus
    if corroboration_bonus_per_source is not None:
        kwargs["corroboration_bonus_per_source"] = corroboration_bonus_per_source

    if not kwargs:
        typer.echo("No fields to update. Pass at least one option.", err=True)
        raise typer.Exit(1)

    try:
        update_scoring_policy(conn, policy_id, **kwargs)
        conn.commit()
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None

    policy = get_scoring_policy(conn, policy_id)
    assert policy is not None
    typer.echo(f'Policy [{policy_id}] v{policy.version} "{policy.label}" updated.')


# ---------------------------------------------------------------------------
# ace intelligence policy activate
# ---------------------------------------------------------------------------


@policy_app.command("activate")
def policy_activate(
    policy_id: Annotated[int, typer.Argument(help="Policy ID to activate.")],
    confirm: Annotated[
        bool, typer.Option("--confirm", help="Required to proceed with activation.")
    ] = False,
) -> None:
    """Make a policy the channel default. Pass --confirm to proceed."""
    from app.intelligence.repository import activate_scoring_policy, get_scoring_policy

    conn = _get_db()
    policy = get_scoring_policy(conn, policy_id)
    if policy is None:
        typer.echo(f"Scoring policy {policy_id} not found.", err=True)
        raise typer.Exit(1)

    if not confirm:
        status_word = "activate" if policy.status.value == "draft" else "set as default"
        typer.echo(
            f'Policy [{policy_id}] v{policy.version} "{policy.label}" will be {status_word} '
            f"for channel {policy.channel_id}. "
            "All future scoring calls will use this policy. "
            "Pass --confirm to proceed."
        )
        return  # exit 0 — informational guard, not an error

    try:
        activate_scoring_policy(conn, policy_id)
        conn.commit()
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None

    updated = get_scoring_policy(conn, policy_id)
    assert updated is not None
    typer.echo(
        f'Policy [{policy_id}] v{updated.version} "{updated.label}" activated '
        f"as default for channel {updated.channel_id}."
    )


# ---------------------------------------------------------------------------
# ace intelligence policy archive
# ---------------------------------------------------------------------------


@policy_app.command("archive")
def policy_archive(
    policy_id: Annotated[int, typer.Argument(help="Policy ID to archive.")],
) -> None:
    """Archive a scoring policy (must not be the current default)."""
    from app.intelligence.repository import archive_scoring_policy, get_scoring_policy

    conn = _get_db()
    try:
        archive_scoring_policy(conn, policy_id)
        conn.commit()
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None

    policy = get_scoring_policy(conn, policy_id)
    if policy:
        typer.echo(f'Policy [{policy_id}] "{policy.label}" archived.')
