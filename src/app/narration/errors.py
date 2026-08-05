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
