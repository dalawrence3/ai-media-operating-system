"""Typed application query objects — read-only requests, no side effects."""

from __future__ import annotations

from dataclasses import dataclass

QUERY_CONTRACT_VERSION = "13.0.0"


@dataclass(frozen=True)
class GetWorkspaceSummaryQuery:
    workspace_id: str
    contract_version: str = QUERY_CONTRACT_VERSION


@dataclass(frozen=True)
class GetChannelSummaryQuery:
    workspace_id: str
    channel_id: str
    contract_version: str = QUERY_CONTRACT_VERSION


@dataclass(frozen=True)
class GetAccountSummaryQuery:
    workspace_id: str
    account_id: str
    contract_version: str = QUERY_CONTRACT_VERSION


@dataclass(frozen=True)
class GetPipelineStatusQuery:
    workspace_id: str
    pipeline_id: str
    contract_version: str = QUERY_CONTRACT_VERSION


@dataclass(frozen=True)
class ListPipelinesQuery:
    workspace_id: str
    status: str | None = None
    channel_id: str | None = None
    limit: int = 50
    contract_version: str = QUERY_CONTRACT_VERSION


@dataclass(frozen=True)
class ListOperationsQuery:
    workspace_id: str
    status: str | None = None
    channel_id: str | None = None
    limit: int = 50
    contract_version: str = QUERY_CONTRACT_VERSION


@dataclass(frozen=True)
class GetReviewQueueQuery:
    workspace_id: str
    contract_version: str = QUERY_CONTRACT_VERSION


@dataclass(frozen=True)
class GetExceptionQueueQuery:
    workspace_id: str
    contract_version: str = QUERY_CONTRACT_VERSION


@dataclass(frozen=True)
class GetSystemHealthQuery:
    workspace_id: str
    contract_version: str = QUERY_CONTRACT_VERSION


@dataclass(frozen=True)
class GetCostSummaryQuery:
    workspace_id: str
    period: str = "monthly"
    contract_version: str = QUERY_CONTRACT_VERSION


@dataclass(frozen=True)
class GetDiagnosticsQuery:
    workspace_id: str
    subject: str
    subject_id: str
    stage: str | None = None
    contract_version: str = QUERY_CONTRACT_VERSION


@dataclass(frozen=True)
class ListSchedulesQuery:
    workspace_id: str
    is_active: bool | None = None
    contract_version: str = QUERY_CONTRACT_VERSION


@dataclass(frozen=True)
class GetAuditTimelineQuery:
    workspace_id: str
    limit: int = 50
    contract_version: str = QUERY_CONTRACT_VERSION


@dataclass(frozen=True)
class GetEffectiveConfigQuery:
    workspace_id: str
    channel_id: str | None = None
    platform_account_id: str | None = None
    contract_version: str = QUERY_CONTRACT_VERSION
