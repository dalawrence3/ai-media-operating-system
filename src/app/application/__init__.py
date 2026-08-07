"""Phase 13 — Backend Integration & System Architecture.

Public surface:
  - build_application_service()  → ApplicationService (the entry point)
  - ApplicationService           → command/query facade
  - All contracts in .contracts
  - All commands in .commands
  - All queries in .queries
  - All errors in .errors
"""

from app.application.composition import build_application_service
from app.application.contracts import (
    AccountView,
    AuditView,
    ChannelView,
    CostView,
    DiagnosticFinding,
    DiagnosticReport,
    ExceptionView,
    HealthView,
    OperationView,
    PipelineStageView,
    PipelineView,
    ReviewItemView,
    ScheduleView,
    WorkspaceView,
)
from app.application.errors import ApplicationError
from app.application.services import ApplicationService
from app.application.versioning import APPLICATION_CONTRACT_VERSION

__all__ = [
    "APPLICATION_CONTRACT_VERSION",
    "ApplicationError",
    "ApplicationService",
    "AccountView",
    "AuditView",
    "ChannelView",
    "CostView",
    "DiagnosticFinding",
    "DiagnosticReport",
    "ExceptionView",
    "HealthView",
    "OperationView",
    "PipelineStageView",
    "PipelineView",
    "ReviewItemView",
    "ScheduleView",
    "WorkspaceView",
    "build_application_service",
]
