"""Phase 7C.1, Part C — format-aware visual quality profiles.

Reuses the exact BeatComposition/compose() builders from
test_visual_quality_18e.py's own pattern — no second assessment engine,
just quality_profile_for() choosing which VisualQualityThresholds
assess_composition() runs against.
"""

from __future__ import annotations

from app.visuals.composition import (
    FAMILY_MOTION_FOOTAGE,
    FAMILY_TEXT_CARD,
    BeatComposition,
    classify_fallback,
    compose,
)
from app.visuals.quality import (
    DEFAULT_THRESHOLDS,
    VQ_BLOCKED,
    assess_composition,
    quality_profile_for,
)


def _beat(
    index: int,
    *,
    start_ms: int,
    duration_ms: int = 4_000,
    family: str = FAMILY_MOTION_FOOTAGE,
    planned: str = FAMILY_MOTION_FOOTAGE,
    provider: str = "pexels",
    fallback_reason: str | None = None,
    verification_status: str | None = None,
) -> BeatComposition:
    return BeatComposition(
        beat_index=index,
        scene_index=0,
        start_ms=start_ms,
        end_ms=start_ms + duration_ms,
        duration_ms=duration_ms,
        visual_intent="entity",
        planned_family=planned,
        realized_family=family,
        asset_key=f"{provider}:{index}",
        provider=provider,
        fallback_reason=fallback_reason,
        fallback_class=classify_fallback(fallback_reason),
        verification_status=verification_status,
    )


def _intentional_quiz_composition(n: int = 8):
    """A well-executed quiz: prompt/reveal cards were PLANNED as text from
    the start (never attempted retrieval, never a fallback at all) — the
    exact shape a real interactive_recall_quiz render produces."""
    beats = [
        _beat(
            i,
            start_ms=i * 4_000,
            family=FAMILY_TEXT_CARD,
            planned=FAMILY_TEXT_CARD,
            provider="programmatic",
        )
        for i in range(n)
    ]
    return compose(beats)


def _genuine_retrieval_failure_composition(n: int = 8, failing: int = 6):
    """The failure mode that must still block regardless of format: the
    plan WANTED photographic/motion footage but retrieval failed."""
    beats = []
    for i in range(n):
        if i < (n - failing):
            beats.append(_beat(i, start_ms=i * 4_000))
        else:
            beats.append(
                _beat(
                    i,
                    start_ms=i * 4_000,
                    family=FAMILY_TEXT_CARD,
                    planned=FAMILY_MOTION_FOOTAGE,
                    provider="programmatic",
                    fallback_reason="all_candidates_rejected",
                )
            )
    return compose(beats)


class TestQualityProfileForResolvesCorrectThresholds:
    def test_explainer_gets_default_thresholds_unchanged(self):
        assert quality_profile_for("curiosity_explainer") is DEFAULT_THRESHOLDS

    def test_unlinked_or_legacy_topic_gets_default_thresholds(self):
        assert quality_profile_for(None) is DEFAULT_THRESHOLDS
        assert quality_profile_for("") is DEFAULT_THRESHOLDS

    def test_unsupported_seed_format_gets_default_thresholds(self):
        """No dedicated profile exists for e.g. 'myth_bust' — falls back to
        the current hardened standard, same as the explainer."""
        assert quality_profile_for("myth_bust") is DEFAULT_THRESHOLDS

    def test_quiz_and_lesson_get_distinct_relaxed_profiles(self):
        quiz = quality_profile_for("interactive_recall_quiz")
        lesson = quality_profile_for("practical_micro_lesson")
        assert quiz is not DEFAULT_THRESHOLDS
        assert lesson is not DEFAULT_THRESHOLDS
        assert quiz.min_meaningful_runtime_pct < DEFAULT_THRESHOLDS.min_meaningful_runtime_pct
        assert lesson.min_meaningful_runtime_pct < DEFAULT_THRESHOLDS.min_meaningful_runtime_pct


class TestFailureFloorsNeverRelaxForAnyFormat:
    """§12/§13 — 'no accidental unplanned fallback', 'no severe mismatch',
    'still penalize generic fallback' must hold identically across every
    format's profile. Only creative-content floors may differ."""

    def test_provider_fallback_floor_identical_across_all_profiles(self):
        for fmt in ("curiosity_explainer", "interactive_recall_quiz", "practical_micro_lesson"):
            profile = quality_profile_for(fmt)
            assert (
                profile.max_provider_fallback_rate == DEFAULT_THRESHOLDS.max_provider_fallback_rate
            )
            assert (
                profile.warn_provider_fallback_rate
                == DEFAULT_THRESHOLDS.warn_provider_fallback_rate
            )

    def test_severe_mismatch_floor_identical_across_all_profiles(self):
        for fmt in ("curiosity_explainer", "interactive_recall_quiz", "practical_micro_lesson"):
            profile = quality_profile_for(fmt)
            assert profile.max_severe_mismatch_count == DEFAULT_THRESHOLDS.max_severe_mismatch_count

    def test_genuine_retrieval_failure_still_blocks_under_quiz_profile(self):
        """A quiz whose beats WANTED photographic footage but retrieval
        genuinely failed must still block — relaxing the quiz profile must
        never hide a real failure."""
        comp = _genuine_retrieval_failure_composition(n=8, failing=6)
        assessment = assess_composition(
            comp, thresholds=quality_profile_for("interactive_recall_quiz")
        )
        assert assessment.status == VQ_BLOCKED

    def test_genuine_retrieval_failure_blocks_under_every_profile(self):
        comp = _genuine_retrieval_failure_composition(n=8, failing=6)
        for fmt in ("curiosity_explainer", "interactive_recall_quiz", "practical_micro_lesson"):
            assessment = assess_composition(comp, thresholds=quality_profile_for(fmt))
            assert assessment.status == VQ_BLOCKED, f"{fmt} failed to block a real failure"


class TestIntentionalQuizTextPassesUnderQuizProfileButNotExplainer:
    def test_intentional_quiz_composition_passes_under_quiz_profile(self):
        comp = _intentional_quiz_composition(n=8)
        assessment = assess_composition(
            comp, thresholds=quality_profile_for("interactive_recall_quiz")
        )
        assert assessment.status != VQ_BLOCKED

    def test_the_same_composition_would_block_under_the_explainer_profile(self):
        """Proves the relaxation is real and format-specific — not that
        the assessment engine itself got weaker for everyone."""
        comp = _intentional_quiz_composition(n=8)
        assessment = assess_composition(comp, thresholds=quality_profile_for("curiosity_explainer"))
        assert assessment.status == VQ_BLOCKED


class TestExplainerRegressionUnchanged:
    """§11 — no regression: an explainer-format composition that used to
    block must still block, identically, after this change."""

    def test_explainer_profile_blocks_exactly_like_default_thresholds(self):
        comp = _genuine_retrieval_failure_composition(n=8, failing=6)
        via_profile = assess_composition(
            comp, thresholds=quality_profile_for("curiosity_explainer")
        )
        via_default = assess_composition(comp, thresholds=DEFAULT_THRESHOLDS)
        assert via_profile.status == via_default.status == VQ_BLOCKED
        assert len(via_profile.findings) == len(via_default.findings)


class TestSevereMismatchAlwaysBlocksRegardlessOfFormat:
    def test_severe_mismatch_blocks_even_under_the_most_relaxed_profile(self):
        beats = [
            _beat(
                i,
                start_ms=i * 4_000,
                family=FAMILY_TEXT_CARD,
                planned=FAMILY_TEXT_CARD,
                provider="programmatic",
                verification_status="rejected" if i == 0 else None,
            )
            for i in range(8)
        ]
        comp = compose(beats)
        assessment = assess_composition(
            comp, thresholds=quality_profile_for("interactive_recall_quiz")
        )
        assert assessment.status == VQ_BLOCKED
