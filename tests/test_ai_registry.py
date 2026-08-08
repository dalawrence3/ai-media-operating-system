"""Tests for PromptRegistry and Prompt loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ai.errors import PromptMetadataError, PromptNotFoundError
from app.ai.registry import PromptRegistry

# ── Helpers ───────────────────────────────────────────────────────────────────


def _write_prompt(dir_: Path, dir_name: str, dir_version: str, **toml_overrides: str) -> Path:
    """Write a prompt TOML under dir_/dir_name/v{dir_version}.toml.

    toml_overrides replace individual TOML fields (e.g. name="wrong" to test mismatch).
    """
    defaults = {
        "name": dir_name,
        "version": dir_version,
        "description": "test prompt",
        "system": "You are a test assistant.",
        "user_template": "Hello {subject}!",
    }
    data = {**defaults, **toml_overrides}
    path = dir_ / dir_name / f"v{dir_version}.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'{k} = "{v}"' for k, v in data.items()]
    path.write_text("\n".join(lines) + "\n")
    return path


# ── Built-in prompt ───────────────────────────────────────────────────────────


def test_demo_echo_loads_from_package() -> None:
    registry = PromptRegistry()
    p = registry.get("demo-echo", "1")
    assert p.name == "demo-echo"
    assert p.version == "1"
    assert "{text}" in p.user_template


def test_demo_echo_format_user() -> None:
    registry = PromptRegistry()
    p = registry.get("demo-echo", "1")
    formatted = p.format_user(text="Hello world")
    assert "Hello world" in formatted


def test_list_all_includes_demo_echo() -> None:
    registry = PromptRegistry()
    prompts = registry.list_all()
    names = [p.name for p in prompts]
    assert "demo-echo" in names


# ── Missing / malformed prompts ───────────────────────────────────────────────


def test_missing_prompt_raises(tmp_path: Path) -> None:
    registry = PromptRegistry(prompts_dir=tmp_path)
    with pytest.raises(PromptNotFoundError) as exc_info:
        registry.get("ghost", "1")
    assert exc_info.value.prompt_name == "ghost"


def test_missing_required_field_raises(tmp_path: Path) -> None:
    path = tmp_path / "bad" / "v1.toml"
    path.parent.mkdir(parents=True)
    path.write_text('name = "bad"\nversion = "1"\n')  # missing description, system, user_template
    with pytest.raises(PromptMetadataError, match="missing required fields"):
        PromptRegistry(prompts_dir=tmp_path).get("bad", "1")


def test_malformed_toml_raises(tmp_path: Path) -> None:
    path = tmp_path / "bad" / "v1.toml"
    path.parent.mkdir(parents=True)
    path.write_text("this is not valid toml }{")
    with pytest.raises(PromptMetadataError, match="Malformed TOML"):
        PromptRegistry(prompts_dir=tmp_path).get("bad", "1")


def test_name_mismatch_raises(tmp_path: Path) -> None:
    _write_prompt(tmp_path, "correct-name", "1", **{"name": "wrong-name"})
    with pytest.raises(PromptMetadataError, match="name mismatch"):
        PromptRegistry(prompts_dir=tmp_path).get("correct-name", "1")


def test_version_mismatch_raises(tmp_path: Path) -> None:
    _write_prompt(tmp_path, "myprompt", "1", **{"version": "99"})
    with pytest.raises(PromptMetadataError, match="version mismatch"):
        PromptRegistry(prompts_dir=tmp_path).get("myprompt", "1")


def test_empty_field_raises(tmp_path: Path) -> None:
    path = tmp_path / "bad" / "v1.toml"
    path.parent.mkdir(parents=True)
    path.write_text(
        'name = "bad"\nversion = "1"\ndescription = ""\nsystem = "s"\nuser_template = "u"\n'
    )
    with pytest.raises(PromptMetadataError):
        PromptRegistry(prompts_dir=tmp_path).get("bad", "1")


# ── Caching — no silent replacement ──────────────────────────────────────────


def test_same_version_returns_cached_object(tmp_path: Path) -> None:
    _write_prompt(tmp_path, "myprompt", "1")
    registry = PromptRegistry(prompts_dir=tmp_path)
    p1 = registry.get("myprompt", "1")
    p2 = registry.get("myprompt", "1")
    assert p1 is p2  # identical object from cache


# ── format_user ───────────────────────────────────────────────────────────────


def test_format_user_substitutes(tmp_path: Path) -> None:
    _write_prompt(tmp_path, "p", "1", user_template="Hello {name}, you are {age}!")
    p = PromptRegistry(prompts_dir=tmp_path).get("p", "1")
    assert p.format_user(name="Alice", age="30") == "Hello Alice, you are 30!"


def test_format_user_missing_variable_raises(tmp_path: Path) -> None:
    _write_prompt(tmp_path, "p", "1", user_template="Hello {name}!")
    p = PromptRegistry(prompts_dir=tmp_path).get("p", "1")
    with pytest.raises(ValueError, match="Missing template variable"):
        p.format_user()
