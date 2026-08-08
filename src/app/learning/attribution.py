"""Phase 11 — upstream attribution for optimization recommendations.

Every recommendation identifies the specific upstream subsystem entity
that produced the observed outcome.  Attribution maps domain+subsystem
pairs back to the entity type and ID from the analytics attribution chain.

Consumes only the public AnalyticsHandoff contract.  Never reads from
analytics tables directly — that is the orchestrator's responsibility.
"""

from __future__ import annotations

from app.analytics.models import AnalyticsHandoff
from app.learning.constants import (
    DOMAIN_ANALYTICS,
    DOMAIN_CAPTIONS,
    DOMAIN_MEDIA,
    DOMAIN_NARRATION,
    DOMAIN_PUBLISHING,
    DOMAIN_RESEARCH,
    DOMAIN_SCENES,
    DOMAIN_SCRIPTS,
    DOMAIN_TOPICS,
    ENTITY_CAPTION_RUN,
    ENTITY_NARRATION_RUN,
    ENTITY_PRODUCTION_PLAN,
    ENTITY_PUBLICATION,
    ENTITY_PUBLISHING_PLAN,
    ENTITY_RENDER_MANIFEST,
    ENTITY_SCENE_MANIFEST,
    ENTITY_SCRIPT,
    ENTITY_TOPIC,
)


def resolve_attribution(
    domain: str,
    handoff: AnalyticsHandoff,
) -> tuple[str, str, int | None]:
    """Return (affected_subsystem, subsystem_entity_type, subsystem_entity_id).

    The affected_subsystem is a human-readable label describing which engine
    component should be adjusted.  The entity type and ID allow the reviewer
    to navigate directly to the upstream artifact.

    domain → (affected_subsystem_label, entity_type, entity_id)
    """
    if domain == DOMAIN_TOPICS:
        return (
            "Topic selection and niche targeting",
            ENTITY_TOPIC,
            handoff.topic_id,
        )
    if domain == DOMAIN_RESEARCH:
        return (
            "Research depth and claim extraction",
            ENTITY_SCRIPT,
            handoff.script_id,
        )
    if domain == DOMAIN_SCRIPTS:
        return (
            "Script generation and structure",
            ENTITY_SCRIPT,
            handoff.script_id,
        )
    if domain == DOMAIN_NARRATION:
        return (
            "Narration pacing and synthesis",
            ENTITY_NARRATION_RUN,
            handoff.narration_run_id,
        )
    if domain == DOMAIN_CAPTIONS:
        return (
            "Caption segmentation and density",
            ENTITY_CAPTION_RUN,
            handoff.caption_run_id,
        )
    if domain == DOMAIN_SCENES:
        return (
            "Scene planning and visual selection",
            ENTITY_SCENE_MANIFEST,
            handoff.scene_manifest_id,
        )
    if domain == DOMAIN_MEDIA:
        return (
            "Render settings and thumbnail strategy",
            ENTITY_RENDER_MANIFEST,
            handoff.render_manifest_id,
        )
    if domain == DOMAIN_PUBLISHING:
        return (
            "Publishing metadata and scheduling",
            ENTITY_PUBLISHING_PLAN,
            handoff.publishing_plan_id,
        )
    if domain == DOMAIN_ANALYTICS:
        return (
            "Overall publication performance trends",
            ENTITY_PUBLICATION,
            handoff.publication_id,
        )
    # Fallback — should not be reached if domain is validated first
    return (
        f"Unknown domain: {domain}",
        ENTITY_PRODUCTION_PLAN,
        None,
    )
