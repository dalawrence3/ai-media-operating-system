"""Named constants for Phase 6 M6.1 production plan generation.

Changing any version constant changes the production plan input_hash, which
causes a new plan to be created for the same script (correct behaviour when
deterministic algorithms are updated).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Algorithm version identifiers (changing any bumps the input hash)
# ---------------------------------------------------------------------------

PRODUCTION_PLAN_SCHEMA_VERSION: str = "ProductionPlan-v1"
PRODUCTION_PLAN_RENDERER_VERSION: str = "production-renderer-v1"
PRODUCTION_DURATION_VERSION: str = "duration-150wpm-v1"

# ---------------------------------------------------------------------------
# Rejection reason codes (must match the DB CHECK constraint)
# ---------------------------------------------------------------------------

REJECTION_REASON_CODES: frozenset[str] = frozenset(
    {
        "segment_structure",
        "narration_wording",
        "pacing",
        "duration",
        "citation_mapping",
        "evidence_concern",
        "format_mismatch",
        "other",
    }
)

REJECTION_REASON_CODE_REQUIRING_NOTES: str = "other"
