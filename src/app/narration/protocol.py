"""Phase 6 M6.2: Provider-neutral TTS protocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class TTSRequest:
    text: str
    provider: str
    model: str
    voice_id: str
    language: str
    speaking_rate: float
    output_format: str
    sample_rate_hz: int
    style: str | None = None
    stability: float | None = None
    similarity_boost: float | None = None
    settings_json: str = "{}"
    # Phase 7B.1, §1/§3 — set ONLY when a segment explicitly overrides the
    # voice profile's own language (e.g. a language-quiz's target-language
    # reveal). `language` above is always populated (asset metadata/hashing
    # use it unconditionally); this field distinguishes "genuine override"
    # from "fell back to the voice profile", so a provider forwards an
    # explicit language_code to its API only when one was actually asked
    # for — every call that never sets this stays byte-identical to before.
    language_override: str | None = None


@dataclass
class TTSResponse:
    audio_bytes: bytes
    provider: str
    model: str
    voice_id: str
    characters_billed: int
    duration_seconds: float
    request_id: str | None = None
    provider_metadata_json: str | None = None
    latency_ms: int | None = None


@runtime_checkable
class TTSProvider(Protocol):
    def synthesize(self, request: TTSRequest) -> TTSResponse: ...

    @property
    def provider_name(self) -> str: ...

    @property
    def default_model(self) -> str: ...
