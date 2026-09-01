"""Discovery adapter protocol and shared data structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.intelligence.models import AdapterName, Channel, ChannelProfileVersion

VALID_EVIDENCE_TYPES: frozenset[str] = frozenset(
    {
        # YouTube / manual signals (Phase 3–12)
        "view_count",
        "like_count",
        "comment_count",
        "video_count_in_niche",
        "top_video_age_days",
        "incumbent_subscriber_count",
        "manual_demand_note",
        # External market intelligence signals (Phase 13F)
        "market_demand_score",
        "market_saturation_score",
        "market_freshness_score",
        "market_momentum_score",
        "market_persistence_score",
        "market_confidence",
        "market_maturity",
        "market_state_label",
        # Provenance references (Phase 13F)
        "market_canonical_cluster_id",
        "market_cluster_snapshot_id",
        "market_signal_snapshot_id",
        "market_interpretation_run_id",
    }
)


@dataclass
class EvidenceItem:
    evidence_type: str
    evidence_unit: str
    source_label: str
    evidence_value: float | None = None
    evidence_text: str | None = None


@dataclass
class OpportunityCandidate:
    raw_topic: str
    normalized_topic: str
    adapter_name: AdapterName
    raw_payload: dict
    evidence: list[EvidenceItem] = field(default_factory=list)
    signal_age_days: float | None = None
    source_quality_tier: str = "medium"
    collection_notes: str = ""


class DiscoveryAdapter(Protocol):
    def run(
        self,
        topics: list[str],
        channel: Channel,
        profile: ChannelProfileVersion,
    ) -> tuple[list[OpportunityCandidate], int]:
        """Return (candidates, quota_units_consumed)."""
        ...
