"""Phase 2 demonstration output schema.

Only the minimal schema needed to prove the structured-output infrastructure.
Full content schemas (scripts, critique, metadata) belong in later phases.

Schema convention
-----------------
All AI output schemas used to validate machine-consumed provider responses
must set ``model_config = ConfigDict(extra="forbid")``.  This ensures that
unexpected fields from the provider surface as validation errors rather than
being silently dropped, making schema drift detectable and auditable.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class EchoOutput(BaseModel):
    """Demonstration schema: provider echoes supplied text as structured JSON."""

    model_config = ConfigDict(extra="forbid")

    echo: str = Field(description="The input text echoed verbatim.")
