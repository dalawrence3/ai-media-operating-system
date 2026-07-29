"""ManualSignalAdapter — wraps operator-supplied topic strings."""

from __future__ import annotations

from app.intelligence.adapters.base import EvidenceItem, OpportunityCandidate
from app.intelligence.dedup import normalize_topic
from app.intelligence.models import AdapterName, Channel, ChannelProfileVersion


class ManualSignalAdapter:
    """Converts --topic strings into OpportunityCandidate objects. No external calls."""

    def run(
        self,
        topics: list[str],
        channel: Channel,
        profile: ChannelProfileVersion,
    ) -> tuple[list[OpportunityCandidate], int]:
        candidates: list[OpportunityCandidate] = []
        for raw in topics:
            raw = raw.strip()
            if not raw:
                continue
            normalized = normalize_topic(raw)
            if not normalized:
                continue
            candidates.append(
                OpportunityCandidate(
                    raw_topic=raw,
                    normalized_topic=normalized,
                    adapter_name=AdapterName.manual,
                    raw_payload={"topic": raw},
                    evidence=[
                        EvidenceItem(
                            evidence_type="manual_demand_note",
                            evidence_value=None,
                            evidence_text=raw,
                            evidence_unit="text",
                            source_label="manual:operator",
                        )
                    ],
                    signal_age_days=None,
                    source_quality_tier="variable",
                )
            )
        return candidates, 0
