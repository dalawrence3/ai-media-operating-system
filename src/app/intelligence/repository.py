"""Repository layer for Phase 3 channel strategy entities."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from app.core.logging import get_logger
from app.intelligence.models import (
    DEFAULT_PRE_MONETIZATION_WEIGHTS,
    AudienceIntent,
    BrandVoice,
    Channel,
    ChannelCapacityPolicy,
    ChannelMonetizationStrategy,
    ChannelOperatingModeEvent,
    ChannelProfileVersion,
    ContentStyle,
    MaturityStage,
    MonetizationStatus,
    OperatingMode,
    Platform,
    PortfolioTargets,
    PrimaryFormat,
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
