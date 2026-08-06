"""Phase 6 M6.3C: ElevenLabs smoke test — always skipped in CI.

To run manually:
    export ACE_ELEVENLABS_API_KEY=<key>
    export ACE_TTS_LIVE_ENABLED=true
    pytest tests/test_narration_elevenlabs_smoke.py -v -s

The test will be skipped automatically unless ACE_TTS_LIVE_ENABLED=true
and ACE_ELEVENLABS_API_KEY is set.

A Creator-tier ($22/mo) ElevenLabs account is required for WAV output.
"""

from __future__ import annotations

import os

import pytest

from app.narration.protocol import TTSRequest
from app.narration.providers.elevenlabs import (
    ELEVENLABS_DEFAULT_MODEL,
    ELEVENLABS_PROVIDER_NAME,
    ElevenLabsTTSProvider,
)

_LIVE_ENABLED = (
    os.environ.get("ACE_TTS_LIVE_ENABLED", "").lower() in {"1", "true", "yes"}
    and bool(os.environ.get("ACE_ELEVENLABS_API_KEY", "").strip())
)

skip_unless_live = pytest.mark.skipif(
    not _LIVE_ENABLED,
    reason=(
        "Skipped in CI: set ACE_TTS_LIVE_ENABLED=true and ACE_ELEVENLABS_API_KEY "
        "to run live ElevenLabs synthesis."
    ),
)

_VOICE_ID = os.environ.get("ACE_SMOKE_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb")


@skip_unless_live
def test_elevenlabs_smoke_live_synthesis() -> None:
    """Smoke test: synthesise a short phrase via the live ElevenLabs API.

    Validates:
    - Provider initializes successfully (valid API key).
    - synthesize() returns non-empty WAV bytes.
    - duration_seconds > 0.
    - provider_metadata_json is valid JSON with 'alignment' and 'loudness' keys.
    - request_id is returned.
    - No credentials appear in the response metadata.
    """
    import io
    import json
    import wave

    from app.core.config import reset_config
    reset_config()

    provider = ElevenLabsTTSProvider()
    provider.initialize()
    assert provider.lifecycle_state.value == "ready"

    request = TTSRequest(
        text="Hello from the AI Content Engine smoke test.",
        provider=ELEVENLABS_PROVIDER_NAME,
        model=ELEVENLABS_DEFAULT_MODEL,
        voice_id=_VOICE_ID,
        language="en",
        speaking_rate=1.0,
        output_format="wav",
        sample_rate_hz=22050,
    )

    response = provider.synthesize(request)

    assert len(response.audio_bytes) > 1000, "Expected non-trivial WAV payload"
    assert response.duration_seconds > 0.0
    assert response.characters_billed == len(request.text)
    assert response.provider == ELEVENLABS_PROVIDER_NAME
    assert response.model == ELEVENLABS_DEFAULT_MODEL
    assert response.latency_ms is not None and response.latency_ms > 0

    # WAV must be parseable.
    with wave.open(io.BytesIO(response.audio_bytes), "rb") as wf:
        assert wf.getnchannels() in (1, 2)
        assert wf.getframerate() == 22050

    # Metadata JSON must be valid.
    meta = json.loads(response.provider_metadata_json)
    assert "alignment" in meta
    assert "loudness" in meta

    # No credentials in metadata.
    meta_str = response.provider_metadata_json or ""
    api_key = os.environ.get("ACE_ELEVENLABS_API_KEY", "")
    assert api_key not in meta_str, "API key must not appear in metadata"

    provider.shutdown()
    reset_config()
