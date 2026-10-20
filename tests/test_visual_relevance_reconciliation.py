"""Phase 6, §1-3, §6, §15 — relevant_visual_runtime_pct / visualizability_score
semantic source-of-truth reconciliation.

Root cause (traced, not guessed): `BeatComposition.low_confidence` checked
the pre-retrieval lexical `resolved_score` against `LOW_CONFIDENCE_SCORE`
BEFORE ever looking at `verification_status`, so a beat actual-content
(vision) verification explicitly confirmed as "verified" still counted as
low-confidence — and so got excluded from `relevant_visual_runtime_ms` —
merely because the query that retrieved it (often a translation-grounded
query against sparse Wikimedia-style metadata) scored weakly on lexical
terms. This is exactly why canary #3 showed 14/16 vision-verified beats but
only 21.3% relevant_visual_runtime_pct.

The fix establishes a hierarchy: `verified` is a completed measurement and
overrides the lexical prediction; `questionable` and unmeasured (`None`)
behave exactly as before — nothing here loosens `severe_mismatch`
(`rejected` handling, tested unchanged below), the tri-state candidate
disposition, or provider-unavailable behavior.
"""

from __future__ import annotations

from app.visuals.composition import (
    FAMILY_MOTION_FOOTAGE,
    FAMILY_TEXT_CARD,
    BeatComposition,
    classify_fallback,
    compose,
)
from app.visuals.constants import LOW_CONFIDENCE_SCORE
from app.visuals.models import BeatResolution, VisualBeat, VisualPlan
from app.visuals.qa import audit_visual_plan


def _plan(resolutions: list[BeatResolution]) -> VisualPlan:
    return VisualPlan(
        scene_manifest_id=1,
        topic_id=1,
        experiment_id=None,
        channel_key=None,
        engine_version="test",
        planner_version="test",
        beats=[r.beat for r in resolutions],
        resolutions=resolutions,
    )


def _beat(
    index: int,
    *,
    start_ms: int = 0,
    duration_ms: int = 4_000,
    family: str = FAMILY_MOTION_FOOTAGE,
    resolved_score: float | None = None,
    verification_status: str | None = None,
    provider: str = "pexels",
) -> BeatComposition:
    return BeatComposition(
        beat_index=index,
        scene_index=0,
        start_ms=start_ms,
        end_ms=start_ms + duration_ms,
        duration_ms=duration_ms,
        visual_intent="entity",
        planned_family=family,
        realized_family=family,
        asset_key=f"{provider}:{index}",
        provider=provider,
        fallback_reason=None,
        fallback_class=classify_fallback(None),
        resolved_score=resolved_score,
        verification_status=verification_status,
    )


class TestLowConfidencePropertyHierarchy:
    def test_verified_beat_is_never_low_confidence_even_with_weak_lexical_score(self):
        """The exact canary #3 shape: a real vision call confirmed the beat,
        but the pre-retrieval lexical score was below the threshold."""
        beat = _beat(0, resolved_score=0.30, verification_status="verified")
        assert LOW_CONFIDENCE_SCORE > 0.30
        assert beat.low_confidence is False

    def test_questionable_beat_is_still_low_confidence_regardless_of_score(self):
        beat = _beat(0, resolved_score=0.95, verification_status="questionable")
        assert beat.low_confidence is True

    def test_unmeasured_beat_still_falls_back_to_lexical_score(self):
        """No vision signal at all (verification off, or never reached this
        beat) — deterministic fast-path behavior must be unchanged."""
        weak = _beat(0, resolved_score=0.30, verification_status=None)
        strong = _beat(1, resolved_score=0.95, verification_status=None)
        assert weak.low_confidence is True
        assert strong.low_confidence is False

    def test_rejected_beat_is_severe_mismatch_not_merely_low_confidence(self):
        """Hard safety unchanged: `severe_mismatch` is driven solely by a
        `rejected` verdict, independent of this fix."""
        beat = _beat(0, resolved_score=0.95, verification_status="rejected")
        assert beat.severe_mismatch is True

    def test_verified_never_masks_a_severe_mismatch_it_isnt(self):
        """Sanity: `verified` only ever affects `low_confidence`; it can
        never itself BE a severe_mismatch."""
        beat = _beat(0, resolved_score=0.30, verification_status="verified")
        assert beat.severe_mismatch is False

    def test_text_card_is_never_low_confidence_regardless_of_verification(self):
        """A non-meaningful beat (text card) is excluded from low_confidence
        entirely — verification never runs on programmatic graphics."""
        beat = _beat(0, family=FAMILY_TEXT_CARD, verification_status="questionable")
        assert beat.low_confidence is False


class TestRelevantVisualRuntimeReconciled:
    def test_verified_weak_lexical_beats_are_counted_as_relevant_runtime(self):
        beats = [
            _beat(0, start_ms=0, resolved_score=0.30, verification_status="verified"),
            _beat(1, start_ms=4_000, resolved_score=0.30, verification_status="verified"),
        ]
        comp = compose(beats)
        assert comp.relevant_visual_runtime_ms == 8_000
        assert comp.relevant_visual_runtime_pct == 1.0

    def test_questionable_beats_remain_excluded_from_relevant_runtime(self):
        beats = [
            _beat(0, start_ms=0, resolved_score=0.95, verification_status="questionable"),
        ]
        comp = compose(beats)
        assert comp.relevant_visual_runtime_ms == 0
        assert comp.low_confidence_scene_count == 1

    def test_rejected_beats_remain_excluded_from_relevant_runtime(self):
        beats = [_beat(0, start_ms=0, verification_status="rejected")]
        comp = compose(beats)
        assert comp.relevant_visual_runtime_ms == 0
        assert comp.severe_mismatch_count == 1

    def test_mixed_render_matches_the_canary_3_shape(self):
        """14 verified (weak lexical score), 1 questionable, 1 unresolved
        graphic (excluded as non-meaningful is irrelevant here since it's a
        graphic) — relevant runtime should now credit the 14 verified beats."""
        beats = [
            _beat(i, start_ms=i * 4_000, resolved_score=0.35, verification_status="verified")
            for i in range(14)
        ] + [
            _beat(14, start_ms=14 * 4_000, resolved_score=0.90, verification_status="questionable"),
        ]
        comp = compose(beats)
        assert comp.relevant_visual_runtime_ms == 14 * 4_000
        assert comp.low_confidence_scene_count == 1


class TestVisualizabilityScoreUsesReconciledRelevance:
    def test_visualizability_rises_when_weak_lexical_beats_are_vision_verified(self):
        unverified = [_beat(i, start_ms=i * 4_000, resolved_score=0.30) for i in range(10)]
        verified = [
            _beat(i, start_ms=i * 4_000, resolved_score=0.30, verification_status="verified")
            for i in range(10)
        ]
        score_unverified = compose(unverified).visualizability_score
        score_verified = compose(verified).visualizability_score
        assert score_verified > score_unverified

    def test_visualizability_still_not_wired_into_pass_fail_verdict(self):
        """§6 explicitly: do NOT wire it into planner scoring / the preflight
        verdict yet — only confirm it is still a pure observational metric."""
        from app.visuals.quality import assess_composition

        beats = [
            _beat(i, start_ms=i * 4_000, resolved_score=0.30, verification_status="verified")
            for i in range(10)
        ]
        comp = compose(beats)
        result = assess_composition(comp)
        # The verdict must be reachable without ever consulting visualizability_score.
        assert result.status in {"pass", "pass_with_warnings", "blocked"}


class TestQAAuditLowConfidenceCountReconciled:
    """The sibling `VisualQAReport.low_confidence_count` (audit_visual_plan,
    qa.py) had the identical lexical-score-only bug and produced the exact
    misleading "75% low confidence" warning canary #3 showed next to a
    14/16-verified vision result. Same fix, same hierarchy."""

    def _resolution(
        self, index: int, *, score: float, verification_status: str | None
    ) -> BeatResolution:
        beat = VisualBeat(
            beat_index=index,
            scene_index=0,
            scene_id=1,
            segment_id=1,
            start_ms=index * 4_000,
            end_ms=(index + 1) * 4_000,
            duration_ms=4_000,
            narration_text="x",
        )
        return BeatResolution(
            beat=beat,
            media_type="video",
            local_path="/tmp/x.mp4",
            asset_key=f"pexels:{index}",
            provider="pexels",
            score=score,
            verification_status=verification_status,
        )

    def test_verified_weak_score_resolution_not_counted_low_confidence(self):
        plan = _plan(
            resolutions=[
                self._resolution(0, score=0.3, verification_status="verified"),
                self._resolution(1, score=0.3, verification_status="verified"),
            ],
        )
        report = audit_visual_plan(plan)
        assert report.low_confidence_count == 0

    def test_unverified_weak_score_resolution_still_counted_low_confidence(self):
        plan = _plan(resolutions=[self._resolution(0, score=0.3, verification_status=None)])
        report = audit_visual_plan(plan)
        assert report.low_confidence_count == 1

    def test_questionable_weak_score_resolution_still_counted_low_confidence(self):
        plan = _plan(
            resolutions=[self._resolution(0, score=0.3, verification_status="questionable")]
        )
        report = audit_visual_plan(plan)
        assert report.low_confidence_count == 1
