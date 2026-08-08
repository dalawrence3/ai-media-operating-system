"""Input snapshot construction and hashing for reproducible scoring."""

from __future__ import annotations

import hashlib
import json

from app.intelligence.models import (
    FactorContext,
)


def build_input_snapshot(ctx: FactorContext) -> dict:
    """
    Build a fully deterministic snapshot of every value that can affect scoring.
    The policy is NOT included; policy_id is compared separately and active
    policies are immutable.
    """
    opp = ctx.opportunity
    profile = ctx.profile

    obs_dict: dict[str, object] = {}
    for obs in sorted(ctx.observations, key=lambda o: o.id or 0):
        evs = ctx.evidence.get(obs.id or 0, [])
        # Sort evidence by composite key for determinism when types share a value
        sorted_evs = sorted(
            evs,
            key=lambda e: (
                e.evidence_type,
                e.evidence_value if e.evidence_value is not None else float("inf"),
                e.evidence_text or "",
                e.id or 0,
            ),
        )
        ev_list = []
        for ev in sorted_evs:
            if ev.evidence_value is not None:
                ev_list.append(
                    {"evidence_type": ev.evidence_type, "evidence_value": ev.evidence_value}
                )
            else:
                ev_list.append(
                    {
                        "evidence_type": ev.evidence_type,
                        "evidence_text": ev.evidence_text,
                    }
                )
        obs_dict[str(obs.id)] = {
            "adapter_name": obs.adapter_name.value,
            "signal_age_days": obs.signal_age_days,
            "source_quality_tier": obs.source_quality_tier.value,
            "evidence": ev_list,
        }

    return {
        "opportunity": {
            "id": opp.id,
            "normalized_topic": opp.normalized_topic,
            "raw_topic": opp.raw_topic,
        },
        "profile": {
            "id": profile.id,
            "version": profile.version,
            "primary_niche": profile.primary_niche,
            "secondary_niches": sorted(profile.secondary_niches),
            "audience_description": profile.audience_description,
            "signal_staleness_days": profile.signal_staleness_days,
            "duplicate_similarity_threshold": profile.duplicate_similarity_threshold,
        },
        "novelty_context": {
            "best_similarity": ctx.best_similarity,
            "matched_opportunity_id": ctx.matched_opportunity_id,
            "matched_normalized_topic": ctx.matched_normalized_topic,
        },
        "observations": obs_dict,
    }


def compute_hash(snapshot: dict) -> str:
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
