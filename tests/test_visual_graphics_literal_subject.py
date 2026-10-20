"""Phase 5, §11 — programmatic graphics use literal visual meaning.

`_render_diagram`'s no-entity fallback now prefers `beat.literal_visual_subject`
over the raw (possibly figurative) narration sentence, so a diagram beat that
translation grounded still shows the right subject even when no entity or
keyword survived beat planning. The cache key must vary with that field too,
or a re-translated beat could serve a stale graphic.
"""

from __future__ import annotations

from app.visuals.graphics import graphic_cache_key
from app.visuals.models import VisualBeat


def _beat(**overrides) -> VisualBeat:
    fields = dict(
        beat_index=0,
        scene_index=0,
        scene_id=1,
        segment_id=1,
        start_ms=0,
        end_ms=3000,
        duration_ms=3000,
        narration_text="A fire drill for your immune system.",
        visual_intent="diagram",
    )
    fields.update(overrides)
    return VisualBeat(**fields)


class TestGraphicCacheKeyReflectsLiteralSubject:
    def test_cache_key_changes_with_literal_visual_subject(self):
        untranslated = _beat()
        translated = _beat(literal_visual_subject="immune cells rehearsing an antigen response")
        key_a = graphic_cache_key(untranslated, 1080, 1920)
        key_b = graphic_cache_key(translated, 1080, 1920)
        assert key_a != key_b

    def test_cache_key_stable_for_identical_beats(self):
        beat_a = _beat(literal_visual_subject="immune cells")
        beat_b = _beat(literal_visual_subject="immune cells")
        assert graphic_cache_key(beat_a, 1080, 1920) == graphic_cache_key(beat_b, 1080, 1920)
