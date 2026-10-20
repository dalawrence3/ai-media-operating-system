"""Phase 5, §8-9 — visual sequence continuity.

A beat translation can mark a beat as continuing its immediate predecessor's
process; the engine tries reusing the predecessor's already-resolved real
asset before searching cold. This never touches beat timing — only which
asset a beat ends up showing — and is always a "try first, fall through if
unavailable" optimization, never a hard override.
"""

from __future__ import annotations

from pathlib import Path

from app.visuals.constants import MEDIA_GRAPHIC, MEDIA_PHOTO, MEDIA_VIDEO
from app.visuals.engine import VisualEngine, VisualEngineConfig
from app.visuals.models import VisualBeat, VisualCandidate
from app.visuals.policy import resolve_policy
from app.visuals.providers.base import ProviderCapability


class _StubProvider:
    identity = "pexels"
    capability = ProviderCapability(
        media_types=(MEDIA_VIDEO, MEDIA_PHOTO), tier=1, cost_units=0, supports_orientation=True
    )

    def __init__(self, results=None):
        self._results = results if results is not None else []
        self.searches: list[str] = []
        self.downloads: list[str] = []

    def available(self) -> bool:
        return True

    def search(self, query, *, media_type, limit=10, orientation=None):
        self.searches.append(query)
        return [c for c in self._results if c.media_type == media_type]

    def download(self, candidate, cache_dir: Path):
        self.downloads.append(candidate.asset_key)
        cache_dir.mkdir(parents=True, exist_ok=True)
        dest = cache_dir / f"{candidate.asset_key.replace(':', '_')}.mp4"
        dest.write_bytes(b"stub")
        return dest


def _engine(tmp_path: Path, providers, **kwargs) -> VisualEngine:
    config = VisualEngineConfig(
        policy=resolve_policy(),
        cache_dir=tmp_path / "cache",
        graphics_dir=tmp_path / "graphics",
        **kwargs,
    )
    return VisualEngine(config, providers=providers)


def _beat(index: int, text: str, *, continues: bool = False, entities=None) -> VisualBeat:
    return VisualBeat(
        beat_index=index,
        scene_index=0,
        scene_id=1,
        segment_id=1,
        start_ms=index * 3000,
        end_ms=(index + 1) * 3000,
        duration_ms=3000,
        narration_text=text,
        keywords=["immune", "cell"],
        entities=entities or [],
        visual_intent="action",
        media_type_preferences=[MEDIA_VIDEO, MEDIA_PHOTO, MEDIA_GRAPHIC],
        search_queries=["immune cell response"],
        continues_sequence=continues,
    )


def _candidate(asset_id="1", **overrides) -> VisualCandidate:
    fields = dict(
        provider="pexels",
        provider_asset_id=asset_id,
        media_type=MEDIA_VIDEO,
        query="immune cell response",
        width=1080,
        height=1920,
        duration_s=10.0,
        license_status="verified",
        commercial_safe=True,
        tags=["immune", "cell", "response", "antigen"],
        title="immune cell antigen response",
    )
    fields.update(overrides)
    return VisualCandidate(**fields)


class TestSequenceContinuityReuse:
    def test_continuing_beat_reuses_the_predecessors_real_asset(self, tmp_path):
        provider = _StubProvider(results=[_candidate()])
        engine = _engine(tmp_path, [provider])
        beats = [
            _beat(0, "Immune cells recognize the antigen.", entities=["antigen"]),
            _beat(1, "They begin to respond.", continues=True, entities=["antigen"]),
        ]
        plan = engine.resolve(beats)
        first, second = plan.resolutions
        assert first.asset_key is not None and first.provider != "programmatic"
        assert second.asset_key == first.asset_key
        assert second.continued_from_previous is True
        # Only one download for two beats — the second beat never searched.
        assert provider.downloads.count(first.asset_key) == 1

    def test_non_continuing_beat_never_reuses(self, tmp_path):
        provider = _StubProvider(results=[_candidate()])
        engine = _engine(tmp_path, [provider])
        beats = [
            _beat(0, "Immune cells recognize the antigen.", entities=["antigen"]),
            _beat(1, "A different independent beat.", continues=False),
        ]
        plan = engine.resolve(beats)
        assert plan.resolutions[1].continued_from_previous is False

    def test_reuse_falls_through_when_predecessor_is_a_graphic(self, tmp_path):
        provider = _StubProvider(results=[])  # nothing retrievable -> graphics
        engine = _engine(tmp_path, [provider])
        beats = [
            _beat(0, "An abstract concept with nothing to retrieve."),
            _beat(1, "The continuing beat.", continues=True),
        ]
        plan = engine.resolve(beats)
        assert plan.resolutions[0].provider == "programmatic"
        assert plan.resolutions[1].continued_from_previous is False

    def test_reuse_falls_through_across_a_scene_boundary(self, tmp_path):
        provider = _StubProvider(results=[_candidate()])
        engine = _engine(tmp_path, [provider])
        beat0 = _beat(0, "Immune cells recognize the antigen.", entities=["antigen"])
        beat1 = _beat(1, "A beat in the next scene.", continues=True, entities=["antigen"])
        beat1.scene_index = 1  # different scene: no continuity assumed across it
        plan = engine.resolve([beat0, beat1])
        assert plan.resolutions[1].continued_from_previous is False

    def test_reuse_respects_the_repetition_budget(self, tmp_path):
        provider = _StubProvider(results=[_candidate()])
        engine = _engine(tmp_path, [provider])
        # max_asset_uses_per_video defaults to 2 — three continuing beats in
        # a row must not reuse the same asset a third time.
        beats = [
            _beat(0, "Immune cells recognize the antigen.", entities=["antigen"]),
            _beat(1, "They begin to respond.", continues=True, entities=["antigen"]),
            _beat(2, "The response continues.", continues=True, entities=["antigen"]),
        ]
        plan = engine.resolve(beats)
        assert plan.resolutions[1].continued_from_previous is True
        assert plan.resolutions[2].continued_from_previous is False

    def test_reuse_does_not_alter_beat_timing(self, tmp_path):
        provider = _StubProvider(results=[_candidate()])
        engine = _engine(tmp_path, [provider])
        beats = [
            _beat(0, "Immune cells recognize the antigen.", entities=["antigen"]),
            _beat(1, "They begin to respond.", continues=True, entities=["antigen"]),
        ]
        plan = engine.resolve(beats)
        assert plan.resolutions[1].beat.start_ms == 3000
        assert plan.resolutions[1].beat.end_ms == 6000
        assert plan.resolutions[1].beat.duration_ms == 3000

    def test_reused_beat_still_counts_toward_asset_reuse_tracking(self, tmp_path):
        """A beat resolved by the reuse shortcut must still charge the
        asset's in-video usage budget, or later beats would under-count how
        much of the video it already occupies."""
        provider = _StubProvider(results=[_candidate()])
        engine = _engine(tmp_path, [provider])
        beats = [
            _beat(0, "Immune cells recognize the antigen.", entities=["antigen"]),
            _beat(1, "They begin to respond.", continues=True, entities=["antigen"]),
        ]
        engine.resolve(beats)
        asset_key = "pexels:1"
        assert engine._ctx.used_count_in_video.get(asset_key) == 2
