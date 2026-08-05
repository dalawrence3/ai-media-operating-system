"""Tests for Phase 5 generator (Stage 10). No live LLM calls — FakeProvider only."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.ai.fake import FakeProvider
from app.content.errors import NoActiveEvidenceError, ScriptGenerationError
from app.content.generator import GenerationResult, generate_script
from app.content.repository import get_generation_run, list_generation_runs
from app.core.database import open_db
from app.core.models import Script, Topic
from app.core.repository import approve_script, create_script, create_topic
from app.research.models import ClaimSupportStatus, ClaimType, EvidenceClaim

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / "test.db")


def _topic(db: sqlite3.Connection, title: str = "Test Topic") -> Topic:
    return create_topic(db, Topic(title=title, angle="Some angle"))


def _evidence(
    claim_id: int,
    source_id: int = 1,
    requires_date_review: bool = False,
) -> EvidenceClaim:
    return EvidenceClaim(
        claim_id=claim_id,
        claim_text=f"Claim {claim_id} text.",
        claim_type=ClaimType.factual,
        supporting_quote="Some quote.",
        quote_support_status=ClaimSupportStatus.exact,
        quote_start=0,
        quote_end=10,
        page_number=None,
        chunk_index=0,
        requires_date_review=requires_date_review,
        source_id=source_id,
        source_content_id=claim_id,
        extraction_run_id=1,
        source_title=f"Source {source_id}",
        canonical_url=None,
        author=None,
        published_at=None,
        quality_score=0.8,
        extraction_status="ok",
        suspected_truncation=False,
        prompt_name="test",
        prompt_version="1",
        model="fake",
    )


def _fake_script_json(
    title: str = "Test Script",
    hook: str = "This is the hook.",
    body: str = "This is the body.",
) -> str:
    return json.dumps({
        "title": title,
        "sections": [
            {"section_type": "hook", "text": hook, "cited_claim_ids": []},
            {"section_type": "body", "text": body, "cited_claim_ids": []},
        ],
    })


def _fake_script_with_citation(claim_id: int) -> str:
    return json.dumps({
        "title": "Citing Script",
        "sections": [
            {
                "section_type": "hook",
                "text": f"Fact [claim:{claim_id}] is true.",
                "cited_claim_ids": [claim_id],
            },
        ],
    })


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestGenerateScriptSuccess:
    def test_returns_generation_result(self, db: sqlite3.Connection) -> None:
        t = _topic(db)
        provider = FakeProvider(_fake_script_json())
        result = generate_script(db, provider, t, evidence=[], allow_no_evidence=True)
        assert isinstance(result, GenerationResult)
        assert result.script_id is not None
        assert result.run_id is not None
        assert not result.was_idempotent

    def test_script_status_is_draft(self, db: sqlite3.Connection) -> None:
        t = _topic(db)
        provider = FakeProvider(_fake_script_json())
        result = generate_script(db, provider, t, evidence=[], allow_no_evidence=True)
        row = db.execute("SELECT status FROM scripts WHERE id=?", (result.script_id,)).fetchone()
        assert row["status"] == "draft"

    def test_script_has_body_json(self, db: sqlite3.Connection) -> None:
        t = _topic(db)
        provider = FakeProvider(_fake_script_json())
        result = generate_script(db, provider, t, evidence=[], allow_no_evidence=True)
        row = db.execute("SELECT body_json FROM scripts WHERE id=?", (result.script_id,)).fetchone()
        assert row["body_json"] is not None

    def test_run_status_completed(self, db: sqlite3.Connection) -> None:
        t = _topic(db)
        provider = FakeProvider(_fake_script_json())
        result = generate_script(db, provider, t, evidence=[], allow_no_evidence=True)
        run = get_generation_run(db, result.run_id)
        assert run is not None
        assert run.status.value == "completed"

    def test_citation_inserted_for_claimed_evidence(self, db: sqlite3.Connection) -> None:
        t = _topic(db)
        ev = _evidence(1)
        db.execute("PRAGMA foreign_keys=OFF")
        provider = FakeProvider(_fake_script_with_citation(1))
        result = generate_script(db, provider, t, evidence=[ev])
        rows = db.execute(
            "SELECT * FROM script_citations WHERE script_id=?", (result.script_id,)
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["claim_id"] == 1

    def test_word_count_and_duration_populated(self, db: sqlite3.Connection) -> None:
        t = _topic(db)
        provider = FakeProvider(_fake_script_json())
        result = generate_script(db, provider, t, evidence=[], allow_no_evidence=True)
        assert result.word_count > 0
        assert result.duration_s >= 15


# ---------------------------------------------------------------------------
# Evidence guard (Invariant 10)
# ---------------------------------------------------------------------------


class TestEvidenceGuard:
    def test_empty_evidence_raises_without_flag(self, db: sqlite3.Connection) -> None:
        t = _topic(db)
        provider = FakeProvider(_fake_script_json())
        with pytest.raises(NoActiveEvidenceError):
            generate_script(db, provider, t, evidence=[])

    def test_empty_evidence_ok_with_allow_flag(self, db: sqlite3.Connection) -> None:
        t = _topic(db)
        provider = FakeProvider(_fake_script_json())
        result = generate_script(db, provider, t, evidence=[], allow_no_evidence=True)
        assert result.script_id is not None

    def test_no_run_created_when_evidence_guard_fails(self, db: sqlite3.Connection) -> None:
        t = _topic(db)
        provider = FakeProvider(_fake_script_json())
        try:
            generate_script(db, provider, t, evidence=[])
        except NoActiveEvidenceError:
            pass
        runs = list_generation_runs(db, t.id)  # type: ignore[arg-type]
        assert len(runs) == 0


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_same_inputs_returns_idempotent(self, db: sqlite3.Connection) -> None:
        t = _topic(db)
        provider = FakeProvider(_fake_script_json())
        r1 = generate_script(db, provider, t, evidence=[], allow_no_evidence=True)
        r2 = generate_script(db, provider, t, evidence=[], allow_no_evidence=True)
        assert r2.was_idempotent
        assert r2.script_id == r1.script_id

    def test_idempotent_does_not_create_new_run(self, db: sqlite3.Connection) -> None:
        t = _topic(db)
        provider = FakeProvider(_fake_script_json())
        generate_script(db, provider, t, evidence=[], allow_no_evidence=True)
        generate_script(db, provider, t, evidence=[], allow_no_evidence=True)
        runs = list_generation_runs(db, t.id)  # type: ignore[arg-type]
        assert len(runs) == 1

    def test_different_tone_not_idempotent(self, db: sqlite3.Connection) -> None:
        t = _topic(db)
        p1 = FakeProvider(_fake_script_json("Script A"))
        p2 = FakeProvider(_fake_script_json("Script B"))
        r1 = generate_script(db, p1, t, evidence=[], allow_no_evidence=True, tone="conversational")
        r2 = generate_script(db, p2, t, evidence=[], allow_no_evidence=True, tone="formal")
        assert not r2.was_idempotent
        assert r2.script_id != r1.script_id


# ---------------------------------------------------------------------------
# Failure persistence (Invariant 23)
# ---------------------------------------------------------------------------


class TestFailurePersistence:
    def test_invalid_json_marks_run_failed(self, db: sqlite3.Connection) -> None:
        t = _topic(db)
        provider = FakeProvider("not json at all")
        with pytest.raises(ScriptGenerationError):
            generate_script(db, provider, t, evidence=[], allow_no_evidence=True)
        runs = list_generation_runs(db, t.id)  # type: ignore[arg-type]
        assert len(runs) == 1
        assert runs[0].status.value == "failed"
        assert runs[0].error_message is not None

    def test_failed_run_leaves_prior_approved_script_intact(self, db: sqlite3.Connection) -> None:
        t = _topic(db)
        # First: create and approve a manual script
        s = create_script(db, Script(topic_id=t.id, version=1, body="manual"))  # type: ignore[arg-type]
        approve_script(db, s.id)

        # Then: try generating with bad output
        provider = FakeProvider("bad json")
        with pytest.raises(ScriptGenerationError):
            generate_script(db, provider, t, evidence=[], allow_no_evidence=True)

        # The manual approved script must still be there
        row = db.execute("SELECT status FROM scripts WHERE id=?", (s.id,)).fetchone()
        assert row["status"] == "approved"


# ---------------------------------------------------------------------------
# Replace semantics (Invariant 22)
# ---------------------------------------------------------------------------


class TestReplaceSemantics:
    def test_replace_supersedes_prior_run(self, db: sqlite3.Connection) -> None:
        t = _topic(db)
        p1 = FakeProvider(_fake_script_json("First"))
        r1 = generate_script(db, p1, t, evidence=[], allow_no_evidence=True)

        p2 = FakeProvider(_fake_script_json("Second"))
        generate_script(
            db, p2, t, evidence=[], allow_no_evidence=True,
            replace_run_id=r1.run_id, tone="formal",
        )
        run1_row = db.execute(
            "SELECT superseded_at FROM script_generation_runs WHERE id=?", (r1.run_id,)
        ).fetchone()
        assert run1_row["superseded_at"] is not None


# ---------------------------------------------------------------------------
# Zero-evidence output constraints (Invariant 11)
# ---------------------------------------------------------------------------


class TestZeroEvidenceConstraints:
    def test_zero_evidence_script_has_no_citations(self, db: sqlite3.Connection) -> None:
        t = _topic(db)
        provider = FakeProvider(_fake_script_json())
        result = generate_script(db, provider, t, evidence=[], allow_no_evidence=True)
        rows = db.execute(
            "SELECT COUNT(*) FROM script_citations WHERE script_id=?", (result.script_id,)
        ).fetchone()
        assert rows[0] == 0

    def test_zero_evidence_script_with_markers_raises(self, db: sqlite3.Connection) -> None:
        t = _topic(db)
        bad_output = json.dumps({
            "title": "Bad",
            "sections": [
                {
                    "section_type": "hook",
                    "text": "Fact [claim:1] claimed.",
                    "cited_claim_ids": [1],
                },
            ],
        })
        provider = FakeProvider(bad_output)
        with pytest.raises(ScriptGenerationError):
            generate_script(db, provider, t, evidence=[], allow_no_evidence=True)
