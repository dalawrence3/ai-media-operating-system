"""Generic channel onboarding service — Phase 16B.2.

Idempotent full-stack channel bootstrap: creates or returns all intelligence
rows (channel, profile, monetization strategy, capacity policy, scoring policy)
linked to a control-plane cp_channel_id.

Safety contract:
- Does NOT call any external APIs.
- Does NOT create experiments or market clusters.
- Does NOT mutate existing rows on repeated calls.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.logging import get_logger
from app.intelligence.channel_bridge import (
    bootstrap_intelligence_channel,
    get_intelligence_channel_id,
)
from app.intelligence.models import (
    DEFAULT_PRE_MONETIZATION_WEIGHTS,
    BrandVoice,
    Channel,
    ChannelCapacityPolicy,
    ChannelMonetizationStrategy,
    ChannelProfileVersion,
    ContentStyle,
    MaturityStage,
    MonetizationStatus,
    OperatingMode,
    Platform,
    ScoringPolicy,
    VersionStatus,
)
from app.intelligence.repository import (
    activate_scoring_policy,
    create_monetization_strategy,
    create_or_update_capacity_policy,
    create_profile_version,
    create_scoring_policy,
    get_active_monetization_strategy,
    get_active_profile_version,
    get_capacity_policy,
    get_default_scoring_policy,
)

logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


@dataclass(frozen=True)
class ChannelOnboardingResult:
    channel: Channel
    profile: ChannelProfileVersion
    strategy: ChannelMonetizationStrategy
    capacity: ChannelCapacityPolicy
    scoring_policy: ScoringPolicy
    was_channel_created: bool
    was_profile_created: bool
    was_scoring_policy_created: bool


def bootstrap_channel_intelligence(
    conn: sqlite3.Connection,
    cp_channel_id: str,
    *,
    channel_name: str,
    primary_niche: str,
    platform_channel_id: str | None = None,
    platform: Platform = Platform.youtube,
    operating_mode: OperatingMode = OperatingMode.manual,
    maturity_stage: MaturityStage = MaturityStage.validation,
    secondary_niches: list[str] | None = None,
    excluded_topics: list[str] | None = None,
    audience_description: str = "",
    brand_voice: BrandVoice = BrandVoice.conversational,
    content_style: ContentStyle = ContentStyle.explainer,
    operator: str = "",
) -> ChannelOnboardingResult:
    """Idempotent full-stack channel onboarding.

    Creates or returns a fully-configured intelligence channel linked to
    cp_channel_id. Calling this function multiple times with the same
    cp_channel_id is safe — existing rows are returned without mutation.

    Partial-state recovery: if the channel row exists but profile/strategy/
    scoring policy are missing, they are created on the next call.
    """
    # ── Step 1: Channel row (idempotent) ─────────────────────────────────────
    was_channel_created = get_intelligence_channel_id(conn, cp_channel_id) is None
    channel = bootstrap_intelligence_channel(
        conn,
        cp_channel_id,
        channel_name=channel_name,
        platform_channel_id=platform_channel_id,
        platform=platform,
        operating_mode=operating_mode,
    )
    assert channel.id is not None

    # ── Step 2: Profile + strategy + capacity (idempotent) ───────────────────
    was_profile_created = False
    profile = get_active_profile_version(conn, channel.id)
    strategy: ChannelMonetizationStrategy

    if profile is None:
        was_profile_created = True

        strategy = create_monetization_strategy(
            conn,
            ChannelMonetizationStrategy(
                channel_id=channel.id,
                version=1,
                monetization_status=MonetizationStatus.pre,
                objective_weights=dict(DEFAULT_PRE_MONETIZATION_WEIGHTS),
                created_by=operator,
                status=VersionStatus.active,
                activated_by=operator,
                activation_reason="Channel bootstrapped",
            ),
        )
        assert strategy.id is not None

        profile = create_profile_version(
            conn,
            ChannelProfileVersion(
                channel_id=channel.id,
                version=1,
                strategy_id=strategy.id,
                maturity_stage=maturity_stage,
                primary_niche=primary_niche,
                secondary_niches=secondary_niches or [],
                excluded_topics=excluded_topics or [],
                audience_description=audience_description,
                brand_voice=brand_voice,
                content_style=content_style,
                created_by=operator,
                status=VersionStatus.active,
                activated_by=operator,
                activation_reason="Channel bootstrapped",
            ),
        )
        assert profile.id is not None

        # Wire channel → active profile and strategy (mirrors _activate_channel_* helpers)
        now = _now()
        conn.execute(
            """UPDATE channels
               SET current_profile_version_id = ?,
                   current_maturity_stage = ?,
                   updated_at = ?
               WHERE id = ?""",
            (profile.id, maturity_stage.value, now, channel.id),
        )
        conn.execute(
            "UPDATE channels SET current_strategy_id = ?, updated_at = ? WHERE id = ?",
            (strategy.id, now, channel.id),
        )
        channel.current_profile_version_id = profile.id
        channel.current_strategy_id = strategy.id
        channel.current_maturity_stage = maturity_stage

        create_or_update_capacity_policy(conn, ChannelCapacityPolicy(channel_id=channel.id))
        conn.commit()
    else:
        existing_strategy = get_active_monetization_strategy(conn, channel.id)
        assert existing_strategy is not None, (
            f"Channel {channel.id} has an active profile but no active monetization strategy"
        )
        strategy = existing_strategy

    capacity = get_capacity_policy(conn, channel.id)
    assert capacity is not None

    # ── Step 3: Scoring policy (idempotent) ──────────────────────────────────
    was_scoring_policy_created = False
    scoring_policy = get_default_scoring_policy(conn, channel.id)

    if scoring_policy is None:
        was_scoring_policy_created = True
        draft = create_scoring_policy(
            conn,
            ScoringPolicy(
                channel_id=channel.id,
                version=1,
                label="default",
                created_by=operator,
            ),
        )
        assert draft.id is not None
        activate_scoring_policy(conn, draft.id)
        conn.commit()
        scoring_policy = get_default_scoring_policy(conn, channel.id)
        assert scoring_policy is not None

    logger.info(
        "bootstrap_channel_intelligence: cp_channel_id=%r channel_id=%d "
        "created=(channel=%s profile=%s scoring_policy=%s)",
        cp_channel_id,
        channel.id,
        was_channel_created,
        was_profile_created,
        was_scoring_policy_created,
    )

    return ChannelOnboardingResult(
        channel=channel,
        profile=profile,
        strategy=strategy,
        capacity=capacity,
        scoring_policy=scoring_policy,
        was_channel_created=was_channel_created,
        was_profile_created=was_profile_created,
        was_scoring_policy_created=was_scoring_policy_created,
    )
