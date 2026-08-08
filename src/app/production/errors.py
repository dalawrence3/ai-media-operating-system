"""Typed errors for Phase 6 M6.1 production plan generation."""

from __future__ import annotations


class ProductionPlanError(Exception):
    """Base class for production plan failures."""


class NoPlanError(ProductionPlanError):
    """Raised when no production plan exists for the given topic or ID."""


class NoApprovedProductionPlanError(ProductionPlanError):
    """Raised by the M6.2 handoff when no active approved plan exists for the topic."""


class IllegalTransitionError(ProductionPlanError):
    """Raised when an illegal plan lifecycle transition is attempted."""


class DuplicateInputHashError(ProductionPlanError):
    """Raised when a plan with the same (script_id, input_hash) already exists."""


class InvalidReasonCodeError(ProductionPlanError):
    """Raised when a rejection reason code is invalid or 'other' is used without notes."""
