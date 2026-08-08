"""Deterministic date-review risk signals for Phase 4.2 claim extraction.

Computes requires_date_review locally after LLM extraction.
The LLM never sets this flag.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from app.research.constants import STAT_REVIEW_THRESHOLD_DAYS, TIME_SENSITIVE_REVIEW_DAYS
from app.research.models import ClaimType, SourceContent

_TIME_SENSITIVE_RE = re.compile(
    r"\b(current(?:ly)?|today|recent(?:ly)?|now|latest|this year|this month)\b",
    re.IGNORECASE,
)
_HISTORICAL_YEAR_RE = re.compile(r"\bin (?:19|20)\d{2}\b", re.IGNORECASE)


def compute_requires_date_review(
    claim_text: str,
    claim_type: ClaimType,
    source: SourceContent,
) -> bool:
    """Return True if this claim should be flagged for human date-context review.

    Rules (any one triggers the flag):
      1. Statistical claim with no publication date.
      2. Statistical claim from a source older than STAT_REVIEW_THRESHOLD_DAYS.
      3. Statistical claim from suspected-truncated content.
      4. Claim with time-sensitive wording from a source older than
         TIME_SENSITIVE_REVIEW_DAYS — suppressed when the claim explicitly
         references a historical year.

    Rule 4 suppression applies ONLY to rule 4; rules 1–3 are independent of
    the claim's wording.
    """
    is_statistical = claim_type == ClaimType.statistical

    # Rule 1
    if is_statistical and source.published_at is None:
        return True

    age_days: int | None = None
    if source.published_at is not None:
        try:
            pub = datetime.fromisoformat(source.published_at.split("T")[0]).replace(tzinfo=UTC)
            age_days = (datetime.now(UTC) - pub).days
        except ValueError:
            age_days = None

    # Rule 2
    if is_statistical and age_days is not None and age_days > STAT_REVIEW_THRESHOLD_DAYS:
        return True

    # Rule 3
    if is_statistical and source.suspected_truncation:
        return True

    # Rule 4 — suppressed by explicit historical-year language
    has_time_sensitive = bool(_TIME_SENSITIVE_RE.search(claim_text))
    if has_time_sensitive:
        has_historical_year = bool(_HISTORICAL_YEAR_RE.search(claim_text))
        if not has_historical_year:
            threshold = TIME_SENSITIVE_REVIEW_DAYS
            if age_days is not None and age_days > threshold:
                return True
            if source.published_at is None:
                return True

    return False
