"""Tests for Phase 5 content repository (Stage 8)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.content.errors import MalformedBodyJsonError, UnstructuredApprovedScriptError
from app.content.models import ScriptGenerationRun, ScriptGenerationRunStatus
from app.content.repository import (
    create_generation_run,
    fail_generation_run,
    finalize_generation_run,
    find_completed_run_by_input_hash,
    get_active_approved_generated_script,
    get_generation_run,
    list_citations,
    list_generation_runs,
)
from app.content.schemas import GeneratedScript, LLMGeneratedScript, ScriptCitation
from app.core.database import open_db
from app.core.models import Script, Topic
from app.core.repository import (
    approve_script,
    create_script,
    create_topic,
    next_script_version,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / "test.db")


def _topic(db: sqlite3.Connection, title: str = "Test Topic") -> Topic:
    return create_topic(db, Topic(title=title))


def _base_run(topic_id: int) -> ScriptGenerationRun:
    return ScriptGenerationRun(
        topic_id=topic_id,
        status=ScriptGenerationRunStatus.running,
        input_hash="a" * 64,
        evidence_hash="b" * 64,
        prompt_hash="c" * 64,
        prompt_name="script-generation",
        prompt_version="1",
        model="fake",
        temperature=0.3,
        max_tokens=2048,
        tone="conversational",
        audience="",
        target_duration_s=60,
        started_at="2024-01-01T00:00:00",
    )


def _make_generated_script(title: str = "My Script") -> GeneratedScript:
    llm = LLMGeneratedScript(
        title=title,
        sections=[
            {"section_type": "hook", "text": "Hook text.", "cited_claim_ids": []},
            {"section_type": "body", "text": "Body text.", "cited_claim_ids": []},
        ],
    )
    return GeneratedScript.from_llm(llm)


def _finalize(
    db: sqlite3.Connection,
    run_id: int,
    topic_id: int,
    script: GeneratedScript | None = None,
    citations: list[ScriptCitation] | None = None,
    replace_run_id: int | None = None,
) -> tuple[int, int]:
    if script is None:
        script = _make_generated_script()
    from app.content.renderer import compute_duration_s, count_words, render_body

    body = render_body(script)
    word_count = count_words(script)
    duration_s = compute_duration_s(word_count)
    version = next_script_version(db, topic_id)
    return finalize_generation_run(
        db,
        run_id=run_id,
        topic_id=topic_id,
        script_body=body,
        script_body_json=script.model_dump_json(),
        script_version=version,
        computed_word_count=word_count,
        computed_duration_s=duration_s,
        warnings=list(script.warnings),
        requires_evidence_review=False,
        pending_citations=citations or [],
        ai_call_id=None,
        replace_run_id=replace_run_id,
    )


# ---------------------------------------------------------------------------
# Generation-run creation and retrieval
# ---------------------------------------------------------------------------


class TestCreateAndGetGenerationRun:
    def test_create_returns_run_with_id(self, db: sqlite3.Connection) -> None:
        t = _topic(db)
        run = create_generation_run(db, _base_run(t.id))  # type: ignore[arg-type]
        assert run.id is not None
        assert run.status == ScriptGenerationRunStatus.running

    def test_get_by_id(self, db: sqlite3.Connection) -> None:
        t = _topic(db)
        created = create_generation_run(db, _base_run(t.id))  # type: ignore[arg-type]
        fetched = get_generation_run(db, created.id)  # type: ignore[arg-type]
        assert fetched is not None
        assert fetched.id == created.id

    def test_get_missing_returns_none(self, db: sqlite3.Connection) -> None:
        assert get_generation_run(db, 99999) is None

    def test_list_runs_ordered_desc(self, db: sqlite3.Connection) -> None:
        t = _topic(db)
        r1 = create_generation_run(db, _base_run(t.id))  # type: ignore[arg-type]
        r2 = create_generation_run(db, _base_run(t.id))  # type: ignore[arg-type]
        runs = list_generation_runs(db, t.id)  # type: ignore[arg-type]
        assert runs[0].id == r2.id  # newest first
        assert runs[1].id == r1.id

    def test_list_runs_empty(self, db: sqlite3.Connection) -> None:
        t = _topic(db)
        assert list_generation_runs(db, t.id) == []  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Idempotency lookup
# ---------------------------------------------------------------------------


class TestFindCompletedRunByInputHash:
    def test_no_match_returns_none(self, db: sqlite3.Connection) -> None:
        t = _topic(db)
        result = find_completed_run_by_input_hash(db, t.id, "x" * 64)  # type: ignore[arg-type]
        assert result is None

    def test_finds_completed_run(self, db: sqlite3.Connection) -> None:
        t = _topic(db)
        run = create_generation_run(db, _base_run(t.id))  # type: ignore[arg-type]
        _finalize(db, run.id, t.id)  # type: ignore[arg-type]
        found = find_completed_run_by_input_hash(db, t.id, "a" * 64)  # type: ignore[arg-type]
        assert found is not None
        assert found.status == ScriptGenerationRunStatus.completed

    def test_running_run_not_returned(self, db: sqlite3.Connection) -> None:
        t = _topic(db)
        create_generation_run(db, _base_run(t.id))  # type: ignore[arg-type]
        result = find_completed_run_by_input_hash(db, t.id, "a" * 64)  # type: ignore[arg-type]
        assert result is None

    def test_superseded_run_not_returned(self, db: sqlite3.Connection) -> None:
        t = _topic(db)
        r1 = create_generation_run(db, _base_run(t.id))  # type: ignore[arg-type]
        _finalize(db, r1.id, t.id)  # type: ignore[arg-type]
        r2 = create_generation_run(db, _base_run(t.id))  # type: ignore[arg-type]
        _finalize(db, r2.id, t.id, replace_run_id=r1.id)  # type: ignore[arg-type]
        # r1 is superseded, only r2 should be found
        found = find_completed_run_by_input_hash(db, t.id, "a" * 64)  # type: ignore[arg-type]
        assert found is not None
        assert found.id == r2.id


# ---------------------------------------------------------------------------
# Atomic finalization
# ---------------------------------------------------------------------------


class TestFinalizeGenerationRun:
    def test_creates_script(self, db: sqlite3.Connection) -> None:
        t = _topic(db)
        run = create_generation_run(db, _base_run(t.id))  # type: ignore[arg-type]
        script_id, _ = _finalize(db, run.id, t.id)  # type: ignore[arg-type]
        row = db.execute("SELECT * FROM scripts WHERE id=?", (script_id,)).fetchone()
        assert row is not None
        assert row["status"] == "draft"
        assert row["body_json"] is not None

    def test_run_status_becomes_completed(self, db: sqlite3.Connection) -> None:
        t = _topic(db)
        run = create_generation_run(db, _base_run(t.id))  # type: ignore[arg-type]
        _, run_id = _finalize(db, run.id, t.id)  # type: ignore[arg-type]
        row = db.execute(
            "SELECT status FROM script_generation_runs WHERE id=?", (run_id,)
        ).fetchone()
        assert row["status"] == "completed"

    def test_citations_inserted(self, db: sqlite3.Connection) -> None:
        t = _topic(db)
        run = create_generation_run(db, _base_run(t.id))  # type: ignore[arg-type]
        db.execute("PRAGMA foreign_keys=OFF")
        citations = [
            ScriptCitation(script_id=0, claim_id=1, section_index=0, citation_order=0),
            ScriptCitation(script_id=0, claim_id=2, section_index=1, citation_order=0),
        ]
        script_id, _ = _finalize(db, run.id, t.id, citations=citations)  # type: ignore[arg-type]
        cits = list_citations(db, script_id)
        assert len(cits) == 2
        assert cits[0].claim_id == 1
        assert cits[1].claim_id == 2

    def test_rollback_on_duplicate_version(self, db: sqlite3.Connection) -> None:
        t = _topic(db)
        run1 = create_generation_run(db, _base_run(t.id))  # type: ignore[arg-type]
        _finalize(db, run1.id, t.id)  # type: ignore[arg-type]
        # Force a version collision by resetting next version counter via direct insert
        db.execute(
            "INSERT INTO scripts (topic_id, version, body, status, created_at, updated_at)"
            " VALUES (?, 2, 'body', 'draft', '2024-01-01', '2024-01-01')",
            (t.id,),
        )
        db.commit()
        run2 = create_generation_run(db, _base_run(t.id))  # type: ignore[arg-type]
        with pytest.raises(sqlite3.IntegrityError):
            # version=2 collides — SAVEPOINT must rollback
            finalize_generation_run(
                db,
                run_id=run2.id,
                topic_id=t.id,  # type: ignore[arg-type]
                script_body="body",
                script_body_json='{"title":"T","sections":[],"warnings":[]}',
                script_version=2,
                computed_word_count=10,
                computed_duration_s=15,
                warnings=[],
                requires_evidence_review=False,
                pending_citations=[],
                ai_call_id=None,
            )
        # run2 should still be in 'running' state (rollback worked)
        row = db.execute(
            "SELECT status FROM script_generation_runs WHERE id=?", (run2.id,)
        ).fetchone()
        assert row["status"] == "running"

    def test_replace_supersedes_prior_run(self, db: sqlite3.Connection) -> None:
        t = _topic(db)
        r1 = create_generation_run(db, _base_run(t.id))  # type: ignore[arg-type]
        _finalize(db, r1.id, t.id)  # type: ignore[arg-type]
        r2 = create_generation_run(db, _base_run(t.id))  # type: ignore[arg-type]
        _finalize(db, r2.id, t.id, replace_run_id=r1.id)  # type: ignore[arg-type]
        r1_row = db.execute(
            "SELECT superseded_at, superseded_by_run_id FROM script_generation_runs WHERE id=?",
            (r1.id,),
        ).fetchone()
        assert r1_row["superseded_at"] is not None
        assert r1_row["superseded_by_run_id"] == r2.id


# ---------------------------------------------------------------------------
# Fail generation run
# ---------------------------------------------------------------------------


class TestFailGenerationRun:
    def test_marks_failed(self, db: sqlite3.Connection) -> None:
        t = _topic(db)
        run = create_generation_run(db, _base_run(t.id))  # type: ignore[arg-type]
        fail_generation_run(db, run.id, "Provider timeout")  # type: ignore[arg-type]
        row = db.execute(
            "SELECT status, error_message FROM script_generation_runs WHERE id=?", (run.id,)
        ).fetchone()
        assert row["status"] == "failed"
        assert row["error_message"] == "Provider timeout"

    def test_does_not_touch_approved_scripts(self, db: sqlite3.Connection) -> None:
        t = _topic(db)
        s = create_script(db, Script(topic_id=t.id, version=1, body="manual"))  # type: ignore[arg-type]
        approve_script(db, s.id)  # type: ignore[arg-type]
        run = create_generation_run(db, _base_run(t.id))  # type: ignore[arg-type]
        fail_generation_run(db, run.id, "Error")  # type: ignore[arg-type]
        row = db.execute("SELECT status FROM scripts WHERE id=?", (s.id,)).fetchone()
        assert row["status"] == "approved"


# ---------------------------------------------------------------------------
# Phase 6 handoff
# ---------------------------------------------------------------------------


class TestGetActiveApprovedGeneratedScript:
    def test_returns_none_when_no_approved(self, db: sqlite3.Connection) -> None:
        t = _topic(db)
        assert get_active_approved_generated_script(db, t.id) is None  # type: ignore[arg-type]

    def test_manual_script_raises_unstructured_error(self, db: sqlite3.Connection) -> None:
        t = _topic(db)
        s = create_script(db, Script(topic_id=t.id, version=1, body="manual body"))  # type: ignore[arg-type]
        approve_script(db, s.id)  # type: ignore[arg-type]
        with pytest.raises(UnstructuredApprovedScriptError):
            get_active_approved_generated_script(db, t.id)  # type: ignore[arg-type]

    def test_returns_approved_generated_script(self, db: sqlite3.Connection) -> None:
        t = _topic(db)
        run = create_generation_run(db, _base_run(t.id))  # type: ignore[arg-type]
        script_id, _ = _finalize(db, run.id, t.id)  # type: ignore[arg-type]
        approve_script(db, script_id)
        result = get_active_approved_generated_script(db, t.id)  # type: ignore[arg-type]
        assert result is not None
        assert result.script_id == script_id
        assert result.status == "approved"

    def test_malformed_body_json_raises(self, db: sqlite3.Connection) -> None:
        t = _topic(db)
        db.execute(
            "INSERT INTO scripts"
            " (topic_id, version, body, body_json, format, status,"
            " approved_at, created_at, updated_at)"
            " VALUES (?, 1, 'body', '{bad json}', 'short', 'approved',"
            " '2024-01-01T00:00:00', '2024-01-01T00:00:00', '2024-01-01T00:00:00')",
            (t.id,),
        )
        db.commit()
        with pytest.raises(MalformedBodyJsonError):
            get_active_approved_generated_script(db, t.id)  # type: ignore[arg-type]

    def test_includes_citations(self, db: sqlite3.Connection) -> None:
        t = _topic(db)
        db.execute("PRAGMA foreign_keys=OFF")
        run = create_generation_run(db, _base_run(t.id))  # type: ignore[arg-type]
        citations = [ScriptCitation(script_id=0, claim_id=99, section_index=0, citation_order=0)]
        script_id, _ = _finalize(db, run.id, t.id, citations=citations)  # type: ignore[arg-type]
        approve_script(db, script_id)
        result = get_active_approved_generated_script(db, t.id)  # type: ignore[arg-type]
        assert result is not None
        assert len(result.citations) == 1
        assert result.citations[0].claim_id == 99

    def test_returns_latest_after_supersession(self, db: sqlite3.Connection) -> None:
        t = _topic(db)
        r1 = create_generation_run(db, _base_run(t.id))  # type: ignore[arg-type]
        sid1, _ = _finalize(db, r1.id, t.id)
        approve_script(db, sid1)
        r2 = create_generation_run(db, _base_run(t.id))  # type: ignore[arg-type]
        sid2, _ = _finalize(db, r2.id, t.id)
        approve_script(db, sid2)
        result = get_active_approved_generated_script(db, t.id)  # type: ignore[arg-type]
        assert result is not None
        assert result.script_id == sid2
