"""Phase 12 Control Plane errors."""

from __future__ import annotations


class ControlPlaneError(Exception):
    """Base for all control plane errors."""


class WorkspaceNotFoundError(ControlPlaneError):
    """Workspace does not exist."""


class ChannelNotFoundError(ControlPlaneError):
    """Channel does not exist."""


class PlatformNotFoundError(ControlPlaneError):
    """Platform definition does not exist."""


class PlatformAccountNotFoundError(ControlPlaneError):
    """Platform account does not exist."""


class CredentialProfileNotFoundError(ControlPlaneError):
    """Credential profile does not exist."""


class AutomationPolicyNotFoundError(ControlPlaneError):
    """No active automation policy for the given scope."""


class StrategyProfileNotFoundError(ControlPlaneError):
    """Strategy profile does not exist."""


class WorkflowNotFoundError(ControlPlaneError):
    """Workflow does not exist."""


class WorkflowRunNotFoundError(ControlPlaneError):
    """Workflow run does not exist."""


class ExperimentNotFoundError(ControlPlaneError):
    """Experiment does not exist."""


class ExperimentVariantNotFoundError(ControlPlaneError):
    """Experiment variant does not exist."""


class OperationNotFoundError(ControlPlaneError):
    """Operation execution record does not exist."""


class BudgetPolicyNotFoundError(ControlPlaneError):
    """No active budget policy for the given scope."""


class HealthRecordNotFoundError(ControlPlaneError):
    """No health record found for the given entity."""


class ProviderNotFoundError(ControlPlaneError):
    """Provider not registered in the registry."""


class DuplicateIdempotencyKeyError(ControlPlaneError):
    """An operation with this idempotency key already exists."""

    def __init__(self, idempotency_key: str, existing_operation_id: str) -> None:
        super().__init__(
            f"Idempotency key {idempotency_key!r} already used by operation {existing_operation_id}"
        )
        self.idempotency_key = idempotency_key
        self.existing_operation_id = existing_operation_id


class AccountIsolationError(ControlPlaneError):
    """Operation would violate workspace/channel/account isolation boundary."""


class PolicyViolationError(ControlPlaneError):
    """Action not permitted under the current automation policy."""

    def __init__(self, required_level: str, current_level: str) -> None:
        super().__init__(
            f"Action requires automation level {required_level!r}, "
            f"but current policy is {current_level!r}"
        )
        self.required_level = required_level
        self.current_level = current_level


class BudgetExceededError(ControlPlaneError):
    """Budget hard limit exceeded; operation blocked."""

    def __init__(self, scope: str, scope_id: str, limit_usd: float, current_usd: float) -> None:
        super().__init__(
            f"Budget exceeded for {scope} {scope_id}: "
            f"limit={limit_usd:.4f} current={current_usd:.4f}"
        )
        self.scope = scope
        self.scope_id = scope_id
        self.limit_usd = limit_usd
        self.current_usd = current_usd


class InvalidWorkflowConditionError(ControlPlaneError):
    """Workflow condition references an unsupported operator or field."""


class InvalidWorkflowActionError(ControlPlaneError):
    """Workflow action references an unsupported action type."""


class InvalidAutomationLevelError(ControlPlaneError):
    """Automation level value is not recognised."""


class ExperimentAlreadyActiveError(ControlPlaneError):
    """Experiment is immutable once activated; cannot be modified."""

    def __init__(self, experiment_id: str) -> None:
        super().__init__(f"Experiment {experiment_id} is already active and immutable")
        self.experiment_id = experiment_id


class ExperimentNotActiveError(ControlPlaneError):
    """Operation requires experiment to be in active status."""


class VariantAssignmentConflictError(ControlPlaneError):
    """Unit already assigned to a different variant in this experiment."""


class EmergencyStopActiveError(ControlPlaneError):
    """All operations are blocked because an emergency stop is in effect."""


class InvalidEventTypeError(ControlPlaneError):
    """Event type is not in ALL_EVENT_TYPES."""
