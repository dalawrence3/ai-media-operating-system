"""Composition root — assembles all application-layer dependencies.

One call to build_application_service() wires up:
- the registered command dispatcher
- the stage executor registry
- the extension registry
- observability context
- the authorization hook
- and returns a ready ApplicationService

Call this once per process (or once per request if the conn is request-scoped).
"""

from __future__ import annotations

from typing import Any

from app.application.authorization import AuthHook
from app.application.dispatcher import register_default_handlers
from app.application.executor import get_default_executor_registry, register_default_executors
from app.application.observability import ObservabilityContext
from app.application.registry import ExtensionRegistry
from app.application.services import ApplicationService

_handlers_registered = False
_executors_registered = False


def build_application_service(
    conn: Any,
    *,
    registry: ExtensionRegistry | None = None,
    obs_ctx: ObservabilityContext | None = None,
    auth_hook: AuthHook | None = None,
    force_register: bool = False,
) -> ApplicationService:
    """Build and return an ApplicationService with all defaults registered.

    Args:
        conn: An open sqlite3.Connection (row_factory=sqlite3.Row, FK=ON).
        registry: Optional pre-populated ExtensionRegistry.
        obs_ctx: Optional ObservabilityContext for timing spans.
        auth_hook: Optional authorization hook; defaults to fail-closed for
            non-system actors.  Pass allow_all_auth_hook for dev/test.
        force_register: Re-register handlers/executors even if already done.
    """
    global _handlers_registered, _executors_registered

    if not _handlers_registered or force_register:
        register_default_handlers()
        _handlers_registered = True

    if not _executors_registered or force_register:
        exec_registry = get_default_executor_registry()
        # replace=False: same-class/version re-registration is idempotent (no-op);
        # a genuinely different executor already registered for a stage raises —
        # Phase 14 must use replace=True explicitly to install a different executor.
        register_default_executors(exec_registry, replace=False)
        _executors_registered = True

    if registry is None:
        registry = ExtensionRegistry()

    if obs_ctx is None:
        obs_ctx = ObservabilityContext()

    return ApplicationService(
        conn, registry=registry, obs_ctx=obs_ctx, auth_hook=auth_hook
    )


def reset_composition() -> None:
    """For testing: force handler and executor re-registration on the next build call."""
    global _handlers_registered, _executors_registered
    _handlers_registered = False
    _executors_registered = False
    from app.application.dispatcher import clear_handlers
    from app.application.executor import reset_default_executor_registry

    clear_handlers()
    reset_default_executor_registry()
