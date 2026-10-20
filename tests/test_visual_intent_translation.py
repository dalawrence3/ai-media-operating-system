"""Phase 5, §5-9 — unit tests for `intent_translation.py`'s pure functions.

`test_visual_intent_translation_wiring.py` proves the module is actually
called from the real render path; these tests pin the module's own
contract in isolation: the env-var gate, the one-call cost bound, strict
fail-closed validation of the structured response, and the merge semantics
`apply_visual_intent_translation` uses to layer literal meaning onto
`plan_beats()`'s deterministic output without ever discarding it.
"""

from __future__ import annotations

from app.ai.provider import AIResponse
from app.visuals import intent_translation
from app.visuals.intent_translation import (
    DEFAULT_TRANSLATION_MODEL,
    MAX_BEATS_PER_TRANSLATION_CALL,
    BeatVisualMeaning,
    _BeatTranslation,
    _TranslationOutput,
    apply_visual_intent_translation,
    translate_visual_intents,
    translation_enabled,
    translation_model,
)
from app.visuals.models import VisualBeat


def _beat(index: int, text: str = "Some narration.", **overrides) -> VisualBeat:
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


class _FakeProvider:
    """Mirrors ClaudeProvider's `.complete(request) -> AIResponse` contract."""

    def __init__(self, parsed=None, raises=None):
        self._parsed = parsed
        self._raises = raises
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        if self._raises is not None:
            raise self._raises
        return AIResponse(
            raw_text="{}",
            provider_name="fake",
            model=request.model,
            input_tokens=0,
            output_tokens=0,
            duration_ms=0,
            retry_count=0,
            parsed=self._parsed,
        )


def _translate(beats, provider):
    return translate_visual_intents(beats, topic_title="t", topic_angle="a", ai_provider=provider)


class TestTranslationEnabled:
    def test_unset_is_disabled(self, monkeypatch):
        monkeypatch.delenv(intent_translation.ENV_TRANSLATION_ENABLED, raising=False)
        assert translation_enabled() is False

    def test_true_variants_enable(self, monkeypatch):
        for value in ("1", "true", "True", "yes", "YES"):
            monkeypatch.setenv(intent_translation.ENV_TRANSLATION_ENABLED, value)
            assert translation_enabled() is True

    def test_other_values_stay_disabled(self, monkeypatch):
        monkeypatch.setenv(intent_translation.ENV_TRANSLATION_ENABLED, "0")
        assert translation_enabled() is False
        monkeypatch.setenv(intent_translation.ENV_TRANSLATION_ENABLED, "nope")
        assert translation_enabled() is False


class TestTranslationModel:
    def test_defaults_when_unset(self, monkeypatch):
        monkeypatch.delenv(intent_translation.ENV_TRANSLATION_MODEL, raising=False)
        assert translation_model() == DEFAULT_TRANSLATION_MODEL

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv(intent_translation.ENV_TRANSLATION_MODEL, "claude-sonnet-5")
        assert translation_model() == "claude-sonnet-5"


class TestTranslateVisualIntentsFailOpen:
    def test_no_beats_returns_empty(self):
        assert _translate([], _FakeProvider()) == {}

    def test_no_provider_returns_empty(self):
        assert _translate([_beat(0)], None) == {}

    def test_provider_exception_returns_empty(self):
        beats = [_beat(0)]
        provider = _FakeProvider(raises=RuntimeError("network down"))
        assert _translate(beats, provider) == {}

    def test_beat_index_mismatch_discards_whole_response(self):
        beats = [_beat(0), _beat(1)]
        # Response only covers beat 0 — expected {0, 1} != got {0}.
        output = _TranslationOutput(
            topic_context="ctx",
            beats=[_BeatTranslation(beat_index=0, literal_visual_subject="subject")],
        )
        assert _translate(beats, _FakeProvider(parsed=output)) == {}

    def test_extra_beat_index_in_response_also_discards(self):
        beats = [_beat(0)]
        output = _TranslationOutput(
            topic_context="ctx",
            beats=[
                _BeatTranslation(beat_index=0, literal_visual_subject="subject"),
                _BeatTranslation(beat_index=99, literal_visual_subject="phantom"),
            ],
        )
        assert _translate(beats, _FakeProvider(parsed=output)) == {}


class TestTranslateVisualIntentsSuccess:
    def test_matching_response_is_returned_keyed_by_beat_index(self):
        beats = [_beat(0, "A fire drill for your immune system.")]
        output = _TranslationOutput(
            topic_context="vaccines and immune response",
            beats=[
                _BeatTranslation(
                    beat_index=0,
                    literal_visual_subject="immune cells rehearsing a response",
                    process_or_action="rehearsal",
                    concrete_entities=["immune cell", "antigen"],
                    preferred_visual_types=["video", "illustration"],
                    avoid_literal_interpretations=["firefighters"],
                    continues_previous=True,
                )
            ],
        )
        result = _translate(beats, _FakeProvider(parsed=output))
        assert set(result) == {0}
        meaning = result[0]
        assert isinstance(meaning, BeatVisualMeaning)
        assert meaning.literal_visual_subject == "immune cells rehearsing a response"
        assert meaning.topic_context == "vaccines and immune response"
        assert meaning.concrete_entities == ["immune cell", "antigen"]
        assert meaning.continues_previous is True

    def test_unknown_media_type_is_filtered_out(self):
        beats = [_beat(0)]
        output = _TranslationOutput(
            topic_context="ctx",
            beats=[
                _BeatTranslation(
                    beat_index=0,
                    literal_visual_subject="subject",
                    preferred_visual_types=["video", "hologram"],
                )
            ],
        )
        result = _translate(beats, _FakeProvider(parsed=output))
        assert result[0].preferred_visual_types == ["video"]

    def test_blank_strings_in_lists_are_dropped(self):
        beats = [_beat(0)]
        output = _TranslationOutput(
            topic_context="ctx",
            beats=[
                _BeatTranslation(
                    beat_index=0,
                    literal_visual_subject="subject",
                    concrete_entities=["real", "  ", ""],
                    search_concepts=["", "concept"],
                    avoid_literal_interpretations=["  ", "avoid-me"],
                )
            ],
        )
        result = _translate(beats, _FakeProvider(parsed=output))
        assert result[0].concrete_entities == ["real"]
        assert result[0].search_concepts == ["concept"]
        assert result[0].avoid_literal_interpretations == ["avoid-me"]

    def test_max_tokens_scales_with_beat_count(self):
        """Regression: canary #3's real 16-beat call truncated mid-JSON and
        failed the strict parse because the request used AIRequest's 1024
        default regardless of beat count. max_tokens must grow with the
        number of beats being translated in one call."""
        beats = [_beat(i) for i in range(16)]
        output = _TranslationOutput(
            topic_context="ctx",
            beats=[
                _BeatTranslation(beat_index=i, literal_visual_subject=f"subject {i}")
                for i in range(16)
            ],
        )
        provider = _FakeProvider(parsed=output)
        _translate(beats, provider)
        assert len(provider.requests) == 1
        assert provider.requests[0].max_tokens > 1024

    def test_only_first_max_beats_are_sent_and_expected(self):
        beats = [_beat(i) for i in range(MAX_BEATS_PER_TRANSLATION_CALL + 5)]
        translations = [
            _BeatTranslation(beat_index=i, literal_visual_subject=f"subject {i}")
            for i in range(MAX_BEATS_PER_TRANSLATION_CALL)
        ]
        output = _TranslationOutput(topic_context="ctx", beats=translations)
        result = _translate(beats, _FakeProvider(parsed=output))
        assert len(result) == MAX_BEATS_PER_TRANSLATION_CALL
        assert (MAX_BEATS_PER_TRANSLATION_CALL + 1) not in result


class TestApplyVisualIntentTranslation:
    def test_empty_translations_returns_beats_unchanged(self):
        beats = [_beat(0)]
        result = apply_visual_intent_translation(beats, {})
        assert result is beats

    def test_beat_with_no_matching_translation_is_unchanged(self):
        beats = [_beat(0), _beat(1)]
        translations = {
            0: BeatVisualMeaning(
                beat_index=0, topic_context="ctx", literal_visual_subject="subject"
            )
        }
        result = apply_visual_intent_translation(beats, translations)
        assert result[1] is beats[1]
        assert result[1].literal_visual_subject == ""

    def test_translation_with_empty_literal_subject_is_skipped(self):
        beats = [_beat(0)]
        translations = {
            0: BeatVisualMeaning(beat_index=0, topic_context="ctx", literal_visual_subject="")
        }
        result = apply_visual_intent_translation(beats, translations)
        assert result[0] is beats[0]

    def test_entities_and_avoid_terms_are_extended_not_replaced(self):
        beats = [_beat(0, entities=["existing_entity"], avoid_terms=["existing_avoid"])]
        translations = {
            0: BeatVisualMeaning(
                beat_index=0,
                topic_context="ctx",
                literal_visual_subject="subject",
                concrete_entities=["existing_entity", "new_entity"],
                avoid_literal_interpretations=["new_avoid"],
            )
        }
        result = apply_visual_intent_translation(beats, translations)
        assert result[0].entities == ["existing_entity", "new_entity"]
        assert result[0].avoid_terms == ["existing_avoid", "new_avoid"]

    def test_media_type_preferences_broadened_and_led_by_translation(self):
        from app.visuals.constants import (
            MEDIA_GRAPHIC,
            MEDIA_ILLUSTRATION,
            MEDIA_PHOTO,
            MEDIA_VIDEO,
        )

        beats = [_beat(0, media_type_preferences=[MEDIA_VIDEO, MEDIA_PHOTO])]
        translations = {
            0: BeatVisualMeaning(
                beat_index=0,
                topic_context="ctx",
                literal_visual_subject="subject",
                preferred_visual_types=[MEDIA_ILLUSTRATION, MEDIA_VIDEO],
            )
        }
        result = apply_visual_intent_translation(beats, translations)
        assert result[0].media_type_preferences == [MEDIA_ILLUSTRATION, MEDIA_VIDEO, MEDIA_PHOTO]
        assert MEDIA_GRAPHIC not in result[0].media_type_preferences

    def test_search_queries_are_grounded_in_literal_meaning(self):
        beats = [_beat(0, search_queries=["fire drill"])]
        translations = {
            0: BeatVisualMeaning(
                beat_index=0,
                topic_context="immune response",
                literal_visual_subject="immune cells rehearsing a response",
                concrete_entities=["antigen"],
            )
        }
        result = apply_visual_intent_translation(beats, translations)
        assert result[0].search_queries != ["fire drill"]
        assert result[0].search_queries
        assert any("immune" in q for q in result[0].search_queries)

    def test_marks_continuation_and_sets_setting_and_topic_context(self):
        beats = [_beat(0)]
        translations = {
            0: BeatVisualMeaning(
                beat_index=0,
                topic_context="ctx",
                literal_visual_subject="subject",
                setting="a laboratory",
                continues_previous=True,
            )
        }
        result = apply_visual_intent_translation(beats, translations)
        assert result[0].continues_sequence is True
        assert result[0].visual_setting == "a laboratory"
        assert result[0].topic_context == "ctx"

    def test_untranslated_sibling_beat_is_returned_byte_for_byte(self):
        """A beat translation discarding never mutates a NEIGHBOURING beat
        the response simply didn't mention."""
        beats = [_beat(0), _beat(1, entities=["untouched"])]
        translations = {
            0: BeatVisualMeaning(
                beat_index=0, topic_context="ctx", literal_visual_subject="subject"
            )
        }
        result = apply_visual_intent_translation(beats, translations)
        assert result[1].entities == ["untouched"]
        assert result[1].literal_visual_subject == ""
        assert result[1].continues_sequence is False
