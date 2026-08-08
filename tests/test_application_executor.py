"""Tests for the stage executor registry, protocol, and stage adapters."""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

import pytest

from app.application.commands import ExecutePipelineStageCommand, StartPipelineCommand
from app.application.errors import ExecutorDisabledError, ExecutorNotFoundError
from app.application.executor import (
    EXECUTOR_CONTRACT_VERSION,
    ExternalProviderRequiredExecutor,
    LearningExecutor,
    PipelineStageExecutor,
    ProductionPlanExecutor,
    ProviderRequiredExecutor,
    StageExecutionRequest,
    StageExecutionResult,
    StageExecutorRegistry,
    register_default_executors,
    reset_default_executor_registry,
)
from app.control_plane import repository as cp_repo
from app.control_plane.models import ChannelDraft, OrganizationDraft, WorkspaceDraft
from app.core.database import open_db


def _uid() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def conn():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = Path(f.name)
    c = open_db(path)
    yield c
    c.close()
    path.unlink(missing_ok=True)


@pytest.fixture
def workspace(conn):
    org = cp_repo.create_organization(
        conn, OrganizationDraft(id=_uid(), name="Org", slug="org", actor="cli")
    )
    return cp_repo.create_workspace(
        conn,
        WorkspaceDraft(id=_uid(), name="WS", slug="ws", actor="cli", organization_id=org.id),
    )


@pytest.fixture
def channel(conn, workspace):
    return cp_repo.create_channel(
        conn,
        ChannelDraft(id=_uid(), workspace_id=workspace.id, name="Chan", slug="chan", actor="cli"),
    )


@pytest.fixture(autouse=True)
def _reset_state():
    from app.application import dispatcher as bus
    reset_default_executor_registry()
    bus._HANDLERS.clear()
    yield
    reset_default_executor_registry()
    bus._HANDLERS.clear()


def _make_request(stage="research", *, topic_id=None, prereqs=None, config=None):
    return StageExecutionRequest(
        pipeline_execution_id=_uid(),
        workspace_id=_uid(),
        stage=stage,
        attempt_number=1,
        correlation_id=_uid(),
        idempotency_key=_uid(),
        actor="system:test",
        topic_id=topic_id,
        prerequisite_artifact_ids=prereqs or {},
        effective_config=config or {},
    )


class TestStageExecutorRegistry:
    def test_register_and_get(self):
        reg = StageExecutorRegistry()
        ex = ExternalProviderRequiredExecutor("research", "test reason")
        reg.register("research", ex)
        assert reg.get("research") is ex

    def test_get_unknown_stage_raises(self):
        reg = StageExecutorRegistry()
        with pytest.raises(ExecutorNotFoundError):
            reg.get("nonexistent")

    def test_same_class_same_version_is_idempotent(self):
        reg = StageExecutorRegistry()
        ex1 = ExternalProviderRequiredExecutor("research", "r1")
        ex2 = ExternalProviderRequiredExecutor("research", "r2")
        reg.register("research", ex1)
        # Same class + same version → no-op; original executor retained.
        reg.register("research", ex2)
        assert reg.get("research") is ex1

    def test_different_class_raises_without_replace(self):
        reg = StageExecutorRegistry()
        reg.register("research", ExternalProviderRequiredExecutor("research", "r"))
        with pytest.raises(RuntimeError, match="replace=True"):
            reg.register("research", LearningExecutor())

    def test_explicit_replace_works(self):
        reg = StageExecutorRegistry()
        ex1 = ExternalProviderRequiredExecutor("research", "r1")
        ex2 = ExternalProviderRequiredExecutor("research", "r2")
        reg.register("research", ex1)
        reg.register("research", ex2, replace=True)
        assert reg.get("research") is ex2

    def test_disabled_executor_raises(self):
        reg = StageExecutorRegistry()
        ex = ExternalProviderRequiredExecutor("research", "r")
        reg.register("research", ex)
        reg.disable("research")
        with pytest.raises(ExecutorDisabledError):
            reg.get("research")

    def test_enable_after_disable(self):
        reg = StageExecutorRegistry()
        ex = ExternalProviderRequiredExecutor("research", "r")
        reg.register("research", ex)
        reg.disable("research")
        reg.enable("research")
        assert reg.get("research") is ex

    def test_has(self):
        reg = StageExecutorRegistry()
        assert not reg.has("research")
        reg.register("research", ExternalProviderRequiredExecutor("research", "r"))
        assert reg.has("research")

    def test_list_stages(self):
        reg = StageExecutorRegistry()
        reg.register("research", ExternalProviderRequiredExecutor("research", "r"))
        reg.register("learning", LearningExecutor())
        assert reg.list_stages() == ["learning", "research"]

    def test_len(self):
        reg = StageExecutorRegistry()
        assert len(reg) == 0
        reg.register("research", ExternalProviderRequiredExecutor("research", "r"))
        assert len(reg) == 1


class TestDefaultExecutorRegistration:
    def test_all_canonical_stages_registered(self):
        from app.application.commands import PIPELINE_STAGES
        reg = StageExecutorRegistry()
        register_default_executors(reg)
        for stage in PIPELINE_STAGES:
            assert reg.has(stage), f"Stage '{stage}' not registered"

    def test_register_twice_with_replace_is_deterministic(self):
        reg = StageExecutorRegistry()
        register_default_executors(reg)
        count = len(reg)
        register_default_executors(reg, replace=True)
        assert len(reg) == count

    def test_register_twice_same_executors_is_idempotent(self):
        reg = StageExecutorRegistry()
        register_default_executors(reg)
        count = len(reg)
        # Registering same canonical executors again is a no-op (same class+version).
        register_default_executors(reg, replace=False)
        assert len(reg) == count


class TestExecutorProtocol:
    def test_deferred_executor_implements_protocol(self):
        ex = ExternalProviderRequiredExecutor("research", "r")
        assert isinstance(ex, PipelineStageExecutor)

    def test_provider_required_executor_implements_protocol(self):
        ex = ProviderRequiredExecutor("narration", "n")
        assert isinstance(ex, PipelineStageExecutor)

    def test_production_plan_executor_implements_protocol(self):
        assert isinstance(ProductionPlanExecutor(), PipelineStageExecutor)

    def test_learning_executor_implements_protocol(self):
        assert isinstance(LearningExecutor(), PipelineStageExecutor)


class TestDeferredExecutors:
    def test_external_provider_returns_blocked(self):
        ex = ExternalProviderRequiredExecutor("research", "Needs live HTTP")
        result = ex.execute(None, _make_request("research"))
        assert result.status == "blocked"
        assert result.error_category == "external_provider_required"
        assert "Needs live HTTP" in (result.error_message or "")

    def test_provider_required_returns_blocked(self):
        ex = ProviderRequiredExecutor("script_generation", "Needs LLM")
        result = ex.execute(None, _make_request("script_generation"))
        assert result.status == "blocked"
        assert result.error_category == "provider_required"

    def test_deferred_result_has_no_artifact(self):
        ex = ExternalProviderRequiredExecutor("publishing", "reason")
        result = ex.execute(None, _make_request("publishing"))
        assert result.artifact_id is None
        assert result.artifact_type is None
        assert not result.review_required

    def test_executor_key_includes_stage(self):
        ex = ExternalProviderRequiredExecutor("analytics", "r")
        assert "analytics" in ex.executor_key

    def test_executor_version_is_contract_version(self):
        ex = ProviderRequiredExecutor("narration", "r")
        assert ex.executor_version == EXECUTOR_CONTRACT_VERSION


class TestProductionPlanExecutor:
    def test_returns_blocked_when_no_topic_id(self, conn):
        ex = ProductionPlanExecutor()
        req = _make_request("production_plan", topic_id=None)
        result = ex.execute(conn, req)
        assert result.status == "blocked"
        assert result.error_category == "prerequisite_missing"
        assert "topic_id" in (result.error_message or "")

    def test_returns_blocked_when_no_approved_script(self, conn):
        ex = ProductionPlanExecutor()
        req = _make_request("production_plan", topic_id=9999)
        result = ex.execute(conn, req)
        assert result.status == "blocked"
        assert result.error_category == "prerequisite_missing"

    def test_executor_key(self):
        assert ProductionPlanExecutor.executor_key == "production_plan_v1"

    def test_result_is_typed_model(self, conn):
        ex = ProductionPlanExecutor()
        result = ex.execute(conn, _make_request("production_plan", topic_id=None))
        assert isinstance(result, StageExecutionResult)


class TestLearningExecutor:
    def test_returns_blocked_when_no_analytics_prereq(self, conn):
        ex = LearningExecutor()
        req = _make_request("learning", topic_id=1)
        result = ex.execute(conn, req)
        assert result.status == "blocked"
        assert result.error_category == "prerequisite_missing"
        assert "analytics" in (result.error_message or "")

    def test_returns_blocked_when_no_topic_id(self, conn):
        ex = LearningExecutor()
        req = _make_request("learning", topic_id=None, prereqs={"analytics": "42"})
        result = ex.execute(conn, req)
        assert result.status == "blocked"
        assert result.error_category == "prerequisite_missing"
        assert "topic_id" in (result.error_message or "")

    def test_returns_blocked_when_invalid_publication_id(self, conn):
        ex = LearningExecutor()
        req = _make_request("learning", topic_id=1, prereqs={"analytics": "not-an-int"})
        result = ex.execute(conn, req)
        assert result.status == "blocked"
        assert result.error_category == "prerequisite_invalid"

    def test_executor_key(self):
        assert LearningExecutor.executor_key == "learning_v1"


class TestExecutePipelineStage:
    """Integration tests for execute_pipeline_stage via pipeline controller."""

    def test_execute_deferred_stage_returns_blocked_view(self, conn, workspace, channel):
        from app.application import pipeline as pipeline_ctrl
        from app.application.executor import StageExecutorRegistry

        pv = pipeline_ctrl.start_pipeline(
            conn,
            StartPipelineCommand(
                workspace_id=workspace.id,
                channel_id=channel.id,
                actor="system:pipeline",
                idempotency_key=_uid(),
                start_stage="research",
                end_stage="research",
            ),
        )
        reg = StageExecutorRegistry()
        reg.register("research", ExternalProviderRequiredExecutor("research", "external"))
        cmd = ExecutePipelineStageCommand(
            pipeline_id=pv.id,
            workspace_id=workspace.id,
            stage="research",
            actor="system:pipeline",
        )
        result = pipeline_ctrl.execute_pipeline_stage(conn, cmd, executor_registry=reg)
        assert result.status == "blocked"

    def test_execute_completed_stage_via_real_executor(self, conn, workspace, channel):
        """A trivially-completing executor advances the stage to 'completed'."""
        from app.application import pipeline as pipeline_ctrl
        from app.application.executor import StageExecutorRegistry

        class _AlwaysCompleteExecutor:
            executor_key = "test_complete"
            executor_version = "1.0"

            def execute(self, conn, req):
                return StageExecutionResult(
                    stage=req.stage,
                    executor_key=self.executor_key,
                    executor_version=self.executor_version,
                    status="completed",
                    artifact_type="test_artifact",
                    artifact_id="art-123",
                )

        pv = pipeline_ctrl.start_pipeline(
            conn,
            StartPipelineCommand(
                workspace_id=workspace.id,
                channel_id=channel.id,
                actor="system:pipeline",
                idempotency_key=_uid(),
                start_stage="research",
                end_stage="research",
            ),
        )
        reg = StageExecutorRegistry()
        reg.register("research", _AlwaysCompleteExecutor())
        cmd = ExecutePipelineStageCommand(
            pipeline_id=pv.id,
            workspace_id=workspace.id,
            stage="research",
            actor="system:pipeline",
        )
        result = pipeline_ctrl.execute_pipeline_stage(conn, cmd, executor_registry=reg)
        assert result.status == "completed"
        research = next(s for s in result.stages if s.stage == "research")
        assert research.status == "completed"
        assert research.artifact_id == "art-123"

    def test_execute_review_gated_stage_parks_at_waiting(self, conn, workspace, channel):
        from app.application import pipeline as pipeline_ctrl
        from app.application.executor import StageExecutorRegistry

        class _ReviewExecutor:
            executor_key = "test_review"
            executor_version = "1.0"

            def execute(self, conn, req):
                return StageExecutionResult(
                    stage=req.stage,
                    executor_key=self.executor_key,
                    executor_version=self.executor_version,
                    status="waiting_for_review",
                    review_required=True,
                    artifact_type="draft",
                    artifact_id="draft-1",
                )

        pv = pipeline_ctrl.start_pipeline(
            conn,
            StartPipelineCommand(
                workspace_id=workspace.id,
                channel_id=channel.id,
                actor="system:pipeline",
                idempotency_key=_uid(),
                start_stage="research",
                end_stage="research",
            ),
        )
        reg = StageExecutorRegistry()
        reg.register("research", _ReviewExecutor())
        cmd = ExecutePipelineStageCommand(
            pipeline_id=pv.id,
            workspace_id=workspace.id,
            stage="research",
            actor="system:pipeline",
        )
        result = pipeline_ctrl.execute_pipeline_stage(conn, cmd, executor_registry=reg)
        assert result.status == "waiting_for_review"

    def test_executor_unknown_stage_raises(self, conn, workspace, channel):
        from app.application import pipeline as pipeline_ctrl
        from app.application.executor import StageExecutorRegistry

        pv = pipeline_ctrl.start_pipeline(
            conn,
            StartPipelineCommand(
                workspace_id=workspace.id,
                channel_id=channel.id,
                actor="system:pipeline",
                idempotency_key=_uid(),
                start_stage="research",
                end_stage="research",
            ),
        )
        empty_reg = StageExecutorRegistry()
        cmd = ExecutePipelineStageCommand(
            pipeline_id=pv.id,
            workspace_id=workspace.id,
            stage="research",
            actor="system:pipeline",
        )
        with pytest.raises(ExecutorNotFoundError):
            pipeline_ctrl.execute_pipeline_stage(conn, cmd, executor_registry=empty_reg)

    def test_executor_disabled_stage_raises(self, conn, workspace, channel):
        from app.application import pipeline as pipeline_ctrl
        from app.application.executor import StageExecutorRegistry

        pv = pipeline_ctrl.start_pipeline(
            conn,
            StartPipelineCommand(
                workspace_id=workspace.id,
                channel_id=channel.id,
                actor="system:pipeline",
                idempotency_key=_uid(),
                start_stage="research",
                end_stage="research",
            ),
        )
        reg = StageExecutorRegistry()
        reg.register("research", ExternalProviderRequiredExecutor("research", "ext"))
        reg.disable("research")
        cmd = ExecutePipelineStageCommand(
            pipeline_id=pv.id,
            workspace_id=workspace.id,
            stage="research",
            actor="system:pipeline",
        )
        with pytest.raises(ExecutorDisabledError):
            pipeline_ctrl.execute_pipeline_stage(conn, cmd, executor_registry=reg)

    def test_idempotency_key_in_request(self, conn, workspace, channel):
        """Executor receives idempotency_key encoding pipeline:stage:attempt."""
        from app.application import pipeline as pipeline_ctrl
        from app.application.executor import StageExecutorRegistry

        captured: list[StageExecutionRequest] = []

        class _CapturingExecutor:
            executor_key = "capturing"
            executor_version = "1.0"

            def execute(self, conn, req):
                captured.append(req)
                return StageExecutionResult(
                    stage=req.stage, executor_key=self.executor_key,
                    executor_version=self.executor_version, status="blocked",
                    error_category="test", error_message="capturing only",
                )

        pv = pipeline_ctrl.start_pipeline(
            conn,
            StartPipelineCommand(
                workspace_id=workspace.id,
                channel_id=channel.id,
                actor="system:pipeline",
                idempotency_key=_uid(),
                start_stage="research",
                end_stage="research",
            ),
        )
        reg = StageExecutorRegistry()
        reg.register("research", _CapturingExecutor())
        cmd = ExecutePipelineStageCommand(
            pipeline_id=pv.id,
            workspace_id=workspace.id,
            stage="research",
            actor="system:pipeline",
        )
        pipeline_ctrl.execute_pipeline_stage(conn, cmd, executor_registry=reg)
        assert len(captured) == 1
        assert captured[0].pipeline_execution_id == pv.id
        assert "research" in captured[0].idempotency_key
        assert str(captured[0].attempt_number) in captured[0].idempotency_key


class TestSideEffectOrdering:
    """Verify checks fire in correct order — auth/pause before executor invocation."""

    def test_workspace_paused_blocks_execute_stage(self, conn, workspace, channel):
        """Workspace pause check must fire before executor is invoked."""
        from app.application import pipeline as pipeline_ctrl
        from app.application.errors import WorkspacePausedError

        pv = pipeline_ctrl.start_pipeline(
            conn,
            StartPipelineCommand(
                workspace_id=workspace.id,
                channel_id=channel.id,
                actor="system:pipeline",
                idempotency_key=_uid(),
                start_stage="research",
                end_stage="research",
            ),
        )
        # Suspend the workspace AFTER pipeline creation (non-active → WorkspacePausedError).
        cp_repo.update_workspace_status(conn, workspace.id, "suspended", actor="system:test")

        call_count = 0

        class _SpyExecutor:
            executor_key = "spy"
            executor_version = "1.0"

            def execute(self, conn, req):
                nonlocal call_count
                call_count += 1
                return StageExecutionResult(
                    stage=req.stage, executor_key=self.executor_key,
                    executor_version=self.executor_version, status="completed",
                )

        reg = StageExecutorRegistry()
        reg.register("research", _SpyExecutor())
        cmd = ExecutePipelineStageCommand(
            pipeline_id=pv.id,
            workspace_id=workspace.id,
            stage="research",
            actor="system:pipeline",
        )
        from app.application import dispatcher as bus
        from app.application.authorization import allow_all_auth_hook
        from app.application.registry import ExtensionRegistry

        bus.register_default_handlers()
        with pytest.raises(WorkspacePausedError):
            bus.dispatch(
                conn, cmd, registry=ExtensionRegistry(), auth_hook=allow_all_auth_hook
            )
        # Executor must never have been called.
        assert call_count == 0

    def test_denied_auth_executor_never_invoked(self, conn, workspace, channel):
        """Authorization denial must prevent executor invocation entirely."""
        from app.application import dispatcher as bus
        from app.application.errors import AuthorizationRequiredError
        from app.application.registry import ExtensionRegistry

        call_count = 0

        class _SpyExecutor:
            executor_key = "spy_auth"
            executor_version = "1.0"

            def execute(self, conn, req):
                nonlocal call_count
                call_count += 1
                return StageExecutionResult(
                    stage=req.stage, executor_key=self.executor_key,
                    executor_version=self.executor_version, status="completed",
                )

        pv_setup = pipeline_ctrl_factory(conn, workspace, channel)

        reg = StageExecutorRegistry()
        reg.register("research", _SpyExecutor())
        bus.register_default_handlers()
        cmd = ExecutePipelineStageCommand(
            pipeline_id=pv_setup.id,
            workspace_id=workspace.id,
            stage="research",
            actor="user-alice",  # non-system actor, no hook injected
        )
        with pytest.raises(AuthorizationRequiredError):
            bus.dispatch(conn, cmd, registry=ExtensionRegistry())
        assert call_count == 0


def pipeline_ctrl_factory(conn, workspace, channel):
    """Helper: start a single-stage research pipeline for test use."""
    from app.application import pipeline as pipeline_ctrl
    return pipeline_ctrl.start_pipeline(
        conn,
        StartPipelineCommand(
            workspace_id=workspace.id,
            channel_id=channel.id,
            actor="system:pipeline",
            idempotency_key=_uid(),
            start_stage="research",
            end_stage="research",
        ),
    )


class TestExecutorDiagnostics:
    """Verify explain_executor_status structured diagnostics."""

    def test_unknown_stage_reports_executor_unavailable(self, conn, workspace):
        from app.application.diagnostics import explain_executor_status
        reg = StageExecutorRegistry()
        report = explain_executor_status(conn, workspace.id, "nonexistent", executor_registry=reg)
        assert report.status == "error"
        cats = [f.category for f in report.findings]
        assert "executor_unavailable" in cats

    def test_disabled_stage_reports_executor_disabled(self, conn, workspace):
        from app.application.diagnostics import explain_executor_status
        reg = StageExecutorRegistry()
        reg.register("research", ExternalProviderRequiredExecutor("research", "ext"))
        reg.disable("research")
        report = explain_executor_status(conn, workspace.id, "research", executor_registry=reg)
        assert report.status == "blocked"
        cats = [f.category for f in report.findings]
        assert "executor_disabled" in cats

    def test_external_provider_stage_reports_blocked(self, conn, workspace):
        from app.application.diagnostics import explain_executor_status
        reg = StageExecutorRegistry()
        reg.register("research", ExternalProviderRequiredExecutor("research", "r"))
        report = explain_executor_status(conn, workspace.id, "research", executor_registry=reg)
        assert report.status == "blocked"
        cats = [f.category for f in report.findings]
        assert "external_provider_required" in cats

    def test_provider_required_stage_reports_blocked(self, conn, workspace):
        from app.application.diagnostics import explain_executor_status
        reg = StageExecutorRegistry()
        reg.register("narration", ProviderRequiredExecutor("narration", "n"))
        report = explain_executor_status(conn, workspace.id, "narration", executor_registry=reg)
        assert report.status == "blocked"
        cats = [f.category for f in report.findings]
        assert "provider_required" in cats

    def test_executable_stage_reports_ok(self, conn, workspace):
        from app.application.diagnostics import explain_executor_status
        reg = StageExecutorRegistry()
        reg.register("production_plan", ProductionPlanExecutor())
        report = explain_executor_status(
            conn, workspace.id, "production_plan", executor_registry=reg
        )
        assert report.status == "ok"
        cats = [f.category for f in report.findings]
        assert "executor_ok" in cats
