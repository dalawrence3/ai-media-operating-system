"""Deterministic source-quality scoring.

All inputs come from a SourceContent row. No network or LLM calls.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.research.constants import QUALITY_SCORER_VERSION
from app.research.models import DomainType, ExtractionStatus, SourceContent

_RECENCY_FRESH_DAYS = 30
_RECENCY_OLD_DAYS = 730  # 2 years
_WORD_COUNT_MIN = 200
_TRUNCATION_WORD_COUNT = 300

# Weights must sum to 1.0
_WEIGHTS: dict[str, float] = {
    "recency": 0.20,
    "primary_source": 0.20,
    "has_author": 0.10,
    "word_count_score": 0.20,
    "extraction_success": 0.15,
    "publication_identity": 0.10,
    "not_truncated": 0.05,
}
assert abs(sum(_WEIGHTS.values()) - 1.0) < 1e-9, "Quality weights must sum to 1.0"


@dataclass
class QualityResult:
    score: float
    factors: dict[str, float]
    scorer_version: str = field(default=QUALITY_SCORER_VERSION)

    @property
    def factors_json(self) -> str:
        return json.dumps(self.factors)


def score_quality(sc: SourceContent) -> QualityResult:
    """Compute a composite quality score for *sc*.

    Returns a :class:`QualityResult` with per-factor values and the composite.
    Factors are weighted sums over [0, 1] sub-scores.
    """
    factors: dict[str, float] = {}

    # Recency
    if sc.published_at:
        try:
            pub = datetime.fromisoformat(sc.published_at.split("T")[0])
            pub = pub.replace(tzinfo=UTC)
        except ValueError:
            factors["recency"] = 0.5  # unparseable date → neutral
        else:
            now = datetime.now(UTC)
            age_days = (now - pub).days
            if age_days <= _RECENCY_FRESH_DAYS:
                factors["recency"] = 1.0
            elif age_days >= _RECENCY_OLD_DAYS:
                factors["recency"] = 0.1
            else:
                # Linear decay between fresh and old thresholds
                span = _RECENCY_OLD_DAYS - _RECENCY_FRESH_DAYS
                factors["recency"] = max(
                    0.1,
                    1.0 - 0.9 * (age_days - _RECENCY_FRESH_DAYS) / span,
                )
    else:
        factors["recency"] = 0.3  # unknown date → penalised

    # Primary source (government or academic)
    factors["primary_source"] = (
        1.0 if sc.domain_type in (DomainType.government, DomainType.academic) else 0.0
    )

    # Author attribution
    factors["has_author"] = 1.0 if sc.author else 0.0

    # Word count
    wc = sc.word_count or 0
    if wc >= _WORD_COUNT_MIN:
        factors["word_count_score"] = 1.0
    elif wc == 0:
        factors["word_count_score"] = 0.0
    else:
        factors["word_count_score"] = wc / _WORD_COUNT_MIN

    # Extraction success
    if sc.extraction_status == ExtractionStatus.ok:
        factors["extraction_success"] = 1.0
    elif sc.extraction_status == ExtractionStatus.partial:
        factors["extraction_success"] = 0.5
    else:
        factors["extraction_success"] = 0.0

    # Publication identity (any known domain type)
    factors["publication_identity"] = 0.0 if sc.domain_type == DomainType.unknown else 1.0

    # Not truncated
    factors["not_truncated"] = 0.0 if sc.suspected_truncation else 1.0

    composite = sum(_WEIGHTS[k] * v for k, v in factors.items())
    composite = round(min(1.0, max(0.0, composite)), 4)

    return QualityResult(score=composite, factors=factors)
