"""Phase 6 M6.2: Fake TTS provider for testing and local development.

M6.3B: FakeTTSProvider now implements ProviderLifecycle and exposes module-level
capability / metadata / version / config constants.  Synthesis behaviour is
identical to M6.2 — only additive attributes are added.
"""

from __future__ import annotations

import io
import struct
import wave

from app.narration.protocol import TTSProvider, TTSRequest, TTSResponse

FAKE_PROVIDER_NAME = "fake"
FAKE_MODEL_NAME = "fake/FAKE"
FAKE_VOICE_ID = "fake-voice"
FAKE_WORDS_PER_MINUTE = 150
FAKE_INTEGRATION_VERSION = "0.1.0"


class FakeTTSProvider:
    """Deterministic TTS provider that emits silence WAV bytes.

    Satisfies the TTSProvider Protocol without any network calls.
    Injectable fail_on can trigger SynthesisError for error-path tests.
    Also satisfies ProviderLifecycle — lifecycle state is observable but does
    NOT gate synthesis (backward-compatible with all M6.2 callers).
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
        from app.narration.lifecycle import ProviderLifecycleState

        self._lifecycle_state = ProviderLifecycleState.CREATED

    # ── TTSProvider Protocol ──────────────────────────────────────────────────

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
            raise SynthesisError(f"FakeTTSProvider: injected failure on call {self._call_count}")

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

    # ── ProviderLifecycle Protocol ────────────────────────────────────────────

    def initialize(self) -> None:
        from app.narration.lifecycle import ProviderLifecycleState

        self._lifecycle_state = ProviderLifecycleState.READY

    def shutdown(self) -> None:
        from app.narration.lifecycle import ProviderLifecycleState

        self._lifecycle_state = ProviderLifecycleState.SHUTDOWN

    @property
    def lifecycle_state(self):
        return self._lifecycle_state

    # ── Metadata accessor ─────────────────────────────────────────────────────

    @property
    def provider_metadata(self):
        return FAKE_METADATA

    # ── Internal helpers ──────────────────────────────────────────────────────

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


# ── Module-level capability / metadata / version / config constants ───────────
# Built via helper functions to avoid circular imports at class-body time.


def _build_fake_capabilities():
    from app.narration.capabilities import ProviderCapabilities, ProviderFeatureFlags
    from app.narration.constants import (
        NARRATION_DEFAULT_OUTPUT_FORMAT,
        PROVIDER_LANGUAGE_WILDCARD,
    )

    flags = ProviderFeatureFlags(supports_speaking_rate=True)
    caps = ProviderCapabilities(
        supported_output_formats=frozenset({NARRATION_DEFAULT_OUTPUT_FORMAT}),
        supported_languages=frozenset({PROVIDER_LANGUAGE_WILDCARD}),
        supported_sample_rates_hz=frozenset({8000, 16000, 22050, 44100, 48000}),
        min_speaking_rate=0.25,
        max_speaking_rate=4.0,
        max_characters_per_request=100_000,
        feature_flags=flags,
    )
    return caps, flags


def _build_fake_metadata(capabilities, flags):
    from app.narration.metadata import ProviderMetadata

    return ProviderMetadata(
        provider_name=FAKE_PROVIDER_NAME,
        provider_version=FAKE_INTEGRATION_VERSION,
        model_id=FAKE_MODEL_NAME,
        api_version=None,
        sdk_name=None,
        sdk_version=None,
        capabilities=capabilities,
        feature_flags=flags,
    )


def _build_fake_version():
    from app.narration.constants import (
        NARRATION_ALGORITHM_VERSION,
        NARRATION_SCHEMA_VERSION,
    )
    from app.narration.versioning import ProviderVersion

    return ProviderVersion(
        provider_name=FAKE_PROVIDER_NAME,
        integration_version=FAKE_INTEGRATION_VERSION,
        schema_version=NARRATION_SCHEMA_VERSION,
        algorithm_version=NARRATION_ALGORITHM_VERSION,
    )


def _build_fake_config():
    from app.narration.config import ProviderConfig
    from app.narration.constants import (
        NARRATION_DEFAULT_LANGUAGE,
        NARRATION_DEFAULT_OUTPUT_FORMAT,
        NARRATION_DEFAULT_SAMPLE_RATE_HZ,
        NARRATION_DEFAULT_SPEAKING_RATE,
    )

    return ProviderConfig(
        provider_name=FAKE_PROVIDER_NAME,
        model_id=FAKE_MODEL_NAME,
        voice_id=FAKE_VOICE_ID,
        language=NARRATION_DEFAULT_LANGUAGE,
        speaking_rate=NARRATION_DEFAULT_SPEAKING_RATE,
        output_format=NARRATION_DEFAULT_OUTPUT_FORMAT,
        sample_rate_hz=NARRATION_DEFAULT_SAMPLE_RATE_HZ,
    )


FAKE_CAPABILITIES, FAKE_FEATURE_FLAGS = _build_fake_capabilities()
FAKE_METADATA = _build_fake_metadata(FAKE_CAPABILITIES, FAKE_FEATURE_FLAGS)
FAKE_PROVIDER_VERSION = _build_fake_version()
FAKE_PROVIDER_CONFIG = _build_fake_config()

# ── Protocol assertions ───────────────────────────────────────────────────────

assert isinstance(FakeTTSProvider(), TTSProvider), (
    "FakeTTSProvider must satisfy TTSProvider Protocol"
)

from app.narration.lifecycle import ProviderLifecycle  # noqa: E402

assert isinstance(FakeTTSProvider(), ProviderLifecycle), (
    "FakeTTSProvider must satisfy ProviderLifecycle Protocol"
)
