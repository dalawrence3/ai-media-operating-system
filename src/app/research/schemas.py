"""Provider-output schemas for Phase 4.2 claim extraction.

Schema convention: extra='forbid' so unexpected fields surface as validation
errors rather than being silently dropped, making schema drift detectable.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ExtractedClaim(BaseModel):
    """LLM-supplied fields only.

    Deterministic fields (support status, offsets, page number,
    requires_date_review) are computed locally after the call.
    """

    model_config = ConfigDict(extra="forbid")

    claim_text: str = Field(min_length=10, max_length=500)
    claim_type: Literal["factual", "statistical", "attribution", "definition"]
    supporting_quote: str | None = Field(default=None, max_length=300)


class ClaimExtractionOutput(BaseModel):
    """Top-level structured output returned by the LLM for one chunk."""

    model_config = ConfigDict(extra="forbid")

    claims: list[ExtractedClaim] = Field(max_length=10)
