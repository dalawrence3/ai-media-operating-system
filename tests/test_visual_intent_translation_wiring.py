"""Phase 5, §5-9 — the translation call is actually wired into the real
render entry point (`RenderingExecutor._plan_visuals`), not just built as a
standalone module.

These tests exercise `_plan_visuals` directly (mocking only the heavy
downstream repo/DB/engine machinery it does not itself own) so the wiring
block added between `plan_beats()` and `engine.resolve()` is verified against
the real `intent_translation.translate_visual_intents` /
`apply_visual_intent_translation` functions, not a stand-in.

The property under test throughout: translation is OFF by default, and any
failure (disabled flag, no credentials, a raising provider) must leave the
beats `engine.resolve()` receives byte-for-byte identical to what
`plan_beats()` produced — the render must never fail or silently regress
because of this feature.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.application.stage_executors import RenderingExecutor
from app.visuals.intent_translation import _BeatTranslation, _TranslationOutput
from app.visuals.models import VisualBeat


def _beat(index: int, text: str) -> VisualBeat:
    return VisualBeat(
        beat_index=index,
        scene_index=0,
        scene_id=1,
        segment_id=1,
        start_ms=index * 3000,
        end_ms=(index + 1) * 3000,
        duration_ms=3000,
        narration_text=text,
        search_queries=["fire drill"],
    )


def _topic():
    topic = MagicMock()
    topic.title = "How vaccines train your immune system"
    topic.angle = "Explain immune memory with a concrete drill analogy"
    topic.promoted_opportunity_id = None
    return topic


def _run_plan_visuals(
    monkeypatch, *, env_enabled: bool, claude_provider_factory, api_key="test-key"
):
    if env_enabled:
        monkeypatch.setenv("ACE_VISUAL_INTENT_TRANSLATION_ENABLED", "true")
    else:
        monkeypatch.delenv("ACE_VISUAL_INTENT_TRANSLATION_ENABLED", raising=False)

    beats_in = [_beat(0, "A fire drill for your immune system.")]
    captured: dict[str, object] = {}

    mock_plan = MagicMock()
    mock_plan.resolutions = []

    def _capture_resolve(beats):
        captured["beats"] = beats
        mock_plan.beats = beats
        return mock_plan

    mock_engine_instance = MagicMock()
    mock_engine_instance.resolve.side_effect = _capture_resolve

    mock_composition = MagicMock()
    mock_composition.severe_mismatch_count = 0
    mock_assessment = MagicMock()
    mock_assessment.blocked = False
    mock_assessment.findings = []

    mock_report = MagicMock()
    mock_report.findings = []

    mock_cfg = MagicMock()
    mock_cfg.anthropic_api_key = ""

    approved = MagicMock()
    approved.manifest_id = 1
    approved.topic_id = 4
    approved.experiment_id = None
    approved.caption_run_id = None
    approved.scenes = [
        MagicMock(
            scene_index=0,
            scene_id=1,
            segment_id=1,
            start_ms=0,
            duration_ms=3000,
            narration_text="A fire drill for your immune system.",
            visual_rationale="",
            claim_ids=[],
        )
    ]

    req = MagicMock()
    req.channel_id = "chan-1"
    req.workspace_id = 1
    req.effective_config = {"anthropic_api_key": api_key} if api_key else {}

    executor = RenderingExecutor()

    with (
        patch("app.core.repository.get_topic", return_value=_topic()),
        patch("app.visuals.beats.plan_beats", return_value=beats_in),
        patch("app.visuals.engine.VisualEngine", return_value=mock_engine_instance),
        patch("app.visuals.composition.composition_from_plan", return_value=mock_composition),
        patch("app.visuals.quality.assess_composition", return_value=mock_assessment),
        patch("app.visuals.qa.audit_visual_plan", return_value=mock_report),
        patch("app.visuals.repository.save_visual_plan"),
        patch("app.core.config.get_config", return_value=mock_cfg),
        patch("app.ai.claude.ClaudeProvider", side_effect=claude_provider_factory),
    ):
        executor._plan_visuals(
            MagicMock(),
            req,
            approved=approved,
            scene_inputs=[],
            artifacts_path=__import__("pathlib").Path("/tmp/does-not-matter"),
            width=1080,
            height=1920,
        )

    return captured["beats"]


class TestTranslationWiringDisabledByDefault:
    def test_translation_off_leaves_beats_untouched(self, monkeypatch):
        provider_factory = MagicMock()
        beats = _run_plan_visuals(
            monkeypatch, env_enabled=False, claude_provider_factory=provider_factory
        )
        assert beats[0].literal_visual_subject == ""
        assert beats[0].search_queries == ["fire drill"]
        provider_factory.assert_not_called()


class TestTranslationWiringEnabled:
    def test_translation_on_grounds_beats_in_topic_literal_meaning(self, monkeypatch):
        translated = _TranslationOutput(
            topic_context="vaccines and immune response",
            beats=[
                _BeatTranslation(
                    beat_index=0,
                    literal_visual_subject="immune cells rehearsing an antigen response",
                    process_or_action="cellular response rehearsal",
                    concrete_entities=["immune cell", "antigen"],
                    preferred_visual_types=["video"],
                    avoid_literal_interpretations=["firefighters", "fire drill"],
                )
            ],
        )

        def _factory(*args, **kwargs):
            provider = MagicMock()
            response = MagicMock()
            response.parsed = translated
            provider.complete.return_value = response
            return provider

        beats = _run_plan_visuals(monkeypatch, env_enabled=True, claude_provider_factory=_factory)

        beat = beats[0]
        assert beat.literal_visual_subject == "immune cells rehearsing an antigen response"
        assert beat.topic_context == "vaccines and immune response"
        assert "firefighters" in beat.avoid_terms
        # The query is now grounded in the literal subject, not the raw
        # figurative narration text.
        assert beat.search_queries != ["fire drill"]
        assert any("immune" in q for q in beat.search_queries)

    def test_missing_api_key_fails_open(self, monkeypatch):
        provider_factory = MagicMock()
        beats = _run_plan_visuals(
            monkeypatch, env_enabled=True, claude_provider_factory=provider_factory, api_key=""
        )
        assert beats[0].literal_visual_subject == ""
        assert beats[0].search_queries == ["fire drill"]
        provider_factory.assert_not_called()

    def test_provider_exception_fails_open(self, monkeypatch):
        def _factory(*args, **kwargs):
            raise RuntimeError("provider construction failed")

        beats = _run_plan_visuals(monkeypatch, env_enabled=True, claude_provider_factory=_factory)
        assert beats[0].literal_visual_subject == ""
        assert beats[0].search_queries == ["fire drill"]

    def test_provider_call_exception_fails_open(self, monkeypatch):
        def _factory(*args, **kwargs):
            provider = MagicMock()
            provider.complete.side_effect = RuntimeError("network error")
            return provider

        beats = _run_plan_visuals(monkeypatch, env_enabled=True, claude_provider_factory=_factory)
        assert beats[0].literal_visual_subject == ""
        assert beats[0].search_queries == ["fire drill"]
