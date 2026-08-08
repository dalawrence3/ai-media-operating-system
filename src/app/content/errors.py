"""Typed errors for Phase 5 script generation."""

from __future__ import annotations


class ScriptGenerationError(Exception):
    """Base class for script generation failures."""


class NoActiveEvidenceError(ScriptGenerationError):
    """Raised when evidence is required but none exists for the topic."""


class ScriptValidationError(ScriptGenerationError):
    """Raised when generated output fails deterministic validation."""


class ScriptRenderingError(ScriptGenerationError):
    """Raised when deterministic body rendering fails."""


class UnstructuredApprovedScriptError(ScriptGenerationError):
    """Raised by the Phase 6 handoff when the active approved Script has body_json=NULL."""


class MalformedBodyJsonError(ScriptGenerationError):
    """Raised when body_json is non-NULL but cannot be parsed as GeneratedScript."""


class ScriptNotFoundError(ScriptGenerationError):
    """Raised when a Script ID does not exist."""


class ScriptOwnershipError(ScriptGenerationError):
    """Raised when a Script does not belong to the expected Topic."""
