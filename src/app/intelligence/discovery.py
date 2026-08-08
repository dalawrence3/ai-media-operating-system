"""Orchestrates a discovery run end-to-end with per-candidate transactions."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

import httpx

from app.intelligence.adapters.base import OpportunityCandidate
from app.intelligence.adapters.manual import ManualSignalAdapter
from app.intelligence.adapters.youtube import YouTubeDataAPIAdapter
from app.intelligence.models import (
    AdapterName,
    DiscoveryRun,
    LifecycleState,
    Opportunity,
    OpportunityObservation,
    OpportunitySourceEvidence,
    RunStatus,
    SourceQualityTier,
)
from app.intelligence.repository import (
    create_discovery_run,
    create_observation,
    create_opportunity,
    create_source_evidence,
    find_existing_opportunity,
    get_active_profile_version,
    get_channel,
    get_discovery_run,
    update_discovery_run,
)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _process_candidate(
    conn: sqlite3.Connection,
    candidate: OpportunityCandidate,
    channel_id: int,
    run_id: int,
    threshold: float,
) -> str:
    """
    Persist one candidate within the caller's savepoint.

    Returns 'new' if a new opportunity was created, 'dedup' if the candidate
    was matched to an existing opportunity.
    """
    now = _now()
    match = find_existing_opportunity(conn, channel_id, candidate.normalized_topic, threshold)

    if match is not None:
        existing_opp, similarity = match
        opportunity_id = existing_opp.id
        was_deduplicated = True
        candidate_topic = candidate.raw_topic
        dedup_score: float | None = similarity
        result = "dedup"
    else:
        opp = create_opportunity(
            conn,
            Opportunity(
                channel_id=channel_id,
                discovery_run_id=run_id,
                normalized_topic=candidate.normalized_topic,
                raw_topic=candidate.raw_topic,
                current_lifecycle_state=LifecycleState.new,
            ),
        )
        opportunity_id = opp.id
        was_deduplicated = False
        candidate_topic = None
        dedup_score = None
        result = "new"

    obs = create_observation(
        conn,
        OpportunityObservation(
            opportunity_id=opportunity_id,
            discovery_run_id=run_id,
            adapter_name=candidate.adapter_name,
            collected_at=datetime.fromisoformat(now),
            signal_age_days=candidate.signal_age_days,
            source_quality_tier=SourceQualityTier(candidate.source_quality_tier),
            raw_payload_json=json.dumps(candidate.raw_payload),
            collection_notes=candidate.collection_notes,
            was_deduplicated=was_deduplicated,
            candidate_topic=candidate_topic,
            dedup_similarity_score=dedup_score,
        ),
    )

    for ev in candidate.evidence:
        create_source_evidence(
            conn,
            OpportunitySourceEvidence(
                observation_id=obs.id,
                opportunity_id=opportunity_id,
                evidence_type=ev.evidence_type,
                evidence_value=ev.evidence_value,
                evidence_text=ev.evidence_text,
                evidence_unit=ev.evidence_unit,
                source_label=ev.source_label,
                collected_at=datetime.fromisoformat(now),
            ),
        )

    return result


def run_discovery(
    conn: sqlite3.Connection,
    channel_id: int,
    adapter_name: AdapterName,
    topics: list[str],
    *,
    operator: str = "system",
    youtube_api_key: str = "",
    youtube_client: httpx.Client | None = None,
) -> DiscoveryRun:
    """
    Run a discovery cycle for one adapter and persist all results.

    Each candidate is processed in its own savepoint so one failure does not
    invalidate the rest. The discovery_run status becomes 'partial' if any
    candidates fail, 'completed' if all succeed.
    """
    channel = get_channel(conn, channel_id)
    if channel is None:
        raise ValueError(f"Channel {channel_id} not found")
    profile = get_active_profile_version(conn, channel_id)
    if profile is None:
        raise ValueError(f"No active profile version for channel {channel_id}")

    run = create_discovery_run(
        conn,
        DiscoveryRun(
            channel_id=channel_id,
            profile_version_id=profile.id,
            adapter_name=adapter_name,
            query_parameters_json=json.dumps({"topics": topics, "operator": operator}),
            status=RunStatus.running,
        ),
    )
    conn.commit()

    if adapter_name == AdapterName.manual:
        adapter = ManualSignalAdapter()
    else:
        adapter = YouTubeDataAPIAdapter(api_key=youtube_api_key, client=youtube_client)

    try:
        candidates, quota_units = adapter.run(topics, channel, profile)
    except Exception as exc:
        update_discovery_run(
            conn,
            run.id,
            status=RunStatus.failed,
            error_message=str(exc),
            completed_at=_now(),
        )
        conn.commit()
        result = get_discovery_run(conn, run.id)
        assert result is not None
        return result

    threshold = profile.duplicate_similarity_threshold
    new_count = dedup_count = failed_count = 0

    for i, candidate in enumerate(candidates):
        sp = f"sp_cand_{i}"
        conn.execute(f"SAVEPOINT {sp}")
        try:
            outcome = _process_candidate(conn, candidate, channel_id, run.id, threshold)
            conn.execute(f"RELEASE SAVEPOINT {sp}")
            if outcome == "new":
                new_count += 1
            else:
                dedup_count += 1
        except Exception:
            conn.execute(f"ROLLBACK TO SAVEPOINT {sp}")
            conn.execute(f"RELEASE SAVEPOINT {sp}")
            failed_count += 1

    final_status = RunStatus.completed if failed_count == 0 else RunStatus.partial
    update_discovery_run(
        conn,
        run.id,
        status=final_status,
        candidate_count=len(candidates),
        new_opportunity_count=new_count,
        dedup_count=dedup_count,
        failed_count=failed_count,
        quota_units_consumed=quota_units,
        completed_at=_now(),
    )
    conn.commit()

    result = get_discovery_run(conn, run.id)
    assert result is not None
    return result
