"""Tests for the repository layer."""

from __future__ import annotations

import sqlite3

import pytest

from app.core.models import (
    Run,
    RunStatus,
    Script,
    ScriptStatus,
    Source,
    SourceKind,
    Topic,
    TopicStatus,
)
from app.core.repository import (
    create_run,
    create_script,
    create_source,
    create_topic,
    delete_source,
    delete_topic,
    get_source,
    get_topic,
    list_runs,
    list_scripts,
    list_sources,
    list_topics,
    next_script_version,
    update_run_status,
    update_script_status,
    update_topic,
)

# ---------------------------------------------------------------------------
# Topics
# ---------------------------------------------------------------------------


def test_create_and_get_topic(db: sqlite3.Connection) -> None:
    t = create_topic(db, Topic(title="Black holes"))
    assert t.id is not None
    assert t.title == "Black holes"
    assert t.status == TopicStatus.active
    assert t.created_at is not None

    fetched = get_topic(db, t.id)
    assert fetched is not None
    assert fetched.id == t.id


def test_get_topic_missing_returns_none(db: sqlite3.Connection) -> None:
    assert get_topic(db, 9999) is None


def test_list_topics_empty(db: sqlite3.Connection) -> None:
    assert list_topics(db) == []


def test_list_topics_with_status_filter(db: sqlite3.Connection) -> None:
    create_topic(db, Topic(title="Active"))
    t2 = create_topic(db, Topic(title="Archived"))
    t2.status = TopicStatus.archived
    update_topic(db, t2)

    active = list_topics(db, status=TopicStatus.active)
    archived = list_topics(db, status=TopicStatus.archived)
    assert len(active) == 1
    assert active[0].title == "Active"
    assert len(archived) == 1


def test_update_topic(db: sqlite3.Connection) -> None:
    t = create_topic(db, Topic(title="Original"))
    t.title = "Updated"
    t.angle = "new angle"
    updated = update_topic(db, t)
    assert updated.title == "Updated"
    assert updated.angle == "new angle"


def test_update_topic_without_id_raises(db: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="id"):
        update_topic(db, Topic(title="No id"))


def test_delete_topic_cascades(db: sqlite3.Connection) -> None:
    t = create_topic(db, Topic(title="To delete"))
    create_source(db, Source(topic_id=t.id, kind=SourceKind.url, reference="https://x.com"))  # type: ignore[arg-type]
    assert delete_topic(db, t.id)  # type: ignore[arg-type]
    assert get_topic(db, t.id) is None  # type: ignore[arg-type]
    assert list_sources(db, t.id) == []  # type: ignore[arg-type]


def test_delete_topic_missing_returns_false(db: sqlite3.Connection) -> None:
    assert not delete_topic(db, 9999)


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


def test_create_and_get_source(db: sqlite3.Connection) -> None:
    t = create_topic(db, Topic(title="T"))
    s = create_source(
        db, Source(topic_id=t.id, kind=SourceKind.url, reference="https://example.com")
    )  # type: ignore[arg-type]
    assert s.id is not None
    assert s.kind == SourceKind.url
    fetched = get_source(db, s.id)
    assert fetched is not None
    assert fetched.reference == "https://example.com"


def test_list_sources(db: sqlite3.Connection) -> None:
    t = create_topic(db, Topic(title="T"))
    create_source(db, Source(topic_id=t.id, kind=SourceKind.url, reference="https://a.com"))  # type: ignore[arg-type]
    create_source(db, Source(topic_id=t.id, kind=SourceKind.note, reference="some note"))  # type: ignore[arg-type]
    assert len(list_sources(db, t.id)) == 2  # type: ignore[arg-type]


def test_source_foreign_key_violation(db: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        create_source(db, Source(topic_id=999, kind=SourceKind.url, reference="https://x.com"))


def test_delete_source(db: sqlite3.Connection) -> None:
    t = create_topic(db, Topic(title="T"))
    s = create_source(db, Source(topic_id=t.id, kind=SourceKind.note, reference="note"))  # type: ignore[arg-type]
    assert delete_source(db, s.id)  # type: ignore[arg-type]
    assert get_source(db, s.id) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Scripts
# ---------------------------------------------------------------------------


def test_create_and_get_script(db: sqlite3.Connection) -> None:
    t = create_topic(db, Topic(title="T"))
    s = create_script(db, Script(topic_id=t.id, version=1, body="Hello world."))  # type: ignore[arg-type]
    assert s.id is not None
    assert s.version == 1
    assert s.status == ScriptStatus.draft


def test_next_script_version(db: sqlite3.Connection) -> None:
    t = create_topic(db, Topic(title="T"))
    assert next_script_version(db, t.id) == 1  # type: ignore[arg-type]
    create_script(db, Script(topic_id=t.id, version=1, body="v1"))  # type: ignore[arg-type]
    assert next_script_version(db, t.id) == 2  # type: ignore[arg-type]


def test_duplicate_version_rejected(db: sqlite3.Connection) -> None:
    t = create_topic(db, Topic(title="T"))
    create_script(db, Script(topic_id=t.id, version=1, body="v1"))  # type: ignore[arg-type]
    with pytest.raises(sqlite3.IntegrityError):
        create_script(db, Script(topic_id=t.id, version=1, body="duplicate"))  # type: ignore[arg-type]


def test_list_scripts_ordered_by_version(db: sqlite3.Connection) -> None:
    t = create_topic(db, Topic(title="T"))
    create_script(db, Script(topic_id=t.id, version=2, body="v2"))  # type: ignore[arg-type]
    create_script(db, Script(topic_id=t.id, version=1, body="v1"))  # type: ignore[arg-type]
    scripts = list_scripts(db, t.id)  # type: ignore[arg-type]
    assert [s.version for s in scripts] == [1, 2]


def test_update_script_status(db: sqlite3.Connection) -> None:
    t = create_topic(db, Topic(title="T"))
    s = create_script(db, Script(topic_id=t.id, version=1, body="body"))  # type: ignore[arg-type]
    updated = update_script_status(db, s.id, ScriptStatus.approved)  # type: ignore[arg-type]
    assert updated is not None
    assert updated.status == ScriptStatus.approved


def test_update_script_status_missing_returns_none(db: sqlite3.Connection) -> None:
    result = update_script_status(db, 9999, ScriptStatus.approved)
    assert result is None


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


def test_create_and_get_run(db: sqlite3.Connection) -> None:
    t = create_topic(db, Topic(title="T"))
    r = create_run(db, Run(topic_id=t.id))  # type: ignore[arg-type]
    assert r.id is not None
    assert r.status == RunStatus.pending
    assert r.script_id is None


def test_run_with_script(db: sqlite3.Connection) -> None:
    t = create_topic(db, Topic(title="T"))
    s = create_script(db, Script(topic_id=t.id, version=1, body="body"))  # type: ignore[arg-type]
    r = create_run(db, Run(topic_id=t.id, script_id=s.id))  # type: ignore[arg-type]
    assert r.script_id == s.id


def test_list_runs(db: sqlite3.Connection) -> None:
    t = create_topic(db, Topic(title="T"))
    create_run(db, Run(topic_id=t.id))  # type: ignore[arg-type]
    create_run(db, Run(topic_id=t.id))  # type: ignore[arg-type]
    assert len(list_runs(db, t.id)) == 2  # type: ignore[arg-type]


def test_update_run_status_running(db: sqlite3.Connection) -> None:
    t = create_topic(db, Topic(title="T"))
    r = create_run(db, Run(topic_id=t.id))  # type: ignore[arg-type]
    updated = update_run_status(db, r.id, RunStatus.running)  # type: ignore[arg-type]
    assert updated is not None
    assert updated.status == RunStatus.running
    assert updated.started_at is not None


def test_update_run_status_failed_with_error(db: sqlite3.Connection) -> None:
    t = create_topic(db, Topic(title="T"))
    r = create_run(db, Run(topic_id=t.id))  # type: ignore[arg-type]
    updated = update_run_status(db, r.id, RunStatus.failed, error="timeout")  # type: ignore[arg-type]
    assert updated is not None
    assert updated.error == "timeout"
    assert updated.finished_at is not None


def test_update_run_status_missing_returns_none(db: sqlite3.Connection) -> None:
    result = update_run_status(db, 9999, RunStatus.completed)
    assert result is None


def test_run_foreign_key_violation(db: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        create_run(db, Run(topic_id=9999))
