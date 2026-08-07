"""Cost record creation and attribution."""

from __future__ import annotations

import uuid
from typing import Any

from app.control_plane import repository as repo
from app.control_plane.models import CostRecord, CostRecordDraft


def record_cost(
    conn: Any,
    *,
    workspace_id: str,
    provider_key: str,
    cost_unit: str,
    quantity: float,
    usd_equivalent: float,
    channel_id: str | None = None,
    platform_account_id: str | None = None,
    operation_execution_id: str | None = None,
    description: str | None = None,
) -> CostRecord:
    draft = CostRecordDraft(
        id=str(uuid.uuid4()),
        workspace_id=workspace_id,
        provider_key=provider_key,
        cost_unit=cost_unit,
        quantity=quantity,
        usd_equivalent=usd_equivalent,
        channel_id=channel_id,
        platform_account_id=platform_account_id,
        operation_execution_id=operation_execution_id,
        description=description,
    )
    return repo.create_cost_record(conn, draft)
