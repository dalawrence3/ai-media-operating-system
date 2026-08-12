"""Frontend-facing service facade.

Phase 14 consumes this facade exclusively — it must not access SQLite directly.
All results are Pydantic view models; no sqlite.Row objects cross the boundary.
"""

from __future__ import annotations

from typing import Any

from app.application import diagnostics as diag_svc
from app.application import dispatcher
from app.application import exception_queue as exc_svc
from app.application import health as health_svc
from app.application import recovery as recovery_svc
from app.application import review as review_svc
from app.application import scheduler as sched_svc
from app.application import state as pipeline_state
from app.application.authorization import AuthHook, default_auth_hook
from app.application.commands import (
    AdvancePipelineStageCommand,
    ApproveReviewItemCommand,
    CancelOperationCommand,
    CancelPipelineCommand,
    CreateChannelCommand,
    CreatePlatformAccountCommand,
    CreateScheduleCommand,
    DeleteScheduleCommand,
    ExecutePipelineStageCommand,
    FailPipelineStageCommand,
    PausePipelineCommand,
    PauseScheduleCommand,
    RecoverPipelineCommand,
    RejectReviewItemCommand,
    ReplayEventCommand,
    ResumePipelineCommand,
    ResumeScheduleCommand,
    RetryOperationCommand,
    StartPipelineCommand,
)
from app.application.configuration import resolve_config
from app.application.contracts import (
    AccountView,
    AuditView,
    ChannelView,
    CostView,
    DiagnosticReport,
    ExceptionView,
    HealthView,
    OperationView,
    PipelineView,
    ReviewItemView,
    ScheduleView,
    WorkspaceView,
)
from app.application.observability import NullObservabilityContext, ObservabilityContext
from app.application.queries import (
    GetAccountSummaryQuery,
    GetAuditTimelineQuery,
    GetChannelSummaryQuery,
    GetCostSummaryQuery,
    GetDiagnosticsQuery,
    GetEffectiveConfigQuery,
    GetExceptionQueueQuery,
    GetPipelineStatusQuery,
    GetReviewQueueQuery,
    GetSystemHealthQuery,
    GetWorkspaceSummaryQuery,
    ListOperationsQuery,
    ListPipelinesQuery,
    ListSchedulesQuery,
)
from app.application.registry import ExtensionRegistry
from app.control_plane import repository as cp_repo
from app.control_plane import services as cp_services
from app.control_plane.costs import check_budget


class ApplicationService:
    """Single public backend surface for Phase 14 consumers.

    Instantiated once per request (or shared per process with a pooled conn).
    Thread-safety is the caller's responsibility.
    """

    def __init__(
        self,
        conn: Any,
        *,
        registry: ExtensionRegistry | None = None,
        obs_ctx: ObservabilityContext | None = None,
        auth_hook: AuthHook | None = None,
    ) -> None:
        self._conn = conn
        self._registry = registry or ExtensionRegistry()
        self._obs = obs_ctx or NullObservabilityContext()
        # default_auth_hook fails closed for non-system actors.
        self._auth_hook: AuthHook = auth_hook if auth_hook is not None else default_auth_hook

    # ------------------------------------------------------------------
    # Mutating commands
    # ------------------------------------------------------------------

    def create_channel(self, cmd: CreateChannelCommand):
        with self._obs.span("create_channel", workspace_id=cmd.workspace_id):
            return dispatcher.dispatch(
                self._conn, cmd, registry=self._registry, auth_hook=self._auth_hook
            )

    def create_platform_account(self, cmd: CreatePlatformAccountCommand):
        with self._obs.span("create_platform_account"):
            return dispatcher.dispatch(
                self._conn, cmd, registry=self._registry, auth_hook=self._auth_hook
            )

    def start_pipeline(self, cmd: StartPipelineCommand) -> PipelineView:
        with self._obs.span("start_pipeline", workspace_id=cmd.workspace_id):
            return dispatcher.dispatch(
                self._conn, cmd, registry=self._registry, auth_hook=self._auth_hook
            )

    def pause_pipeline(self, cmd: PausePipelineCommand) -> PipelineView:
        with self._obs.span("pause_pipeline"):
            return dispatcher.dispatch(
                self._conn, cmd, registry=self._registry, auth_hook=self._auth_hook
            )

    def resume_pipeline(self, cmd: ResumePipelineCommand) -> PipelineView:
        with self._obs.span("resume_pipeline"):
            return dispatcher.dispatch(
                self._conn, cmd, registry=self._registry, auth_hook=self._auth_hook
            )

    def cancel_pipeline(self, cmd: CancelPipelineCommand) -> PipelineView:
        with self._obs.span("cancel_pipeline"):
            return dispatcher.dispatch(
                self._conn, cmd, registry=self._registry, auth_hook=self._auth_hook
            )

    def advance_pipeline_stage(self, cmd: AdvancePipelineStageCommand) -> PipelineView:
        with self._obs.span("advance_stage"):
            return dispatcher.dispatch(
                self._conn, cmd, registry=self._registry, auth_hook=self._auth_hook
            )

    def fail_pipeline_stage(self, cmd: FailPipelineStageCommand) -> PipelineView:
        with self._obs.span("fail_stage"):
            return dispatcher.dispatch(
                self._conn, cmd, registry=self._registry, auth_hook=self._auth_hook
            )

    def retry_operation(self, cmd: RetryOperationCommand) -> dict[str, Any]:
        with self._obs.span("retry_operation"):
            return dispatcher.dispatch(
                self._conn, cmd, registry=self._registry, auth_hook=self._auth_hook
            )

    def cancel_operation(self, cmd: CancelOperationCommand) -> dict[str, Any]:
        with self._obs.span("cancel_operation"):
            return dispatcher.dispatch(
                self._conn, cmd, registry=self._registry, auth_hook=self._auth_hook
            )

    def approve_review_item(self, cmd: ApproveReviewItemCommand) -> dict[str, Any]:
        with self._obs.span("approve_review"):
            return dispatcher.dispatch(
                self._conn, cmd, registry=self._registry, auth_hook=self._auth_hook
            )

    def reject_review_item(self, cmd: RejectReviewItemCommand) -> dict[str, Any]:
        with self._obs.span("reject_review"):
            return dispatcher.dispatch(
                self._conn, cmd, registry=self._registry, auth_hook=self._auth_hook
            )

    def create_schedule(self, cmd: CreateScheduleCommand) -> ScheduleView:
        with self._obs.span("create_schedule"):
            return dispatcher.dispatch(
                self._conn, cmd, registry=self._registry, auth_hook=self._auth_hook
            )

    def pause_schedule(self, cmd: PauseScheduleCommand) -> ScheduleView:
        with self._obs.span("pause_schedule"):
            return dispatcher.dispatch(
                self._conn, cmd, registry=self._registry, auth_hook=self._auth_hook
            )

    def resume_schedule(self, cmd: ResumeScheduleCommand) -> ScheduleView:
        with self._obs.span("resume_schedule"):
            return dispatcher.dispatch(
                self._conn, cmd, registry=self._registry, auth_hook=self._auth_hook
            )

    def delete_schedule(self, cmd: DeleteScheduleCommand) -> None:
        with self._obs.span("delete_schedule"):
            dispatcher.dispatch(self._conn, cmd, registry=self._registry, auth_hook=self._auth_hook)

    def execute_pipeline_stage(self, cmd: ExecutePipelineStageCommand) -> PipelineView:
        with self._obs.span("execute_stage"):
            return dispatcher.dispatch(
                self._conn, cmd, registry=self._registry, auth_hook=self._auth_hook
            )

    def get_stage_history(self, pipeline_id: str, stage: str, workspace_id: str) -> list[Any]:
        pv = pipeline_state.get_pipeline(self._conn, pipeline_id)
        if pv.workspace_id != workspace_id:
            from app.application.errors import CrossWorkspaceAccessError

            raise CrossWorkspaceAccessError("Pipeline", pipeline_id, workspace_id)
        return pipeline_state.get_stage_history(self._conn, pipeline_id, stage)

    def get_stage_artifact(self, workspace_id: str, pipeline_id: str, stage: str) -> dict[str, Any]:
        """Return safe artifact metadata + content preview for a pipeline stage.

        Validates workspace ownership, resolves artifact content by type from
        the appropriate domain table. Never exposes filesystem paths or secrets.
        """
        from app.application import artifact_resolver

        pv = pipeline_state.get_pipeline(self._conn, pipeline_id)
        if pv.workspace_id != workspace_id:
            from app.application.errors import CrossWorkspaceAccessError

            raise CrossWorkspaceAccessError("Pipeline", pipeline_id, workspace_id)
        return artifact_resolver.resolve_stage_artifact(
            self._conn, pipeline_id=pipeline_id, stage=stage, workspace_id=workspace_id
        )

    def replay_event(self, cmd: ReplayEventCommand) -> dict[str, Any]:
        with self._obs.span("replay_event"):
            return dispatcher.dispatch(
                self._conn, cmd, registry=self._registry, auth_hook=self._auth_hook
            )

    def recover_pipeline(self, cmd: RecoverPipelineCommand) -> PipelineView:
        with self._obs.span("recover_pipeline"):
            return dispatcher.dispatch(
                self._conn, cmd, registry=self._registry, auth_hook=self._auth_hook
            )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_workspace_summary(self, q: GetWorkspaceSummaryQuery) -> WorkspaceView:
        with self._obs.span("get_workspace_summary"):
            ws = cp_repo.get_workspace(self._conn, q.workspace_id)
            channels = cp_repo.list_channels_by_workspace(self._conn, q.workspace_id)
            active = self._conn.execute(
                "SELECT COUNT(*) FROM app_pipeline_executions "
                "WHERE workspace_id=? AND status IN ('running','waiting_for_review')",
                (q.workspace_id,),
            ).fetchone()[0]
            level = cp_services.get_effective_policy(self._conn, q.workspace_id)
            return WorkspaceView(
                id=ws.id,
                name=ws.name,
                slug=ws.slug,
                status=ws.status,
                organization_id=ws.organization_id,
                channel_count=len(channels),
                active_pipeline_count=active,
                paused=ws.status == "paused",
                automation_level=level,
            )

    def get_channel_summary(self, q: GetChannelSummaryQuery) -> ChannelView:
        with self._obs.span("get_channel_summary"):
            ch = cp_repo.get_channel(self._conn, q.channel_id)
            if ch.workspace_id != q.workspace_id:
                from app.application.errors import CrossWorkspaceAccessError

                raise CrossWorkspaceAccessError("Channel", q.channel_id, q.workspace_id)
            accounts = cp_repo.list_platform_accounts_by_channel(self._conn, q.channel_id)
            active = self._conn.execute(
                "SELECT COUNT(*) FROM app_pipeline_executions "
                "WHERE channel_id=? AND status IN ('running','waiting_for_review')",
                (q.channel_id,),
            ).fetchone()[0]
            level = cp_services.get_effective_policy(self._conn, q.workspace_id, q.channel_id)
            return ChannelView(
                id=ch.id,
                workspace_id=ch.workspace_id,
                name=ch.name,
                slug=ch.slug,
                status=ch.status,
                paused=ch.status == "paused",
                account_count=len(accounts),
                automation_level=level,
                active_pipeline_count=active,
            )

    def get_account_summary(self, q: GetAccountSummaryQuery) -> AccountView:
        with self._obs.span("get_account_summary"):
            acc = cp_repo.get_platform_account(self._conn, q.account_id)
            ch = cp_repo.get_channel(self._conn, acc.channel_id)
            if ch.workspace_id != q.workspace_id:
                from app.application.errors import CrossWorkspaceAccessError

                raise CrossWorkspaceAccessError("Account", q.account_id, q.workspace_id)
            level = cp_services.get_effective_policy(
                self._conn, q.workspace_id, acc.channel_id, q.account_id
            )
            return AccountView(
                id=acc.id,
                channel_id=acc.channel_id,
                platform_key=acc.platform_key,
                display_name=acc.display_name,
                status=acc.status,
                external_account_id=acc.external_account_id,
                automation_level=level,
            )

    def get_pipeline_status(self, q: GetPipelineStatusQuery) -> PipelineView:
        with self._obs.span("get_pipeline_status"):
            pv = pipeline_state.get_pipeline(self._conn, q.pipeline_id)
            if pv.workspace_id != q.workspace_id:
                from app.application.errors import CrossWorkspaceAccessError

                raise CrossWorkspaceAccessError("Pipeline", q.pipeline_id, q.workspace_id)
            return pv

    def list_pipelines(self, q: ListPipelinesQuery) -> list[PipelineView]:
        with self._obs.span("list_pipelines"):
            return pipeline_state.list_pipelines(
                self._conn,
                q.workspace_id,
                status=q.status,
                channel_id=q.channel_id,
                limit=q.limit,
            )

    def list_operations(self, q: ListOperationsQuery) -> list[OperationView]:
        with self._obs.span("list_operations"):
            sql = "SELECT * FROM cp_operation_executions WHERE workspace_id=?"
            params: list[Any] = [q.workspace_id]
            if q.status:
                sql += " AND status=?"
                params.append(q.status)
            if q.channel_id:
                sql += " AND channel_id=?"
                params.append(q.channel_id)
            sql += " ORDER BY created_at DESC LIMIT ?"
            params.append(q.limit)
            rows = self._conn.execute(sql, params).fetchall()
            return [_operation_row_to_view(r) for r in rows]

    def get_review_queue(self, q: GetReviewQueueQuery) -> list[ReviewItemView]:
        with self._obs.span("get_review_queue"):
            return review_svc.get_review_queue(self._conn, q.workspace_id)

    def get_exception_queue(self, q: GetExceptionQueueQuery) -> list[ExceptionView]:
        with self._obs.span("get_exception_queue"):
            return exc_svc.get_exception_queue(self._conn, q.workspace_id)

    def get_system_health(self, q: GetSystemHealthQuery) -> HealthView:
        with self._obs.span("get_system_health"):
            return health_svc.get_workspace_health(self._conn, q.workspace_id)

    def get_cost_summary(self, q: GetCostSummaryQuery) -> CostView:
        with self._obs.span("get_cost_summary"):
            budget_result = _safe_check_budget(self._conn, q.workspace_id)
            return CostView(
                workspace_id=q.workspace_id,
                period=q.period,
                total_usd=budget_result.get("total_usd", 0.0),
                channel_breakdown=budget_result.get("channel_breakdown", []),
                budget_policies=budget_result.get("policies", []),
                warning_active=bool(budget_result.get("warnings")),
                block_active=bool(budget_result.get("block_active")),
            )

    def get_diagnostics(self, q: GetDiagnosticsQuery) -> DiagnosticReport:
        with self._obs.span("get_diagnostics"):
            subject = q.subject
            subject_id = q.subject_id
            if subject == "pipeline":
                return diag_svc.explain_pipeline(
                    self._conn, subject_id, q.workspace_id, stage=q.stage
                )
            if subject == "workspace":
                return diag_svc.explain_workspace_health(self._conn, q.workspace_id)
            if subject == "policy":
                return diag_svc.explain_effective_policy(self._conn, q.workspace_id)
            if subject == "budget":
                return diag_svc.explain_budget_status(self._conn, q.workspace_id)
            if subject == "executor":
                return diag_svc.explain_executor_status(
                    self._conn, q.workspace_id, subject_id or ""
                )
            return diag_svc.explain_workspace_health(self._conn, q.workspace_id)

    def list_schedules(self, q: ListSchedulesQuery) -> list[ScheduleView]:
        with self._obs.span("list_schedules"):
            return sched_svc.list_schedules(self._conn, q.workspace_id, is_active=q.is_active)

    def get_audit_timeline(self, q: GetAuditTimelineQuery) -> list[AuditView]:
        with self._obs.span("get_audit_timeline"):
            rows = cp_services.workspace_audit_timeline(self._conn, q.workspace_id, q.limit)
            return [_audit_row_to_view(r) for r in rows]

    def get_effective_config(self, q: GetEffectiveConfigQuery) -> dict[str, Any]:
        with self._obs.span("get_effective_config"):
            return resolve_config(
                self._conn,
                q.workspace_id,
                channel_id=q.channel_id,
                platform_account_id=q.platform_account_id,
            )

    def get_eligible_schedules(self, workspace_id: str) -> list[ScheduleView]:
        return sched_svc.eligible_schedules(self._conn, workspace_id)

    def find_recoverable_pipelines(self, workspace_id: str) -> list[PipelineView]:
        return recovery_svc.find_recoverable_pipelines(self._conn, workspace_id)

    def find_dead_lettered_events(self, workspace_id: str) -> list[dict[str, Any]]:
        return recovery_svc.find_dead_lettered_events(self._conn, workspace_id)

    def observability_summary(self) -> dict[str, Any]:
        return self._obs.summary()


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _operation_row_to_view(row: Any) -> OperationView:
    return OperationView(
        id=row["id"],
        workspace_id=row["workspace_id"],
        channel_id=row["channel_id"],
        platform_account_id=row["platform_account_id"],
        operation_type=row["operation_type"],
        status=row["status"],
        idempotency_key=row["idempotency_key"],
        actor=row["actor"],
        correlation_id=row["correlation_id"],
        error_message=row["error_message"],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _audit_row_to_view(row: dict[str, Any]) -> AuditView:
    # Events use "ts", operations use "created_at".
    timestamp = str(row.get("ts") or row.get("created_at") or "")
    kind = row.get("kind", "")
    event_type = row.get("event_type") or row.get("operation_type") or kind
    return AuditView(
        event_type=event_type,
        actor=row.get("actor", ""),
        timestamp=timestamp,
        correlation_id=row.get("correlation_id"),
        description=event_type,
        metadata={
            k: v
            for k, v in row.items()
            if k
            not in (
                "event_type",
                "operation_type",
                "actor",
                "ts",
                "created_at",
                "correlation_id",
                "kind",
            )
        },
    )


def _safe_check_budget(conn: Any, workspace_id: str) -> dict[str, Any]:
    try:
        result = check_budget(conn, workspace_id)
        return {**result, "block_active": False}
    except Exception:
        return {"ok": False, "block_active": True, "warnings": []}
