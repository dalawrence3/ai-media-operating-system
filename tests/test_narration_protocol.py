"""Tests for Phase 6 M6.2 TTS protocol and fake provider."""

from __future__ import annotations

import wave
from io import BytesIO

import pytest

from app.narration.errors import SynthesisError
from app.narration.fake import (
    FAKE_MODEL_NAME,
    FAKE_PROVIDER_NAME,
    FAKE_VOICE_ID,
    FakeTTSProvider,
)
from app.narration.protocol import TTSProvider, TTSRequest, TTSResponse


def _request(**kwargs) -> TTSRequest:
    defaults = dict(
        text="Hello world",
        provider=FAKE_PROVIDER_NAME,
        model=FAKE_MODEL_NAME,
        voice_id=FAKE_VOICE_ID,
        language="en-US",
        speaking_rate=1.0,
        output_format="wav",
        sample_rate_hz=22050,
    )
    defaults.update(kwargs)
    return TTSRequest(**defaults)


# ── Protocol satisfaction ─────────────────────────────────────────────────────


def test_fake_satisfies_protocol() -> None:
    assert isinstance(FakeTTSProvider(), TTSProvider)


def test_provider_name() -> None:
    assert FakeTTSProvider().provider_name == FAKE_PROVIDER_NAME


def test_default_model() -> None:
    assert FakeTTSProvider().default_model == FAKE_MODEL_NAME


# ── Synthesis output ──────────────────────────────────────────────────────────


def test_synthesize_returns_tts_response() -> None:
    resp = FakeTTSProvider().synthesize(_request())
    assert isinstance(resp, TTSResponse)


def test_synthesize_wav_bytes_valid() -> None:
    resp = FakeTTSProvider().synthesize(_request())
    with wave.open(BytesIO(resp.audio_bytes), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getframerate() == 22050
        assert wf.getsampwidth() == 2


def test_synthesize_sample_rate_matches_request() -> None:
    resp = FakeTTSProvider().synthesize(_request(sample_rate_hz=44100))
    with wave.open(BytesIO(resp.audio_bytes), "rb") as wf:
        assert wf.getframerate() == 44100


def test_synthesize_duration_positive() -> None:
    resp = FakeTTSProvider().synthesize(_request(text="One two three four five"))
    assert resp.duration_seconds > 0.0


def test_synthesize_duration_scales_with_word_count() -> None:
    short = FakeTTSProvider().synthesize(_request(text="Hi"))
    long = FakeTTSProvider().synthesize(
        _request(text="This sentence has many many many more words than the short one")
    )
    assert long.duration_seconds > short.duration_seconds


def test_synthesize_characters_billed_equals_text_length() -> None:
    text = "Hello world"
    resp = FakeTTSProvider().synthesize(_request(text=text))
    assert resp.characters_billed == len(text)


def test_synthesize_provider_and_model_in_response() -> None:
    resp = FakeTTSProvider().synthesize(_request())
    assert resp.provider == FAKE_PROVIDER_NAME
    assert resp.model == FAKE_MODEL_NAME


def test_synthesize_request_id_present() -> None:
    resp = FakeTTSProvider().synthesize(_request())
    assert resp.request_id is not None


# ── Determinism ───────────────────────────────────────────────────────────────


def test_synthesize_deterministic_same_input() -> None:
    p1 = FakeTTSProvider()
    p2 = FakeTTSProvider()
    r = _request()
    assert p1.synthesize(r).audio_bytes == p2.synthesize(r).audio_bytes


# ── Injected failures ─────────────────────────────────────────────────────────


def test_fail_on_first_call() -> None:
    provider = FakeTTSProvider(fail_on={1})
    with pytest.raises(SynthesisError):
        provider.synthesize(_request())


def test_fail_on_second_call_succeeds_first() -> None:
    provider = FakeTTSProvider(fail_on={2})
    resp1 = provider.synthesize(_request())
    assert resp1.audio_bytes  # first call succeeds
    with pytest.raises(SynthesisError):
        provider.synthesize(_request())


def test_no_fail_by_default() -> None:
    provider = FakeTTSProvider()
    for _ in range(5):
        provider.synthesize(_request())


# ── WAV bytes helper ──────────────────────────────────────────────────────────


def test_build_wav_bytes_produces_valid_wav() -> None:
    raw = FakeTTSProvider._build_wav_bytes(duration_seconds=1.0, sample_rate_hz=22050)
    with wave.open(BytesIO(raw), "rb") as wf:
        assert wf.getnframes() == 22050


def test_build_wav_bytes_minimum_duration() -> None:
    raw = FakeTTSProvider._build_wav_bytes(duration_seconds=0.001, sample_rate_hz=22050)
    with wave.open(BytesIO(raw), "rb") as wf:
        assert wf.getnframes() >= 0
