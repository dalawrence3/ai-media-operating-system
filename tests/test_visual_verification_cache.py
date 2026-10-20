"""Phase 3 — verification result persistence / idempotency (§7).

A restart, a rerender, or a repeated preflight must reuse an already-computed
verdict for the same (asset, intent, model, verifier version) rather than
paying for the same verification call again — but only for a genuine repeat,
never merely because the local file happens to be cached on disk.
"""

from __future__ import annotations

from app.visuals.models import BeatResolution, VisualBeat
from app.visuals.verification import (
    STATUS_REJECTED,
    STATUS_UNAVAILABLE,
    STATUS_VERIFIED,
    BeatVerification,
    verification_cache_key,
)
from app.visuals.verification_repository import verify_plan_with_cache


def _beat(index: int, text: str = "A galaxy forms.", **overrides) -> VisualBeat:
    fields = dict(
        beat_index=index,
        scene_index=0,
        scene_id=1,
        segment_id=1,
        start_ms=index * 3000,
        end_ms=(index + 1) * 3000,
        duration_ms=3000,
        narration_text=text,
        keywords=["galaxy"],
        entities=[],
        visual_intent="entity",
        media_type_preferences=["video", "photo", "graphic"],
        search_queries=["galaxy"],
        avoid_terms=[],
    )
    fields.update(overrides)
    return VisualBeat(**fields)


def _photo_resolution(index: int, tmp_path, *, asset_key="pexels:1", text=None) -> BeatResolution:
    from PIL import Image

    path = tmp_path / f"photo_{index}.png"
    Image.new("RGB", (32, 32), (5, 5, 5)).save(path)
    return BeatResolution(
        beat=_beat(index, text=text or "A galaxy forms."),
        media_type="photo",
        local_path=str(path),
        asset_key=asset_key,
        provider="pexels",
    )


class _CountingVerifier:
    """Records every call it actually receives; returns a fixed verdict."""

    def __init__(self, status: str = STATUS_VERIFIED):
        self.calls: list[list[int]] = []
        self._status = status

    def verify(self, contact_sheet, beats):
        self.calls.append([b.beat_index for b in beats])
        return [BeatVerification(b.beat_index, self._status, "ok", 0.9) for b in beats]


class TestCacheKeyIdentity:
    def test_same_asset_and_intent_produce_the_same_key(self, tmp_path):
        r1 = _photo_resolution(0, tmp_path)
        r2 = _photo_resolution(1, tmp_path)  # different beat index, same asset+text
        assert verification_cache_key(r1, model="haiku") == verification_cache_key(
            r2, model="haiku"
        )

    def test_different_asset_key_changes_the_cache_key(self, tmp_path):
        r1 = _photo_resolution(0, tmp_path, asset_key="pexels:1")
        r2 = _photo_resolution(0, tmp_path, asset_key="pexels:2")
        assert verification_cache_key(r1, model="haiku") != verification_cache_key(
            r2, model="haiku"
        )

    def test_different_narration_changes_the_cache_key(self, tmp_path):
        r1 = _photo_resolution(0, tmp_path, text="A galaxy forms.")
        r2 = _photo_resolution(0, tmp_path, text="A different narration entirely.")
        assert verification_cache_key(r1, model="haiku") != verification_cache_key(
            r2, model="haiku"
        )

    def test_different_model_changes_the_cache_key(self, tmp_path):
        r = _photo_resolution(0, tmp_path)
        assert verification_cache_key(r, model="haiku") != verification_cache_key(r, model="opus")


class TestPersistedReuse:
    def test_first_run_calls_the_verifier_and_caches_the_result(self, db, tmp_path):
        resolutions = [_photo_resolution(0, tmp_path)]
        verifier = _CountingVerifier()
        stats = verify_plan_with_cache(db, resolutions, verifier, model="haiku")
        assert stats.cache_hits == 0
        assert stats.verified_via_calls == 1
        assert len(verifier.calls) == 1
        assert resolutions[0].verification_status == STATUS_VERIFIED

    def test_second_run_on_the_same_asset_and_intent_reuses_the_cache(self, db, tmp_path):
        verifier = _CountingVerifier()
        resolutions_1 = [_photo_resolution(0, tmp_path)]
        verify_plan_with_cache(db, resolutions_1, verifier, model="haiku")

        resolutions_2 = [_photo_resolution(0, tmp_path)]  # a fresh plan, same asset+intent
        stats = verify_plan_with_cache(db, resolutions_2, verifier, model="haiku")

        assert stats.cache_hits == 1
        assert stats.verified_via_calls == 0
        assert len(verifier.calls) == 1  # no second call at all
        assert resolutions_2[0].verification_status == STATUS_VERIFIED
        assert resolutions_2[0].verification_reason == "ok"

    def test_changed_asset_causes_reverification(self, db, tmp_path):
        verifier = _CountingVerifier()
        verify_plan_with_cache(
            db, [_photo_resolution(0, tmp_path, asset_key="pexels:1")], verifier, model="haiku"
        )
        stats = verify_plan_with_cache(
            db, [_photo_resolution(0, tmp_path, asset_key="pexels:2")], verifier, model="haiku"
        )
        assert stats.cache_hits == 0
        assert stats.verified_via_calls == 1
        assert len(verifier.calls) == 2

    def test_changed_intent_causes_reverification(self, db, tmp_path):
        verifier = _CountingVerifier()
        verify_plan_with_cache(
            db,
            [_photo_resolution(0, tmp_path, text="Original narration.")],
            verifier,
            model="haiku",
        )
        stats = verify_plan_with_cache(
            db,
            [_photo_resolution(0, tmp_path, text="Completely different narration.")],
            verifier,
            model="haiku",
        )
        assert stats.cache_hits == 0
        assert stats.verified_via_calls == 1
        assert len(verifier.calls) == 2

    def test_unavailable_results_are_never_cached(self, db, tmp_path):
        """Caching "we couldn't check" would make a transient provider
        outage permanent for that asset/intent pair."""
        verifier = _CountingVerifier(status=STATUS_UNAVAILABLE)
        verify_plan_with_cache(db, [_photo_resolution(0, tmp_path)], verifier, model="haiku")
        stats = verify_plan_with_cache(
            db, [_photo_resolution(0, tmp_path)], verifier, model="haiku"
        )
        assert stats.cache_hits == 0  # not reused — retried instead
        assert len(verifier.calls) == 2

    def test_rejected_result_is_cached_and_reused(self, db, tmp_path):
        verifier = _CountingVerifier(status=STATUS_REJECTED)
        verify_plan_with_cache(db, [_photo_resolution(0, tmp_path)], verifier, model="haiku")
        resolutions_2 = [_photo_resolution(0, tmp_path)]
        stats = verify_plan_with_cache(db, resolutions_2, verifier, model="haiku")
        assert stats.cache_hits == 1
        assert resolutions_2[0].verification_status == STATUS_REJECTED

    def test_no_candidates_is_a_cheap_no_op(self, db):
        stats = verify_plan_with_cache(db, [], _CountingVerifier(), model="haiku")
        assert stats.candidates == 0
        assert stats.cache_hits == 0
        assert stats.verified_via_calls == 0
