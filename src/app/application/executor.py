"""Stage executor protocol, registry, request/result contract, and stage adapters.

Stage execution classification:
  A — Executable now through an existing public orchestrator (local, deterministic)
  B — Requires an AI/TTS provider injected at runtime (executor deferred)
  C — Requires a live external provider (HTTP, YouTube, analytics) (executor deferred)

Stage map:
  research            C  ExternalProviderRequiredExecutor — live HTTP + LLM
  script_generation   B  ProviderRequiredExecutor — LLM provider not in request
  production_plan     A  ProductionPlanExecutor — deterministic from approved script
  narration           B  ProviderRequiredExecutor — TTS provider + artifacts on disk
  captions            B  ProviderRequiredExecutor — requires narration artifacts on disk
  visual_intelligence B  ProviderRequiredExecutor — requires approved upstream artifacts
  rendering           B  ProviderRequiredExecutor — requires all upstream artifacts
  publishing          C  ExternalProviderRequiredExecutor — live platform provider
  analytics           C  ExternalProviderRequiredExecutor — live analytics provider
  learning            A  LearningExecutor — deterministic local; review-gated output

No eval, exec, shell, or arbitrary dynamic imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.application.errors import ExecutorDisabledError, ExecutorNotFoundError

EXECUTOR_CONTRACT_VERSION = "13.0.0"


# ---------------------------------------------------------------------------
# Request / Result contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StageExecutionRequest:
    """Typed input handed to a stage executor."""

    pipeline_execution_id: str
    workspace_id: str
    stage: str
    attempt_number: int
    correlation_id: str
    idempotency_key: str
    actor: str
    channel_id: str | None = None
    platform_account_id: str | None = None
    topic_id: int | None = None
    experiment_id: str | None = None
    causation_event_id: str | None = None
    # Canonical artifact IDs from preceding completed stages.
    # Keys are stage names; values are the artifact_id stored on that stage row.
    prerequisite_artifact_ids: dict[str, str] = field(default_factory=dict)
    # Runtime config values (e.g. "artifacts_path", "voice_profile_id").
    effective_config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StageExecutionResult:
    """Typed output produced by a stage executor."""

    stage: str
    executor_key: str
    executor_version: str
    status: str  # completed | waiting_for_review | failed | blocked
    review_required: bool = False
    artifact_type: str | None = None
    artifact_id: str | None = None
    warnings: tuple[str, ...] = ()
    error_category: str | None = None
    error_message: str | None = None
    emitted_event_ids: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class PipelineStageExecutor(Protocol):
    """Contract every stage executor must satisfy."""

    executor_key: str
    executor_version: str

    def execute(self, conn: Any, req: StageExecutionRequest) -> StageExecutionResult: ...


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


@dataclass
class StageExecutorEntry:
    stage: str
    executor: PipelineStageExecutor
    enabled: bool = True


class StageExecutorRegistry:
    """Registry mapping canonical stage names to their executor instances.

    Duplicate registration fails unless replace=True.
    Unknown or disabled stages fail closed.
    """

    def __init__(self) -> None:
        self._entries: dict[str, StageExecutorEntry] = {}

    def register(
        self,
        stage: str,
        executor: PipelineStageExecutor,
        *,
        enabled: bool = True,
        replace: bool = False,
    ) -> None:
        if stage in self._entries:
            existing = self._entries[stage]
            if replace:
                pass  # explicit replacement — always overwrite
            elif (
                type(existing.executor) is type(executor)
                and existing.executor.executor_version == executor.executor_version
            ):
                # Idempotent: same class + version re-registration is a no-op.
                return
            else:
                raise RuntimeError(
                    f"Executor already registered for stage '{stage}' "
                    f"(existing: {type(existing.executor).__name__} "
                    f"v{existing.executor.executor_version}); "
                    "pass replace=True to override with a different executor"
                )
        self._entries[stage] = StageExecutorEntry(stage=stage, executor=executor, enabled=enabled)

    def get(self, stage: str) -> PipelineStageExecutor:
        entry = self._entries.get(stage)
        if entry is None:
            raise ExecutorNotFoundError(stage)
        if not entry.enabled:
            raise ExecutorDisabledError(stage)
        return entry.executor

    def enable(self, stage: str) -> None:
        if stage not in self._entries:
            raise ExecutorNotFoundError(stage)
        self._entries[stage].enabled = True

    def disable(self, stage: str) -> None:
        if stage not in self._entries:
            raise ExecutorNotFoundError(stage)
        self._entries[stage].enabled = False

    def has(self, stage: str) -> bool:
        return stage in self._entries

    def list_stages(self) -> list[str]:
        return sorted(self._entries.keys())

    def list_entries(self) -> list[StageExecutorEntry]:
        return list(self._entries.values())

    def __len__(self) -> int:
        return len(self._entries)


# Module-level singleton used by the composition root.
_default_executor_registry: StageExecutorRegistry | None = None


def get_default_executor_registry() -> StageExecutorRegistry:
    global _default_executor_registry
    if _default_executor_registry is None:
        _default_executor_registry = StageExecutorRegistry()
    return _default_executor_registry


def reset_default_executor_registry() -> None:
    """For use in tests only."""
    global _default_executor_registry
    _default_executor_registry = StageExecutorRegistry()


# ---------------------------------------------------------------------------
# Deferred executor base classes (for stages not yet locally executable)
# ---------------------------------------------------------------------------


class _DeferredExecutorBase:
    """Common base for blocked executors that return a typed result."""

    executor_key: str
    executor_version: str = EXECUTOR_CONTRACT_VERSION
    _error_category: str
    _error_message: str

    def execute(self, conn: Any, req: StageExecutionRequest) -> StageExecutionResult:
        return StageExecutionResult(
            stage=req.stage,
            executor_key=self.executor_key,
            executor_version=self.executor_version,
            status="blocked",
            error_category=self._error_category,
            error_message=self._error_message,
        )


class ExternalProviderRequiredExecutor(_DeferredExecutorBase):
    """Executor for stages that require a live external provider (class C).

    Returns a blocked result. Phase 15 cloud infrastructure provides the
    real provider integration.
    """

    def __init__(self, stage: str, reason: str) -> None:
        self.executor_key = f"{stage}_external_provider"
        self._error_category = "external_provider_required"
        self._error_message = reason


class ProviderRequiredExecutor(_DeferredExecutorBase):
    """Executor for stages that require an AI/TTS/artifact provider (class B).

    Returns a blocked result. The provider must be injected by the caller via
    effective_config or by a future Phase 14 provider registry.
    """

    def __init__(self, stage: str, reason: str) -> None:
        self.executor_key = f"{stage}_provider_required"
        self._error_category = "provider_required"
        self._error_message = reason


# ---------------------------------------------------------------------------
# Stage A: ProductionPlanExecutor
# ---------------------------------------------------------------------------


class ProductionPlanExecutor:
    """Build a production plan from the topic's active approved generated script.

    Calls the existing public boundary:
      content.repository.get_active_approved_generated_script(conn, topic_id)
      production.renderer.build_production_plan(approved_script)
      production.repository.get_or_create_production_plan(conn, draft)

    Returns waiting_for_review (production_plan is a REVIEW_REQUIRED_STAGES member).
    Idempotent: get_or_create_production_plan uses (script_id, input_hash) uniqueness.
    """

    executor_key = "production_plan_v1"
    executor_version = EXECUTOR_CONTRACT_VERSION

    def execute(self, conn: Any, req: StageExecutionRequest) -> StageExecutionResult:
        topic_id = req.topic_id
        if topic_id is None:
            return StageExecutionResult(
                stage=req.stage,
                executor_key=self.executor_key,
                executor_version=self.executor_version,
                status="blocked",
                error_category="prerequisite_missing",
                error_message="topic_id is required for production_plan execution",
            )

        try:
            from app.content.repository import get_active_approved_generated_script
        except ImportError as exc:
            return StageExecutionResult(
                stage=req.stage,
                executor_key=self.executor_key,
                executor_version=self.executor_version,
                status="blocked",
                error_category="engine_unavailable",
                error_message=f"content engine unavailable: {exc}",
            )

        try:
            approved_script = get_active_approved_generated_script(conn, topic_id)
        except Exception as exc:
            return StageExecutionResult(
                stage=req.stage,
                executor_key=self.executor_key,
                executor_version=self.executor_version,
                status="blocked",
                error_category="prerequisite_missing",
                error_message=f"No approved generated script for topic {topic_id}: {exc}",
            )

        if approved_script is None:
            return StageExecutionResult(
                stage=req.stage,
                executor_key=self.executor_key,
                executor_version=self.executor_version,
                status="blocked",
                error_category="prerequisite_missing",
                error_message=f"No approved generated script for topic {topic_id}",
            )

        try:
            from app.production.renderer import build_production_plan
            from app.production.repository import get_or_create_production_plan

            draft = build_production_plan(approved_script)
            plan, _ = get_or_create_production_plan(conn, draft)
        except Exception as exc:
            return StageExecutionResult(
                stage=req.stage,
                executor_key=self.executor_key,
                executor_version=self.executor_version,
                status="failed",
                error_category="execution_error",
                error_message=str(exc),
            )

        return StageExecutionResult(
            stage=req.stage,
            executor_key=self.executor_key,
            executor_version=self.executor_version,
            status="waiting_for_review",
            review_required=True,
            artifact_type="production_plan",
            artifact_id=str(plan.id),
        )


# ---------------------------------------------------------------------------
# Stage A: LearningExecutor
# ---------------------------------------------------------------------------


class LearningExecutor:
    """Run optimization analysis for a completed publication.

    Calls the existing public boundary:
      learning.orchestrator.analyze_publication(conn, publication_id=..., topic_id=...)

    Requires prerequisite_artifact_ids["analytics"] to hold the publication_id.
    Returns waiting_for_review — recommendations are NEVER applied automatically.
    """

    executor_key = "learning_v1"
    executor_version = EXECUTOR_CONTRACT_VERSION

    def execute(self, conn: Any, req: StageExecutionRequest) -> StageExecutionResult:
        publication_id_str = req.prerequisite_artifact_ids.get("analytics")
        if publication_id_str is None:
            return StageExecutionResult(
                stage=req.stage,
                executor_key=self.executor_key,
                executor_version=self.executor_version,
                status="blocked",
                error_category="prerequisite_missing",
                error_message=(
                    "learning requires prerequisite_artifact_ids['analytics'] "
                    "(publication_id from analytics stage)"
                ),
            )

        topic_id = req.topic_id
        if topic_id is None:
            return StageExecutionResult(
                stage=req.stage,
                executor_key=self.executor_key,
                executor_version=self.executor_version,
                status="blocked",
                error_category="prerequisite_missing",
                error_message="topic_id is required for learning execution",
            )

        try:
            publication_id = int(publication_id_str)
        except ValueError:
            return StageExecutionResult(
                stage=req.stage,
                executor_key=self.executor_key,
                executor_version=self.executor_version,
                status="blocked",
                error_category="prerequisite_invalid",
                error_message=f"Invalid publication_id: {publication_id_str!r}",
            )

        try:
            from app.learning.orchestrator import analyze_publication

            run_id = analyze_publication(conn, publication_id=publication_id, topic_id=topic_id)
        except Exception as exc:
            return StageExecutionResult(
                stage=req.stage,
                executor_key=self.executor_key,
                executor_version=self.executor_version,
                status="failed",
                error_category="execution_error",
                error_message=str(exc),
            )

        return StageExecutionResult(
            stage=req.stage,
            executor_key=self.executor_key,
            executor_version=self.executor_version,
            status="waiting_for_review",
            review_required=True,
            artifact_type="learning_run",
            artifact_id=str(run_id),
        )


# ---------------------------------------------------------------------------
# Default executor registration
# ---------------------------------------------------------------------------


def register_default_executors(registry: StageExecutorRegistry, *, replace: bool = False) -> None:
    """Register the canonical executor for every pipeline stage.

    Called by the composition root at startup.
    Pass replace=True when re-registering (e.g. tests resetting the registry).
    """
    registry.register(
        "research",
        ExternalProviderRequiredExecutor(
            "research",
            "Requires live HTTP source ingestion and LLM claim extraction providers. "
            "Phase 15 cloud infrastructure provides these.",
        ),
        replace=replace,
    )
    registry.register(
        "script_generation",
        ProviderRequiredExecutor(
            "script_generation",
            "Requires an AIProvider (e.g. ClaudeProvider) injected via effective_config. "
            "Phase 14 studio layer supplies the configured provider.",
        ),
        replace=replace,
    )
    registry.register("production_plan", ProductionPlanExecutor(), replace=replace)
    registry.register(
        "narration",
        ProviderRequiredExecutor(
            "narration",
            "Requires a TTSProvider and artifacts_path. "
            "Phase 14 studio layer supplies these at runtime.",
        ),
        replace=replace,
    )
    registry.register(
        "captions",
        ProviderRequiredExecutor(
            "captions",
            "Requires approved narration audio artifacts on disk (artifacts_path). "
            "Phase 14 studio layer supplies the artifacts root.",
        ),
        replace=replace,
    )
    registry.register(
        "visual_intelligence",
        ProviderRequiredExecutor(
            "visual_intelligence",
            "Requires approved narration run and caption run artifacts in the DB. "
            "Phase 14 studio layer ensures these are present before execution.",
        ),
        replace=replace,
    )
    registry.register(
        "rendering",
        ProviderRequiredExecutor(
            "rendering",
            "Requires all upstream approved artifacts plus a render backend (ffmpeg). "
            "Phase 15 rendering infrastructure provides this.",
        ),
        replace=replace,
    )
    registry.register(
        "publishing",
        ExternalProviderRequiredExecutor(
            "publishing",
            "Requires a live publishing provider (e.g. YouTube Data API). "
            "Phase 15 cloud infrastructure provides these.",
        ),
        replace=replace,
    )
    registry.register(
        "analytics",
        ExternalProviderRequiredExecutor(
            "analytics",
            "Requires a live analytics provider. Phase 15 cloud infrastructure provides these.",
        ),
        replace=replace,
    )
    registry.register("learning", LearningExecutor(), replace=replace)
