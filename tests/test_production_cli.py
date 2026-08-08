"""Tests for Phase 6 M6.1 production plan CLI commands."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from app.cli import app
from app.core.config import reset_config
from app.core.database import open_db
from app.core.models import Script, ScriptStatus, Topic
from app.core.repository import create_script, create_topic
from app.production.constants import (
    PRODUCTION_DURATION_VERSION,
    PRODUCTION_PLAN_RENDERER_VERSION,
    PRODUCTION_PLAN_SCHEMA_VERSION,
)
from app.production.models import ProductionPlanDraft, ProductionSegmentDraft
from app.production.repository import create_production_plan

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACE_DB_PATH", str(tmp_path / "test.db"))
    reset_config()
    yield
    reset_config()


def _db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


def _seed_topic_and_script(tmp_path: Path) -> tuple[int, int]:
    db = open_db(_db_path(tmp_path))
    topic = create_topic(db, Topic(title="Test Topic"))
    script = create_script(
        db, Script(topic_id=topic.id, version=1, body="Body.", status=ScriptStatus.draft)
    )
    db.commit()
    db.close()
    return topic.id, script.id


def _seed_approved_plan(tmp_path: Path, topic_id: int, script_id: int) -> int:
    db = open_db(_db_path(tmp_path))
    draft = ProductionPlanDraft(
        topic_id=topic_id,
        script_id=script_id,
        script_version=1,
        input_hash="a" * 64,
        script_body_hash="b" * 64,
        plan_schema_version=PRODUCTION_PLAN_SCHEMA_VERSION,
        renderer_version=PRODUCTION_PLAN_RENDERER_VERSION,
        duration_algorithm_version=PRODUCTION_DURATION_VERSION,
        title="Test Script",
        format="short",
        total_estimated_duration_s=6,
        total_word_count=4,
        warnings=[],
        requires_evidence_review=False,
        evidence_hash="e" * 64,
        generation_run_id=None,
        experiment_id=None,
        segments=[
            ProductionSegmentDraft(
                segment_index=0,
                section_index=0,
                section_type="hook",
                narration_text="Hook text.",
                estimated_duration_s=4,
                estimated_word_count=2,
            ),
            ProductionSegmentDraft(
                segment_index=1,
                section_index=1,
                section_type="cta",
                narration_text="CTA text.",
                estimated_duration_s=2,
                estimated_word_count=2,
            ),
        ],
    )
    plan = create_production_plan(db, draft)
    db.commit()
    plan_id = plan.id
    db.close()
    return plan_id


def _seed_draft_plan(tmp_path: Path, topic_id: int, script_id: int) -> int:
    return _seed_approved_plan(tmp_path, topic_id, script_id)


# ---------------------------------------------------------------------------
# production list
# ---------------------------------------------------------------------------


def test_production_list_no_plans(tmp_path: Path) -> None:
    topic_id, _ = _seed_topic_and_script(tmp_path)
    result = runner.invoke(app, ["production", "list", str(topic_id)])
    assert result.exit_code == 0
    assert "No production plans found" in result.output


def test_production_list_shows_plans(tmp_path: Path) -> None:
    topic_id, script_id = _seed_topic_and_script(tmp_path)
    _seed_draft_plan(tmp_path, topic_id, script_id)
    result = runner.invoke(app, ["production", "list", str(topic_id)])
    assert result.exit_code == 0
    assert "status=draft" in result.output


# ---------------------------------------------------------------------------
# production show
# ---------------------------------------------------------------------------


def test_production_show_existing_plan(tmp_path: Path) -> None:
    topic_id, script_id = _seed_topic_and_script(tmp_path)
    plan_id = _seed_draft_plan(tmp_path, topic_id, script_id)
    result = runner.invoke(app, ["production", "show", str(plan_id)])
    assert result.exit_code == 0
    assert f"Plan id={plan_id}" in result.output
    assert "hook" in result.output
    assert "cta" in result.output


def test_production_show_missing_plan(tmp_path: Path) -> None:
    result = runner.invoke(app, ["production", "show", "99999"])
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# production approve
# ---------------------------------------------------------------------------


def test_production_approve_draft_plan(tmp_path: Path) -> None:
    topic_id, script_id = _seed_topic_and_script(tmp_path)
    plan_id = _seed_draft_plan(tmp_path, topic_id, script_id)
    result = runner.invoke(app, ["production", "approve", str(plan_id), "--actor", "dom"])
    assert result.exit_code == 0
    assert "Approved plan" in result.output
    assert f"id={plan_id}" in result.output


def test_production_approve_already_approved_fails(tmp_path: Path) -> None:
    topic_id, script_id = _seed_topic_and_script(tmp_path)
    plan_id = _seed_draft_plan(tmp_path, topic_id, script_id)
    runner.invoke(app, ["production", "approve", str(plan_id)])
    result = runner.invoke(app, ["production", "approve", str(plan_id)])
    assert result.exit_code == 1


def test_production_approve_missing_plan_fails(tmp_path: Path) -> None:
    result = runner.invoke(app, ["production", "approve", "99999"])
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# production reject
# ---------------------------------------------------------------------------


def test_production_reject_draft_plan(tmp_path: Path) -> None:
    topic_id, script_id = _seed_topic_and_script(tmp_path)
    plan_id = _seed_draft_plan(tmp_path, topic_id, script_id)
    result = runner.invoke(app, ["production", "reject", str(plan_id), "pacing", "--actor", "dom"])
    assert result.exit_code == 0
    assert "Rejected plan" in result.output


def test_production_reject_invalid_reason_fails(tmp_path: Path) -> None:
    topic_id, script_id = _seed_topic_and_script(tmp_path)
    plan_id = _seed_draft_plan(tmp_path, topic_id, script_id)
    result = runner.invoke(app, ["production", "reject", str(plan_id), "not_real"])
    assert result.exit_code == 1


def test_production_reject_other_without_notes_fails(tmp_path: Path) -> None:
    topic_id, script_id = _seed_topic_and_script(tmp_path)
    plan_id = _seed_draft_plan(tmp_path, topic_id, script_id)
    result = runner.invoke(app, ["production", "reject", str(plan_id), "other"])
    assert result.exit_code == 1


def test_production_reject_other_with_notes_succeeds(tmp_path: Path) -> None:
    topic_id, script_id = _seed_topic_and_script(tmp_path)
    plan_id = _seed_draft_plan(tmp_path, topic_id, script_id)
    result = runner.invoke(
        app, ["production", "reject", str(plan_id), "other", "--notes", "Bad pacing."]
    )
    assert result.exit_code == 0


def test_production_reject_missing_plan_fails(tmp_path: Path) -> None:
    result = runner.invoke(app, ["production", "reject", "99999", "pacing"])
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# production feedback
# ---------------------------------------------------------------------------


def test_production_feedback_no_events(tmp_path: Path) -> None:
    topic_id, script_id = _seed_topic_and_script(tmp_path)
    plan_id = _seed_draft_plan(tmp_path, topic_id, script_id)
    result = runner.invoke(app, ["production", "feedback", str(plan_id)])
    assert result.exit_code == 0
    assert "No review events" in result.output


def test_production_feedback_shows_events(tmp_path: Path) -> None:
    topic_id, script_id = _seed_topic_and_script(tmp_path)
    plan_id = _seed_draft_plan(tmp_path, topic_id, script_id)
    runner.invoke(app, ["production", "approve", str(plan_id), "--actor", "dom"])
    result = runner.invoke(app, ["production", "feedback", str(plan_id)])
    assert result.exit_code == 0
    assert "approved" in result.output
    assert "dom" in result.output


def test_production_feedback_missing_plan_fails(tmp_path: Path) -> None:
    result = runner.invoke(app, ["production", "feedback", "99999"])
    assert result.exit_code == 1
