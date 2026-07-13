"""Tests for Pydantic domain models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

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


class TestTopic:
    def test_valid_topic(self) -> None:
        t = Topic(title="Black holes", angle="debunking myths")
        assert t.title == "Black holes"
        assert t.status == TopicStatus.active

    def test_title_stripped(self) -> None:
        t = Topic(title="  hello  ")
        assert t.title == "hello"

    def test_empty_title_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Topic(title="")

    def test_title_too_long_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Topic(title="x" * 201)

    def test_invalid_status_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Topic(title="hi", status="deleted")  # type: ignore[arg-type]


class TestSource:
    def test_valid_url_source(self) -> None:
        s = Source(topic_id=1, kind=SourceKind.url, reference="https://example.com")
        assert s.kind == SourceKind.url

    def test_empty_reference_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Source(topic_id=1, kind=SourceKind.note, reference="")


class TestScript:
    def test_valid_script(self) -> None:
        s = Script(topic_id=1, version=1, body="Line one.\nLine two.")
        assert s.status == ScriptStatus.draft

    def test_version_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Script(topic_id=1, version=0, body="text")

    def test_empty_body_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Script(topic_id=1, version=1, body="")


class TestRun:
    def test_valid_run(self) -> None:
        r = Run(topic_id=1)
        assert r.status == RunStatus.pending

    def test_run_with_script(self) -> None:
        r = Run(topic_id=1, script_id=5)
        assert r.script_id == 5
