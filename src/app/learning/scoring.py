"""Phase 11 — confidence scoring for optimization recommendations.

Confidence is derived from three deterministic factors:
  1. Evidence volume   — how many snapshots contribute
  2. Effect magnitude  — how far observed values deviate from the threshold
  3. Consistency       — whether the signal is present across multiple periods

No ML, no randomness.  Same inputs → same score.
"""

from __future__ import annotations

import math

from app.learning.constants import (
    MIN_SNAPSHOTS_HIGH_CONFIDENCE,
    MIN_SNAPSHOTS_MEDIUM_CONFIDENCE,
    confidence_label,
)
from app.learning.models import EvidenceItem


def _volume_score(snapshot_count: int) -> float:
    """Score 0–1 based on number of contributing snapshots.

    Logarithmic scale so that diminishing returns apply beyond a few snapshots.
    """
    if snapshot_count <= 0:
        return 0.0
    # log2 scale: 1→0.25, 2→0.50, 4→0.75, 8→1.0 (clamped)
    raw = math.log2(snapshot_count + 1) / math.log2(MIN_SNAPSHOTS_HIGH_CONFIDENCE + 1)
    return min(1.0, raw)


def _effect_score(observed: float, threshold: float, direction: str) -> float:
    """Score 0–1 based on how far the observed value is from the threshold.

    direction = 'below' → signal fires when observed < threshold (bad metric)
    direction = 'above' → signal fires when observed > threshold (positive signal)
    """
    if threshold == 0:
        return 0.5
    if direction == "below":
        # How far below the threshold? Deeper = stronger signal.
        gap = max(0.0, threshold - observed)
        return min(1.0, gap / threshold)
    else:  # above
        gap = max(0.0, observed - threshold)
        return min(1.0, gap / threshold)


def _consistency_score(evidence: list[EvidenceItem]) -> float:
    """Score 0–1 based on signal consistency across independent periods.

    A single period cannot demonstrate consistency — it returns 0.0, not a
    neutral 0.5.  This prevents a solo data point from receiving an unearned
    consistency bonus and keeps single-observation confidence in the exploratory
    range.

    Deduplication rules:
    - Repeated snapshots of the same period_key count as one independent period.
    - Multiple metrics from one snapshot do not inflate consistency.
    - Provisional/duplicate evidence must be deduplicated before calling.
    """
    if not evidence:
        return 0.0
    if len(evidence) == 1:
        # One period: consistency is unknown, not neutral.
        return 0.0
    # Count unique period keys; duplicate period_keys count once.
    unique_periods = len({e.period_key for e in evidence})
    return min(1.0, 0.3 + 0.7 * (unique_periods / len(evidence)))


def compute_confidence(
    evidence: list[EvidenceItem],
    observed_value: float,
    threshold: float,
    direction: str = "below",
) -> tuple[float, str]:
    """Return (confidence_score, confidence_label) from evidence and observed value.

    All three sub-scores are averaged with equal weight.

    Zero evidence → score 0.0 (LOW) immediately.
    Snapshot IDs are deduplicated across all evidence items before computing
    volume; duplicated IDs cannot inflate confidence.

    Note: the returned score is a heuristic signal strength, NOT a statistical
    confidence interval.  Thresholds (0.4 for medium, 0.7 for high) are engineering
    judgement calls versioned with LEARNING_ALGORITHM_VERSION, not p-values.
    """
    if not evidence:
        return 0.0, confidence_label(0.0)

    # Deduplicate snapshot IDs to prevent volume inflation from duplicate evidence.
    unique_snapshot_ids = {sid for ev in evidence for sid in ev.snapshot_ids}
    snapshot_count = len(unique_snapshot_ids)

    vol = _volume_score(snapshot_count)
    eff = _effect_score(observed_value, threshold, direction)
    con = _consistency_score(evidence)

    score = round((vol + eff + con) / 3.0, 4)
    label = confidence_label(score)
    return score, label


def compute_confidence_from_evidence(
    evidence: list[EvidenceItem],
) -> tuple[float, str]:
    """Return (confidence_score, confidence_label) when threshold comparison is N/A.

    Used for positive-reinforcement recommendations where the direction is
    'above threshold' and we primarily care about evidence volume.

    Snapshot IDs are deduplicated before computing volume.
    """
    if not evidence:
        return 0.0, confidence_label(0.0)

    unique_snapshot_ids = {sid for ev in evidence for sid in ev.snapshot_ids}
    snapshot_count = len(unique_snapshot_ids)
    con = _consistency_score(evidence)
    vol = _volume_score(snapshot_count)
    score = round((vol + con) / 2.0, 4)
    label = confidence_label(score)
    return score, label


def meets_minimum_confidence(
    snapshot_count: int, required: str
) -> bool:
    """Return True if the snapshot count meets the minimum for the required confidence level."""
    if required == "high":
        return snapshot_count >= MIN_SNAPSHOTS_HIGH_CONFIDENCE
    if required == "medium":
        return snapshot_count >= MIN_SNAPSHOTS_MEDIUM_CONFIDENCE
    return True  # low confidence has no minimum
