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
    temperature: float = 0.3
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
