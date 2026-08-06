"""Tests for Phase 6 M6.3B ProviderValidator and compatibility checking."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from app.narration.errors import ProviderCompatibilityError
from app.narration.fake import FAKE_METADATA, FAKE_MODEL_NAME, FAKE_PROVIDER_NAME, FakeTTSProvider
from app.narration.protocol import TTSRequest
from app.narration.validation import DefaultProviderValidator, ProviderCompatibilityResult


def _request(**overrides) -> TTSRequest:
    defaults = dict(
        text="Hello world",
        provider=FAKE_PROVIDER_NAME,
        model=FAKE_MODEL_NAME,
        voice_id="fake-voice",
        language="en-US",
        speaking_rate=1.0,
        output_format="wav",
        sample_rate_hz=22050,
    )
    defaults.update(overrides)
    return TTSRequest(**defaults)


def _validate(request: TTSRequest | None = None):
    provider = FakeTTSProvider()
    req = request or _request()
    validator = DefaultProviderValidator()
    return validator.validate(provider, FAKE_METADATA, req)


# ── ProviderCompatibilityResult constructors ──────────────────────────────────


def test_ok_result_is_compatible() -> None:
    result = ProviderCompatibilityResult.ok("fake", "fake/FAKE")
    assert result.is_compatible
    assert result.issues == ()


def test_fail_result_is_incompatible() -> None:
    result = ProviderCompatibilityResult.fail("fake", "fake/FAKE", ["bad format"])
    assert not result.is_compatible
    assert "bad format" in result.issues


def test_ok_result_does_not_raise() -> None:
    result = ProviderCompatibilityResult.ok("fake", "fake/FAKE")
    result.raise_if_incompatible()  # should not raise


def test_fail_result_raises_on_check() -> None:
    result = ProviderCompatibilityResult.fail("fake", "fake/FAKE", ["issue"])
    with pytest.raises(ProviderCompatibilityError):
        result.raise_if_incompatible()


def test_fail_result_error_message_contains_issue() -> None:
    result = ProviderCompatibilityResult.fail("fake", "fake/FAKE", ["bad language"])
    with pytest.raises(ProviderCompatibilityError, match="bad language"):
        result.raise_if_incompatible()


def test_result_is_frozen() -> None:
    result = ProviderCompatibilityResult.ok("fake", "fake/FAKE")
    with pytest.raises(FrozenInstanceError):
        result.is_compatible = False  # type: ignore[misc]


# ── DefaultProviderValidator — passing cases ──────────────────────────────────


def test_valid_request_is_compatible() -> None:
    result = _validate()
    assert result.is_compatible


def test_valid_request_no_issues() -> None:
    result = _validate()
    assert result.issues == ()


def test_valid_request_stores_provider_name() -> None:
    result = _validate()
    assert result.provider_name == FAKE_PROVIDER_NAME


def test_valid_request_stores_model_id() -> None:
    result = _validate()
    assert result.model_id == FAKE_MODEL_NAME


# ── DefaultProviderValidator — failing cases ──────────────────────────────────


def test_unsupported_output_format_fails() -> None:
    result = _validate(_request(output_format="flac"))
    assert not result.is_compatible
    assert any("output_format" in issue for issue in result.issues)


def test_unsupported_sample_rate_fails() -> None:
    result = _validate(_request(sample_rate_hz=999_999))
    assert not result.is_compatible
    assert any("sample_rate_hz" in issue for issue in result.issues)


def test_speaking_rate_too_low_fails() -> None:
    result = _validate(_request(speaking_rate=0.01))
    assert not result.is_compatible
    assert any("speaking_rate" in issue for issue in result.issues)


def test_speaking_rate_too_high_fails() -> None:
    result = _validate(_request(speaking_rate=99.0))
    assert not result.is_compatible
    assert any("speaking_rate" in issue for issue in result.issues)


def test_text_too_long_fails() -> None:
    long_text = "x" * 200_000
    result = _validate(_request(text=long_text))
    assert not result.is_compatible
    assert any("max_characters_per_request" in issue for issue in result.issues)


def test_multiple_issues_reported_together() -> None:
    result = _validate(_request(output_format="flac", sample_rate_hz=999_999))
    assert not result.is_compatible
    assert len(result.issues) >= 2


# ── Wildcard language acceptance ──────────────────────────────────────────────


def test_any_language_accepted_by_fake() -> None:
    result = _validate(_request(language="zh-CN"))
    assert result.is_compatible

    result2 = _validate(_request(language="de-DE"))
    assert result2.is_compatible
