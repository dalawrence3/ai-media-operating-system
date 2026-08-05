"""Typed exception hierarchy for Phase 6 M6.3A caption generation."""

from __future__ import annotations


class CaptionError(Exception):
    """Base class for all caption errors."""


# ── Narration handoff errors ──────────────────────────────────────────────────


class NarrationHandoffError(CaptionError):
    """Base class for errors resolving the approved narration handoff."""


class NoApprovedNarrationRunError(NarrationHandoffError):
    """Raised when no active approved narration run exists for the plan."""


class SupersededNarrationRunError(NarrationHandoffError):
    """Raised when the resolved narration run is superseded."""


class MissingNarrationSegmentAssetError(NarrationHandoffError):
    """Raised when a required narration segment asset is absent."""


class RejectedNarrationSegmentAssetError(NarrationHandoffError):
    """Raised when a required narration segment asset has status 'rejected'."""


class PendingNarrationSegmentAssetError(NarrationHandoffError):
    """Raised when a required narration segment asset is still 'pending'."""


class DuplicateNarrationSegmentAssetError(NarrationHandoffError):
    """Raised when more than one active asset exists for a production segment."""


class MissingNarrationAudioFileError(NarrationHandoffError):
    """Raised when the audio file referenced by a narration asset is absent."""


class NarrationAudioHashMismatchError(NarrationHandoffError):
    """Raised when the audio file SHA-256 does not match the stored hash."""


class NarrationTextHashMismatchError(NarrationHandoffError):
    """Raised when the narration text hash does not match the stored value."""


class IncompleteNarrationProvenanceError(NarrationHandoffError):
    """Raised when required provenance fields on a narration asset are NULL."""


# ── Caption run errors ────────────────────────────────────────────────────────


class CaptionRunError(CaptionError):
    """Base class for caption run lifecycle errors."""


class NoCaptionRunError(CaptionRunError):
    """Raised when a required caption run cannot be found."""


class NoActiveCaptionRunError(CaptionRunError):
    """Raised when no active approved caption run exists."""


class IllegalCaptionTransitionError(CaptionRunError):
    """Raised when an illegal lifecycle transition is attempted."""


class DuplicateCaptionInputHashError(CaptionRunError):
    """Raised when UNIQUE(narration_run_id, input_hash) is violated."""


class FailedCaptionRunError(CaptionRunError):
    """Raised when the idempotency lookup finds a failed run with the same hash.

    Because failed runs are not deleted (no caption-attempt-history table is in
    scope for M6.3A), a retry requires an input or algorithm-version change that
    produces a new input hash.
    """


class SupersededCaptionRunError(CaptionRunError):
    """Raised when an operation targets a superseded caption run."""


# ── Caption approval errors ───────────────────────────────────────────────────


class CaptionApprovalError(CaptionError):
    """Base class for errors that block caption run approval."""


class CueRejectionBlocksApprovalError(CaptionApprovalError):
    """Raised when cue_rejected events exist on a run that is being approved."""


class IncompleteCaptionRunError(CaptionApprovalError):
    """Raised when a run is not in 'completed' status and approval is attempted."""


# ── Segmentation errors ───────────────────────────────────────────────────────


class CaptionSegmentationError(CaptionError):
    """Raised when segmentation produces an invalid or empty cue set."""


# ── Validation errors ─────────────────────────────────────────────────────────


class CaptionValidationError(CaptionError):
    """Raised when the in-memory cue set fails hard validation."""


# ── Storage errors ────────────────────────────────────────────────────────────


class CaptionStorageError(CaptionError):
    """Raised on filesystem errors during caption artifact storage."""


class CaptionExportValidationError(CaptionError):
    """Raised when a generated export file fails content validation."""


# ── Review / feedback errors ──────────────────────────────────────────────────


class InvalidCaptionReasonCodeError(CaptionError):
    """Raised when an unknown or invalid rejection reason code is supplied."""


class InvalidCaptionSeverityError(CaptionError):
    """Raised when severity is outside the valid 1–5 range."""
