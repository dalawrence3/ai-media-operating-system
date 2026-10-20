"""Phase 2 — actual-content verification (§7-9, §20).

Covers: representative-frame extraction, the contact-sheet builder, the
`ContentVerifier` adapter boundary, verification result persistence onto
`BeatResolution`, batch/cost bounding, and the fail-safe default (a verifier
that never ran, or that raises, must never fabricate a verdict).
"""

from __future__ import annotations

import shutil
from io import BytesIO
from pathlib import Path

from PIL import Image

from app.visuals.composition import composition_from_plan
from app.visuals.constants import MEDIA_PHOTO, MEDIA_VIDEO
from app.visuals.models import BeatResolution, VisualBeat, VisualPlan
from app.visuals.verification import (
    MAX_BEATS_PER_CONTACT_SHEET,
    STATUS_QUESTIONABLE,
    STATUS_REJECTED,
    STATUS_UNAVAILABLE,
    STATUS_VERIFIED,
    BeatIntent,
    BeatVerification,
    NullVerifier,
    build_contact_sheet,
    extract_representative_frame,
    get_content_verifier,
    verify_plan,
)


def _beat(index: int, text: str = "A galaxy forms from gravitational collapse.") -> VisualBeat:
    return VisualBeat(
        beat_index=index,
        scene_index=0,
        scene_id=1,
        segment_id=1,
        start_ms=index * 3000,
        end_ms=(index + 1) * 3000,
        duration_ms=3000,
        narration_text=text,
        keywords=["galaxy", "collapse"],
        entities=[],
        visual_intent="entity",
        media_type_preferences=["video", "photo", "graphic"],
        search_queries=["galaxy formation"],
    )


def _photo_resolution(index: int, tmp_path: Path, *, valid: bool = True) -> BeatResolution:
    path = tmp_path / f"photo_{index}.png"
    if valid:
        Image.new("RGB", (64, 64), (10, 20, 30)).save(path)
    return BeatResolution(
        beat=_beat(index),
        media_type=MEDIA_PHOTO,
        local_path=str(path) if valid else str(tmp_path / "missing.png"),
        asset_key=f"pexels:{index}",
        provider="pexels",
    )


def _graphic_resolution(index: int) -> BeatResolution:
    return BeatResolution(
        beat=_beat(index),
        media_type="graphic",
        local_path="/some/graphic.png",
        asset_key=f"programmatic:{index}",
        provider="programmatic",
    )


class _FakeVerifier:
    def __init__(self, verdicts_by_beat: dict[int, BeatVerification] | None = None, raises=False):
        self._verdicts = verdicts_by_beat or {}
        self._raises = raises
        self.calls: list[list[int]] = []

    def verify(self, contact_sheet: bytes, beats: list[BeatIntent]) -> list[BeatVerification]:
        self.calls.append([b.beat_index for b in beats])
        if self._raises:
            raise RuntimeError("verifier outage")
        return [
            self._verdicts.get(
                b.beat_index, BeatVerification(b.beat_index, STATUS_VERIFIED, "on-topic", 0.9)
            )
            for b in beats
        ]


class TestFrameExtraction:
    def test_generated_graphic_is_never_a_verification_candidate(self, tmp_path):
        resolution = _graphic_resolution(0)
        assert extract_representative_frame(resolution) is None

    def test_missing_local_file_returns_none(self, tmp_path):
        resolution = _photo_resolution(0, tmp_path, valid=False)
        assert extract_representative_frame(resolution) is None

    def test_photo_file_is_read_and_returned_as_png_bytes(self, tmp_path):
        resolution = _photo_resolution(0, tmp_path)
        frame = extract_representative_frame(resolution)
        assert frame is not None
        with Image.open(BytesIO(frame)) as img:
            assert img.size == (64, 64)

    def test_video_frame_extraction_fails_closed_without_ffmpeg(self, tmp_path, monkeypatch):
        video_path = tmp_path / "clip.mp4"
        video_path.write_bytes(b"not a real video")
        resolution = BeatResolution(
            beat=_beat(0),
            media_type=MEDIA_VIDEO,
            local_path=str(video_path),
            asset_key="pexels:1",
            provider="pexels",
        )
        monkeypatch.setattr(shutil, "which", lambda _bin: None)
        assert extract_representative_frame(resolution) is None


class TestContactSheet:
    def test_empty_frame_list_returns_none(self):
        assert build_contact_sheet([]) is None

    def test_builds_a_labelled_grid(self, tmp_path):
        frame = extract_representative_frame(_photo_resolution(0, tmp_path))
        sheet = build_contact_sheet([(0, frame), (1, frame), (2, frame)])
        assert sheet is not None
        with Image.open(BytesIO(sheet)) as img:
            assert img.width > 0 and img.height > 0

    def test_a_corrupt_frame_does_not_abort_the_whole_sheet(self):
        sheet = build_contact_sheet([(0, b"not an image")])
        assert sheet is not None  # the cell is skipped, not fatal


class TestNullVerifierDefault:
    def test_null_verifier_marks_everything_unavailable_never_verified(self):
        verifier = NullVerifier()
        beats = [BeatIntent(beat_index=i, narration_text="x") for i in range(3)]
        verdicts = verifier.verify(b"", beats)
        assert all(v.status == STATUS_UNAVAILABLE for v in verdicts)
        assert not any(v.status == STATUS_VERIFIED for v in verdicts)

    def test_factory_returns_a_verifier_that_never_fabricates_verified(self):
        """The shipped default (§20): enabling the policy flag today costs
        nothing and never marks anything "verified" or "rejected" without a
        real vision-backed implementation registered."""
        verifier = get_content_verifier()
        verdicts = verifier.verify(b"", [BeatIntent(beat_index=0, narration_text="x")])
        assert verdicts[0].status == STATUS_UNAVAILABLE


class TestVerifyPlanPersistence:
    def test_generated_graphics_are_skipped_entirely(self, tmp_path):
        resolutions = [_graphic_resolution(0)]
        written = verify_plan(resolutions, _FakeVerifier())
        assert written == 0
        assert resolutions[0].verification_status is None

    def test_verdicts_are_written_onto_the_matching_resolution(self, tmp_path):
        resolutions = [_photo_resolution(0, tmp_path), _photo_resolution(1, tmp_path)]
        verifier = _FakeVerifier(
            {1: BeatVerification(1, STATUS_REJECTED, "shows unrelated content", 0.1)}
        )
        written = verify_plan(resolutions, verifier)
        assert written == 2
        assert resolutions[0].verification_status == STATUS_VERIFIED
        assert resolutions[1].verification_status == STATUS_REJECTED
        assert resolutions[1].verification_reason == "shows unrelated content"
        assert resolutions[1].visual_relevance_confidence == 0.1

    def test_rejected_verdict_surfaces_as_a_severe_mismatch_in_composition(self, tmp_path):
        resolutions = [_photo_resolution(0, tmp_path), _photo_resolution(1, tmp_path)]
        verifier = _FakeVerifier({1: BeatVerification(1, STATUS_REJECTED, "mismatch", 0.1)})
        verify_plan(resolutions, verifier)
        plan = VisualPlan(
            scene_manifest_id=1,
            topic_id=1,
            experiment_id=None,
            channel_key=None,
            engine_version="1.0",
            planner_version="1.0",
            beats=[r.beat for r in resolutions],
            resolutions=resolutions,
        )
        comp = composition_from_plan(plan)
        assert comp.severe_mismatch_count == 1

    def test_questionable_verdict_is_not_a_severe_mismatch(self, tmp_path):
        resolutions = [_photo_resolution(0, tmp_path)]
        verifier = _FakeVerifier({0: BeatVerification(0, STATUS_QUESTIONABLE, "unclear", 0.5)})
        verify_plan(resolutions, verifier)
        assert resolutions[0].verification_status == STATUS_QUESTIONABLE

    def test_verifier_exception_leaves_beats_unmeasured_not_rejected(self, tmp_path):
        """A verifier outage must never fail the render or fabricate a
        mismatch — it must look exactly like verification never ran."""
        resolutions = [_photo_resolution(0, tmp_path)]
        written = verify_plan(resolutions, _FakeVerifier(raises=True))
        assert written == 0
        assert resolutions[0].verification_status is None

    def test_missing_frame_leaves_that_beat_unmeasured(self, tmp_path):
        resolutions = [_photo_resolution(0, tmp_path, valid=False)]
        written = verify_plan(resolutions, _FakeVerifier())
        assert written == 0
        assert resolutions[0].verification_status is None


class TestCostBounding:
    def test_calls_are_chunked_at_the_bound(self, tmp_path):
        count = MAX_BEATS_PER_CONTACT_SHEET + 5
        resolutions = [_photo_resolution(i, tmp_path) for i in range(count)]
        verifier = _FakeVerifier()
        verify_plan(resolutions, verifier)
        assert len(verifier.calls) == 2  # one full chunk + one partial
        assert len(verifier.calls[0]) == MAX_BEATS_PER_CONTACT_SHEET
        assert len(verifier.calls[1]) == 5

    def test_a_small_video_costs_exactly_one_verifier_call(self, tmp_path):
        resolutions = [_photo_resolution(i, tmp_path) for i in range(6)]
        verifier = _FakeVerifier()
        verify_plan(resolutions, verifier)
        assert len(verifier.calls) == 1

    def test_re_verification_after_repair_only_covers_remediated_beats(self, tmp_path):
        """Mirrors how stage_executors calls verify_plan a second time after
        engine.remediate() — only resolutions flagged `remediated` are passed,
        so a repair of 2 beats out of 20 costs one small call, not a second
        full-plan pass."""
        resolutions = [_photo_resolution(i, tmp_path) for i in range(20)]
        resolutions[3].remediated = True
        resolutions[7].remediated = True
        verifier = _FakeVerifier()
        verify_plan([r for r in resolutions if r.remediated], verifier)
        assert verifier.calls == [[3, 7]]
