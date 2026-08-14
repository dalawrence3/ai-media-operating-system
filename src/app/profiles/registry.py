"""Named production profiles.

Add new platform targets here only — no pipeline code should contain
resolution or duration literals; it should call get_profile() instead.
"""

from __future__ import annotations

from app.media.constants import DEFAULT_FPS, RESOLUTION_SHORTS
from app.profiles.models import ProductionProfile

YOUTUBE_SHORT = ProductionProfile(
    name="youtube_short",
    target_duration_s=45,
    min_duration_s=35,
    max_duration_s=55,
    width=RESOLUTION_SHORTS[0],
    height=RESOLUTION_SHORTS[1],
    fps=DEFAULT_FPS,
    max_segment_duration_s=8,
)

PROFILES: dict[str, ProductionProfile] = {
    YOUTUBE_SHORT.name: YOUTUBE_SHORT,
}


def get_profile(name: str) -> ProductionProfile:
    """Return the named profile or raise ValueError."""
    if name not in PROFILES:
        raise ValueError(f"Unknown production profile: {name!r}. Available: {sorted(PROFILES)}")
    return PROFILES[name]
