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
