"""Phase 7B, §16/§17/§19 — ExplorationCell.format survives real script
generation, and the pre-existing explainer path is unaffected when no cell
is linked.

Exercises the REAL `ScriptGenerationExecutor.execute()` end to end (real
`generate_script()`, real schema/validator, real `scripts` row), with only
the Anthropic provider faked — the same pattern
test_autonomy_production_cycle_18b.py already established.
"""

from __future__ import annotations

import json

from app.ai.provider import AIRequest, AIResponse
from app.application.executor import StageExecutionRequest
from app.application.stage_executors import ScriptGenerationExecutor
from app.core.models import Topic
from app.core.repository import create_topic
from app.intelligence.exploration.cell import ExplorationCell
from app.intelligence.exploration.repository import record_cell_selection


class _FakeClaudeProvider:
    """Drop-in replacement for app.ai.claude.ClaudeProvider — returns a
    fixed payload regardless of prompt, so ScriptGenerationExecutor's real
    code path (prompt selection, generate_script, format validation,
    persistence) runs unmodified."""

    name = "claude"

    def __init__(self, api_key: str = "", model: str = ""):
        self.api_key = api_key
        self.model = model
        self.requests: list[AIRequest] = []

    def __call__(self, payload: dict):
        self._payload = payload
        return self

    def complete(self, request: AIRequest) -> AIResponse:
        self.requests.append(request)
        return AIResponse(
            raw_text=json.dumps(self._payload),
            provider_name="claude",
            model=request.model or self.model or "claude-fake-test-model",
            input_tokens=10,
            output_tokens=10,
            duration_ms=1,
            retry_count=0,
            parsed=None,
        )


_EXPLAINER_PAYLOAD = {
    "title": "Test Script Title",
    "sections": [
        {"section_type": "hook", "text": "A short, punchy hook.", "cited_claim_ids": []},
        {"section_type": "body", "text": "The main explanation body.", "cited_claim_ids": []},
        {"section_type": "cta", "text": "Follow for more!", "cited_claim_ids": []},
    ],
}

_QUIZ_PAYLOAD = {
    "title": "Spanish Vocab Quiz",
    "sections": [
        {"section_type": "hook", "text": "Test your Spanish!", "cited_claim_ids": []},
        {
            "section_type": "prompt",
            "text": "How do you say 'window' in Spanish?",
            "cited_claim_ids": [],
            "think_duration_ms": 3000,
        },
        {"section_type": "reveal", "text": "La ventana.", "cited_claim_ids": []},
        {"section_type": "next_challenge", "text": "One more!", "cited_claim_ids": []},
        {
            "section_type": "prompt",
            "text": "How do you say 'thank you' in Spanish?",
            "cited_claim_ids": [],
            "think_duration_ms": 2500,
        },
        {"section_type": "reveal", "text": "Gracias.", "cited_claim_ids": []},
        {"section_type": "cta", "text": "Follow for more!", "cited_claim_ids": []},
    ],
}

_MALFORMED_QUIZ_PAYLOAD = {
    "title": "Broken Quiz",
    "sections": [
        {"section_type": "hook", "text": "Test your Spanish!", "cited_claim_ids": []},
        {
            "section_type": "prompt",
            "text": "How do you say 'window' in Spanish?",
            "cited_claim_ids": [],
            "think_duration_ms": 0,  # missing the required pause
        },
        {"section_type": "reveal", "text": "La ventana.", "cited_claim_ids": []},
        {"section_type": "cta", "text": "Follow for more!", "cited_claim_ids": []},
    ],
}


def _install_fake_claude(monkeypatch, payload: dict):
    import app.ai.claude as claude_mod

    def _factory(api_key: str = "", model: str = ""):
        provider = _FakeClaudeProvider(api_key=api_key, model=model)
        provider._payload = payload
        return provider

    monkeypatch.setattr(claude_mod, "ClaudeProvider", _factory)


def _seed_topic_with_cell(db, *, format_name: str, opportunity_id: int = 100) -> int:
    db.execute("PRAGMA foreign_keys = OFF")
    db.execute(
        """INSERT INTO opportunities
               (id, channel_id, discovery_run_id, normalized_topic, raw_topic, title,
                current_lifecycle_state, created_at, updated_at)
           VALUES (?, 1, 0, 'x', 'x', 'x', 'new', datetime('now'), datetime('now'))""",
        (opportunity_id,),
    )
    topic = create_topic(
        db, Topic(title="How to order coffee in Spanish", angle="practical phrases")
    )
    db.execute(
        "UPDATE topics SET promoted_opportunity_id = ? WHERE id = ?",
        (opportunity_id, topic.id),
    )
    record_cell_selection(
        db,
        channel_id=1,
        workspace_id="ws",
        cell=ExplorationCell(
            domain="language_learning", format=format_name, audience_value="practical_utility"
        ),
        selected_at="2026-01-01T00:00:00",
        opportunity_id=opportunity_id,
    )
    db.commit()
    return topic.id


def _req(topic_id: int) -> StageExecutionRequest:
    return StageExecutionRequest(
        pipeline_execution_id="pipe-1",
        workspace_id="ws",
        stage="script_generation",
        attempt_number=1,
        correlation_id="corr-1",
        idempotency_key="idem-1",
        actor="test",
        topic_id=topic_id,
        effective_config={"anthropic_api_key": "fake-test-key"},
    )


class TestFormatSurvivesThroughScriptGeneration:
    def test_quiz_cell_produces_quiz_formatted_script(self, db, monkeypatch):
        _install_fake_claude(monkeypatch, _QUIZ_PAYLOAD)
        topic_id = _seed_topic_with_cell(db, format_name="interactive_recall_quiz")

        result = ScriptGenerationExecutor().execute(db, _req(topic_id))

        assert result.status == "waiting_for_review"
        script_id = int(result.artifact_id)
        row = db.execute(
            "SELECT exploration_format FROM scripts WHERE id = ?", (script_id,)
        ).fetchone()
        assert row["exploration_format"] == "interactive_recall_quiz"

    def test_micro_lesson_cell_produces_lesson_formatted_script(self, db, monkeypatch):
        lesson_payload = {
            "title": "Compound Interest",
            "sections": [
                {"section_type": "hook", "text": "This changes everything.", "cited_claim_ids": []},
                {"section_type": "problem", "text": "Why start early?", "cited_claim_ids": []},
                {
                    "section_type": "explanation",
                    "text": "Interest compounds on itself.",
                    "cited_claim_ids": [],
                },
                {
                    "section_type": "example",
                    "text": "$1,000 at 7% becomes ~$2,000 in 10 years.",
                    "cited_claim_ids": [],
                },
                {"section_type": "takeaway", "text": "Start now.", "cited_claim_ids": []},
                {"section_type": "cta", "text": "Follow for more!", "cited_claim_ids": []},
            ],
        }
        _install_fake_claude(monkeypatch, lesson_payload)
        topic_id = _seed_topic_with_cell(
            db, format_name="practical_micro_lesson", opportunity_id=101
        )

        result = ScriptGenerationExecutor().execute(db, _req(topic_id))

        assert result.status == "waiting_for_review"
        script_id = int(result.artifact_id)
        row = db.execute(
            "SELECT exploration_format FROM scripts WHERE id = ?", (script_id,)
        ).fetchone()
        assert row["exploration_format"] == "practical_micro_lesson"


class TestBackwardCompatibilityUnlinkedTopic:
    def test_topic_with_no_exploration_cell_defaults_to_explainer(self, db, monkeypatch):
        """§17 — old/ordinary topics (no promoted_opportunity_id link to
        any exploration cell) must behave exactly as before Phase 7B."""
        _install_fake_claude(monkeypatch, _EXPLAINER_PAYLOAD)
        topic = create_topic(db, Topic(title="A plain topic", angle="no cell here"))
        db.commit()

        result = ScriptGenerationExecutor().execute(db, _req(topic.id))

        assert result.status == "waiting_for_review"
        script_id = int(result.artifact_id)
        row = db.execute(
            "SELECT exploration_format FROM scripts WHERE id = ?", (script_id,)
        ).fetchone()
        assert row["exploration_format"] == "curiosity_explainer"


class TestUnsupportedFormatNeverLiesAboutLineage:
    def test_cell_with_no_production_contract_persists_the_truthful_format(self, db, monkeypatch):
        """Phase 7C, §1 — a cell selected with a format that has no
        production contract (e.g. 'myth_bust', a valid exploration concept
        Phase 7A seeded but Phase 7B never built a prompt/contract for)
        must not leave scripts.exploration_format claiming 'myth_bust'
        when the script was actually generated via the default explainer
        prompt — that would be a misleading publication lineage. The
        persisted value must match what was ACTUALLY produced."""
        _install_fake_claude(monkeypatch, _EXPLAINER_PAYLOAD)
        topic_id = _seed_topic_with_cell(db, format_name="myth_bust", opportunity_id=103)

        result = ScriptGenerationExecutor().execute(db, _req(topic_id))

        assert result.status == "waiting_for_review"
        script_id = int(result.artifact_id)
        row = db.execute(
            "SELECT exploration_format FROM scripts WHERE id = ?", (script_id,)
        ).fetchone()
        # Truthful: the explainer prompt is what actually ran, so this must
        # say 'curiosity_explainer', never the requested-but-unsupported
        # 'myth_bust'.
        assert row["exploration_format"] == "curiosity_explainer"


class TestFormatValidationBlocksMalformedGeneration:
    def test_malformed_quiz_response_fails_the_stage(self, db, monkeypatch):
        """§19 — production validation catches a malformed quiz item
        (a 'prompt' section with no think_duration_ms) rather than
        silently persisting a broken quiz."""
        _install_fake_claude(monkeypatch, _MALFORMED_QUIZ_PAYLOAD)
        topic_id = _seed_topic_with_cell(
            db, format_name="interactive_recall_quiz", opportunity_id=102
        )

        result = ScriptGenerationExecutor().execute(db, _req(topic_id))

        assert result.status == "failed"
