"""Production profile: a named bundle of format constraints for a distribution target."""

from __future__ import annotations

from dataclasses import dataclass

from app.content.constants import SCRIPT_WORDS_PER_MINUTE


@dataclass(frozen=True)
class ProductionProfile:
    """Immutable set of constraints that define a distribution target.

    Carries both content constraints (duration, word count) and render
    constraints (resolution, frame rate) so every pipeline stage reads from
    one source of truth rather than scattering platform-specific constants.
    """

    name: str
    target_duration_s: int
    min_duration_s: int
    max_duration_s: int
    width: int
    height: int
    fps: int
    max_segment_duration_s: int | None = None  # None = no intra-section splitting

    @property
    def target_word_count(self) -> int:
        return round(self.target_duration_s * SCRIPT_WORDS_PER_MINUTE / 60)

    @property
    def min_word_count(self) -> int:
        return round(self.min_duration_s * SCRIPT_WORDS_PER_MINUTE / 60)

    @property
    def max_word_count(self) -> int:
        return round(self.max_duration_s * SCRIPT_WORDS_PER_MINUTE / 60)
