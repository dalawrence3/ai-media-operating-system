"""Phase 6 M6.2: Fake TTS provider for testing and local development."""

from __future__ import annotations

import io
import struct
import wave

from app.narration.protocol import TTSProvider, TTSRequest, TTSResponse

FAKE_PROVIDER_NAME = "fake"
FAKE_MODEL_NAME = "fake/FAKE"
FAKE_VOICE_ID = "fake-voice"
FAKE_WORDS_PER_MINUTE = 150


class FakeTTSProvider:
    """Deterministic TTS provider that emits silence WAV bytes.

    Satisfies the TTSProvider Protocol without any network calls.
    Injectable fail_on can trigger SynthesisError for error-path tests.
    """

    def __init__(
        self,
        *,
        fail_on: set[int] | None = None,
        words_per_minute: int = FAKE_WORDS_PER_MINUTE,
    ) -> None:
        self._fail_on: set[int] = fail_on or set()
        self._words_per_minute = words_per_minute
        self._call_count = 0

    @property
    def provider_name(self) -> str:
        return FAKE_PROVIDER_NAME

    @property
    def default_model(self) -> str:
        return FAKE_MODEL_NAME

    def synthesize(self, request: TTSRequest) -> TTSResponse:
        from app.narration.errors import SynthesisError

        self._call_count += 1
        if self._call_count in self._fail_on:
            raise SynthesisError(
                f"FakeTTSProvider: injected failure on call {self._call_count}"
            )

        word_count = len(request.text.split())
        duration_seconds = max(0.1, word_count / self._words_per_minute * 60.0)
        audio_bytes = self._build_wav_bytes(
            duration_seconds=duration_seconds,
            sample_rate_hz=request.sample_rate_hz,
        )
        characters_billed = len(request.text)

        return TTSResponse(
            audio_bytes=audio_bytes,
            provider=FAKE_PROVIDER_NAME,
            model=FAKE_MODEL_NAME,
            voice_id=request.voice_id,
            characters_billed=characters_billed,
            duration_seconds=duration_seconds,
            request_id=f"fake-req-{self._call_count}",
            latency_ms=1,
        )

    @staticmethod
    def _build_wav_bytes(*, duration_seconds: float, sample_rate_hz: int) -> bytes:
        num_frames = int(duration_seconds * sample_rate_hz)
        num_channels = 1
        sample_width = 2  # 16-bit PCM
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(num_channels)
            wf.setsampwidth(sample_width)
            wf.setframerate(sample_rate_hz)
            wf.writeframes(struct.pack(f"<{num_frames}h", *([0] * num_frames)))
        return buf.getvalue()


assert isinstance(FakeTTSProvider(), TTSProvider), (
    "FakeTTSProvider must satisfy TTSProvider Protocol"
)
