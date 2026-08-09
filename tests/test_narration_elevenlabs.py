"""Phase 6 M6.3C: Unit tests for ElevenLabsTTSProvider.

All tests use an injected MagicMock SDK client — no live network calls.
The mock bypasses the credential guard (ACE_TTS_LIVE_ENABLED / ACE_ELEVENLABS_API_KEY)
entirely when _sdk_client is provided to ElevenLabsTTSProvider.__init__().

Alignment object mock structure:
    mock_client.text_to_speech.convert_with_timestamps(...)
    → response with:
        .audio_base64: str (base64 WAV)
        .alignment.characters: list[str]
        .alignment.character_start_times_seconds: list[float]
        .alignment.character_end_times_seconds: list[float]
        .normalized_alignment: same shape
        .request_id: str | None
        .history_item_id: str | None
"""

from __future__ import annotations

import base64
import io
import json
import struct
import wave
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.narration.capabilities import ProviderCapabilities, ProviderFeatureFlags
from app.narration.constants import (
    PROVIDER_FEATURE_ALIGNMENT,
    PROVIDER_FEATURE_PRONUNCIATION_DICTIONARY,
    PROVIDER_FEATURE_SEED,
    PROVIDER_FEATURE_VOICE_CLONING,
)
from app.narration.errors import (
    AudioValidationError,
    ProviderCredentialError,
    ProviderRateLimitError,
    SynthesisError,
)
from app.narration.lifecycle import ProviderLifecycle, ProviderLifecycleState
from app.narration.metadata import ProviderMetadata
from app.narration.protocol import TTSProvider, TTSRequest
from app.narration.providers.elevenlabs import (
    ELEVENLABS_CAPABILITIES,
    ELEVENLABS_DEFAULT_MODEL,
    ELEVENLABS_FEATURE_FLAGS,
    ELEVENLABS_FLASH_MODEL,
    ELEVENLABS_METADATA,
    ELEVENLABS_PROVIDER_CONFIG,
    ELEVENLABS_PROVIDER_NAME,
    ELEVENLABS_PROVIDER_VERSION,
    ElevenLabsTTSProvider,
    _build_provider_metadata_json,
    _elevenlabs_output_format,
    _extract_status_code,
    _measure_rms_dbfs,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_silent_wav(duration_seconds: float = 0.5, sample_rate: int = 22050) -> bytes:
    """Return a valid 16-bit mono silent WAV at the given duration."""
    n_samples = int(sample_rate * duration_seconds)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"<{n_samples}h", *([0] * n_samples)))
    return buf.getvalue()


def _make_mock_response(
    wav_bytes: bytes | None = None,
    duration: float = 0.5,
    characters: list[str] | None = None,
    request_id: str | None = "req-abc123",
) -> SimpleNamespace:
    if wav_bytes is None:
        wav_bytes = _make_silent_wav(duration)
    audio_b64 = base64.b64encode(wav_bytes).decode()

    chars = characters or list("Hello")
    n = len(chars)
    step = duration / max(n, 1)
    starts = [i * step for i in range(n)]
    ends = [(i + 1) * step for i in range(n)]

    alignment = SimpleNamespace(
        characters=chars,
        character_start_times_seconds=starts,
        character_end_times_seconds=ends,
    )
    normalized_alignment = SimpleNamespace(
        characters=chars,
        character_start_times_seconds=starts,
        character_end_times_seconds=ends,
    )
    # Use 'audio_base_64' to match the real SDK Pydantic attribute name
    # (the SDK field is named audio_base_64 in Python; its JSON alias is audio_base64).
    # The adapter resolves both names via getattr fallback for test compatibility.
    return SimpleNamespace(
        audio_base_64=audio_b64,
        alignment=alignment,
        normalized_alignment=normalized_alignment,
        request_id=request_id,
        history_item_id=None,
    )


def _make_sdk_client(response: SimpleNamespace | None = None) -> MagicMock:
    client = MagicMock()
    if response is None:
        response = _make_mock_response()
    client.text_to_speech.convert_with_timestamps.return_value = response
    return client


def _default_request(**overrides) -> TTSRequest:
    defaults = dict(
        text="Hello from the test suite.",
        provider=ELEVENLABS_PROVIDER_NAME,
        model=ELEVENLABS_DEFAULT_MODEL,
        voice_id="test-voice-id",
        language="en",
        speaking_rate=1.0,
        output_format="wav",
        sample_rate_hz=22050,
    )
    defaults.update(overrides)
    return TTSRequest(**defaults)


# ── Protocol conformance ──────────────────────────────────────────────────────


class TestProtocolConformance:
    def test_satisfies_tts_provider(self):
        assert isinstance(ElevenLabsTTSProvider(), TTSProvider)

    def test_satisfies_provider_lifecycle(self):
        assert isinstance(ElevenLabsTTSProvider(), ProviderLifecycle)


# ── Module-level constants ────────────────────────────────────────────────────


class TestModuleLevelConstants:
    def test_provider_name(self):
        p = ElevenLabsTTSProvider()
        assert p.provider_name == "elevenlabs"

    def test_default_model(self):
        p = ElevenLabsTTSProvider()
        assert p.default_model == ELEVENLABS_DEFAULT_MODEL

    def test_both_models_registered_to_names(self):
        assert ELEVENLABS_DEFAULT_MODEL == "eleven_multilingual_v2"
        assert ELEVENLABS_FLASH_MODEL == "eleven_flash_v2_5"

    def test_capabilities_is_provider_capabilities_instance(self):
        assert isinstance(ELEVENLABS_CAPABILITIES, ProviderCapabilities)

    def test_feature_flags_is_provider_feature_flags_instance(self):
        assert isinstance(ELEVENLABS_FEATURE_FLAGS, ProviderFeatureFlags)

    def test_metadata_is_provider_metadata_instance(self):
        assert isinstance(ELEVENLABS_METADATA, ProviderMetadata)

    def test_metadata_provider_name_matches(self):
        assert ELEVENLABS_METADATA.provider_name == ELEVENLABS_PROVIDER_NAME

    def test_provider_version_provider_name_matches(self):
        assert ELEVENLABS_PROVIDER_VERSION.provider_name == ELEVENLABS_PROVIDER_NAME

    def test_provider_config_provider_name_matches(self):
        assert ELEVENLABS_PROVIDER_CONFIG.provider_name == ELEVENLABS_PROVIDER_NAME

    def test_provider_config_default_model_matches(self):
        assert ELEVENLABS_PROVIDER_CONFIG.model_id == ELEVENLABS_DEFAULT_MODEL

    def test_provider_config_default_output_format(self):
        assert ELEVENLABS_PROVIDER_CONFIG.output_format == "wav"

    def test_provider_config_default_sample_rate(self):
        assert ELEVENLABS_PROVIDER_CONFIG.sample_rate_hz == 22050


# ── Feature flags ─────────────────────────────────────────────────────────────


class TestFeatureFlags:
    def test_supports_alignment(self):
        assert ELEVENLABS_FEATURE_FLAGS.supports_alignment is True

    def test_supports_seed(self):
        assert ELEVENLABS_FEATURE_FLAGS.supports_seed is True

    def test_supports_voice_cloning(self):
        assert ELEVENLABS_FEATURE_FLAGS.supports_voice_cloning is True

    def test_supports_pronunciation_dictionary(self):
        assert ELEVENLABS_FEATURE_FLAGS.supports_pronunciation_dictionary is True

    def test_supports_stability(self):
        assert ELEVENLABS_FEATURE_FLAGS.supports_stability is True

    def test_supports_similarity_boost(self):
        assert ELEVENLABS_FEATURE_FLAGS.supports_similarity_boost is True

    def test_supports_speaking_rate(self):
        assert ELEVENLABS_FEATURE_FLAGS.supports_speaking_rate is True

    def test_supports_streaming(self):
        assert ELEVENLABS_FEATURE_FLAGS.supports_streaming is True

    def test_supports_style_transfer(self):
        assert ELEVENLABS_FEATURE_FLAGS.supports_style_transfer is True

    def test_does_not_support_multi_speaker(self):
        assert ELEVENLABS_FEATURE_FLAGS.supports_multi_speaker is False

    def test_as_dict_contains_all_new_flags(self):
        d = ELEVENLABS_FEATURE_FLAGS.as_dict()
        assert d[PROVIDER_FEATURE_ALIGNMENT] is True
        assert d[PROVIDER_FEATURE_SEED] is True
        assert d[PROVIDER_FEATURE_VOICE_CLONING] is True
        assert d[PROVIDER_FEATURE_PRONUNCIATION_DICTIONARY] is True


# ── Capabilities ──────────────────────────────────────────────────────────────


class TestCapabilities:
    def test_wav_format_supported(self):
        assert "wav" in ELEVENLABS_CAPABILITIES.supported_output_formats

    def test_mp3_format_supported(self):
        assert "mp3" in ELEVENLABS_CAPABILITIES.supported_output_formats

    def test_22050_sample_rate_supported(self):
        assert 22050 in ELEVENLABS_CAPABILITIES.supported_sample_rates_hz

    def test_english_language_supported(self):
        assert "en" in ELEVENLABS_CAPABILITIES.supported_languages

    def test_max_characters(self):
        assert ELEVENLABS_CAPABILITIES.max_characters_per_request >= 5000

    def test_has_feature_alignment(self):
        assert ELEVENLABS_CAPABILITIES.has_feature(PROVIDER_FEATURE_ALIGNMENT)

    def test_has_feature_seed(self):
        assert ELEVENLABS_CAPABILITIES.has_feature(PROVIDER_FEATURE_SEED)


# ── Lifecycle ─────────────────────────────────────────────────────────────────


class TestLifecycle:
    def test_initial_state_is_created(self):
        p = ElevenLabsTTSProvider()
        assert p.lifecycle_state == ProviderLifecycleState.CREATED

    def test_initialize_with_injected_client_sets_ready(self):
        p = ElevenLabsTTSProvider(_sdk_client=_make_sdk_client())
        p.initialize()
        assert p.lifecycle_state == ProviderLifecycleState.READY

    def test_shutdown_sets_state(self):
        p = ElevenLabsTTSProvider(_sdk_client=_make_sdk_client())
        p.initialize()
        p.shutdown()
        assert p.lifecycle_state == ProviderLifecycleState.SHUTDOWN

    def test_initialize_no_live_enabled_raises_credential_error(self, monkeypatch):
        monkeypatch.delenv("ACE_TTS_LIVE_ENABLED", raising=False)
        monkeypatch.delenv("ACE_ELEVENLABS_API_KEY", raising=False)
        from app.core.config import reset_config

        reset_config()
        p = ElevenLabsTTSProvider()
        with pytest.raises(ProviderCredentialError, match="ACE_TTS_LIVE_ENABLED"):
            p.initialize()
        reset_config()

    def test_initialize_live_enabled_but_no_key_raises(self, monkeypatch):
        monkeypatch.setenv("ACE_TTS_LIVE_ENABLED", "true")
        monkeypatch.delenv("ACE_ELEVENLABS_API_KEY", raising=False)
        from app.core.config import reset_config

        reset_config()
        p = ElevenLabsTTSProvider()
        with pytest.raises(ProviderCredentialError, match="ACE_ELEVENLABS_API_KEY"):
            p.initialize()
        reset_config()


# ── Synthesis (mocked client) ─────────────────────────────────────────────────


class TestSynthesisWithMockedClient:
    def _provider(self, response=None) -> ElevenLabsTTSProvider:
        client = _make_sdk_client(response)
        p = ElevenLabsTTSProvider(_sdk_client=client)
        p.initialize()
        return p

    def test_synthesize_returns_wav_bytes(self):
        p = self._provider()
        resp = p.synthesize(_default_request())
        assert len(resp.audio_bytes) > 0

    def test_synthesize_provider_field(self):
        p = self._provider()
        resp = p.synthesize(_default_request())
        assert resp.provider == ELEVENLABS_PROVIDER_NAME

    def test_synthesize_model_field(self):
        p = self._provider()
        resp = p.synthesize(_default_request())
        assert resp.model == ELEVENLABS_DEFAULT_MODEL

    def test_synthesize_voice_id_field(self):
        p = self._provider()
        resp = p.synthesize(_default_request(voice_id="my-voice"))
        assert resp.voice_id == "my-voice"

    def test_synthesize_characters_billed(self):
        text = "Hello world"
        p = self._provider()
        resp = p.synthesize(_default_request(text=text))
        assert resp.characters_billed == len(text)

    def test_synthesize_duration_from_alignment(self):
        wav = _make_silent_wav(1.0)
        response = _make_mock_response(wav_bytes=wav, duration=1.0)
        p = self._provider(response)
        resp = p.synthesize(_default_request())
        assert resp.duration_seconds > 0.0

    def test_synthesize_latency_ms_present(self):
        p = self._provider()
        resp = p.synthesize(_default_request())
        assert resp.latency_ms is not None and resp.latency_ms >= 0

    def test_synthesize_request_id_from_response(self):
        response = _make_mock_response(request_id="rid-999")
        p = self._provider(response)
        resp = p.synthesize(_default_request())
        assert resp.request_id == "rid-999"

    def test_synthesize_provider_metadata_json_is_valid_json(self):
        p = self._provider()
        resp = p.synthesize(_default_request())
        parsed = json.loads(resp.provider_metadata_json)
        assert "alignment" in parsed
        assert "loudness" in parsed

    def test_synthesize_metadata_json_contains_alignment_keys(self):
        p = self._provider()
        resp = p.synthesize(_default_request())
        parsed = json.loads(resp.provider_metadata_json)
        al = parsed["alignment"]
        assert "characters" in al
        assert "character_start_times_seconds" in al
        assert "character_end_times_seconds" in al

    def test_synthesize_metadata_json_contains_loudness(self):
        p = self._provider()
        resp = p.synthesize(_default_request())
        parsed = json.loads(resp.provider_metadata_json)
        loudness = parsed["loudness"]
        assert "rms_dbfs" in loudness
        assert "advisory_rms_dbfs" in loudness
        assert loudness["measurement"] == "rms_amplitude_dbfs"
        assert loudness["unit"] == "dBFS"
        assert "LUFS" not in loudness
        assert "approx_lufs" not in loudness
        assert "target_lufs" not in loudness

    def test_synthesize_without_initialize_raises(self):
        client = _make_sdk_client()
        p = ElevenLabsTTSProvider(_sdk_client=client)
        with pytest.raises(SynthesisError, match="initialize"):
            p.synthesize(_default_request())

    def test_synthesize_flash_model(self):
        p = self._provider()
        resp = p.synthesize(_default_request(model=ELEVENLABS_FLASH_MODEL))
        assert resp.model == ELEVENLABS_FLASH_MODEL

    def test_seed_passed_from_settings_json(self):
        client = _make_sdk_client()
        p = ElevenLabsTTSProvider(_sdk_client=client)
        p.initialize()
        p.synthesize(_default_request(settings_json=json.dumps({"seed": 42})))
        call_kwargs = client.text_to_speech.convert_with_timestamps.call_args[1]
        assert call_kwargs.get("seed") == 42

    def test_no_seed_when_settings_json_empty(self):
        client = _make_sdk_client()
        p = ElevenLabsTTSProvider(_sdk_client=client)
        p.initialize()
        p.synthesize(_default_request(settings_json="{}"))
        call_kwargs = client.text_to_speech.convert_with_timestamps.call_args[1]
        assert "seed" not in call_kwargs

    def test_stability_forwarded(self):
        client = _make_sdk_client()
        p = ElevenLabsTTSProvider(_sdk_client=client)
        p.initialize()
        p.synthesize(_default_request(stability=0.3))
        call_kwargs = client.text_to_speech.convert_with_timestamps.call_args[1]
        assert call_kwargs["voice_settings"].stability == pytest.approx(0.3)

    def test_similarity_boost_forwarded(self):
        client = _make_sdk_client()
        p = ElevenLabsTTSProvider(_sdk_client=client)
        p.initialize()
        p.synthesize(_default_request(similarity_boost=0.9))
        call_kwargs = client.text_to_speech.convert_with_timestamps.call_args[1]
        assert call_kwargs["voice_settings"].similarity_boost == pytest.approx(0.9)

    def test_output_format_wav_22050(self):
        client = _make_sdk_client()
        p = ElevenLabsTTSProvider(_sdk_client=client)
        p.initialize()
        p.synthesize(_default_request(output_format="wav", sample_rate_hz=22050))
        call_kwargs = client.text_to_speech.convert_with_timestamps.call_args[1]
        assert call_kwargs["output_format"] == "wav_22050"


# ── Retry behaviour ───────────────────────────────────────────────────────────


class TestRetryBehaviour:
    def _provider_with_error_then_success(self, error_exc: Exception):
        client = MagicMock()
        success_response = _make_mock_response()
        client.text_to_speech.convert_with_timestamps.side_effect = [
            error_exc,
            success_response,
        ]
        p = ElevenLabsTTSProvider(_sdk_client=client)
        p.initialize()
        return p, client

    def _make_sdk_exc(self, status_code: int) -> Exception:
        exc = Exception(f"HTTP {status_code} error")
        exc.status_code = status_code  # type: ignore[attr-defined]
        return exc

    def test_retries_on_500(self):
        exc = self._make_sdk_exc(500)
        with patch("app.narration.providers.elevenlabs.time.sleep"):
            p, client = self._provider_with_error_then_success(exc)
            resp = p.synthesize(_default_request())
        assert resp.provider == ELEVENLABS_PROVIDER_NAME
        assert client.text_to_speech.convert_with_timestamps.call_count == 2

    def test_retries_on_502(self):
        exc = self._make_sdk_exc(502)
        with patch("app.narration.providers.elevenlabs.time.sleep"):
            p, client = self._provider_with_error_then_success(exc)
            resp = p.synthesize(_default_request())
        assert resp is not None
        assert client.text_to_speech.convert_with_timestamps.call_count == 2

    def test_429_after_all_retries_raises_rate_limit(self):
        exc = self._make_sdk_exc(429)
        client = MagicMock()
        client.text_to_speech.convert_with_timestamps.side_effect = exc
        p = ElevenLabsTTSProvider(_sdk_client=client)
        p.initialize()
        with patch("app.narration.providers.elevenlabs.time.sleep"):
            with pytest.raises(ProviderRateLimitError) as exc_info:
                p.synthesize(_default_request())
        assert exc_info.value.provider_name == ELEVENLABS_PROVIDER_NAME
        assert exc_info.value.attempts > 0

    def test_non_retryable_400_raises_synthesis_error(self):
        exc = self._make_sdk_exc(400)
        client = MagicMock()
        client.text_to_speech.convert_with_timestamps.side_effect = exc
        p = ElevenLabsTTSProvider(_sdk_client=client)
        p.initialize()
        with pytest.raises(SynthesisError):
            p.synthesize(_default_request())

    def test_max_3_retries_then_synthesis_error(self):
        exc = self._make_sdk_exc(503)
        client = MagicMock()
        client.text_to_speech.convert_with_timestamps.side_effect = exc
        p = ElevenLabsTTSProvider(_sdk_client=client)
        p.initialize()
        with patch("app.narration.providers.elevenlabs.time.sleep"):
            with pytest.raises(SynthesisError):
                p.synthesize(_default_request())
        assert client.text_to_speech.convert_with_timestamps.call_count == 3


# ── Bad audio payload ─────────────────────────────────────────────────────────


class TestBadAudioPayload:
    def test_empty_audio_raises_audio_validation_error(self):
        response = SimpleNamespace(
            audio_base_64=base64.b64encode(b"").decode(),
            alignment=SimpleNamespace(
                characters=[],
                character_start_times_seconds=[],
                character_end_times_seconds=[],
            ),
            normalized_alignment=SimpleNamespace(
                characters=[],
                character_start_times_seconds=[],
                character_end_times_seconds=[],
            ),
            request_id="r1",
            history_item_id=None,
        )
        client = _make_sdk_client(response)
        p = ElevenLabsTTSProvider(_sdk_client=client)
        p.initialize()
        with pytest.raises(AudioValidationError):
            p.synthesize(_default_request())

    def test_invalid_base64_raises_audio_validation_error(self):
        response = SimpleNamespace(
            audio_base_64="!!!invalid-base64!!!",
            alignment=SimpleNamespace(
                characters=[],
                character_start_times_seconds=[],
                character_end_times_seconds=[],
            ),
            normalized_alignment=SimpleNamespace(
                characters=[],
                character_start_times_seconds=[],
                character_end_times_seconds=[],
            ),
            request_id=None,
            history_item_id=None,
        )
        client = _make_sdk_client(response)
        p = ElevenLabsTTSProvider(_sdk_client=client)
        p.initialize()
        with pytest.raises(AudioValidationError):
            p.synthesize(_default_request())


# ── Utility functions ─────────────────────────────────────────────────────────


class TestOutputFormatMapping:
    def test_wav_22050(self):
        assert _elevenlabs_output_format("wav", 22050) == "wav_22050"

    def test_wav_44100(self):
        assert _elevenlabs_output_format("wav", 44100) == "wav_44100"

    def test_pcm_22050(self):
        assert _elevenlabs_output_format("pcm", 22050) == "pcm_22050"

    def test_mp3_any_rate(self):
        assert _elevenlabs_output_format("mp3", 44100) == "mp3_44100_128"

    def test_opus(self):
        assert _elevenlabs_output_format("opus", 48000) == "opus_48000_128"


class TestMeasureRmsDbfs:
    def test_silent_wav_returns_negative_inf(self):
        wav = _make_silent_wav(0.1)
        result = _measure_rms_dbfs(wav)
        assert result is not None
        import math

        assert math.isinf(result) and result < 0

    def test_non_silent_wav_returns_finite_value(self):
        n_samples = 1000
        amplitude = 16000
        samples = [amplitude] * n_samples
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(22050)
            wf.writeframes(struct.pack(f"<{n_samples}h", *samples))
        wav = buf.getvalue()
        result = _measure_rms_dbfs(wav)
        assert result is not None
        import math

        assert math.isfinite(result)
        assert result < 0

    def test_invalid_bytes_returns_none(self):
        result = _measure_rms_dbfs(b"not-a-wav")
        assert result is None


class TestExtractStatusCode:
    def test_status_code_attribute(self):
        exc = Exception("fail")
        exc.status_code = 429  # type: ignore[attr-defined]
        assert _extract_status_code(exc) == 429

    def test_status_attribute(self):
        exc = Exception("fail")
        exc.status = 500  # type: ignore[attr-defined]
        assert _extract_status_code(exc) == 500

    def test_no_code_returns_none(self):
        exc = Exception("generic error")
        assert _extract_status_code(exc) is None

    def test_http_in_message(self):
        exc = Exception("HTTP 503 service unavailable")
        assert _extract_status_code(exc) == 503


def _meta(response, *, rms_dbfs, seed, el_format="wav_22050") -> dict:
    return json.loads(
        _build_provider_metadata_json(response, rms_dbfs=rms_dbfs, seed=seed, el_format=el_format)
    )


class TestBuildProviderMetadataJson:
    def test_contains_provider_key(self):
        parsed = _meta(_make_mock_response(), rms_dbfs=-20.0, seed=42)
        assert parsed["provider"] == ELEVENLABS_PROVIDER_NAME

    def test_contains_seed(self):
        parsed = _meta(_make_mock_response(), rms_dbfs=None, seed=999)
        assert parsed["seed"] == 999

    def test_seed_none_when_not_set(self):
        parsed = _meta(_make_mock_response(), rms_dbfs=None, seed=None)
        assert parsed["seed"] is None

    def test_loudness_rms_present(self):
        parsed = _meta(_make_mock_response(), rms_dbfs=-18.5, seed=None)
        assert parsed["loudness"]["rms_dbfs"] == pytest.approx(-18.5)
