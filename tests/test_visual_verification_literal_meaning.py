"""Phase 5, §12 — the vision verifier judges against topic + literal visual
meaning, not narration words alone.

A beat translation can make firefighter footage superficially "match" the
narration's own words ("a fire drill for your immune system") while being
the wrong educational visual. These tests pin: (1) the literal-meaning
fields actually reach the verifier's prompt when translation populated
them, (2) an untranslated beat's prompt is byte-for-byte what it was before
Phase 5 (no regression), and (3) the cache key changes when literal meaning
changes, so a re-translated beat is never served a stale verdict.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.visuals.models import BeatResolution, VisualBeat
from app.visuals.verification import (
    _VERIFICATION_SYSTEM_PROMPT,
    AnthropicVisionVerifier,
    BeatIntent,
    _build_user_prompt,
    _format_beat_intent,
    verification_cache_key,
)


def _intent(index: int = 0, text: str = "narration", **kwargs) -> BeatIntent:
    return BeatIntent(beat_index=index, narration_text=text, **kwargs)


def _beat(index: int = 0, text: str = "narration", **overrides) -> VisualBeat:
    fields = dict(
        beat_index=index,
        scene_index=0,
        scene_id=1,
        segment_id=1,
        start_ms=index * 3000,
        end_ms=(index + 1) * 3000,
        duration_ms=3000,
        narration_text=text,
    )
    fields.update(overrides)
    return VisualBeat(**fields)


def _resolution(index: int, tmp_path, **beat_overrides) -> BeatResolution:
    path = tmp_path / f"asset_{index}.mp4"
    path.write_bytes(b"stub")
    return BeatResolution(
        beat=_beat(index, **beat_overrides),
        media_type="video",
        local_path=str(path),
        asset_key=f"pexels:{index}",
        provider="pexels",
    )


class TestSystemPromptCoversLiteralMetaphorMismatch:
    def test_system_prompt_names_the_literal_metaphor_pattern(self):
        assert "literal" in _VERIFICATION_SYSTEM_PROMPT.lower()
        assert "metaphor" in _VERIFICATION_SYSTEM_PROMPT.lower()


class TestSystemPromptCoversGenericSharedObjectMismatch:
    """Phase 7A, §0 — real canary #4 finding: a chemistry teacher deriving
    the Wurtz Reaction on a whiteboard was verified as an "elegant equation"
    visual for a theoretical-physics beat, because both share the generic
    surface category "equations on a board". The rule added must be stated
    generally (object/category vs. specific-content-and-field), never naming
    chemistry/physics themselves as the enforced rule — those only appear in
    the illustrative example.
    """

    def test_system_prompt_names_the_generic_shared_object_pattern(self):
        lowered = _VERIFICATION_SYSTEM_PROMPT.lower()
        assert "generic-shared-object" in lowered or "generic shared object" in lowered
        assert "category" in lowered or "surface" in lowered

    def test_system_prompt_generalizes_beyond_chemistry_and_physics(self):
        """The illustrative example may mention chemistry/physics, but the
        RULE itself (the "ask yourself" test) must be phrased domain-
        generally, not as a hardcoded chemistry-vs-physics special case."""
        assert "would an expert in this beat" in _VERIFICATION_SYSTEM_PROMPT.lower()


class TestGenericSharedObjectWiringEndToEnd:
    """These pin the DATA PATH — that a verdict the model returns for this
    scenario shape is parsed, propagated, and turned into the correct
    disposition. They cannot unit-test the model's own judgement (that
    requires a live call, exercised for real in canary #4); they prove the
    architecture correctly carries a "questionable"/"rejected" verdict
    through when the model applies the new rule, and a "verified" verdict
    through when the visual genuinely matches the beat's own topic.
    """

    def _verify_one(self, *, status: str, topic_context: str, literal_visual_subject: str):
        response_payload = json.dumps(
            [{"beat_index": 0, "status": status, "reason": "ok", "confidence": 0.8}]
        )
        message = SimpleNamespace(
            content=[SimpleNamespace(type="text", text=response_payload)],
            model="claude-haiku-4-5-20251001",
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )
        client = MagicMock()
        client.messages.create.return_value = message
        verifier = AnthropicVisionVerifier(
            api_key="test-key", client=client, sleep_fn=lambda _: None
        )
        beats = [
            _intent(
                0,
                "String theory aims to unite quantum physics and gravity into "
                "one elegant equation.",
                topic_context=topic_context,
                literal_visual_subject=literal_visual_subject,
            )
        ]
        return verifier.verify(b"fake-png-bytes", beats)

    def test_physics_narration_with_chemistry_specific_visual_is_never_verified(self):
        """The model is expected to return "questionable" or "rejected" for
        this shape post-calibration — confirm neither disposition is
        silently upgraded to "verified" anywhere in the parsing/propagation
        path."""
        for status in ("questionable", "rejected"):
            result = self._verify_one(
                status=status,
                topic_context="theoretical physics: string theory",
                literal_visual_subject="a unified equation of quantum physics and gravity",
            )
            assert result[0].status == status
            assert result[0].status != "verified"

    def test_physics_narration_with_physics_relevant_visual_is_verified(self):
        result = self._verify_one(
            status="verified",
            topic_context="theoretical physics: string theory",
            literal_visual_subject="a unified equation of quantum physics and gravity",
        )
        assert result[0].status == "verified"


class TestFormatBeatIntent:
    def test_untranslated_beat_omits_topic_lines_entirely(self):
        intent = _intent(0, "A fire drill for your immune system.")
        formatted = _format_beat_intent(intent)
        assert "topic:" not in formatted
        assert "literal visual subject" not in formatted

    def test_translated_beat_includes_topic_and_literal_subject(self):
        intent = _intent(
            0,
            "A fire drill for your immune system.",
            topic_context="vaccines and immune response",
            literal_visual_subject="immune cells rehearsing an antigen response",
        )
        formatted = _format_beat_intent(intent)
        assert "vaccines and immune response" in formatted
        assert "immune cells rehearsing an antigen response" in formatted

    def test_either_field_alone_is_enough_to_show_the_topic_block(self):
        formatted = _format_beat_intent(_intent(0, "x", topic_context="ctx only"))
        assert "topic: ctx only" in formatted


class TestBuildUserPromptCarriesLiteralMeaningThrough:
    def test_prompt_contains_literal_subject_for_translated_beats(self):
        beats = [
            _intent(
                0,
                "A fire drill for your immune system.",
                topic_context="vaccines and immune response",
                literal_visual_subject="immune cells rehearsing an antigen response",
            )
        ]
        prompt = _build_user_prompt(beats)
        assert "immune cells rehearsing an antigen response" in prompt

    def test_mixed_translated_and_untranslated_beats_each_render_correctly(self):
        beats = [
            _intent(0, "translated one", literal_visual_subject="subject A", topic_context="ctx"),
            _intent(1, "untranslated one"),
        ]
        prompt = _build_user_prompt(beats)
        assert "subject A" in prompt
        # Beat 1 has no topic block at all.
        beat_1_section = prompt.split("Beat 1")[1]
        assert "topic:" not in beat_1_section


class TestVerifierRequestIncludesLiteralMeaning:
    def test_real_api_call_prompt_carries_literal_visual_subject(self):
        response_payload = json.dumps(
            [{"beat_index": 0, "status": "verified", "reason": "ok", "confidence": 0.9}]
        )
        message = SimpleNamespace(
            content=[SimpleNamespace(type="text", text=response_payload)],
            model="claude-haiku-4-5-20251001",
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )
        client = MagicMock()
        client.messages.create.return_value = message
        verifier = AnthropicVisionVerifier(
            api_key="test-key", client=client, sleep_fn=lambda _: None
        )

        beats = [
            _intent(
                0,
                "A fire drill for your immune system.",
                topic_context="vaccines and immune response",
                literal_visual_subject="immune cells rehearsing an antigen response",
            )
        ]
        verifier.verify(b"fake-png-bytes", beats)

        sent_kwargs = client.messages.create.call_args.kwargs
        sent_text = json.dumps(sent_kwargs)
        assert "immune cells rehearsing an antigen response" in sent_text


class TestVerificationCacheKeyReflectsLiteralMeaning:
    def test_cache_key_changes_when_literal_visual_subject_changes(self, tmp_path):
        r1 = _resolution(0, tmp_path, literal_visual_subject="immune cells")
        r2 = _resolution(1, tmp_path, literal_visual_subject="firefighters")
        key_1 = verification_cache_key(r1, model="haiku")
        key_2 = verification_cache_key(r2, model="haiku")
        assert key_1 != key_2

    def test_cache_key_changes_when_topic_context_changes(self, tmp_path):
        r1 = _resolution(0, tmp_path, topic_context="vaccines")
        r2 = _resolution(1, tmp_path, topic_context="firefighting")
        assert verification_cache_key(r1, model="haiku") != verification_cache_key(
            r2, model="haiku"
        )

    def test_cache_key_stable_when_only_beat_index_differs(self, tmp_path):
        r1 = _resolution(0, tmp_path, literal_visual_subject="immune cells")
        r2 = _resolution(1, tmp_path, literal_visual_subject="immune cells")
        # Same asset_key required for a true identity match — reuse index 0's key.
        r2 = BeatResolution(
            beat=_beat(1, literal_visual_subject="immune cells"),
            media_type="video",
            local_path=r2.local_path,
            asset_key=r1.asset_key,
            provider="pexels",
        )
        assert verification_cache_key(r1, model="haiku") == verification_cache_key(
            r2, model="haiku"
        )
