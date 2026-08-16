"""Tests for production profile models and registry."""

from __future__ import annotations

import pytest

from app.profiles.models import ProductionProfile
from app.profiles.registry import PROFILES, YOUTUBE_SHORT, get_profile


class TestProductionProfile:
    def test_word_counts_derived_from_duration(self) -> None:
        p = ProductionProfile(
            name="test",
            target_duration_s=60,
            min_duration_s=45,
            max_duration_s=90,
            width=1080,
            height=1920,
            fps=30,
        )
        assert p.target_word_count == 150  # 60s * 150wpm / 60
        assert p.min_word_count == 112  # 45s * 150wpm / 60
        assert p.max_word_count == 225  # 90s * 150wpm / 60

    def test_frozen(self) -> None:
        p = YOUTUBE_SHORT
        with pytest.raises((AttributeError, TypeError)):
            p.name = "other"  # type: ignore[misc]


class TestYoutubeShortProfile:
    def test_dimensions(self) -> None:
        assert YOUTUBE_SHORT.width == 1080
        assert YOUTUBE_SHORT.height == 1920
        assert YOUTUBE_SHORT.fps == 30

    def test_duration_range(self) -> None:
        assert YOUTUBE_SHORT.target_duration_s == 45
        assert YOUTUBE_SHORT.min_duration_s == 35
        assert YOUTUBE_SHORT.max_duration_s == 55

    def test_word_counts_in_expected_range(self) -> None:
        # 35–55s at 150wpm = 87–137 words
        assert 85 <= YOUTUBE_SHORT.min_word_count <= 90
        assert 130 <= YOUTUBE_SHORT.max_word_count <= 140
        assert (
            YOUTUBE_SHORT.min_word_count
            < YOUTUBE_SHORT.target_word_count
            < YOUTUBE_SHORT.max_word_count
        )

    def test_name(self) -> None:
        assert YOUTUBE_SHORT.name == "youtube_short"


class TestRegistry:
    def test_get_known_profile(self) -> None:
        p = get_profile("youtube_short")
        assert p is YOUTUBE_SHORT

    def test_unknown_profile_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown production profile"):
            get_profile("nonexistent_profile")

    def test_all_profiles_registered(self) -> None:
        for name, profile in PROFILES.items():
            assert profile.name == name
            assert get_profile(name) is profile
