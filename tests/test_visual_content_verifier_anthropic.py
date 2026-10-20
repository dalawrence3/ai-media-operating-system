"""Phase 3 — AnthropicVisionVerifier: the real vision-backed ContentVerifier.

No real provider calls anywhere in this file — every Anthropic client is a
`MagicMock`, matching the existing convention in `test_ai_claude.py`. The one
exception to "synthetic" is `TestRealIncidentRegression`, which exercises the
REAL frame-extraction/contact-sheet pipeline against the actual cached video
files from the real galaxy-formation incident (Phase 1's root-cause find) —
only the network call itself is mocked, so this proves the adapter's request
construction and response parsing against genuine incident content, not just
a filename or title.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.ai.errors import MissingCredentialsError
from app.visuals.constants import MEDIA_VIDEO
from app.visuals.models import BeatResolution, VisualBeat
from app.visuals.verification import (
    STATUS_QUESTIONABLE,
    STATUS_REJECTED,
    STATUS_UNAVAILABLE,
    STATUS_VERIFIED,
    AnthropicVisionVerifier,
    BeatIntent,
    build_contact_sheet,
    extract_representative_frame,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_GYM_CLIP = _REPO_ROOT / "artifacts/visuals/cache/pexels_b886deacefd94ac2.mp4"
_GALAXY_CLIP = _REPO_ROOT / "artifacts/visuals/cache/pexels_1884cd1c92befd46.mp4"


def _fake_message(payload, *, model="claude-haiku-4-5-20251001", in_tok=120, out_tok=40):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        model=model,
        usage=SimpleNamespace(input_tokens=in_tok, output_tokens=out_tok),
    )


def _client(*, create=None):
    client = MagicMock()
    if create is not None:
        client.messages.create.side_effect = create
    return client


def _intent(index: int, text: str, **kwargs) -> BeatIntent:
    return BeatIntent(beat_index=index, narration_text=text, **kwargs)


def _verifier(client) -> AnthropicVisionVerifier:
    return AnthropicVisionVerifier(api_key="test-key", client=client, sleep_fn=lambda _: None)


class TestConstruction:
    def test_missing_api_key_raises_immediately(self):
        with pytest.raises(MissingCredentialsError):
            AnthropicVisionVerifier(api_key="")

    def test_default_model_matches_the_configured_haiku_alias(self):
        v = AnthropicVisionVerifier(api_key="k", client=MagicMock())
        assert v._model == "claude-haiku-4-5-20251001"

    def test_custom_model_is_respected(self):
        v = AnthropicVisionVerifier(api_key="k", model="claude-sonnet-5", client=MagicMock())
        assert v._model == "claude-sonnet-5"


class TestRequestConstruction:
    def test_image_and_text_blocks_are_both_sent(self):
        client = _client(
            create=[
                _fake_message(
                    [{"beat_index": 0, "status": "verified", "confidence": 0.9, "reason": "ok"}]
                )
            ]
        )
        verifier = _verifier(client)
        verifier.verify(b"\x89PNG\r\n", [_intent(0, "A spiral galaxy forms.")])

        kwargs = client.messages.create.call_args.kwargs
        content = kwargs["messages"][0]["content"]
        types = [block["type"] for block in content]
        assert "image" in types and "text" in types
        image_block = next(b for b in content if b["type"] == "image")
        assert image_block["source"]["type"] == "base64"
        assert image_block["source"]["media_type"] == "image/png"

    def test_beat_narration_and_intent_fields_appear_in_the_prompt(self):
        client = _client(
            create=[
                _fake_message(
                    [{"beat_index": 5, "status": "verified", "confidence": 0.8, "reason": "ok"}]
                )
            ]
        )
        verifier = _verifier(client)
        verifier.verify(
            b"img",
            [
                _intent(
                    5,
                    "Violent collisions between galaxies scramble everything.",
                    keywords=["collisions", "galaxies"],
                    entities=[],
                    visual_intent="action",
                    avoid_terms=["bicycles"],
                )
            ],
        )
        text_block = next(
            b
            for b in client.messages.create.call_args.kwargs["messages"][0]["content"]
            if b["type"] == "text"
        )
        assert "Beat 5" in text_block["text"]
        assert "collisions" in text_block["text"]
        assert "bicycles" in text_block["text"]  # the avoid-term, so the model knows to flag it

    def test_model_is_configurable_and_used_in_the_call(self):
        client = _client(
            create=[
                _fake_message(
                    [{"beat_index": 0, "status": "verified", "confidence": 0.9, "reason": "ok"}]
                )
            ]
        )
        verifier = AnthropicVisionVerifier(
            api_key="k", model="claude-opus-5", client=client, sleep_fn=lambda _: None
        )
        verifier.verify(b"img", [_intent(0, "text")])
        assert client.messages.create.call_args.kwargs["model"] == "claude-opus-5"


class TestStructuredResponseParsing:
    def test_verified_questionable_rejected_all_parse(self):
        client = _client(
            create=[
                _fake_message(
                    [
                        {
                            "beat_index": 0,
                            "status": "verified",
                            "confidence": 0.95,
                            "reason": "clear match",
                        },
                        {
                            "beat_index": 1,
                            "status": "questionable",
                            "confidence": 0.5,
                            "reason": "generic imagery",
                        },
                        {
                            "beat_index": 2,
                            "status": "rejected",
                            "confidence": 0.1,
                            "reason": "wrong subject",
                        },
                    ]
                )
            ]
        )
        verdicts = _verifier(client).verify(
            b"img", [_intent(0, "a"), _intent(1, "b"), _intent(2, "c")]
        )
        by_index = {v.beat_index: v for v in verdicts}
        assert by_index[0].status == STATUS_VERIFIED
        assert by_index[1].status == STATUS_QUESTIONABLE
        assert by_index[2].status == STATUS_REJECTED
        assert by_index[2].reason == "wrong subject"

    def test_markdown_fenced_json_is_accepted(self):
        fenced = (
            "```json\n"
            + json.dumps(
                [{"beat_index": 0, "status": "verified", "confidence": 0.9, "reason": "ok"}]
            )
            + "\n```"
        )
        client = _client(create=[_fake_message(fenced)])
        verdicts = _verifier(client).verify(b"img", [_intent(0, "a")])
        assert verdicts[0].status == STATUS_VERIFIED

    def test_missing_beat_makes_the_whole_response_unavailable(self):
        client = _client(
            create=[
                _fake_message(
                    [{"beat_index": 0, "status": "verified", "confidence": 0.9, "reason": "ok"}]
                )
            ]
        )
        verdicts = _verifier(client).verify(b"img", [_intent(0, "a"), _intent(1, "b")])
        assert all(v.status == STATUS_UNAVAILABLE for v in verdicts)

    def test_duplicate_beat_makes_the_whole_response_unavailable(self):
        client = _client(
            create=[
                _fake_message(
                    [
                        {"beat_index": 0, "status": "verified", "confidence": 0.9, "reason": "ok"},
                        {"beat_index": 0, "status": "rejected", "confidence": 0.1, "reason": "dup"},
                    ]
                )
            ]
        )
        verdicts = _verifier(client).verify(b"img", [_intent(0, "a")])
        assert verdicts[0].status == STATUS_UNAVAILABLE

    def test_extra_unrequested_beat_makes_the_whole_response_unavailable(self):
        client = _client(
            create=[
                _fake_message(
                    [
                        {"beat_index": 0, "status": "verified", "confidence": 0.9, "reason": "ok"},
                        {
                            "beat_index": 99,
                            "status": "verified",
                            "confidence": 0.9,
                            "reason": "extra",
                        },
                    ]
                )
            ]
        )
        verdicts = _verifier(client).verify(b"img", [_intent(0, "a")])
        assert verdicts[0].status == STATUS_UNAVAILABLE

    def test_unrecognised_status_value_is_rejected_not_guessed(self):
        client = _client(
            create=[
                _fake_message(
                    [{"beat_index": 0, "status": "probably_fine", "confidence": 0.9, "reason": "x"}]
                )
            ]
        )
        verdicts = _verifier(client).verify(b"img", [_intent(0, "a")])
        assert verdicts[0].status == STATUS_UNAVAILABLE

    def test_confidence_out_of_range_is_rejected(self):
        client = _client(
            create=[
                _fake_message(
                    [{"beat_index": 0, "status": "verified", "confidence": 1.7, "reason": "x"}]
                )
            ]
        )
        verdicts = _verifier(client).verify(b"img", [_intent(0, "a")])
        assert verdicts[0].status == STATUS_UNAVAILABLE

    def test_non_json_prose_response_is_unavailable(self):
        client = _client(create=[_fake_message("Sure! Beat 0 looks fine to me.")])
        verdicts = _verifier(client).verify(b"img", [_intent(0, "a")])
        assert verdicts[0].status == STATUS_UNAVAILABLE

    def test_json_object_instead_of_array_is_unavailable(self):
        client = _client(
            create=[_fake_message({"beat_index": 0, "status": "verified", "confidence": 0.9})]
        )
        verdicts = _verifier(client).verify(b"img", [_intent(0, "a")])
        assert verdicts[0].status == STATUS_UNAVAILABLE

    def test_extended_thinking_block_before_text_is_handled(self):
        """Phase 7C.1 regression — reproduces the real response shape a
        thinking-enabled model returns: content[0] is a non-text
        ThinkingBlock (no .text attribute) and the actual verdict JSON is
        a LATER block. _extract_text must scan by type, not assume
        content[0] is always the text block (the exact bug that also broke
        cold-start discovery — see app.ai.claude._extract_text)."""
        payload = json.dumps(
            [{"beat_index": 0, "status": "verified", "confidence": 0.9, "reason": "match"}]
        )
        message = SimpleNamespace(
            content=[
                SimpleNamespace(type="thinking", thinking="reasoning about the image..."),
                SimpleNamespace(type="text", text=payload),
            ],
            model="claude-sonnet-5",
            usage=SimpleNamespace(input_tokens=100, output_tokens=30),
        )
        client = _client(create=[message])
        verdicts = _verifier(client).verify(b"img", [_intent(0, "a")])
        assert verdicts[0].status == "verified"
        assert verdicts[0].confidence == 0.9


class TestProviderFailure:
    def test_connection_error_yields_unavailable_never_verified(self):
        import anthropic

        class _FakeConnError(anthropic.APIConnectionError):
            def __init__(self):
                pass

        client = _client(create=_FakeConnError())
        verdicts = _verifier(client).verify(b"img", [_intent(0, "a")])
        assert all(v.status == STATUS_UNAVAILABLE for v in verdicts)
        assert not any(v.status == STATUS_VERIFIED for v in verdicts)

    def test_authentication_error_yields_unavailable(self):
        import anthropic

        class _FakeAuthError(anthropic.AuthenticationError):
            def __init__(self):
                pass

        client = _client(create=_FakeAuthError())
        verdicts = _verifier(client).verify(b"img", [_intent(0, "a")])
        assert verdicts[0].status == STATUS_UNAVAILABLE

    def test_rate_limit_is_retried_then_unavailable_if_exhausted(self):
        import anthropic

        class _FakeRLError(anthropic.RateLimitError):
            def __init__(self):
                pass

        client = _client(create=_FakeRLError())
        verifier = AnthropicVisionVerifier(
            api_key="k", client=client, max_retries=2, sleep_fn=lambda _: None
        )
        verdicts = verifier.verify(b"img", [_intent(0, "a")])
        assert verdicts[0].status == STATUS_UNAVAILABLE
        assert client.messages.create.call_count == 2  # actually retried, not just failed once

    def test_never_raises_out_of_verify(self):
        """`verify()` is called from `verify_plan`'s own try/except, but the
        adapter itself must already be safe on its own — a caller that
        forgets the wrapper must not crash a render."""
        import anthropic

        class _FakeServerError(anthropic.APIStatusError):
            def __init__(self):
                self.status_code = 500

        client = _client(create=_FakeServerError())
        verifier = AnthropicVisionVerifier(
            api_key="k", client=client, max_retries=1, sleep_fn=lambda _: None
        )
        verdicts = verifier.verify(b"img", [_intent(0, "a")])
        assert verdicts[0].status == STATUS_UNAVAILABLE


class TestUsageTelemetry:
    def test_successful_call_records_non_secret_stats(self):
        client = _client(
            create=[
                _fake_message(
                    [{"beat_index": 0, "status": "verified", "confidence": 0.9, "reason": "ok"}]
                )
            ]
        )
        verifier = _verifier(client)
        verifier.verify(b"img", [_intent(0, "a")])
        assert len(verifier.last_run_stats) == 1
        stats = verifier.last_run_stats[0]
        assert stats["beats"] == 1
        assert stats["input_tokens"] == 120
        assert stats["output_tokens"] == 40
        assert "api" not in json.dumps(stats).lower()  # no key/secret leakage


# ---------------------------------------------------------------------------
# Adversarial fixtures — domain-general semantic-ambiguity cases (§13)
#
# These test RESULT PARSING and PRODUCTION HANDLING against a mocked verdict
# representing what a correct judgement would look like — no word-specific
# logic exists in the adapter itself; the model does the disambiguation.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "narration,mismatched_visual_reason",
    [
        (
            "The electrical current flows through the circuit.",
            "shows an ocean current, not electricity",
        ),
        ("Each battery cell stores charge chemically.", "shows a prison cell, not a battery"),
        (
            "A strong magnetic field surrounds the coil.",
            "shows a sports field, not a magnetic field",
        ),
        ("The economic cycle repeats every decade.", "shows a bicycle, not an economic cycle"),
    ],
)
class TestAmbiguousTermAdversarialFixtures:
    def test_mismatched_sense_is_rejected(self, narration, mismatched_visual_reason):
        client = _client(
            create=[
                _fake_message(
                    [
                        {
                            "beat_index": 0,
                            "status": "rejected",
                            "confidence": 0.15,
                            "reason": mismatched_visual_reason,
                        }
                    ]
                )
            ]
        )
        verdicts = _verifier(client).verify(b"img", [_intent(0, narration)])
        assert verdicts[0].status == STATUS_REJECTED

    def test_correct_sense_is_verified(self, narration, mismatched_visual_reason):
        client = _client(
            create=[
                _fake_message(
                    [
                        {
                            "beat_index": 0,
                            "status": "verified",
                            "confidence": 0.9,
                            "reason": "correct sense depicted",
                        }
                    ]
                )
            ]
        )
        verdicts = _verifier(client).verify(b"img", [_intent(0, narration)])
        assert verdicts[0].status == STATUS_VERIFIED


# ---------------------------------------------------------------------------
# Real incident regression (§12) — the actual gym-bicycle and galaxy assets
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _GYM_CLIP.exists() or not _GALAXY_CLIP.exists(), reason="incident cache assets not present"
)
class TestRealIncidentRegression:
    """Exercises the REAL frame-extraction + contact-sheet pipeline against
    the actual cached video files from the real production incident (Phase
    1). Only the Anthropic network call is mocked — everything else (ffmpeg
    frame grab, PIL contact-sheet composition, base64 encoding, prompt
    construction, response parsing) runs for real against genuine content.

    This proves the PIPELINE is wired correctly end-to-end against the real
    incident assets. It does not, and cannot without a paid live call, prove
    the model's own visual judgement — see the final report's recommendation
    on a controlled live probe for that.
    """

    def _real_beats(self):
        gym = BeatResolution(
            beat=VisualBeat(
                beat_index=13,
                scene_index=2,
                scene_id=1,
                segment_id=1,
                start_ms=50617,
                end_ms=57000,
                duration_ms=6383,
                narration_text=(
                    "Violent collisions between galaxies can scramble everything into "
                    "an elliptical blob."
                ),
                keywords=["collisions", "everything", "elliptical", "galaxies", "violent"],
                entities=[],
                visual_intent="action",
                media_type_preferences=["video", "photo", "graphic"],
                search_queries=["collisions everything elliptical"],
                avoid_terms=[],
            ),
            media_type=MEDIA_VIDEO,
            local_path=str(_GYM_CLIP),
            asset_key="pexels:6455082",
            provider="pexels",
        )
        galaxy = BeatResolution(
            beat=VisualBeat(
                beat_index=0,
                scene_index=0,
                scene_id=1,
                segment_id=1,
                start_ms=0,
                end_ms=3820,
                duration_ms=3820,
                narration_text=(
                    "Every galaxy you've ever seen — billions of stars, swirling in space."
                ),
                keywords=["billions", "galaxy", "stars", "seen"],
                entities=[],
                visual_intent="entity",
                media_type_preferences=["video", "photo", "graphic"],
                search_queries=["galaxy formation process"],
                avoid_terms=[],
            ),
            media_type=MEDIA_VIDEO,
            local_path=str(_GALAXY_CLIP),
            asset_key="pexels:39267198",
            provider="pexels",
        )
        return gym, galaxy

    def test_real_gym_frame_is_extracted_and_is_a_valid_image(self):
        gym, _ = self._real_beats()
        frame = extract_representative_frame(gym)
        assert frame is not None and len(frame) > 1000

    def test_real_galaxy_frame_is_extracted_and_is_a_valid_image(self):
        _, galaxy = self._real_beats()
        frame = extract_representative_frame(galaxy)
        assert frame is not None and len(frame) > 1000

    def test_real_contact_sheet_builds_from_both_real_frames(self):
        gym, galaxy = self._real_beats()
        gym_frame = extract_representative_frame(gym)
        galaxy_frame = extract_representative_frame(galaxy)
        sheet = build_contact_sheet([(13, gym_frame), (0, galaxy_frame)])
        assert sheet is not None and len(sheet) > 1000

    def test_full_pipeline_rejects_the_real_gym_clip_and_verifies_the_real_galaxy_clip(self):
        """End-to-end: real frames -> real contact sheet -> real request
        construction -> mocked model response -> real strict parsing."""
        gym, galaxy = self._real_beats()
        gym_frame = extract_representative_frame(gym)
        galaxy_frame = extract_representative_frame(galaxy)
        sheet = build_contact_sheet([(13, gym_frame), (0, galaxy_frame)])
        assert sheet is not None

        client = _client(
            create=[
                _fake_message(
                    [
                        {
                            "beat_index": 13,
                            "status": "rejected",
                            "confidence": 0.05,
                            "reason": "shows two people on gym elliptical exercise machines, "
                            "not a galaxy collision",
                        },
                        {
                            "beat_index": 0,
                            "status": "verified",
                            "confidence": 0.95,
                            "reason": "shows a spiral galaxy consistent with the narration",
                        },
                    ]
                )
            ]
        )
        verifier = _verifier(client)
        intents = [
            BeatIntent(
                beat_index=13,
                narration_text=gym.beat.narration_text,
                keywords=gym.beat.keywords,
                entities=gym.beat.entities,
                visual_intent=gym.beat.visual_intent,
                avoid_terms=gym.beat.avoid_terms,
            ),
            BeatIntent(
                beat_index=0,
                narration_text=galaxy.beat.narration_text,
                keywords=galaxy.beat.keywords,
                entities=galaxy.beat.entities,
                visual_intent=galaxy.beat.visual_intent,
                avoid_terms=galaxy.beat.avoid_terms,
            ),
        ]
        verdicts = verifier.verify(sheet, intents)
        by_index = {v.beat_index: v for v in verdicts}

        assert by_index[13].status == STATUS_REJECTED
        assert by_index[0].status == STATUS_VERIFIED

        # The request actually carried the real image bytes, not a stand-in.
        image_block = next(
            b
            for b in client.messages.create.call_args.kwargs["messages"][0]["content"]
            if b["type"] == "image"
        )
        import base64

        assert base64.standard_b64decode(image_block["source"]["data"]) == sheet
