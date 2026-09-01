"""Phase 13F bridge policy and result models.

ExternalMarketBridgePolicy controls how global market signals are mapped
into the existing channel-specific Opportunity scoring system.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# Maturity levels in ascending order of confidence
MATURITY_LEVELS: tuple[str, ...] = (
    "insufficient",
    "exploratory",
    "directional",
    "actionable",
)


@dataclass(frozen=True)
class ExternalMarketBridgePolicy:
    """Versioned policy controlling the Phase 13F signal → factor bridge.

    All fields are immutable after construction so the policy can be safely
    stored alongside the scored opportunity for later audit.
    """

    version: str = "1.0.0"

    # Maturity gate: clusters below this level are skipped entirely
    min_maturity_level: str = "directional"

    # Confidence below which evidence is emitted as `status=insufficient`
    # rather than `status=present`, so the scoring engine's missing-data policy
    # handles it conservatively.
    min_confidence_for_present: float = 0.30

    # Signal staleness: interpretation runs older than this many days are skipped
    max_signal_age_days: int = 90

    # Blend ratios (must each sum to 1.0 within their group)
    trend_momentum_weight: float = 0.60  # weight for market_momentum in trend_strength
    trend_freshness_weight: float = 0.40  # weight for market_freshness in trend_strength
    evergreen_persistence_weight: float = 0.60  # weight for persistence in evergreen blend
    evergreen_lexical_weight: float = 0.40  # weight for lexical heuristic in evergreen blend

    # Deduplication: topic jaccard threshold for legacy-path dedup
    topic_jaccard_threshold: float = 0.50

    def maturity_rank(self, level: str) -> int:
        try:
            return MATURITY_LEVELS.index(level)
        except ValueError:
            return -1

    def meets_maturity_gate(self, signal_maturity: str) -> bool:
        return self.maturity_rank(signal_maturity) >= self.maturity_rank(self.min_maturity_level)

    def evidence_status(self, confidence: float) -> Literal["present", "insufficient"]:
        return "present" if confidence >= self.min_confidence_for_present else "insufficient"


@dataclass
class MarketBridgeSyncItem:
    """Result for a single evidence item processed by the bridge."""

    cluster_label: str
    canonical_cluster_id: int | None
    opportunity_id: int
    opportunity_created: bool  # True = new row; False = existing refreshed
    observation_id: int
    score_id: int | None  # None on dry_run
    composite_score: float | None
    confidence: float | None
    skipped: bool = False
    skip_reason: str | None = None


@dataclass
class MarketBridgeResult:
    """Aggregate result from one sync_channel_market_opportunities() call."""

    channel_id: int
    discovery_run_id: int | None
    bridge_policy_version: str
    dry_run: bool
    items: list[MarketBridgeSyncItem] = field(default_factory=list)

    @property
    def created_count(self) -> int:
        return sum(1 for i in self.items if i.opportunity_created and not i.skipped)

    @property
    def refreshed_count(self) -> int:
        return sum(1 for i in self.items if not i.opportunity_created and not i.skipped)

    @property
    def skipped_count(self) -> int:
        return sum(1 for i in self.items if i.skipped)

    @property
    def scored_count(self) -> int:
        return sum(1 for i in self.items if i.score_id is not None)
