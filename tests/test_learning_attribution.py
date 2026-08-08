"""Tests for Phase 11 attribution module."""

from __future__ import annotations

from app.learning.attribution import resolve_attribution
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
    ENTITY_PUBLICATION,
    ENTITY_PUBLISHING_PLAN,
    ENTITY_RENDER_MANIFEST,
    ENTITY_SCENE_MANIFEST,
    ENTITY_SCRIPT,
    ENTITY_TOPIC,
)


def _make_handoff():
    """Return a minimal AnalyticsHandoff-like object for attribution tests."""
    from unittest.mock import MagicMock

    h = MagicMock()
    h.topic_id = 1
    h.script_id = 2
    h.narration_run_id = 3
    h.caption_run_id = 4
    h.scene_manifest_id = 5
    h.render_manifest_id = 6
    h.publishing_plan_id = 7
    h.publishing_job_id = 8
    h.publication_id = 9
    h.production_plan_id = 10
    return h


class TestResolveAttribution:
    def test_topics_domain(self):
        h = _make_handoff()
        subsystem, etype, eid = resolve_attribution(DOMAIN_TOPICS, h)
        assert etype == ENTITY_TOPIC
        assert eid == h.topic_id
        assert isinstance(subsystem, str) and subsystem

    def test_research_domain(self):
        h = _make_handoff()
        subsystem, etype, eid = resolve_attribution(DOMAIN_RESEARCH, h)
        assert etype == ENTITY_SCRIPT
        assert eid == h.script_id

    def test_scripts_domain(self):
        h = _make_handoff()
        _, etype, eid = resolve_attribution(DOMAIN_SCRIPTS, h)
        assert etype == ENTITY_SCRIPT
        assert eid == h.script_id

    def test_narration_domain(self):
        h = _make_handoff()
        _, etype, eid = resolve_attribution(DOMAIN_NARRATION, h)
        assert etype == ENTITY_NARRATION_RUN
        assert eid == h.narration_run_id

    def test_captions_domain(self):
        h = _make_handoff()
        _, etype, eid = resolve_attribution(DOMAIN_CAPTIONS, h)
        assert etype == ENTITY_CAPTION_RUN
        assert eid == h.caption_run_id

    def test_scenes_domain(self):
        h = _make_handoff()
        _, etype, eid = resolve_attribution(DOMAIN_SCENES, h)
        assert etype == ENTITY_SCENE_MANIFEST
        assert eid == h.scene_manifest_id

    def test_media_domain(self):
        h = _make_handoff()
        _, etype, eid = resolve_attribution(DOMAIN_MEDIA, h)
        assert etype == ENTITY_RENDER_MANIFEST
        assert eid == h.render_manifest_id

    def test_publishing_domain(self):
        h = _make_handoff()
        _, etype, eid = resolve_attribution(DOMAIN_PUBLISHING, h)
        assert etype == ENTITY_PUBLISHING_PLAN
        assert eid == h.publishing_plan_id

    def test_analytics_domain(self):
        h = _make_handoff()
        _, etype, eid = resolve_attribution(DOMAIN_ANALYTICS, h)
        assert etype == ENTITY_PUBLICATION
        assert eid == h.publication_id

    def test_unknown_domain_returns_fallback(self):
        h = _make_handoff()
        subsystem, etype, _ = resolve_attribution("unknown_domain", h)
        assert "Unknown" in subsystem

    def test_all_domains_return_nonempty_subsystem(self):
        h = _make_handoff()
        from app.learning.constants import RECOMMENDATION_DOMAINS

        for domain in RECOMMENDATION_DOMAINS:
            subsystem, etype, _ = resolve_attribution(domain, h)
            assert subsystem
            assert etype
