"""Repository layer for Phase 3 channel strategy entities."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from app.core.logging import get_logger
from app.intelligence.models import (
    DEFAULT_PRE_MONETIZATION_WEIGHTS,
    AdapterName,
    AudienceIntent,
    BrandVoice,
    Channel,
    ChannelCapacityPolicy,
    ChannelMonetizationStrategy,
    ChannelOperatingModeEvent,
    ChannelProfileVersion,
    ContentStyle,
    DiscoveryRun,
    FactorStatus,
    FormatRecommendation,
    LifecycleState,
    MaturityStage,
    MissingDataPolicy,
    MonetizationStatus,
    OperatingMode,
    Opportunity,
    OpportunityObservation,
    OpportunityScore,
    OpportunitySourceEvidence,
    OpportunityStateEvent,
    Platform,
    PolicyStatus,
    PortfolioTargets,
    PrimaryFormat,
    RunStatus,
    ScoringPolicy,
    SourceQualityTier,
    StrategicRole,
    VersionStatus,
)

logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


# ---------------------------------------------------------------------------
# Row converters
# ---------------------------------------------------------------------------


def _row_to_channel(row: sqlite3.Row) -> Channel:
    return Channel(
        id=row["id"],
        platform=Platform(row["platform"]),
        channel_name=row["channel_name"],
        platform_channel_id=row["platform_channel_id"],
        operating_mode=OperatingMode(row["operating_mode"]),
        current_profile_version_id=row["current_profile_version_id"],
        current_strategy_id=row["current_strategy_id"],
        current_maturity_stage=MaturityStage(row["current_maturity_stage"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _row_to_strategy(row: sqlite3.Row) -> ChannelMonetizationStrategy:
    return ChannelMonetizationStrategy(
        id=row["id"],
        channel_id=row["channel_id"],
        version=row["version"],
        monetization_status=MonetizationStatus(row["monetization_status"]),
        objective_weights=json.loads(row["objective_weights_json"]),
        description=row["description"],
        active_from=datetime.fromisoformat(row["active_from"]),
        superseded_at=(
            datetime.fromisoformat(row["superseded_at"]) if row["superseded_at"] else None
        ),
        created_by=row["created_by"],
        status=VersionStatus(row["status"]),
        activated_at=(datetime.fromisoformat(row["activated_at"]) if row["activated_at"] else None),
        activated_by=row["activated_by"],
        activation_reason=row["activation_reason"],
    )


def _row_to_profile_version(row: sqlite3.Row) -> ChannelProfileVersion:
    portfolio_data = json.loads(row["portfolio_targets_json"])
    return ChannelProfileVersion(
        id=row["id"],
        channel_id=row["channel_id"],
        version=row["version"],
        strategy_id=row["strategy_id"],
        maturity_stage=MaturityStage(row["maturity_stage"]),
        primary_niche=row["primary_niche"],
        secondary_niches=json.loads(row["secondary_niches_json"]),
        excluded_topics=json.loads(row["excluded_topics_json"]),
        audience_description=row["audience_description"],
        audience_demographics=row["audience_demographics"],
        audience_intent=AudienceIntent(row["audience_intent"]),
        brand_voice=BrandVoice(row["brand_voice"]),
        tone_notes=row["tone_notes"],
        brand_rules=json.loads(row["brand_rules_json"]),
        content_style=ContentStyle(row["content_style"]),
        primary_format=PrimaryFormat(row["primary_format"]),
        posting_cadence_per_week=row["posting_cadence_per_week"],
        portfolio_targets=PortfolioTargets(**portfolio_data),
        allowed_discovery_adapters=json.loads(row["allowed_discovery_adapters_json"]),
        max_candidates_per_run=row["max_candidates_per_run"],
        min_opportunity_score=row["min_opportunity_score"],
        duplicate_similarity_threshold=row["duplicate_similarity_threshold"],
        signal_staleness_days=row["signal_staleness_days"],
        scoring_policy_version=row["scoring_policy_version"],
        active_from=datetime.fromisoformat(row["active_from"]),
        superseded_at=(
            datetime.fromisoformat(row["superseded_at"]) if row["superseded_at"] else None
        ),
        created_by=row["created_by"],
        status=VersionStatus(row["status"]),
        activated_at=(datetime.fromisoformat(row["activated_at"]) if row["activated_at"] else None),
        activated_by=row["activated_by"],
        activation_reason=row["activation_reason"],
    )


def _row_to_capacity_policy(row: sqlite3.Row) -> ChannelCapacityPolicy:
    return ChannelCapacityPolicy(
        id=row["id"],
        channel_id=row["channel_id"],
        long_form_slots_per_week=row["long_form_slots_per_week"],
        short_slots_per_week=row["short_slots_per_week"],
        content_package_slots_per_week=row["content_package_slots_per_week"],
        max_concurrent_productions=row["max_concurrent_productions"],
        daily_budget_usd=row["daily_budget_usd"],
        per_video_budget_usd=row["per_video_budget_usd"],
        monthly_budget_usd=row["monthly_budget_usd"],
        review_hours_per_week=row["review_hours_per_week"],
        review_hours_per_short=row["review_hours_per_short"],
        review_hours_per_long_form=row["review_hours_per_long_form"],
        review_hours_per_package=row["review_hours_per_package"],
        trend_reservation_slots=row["trend_reservation_slots"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _row_to_mode_event(row: sqlite3.Row) -> ChannelOperatingModeEvent:
    return ChannelOperatingModeEvent(
        id=row["id"],
        channel_id=row["channel_id"],
        from_mode=OperatingMode(row["from_mode"]) if row["from_mode"] else None,
        to_mode=OperatingMode(row["to_mode"]),
        operator=row["operator"],
        reason=row["reason"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


# ---------------------------------------------------------------------------
# Channel CRUD
# ---------------------------------------------------------------------------


def create_channel(conn: sqlite3.Connection, channel: Channel) -> Channel:
    """Insert a bare channel row (no profile/strategy references yet)."""
    now = _now()
    cur = conn.execute(
        """
        INSERT INTO channels
            (platform, channel_name, platform_channel_id, operating_mode,
             current_maturity_stage, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            channel.platform.value,
            channel.channel_name,
            channel.platform_channel_id,
            channel.operating_mode.value,
            channel.current_maturity_stage.value,
            now,
            now,
        ),
    )
    channel.id = cur.lastrowid
    channel.created_at = datetime.fromisoformat(now)
    channel.updated_at = datetime.fromisoformat(now)
    return channel


def get_channel(conn: sqlite3.Connection, channel_id: int) -> Channel | None:
    row = conn.execute("SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()
    return _row_to_channel(row) if row else None


def get_channel_by_name(conn: sqlite3.Connection, channel_name: str) -> Channel | None:
    row = conn.execute("SELECT * FROM channels WHERE channel_name = ?", (channel_name,)).fetchone()
    return _row_to_channel(row) if row else None


def list_channels(conn: sqlite3.Connection) -> list[Channel]:
    rows = conn.execute("SELECT * FROM channels ORDER BY id").fetchall()
    return [_row_to_channel(r) for r in rows]


def _activate_channel_profile(
    conn: sqlite3.Connection,
    channel_id: int,
    profile_version_id: int,
    maturity_stage: MaturityStage,
) -> None:
    """Update channel to point at its newly activated profile version."""
    conn.execute(
        """
        UPDATE channels
        SET current_profile_version_id = ?,
            current_maturity_stage = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (profile_version_id, maturity_stage.value, _now(), channel_id),
    )


def _activate_channel_strategy(
    conn: sqlite3.Connection,
    channel_id: int,
    strategy_id: int,
) -> None:
    """Update channel to point at its newly activated strategy."""
    conn.execute(
        "UPDATE channels SET current_strategy_id = ?, updated_at = ? WHERE id = ?",
        (strategy_id, _now(), channel_id),
    )


# ---------------------------------------------------------------------------
# Monetization strategy
# ---------------------------------------------------------------------------


def create_monetization_strategy(
    conn: sqlite3.Connection,
    strategy: ChannelMonetizationStrategy,
) -> ChannelMonetizationStrategy:
    now = _now()
    # When inserting as 'active' (initial channel setup), record activation timestamp.
    activated_at = now if strategy.status == VersionStatus.active else None
    cur = conn.execute(
        """
        INSERT INTO channel_monetization_strategies
            (channel_id, version, monetization_status, objective_weights_json,
             description, active_from, superseded_at, created_by,
             status, activated_at, activated_by, activation_reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            strategy.channel_id,
            strategy.version,
            strategy.monetization_status.value,
            json.dumps(strategy.objective_weights),
            strategy.description,
            now,
            None,
            strategy.created_by,
            strategy.status.value,
            activated_at,
            strategy.activated_by,
            strategy.activation_reason,
        ),
    )
    strategy.id = cur.lastrowid
    strategy.active_from = datetime.fromisoformat(now)
    if activated_at:
        strategy.activated_at = datetime.fromisoformat(now)
    return strategy


def get_monetization_strategy(
    conn: sqlite3.Connection, strategy_id: int
) -> ChannelMonetizationStrategy | None:
    row = conn.execute(
        "SELECT * FROM channel_monetization_strategies WHERE id = ?", (strategy_id,)
    ).fetchone()
    return _row_to_strategy(row) if row else None


def get_active_monetization_strategy(
    conn: sqlite3.Connection, channel_id: int
) -> ChannelMonetizationStrategy | None:
    row = conn.execute(
        """
        SELECT * FROM channel_monetization_strategies
        WHERE channel_id = ? AND status = 'active'
        LIMIT 1
        """,
        (channel_id,),
    ).fetchone()
    return _row_to_strategy(row) if row else None


def list_monetization_strategies(
    conn: sqlite3.Connection, channel_id: int
) -> list[ChannelMonetizationStrategy]:
    rows = conn.execute(
        "SELECT * FROM channel_monetization_strategies WHERE channel_id = ? ORDER BY version",
        (channel_id,),
    ).fetchall()
    return [_row_to_strategy(r) for r in rows]


def supersede_monetization_strategy(conn: sqlite3.Connection, strategy_id: int) -> None:
    conn.execute(
        """UPDATE channel_monetization_strategies
        SET superseded_at = ?, status = 'superseded' WHERE id = ?""",
        (_now(), strategy_id),
    )


# ---------------------------------------------------------------------------
# Profile versions
# ---------------------------------------------------------------------------


def create_profile_version(
    conn: sqlite3.Connection,
    profile: ChannelProfileVersion,
) -> ChannelProfileVersion:
    now = _now()
    # When inserting as 'active' (initial channel setup), record activation timestamp.
    activated_at = now if profile.status == VersionStatus.active else None
    cur = conn.execute(
        """
        INSERT INTO channel_profile_versions (
            channel_id, version, strategy_id, maturity_stage,
            primary_niche, secondary_niches_json, excluded_topics_json,
            audience_description, audience_demographics, audience_intent,
            brand_voice, tone_notes, brand_rules_json, content_style,
            primary_format, posting_cadence_per_week, portfolio_targets_json,
            allowed_discovery_adapters_json, max_candidates_per_run,
            min_opportunity_score, duplicate_similarity_threshold,
            signal_staleness_days, scoring_policy_version,
            active_from, superseded_at, created_by,
            status, activated_at, activated_by, activation_reason
        ) VALUES (
            ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?,
            ?, ?,
            ?, ?,
            ?, ?, ?,
            ?, ?, ?, ?
        )
        """,
        (
            profile.channel_id,
            profile.version,
            profile.strategy_id,
            profile.maturity_stage.value,
            profile.primary_niche,
            json.dumps(profile.secondary_niches),
            json.dumps(profile.excluded_topics),
            profile.audience_description,
            profile.audience_demographics,
            profile.audience_intent.value,
            profile.brand_voice.value,
            profile.tone_notes,
            json.dumps(profile.brand_rules),
            profile.content_style.value,
            profile.primary_format.value,
            profile.posting_cadence_per_week,
            json.dumps(profile.portfolio_targets.model_dump()),
            json.dumps(profile.allowed_discovery_adapters),
            profile.max_candidates_per_run,
            profile.min_opportunity_score,
            profile.duplicate_similarity_threshold,
            profile.signal_staleness_days,
            profile.scoring_policy_version,
            now,
            None,
            profile.created_by,
            profile.status.value,
            activated_at,
            profile.activated_by,
            profile.activation_reason,
        ),
    )
    profile.id = cur.lastrowid
    profile.active_from = datetime.fromisoformat(now)
    if activated_at:
        profile.activated_at = datetime.fromisoformat(now)
    return profile


def get_profile_version(conn: sqlite3.Connection, version_id: int) -> ChannelProfileVersion | None:
    row = conn.execute(
        "SELECT * FROM channel_profile_versions WHERE id = ?", (version_id,)
    ).fetchone()
    return _row_to_profile_version(row) if row else None


def get_active_profile_version(
    conn: sqlite3.Connection, channel_id: int
) -> ChannelProfileVersion | None:
    row = conn.execute(
        """
        SELECT * FROM channel_profile_versions
        WHERE channel_id = ? AND status = 'active'
        LIMIT 1
        """,
        (channel_id,),
    ).fetchone()
    return _row_to_profile_version(row) if row else None


def list_profile_versions(conn: sqlite3.Connection, channel_id: int) -> list[ChannelProfileVersion]:
    rows = conn.execute(
        "SELECT * FROM channel_profile_versions WHERE channel_id = ? ORDER BY version",
        (channel_id,),
    ).fetchall()
    return [_row_to_profile_version(r) for r in rows]


def supersede_profile_version(conn: sqlite3.Connection, version_id: int) -> None:
    conn.execute(
        """UPDATE channel_profile_versions
        SET superseded_at = ?, status = 'superseded' WHERE id = ?""",
        (_now(), version_id),
    )


# ---------------------------------------------------------------------------
# Capacity policy
# ---------------------------------------------------------------------------


def create_or_update_capacity_policy(
    conn: sqlite3.Connection,
    policy: ChannelCapacityPolicy,
) -> ChannelCapacityPolicy:
    now = _now()
    existing = get_capacity_policy(conn, policy.channel_id)
    if existing is None:
        cur = conn.execute(
            """
            INSERT INTO channel_capacity_policies (
                channel_id,
                long_form_slots_per_week, short_slots_per_week,
                content_package_slots_per_week, max_concurrent_productions,
                daily_budget_usd, per_video_budget_usd, monthly_budget_usd,
                review_hours_per_week, review_hours_per_short,
                review_hours_per_long_form, review_hours_per_package,
                trend_reservation_slots, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                policy.channel_id,
                policy.long_form_slots_per_week,
                policy.short_slots_per_week,
                policy.content_package_slots_per_week,
                policy.max_concurrent_productions,
                policy.daily_budget_usd,
                policy.per_video_budget_usd,
                policy.monthly_budget_usd,
                policy.review_hours_per_week,
                policy.review_hours_per_short,
                policy.review_hours_per_long_form,
                policy.review_hours_per_package,
                policy.trend_reservation_slots,
                now,
                now,
            ),
        )
        policy.id = cur.lastrowid
        policy.created_at = datetime.fromisoformat(now)
    else:
        conn.execute(
            """
            UPDATE channel_capacity_policies SET
                long_form_slots_per_week = ?,
                short_slots_per_week = ?,
                content_package_slots_per_week = ?,
                max_concurrent_productions = ?,
                daily_budget_usd = ?,
                per_video_budget_usd = ?,
                monthly_budget_usd = ?,
                review_hours_per_week = ?,
                review_hours_per_short = ?,
                review_hours_per_long_form = ?,
                review_hours_per_package = ?,
                trend_reservation_slots = ?,
                updated_at = ?
            WHERE channel_id = ?
            """,
            (
                policy.long_form_slots_per_week,
                policy.short_slots_per_week,
                policy.content_package_slots_per_week,
                policy.max_concurrent_productions,
                policy.daily_budget_usd,
                policy.per_video_budget_usd,
                policy.monthly_budget_usd,
                policy.review_hours_per_week,
                policy.review_hours_per_short,
                policy.review_hours_per_long_form,
                policy.review_hours_per_package,
                policy.trend_reservation_slots,
                now,
                policy.channel_id,
            ),
        )
        policy.id = existing.id
        policy.created_at = existing.created_at
    policy.updated_at = datetime.fromisoformat(now)
    return policy


def get_capacity_policy(conn: sqlite3.Connection, channel_id: int) -> ChannelCapacityPolicy | None:
    row = conn.execute(
        "SELECT * FROM channel_capacity_policies WHERE channel_id = ?", (channel_id,)
    ).fetchone()
    return _row_to_capacity_policy(row) if row else None


# ---------------------------------------------------------------------------
# Operating mode events
# ---------------------------------------------------------------------------


def create_operating_mode_event(
    conn: sqlite3.Connection,
    event: ChannelOperatingModeEvent,
) -> ChannelOperatingModeEvent:
    now = _now()
    cur = conn.execute(
        """
        INSERT INTO channel_operating_mode_events
            (channel_id, from_mode, to_mode, operator, reason, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            event.channel_id,
            event.from_mode.value if event.from_mode else None,
            event.to_mode.value,
            event.operator,
            event.reason,
            now,
        ),
    )
    event.id = cur.lastrowid
    event.created_at = datetime.fromisoformat(now)
    return event


def list_operating_mode_events(
    conn: sqlite3.Connection, channel_id: int
) -> list[ChannelOperatingModeEvent]:
    rows = conn.execute(
        "SELECT * FROM channel_operating_mode_events WHERE channel_id = ? ORDER BY id",
        (channel_id,),
    ).fetchall()
    return [_row_to_mode_event(r) for r in rows]


# ---------------------------------------------------------------------------
# High-level composite operations
# ---------------------------------------------------------------------------


def create_channel_full(
    conn: sqlite3.Connection,
    *,
    channel_name: str,
    primary_niche: str,
    platform: Platform = Platform.youtube,
    maturity_stage: MaturityStage = MaturityStage.validation,
    audience_description: str = "",
    audience_intent: AudienceIntent = AudienceIntent.educational,
    primary_format: PrimaryFormat = PrimaryFormat.short,
    posting_cadence_per_week: int = 3,
    brand_voice: BrandVoice = BrandVoice.conversational,
    operator: str = "",
) -> tuple[Channel, ChannelProfileVersion, ChannelMonetizationStrategy, ChannelCapacityPolicy]:
    """
    Create a channel with its first profile version, strategy v1, and capacity policy
    in a single transaction. The initial versions are created as 'active' immediately
    (no draft step required when there is no prior active version to protect).
    Returns (channel, profile_version, strategy, capacity).
    """
    # 1. Insert bare channel record
    channel = create_channel(
        conn,
        Channel(
            channel_name=channel_name,
            platform=platform,
            current_maturity_stage=maturity_stage,
        ),
    )
    assert channel.id is not None

    # 2. Insert monetization strategy v1 — immediately active (initial setup)
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
            activation_reason="Channel created",
        ),
    )
    assert strategy.id is not None

    # 3. Insert profile version v1 — immediately active (initial setup)
    profile = create_profile_version(
        conn,
        ChannelProfileVersion(
            channel_id=channel.id,
            version=1,
            strategy_id=strategy.id,
            maturity_stage=maturity_stage,
            primary_niche=primary_niche,
            audience_description=audience_description,
            audience_intent=audience_intent,
            primary_format=primary_format,
            posting_cadence_per_week=posting_cadence_per_week,
            brand_voice=brand_voice,
            created_by=operator,
            status=VersionStatus.active,
            activated_by=operator,
            activation_reason="Channel created",
        ),
    )
    assert profile.id is not None

    # 4. Update channel to point at the active profile and strategy
    _activate_channel_profile(conn, channel.id, profile.id, maturity_stage)
    _activate_channel_strategy(conn, channel.id, strategy.id)
    channel.current_profile_version_id = profile.id
    channel.current_strategy_id = strategy.id
    channel.current_maturity_stage = maturity_stage

    # 5. Create capacity policy with operator-approved defaults (D6)
    capacity = create_or_update_capacity_policy(
        conn,
        ChannelCapacityPolicy(channel_id=channel.id),
    )

    # 6. Record initial operating mode event
    create_operating_mode_event(
        conn,
        ChannelOperatingModeEvent(
            channel_id=channel.id,
            from_mode=None,
            to_mode=OperatingMode.manual,
            operator=operator,
            reason="Channel created",
        ),
    )

    conn.commit()
    logger.info("Created channel id=%d name=%r", channel.id, channel.channel_name)
    return channel, profile, strategy, capacity


def set_channel_operating_mode(
    conn: sqlite3.Connection,
    channel_id: int,
    new_mode: OperatingMode,
    operator: str = "",
    reason: str = "",
) -> Channel:
    """
    Update a channel's operating mode and record the transition event.
    Phase 3 callers must enforce that new_mode == OperatingMode.manual before calling.
    """
    channel = get_channel(conn, channel_id)
    if channel is None:
        raise ValueError(f"Channel {channel_id} not found")

    old_mode = channel.operating_mode
    conn.execute(
        "UPDATE channels SET operating_mode = ?, updated_at = ? WHERE id = ?",
        (new_mode.value, _now(), channel_id),
    )
    create_operating_mode_event(
        conn,
        ChannelOperatingModeEvent(
            channel_id=channel_id,
            from_mode=old_mode,
            to_mode=new_mode,
            operator=operator,
            reason=reason,
        ),
    )
    conn.commit()
    channel.operating_mode = new_mode
    return channel


def create_new_profile_version(
    conn: sqlite3.Connection,
    channel_id: int,
    *,
    primary_niche: str | None = None,
    maturity_stage: MaturityStage | None = None,
    audience_description: str | None = None,
    audience_intent: AudienceIntent | None = None,
    primary_format: PrimaryFormat | None = None,
    posting_cadence_per_week: int | None = None,
    brand_voice: BrandVoice | None = None,
    operator: str = "",
) -> ChannelProfileVersion:
    """
    Create a DRAFT profile version carrying forward fields from the current active version.
    Does NOT supersede the current active version or update the channel pointer.
    Call activate_profile_version() to make the draft live.
    Returns the new draft.
    """
    current = get_active_profile_version(conn, channel_id)
    if current is None:
        raise ValueError(f"No active profile version found for channel {channel_id}")

    # Use max(version) across all states to avoid UNIQUE conflicts with existing drafts.
    max_row = conn.execute(
        "SELECT MAX(version) FROM channel_profile_versions WHERE channel_id = ?", (channel_id,)
    ).fetchone()
    next_version = (max_row[0] or 0) + 1

    new_profile = ChannelProfileVersion(
        channel_id=channel_id,
        version=next_version,
        strategy_id=current.strategy_id,
        maturity_stage=maturity_stage if maturity_stage is not None else current.maturity_stage,
        primary_niche=primary_niche if primary_niche is not None else current.primary_niche,
        secondary_niches=list(current.secondary_niches),
        excluded_topics=list(current.excluded_topics),
        audience_description=(
            audience_description
            if audience_description is not None
            else current.audience_description
        ),
        audience_demographics=current.audience_demographics,
        audience_intent=(
            audience_intent if audience_intent is not None else current.audience_intent
        ),
        brand_voice=brand_voice if brand_voice is not None else current.brand_voice,
        tone_notes=current.tone_notes,
        brand_rules=list(current.brand_rules),
        content_style=current.content_style,
        primary_format=primary_format if primary_format is not None else current.primary_format,
        posting_cadence_per_week=(
            posting_cadence_per_week
            if posting_cadence_per_week is not None
            else current.posting_cadence_per_week
        ),
        portfolio_targets=current.portfolio_targets,
        allowed_discovery_adapters=list(current.allowed_discovery_adapters),
        max_candidates_per_run=current.max_candidates_per_run,
        min_opportunity_score=current.min_opportunity_score,
        duplicate_similarity_threshold=current.duplicate_similarity_threshold,
        signal_staleness_days=current.signal_staleness_days,
        scoring_policy_version=current.scoring_policy_version,
        created_by=operator,
        status=VersionStatus.draft,
    )

    new_profile = create_profile_version(conn, new_profile)
    conn.commit()
    logger.info(
        "Channel %d: profile version %d draft created (based on active version %d)",
        channel_id,
        next_version,
        current.version,
    )
    return new_profile


def activate_profile_version(
    conn: sqlite3.Connection,
    version_id: int,
    *,
    operator: str = "",
    reason: str = "",
) -> ChannelProfileVersion:
    """
    Atomically activate a draft profile version.
    Supersedes the currently active version, marks the draft as active,
    and updates the channel pointer. Raises ValueError if the version is
    not in 'draft' status or does not exist.
    """
    row = conn.execute(
        "SELECT * FROM channel_profile_versions WHERE id = ?", (version_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Profile version {version_id} not found")
    if row["status"] != "draft":
        raise ValueError(
            f"Profile version {version_id} has status '{row['status']}', expected 'draft'"
        )

    channel_id = row["channel_id"]
    maturity_stage = MaturityStage(row["maturity_stage"])
    now = _now()

    # Atomically supersede the current active version (there may be none)
    conn.execute(
        """
        UPDATE channel_profile_versions
        SET status = 'superseded', superseded_at = ?
        WHERE channel_id = ? AND status = 'active'
        """,
        (now, channel_id),
    )

    # Activate the draft — record operator and reason for full auditability
    conn.execute(
        """
        UPDATE channel_profile_versions
        SET status = 'active', activated_at = ?, activated_by = ?, activation_reason = ?
        WHERE id = ?
        """,
        (now, operator, reason, version_id),
    )

    # Update channel pointer
    _activate_channel_profile(conn, channel_id, version_id, maturity_stage)

    conn.commit()
    logger.info(
        "Channel %d: profile version %d (id=%d) activated by %r",
        channel_id,
        row["version"],
        version_id,
        operator,
    )
    activated = get_profile_version(conn, version_id)
    assert activated is not None
    return activated


def create_new_strategy_version(
    conn: sqlite3.Connection,
    channel_id: int,
    *,
    objective_weights: dict[str, float],
    monetization_status: MonetizationStatus = MonetizationStatus.pre,
    description: str = "",
    operator: str = "",
) -> ChannelMonetizationStrategy:
    """
    Create a DRAFT strategy version. Does NOT supersede the current active strategy
    or update the channel pointer. Call activate_strategy_version() to make it live.
    Returns the new draft.
    """
    current = get_active_monetization_strategy(conn, channel_id)
    if current is None:
        raise ValueError(f"No active strategy found for channel {channel_id}")

    # Use max(version) to avoid UNIQUE conflicts with existing drafts.
    max_row = conn.execute(
        "SELECT MAX(version) FROM channel_monetization_strategies WHERE channel_id = ?",
        (channel_id,),
    ).fetchone()
    next_version = (max_row[0] or 0) + 1

    new_strategy = create_monetization_strategy(
        conn,
        ChannelMonetizationStrategy(
            channel_id=channel_id,
            version=next_version,
            monetization_status=monetization_status,
            objective_weights=objective_weights,
            description=description,
            created_by=operator,
            status=VersionStatus.draft,
        ),
    )
    assert new_strategy.id is not None

    conn.commit()
    logger.info(
        "Channel %d: strategy version %d draft created (based on active version %d)",
        channel_id,
        next_version,
        current.version,
    )
    return new_strategy


def activate_strategy_version(
    conn: sqlite3.Connection,
    version_id: int,
    *,
    operator: str = "",
    reason: str = "",
) -> ChannelMonetizationStrategy:
    """
    Atomically activate a draft strategy version.
    Supersedes the currently active strategy, marks the draft as active,
    and updates the channel pointer. Raises ValueError if the version is
    not in 'draft' status or does not exist.
    """
    row = conn.execute(
        "SELECT * FROM channel_monetization_strategies WHERE id = ?", (version_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Strategy version {version_id} not found")
    if row["status"] != "draft":
        raise ValueError(
            f"Strategy version {version_id} has status '{row['status']}', expected 'draft'"
        )

    channel_id = row["channel_id"]
    now = _now()

    # Atomically supersede the current active strategy
    conn.execute(
        """
        UPDATE channel_monetization_strategies
        SET status = 'superseded', superseded_at = ?
        WHERE channel_id = ? AND status = 'active'
        """,
        (now, channel_id),
    )

    # Activate the draft — record operator and reason for full auditability
    conn.execute(
        """
        UPDATE channel_monetization_strategies
        SET status = 'active', activated_at = ?, activated_by = ?, activation_reason = ?
        WHERE id = ?
        """,
        (now, operator, reason, version_id),
    )

    # Update channel pointer
    _activate_channel_strategy(conn, channel_id, version_id)

    conn.commit()
    logger.info(
        "Channel %d: strategy version %d (id=%d) activated by %r",
        channel_id,
        row["version"],
        version_id,
        operator,
    )
    activated = get_monetization_strategy(conn, version_id)
    assert activated is not None
    return activated


# ---------------------------------------------------------------------------
# Discovery runs
# ---------------------------------------------------------------------------


def _row_to_discovery_run(row: sqlite3.Row) -> DiscoveryRun:
    return DiscoveryRun(
        id=row["id"],
        channel_id=row["channel_id"],
        profile_version_id=row["profile_version_id"],
        adapter_name=AdapterName(row["adapter_name"]),
        query_parameters_json=row["query_parameters_json"],
        status=RunStatus(row["status"]),
        candidate_count=row["candidate_count"],
        new_opportunity_count=row["new_opportunity_count"],
        dedup_count=row["dedup_count"],
        failed_count=row["failed_count"],
        quota_units_consumed=row["quota_units_consumed"],
        error_message=row["error_message"],
        started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
        completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
    )


def create_discovery_run(conn: sqlite3.Connection, run: DiscoveryRun) -> DiscoveryRun:
    now = _now()
    cursor = conn.execute(
        """INSERT INTO discovery_runs
           (channel_id, profile_version_id, adapter_name, query_parameters_json,
            status, candidate_count, new_opportunity_count, dedup_count,
            failed_count, quota_units_consumed, error_message, started_at, completed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            run.channel_id,
            run.profile_version_id,
            run.adapter_name.value,
            run.query_parameters_json,
            run.status.value,
            run.candidate_count,
            run.new_opportunity_count,
            run.dedup_count,
            run.failed_count,
            run.quota_units_consumed,
            run.error_message,
            run.started_at.strftime("%Y-%m-%dT%H:%M:%S") if run.started_at else now,
            run.completed_at.strftime("%Y-%m-%dT%H:%M:%S") if run.completed_at else None,
        ),
    )
    result = get_discovery_run(conn, cursor.lastrowid)
    assert result is not None
    return result


def update_discovery_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    status: RunStatus | None = None,
    candidate_count: int | None = None,
    new_opportunity_count: int | None = None,
    dedup_count: int | None = None,
    failed_count: int | None = None,
    quota_units_consumed: int | None = None,
    error_message: str | None = None,
    completed_at: str | None = None,
) -> None:
    fields: list[str] = []
    values: list[object] = []
    if status is not None:
        fields.append("status = ?")
        values.append(status.value)
    if candidate_count is not None:
        fields.append("candidate_count = ?")
        values.append(candidate_count)
    if new_opportunity_count is not None:
        fields.append("new_opportunity_count = ?")
        values.append(new_opportunity_count)
    if dedup_count is not None:
        fields.append("dedup_count = ?")
        values.append(dedup_count)
    if failed_count is not None:
        fields.append("failed_count = ?")
        values.append(failed_count)
    if quota_units_consumed is not None:
        fields.append("quota_units_consumed = ?")
        values.append(quota_units_consumed)
    if error_message is not None:
        fields.append("error_message = ?")
        values.append(error_message)
    if completed_at is not None:
        fields.append("completed_at = ?")
        values.append(completed_at)
    if not fields:
        return
    values.append(run_id)
    conn.execute(f"UPDATE discovery_runs SET {', '.join(fields)} WHERE id = ?", values)  # noqa: S608


def get_discovery_run(conn: sqlite3.Connection, run_id: int) -> DiscoveryRun | None:
    row = conn.execute("SELECT * FROM discovery_runs WHERE id = ?", (run_id,)).fetchone()
    return _row_to_discovery_run(row) if row else None


def list_discovery_runs(conn: sqlite3.Connection, channel_id: int) -> list[DiscoveryRun]:
    rows = conn.execute(
        "SELECT * FROM discovery_runs WHERE channel_id = ? ORDER BY started_at DESC",
        (channel_id,),
    ).fetchall()
    return [_row_to_discovery_run(r) for r in rows]


# ---------------------------------------------------------------------------
# Opportunities
# ---------------------------------------------------------------------------


def _row_to_opportunity(row: sqlite3.Row) -> Opportunity:
    return Opportunity(
        id=row["id"],
        channel_id=row["channel_id"],
        discovery_run_id=row["discovery_run_id"],
        normalized_topic=row["normalized_topic"],
        raw_topic=row["raw_topic"],
        title=row["title"],
        topic_summary=row["topic_summary"],
        format_recommendation=FormatRecommendation(row["format_recommendation"]),
        strategic_role=StrategicRole(row["strategic_role"]),
        current_lifecycle_state=LifecycleState(row["current_lifecycle_state"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def create_opportunity(conn: sqlite3.Connection, opp: Opportunity) -> Opportunity:
    """Insert a new opportunity and its initial state event atomically."""
    now = _now()
    cursor = conn.execute(
        """INSERT INTO opportunities
           (channel_id, discovery_run_id, normalized_topic, raw_topic,
            title, topic_summary, format_recommendation, strategic_role,
            current_lifecycle_state, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            opp.channel_id,
            opp.discovery_run_id,
            opp.normalized_topic,
            opp.raw_topic,
            opp.title,
            opp.topic_summary,
            opp.format_recommendation.value,
            opp.strategic_role.value,
            opp.current_lifecycle_state.value,
            now,
            now,
        ),
    )
    opp_id = cursor.lastrowid
    conn.execute(
        """INSERT INTO opportunity_state_events
           (opportunity_id, from_state, to_state, actor, reason, created_at)
           VALUES (?, NULL, ?, 'system', 'discovered', ?)""",
        (opp_id, opp.current_lifecycle_state.value, now),
    )
    result = get_opportunity(conn, opp_id)
    assert result is not None
    return result


def get_opportunity(conn: sqlite3.Connection, opportunity_id: int) -> Opportunity | None:
    row = conn.execute("SELECT * FROM opportunities WHERE id = ?", (opportunity_id,)).fetchone()
    return _row_to_opportunity(row) if row else None


def list_opportunities(
    conn: sqlite3.Connection,
    channel_id: int,
    *,
    state: LifecycleState | None = None,
) -> list[Opportunity]:
    if state is not None:
        rows = conn.execute(
            """SELECT * FROM opportunities
               WHERE channel_id = ? AND current_lifecycle_state = ?
               ORDER BY created_at DESC""",
            (channel_id, state.value),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM opportunities WHERE channel_id = ? ORDER BY created_at DESC",
            (channel_id,),
        ).fetchall()
    return [_row_to_opportunity(r) for r in rows]


def find_existing_opportunity(
    conn: sqlite3.Connection,
    channel_id: int,
    normalized_topic: str,
    threshold: float,
) -> tuple[Opportunity, float] | None:
    """Return (best_matching_opportunity, jaccard_score) if score >= threshold, else None."""
    from app.intelligence.dedup import jaccard_similarity

    rows = conn.execute(
        """SELECT * FROM opportunities
           WHERE channel_id = ?
             AND current_lifecycle_state NOT IN ('rejected', 'archived')""",
        (channel_id,),
    ).fetchall()

    best: tuple[Opportunity, float] | None = None
    for row in rows:
        opp = _row_to_opportunity(row)
        score = jaccard_similarity(normalized_topic, opp.normalized_topic)
        if score >= threshold and (best is None or score > best[1]):
            best = (opp, score)
    return best


def transition_opportunity_state(
    conn: sqlite3.Connection,
    opportunity_id: int,
    to_state: LifecycleState,
    *,
    actor: str = "system",
    reason: str = "",
) -> OpportunityStateEvent:
    opp = get_opportunity(conn, opportunity_id)
    if opp is None:
        raise ValueError(f"Opportunity {opportunity_id} not found")
    now = _now()
    cursor = conn.execute(
        """INSERT INTO opportunity_state_events
           (opportunity_id, from_state, to_state, actor, reason, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (opportunity_id, opp.current_lifecycle_state.value, to_state.value, actor, reason, now),
    )
    conn.execute(
        "UPDATE opportunities SET current_lifecycle_state = ?, updated_at = ? WHERE id = ?",
        (to_state.value, now, opportunity_id),
    )
    row = conn.execute(
        "SELECT * FROM opportunity_state_events WHERE id = ?", (cursor.lastrowid,)
    ).fetchone()
    return _row_to_state_event(row)


# ---------------------------------------------------------------------------
# Observations
# ---------------------------------------------------------------------------


def _row_to_observation(row: sqlite3.Row) -> OpportunityObservation:
    return OpportunityObservation(
        id=row["id"],
        opportunity_id=row["opportunity_id"],
        discovery_run_id=row["discovery_run_id"],
        adapter_name=AdapterName(row["adapter_name"]),
        collected_at=datetime.fromisoformat(row["collected_at"]),
        signal_age_days=row["signal_age_days"],
        source_quality_tier=SourceQualityTier(row["source_quality_tier"]),
        raw_payload_json=row["raw_payload_json"],
        collection_notes=row["collection_notes"],
        was_deduplicated=bool(row["was_deduplicated"]),
        candidate_topic=row["candidate_topic"],
        dedup_similarity_score=row["dedup_similarity_score"],
    )


def create_observation(
    conn: sqlite3.Connection, obs: OpportunityObservation
) -> OpportunityObservation:
    now = _now()
    cursor = conn.execute(
        """INSERT INTO opportunity_observations
           (opportunity_id, discovery_run_id, adapter_name, collected_at,
            signal_age_days, source_quality_tier, raw_payload_json,
            collection_notes, was_deduplicated, candidate_topic, dedup_similarity_score)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            obs.opportunity_id,
            obs.discovery_run_id,
            obs.adapter_name.value,
            obs.collected_at.strftime("%Y-%m-%dT%H:%M:%S") if obs.collected_at else now,
            obs.signal_age_days,
            obs.source_quality_tier.value,
            obs.raw_payload_json,
            obs.collection_notes,
            1 if obs.was_deduplicated else 0,
            obs.candidate_topic,
            obs.dedup_similarity_score,
        ),
    )
    row = conn.execute(
        "SELECT * FROM opportunity_observations WHERE id = ?", (cursor.lastrowid,)
    ).fetchone()
    return _row_to_observation(row)


def list_observations(
    conn: sqlite3.Connection, opportunity_id: int
) -> list[OpportunityObservation]:
    rows = conn.execute(
        """SELECT * FROM opportunity_observations
           WHERE opportunity_id = ? ORDER BY collected_at DESC""",
        (opportunity_id,),
    ).fetchall()
    return [_row_to_observation(r) for r in rows]


# ---------------------------------------------------------------------------
# Source evidence
# ---------------------------------------------------------------------------


def _row_to_evidence(row: sqlite3.Row) -> OpportunitySourceEvidence:
    return OpportunitySourceEvidence(
        id=row["id"],
        observation_id=row["observation_id"],
        opportunity_id=row["opportunity_id"],
        evidence_type=row["evidence_type"],
        evidence_value=row["evidence_value"],
        evidence_text=row["evidence_text"],
        evidence_unit=row["evidence_unit"],
        source_label=row["source_label"],
        collected_at=datetime.fromisoformat(row["collected_at"]),
    )


def create_source_evidence(
    conn: sqlite3.Connection, ev: OpportunitySourceEvidence
) -> OpportunitySourceEvidence:
    now = _now()
    cursor = conn.execute(
        """INSERT INTO opportunity_source_evidence
           (observation_id, opportunity_id, evidence_type, evidence_value,
            evidence_text, evidence_unit, source_label, collected_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            ev.observation_id,
            ev.opportunity_id,
            ev.evidence_type,
            ev.evidence_value,
            ev.evidence_text,
            ev.evidence_unit,
            ev.source_label,
            ev.collected_at.strftime("%Y-%m-%dT%H:%M:%S") if ev.collected_at else now,
        ),
    )
    row = conn.execute(
        "SELECT * FROM opportunity_source_evidence WHERE id = ?", (cursor.lastrowid,)
    ).fetchone()
    return _row_to_evidence(row)


def list_evidence(conn: sqlite3.Connection, observation_id: int) -> list[OpportunitySourceEvidence]:
    rows = conn.execute(
        "SELECT * FROM opportunity_source_evidence WHERE observation_id = ? ORDER BY id",
        (observation_id,),
    ).fetchall()
    return [_row_to_evidence(r) for r in rows]


# ---------------------------------------------------------------------------
# State events
# ---------------------------------------------------------------------------


def _row_to_state_event(row: sqlite3.Row) -> OpportunityStateEvent:
    return OpportunityStateEvent(
        id=row["id"],
        opportunity_id=row["opportunity_id"],
        from_state=LifecycleState(row["from_state"]) if row["from_state"] else None,
        to_state=LifecycleState(row["to_state"]),
        actor=row["actor"],
        reason=row["reason"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def list_state_events(conn: sqlite3.Connection, opportunity_id: int) -> list[OpportunityStateEvent]:
    rows = conn.execute(
        """SELECT * FROM opportunity_state_events
           WHERE opportunity_id = ? ORDER BY created_at""",
        (opportunity_id,),
    ).fetchall()
    return [_row_to_state_event(r) for r in rows]


# ---------------------------------------------------------------------------
# Scoring policies
# ---------------------------------------------------------------------------


def _row_to_scoring_policy(row: sqlite3.Row) -> ScoringPolicy:
    return ScoringPolicy(
        id=row["id"],
        channel_id=row["channel_id"],
        version=row["version"],
        label=row["label"],
        description=row["description"],
        weight_trend_strength=row["weight_trend_strength"],
        weight_audience_demand=row["weight_audience_demand"],
        weight_competition=row["weight_competition"],
        weight_evergreen_value=row["weight_evergreen_value"],
        weight_audience_fit=row["weight_audience_fit"],
        weight_content_novelty=row["weight_content_novelty"],
        missing_trend_strength=MissingDataPolicy(row["missing_trend_strength"]),
        missing_audience_demand=MissingDataPolicy(row["missing_audience_demand"]),
        missing_competition=MissingDataPolicy(row["missing_competition"]),
        missing_evergreen_value=MissingDataPolicy(row["missing_evergreen_value"]),
        missing_audience_fit=MissingDataPolicy(row["missing_audience_fit"]),
        missing_content_novelty=MissingDataPolicy(row["missing_content_novelty"]),
        min_confidence_threshold=row["min_confidence_threshold"],
        freshness_decay_days=row["freshness_decay_days"],
        max_corroboration_bonus=row["max_corroboration_bonus"],
        corroboration_bonus_per_source=row["corroboration_bonus_per_source"],
        status=PolicyStatus(row["status"]),
        is_default=bool(row["is_default"]),
        activated_at=datetime.fromisoformat(row["activated_at"]) if row["activated_at"] else None,
        archived_at=datetime.fromisoformat(row["archived_at"]) if row["archived_at"] else None,
        created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
        created_by=row["created_by"],
    )


def create_scoring_policy(conn: sqlite3.Connection, policy: ScoringPolicy) -> ScoringPolicy:
    if policy.status != PolicyStatus.draft:
        raise ValueError("Only draft policies can be created directly")
    now = _now()
    cur = conn.execute(
        """INSERT INTO scoring_policies (
            channel_id, version, label, description,
            weight_trend_strength, weight_audience_demand, weight_competition,
            weight_evergreen_value, weight_audience_fit, weight_content_novelty,
            missing_trend_strength, missing_audience_demand, missing_competition,
            missing_evergreen_value, missing_audience_fit, missing_content_novelty,
            min_confidence_threshold, freshness_decay_days,
            max_corroboration_bonus, corroboration_bonus_per_source,
            status, is_default, activated_at, archived_at, created_at, created_by
        ) VALUES (
            ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?,
            ?, ?,
            ?, ?, ?, ?, ?, ?
        )""",
        (
            policy.channel_id,
            policy.version,
            policy.label,
            policy.description,
            policy.weight_trend_strength,
            policy.weight_audience_demand,
            policy.weight_competition,
            policy.weight_evergreen_value,
            policy.weight_audience_fit,
            policy.weight_content_novelty,
            policy.missing_trend_strength.value,
            policy.missing_audience_demand.value,
            policy.missing_competition.value,
            policy.missing_evergreen_value.value,
            policy.missing_audience_fit.value,
            policy.missing_content_novelty.value,
            policy.min_confidence_threshold,
            policy.freshness_decay_days,
            policy.max_corroboration_bonus,
            policy.corroboration_bonus_per_source,
            policy.status.value,
            1 if policy.is_default else 0,
            None,
            None,
            now,
            policy.created_by,
        ),
    )
    result = get_scoring_policy(conn, cur.lastrowid)
    assert result is not None
    return result


def get_scoring_policy(conn: sqlite3.Connection, policy_id: int) -> ScoringPolicy | None:
    row = conn.execute("SELECT * FROM scoring_policies WHERE id = ?", (policy_id,)).fetchone()
    return _row_to_scoring_policy(row) if row else None


def get_default_scoring_policy(conn: sqlite3.Connection, channel_id: int) -> ScoringPolicy | None:
    row = conn.execute(
        "SELECT * FROM scoring_policies WHERE channel_id = ? AND is_default = 1",
        (channel_id,),
    ).fetchone()
    return _row_to_scoring_policy(row) if row else None


def list_scoring_policies(conn: sqlite3.Connection, channel_id: int) -> list[ScoringPolicy]:
    rows = conn.execute(
        "SELECT * FROM scoring_policies WHERE channel_id = ? ORDER BY version ASC",
        (channel_id,),
    ).fetchall()
    return [_row_to_scoring_policy(r) for r in rows]


def update_scoring_policy(conn: sqlite3.Connection, policy_id: int, **kwargs: object) -> None:
    """Update mutable fields on a draft policy. Raises ValueError for non-draft policies."""
    row = conn.execute("SELECT status FROM scoring_policies WHERE id = ?", (policy_id,)).fetchone()
    if row is None:
        raise ValueError(f"Scoring policy {policy_id} not found")
    if row["status"] != "draft":
        raise ValueError(
            f"Policy {policy_id} has status '{row['status']}'; only draft policies can be updated. "
            "Use clone_scoring_policy() to create an editable copy of an active policy."
        )

    _UPDATABLE = {
        "label", "description",
        "weight_trend_strength", "weight_audience_demand", "weight_competition",
        "weight_evergreen_value", "weight_audience_fit", "weight_content_novelty",
        "missing_trend_strength", "missing_audience_demand", "missing_competition",
        "missing_evergreen_value", "missing_audience_fit", "missing_content_novelty",
        "min_confidence_threshold", "freshness_decay_days",
        "max_corroboration_bonus", "corroboration_bonus_per_source",
    }
    _MISSING_FIELDS = {
        "missing_trend_strength", "missing_audience_demand", "missing_competition",
        "missing_evergreen_value", "missing_audience_fit", "missing_content_novelty",
    }

    unknown = set(kwargs) - _UPDATABLE
    if unknown:
        raise ValueError(f"Cannot update fields: {sorted(unknown)}")

    # Coerce MissingDataPolicy values to their string representation
    params: dict[str, object] = {}
    for k, v in kwargs.items():
        if k in _MISSING_FIELDS and isinstance(v, MissingDataPolicy):
            params[k] = v.value
        else:
            params[k] = v

    # Re-validate weight sum if any weight changed
    weight_keys = {
        "weight_trend_strength", "weight_audience_demand", "weight_competition",
        "weight_evergreen_value", "weight_audience_fit", "weight_content_novelty",
    }
    if weight_keys & set(params):
        current = get_scoring_policy(conn, policy_id)
        assert current is not None
        updated = current.model_copy(update=params)
        # model_validator fires in model_copy if weights changed
        _ = ScoringPolicy(**updated.model_dump())

    fields = [f"{k} = ?" for k in params]
    values = list(params.values()) + [policy_id]
    conn.execute(  # noqa: S608
        f"UPDATE scoring_policies SET {', '.join(fields)} WHERE id = ?",
        values,
    )


def activate_scoring_policy(conn: sqlite3.Connection, policy_id: int) -> None:
    """
    Make the policy the channel default.
    - draft → frozen (status='active'), becomes default
    - active non-default → becomes default (content already frozen)
    - active already-default → idempotent no-op
    - archived → raises ValueError
    """
    row = conn.execute("SELECT * FROM scoring_policies WHERE id = ?", (policy_id,)).fetchone()
    if row is None:
        raise ValueError(f"Scoring policy {policy_id} not found")
    if row["status"] == "archived":
        raise ValueError(f"Policy {policy_id} is archived and cannot be activated")

    # Idempotent: already the default
    if row["is_default"] == 1 and row["status"] == "active":
        return

    channel_id = row["channel_id"]
    now = _now()

    # Clear prior default for this channel
    conn.execute(
        "UPDATE scoring_policies SET is_default = 0 WHERE channel_id = ? AND is_default = 1",
        (channel_id,),
    )

    # Freeze content if transitioning from draft; update is_default regardless
    activated_at = row["activated_at"] if row["activated_at"] else now
    conn.execute(
        """UPDATE scoring_policies
           SET is_default = 1, status = 'active', activated_at = ?
           WHERE id = ?""",
        (activated_at, policy_id),
    )


def clone_scoring_policy(
    conn: sqlite3.Connection, policy_id: int, label: str
) -> ScoringPolicy:
    """Clone any non-archived policy into a new draft at the next version number."""
    source = get_scoring_policy(conn, policy_id)
    if source is None:
        raise ValueError(f"Scoring policy {policy_id} not found")

    max_row = conn.execute(
        "SELECT MAX(version) FROM scoring_policies WHERE channel_id = ?",
        (source.channel_id,),
    ).fetchone()
    next_version = (max_row[0] or 0) + 1

    clone = ScoringPolicy(
        channel_id=source.channel_id,
        version=next_version,
        label=label,
        description=source.description,
        weight_trend_strength=source.weight_trend_strength,
        weight_audience_demand=source.weight_audience_demand,
        weight_competition=source.weight_competition,
        weight_evergreen_value=source.weight_evergreen_value,
        weight_audience_fit=source.weight_audience_fit,
        weight_content_novelty=source.weight_content_novelty,
        missing_trend_strength=source.missing_trend_strength,
        missing_audience_demand=source.missing_audience_demand,
        missing_competition=source.missing_competition,
        missing_evergreen_value=source.missing_evergreen_value,
        missing_audience_fit=source.missing_audience_fit,
        missing_content_novelty=source.missing_content_novelty,
        min_confidence_threshold=source.min_confidence_threshold,
        freshness_decay_days=source.freshness_decay_days,
        max_corroboration_bonus=source.max_corroboration_bonus,
        corroboration_bonus_per_source=source.corroboration_bonus_per_source,
        status=PolicyStatus.draft,
        is_default=False,
    )
    return create_scoring_policy(conn, clone)


def archive_scoring_policy(conn: sqlite3.Connection, policy_id: int) -> None:
    row = conn.execute("SELECT * FROM scoring_policies WHERE id = ?", (policy_id,)).fetchone()
    if row is None:
        raise ValueError(f"Scoring policy {policy_id} not found")
    if row["status"] == "archived":
        raise ValueError(f"Policy {policy_id} is already archived")
    if row["is_default"] == 1:
        raise ValueError(
            f"Policy {policy_id} is the current default and cannot be archived. "
            "Activate another policy first."
        )
    conn.execute(
        "UPDATE scoring_policies"
        " SET status = 'archived', archived_at = ?, is_default = 0 WHERE id = ?",
        (_now(), policy_id),
    )


# ---------------------------------------------------------------------------
# Opportunity scores
# ---------------------------------------------------------------------------


def _row_to_opportunity_score(row: sqlite3.Row) -> OpportunityScore:
    return OpportunityScore(
        id=row["id"],
        opportunity_id=row["opportunity_id"],
        scoring_policy_id=row["scoring_policy_id"],
        channel_profile_version_id=row["channel_profile_version_id"],
        composite_score=row["composite_score"],
        confidence=row["confidence"],
        score_trend_strength=row["score_trend_strength"],
        score_audience_demand=row["score_audience_demand"],
        score_competition=row["score_competition"],
        score_evergreen_value=row["score_evergreen_value"],
        score_audience_fit=row["score_audience_fit"],
        score_content_novelty=row["score_content_novelty"],
        status_trend_strength=FactorStatus(row["status_trend_strength"]),
        status_audience_demand=FactorStatus(row["status_audience_demand"]),
        status_competition=FactorStatus(row["status_competition"]),
        status_evergreen_value=FactorStatus(row["status_evergreen_value"]),
        status_audience_fit=FactorStatus(row["status_audience_fit"]),
        status_content_novelty=FactorStatus(row["status_content_novelty"]),
        eff_weight_trend_strength=row["eff_weight_trend_strength"],
        eff_weight_audience_demand=row["eff_weight_audience_demand"],
        eff_weight_competition=row["eff_weight_competition"],
        eff_weight_evergreen_value=row["eff_weight_evergreen_value"],
        eff_weight_audience_fit=row["eff_weight_audience_fit"],
        eff_weight_content_novelty=row["eff_weight_content_novelty"],
        observation_ids_json=row["observation_ids_json"],
        input_snapshot_json=row["input_snapshot_json"],
        input_hash=row["input_hash"],
        below_confidence_threshold=bool(row["below_confidence_threshold"]),
        requires_research=bool(row["requires_research"]),
        scored_at=datetime.fromisoformat(row["scored_at"]) if row["scored_at"] else None,
        scorer_version=row["scorer_version"],
    )


def create_opportunity_score(
    conn: sqlite3.Connection, score: OpportunityScore
) -> OpportunityScore:
    """Append-only insert. Never updates an existing row."""
    now = _now()
    cur = conn.execute(
        """INSERT INTO opportunity_scores (
            opportunity_id, scoring_policy_id, channel_profile_version_id,
            composite_score, confidence,
            score_trend_strength, score_audience_demand, score_competition,
            score_evergreen_value, score_audience_fit, score_content_novelty,
            status_trend_strength, status_audience_demand, status_competition,
            status_evergreen_value, status_audience_fit, status_content_novelty,
            eff_weight_trend_strength, eff_weight_audience_demand, eff_weight_competition,
            eff_weight_evergreen_value, eff_weight_audience_fit, eff_weight_content_novelty,
            observation_ids_json, input_snapshot_json, input_hash,
            below_confidence_threshold, requires_research,
            scored_at, scorer_version
        ) VALUES (
            ?, ?, ?,
            ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?,
            ?, ?
        )""",
        (
            score.opportunity_id,
            score.scoring_policy_id,
            score.channel_profile_version_id,
            score.composite_score,
            score.confidence,
            score.score_trend_strength,
            score.score_audience_demand,
            score.score_competition,
            score.score_evergreen_value,
            score.score_audience_fit,
            score.score_content_novelty,
            score.status_trend_strength.value,
            score.status_audience_demand.value,
            score.status_competition.value,
            score.status_evergreen_value.value,
            score.status_audience_fit.value,
            score.status_content_novelty.value,
            score.eff_weight_trend_strength,
            score.eff_weight_audience_demand,
            score.eff_weight_competition,
            score.eff_weight_evergreen_value,
            score.eff_weight_audience_fit,
            score.eff_weight_content_novelty,
            score.observation_ids_json,
            score.input_snapshot_json,
            score.input_hash,
            1 if score.below_confidence_threshold else 0,
            1 if score.requires_research else 0,
            now,
            score.scorer_version,
        ),
    )
    result = get_opportunity_score(conn, cur.lastrowid)
    assert result is not None
    return result


def get_opportunity_score(conn: sqlite3.Connection, score_id: int) -> OpportunityScore | None:
    row = conn.execute(
        "SELECT * FROM opportunity_scores WHERE id = ?", (score_id,)
    ).fetchone()
    return _row_to_opportunity_score(row) if row else None


def get_latest_score(
    conn: sqlite3.Connection,
    opportunity_id: int,
    scoring_policy_id: int,
) -> OpportunityScore | None:
    row = conn.execute(
        """SELECT * FROM opportunity_scores
           WHERE opportunity_id = ? AND scoring_policy_id = ?
           ORDER BY scored_at DESC, id DESC
           LIMIT 1""",
        (opportunity_id, scoring_policy_id),
    ).fetchone()
    return _row_to_opportunity_score(row) if row else None


def list_scores_for_opportunity(
    conn: sqlite3.Connection,
    opportunity_id: int,
    scoring_policy_id: int | None = None,
) -> list[OpportunityScore]:
    if scoring_policy_id is not None:
        rows = conn.execute(
            """SELECT * FROM opportunity_scores
               WHERE opportunity_id = ? AND scoring_policy_id = ?
               ORDER BY scored_at DESC, id DESC""",
            (opportunity_id, scoring_policy_id),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT * FROM opportunity_scores
               WHERE opportunity_id = ?
               ORDER BY scored_at DESC, id DESC""",
            (opportunity_id,),
        ).fetchall()
    return [_row_to_opportunity_score(r) for r in rows]


def list_scored_opportunities(
    conn: sqlite3.Connection,
    channel_id: int,
    scoring_policy_id: int,
    *,
    min_score: float | None = None,
    min_confidence: float | None = None,
    limit: int = 50,
) -> list[tuple[Opportunity, OpportunityScore]]:
    """Return (Opportunity, latest OpportunityScore) pairs, ordered by composite_score DESC."""
    score_filter = ""
    params: list[object] = [scoring_policy_id, scoring_policy_id, channel_id]
    if min_score is not None:
        score_filter += " AND s.composite_score >= ?"
        params.append(min_score)
    if min_confidence is not None:
        score_filter += " AND s.confidence >= ?"
        params.append(min_confidence)
    params.append(limit)

    rows = conn.execute(
        f"""
        SELECT o.id AS opp_id, s.id AS score_id
        FROM opportunities o
        JOIN opportunity_scores s ON s.opportunity_id = o.id
        WHERE s.scoring_policy_id = ?
          AND NOT EXISTS (
              SELECT 1 FROM opportunity_scores s2
              WHERE s2.opportunity_id = s.opportunity_id
                AND s2.scoring_policy_id = ?
                AND (s2.scored_at > s.scored_at
                     OR (s2.scored_at = s.scored_at AND s2.id > s.id))
          )
          AND o.channel_id = ?
          {score_filter}
        ORDER BY s.composite_score DESC, s.confidence DESC
        LIMIT ?
        """,  # noqa: S608
        params,
    ).fetchall()

    results = []
    for row in rows:
        opp = get_opportunity(conn, row["opp_id"])
        score_obj = get_opportunity_score(conn, row["score_id"])
        if opp and score_obj:
            results.append((opp, score_obj))
    return results


# ---------------------------------------------------------------------------
# Opportunity promotion
# ---------------------------------------------------------------------------


def _row_to_promoted_topic(row: sqlite3.Row):  # type: ignore[return]
    from datetime import datetime

    from app.core.models import Topic, TopicStatus

    return Topic(
        id=row["id"],
        title=row["title"],
        angle=row["angle"],
        status=TopicStatus(row["status"]),
        promoted_opportunity_id=row["promoted_opportunity_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def promote_opportunity(
    conn: sqlite3.Connection,
    opportunity_id: int,
    *,
    angle_override: str | None = None,
    operator: str = "operator",
    allow_unscored: bool = False,
) -> tuple:
    """
    Promote a discovered opportunity to an active topic.

    Returns (Topic, OpportunityStateEvent). Idempotent: if a topic linked to
    this opportunity already exists it is returned immediately. Uses a SAVEPOINT
    to make the topics INSERT and the lifecycle transition atomic.
    """
    from app.core.repository import get_topic_by_promoted_opportunity

    opp = get_opportunity(conn, opportunity_id)
    if opp is None:
        raise ValueError(f"Opportunity {opportunity_id} not found")

    existing = get_topic_by_promoted_opportunity(conn, opportunity_id)
    if existing is not None:
        events = list_state_events(conn, opportunity_id)
        last_event = events[-1] if events else None
        return (existing, last_event)

    _ELIGIBLE = {LifecycleState.new, LifecycleState.under_review}
    if opp.current_lifecycle_state not in _ELIGIBLE:
        raise ValueError(
            f"Opportunity {opportunity_id} is in state "
            f"'{opp.current_lifecycle_state.value}' and cannot be promoted. "
            "Only 'new' or 'under_review' opportunities are eligible."
        )

    has_score = (
        conn.execute(
            "SELECT 1 FROM opportunity_scores WHERE opportunity_id = ? LIMIT 1",
            (opportunity_id,),
        ).fetchone()
        is not None
    )
    if not has_score and not allow_unscored:
        raise ValueError(
            f"Opportunity {opportunity_id} has no score. "
            "Score it first with 'ace intelligence score', "
            "or pass --allow-unscored to promote anyway."
        )

    title = opp.title if opp.title else opp.raw_topic
    angle = angle_override if angle_override is not None else (opp.topic_summary or "")
    now = _now()

    sp = f"sp_promote_{opportunity_id}"
    conn.execute(f"SAVEPOINT {sp}")
    try:
        cursor = conn.execute(
            """INSERT INTO topics
               (title, angle, status, promoted_opportunity_id, created_at, updated_at)
               VALUES (?, ?, 'active', ?, ?, ?)""",
            (title, angle, opportunity_id, now, now),
        )
        topic_id = cursor.lastrowid

        event = transition_opportunity_state(
            conn,
            opportunity_id,
            LifecycleState.approved,
            actor=operator,
            reason="promoted to topic",
        )

        conn.execute(f"RELEASE SAVEPOINT {sp}")
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {sp}")
        conn.execute(f"RELEASE SAVEPOINT {sp}")
        raise

    conn.commit()
    row = conn.execute("SELECT * FROM topics WHERE id = ?", (topic_id,)).fetchone()
    topic = _row_to_promoted_topic(row)
    logger.info(
        "Promoted opportunity id=%d → topic id=%d title=%r",
        opportunity_id,
        topic_id,
        title,
    )
    return (topic, event)


def get_max_similarity_to_existing(
    conn: sqlite3.Connection,
    opportunity_id: int,
    channel_id: int,
) -> tuple[float, int, str] | None:
    """
    Return (best_similarity, matched_opportunity_id, matched_normalized_topic) among all
    non-rejected, non-archived opportunities in the channel, excluding the current opportunity.
    Returns None if no comparable opportunities exist (first in channel).
    """
    from app.intelligence.dedup import jaccard_similarity

    rows = conn.execute(
        """SELECT id, normalized_topic FROM opportunities
           WHERE channel_id = ?
             AND id != ?
             AND current_lifecycle_state NOT IN ('rejected', 'archived')""",
        (channel_id, opportunity_id),
    ).fetchall()

    if not rows:
        return None

    opp_row = conn.execute(
        "SELECT normalized_topic FROM opportunities WHERE id = ?", (opportunity_id,)
    ).fetchone()
    if opp_row is None:
        return None

    query_topic = opp_row["normalized_topic"]
    best: tuple[float, int, str] | None = None
    for row in rows:
        sim = jaccard_similarity(query_topic, row["normalized_topic"])
        if best is None or sim > best[0]:
            best = (sim, row["id"], row["normalized_topic"])
    return best
