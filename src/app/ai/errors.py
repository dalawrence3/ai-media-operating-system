"""Typed exceptions for the AI layer."""

from __future__ import annotations


class AIError(Exception):
    """Base class for all AI-layer errors."""


class ProviderUnavailableError(AIError):
    """The provider endpoint could not be reached."""


class MissingCredentialsError(AIError):
    """Required API credentials are absent or invalid."""


class RequestTimeoutError(AIError):
    """The provider did not respond within the configured timeout."""


class RateLimitError(AIError):
    """The provider returned a rate-limit or quota error (retryable)."""


class TransientProviderError(AIError):
    """A transient server-side error from the provider (retryable)."""


class InvalidStructuredResponseError(AIError):
    """The provider response could not be parsed or did not match the schema."""


class PromptNotFoundError(AIError):
    """No prompt file exists for the given name and version."""

    def __init__(self, name: str, version: str) -> None:
        super().__init__(f"Prompt not found: {name!r} v{version}")
        self.prompt_name = name
        self.prompt_version = version


class PromptMetadataError(AIError):
    """A prompt file has missing, malformed, or inconsistent metadata."""


class RetryExhaustedError(AIError):
    """All retry attempts were exhausted."""

    def __init__(self, attempts: int, last_error: BaseException) -> None:
        super().__init__(f"Retry exhausted after {attempts} attempt(s): {last_error}")
        self.attempts = attempts
        self.last_error = last_error


class UnknownModelPricingError(AIError):
    """The model is not in the pricing registry and is not a known free provider.

    Cost cannot be estimated; callers should record the cost as unknown rather
    than silently treating the call as free.
    """

    def __init__(self, model: str) -> None:
        super().__init__(
            f"No pricing entry for model {model!r}. "
            "Add it to the pricing registry or set ACE_AI_PRICING_FILE."
        )
        self.model = model
