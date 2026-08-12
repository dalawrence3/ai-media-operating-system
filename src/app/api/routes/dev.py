"""Dev-only routes — available only when ACE_ENV=development.

Never register this router in staging or production.
Used exclusively by E2E tests to reset deterministic seed fixtures.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import get_db
from app.learning.constants import STATUS_PENDING

router = APIRouter(prefix="/dev", tags=["dev"])


@router.post("/reset-learning-fixtures", status_code=200)
def reset_learning_fixtures(conn: Any = Depends(get_db)) -> dict[str, int]:
    """Reset seeded learning recommendations back to pending status.

    Targets only rows with input_hash matching 'seed-dev-rec-*' so real
    user data is never touched. Safe to call repeatedly between E2E runs.
    """
    cur = conn.execute(
        """UPDATE optimization_recommendations
              SET status = ?, superseded_at = NULL, superseded_by_id = NULL
            WHERE input_hash LIKE 'seed-dev-rec-%'""",
        (STATUS_PENDING,),
    )
    conn.commit()
    return {"reset": cur.rowcount}
