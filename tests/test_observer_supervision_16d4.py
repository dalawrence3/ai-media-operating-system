"""Phase 16D.4 process supervision tests.

Verifies that the analytics observation daemon is correctly wired into:
  - docker-compose.yml  (production supervision)
  - Makefile            (development startup)
  - scripts/start-observer.sh  (dev launcher script)

Also validates the scheduler module's __main__ entry point and that the
daemon's safety gates are correct in both environments.
"""

from __future__ import annotations

from pathlib import Path

import yaml  # PyYAML is already a dependency via the test suite

ROOT = Path(__file__).parent.parent

# ── Docker Compose ────────────────────────────────────────────────────────────


def _load_compose() -> dict:
    with open(ROOT / "docker-compose.yml") as f:
        return yaml.safe_load(f)


def test_compose_has_scheduler_service() -> None:
    """docker-compose.yml must define a 'scheduler' service."""
    compose = _load_compose()
    assert "scheduler" in compose["services"]


def test_compose_scheduler_command() -> None:
    """Scheduler service must run python -m app.workers.scheduler."""
    compose = _load_compose()
    cmd = compose["services"]["scheduler"]["command"]
    joined = " ".join(cmd) if isinstance(cmd, list) else cmd
    assert "app.workers.scheduler" in joined


def test_compose_scheduler_restart_policy() -> None:
    """Scheduler service must restart unless-stopped (automatic recovery)."""
    compose = _load_compose()
    policy = compose["services"]["scheduler"].get("restart", "")
    assert policy == "unless-stopped"


def test_compose_scheduler_depends_on_migrate() -> None:
    """Scheduler waits for migrate to complete before starting."""
    compose = _load_compose()
    deps = compose["services"]["scheduler"].get("depends_on", {})
    assert "migrate" in deps


def test_compose_scheduler_safety_gates_off() -> None:
    """Scheduler service must hard-code publishing gates to false."""
    compose = _load_compose()
    env = compose["services"]["scheduler"].get("environment", {})
    # Gates must be present and set to the string "false"
    assert str(env.get("ACE_PUBLISHING_LIVE_ENABLED", "")).lower() == "false"
    assert str(env.get("ACE_RELEASE_PUBLIC_ENABLED", "")).lower() == "false"


def test_compose_scheduler_has_youtube_credentials_passthrough() -> None:
    """Scheduler service passes YOUTUBE_CLIENT_SECRETS_PATH from host env."""
    compose = _load_compose()
    env = compose["services"]["scheduler"].get("environment", {})
    assert "YOUTUBE_CLIENT_SECRETS_PATH" in env


# ── Makefile ──────────────────────────────────────────────────────────────────


def _read_makefile() -> str:
    return (ROOT / "Makefile").read_text()


def test_makefile_dev_starts_observer() -> None:
    """make dev must reference start-observer.sh."""
    makefile = _read_makefile()
    assert "start-observer.sh" in makefile


def test_makefile_stop_kills_observer() -> None:
    """make stop must clean up observer.pid."""
    makefile = _read_makefile()
    assert "observer.pid" in makefile


def test_makefile_dev_uses_pid_for_duplicate_protection() -> None:
    """make dev must guard observer start with a PID-alive check."""
    makefile = _read_makefile()
    # The observer.pid guard pattern
    assert "observer.pid" in makefile
    assert "kill -0" in makefile


# ── scripts/start-observer.sh ─────────────────────────────────────────────────


def _read_start_observer() -> str:
    path = ROOT / "scripts" / "start-observer.sh"
    assert path.exists(), "scripts/start-observer.sh does not exist"
    return path.read_text()


def test_start_observer_script_exists() -> None:
    """scripts/start-observer.sh must exist."""
    assert (ROOT / "scripts" / "start-observer.sh").exists()


def test_start_observer_script_is_executable() -> None:
    """scripts/start-observer.sh must be executable."""
    import os

    path = ROOT / "scripts" / "start-observer.sh"
    assert os.access(path, os.X_OK)


def test_start_observer_runs_scheduler_module() -> None:
    """start-observer.sh must invoke app.workers.scheduler."""
    script = _read_start_observer()
    assert "app.workers.scheduler" in script


def test_start_observer_defaults_publishing_gate_off() -> None:
    """start-observer.sh must start with ACE_PUBLISHING_LIVE_ENABLED off.

    Phase 18C relaxed this from a hard-coded false to the default-off form
    so an operator can authorize autonomous publishing via the git-ignored
    .env.local — this script runs the scheduler daemon, which is the
    process that performs the publish. The gate must therefore still be off
    by default and must never be enabled from within the repository.
    """
    script = _read_start_observer()
    assert 'ACE_PUBLISHING_LIVE_ENABLED="${ACE_PUBLISHING_LIVE_ENABLED:-false}"' in script
    assert "ACE_PUBLISHING_LIVE_ENABLED=true" not in script


def test_start_observer_defaults_release_gate_off() -> None:
    """start-observer.sh must start with ACE_RELEASE_PUBLIC_ENABLED off."""
    script = _read_start_observer()
    assert 'ACE_RELEASE_PUBLIC_ENABLED="${ACE_RELEASE_PUBLIC_ENABLED:-false}"' in script
    assert "ACE_RELEASE_PUBLIC_ENABLED=true" not in script


def test_start_observer_loads_env_local() -> None:
    """start-observer.sh must load .env.local for YouTube credentials."""
    script = _read_start_observer()
    assert ".env.local" in script or "ENV_FILE" in script


# ── Scheduler module __main__ entry point ─────────────────────────────────────


def test_scheduler_module_is_runnable_as_main() -> None:
    """python -m app.workers.scheduler must be importable without crashing."""
    src = (ROOT / "src" / "app" / "workers" / "scheduler.py").read_text()
    assert 'if __name__ == "__main__"' in src
    assert "run_scheduler_daemon" in src


def test_scheduler_daemon_does_startup_reconciliation() -> None:
    """run_scheduler_daemon source must call reconcile_unobserved_publications."""
    src = (ROOT / "src" / "app" / "workers" / "scheduler.py").read_text()
    assert "reconcile_unobserved_publications" in src


def test_scheduler_daemon_passes_oauth_client_to_tick() -> None:
    """run_scheduler_daemon must pass oauth_client to run_scheduler_tick."""
    src = (ROOT / "src" / "app" / "workers" / "scheduler.py").read_text()
    assert "oauth_client=oauth_client" in src


def test_run_scheduler_tick_handles_analytics_observation_inline() -> None:
    """run_scheduler_tick must contain the analytics_observation inline branch."""
    src = (ROOT / "src" / "app" / "workers" / "scheduler.py").read_text()
    assert '"analytics_observation"' in src
    assert "run_observation" in src


# ── Safety gate integration: daemon tick preserves False gates ────────────────


def test_daemon_tick_with_mock_observation_preserves_safety_gates(tmp_path) -> None:
    """run_scheduler_tick never touches publishing gates."""
    import json
    from unittest.mock import MagicMock, patch

    from app.core.database import open_db
    from app.workers.scheduler import run_scheduler_tick

    conn = open_db(tmp_path / "db.sqlite")
    _NOW = "2026-01-01T00:00:00"
    # Seed cp_workspaces
    conn.execute(
        "INSERT OR IGNORE INTO cp_workspaces (id, name, slug, actor, created_at, updated_at)"
        " VALUES ('ws-1','W','ws-1','s',?,?)",
        (_NOW, _NOW),
    )
    # Seed an analytics_observation schedule due now
    conn.execute(
        """INSERT INTO app_schedule_definitions
           (id, workspace_id, name, operation_type, schedule_type, schedule_config_json,
            timezone, is_active, next_run_at, actor, created_at, updated_at)
           VALUES ('s1','ws-1','obs','analytics_observation','interval',?,
                   'UTC',1,?,?,?,?)""",
        (
            json.dumps({"publication_id": 3, "interval_seconds": 3600}),
            _NOW,
            "system:auto_observer",
            _NOW,
            _NOW,
        ),
    )
    conn.commit()

    from app.core.config import get_config

    cfg = get_config()

    mock_result = MagicMock()
    mock_result.error = None

    with patch("app.analytics.auto_observer.run_observation", return_value=mock_result):
        run_scheduler_tick(conn)

    # Publishing gates must remain unchanged after the tick
    assert cfg.publishing_live_enabled is False
    assert cfg.release_public_enabled is False
    conn.close()
