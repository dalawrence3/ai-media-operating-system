"""Tests for M15.6 provider secrets interface and boundary gates."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.providers.boundaries import (
    ProviderBoundary,
    ProviderBoundaryError,
    StageClass,
    classify_stage,
)
from app.providers.secrets import SecretNotConfiguredError, SecretsInterface

# ── SecretsInterface ──────────────────────────────────────────────────────


def test_get_returns_env_var_value(monkeypatch):
    monkeypatch.setenv("ACE_ANTHROPIC_API_KEY", "sk-test-key")
    s = SecretsInterface()
    assert s.get("ACE_ANTHROPIC_API_KEY") == "sk-test-key"


def test_get_returns_empty_when_not_set_and_not_required(monkeypatch):
    monkeypatch.delenv("ACE_ANTHROPIC_API_KEY", raising=False)
    s = SecretsInterface()
    assert s.get("ACE_ANTHROPIC_API_KEY") == ""


def test_get_raises_when_required_and_missing(monkeypatch):
    monkeypatch.delenv("ACE_ANTHROPIC_API_KEY", raising=False)
    s = SecretsInterface()
    with pytest.raises(SecretNotConfiguredError, match="Anthropic API key"):
        s.get("ACE_ANTHROPIC_API_KEY", required=True)


def test_get_raises_for_unknown_required_var(monkeypatch):
    monkeypatch.delenv("MY_CUSTOM_SECRET", raising=False)
    s = SecretsInterface()
    with pytest.raises(SecretNotConfiguredError, match="MY_CUSTOM_SECRET"):
        s.get("MY_CUSTOM_SECRET", required=True)


def test_is_configured_returns_true_when_set(monkeypatch):
    monkeypatch.setenv("ACE_ELEVENLABS_API_KEY", "el-key")
    s = SecretsInterface()
    assert s.is_configured("ACE_ELEVENLABS_API_KEY") is True


def test_is_configured_returns_false_when_unset(monkeypatch):
    monkeypatch.delenv("ACE_ELEVENLABS_API_KEY", raising=False)
    s = SecretsInterface()
    assert s.is_configured("ACE_ELEVENLABS_API_KEY") is False


def test_redact_shows_prefix_only():
    assert SecretsInterface.redact("sk-test-key-1234") == "sk-t***"


def test_redact_short_value_returns_stars():
    assert SecretsInterface.redact("abc") == "***"


def test_redact_empty_returns_not_set():
    assert SecretsInterface.redact("") == "<not set>"


def test_status_returns_boolean_dict(monkeypatch):
    monkeypatch.setenv("ACE_ANTHROPIC_API_KEY", "set")
    monkeypatch.delenv("ACE_ELEVENLABS_API_KEY", raising=False)
    s = SecretsInterface()
    status = s.status()
    assert isinstance(status, dict)
    assert status["ACE_ANTHROPIC_API_KEY"] is True
    assert status["ACE_ELEVENLABS_API_KEY"] is False
    # Values are bool — never the raw secret
    for v in status.values():
        assert isinstance(v, bool)


# ── Stage classification ──────────────────────────────────────────────────


def test_classify_known_class_a_stages():
    for stage in ("research", "planning", "scripting", "scenes", "captions"):
        assert classify_stage(stage) == StageClass.A


def test_classify_known_class_b_stages():
    for stage in ("content_generation", "narration", "voice_synthesis"):
        assert classify_stage(stage) == StageClass.B


def test_classify_known_class_c_stages():
    for stage in ("upload", "publish", "social_publish"):
        assert classify_stage(stage) == StageClass.C


def test_classify_unknown_stage_defaults_to_class_c():
    assert classify_stage("unknown_future_stage") == StageClass.C


# ── ProviderBoundary ──────────────────────────────────────────────────────


def _fake_config(
    ai_provider="fake",
    anthropic_api_key="",
    elevenlabs_api_key="",
    tts_live_enabled=False,
    publishing_live_enabled=False,
):
    cfg = MagicMock()
    cfg.ai_provider = ai_provider
    cfg.anthropic_api_key = anthropic_api_key
    cfg.elevenlabs_api_key = elevenlabs_api_key
    cfg.tts_live_enabled = tts_live_enabled
    cfg.publishing_live_enabled = publishing_live_enabled
    return cfg


def test_class_a_stage_always_allowed():
    boundary = ProviderBoundary(config=_fake_config())  # no live flags
    boundary.check_stage("research")  # no exception
    boundary.check_stage("planning")


def test_class_b_stage_blocked_without_live_provider():
    boundary = ProviderBoundary(config=_fake_config())
    with pytest.raises(ProviderBoundaryError, match="Class B"):
        boundary.check_stage("narration")


def test_class_b_stage_allowed_with_tts_live():
    cfg = _fake_config(tts_live_enabled=True, elevenlabs_api_key="el-key")
    boundary = ProviderBoundary(config=cfg)
    boundary.check_stage("narration")  # no exception


def test_class_b_stage_allowed_with_anthropic_key():
    cfg = _fake_config(ai_provider="anthropic", anthropic_api_key="sk-key")
    boundary = ProviderBoundary(config=cfg)
    boundary.check_stage("content_generation")  # no exception


def test_class_c_stage_blocked_without_publishing_flag():
    cfg = _fake_config(tts_live_enabled=True, elevenlabs_api_key="el-key")
    boundary = ProviderBoundary(config=cfg)
    with pytest.raises(ProviderBoundaryError, match="Class C"):
        boundary.check_stage("publish")


def test_class_c_stage_also_blocked_without_provider():
    cfg = _fake_config(publishing_live_enabled=True)
    boundary = ProviderBoundary(config=cfg)
    with pytest.raises(ProviderBoundaryError, match="live provider"):
        boundary.check_stage("publish")


def test_class_c_stage_allowed_with_all_flags():
    cfg = _fake_config(
        ai_provider="anthropic",
        anthropic_api_key="sk-key",
        publishing_live_enabled=True,
    )
    boundary = ProviderBoundary(config=cfg)
    boundary.check_stage("publish")  # no exception


def test_is_stage_allowed_returns_bool():
    boundary = ProviderBoundary(config=_fake_config())
    assert boundary.is_stage_allowed("research") is True
    assert boundary.is_stage_allowed("publish") is False


def test_stage_class_method():
    boundary = ProviderBoundary(config=_fake_config())
    assert boundary.stage_class("research") == StageClass.A
    assert boundary.stage_class("narration") == StageClass.B
    assert boundary.stage_class("upload") == StageClass.C
