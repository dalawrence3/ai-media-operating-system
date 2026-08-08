"""Tests for intelligence Pydantic models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.intelligence.models import (
    Channel,
    ChannelCapacityPolicy,
    ChannelMonetizationStrategy,
    ChannelProfileVersion,
    ContentStyle,
    MaturityStage,
    MonetizationStatus,
    OperatingMode,
    Platform,
    PortfolioTargets,
    PrimaryFormat,
)

# ---------------------------------------------------------------------------
# Channel
# ---------------------------------------------------------------------------


def test_channel_defaults() -> None:
    ch = Channel(channel_name="Finance Tips")
    assert ch.platform == Platform.youtube
    assert ch.operating_mode == OperatingMode.manual
    assert ch.current_maturity_stage == MaturityStage.validation
    assert ch.id is None


def test_channel_name_stripped() -> None:
    ch = Channel(channel_name="  Finance Tips  ")
    assert ch.channel_name == "Finance Tips"


def test_channel_name_empty_rejected() -> None:
    with pytest.raises(ValidationError):
        Channel(channel_name="")


def test_channel_name_whitespace_only_rejected() -> None:
    with pytest.raises(ValidationError):
        Channel(channel_name="   ")


def test_channel_valid_operating_modes() -> None:
    for mode in ("manual", "supervised", "autonomous"):
        ch = Channel(channel_name="X", operating_mode=mode)
        assert ch.operating_mode == OperatingMode(mode)


def test_channel_invalid_operating_mode_rejected() -> None:
    with pytest.raises(ValidationError):
        Channel(channel_name="X", operating_mode="turbo")  # type: ignore[arg-type]


def test_channel_valid_maturity_stages() -> None:
    for stage in ("validation", "growth", "monetization", "optimization", "scaling"):
        ch = Channel(channel_name="X", current_maturity_stage=stage)
        assert ch.current_maturity_stage == MaturityStage(stage)


# ---------------------------------------------------------------------------
# PortfolioTargets
# ---------------------------------------------------------------------------


def test_portfolio_targets_defaults_sum_to_one() -> None:
    pt = PortfolioTargets()
    total = pt.evergreen + pt.trending + pt.seasonal + pt.experimental
    assert abs(total - 1.0) < 0.001


def test_portfolio_targets_custom_sum_to_one() -> None:
    pt = PortfolioTargets(evergreen=0.5, trending=0.3, seasonal=0.1, experimental=0.1)
    assert abs((pt.evergreen + pt.trending + pt.seasonal + pt.experimental) - 1.0) < 0.001


def test_portfolio_targets_not_summing_to_one_rejected() -> None:
    with pytest.raises(ValidationError, match="sum to 1.0"):
        PortfolioTargets(evergreen=0.5, trending=0.5, seasonal=0.1, experimental=0.1)


def test_portfolio_targets_negative_value_rejected() -> None:
    with pytest.raises(ValidationError):
        PortfolioTargets(evergreen=-0.1, trending=0.7, seasonal=0.2, experimental=0.2)


def test_portfolio_targets_extra_field_rejected() -> None:
    with pytest.raises(ValidationError):
        PortfolioTargets(evergreen=0.6, trending=0.2, seasonal=0.1, experimental=0.1, bonus=0.0)


# ---------------------------------------------------------------------------
# ChannelMonetizationStrategy
# ---------------------------------------------------------------------------


def test_strategy_valid_weights() -> None:
    s = ChannelMonetizationStrategy(
        channel_id=1,
        version=1,
        objective_weights={"qualified_subscriber_growth": 0.6, "watch_hour_progress": 0.4},
    )
    assert s.objective_weights["qualified_subscriber_growth"] == pytest.approx(0.6)


def test_strategy_weights_not_summing_rejected() -> None:
    with pytest.raises(ValidationError, match="sum to 1.0"):
        ChannelMonetizationStrategy(
            channel_id=1,
            version=1,
            objective_weights={"qualified_subscriber_growth": 0.3, "watch_hour_progress": 0.3},
        )


def test_strategy_unknown_objective_key_rejected() -> None:
    with pytest.raises(ValidationError, match="Unknown objective keys"):
        ChannelMonetizationStrategy(
            channel_id=1,
            version=1,
            objective_weights={"invented_kpi": 1.0},
        )


def test_strategy_negative_weight_rejected() -> None:
    with pytest.raises(ValidationError):
        ChannelMonetizationStrategy(
            channel_id=1,
            version=1,
            objective_weights={"qualified_subscriber_growth": -0.5, "watch_hour_progress": 1.5},
        )


def test_strategy_empty_weights_rejected() -> None:
    with pytest.raises(ValidationError):
        ChannelMonetizationStrategy(channel_id=1, version=1, objective_weights={})


def test_strategy_monetization_status_values() -> None:
    for status in ("pre", "active"):
        s = ChannelMonetizationStrategy(
            channel_id=1,
            version=1,
            monetization_status=status,
            objective_weights={"qualified_subscriber_growth": 1.0},
        )
        assert s.monetization_status == MonetizationStatus(status)


# ---------------------------------------------------------------------------
# ChannelProfileVersion
# ---------------------------------------------------------------------------


def test_profile_version_valid() -> None:
    p = ChannelProfileVersion(channel_id=1, version=1, primary_niche="personal finance")
    assert p.primary_niche == "personal finance"
    assert p.duplicate_similarity_threshold == pytest.approx(0.70)  # D5 default
    assert p.min_opportunity_score == pytest.approx(0.40)


def test_profile_version_niche_stripped() -> None:
    p = ChannelProfileVersion(channel_id=1, version=1, primary_niche="  budgeting  ")
    assert p.primary_niche == "budgeting"


def test_profile_version_empty_niche_rejected() -> None:
    with pytest.raises(ValidationError):
        ChannelProfileVersion(channel_id=1, version=1, primary_niche="")


def test_profile_version_cadence_bounds() -> None:
    with pytest.raises(ValidationError):
        ChannelProfileVersion(
            channel_id=1, version=1, primary_niche="x", posting_cadence_per_week=0
        )
    with pytest.raises(ValidationError):
        ChannelProfileVersion(
            channel_id=1, version=1, primary_niche="x", posting_cadence_per_week=22
        )


def test_profile_version_invalid_adapter_rejected() -> None:
    with pytest.raises(ValidationError, match="Unknown discovery adapters"):
        ChannelProfileVersion(
            channel_id=1,
            version=1,
            primary_niche="x",
            allowed_discovery_adapters=["manual", "fake_adapter"],
        )


def test_profile_version_valid_adapters() -> None:
    p = ChannelProfileVersion(
        channel_id=1,
        version=1,
        primary_niche="x",
        allowed_discovery_adapters=["manual", "youtube_data_api"],
    )
    assert "manual" in p.allowed_discovery_adapters


def test_profile_version_content_style_values() -> None:
    for style in ("story-driven", "list-based", "explainer", "mixed"):
        p = ChannelProfileVersion(channel_id=1, version=1, primary_niche="x", content_style=style)
        assert p.content_style == ContentStyle(style)


def test_profile_version_format_values() -> None:
    for fmt in ("short", "long_form", "both", "content_package"):
        p = ChannelProfileVersion(channel_id=1, version=1, primary_niche="x", primary_format=fmt)
        assert p.primary_format == PrimaryFormat(fmt)


# ---------------------------------------------------------------------------
# ChannelCapacityPolicy
# ---------------------------------------------------------------------------


def test_capacity_policy_defaults_match_d6() -> None:
    """Operator decision D6 defaults must be preserved."""
    p = ChannelCapacityPolicy(channel_id=1)
    assert p.long_form_slots_per_week == 2
    assert p.short_slots_per_week == 4
    assert p.content_package_slots_per_week == 1
    assert p.max_concurrent_productions == 2
    assert p.review_hours_per_week == pytest.approx(3.0)


def test_capacity_policy_negative_budget_rejected() -> None:
    with pytest.raises(ValidationError):
        ChannelCapacityPolicy(channel_id=1, daily_budget_usd=-1.0)


def test_capacity_policy_negative_slots_rejected() -> None:
    with pytest.raises(ValidationError):
        ChannelCapacityPolicy(channel_id=1, long_form_slots_per_week=-1)


def test_capacity_policy_zero_concurrent_rejected() -> None:
    with pytest.raises(ValidationError):
        ChannelCapacityPolicy(channel_id=1, max_concurrent_productions=0)
