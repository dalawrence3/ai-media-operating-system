"""Application-layer error hierarchy."""

from __future__ import annotations


class ApplicationError(Exception):
    """Base for all application-layer errors."""


class CommandValidationError(ApplicationError):
    def __init__(self, field: str, detail: str) -> None:
        self.field = field
        self.detail = detail
        super().__init__(f"Command validation failed on '{field}': {detail}")


class UnknownCommandError(ApplicationError):
    def __init__(self, cmd_type: str) -> None:
        super().__init__(f"No handler registered for command '{cmd_type}'")


class WorkspaceNotFoundError(ApplicationError):
    def __init__(self, workspace_id: str) -> None:
        super().__init__(f"Workspace not found: {workspace_id}")


class ChannelNotFoundError(ApplicationError):
    def __init__(self, channel_id: str) -> None:
        super().__init__(f"Channel not found: {channel_id}")


class CrossWorkspaceAccessError(ApplicationError):
    """Raised when a channel/account does not belong to the asserted workspace."""

    def __init__(self, entity: str, entity_id: str, workspace_id: str) -> None:
        self.entity = entity
        self.entity_id = entity_id
        self.workspace_id = workspace_id
        super().__init__(
            f"{entity} '{entity_id}' does not belong to workspace '{workspace_id}'"
        )


class WorkspacePausedError(ApplicationError):
    def __init__(self, workspace_id: str) -> None:
        super().__init__(f"Workspace '{workspace_id}' is paused")


class ChannelPausedError(ApplicationError):
    def __init__(self, channel_id: str) -> None:
        super().__init__(f"Channel '{channel_id}' is paused")


class PolicyBlockedError(ApplicationError):
    def __init__(self, action: str, level: str) -> None:
        self.action = action
        self.level = level
        super().__init__(
            f"Action '{action}' blocked by automation policy (effective level: {level})"
        )


class PipelineNotFoundError(ApplicationError):
    def __init__(self, pipeline_id: str) -> None:
        super().__init__(f"Pipeline not found: {pipeline_id}")


class PipelineAlreadyExistsError(ApplicationError):
    """Idempotency: same key was used for a prior pipeline."""

    def __init__(self, idempotency_key: str, existing_id: str) -> None:
        self.existing_id = existing_id
        super().__init__(
            f"Pipeline with idempotency_key '{idempotency_key}' "
            f"already exists: {existing_id}"
        )


class InvalidStageError(ApplicationError):
    def __init__(self, stage: str) -> None:
        super().__init__(f"Unknown pipeline stage: '{stage}'")


class StagePrerequisiteError(ApplicationError):
    def __init__(self, stage: str, missing: list[str]) -> None:
        self.stage = stage
        self.missing = missing
        super().__init__(
            f"Stage '{stage}' prerequisites not complete: {missing}"
        )


class PipelineNotRecoverableError(ApplicationError):
    def __init__(self, pipeline_id: str, reason: str) -> None:
        super().__init__(f"Pipeline '{pipeline_id}' cannot be recovered: {reason}")


class ScheduleNotFoundError(ApplicationError):
    def __init__(self, schedule_id: str) -> None:
        super().__init__(f"Schedule not found: {schedule_id}")


class InvalidScheduleTypeError(ApplicationError):
    def __init__(self, schedule_type: str) -> None:
        super().__init__(f"Unknown schedule type: '{schedule_type}'")


class ExtensionNotFoundError(ApplicationError):
    def __init__(self, key: str) -> None:
        super().__init__(f"Extension not registered: '{key}'")


class ExtensionAlreadyRegisteredError(ApplicationError):
    def __init__(self, key: str) -> None:
        super().__init__(f"Extension already registered: '{key}'")


class ContractVersionError(ApplicationError):
    def __init__(self, requested: str, supported: str) -> None:
        super().__init__(
            f"Contract version '{requested}' incompatible with supported '{supported}'"
        )


class ReplayBlockedError(ApplicationError):
    """Raised when replay would cause non-idempotent side effects."""

    def __init__(self, event_id: str, reason: str) -> None:
        super().__init__(f"Event '{event_id}' replay blocked: {reason}")


class AuthorizationRequiredError(ApplicationError):
    """Raised when a non-system actor attempts a mutation without an injected auth hook."""

    def __init__(self, actor: str, command_type: str) -> None:
        self.actor = actor
        self.command_type = command_type
        super().__init__(
            f"Actor '{actor}' requires an injected authorization hook "
            f"to execute '{command_type}'; no auth provider configured"
        )


class ExecutorNotFoundError(ApplicationError):
    """Raised when no executor is registered for a pipeline stage."""

    def __init__(self, stage: str) -> None:
        super().__init__(f"No stage executor registered for '{stage}'")


class ExecutorDisabledError(ApplicationError):
    """Raised when the executor for a stage has been administratively disabled."""

    def __init__(self, stage: str) -> None:
        super().__init__(f"Stage executor for '{stage}' is disabled")
