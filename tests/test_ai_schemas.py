"""Tests for Phase 2 demonstration output schema (EchoOutput)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.ai.schemas import EchoOutput


def test_valid_echo_output() -> None:
    out = EchoOutput(echo="hello world")
    assert out.echo == "hello world"


def test_missing_required_field_rejected() -> None:
    with pytest.raises(ValidationError):
        EchoOutput()  # type: ignore[call-arg]


def test_extra_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        EchoOutput.model_validate({"echo": "hi", "unexpected_field": "should fail"})


def test_wrong_type_rejected() -> None:
    with pytest.raises(ValidationError):
        EchoOutput.model_validate({"echo": 123})


def test_model_dump_json() -> None:
    out = EchoOutput(echo="test")
    j = out.model_dump_json()
    assert '"echo"' in j
    assert '"test"' in j
