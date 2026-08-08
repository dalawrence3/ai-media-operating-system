"""Tests for M15.7 production executor dispatcher."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.workers.executor import (
    ExecutorResult,
    dispatch_stage,
)


def _pipeline(pipeline_id="pipe-1"):
    return {"id": pipeline_id, "workspace_id": "ws-1", "status": "active"}


def _fake_config(
    ai_provider="fake",
    anthropic_api_key="",
    tts_live_enabled=False,
    publishing_live_enabled=False,
):
    cfg = MagicMock()
    cfg.ai_provider = ai_provider
    cfg.anthropic_api_key = anthropic_api_key
    cfg.elevenlabs_api_key = ""
    cfg.tts_live_enabled = tts_live_enabled
    cfg.publishing_live_enabled = publishing_live_enabled
    return cfg


# ── ExecutorResult ────────────────────────────────────────────────────────


def test_executor_result_to_dict_success():
    r = ExecutorResult("dispatched", "pipe-1", "research", details={"class": "A"})
    d = r.to_dict()
    assert d["status"] == "dispatched"
    assert d["pipeline_id"] == "pipe-1"
    assert d["details"]["class"] == "A"
    assert "error" not in d


def test_executor_result_to_dict_error():
    r = ExecutorResult("blocked", "pipe-1", "publish", error="No live provider")
    d = r.to_dict()
    assert d["status"] == "blocked"
    assert d["error"] == "No live provider"


# ── Class A dispatch ──────────────────────────────────────────────────────


def test_dispatch_class_a_always_succeeds():
    result = dispatch_stage(
        conn=MagicMock(),
        pipeline=_pipeline(),
        stage="research",
        actor="system",
        workspace_id="ws-1",
        config=_fake_config(),  # no live flags
    )
    assert result.status == "dispatched"
    assert result.details["class"] == "A"


def test_dispatch_planning_is_class_a():
    result = dispatch_stage(
        conn=MagicMock(),
        pipeline=_pipeline(),
        stage="planning",
        actor="system",
        workspace_id="ws-1",
        config=_fake_config(),
    )
    assert result.status == "dispatched"


# ── Class B dispatch ──────────────────────────────────────────────────────


def test_dispatch_class_b_blocked_without_provider():
    result = dispatch_stage(
        conn=MagicMock(),
        pipeline=_pipeline(),
        stage="narration",
        actor="system",
        workspace_id="ws-1",
        config=_fake_config(),
    )
    assert result.status == "blocked"
    assert "Class B" in result.error


def test_dispatch_class_b_succeeds_with_tts_live():
    cfg = _fake_config(tts_live_enabled=True)
    cfg.elevenlabs_api_key = "el-key"
    result = dispatch_stage(
        conn=MagicMock(),
        pipeline=_pipeline(),
        stage="narration",
        actor="system",
        workspace_id="ws-1",
        config=cfg,
    )
    assert result.status == "dispatched"
    assert result.details["class"] == "B"


# ── Class C dispatch ──────────────────────────────────────────────────────


def test_dispatch_class_c_blocked_without_publishing_flag():
    cfg = _fake_config(ai_provider="anthropic", anthropic_api_key="sk-key")
    result = dispatch_stage(
        conn=MagicMock(),
        pipeline=_pipeline(),
        stage="publish",
        actor="system",
        workspace_id="ws-1",
        config=cfg,
    )
    assert result.status == "blocked"
    assert "Class C" in result.error


def test_dispatch_class_c_succeeds_with_all_flags():
    cfg = _fake_config(
        ai_provider="anthropic",
        anthropic_api_key="sk-key",
        publishing_live_enabled=True,
    )
    result = dispatch_stage(
        conn=MagicMock(),
        pipeline=_pipeline(),
        stage="publish",
        actor="system",
        workspace_id="ws-1",
        config=cfg,
    )
    assert result.status == "dispatched"
    assert result.details["class"] == "C"


def test_dispatch_unknown_stage_defaults_to_class_c_blocked():
    result = dispatch_stage(
        conn=MagicMock(),
        pipeline=_pipeline(),
        stage="totally_new_stage",
        actor="system",
        workspace_id="ws-1",
        config=_fake_config(),
    )
    assert result.status == "blocked"


def test_dispatch_pipeline_as_dict():
    result = dispatch_stage(
        conn=MagicMock(),
        pipeline={"id": "pipe-dict"},
        stage="research",
        actor="system",
        workspace_id="ws-1",
        config=_fake_config(),
    )
    assert result.pipeline_id == "pipe-dict"
