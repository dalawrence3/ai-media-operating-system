"""Typed exception hierarchy for Phase 6 M6.2 narration generation."""

from __future__ import annotations


class NarrationError(Exception):
    """Base class for all narration errors."""


# ── Configuration / profile errors ───────────────────────────────────────────


class NoVoiceProfileError(NarrationError):
    """Raised when a required voice profile cannot be found."""


class UnknownTTSModelPricingError(NarrationError):
    """Raised when a production TTS model has no pricing entry."""

    def __init__(self, model: str) -> None:
        super().__init__(
            f"No TTS pricing entry for model {model!r}. "
            "Add it to the TTS pricing registry or set ACE_TTS_PRICING_FILE."
        )
        self.model = model


# ── Narration run errors ──────────────────────────────────────────────────────


class NarrationRunError(NarrationError):
    """Base class for narration run errors."""


class NoNarrationRunError(NarrationRunError):
    """Raised when a required narration run cannot be found."""


class NoActiveNarrationRunError(NarrationRunError):
    """Raised when no active approved narration run exists for a topic."""


class IllegalNarrationTransitionError(NarrationRunError):
    """Raised when an illegal lifecycle transition is attempted on a run."""


class DuplicateNarrationInputHashError(NarrationRunError):
    """Raised when UNIQUE(plan_id, input_hash) is violated on narration_runs."""


# ── Segment asset errors ──────────────────────────────────────────────────────


class SegmentAssetError(NarrationError):
    """Base class for segment asset errors."""


class NoSegmentAssetError(SegmentAssetError):
    """Raised when a required narration segment asset cannot be found."""


class IllegalSegmentTransitionError(SegmentAssetError):
    """Raised when an illegal lifecycle transition is attempted on a segment asset."""


# ── Run approval validation errors ───────────────────────────────────────────


class PendingSegmentsError(NarrationError):
    """Raised when pending or missing segments block run approval."""


class RejectedSegmentsError(NarrationError):
    """Raised when rejected segments block run approval."""


# ── Provider and synthesis errors ─────────────────────────────────────────────


class SynthesisError(NarrationError):
    """Raised when TTS synthesis fails."""


# ── Audio validation errors ───────────────────────────────────────────────────


class AudioValidationError(NarrationError):
    """Raised when synthesized audio fails validation."""


# ── Storage errors ────────────────────────────────────────────────────────────


class NarrationStorageError(NarrationError):
    """Raised on filesystem errors during artifact storage."""


# ── Review / feedback errors ──────────────────────────────────────────────────


class InvalidNarrationReasonCodeError(NarrationError):
    """Raised when an unknown or invalid rejection reason code is supplied."""


class InvalidNarrationSeverityError(NarrationError):
    """Raised when severity is outside the valid 1–5 range."""


# ── Provider infrastructure errors (M6.3B) ────────────────────────────────────


class ProviderInfrastructureError(NarrationError):
    """Base class for provider infrastructure errors."""


class UnknownProviderError(ProviderInfrastructureError):
    """Raised when a provider_name is not registered."""

    def __init__(self, provider_name: str) -> None:
        super().__init__(f"No TTS provider registered under {provider_name!r}")
        self.provider_name = provider_name


class ProviderSelectionError(ProviderInfrastructureError):
    """Raised when no registered provider satisfies selection criteria."""


class ProviderCompatibilityError(ProviderInfrastructureError):
    """Raised when a TTSRequest is incompatible with a provider's capabilities."""


class ProviderNotReadyError(ProviderInfrastructureError):
    """Raised when a provider's lifecycle state is not READY."""

    def __init__(self, provider_name: str, state: str) -> None:
        super().__init__(
            f"Provider {provider_name!r} is not ready (state={state!r})"
        )
        self.provider_name = provider_name
        self.state = state


class ProviderConfigError(ProviderInfrastructureError):
    """Raised when provider configuration is invalid or missing."""


class ProviderFactoryError(ProviderInfrastructureError):
    """Raised when provider instantiation via factory fails."""


class FailoverExhaustedError(ProviderInfrastructureError):
    """Raised when all failover candidates have been exhausted."""


class ProviderVersionIncompatibleError(ProviderInfrastructureError):
    """Raised when a provider version is incompatible with the current schema."""

    def __init__(self, provider_name: str, reason: str) -> None:
        super().__init__(
            f"Provider {provider_name!r} version incompatible: {reason}"
        )
        self.provider_name = provider_name
