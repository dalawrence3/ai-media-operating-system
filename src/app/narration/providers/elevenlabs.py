"""Phase 6 M6.3C: ElevenLabs TTS provider adapter.

Implements TTSProvider and ProviderLifecycle against the ElevenLabs API.

Design constraints:
  - No live network call unless ACE_TTS_LIVE_ENABLED=true and the API key is set.
  - The SDK client is constructed in initialize(); never in __init__().
  - Credentials never appear in logs, DB rows, or reproducibility dicts.
  - Provider-specific parameters (seed, speaker boost, pronunciation dicts)
    are isolated entirely inside this module.
  - The pipeline outside this adapter never branches on provider identity.
"""

from __future__ import annotations

import base64
import json
import logging
import math
import struct
import time

from app.narration.capabilities import ProviderCapabilities, ProviderFeatureFlags
from app.narration.config import ProviderConfig
from app.narration.constants import (
    NARRATION_ALGORITHM_VERSION,
    NARRATION_DEFAULT_LANGUAGE,
    NARRATION_DEFAULT_OUTPUT_FORMAT,
    NARRATION_DEFAULT_SAMPLE_RATE_HZ,
    NARRATION_DEFAULT_SPEAKING_RATE,
    NARRATION_DURATION_DEVIATION_THRESHOLD,
    NARRATION_SCHEMA_VERSION,
)
from app.narration.errors import (
    AudioValidationError,
    ProviderCredentialError,
    ProviderRateLimitError,
    SynthesisError,
)
from app.narration.lifecycle import ProviderLifecycle, ProviderLifecycleState
from app.narration.metadata import ProviderMetadata
from app.narration.protocol import TTSProvider, TTSRequest, TTSResponse
from app.narration.versioning import ProviderVersion

logger = logging.getLogger(__name__)

# ── Provider identity constants ───────────────────────────────────────────────

ELEVENLABS_PROVIDER_NAME: str = "elevenlabs"
ELEVENLABS_DEFAULT_MODEL: str = "eleven_multilingual_v2"
ELEVENLABS_FLASH_MODEL: str = "eleven_flash_v2_5"
ELEVENLABS_INTEGRATION_VERSION: str = "1.0.0"

# ElevenLabs API base URL (used in mock transport construction for tests).
ELEVENLABS_API_BASE_URL: str = "https://api.elevenlabs.io"

# RMS dBFS reference level for YouTube Shorts loudness advisory.
# -14 LUFS target ≈ -2 dBFS RMS for typical speech (RMS is not LUFS;
# this is an advisory heuristic only — no normalization occurs here).
# Normalization is deferred to Phase 8 rendering.
_RMS_DBFS_ADVISORY: float = -2.0
_RMS_DBFS_TOLERANCE: float = 3.0

# Retry configuration.
_MAX_RETRIES: int = 3
_RETRY_BASE_DELAY_S: float = 1.0
_RETRY_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})


# ── Capabilities (module-level constants, built once) ─────────────────────────

def _build_elevenlabs_capabilities() -> tuple[ProviderCapabilities, ProviderFeatureFlags]:
    flags = ProviderFeatureFlags(
        supports_speaking_rate=True,
        supports_style_transfer=True,
        supports_emotion_control=True,
        supports_multi_speaker=False,
        supports_word_timestamps=True,
        supports_phoneme_timestamps=False,
        supports_streaming=True,
        supports_ssml=True,
        supports_stability=True,
        supports_similarity_boost=True,
        supports_alignment=True,
        supports_seed=True,
        supports_voice_cloning=True,
        supports_pronunciation_dictionary=True,
    )
    caps = ProviderCapabilities(
        supported_output_formats=frozenset({"wav", "mp3", "pcm", "opus"}),
        supported_languages=frozenset(
            {
                # BCP-47 codes for the 29 languages in eleven_multilingual_v2.
                # Wildcard is NOT used: we list the exact language set so the
                # DefaultProviderValidator can enforce language compatibility.
                "en", "en-US", "en-GB", "en-AU", "en-IN",
                "ja", "zh", "de", "hi", "fr", "fr-FR",
                "ko", "pt", "pt-BR", "pt-PT", "it", "es", "es-ES", "es-MX",
                "id", "nl", "tr", "pl", "sv", "bg", "ro", "ar", "cs", "el",
                "fi", "hr", "ms", "sk", "da", "ta", "uk",
                # Flash v2.5 adds Hungarian, Norwegian, Vietnamese.
                "hu", "nb", "vi",
            }
        ),
        supported_sample_rates_hz=frozenset({8000, 16000, 22050, 24000, 32000, 44100, 48000}),
        min_speaking_rate=0.7,
        max_speaking_rate=1.2,
        max_characters_per_request=10_000,
        feature_flags=flags,
    )
    return caps, flags


ELEVENLABS_CAPABILITIES, ELEVENLABS_FEATURE_FLAGS = _build_elevenlabs_capabilities()


def _build_elevenlabs_metadata(
    caps: ProviderCapabilities,
    flags: ProviderFeatureFlags,
    sdk_version: str,
) -> ProviderMetadata:
    return ProviderMetadata(
        provider_name=ELEVENLABS_PROVIDER_NAME,
        provider_version=ELEVENLABS_INTEGRATION_VERSION,
        model_id=ELEVENLABS_DEFAULT_MODEL,
        api_version="v1",
        sdk_name="elevenlabs",
        sdk_version=sdk_version,
        capabilities=caps,
        feature_flags=flags,
    )


def _build_elevenlabs_version() -> ProviderVersion:
    return ProviderVersion(
        provider_name=ELEVENLABS_PROVIDER_NAME,
        integration_version=ELEVENLABS_INTEGRATION_VERSION,
        schema_version=NARRATION_SCHEMA_VERSION,
        algorithm_version=NARRATION_ALGORITHM_VERSION,
    )


def _build_elevenlabs_config() -> ProviderConfig:
    return ProviderConfig(
        provider_name=ELEVENLABS_PROVIDER_NAME,
        model_id=ELEVENLABS_DEFAULT_MODEL,
        voice_id="",
        language=NARRATION_DEFAULT_LANGUAGE,
        speaking_rate=NARRATION_DEFAULT_SPEAKING_RATE,
        output_format=NARRATION_DEFAULT_OUTPUT_FORMAT,
        sample_rate_hz=NARRATION_DEFAULT_SAMPLE_RATE_HZ,
        stability=0.5,
        similarity_boost=0.75,
    )


ELEVENLABS_PROVIDER_VERSION: ProviderVersion = _build_elevenlabs_version()
ELEVENLABS_PROVIDER_CONFIG: ProviderConfig = _build_elevenlabs_config()


# ── Output format mapping ─────────────────────────────────────────────────────

def _elevenlabs_output_format(output_format: str, sample_rate_hz: int) -> str:
    """Map generic output_format + sample_rate_hz to an ElevenLabs format string.

    ElevenLabs accepts strings like "wav_22050", "mp3_44100_128", "pcm_22050".
    For WAV and PCM we construct from the sample rate; for MP3 we default to
    the highest-quality bitrate available on the Pro tier.
    """
    fmt = output_format.lower()
    if fmt == "wav":
        return f"wav_{sample_rate_hz}"
    if fmt == "pcm":
        return f"pcm_{sample_rate_hz}"
    if fmt == "mp3":
        return "mp3_44100_128"
    if fmt == "opus":
        return "opus_48000_128"
    return f"{fmt}_{sample_rate_hz}"


# ── Loudness measurement (RMS dBFS from WAV PCM) ─────────────────────────────

def _measure_rms_dbfs(wav_bytes: bytes) -> float | None:
    """Return RMS dBFS of the WAV audio, or None on parse error.

    RMS dBFS is not LUFS but correlates well enough for a warning heuristic.
    Full ITU-R BS.1770-4 measurement requires a K-weighting filter (numpy /
    pyloudnorm) and will be added during the Phase 8 rendering step.
    """
    import io
    import wave as _wave

    try:
        buf = io.BytesIO(wav_bytes)
        with _wave.open(buf, "rb") as wf:
            sample_width = wf.getsampwidth()
            n_frames = wf.getnframes()
            raw = wf.readframes(n_frames)
    except Exception:
        return None

    if not raw or sample_width not in (1, 2, 3, 4):
        return None

    fmt = {1: "b", 2: "h", 4: "i"}.get(sample_width)
    if fmt is None:
        return None

    try:
        samples = struct.unpack(f"<{len(raw) // sample_width}{fmt}", raw)
    except struct.error:
        return None

    if not samples:
        return None

    max_amplitude = float(1 << (sample_width * 8 - 1))
    sum_sq = sum(s * s for s in samples)
    rms = math.sqrt(sum_sq / len(samples))
    if rms == 0.0:
        return -math.inf
    return 20.0 * math.log10(rms / max_amplitude)


# ── Duration deviation check ──────────────────────────────────────────────────

def _check_duration_deviation(
    narration_text: str,
    speaking_rate: float,
    actual_seconds: float,
) -> None:
    """Warn if the audio duration deviates significantly from a word-count estimate.

    This is a safety check only; deviation does not fail synthesis.
    """
    word_count = len(narration_text.split())
    if word_count == 0:
        return
    wpm = 150.0 * speaking_rate
    estimated = word_count / wpm * 60.0
    if estimated <= 0:
        return
    deviation = abs(actual_seconds - estimated) / estimated
    if deviation > NARRATION_DURATION_DEVIATION_THRESHOLD:
        logger.warning(
            "ElevenLabs duration deviation %.0f%% "
            "(estimated=%.2fs actual=%.2fs words=%d)",
            deviation * 100,
            estimated,
            actual_seconds,
            word_count,
        )


# ── ElevenLabs provider ───────────────────────────────────────────────────────

class ElevenLabsTTSProvider:
    """Live ElevenLabs TTS adapter.

    Satisfies TTSProvider and ProviderLifecycle.

    Credentials:
        The API key is read from ACE_ELEVENLABS_API_KEY at initialize() time.
        ACE_TTS_LIVE_ENABLED must be "true" unless a mock SDK client is
        injected (_sdk_client parameter) — used only in automated tests.

    Test injection:
        Pass a pre-built mock object via _sdk_client to bypass all network
        calls.  The mock must expose the attribute path:
            client.text_to_speech.convert_with_timestamps(...)
        returning an object with:
            .audio_base64: str (base64-encoded WAV bytes)
            .alignment: object with
                .characters: list[str]
                .character_start_times_seconds: list[float]
                .character_end_times_seconds: list[float]
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        _sdk_client=None,
    ) -> None:
        self._api_key = api_key
        self._injected_client = _sdk_client
        self._sdk = None
        self._sdk_version: str = "unknown"
        self._lifecycle_state = ProviderLifecycleState.CREATED

    # ── TTSProvider Protocol ──────────────────────────────────────────────────

    @property
    def provider_name(self) -> str:
        return ELEVENLABS_PROVIDER_NAME

    @property
    def default_model(self) -> str:
        return ELEVENLABS_DEFAULT_MODEL

    def synthesize(self, request: TTSRequest) -> TTSResponse:
        """Call ElevenLabs and return a TTSResponse with WAV bytes + metadata.

        Retries up to _MAX_RETRIES times on 429 / 5xx responses.
        Alignment JSON is stored in provider_metadata_json for future use by
        the caption pipeline when timing_source='aligned' is desired.
        """
        if self._sdk is None:
            raise SynthesisError(
                "ElevenLabsTTSProvider.initialize() must be called before synthesize()"
            )

        settings = self._parse_settings(request.settings_json)
        el_format = _elevenlabs_output_format(request.output_format, request.sample_rate_hz)

        last_error: Exception | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                return self._call_api(
                    request, settings, el_format, attempt, retry_count=attempt - 1
                )
            except ProviderRateLimitError:
                raise
            except _RetryableError as exc:
                last_error = exc
                if attempt < _MAX_RETRIES:
                    delay = _RETRY_BASE_DELAY_S * (2 ** (attempt - 1))
                    logger.warning(
                        "ElevenLabs attempt %d/%d failed (%s); retrying in %.1fs",
                        attempt,
                        _MAX_RETRIES,
                        exc,
                        delay,
                    )
                    time.sleep(delay)
            except (SynthesisError, AudioValidationError):
                raise
            except Exception as exc:
                raise SynthesisError(f"ElevenLabs unexpected error: {exc}") from exc

        raise SynthesisError(
            f"ElevenLabs synthesis failed after {_MAX_RETRIES} attempts: {last_error}"
        )

    # ── ProviderLifecycle Protocol ────────────────────────────────────────────

    def initialize(self) -> None:
        """Validate credentials and build the SDK client.

        Raises ProviderCredentialError when:
          - no mock client is injected AND ACE_TTS_LIVE_ENABLED is not true, OR
          - no mock client is injected AND ACE_ELEVENLABS_API_KEY is not set.
        """
        if self._injected_client is not None:
            self._sdk = self._injected_client
            self._lifecycle_state = ProviderLifecycleState.READY
            return

        from app.core.config import get_config

        cfg = get_config()
        if not cfg.tts_live_enabled:
            raise ProviderCredentialError(
                "ElevenLabs live synthesis is disabled. "
                "Set ACE_TTS_LIVE_ENABLED=true to enable it."
            )
        api_key = self._api_key or cfg.elevenlabs_api_key
        if not api_key:
            raise ProviderCredentialError(
                "ACE_ELEVENLABS_API_KEY is not set. "
                "Export the ElevenLabs API key before running narration."
            )

        try:
            import elevenlabs as _el_module
            from elevenlabs import ElevenLabs

            self._sdk_version = getattr(_el_module, "__version__", "unknown")
            self._sdk = ElevenLabs(api_key=api_key)
        except ImportError as exc:
            raise ProviderCredentialError(
                "elevenlabs SDK is not installed. "
                "Run: pip install 'elevenlabs>=2.45.0,<3.0'"
            ) from exc

        self._lifecycle_state = ProviderLifecycleState.READY

    def shutdown(self) -> None:
        self._sdk = None
        self._lifecycle_state = ProviderLifecycleState.SHUTDOWN

    @property
    def lifecycle_state(self) -> ProviderLifecycleState:
        return self._lifecycle_state

    # ── Metadata accessor ─────────────────────────────────────────────────────

    @property
    def provider_metadata(self) -> ProviderMetadata:
        return _build_elevenlabs_metadata(
            ELEVENLABS_CAPABILITIES,
            ELEVENLABS_FEATURE_FLAGS,
            self._sdk_version,
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _parse_settings(settings_json: str) -> dict:
        try:
            parsed = json.loads(settings_json)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, ValueError):
            return {}

    def _call_api(
        self,
        request: TTSRequest,
        settings: dict,
        el_format: str,
        attempt: int,
        *,
        retry_count: int = 0,
    ) -> TTSResponse:
        """Make a single API call to ElevenLabs /with-timestamps."""
        seed: int | None = settings.get("seed")
        use_speaker_boost: bool = bool(settings.get("use_speaker_boost", True))
        pronunciation_dict_ids: list[str] = settings.get("pronunciation_dictionary_ids", [])
        previous_request_ids: list[str] = settings.get("previous_request_ids", [])

        try:
            from elevenlabs.types import VoiceSettings
        except ImportError:
            from types import SimpleNamespace as VoiceSettings  # type: ignore[assignment]

        voice_settings = VoiceSettings(
            stability=request.stability if request.stability is not None else 0.5,
            similarity_boost=(
                request.similarity_boost if request.similarity_boost is not None else 0.75
            ),
            style=float(request.style) if request.style is not None else 0.0,
            use_speaker_boost=use_speaker_boost,
            speed=float(request.speaking_rate),
        )

        kwargs: dict = {
            "voice_id": request.voice_id,
            "text": request.text,
            "model_id": request.model,
            "output_format": el_format,
            "voice_settings": voice_settings,
        }
        if seed is not None:
            kwargs["seed"] = int(seed)
        if pronunciation_dict_ids:
            from elevenlabs.types import PronunciationDictionaryVersionLocator
            kwargs["pronunciation_dictionary_locators"] = [
                PronunciationDictionaryVersionLocator(
                    pronunciation_dictionary_id=did,
                    version_id=None,
                )
                for did in pronunciation_dict_ids
            ]
        if previous_request_ids:
            kwargs["previous_request_ids"] = previous_request_ids[:3]

        t0 = time.monotonic()
        try:
            response = self._sdk.text_to_speech.convert_with_timestamps(**kwargs)
        except Exception as exc:
            status = _extract_status_code(exc)
            if status in _RETRY_STATUSES:
                if status == 429:
                    if attempt >= _MAX_RETRIES:
                        raise ProviderRateLimitError(ELEVENLABS_PROVIDER_NAME, attempt) from exc
                raise _RetryableError(f"HTTP {status}: {exc}") from exc
            raise SynthesisError(f"ElevenLabs API error: {exc}") from exc

        latency_ms = int((time.monotonic() - t0) * 1000)

        # Decode audio bytes.
        # SDK Pydantic model: Python attr 'audio_base_64', JSON alias 'audio_base64'.
        # Mock fixtures use SimpleNamespace, so getattr handles both shapes.
        try:
            raw_b64 = getattr(response, "audio_base_64", None) or getattr(
                response, "audio_base64", None
            )
            if raw_b64 is None:
                raise ValueError("no audio_base_64 field on response")
            audio_bytes = base64.b64decode(raw_b64)
        except Exception as exc:
            raise AudioValidationError(f"ElevenLabs returned invalid base64 audio: {exc}") from exc

        if not audio_bytes:
            raise AudioValidationError("ElevenLabs returned empty audio payload")

        # Measure RMS loudness and warn if outside target.
        rms_dbfs = _measure_rms_dbfs(audio_bytes)
        _warn_loudness(rms_dbfs, request.voice_id, request.model)

        # Determine duration from audio length or alignment data.
        duration_seconds = _infer_duration(response, audio_bytes, request.sample_rate_hz)

        # Duration-deviation warning.
        _check_duration_deviation(request.text, request.speaking_rate, duration_seconds)

        # Build provider metadata JSON (alignment + loudness measurement + retry count).
        # retry_count is included here so the tts_calls aggregate row (which has no
        # dedicated retry_count column in SCHEMA_VERSION 12) retains attempt observability.
        provider_metadata = _build_provider_metadata_json(
            response,
            rms_dbfs,
            seed,
            el_format,
            retry_count=retry_count,
        )

        # Extract provider request ID.
        request_id = _extract_request_id(response)

        characters_billed = len(request.text)

        return TTSResponse(
            audio_bytes=audio_bytes,
            provider=ELEVENLABS_PROVIDER_NAME,
            model=request.model,
            voice_id=request.voice_id,
            characters_billed=characters_billed,
            duration_seconds=duration_seconds,
            request_id=request_id,
            provider_metadata_json=provider_metadata,
            latency_ms=latency_ms,
        )


# ── Module-level metadata constants (SDK version resolved at import time) ─────

def _get_sdk_version() -> str:
    try:
        import elevenlabs as _el

        return getattr(_el, "__version__", "unknown")
    except ImportError:
        return "not_installed"


ELEVENLABS_METADATA: ProviderMetadata = _build_elevenlabs_metadata(
    ELEVENLABS_CAPABILITIES,
    ELEVENLABS_FEATURE_FLAGS,
    _get_sdk_version(),
)


# ── Internal utilities ────────────────────────────────────────────────────────

class _RetryableError(Exception):
    """Internal sentinel: the API call failed with a retryable status code."""


def _extract_status_code(exc: Exception) -> int | None:
    """Best-effort extraction of an HTTP status code from an SDK exception."""
    for attr in ("status_code", "status", "code", "http_status"):
        val = getattr(exc, attr, None)
        if isinstance(val, int):
            return val
    msg = str(exc)
    for prefix in ("status_code=", "HTTP ", "Status: ", "status: "):
        idx = msg.find(prefix)
        if idx != -1:
            tail = msg[idx + len(prefix):].lstrip()
            try:
                return int(tail[:3])
            except ValueError:
                pass
    return None


def _extract_request_id(response) -> str | None:
    """Return the ElevenLabs request/history-item ID from the response object."""
    for attr in ("request_id", "history_item_id", "id"):
        val = getattr(response, attr, None)
        if isinstance(val, str) and val:
            return val
    return None


def _infer_duration(response, audio_bytes: bytes, sample_rate_hz: int) -> float:
    """Infer audio duration from alignment data or WAV header.

    The real SDK returns alignment as Optional[CharacterAlignmentResponseModel];
    we guard against None explicitly before attribute access.
    """
    # Try alignment timestamps first (most accurate).
    try:
        al = getattr(response, "alignment", None)
        if al is not None:
            ends = al.character_end_times_seconds
            if ends:
                return float(ends[-1])
    except (AttributeError, TypeError, IndexError):
        pass

    # Fall back to WAV header parsing.
    import io
    import wave as _wave

    try:
        buf = io.BytesIO(audio_bytes)
        with _wave.open(buf, "rb") as wf:
            return wf.getnframes() / wf.getframerate()
    except Exception:
        pass

    # Last resort: estimate from byte count (16-bit PCM at given sample rate).
    bytes_per_sample = 2
    n_channels = 1
    total_samples = len(audio_bytes) / (bytes_per_sample * n_channels)
    return max(0.1, total_samples / sample_rate_hz)


def _warn_loudness(rms_dbfs: float | None, voice_id: str, model: str) -> None:
    """Log a warning when RMS dBFS is outside the advisory range.

    Measurement is RMS amplitude level in dBFS — not LUFS, not EBU R128
    integrated loudness. No normalization is applied; deferred to rendering.
    """
    if rms_dbfs is None:
        return
    if rms_dbfs == -math.inf:
        logger.warning(
            "ElevenLabs audio appears to be silence (voice=%s model=%s)",
            voice_id,
            model,
        )
        return
    if abs(rms_dbfs - _RMS_DBFS_ADVISORY) > _RMS_DBFS_TOLERANCE:
        logger.warning(
            "ElevenLabs audio RMS level outside advisory range: "
            "rms_dbfs=%.1f advisory_rms_dbfs=%.1f "
            "(voice=%s model=%s) — normalization deferred to rendering",
            rms_dbfs,
            _RMS_DBFS_ADVISORY,
            voice_id,
            model,
        )


def _build_provider_metadata_json(
    response,
    rms_dbfs: float | None,
    seed: int | None,
    el_format: str,
    *,
    retry_count: int = 0,
) -> str:
    """Serialise alignment, loudness, and retry data into provider_metadata_json.

    retry_count is the number of preceding failed attempts (0 = succeeded first try).
    This field provides attempt observability for the aggregate tts_calls row, which
    has no dedicated retry_count column in SCHEMA_VERSION 12.

    The real SDK alignment field is Optional[CharacterAlignmentResponseModel]; we check
    for None explicitly rather than relying on AttributeError to be safe with both the
    real SDK response and SimpleNamespace test fixtures.
    """
    def _extract_alignment(obj) -> dict:
        al = getattr(obj, "alignment", None)
        if al is None:
            return {}
        try:
            return {
                "characters": list(al.characters),
                "character_start_times_seconds": list(al.character_start_times_seconds),
                "character_end_times_seconds": list(al.character_end_times_seconds),
            }
        except AttributeError:
            return {}

    def _extract_normalized_alignment(obj) -> dict:
        nal = getattr(obj, "normalized_alignment", None)
        if nal is None:
            return {}
        try:
            return {
                "characters": list(nal.characters),
                "character_start_times_seconds": list(nal.character_start_times_seconds),
                "character_end_times_seconds": list(nal.character_end_times_seconds),
            }
        except AttributeError:
            return {}

    meta = {
        "provider": ELEVENLABS_PROVIDER_NAME,
        "el_output_format": el_format,
        "seed": seed,
        "retry_count": retry_count,
        "alignment": _extract_alignment(response),
        "normalized_alignment": _extract_normalized_alignment(response),
        "loudness": {
            "rms_dbfs": rms_dbfs,
            "advisory_rms_dbfs": _RMS_DBFS_ADVISORY,
            "measurement": "rms_amplitude_dbfs",
            "unit": "dBFS",
            "note": (
                "RMS amplitude level in dBFS. "
                "This is NOT LUFS and NOT EBU R128 integrated loudness. "
                "No normalization is applied. "
                "Loudness normalization is deferred to Phase 8 rendering."
            ),
        },
    }
    return json.dumps(meta, ensure_ascii=True)


# ── Protocol assertions ───────────────────────────────────────────────────────

assert isinstance(ElevenLabsTTSProvider(), TTSProvider), (
    "ElevenLabsTTSProvider must satisfy TTSProvider Protocol"
)
assert isinstance(ElevenLabsTTSProvider(), ProviderLifecycle), (
    "ElevenLabsTTSProvider must satisfy ProviderLifecycle Protocol"
)
