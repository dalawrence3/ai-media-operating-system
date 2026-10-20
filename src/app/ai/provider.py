"""Provider protocol and request/response data classes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel


@dataclass
class AIRequest:
    """All parameters for a single AI completion call."""

    system: str
    user: str
    model: str
    # Phase 7C.1 — None (the default) means "no opinion, use the model's
    # own default sampling behavior". Newer Claude models (confirmed:
    # claude-sonnet-5) reject an explicit temperature outright with a 400
    # invalid_request_error ("`temperature` is deprecated for this
    # model."), which is exactly what broke cold_start.py's discovery
    # call — it never had a reason to want a specific value, it just
    # inherited this dataclass's old hardcoded 0.3 default. Callers that
    # genuinely need a specific value (content/generator.py,
    # research/extractor.py) already always pass one explicitly and are
    # unaffected by this default changing.
    temperature: float | None = None
    max_tokens: int = 1024
    response_schema: type[BaseModel] | None = None
    prompt_name: str = ""
    prompt_version: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AIResponse:
    """Normalised response from any provider — no provider-specific types leak out."""

    raw_text: str
    provider_name: str
    model: str
    input_tokens: int
    output_tokens: int
    duration_ms: int
    retry_count: int
    parsed: BaseModel | None = None
    request_id: str | None = None
    stop_reason: str | None = None


@runtime_checkable
class AIProvider(Protocol):
    """Provider-independent interface.  Concrete providers must satisfy this protocol."""

    @property
    def name(self) -> str: ...

    def complete(self, request: AIRequest) -> AIResponse: ...
