"""Tests for the AI error hierarchy."""

from __future__ import annotations

from app.ai.errors import (
    AIError,
    MissingCredentialsError,
    PromptMetadataError,
    PromptNotFoundError,
    RetryExhaustedError,
    TransientProviderError,
    UnknownModelPricingError,
)


def test_all_errors_are_ai_error() -> None:
    for cls in (
        MissingCredentialsError,
        TransientProviderError,
        PromptMetadataError,
        RetryExhaustedError,
        UnknownModelPricingError,
    ):
        assert issubclass(cls, AIError)


def test_prompt_not_found_carries_name_and_version() -> None:
    exc = PromptNotFoundError("my-prompt", "3")
    assert exc.prompt_name == "my-prompt"
    assert exc.prompt_version == "3"
    assert "my-prompt" in str(exc)
    assert "3" in str(exc)


def test_retry_exhausted_carries_metadata() -> None:
    inner = ValueError("boom")
    exc = RetryExhaustedError(attempts=3, last_error=inner)
    assert exc.attempts == 3
    assert exc.last_error is inner
    assert "3" in str(exc)


def test_error_message_does_not_expose_api_key() -> None:
    key = "sk-ant-secret-key-value"
    exc = MissingCredentialsError("API key missing")
    assert key not in str(exc)


def test_unknown_model_pricing_error_carries_model() -> None:
    exc = UnknownModelPricingError("gpt-999")
    assert exc.model == "gpt-999"
    assert "gpt-999" in str(exc)
