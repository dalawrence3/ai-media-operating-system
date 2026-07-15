"""Record AI call usage and estimated cost to the database."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from app.ai.errors import UnknownModelPricingError
from app.ai.pricing import PricingRegistry
from app.ai.provider import AIRequest, AIResponse
from app.core.logging import get_logger

logger = get_logger(__name__)

_registry = PricingRegistry()


def record_ai_call(
    conn: sqlite3.Connection,
    request: AIRequest,
    response: AIResponse | None,
    *,
    status: str,
    error_category: str | None = None,
    error_message: str | None = None,
    run_id: int | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
    pricing: PricingRegistry | None = None,
) -> int:
    """Insert one row into ``ai_calls`` and return its ``id``."""
    pr = pricing or _registry
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")

    provider = response.provider_name if response else "unknown"
    model = response.model if response else request.model
    input_tokens: int | None = response.input_tokens if response else None
    output_tokens: int | None = response.output_tokens if response else None
    duration_ms: int | None = response.duration_ms if response else None
    retry_count: int = response.retry_count if response else 0

    cost: float | None = None
    if input_tokens is not None and output_tokens is not None:
        try:
            cost = pr.estimate_cost(model, input_tokens, output_tokens)
        except UnknownModelPricingError:
            logger.warning(
                "No pricing entry for model %r — estimated_cost_usd recorded as NULL. "
                "Add the model to the pricing registry to track costs.",
                model,
            )

    cur = conn.execute(
        """
        INSERT INTO ai_calls
            (provider, model, prompt_name, prompt_version,
             input_tokens, output_tokens, estimated_cost_usd,
             duration_ms, retry_count, status,
             error_category, error_message, run_id,
             created_at, completed_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            provider,
            model,
            request.prompt_name,
            request.prompt_version,
            input_tokens,
            output_tokens,
            cost,
            duration_ms,
            retry_count,
            status,
            error_category,
            error_message,
            run_id,
            started_at or now,
            completed_at or now,
        ),
    )
    conn.commit()
    cost_display = f"{cost:.6f}" if cost is not None else "unknown"
    logger.debug(
        "ai_call id=%d provider=%s model=%s tokens=%s/%s cost=%s status=%s",
        cur.lastrowid,
        provider,
        model,
        input_tokens,
        output_tokens,
        cost_display,
        status,
    )
    return cur.lastrowid  # type: ignore[return-value]
