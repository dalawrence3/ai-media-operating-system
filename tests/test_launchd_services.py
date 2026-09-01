"""Tests for macOS launchd LaunchAgent configuration (Phase 16D.4 follow-on).

Verifies static properties of:
  - plist templates (config/launchd/*.plist.template)
  - wrapper scripts (scripts/start-backend-service.sh, start-observer.sh)
  - service lifecycle script (scripts/service.sh)
  - Makefile service-* targets

No actual launchctl calls are made.  These tests run safely in any environment,
including CI (macOS or Linux).  They validate configuration correctness without
installing or starting any service.
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
TEMPLATE_DIR = ROOT / "config" / "launchd"
SCRIPTS_DIR = ROOT / "scripts"
MAKEFILE = ROOT / "Makefile"

EXPECTED_LABELS = {
    "com.aicontentengine.backend",
    "com.aicontentengine.observer",
    "com.aicontentengine.frontend",
}

TEMPLATE_FILES = {label: TEMPLATE_DIR / f"{label}.plist.template" for label in EXPECTED_LABELS}

# Values a shell would read as "gate on". Matched case-insensitively because
# app.core.config lowercases before comparing.
_ENABLED_VALUES = ("1", "true", "yes")


def _defaults_off(src: str, gate: str) -> bool:
    """True when `src` exports `gate` with false as its default.

    Accepts both the hard-coded form (`GATE="false"`) and the default-off
    parameter expansion (`GATE="${GATE:-false}"`), since both start the
    process with the gate off.
    """
    return bool(re.search(rf'{gate}="(?:\$\{{{gate}:-)?false\}}?"', src))


def _hardcodes_enabled(src: str, gate: str) -> bool:
    """True when `src` *assigns* `gate` a literal enabled value.

    Anchored to the start of a line (allowing `export `) so that a script
    merely mentioning the gate — doctor.sh prints an error containing
    `ACE_PUBLISHING_LIVE_ENABLED=true` when it detects one — is not
    mistaken for a script that sets it.
    """
    for value in _ENABLED_VALUES:
        direct = rf'^\s*(?:export\s+)?{gate}=["\']?{value}["\']?\s*$'
        defaulted = rf'^\s*(?:export\s+)?{gate}="\$\{{{gate}:-{value}\}}"'
        if re.search(direct, src, re.IGNORECASE | re.MULTILINE):
            return True
        if re.search(defaulted, src, re.IGNORECASE | re.MULTILINE):
            return True
    return False


# ── Template existence ─────────────────────────────────────────────────────────


def test_all_plist_templates_exist():
    for path in TEMPLATE_FILES.values():
        assert path.exists(), f"Missing template: {path}"


# ── Template XML structure ─────────────────────────────────────────────────────


def _parse_plist(label: str) -> ET.Element:
    """Parse a plist template as XML after replacing placeholders with dummy values."""
    raw = TEMPLATE_FILES[label].read_text()
    # Substitute placeholders so the XML parses without errors.
    xml_str = (
        raw.replace("{{REPO_PATH}}", "/repo")
        .replace("{{HOME}}", "/home/user")
        .replace("{{LOG_DIR}}", "/logs")
    )
    # Strip the DOCTYPE line — ElementTree doesn't handle DTDs.
    xml_str = re.sub(r"<!DOCTYPE[^>]+>", "", xml_str)
    return ET.fromstring(xml_str)


def _plist_keys(root: ET.Element) -> dict[str, str]:
    """Return {key: value_text} for top-level <dict> entries."""
    top_dict = root.find("dict")
    assert top_dict is not None
    pairs = {}
    it = iter(top_dict)
    for key_el in it:
        val_el = next(it)
        pairs[key_el.text] = val_el.text or val_el.tag
    return pairs


@pytest.mark.parametrize("label", sorted(EXPECTED_LABELS))
def test_plist_label_matches_filename(label):
    root = _parse_plist(label)
    pairs = _plist_keys(root)
    assert pairs.get("Label") == label


@pytest.mark.parametrize("label", sorted(EXPECTED_LABELS))
def test_plist_run_at_load(label):
    root = _parse_plist(label)
    top_dict = root.find("dict")
    keys = [el.text for el in top_dict if el.tag == "key"]
    assert "RunAtLoad" in keys


@pytest.mark.parametrize("label", sorted(EXPECTED_LABELS))
def test_plist_keep_alive(label):
    root = _parse_plist(label)
    top_dict = root.find("dict")
    keys = [el.text for el in top_dict if el.tag == "key"]
    assert "KeepAlive" in keys


@pytest.mark.parametrize("label", sorted(EXPECTED_LABELS))
def test_plist_working_directory_uses_placeholder(label):
    raw = TEMPLATE_FILES[label].read_text()
    assert "{{REPO_PATH}}" in raw, "WorkingDirectory must use {{REPO_PATH}} placeholder"


@pytest.mark.parametrize("label", sorted(EXPECTED_LABELS))
def test_plist_log_paths_use_log_dir_placeholder(label):
    raw = TEMPLATE_FILES[label].read_text()
    assert "{{LOG_DIR}}" in raw


@pytest.mark.parametrize("label", sorted(EXPECTED_LABELS))
def test_plist_standard_out_and_err_paths_present(label):
    raw = TEMPLATE_FILES[label].read_text()
    assert "StandardOutPath" in raw
    assert "StandardErrorPath" in raw


@pytest.mark.parametrize("label", sorted(EXPECTED_LABELS))
def test_plist_throttle_interval_present(label):
    raw = TEMPLATE_FILES[label].read_text()
    assert "ThrottleInterval" in raw


@pytest.mark.parametrize("label", sorted(EXPECTED_LABELS))
def test_plist_path_env_covers_homebrew(label):
    raw = TEMPLATE_FILES[label].read_text()
    assert "/opt/homebrew/bin" in raw, "PATH must include Homebrew so launchd finds npm/node"


# ── Backend plist: correct script, no --reload ────────────────────────────────


def test_backend_plist_uses_service_script():
    raw = TEMPLATE_FILES["com.aicontentengine.backend"].read_text()
    assert "start-backend-service.sh" in raw, (
        "Backend LaunchAgent must use start-backend-service.sh, not start-backend.sh"
    )


def test_backend_plist_not_dev_script():
    raw = TEMPLATE_FILES["com.aicontentengine.backend"].read_text()
    assert "start-backend.sh" not in raw, (
        "Backend LaunchAgent must NOT use start-backend.sh (dev script with --reload)"
    )


# ── Observer plist: uses existing observer script ─────────────────────────────


def test_observer_plist_uses_observer_script():
    raw = TEMPLATE_FILES["com.aicontentengine.observer"].read_text()
    assert "start-observer.sh" in raw


# ── Frontend plist: uses Vite dev server ──────────────────────────────────────


def test_frontend_plist_uses_frontend_script():
    raw = TEMPLATE_FILES["com.aicontentengine.frontend"].read_text()
    assert "start-frontend.sh" in raw


# ── start-backend-service.sh correctness ──────────────────────────────────────


def _read_script(name: str) -> str:
    path = SCRIPTS_DIR / name
    assert path.exists(), f"Missing script: {path}"
    return path.read_text()


def test_backend_service_script_no_reload():
    src = _read_script("start-backend-service.sh")
    # Strip comment lines before checking — comments may mention --reload to explain
    # its absence; the uvicorn command must not contain it as a live flag.
    non_comment_lines = [line for line in src.splitlines() if not line.lstrip().startswith("#")]
    non_comment_src = "\n".join(non_comment_lines)
    assert "--reload" not in non_comment_src, (
        "start-backend-service.sh must NOT pass --reload to uvicorn (persistent service, not dev)"
    )


def test_backend_service_script_has_uvicorn():
    src = _read_script("start-backend-service.sh")
    assert "uvicorn" in src
    assert "app.api.main:app" in src


def test_backend_service_script_correct_host_port():
    src = _read_script("start-backend-service.sh")
    assert "127.0.0.1" in src
    assert "8000" in src


def test_backend_service_script_loads_env_local():
    src = _read_script("start-backend-service.sh")
    assert ".env.local" in src


def test_backend_service_script_safety_gates_default_off():
    """Both publishing gates must default to false and never be enabled in source.

    Phase 18C changed these from a hard-coded `="false"` to the default-off
    form `="${VAR:-false}"` so an operator can authorize autonomous
    publishing through the git-ignored .env.local. The invariant that
    actually protects us is therefore not the literal string — it is that
    the script starts with the gate off unless something outside the
    repository says otherwise, and that no enabled value is ever committed.
    This asserts exactly that, for both gates.
    """
    src = _read_script("start-backend-service.sh")
    for gate in ("ACE_PUBLISHING_LIVE_ENABLED", "ACE_RELEASE_PUBLIC_ENABLED"):
        assert _defaults_off(src, gate), f"{gate} must default to false"
        assert not _hardcodes_enabled(src, gate), f"{gate} must never be enabled in source"


def test_backend_service_script_is_executable():
    path = SCRIPTS_DIR / "start-backend-service.sh"
    assert os.access(path, os.X_OK)


# ── start-observer.sh safety gates ────────────────────────────────────────────


def test_observer_script_publishing_gate_defaults_off():
    src = _read_script("start-observer.sh")
    assert _defaults_off(src, "ACE_PUBLISHING_LIVE_ENABLED")
    assert not _hardcodes_enabled(src, "ACE_PUBLISHING_LIVE_ENABLED")


def test_observer_script_release_gate_defaults_off():
    src = _read_script("start-observer.sh")
    assert _defaults_off(src, "ACE_RELEASE_PUBLIC_ENABLED")
    assert not _hardcodes_enabled(src, "ACE_RELEASE_PUBLIC_ENABLED")


def test_no_service_script_ever_enables_a_publishing_gate():
    """No committed script may turn a publishing gate on.

    The strongest form of the guarantee: whatever a script does with these
    variables, an enabled value must never reach the repository. Covers
    every wrapper script at once so a new one cannot slip past.
    """
    for script in SCRIPTS_DIR.glob("*.sh"):
        src = script.read_text()
        for gate in ("ACE_PUBLISHING_LIVE_ENABLED", "ACE_RELEASE_PUBLIC_ENABLED"):
            assert not _hardcodes_enabled(src, gate), (
                f"{script.name} hard-codes {gate} to an enabled value"
            )


def test_observer_script_uses_scheduler_entrypoint():
    src = _read_script("start-observer.sh")
    assert "app.workers.scheduler" in src


# ── service.sh lifecycle manager ──────────────────────────────────────────────


def test_service_sh_exists_and_is_executable():
    path = SCRIPTS_DIR / "service.sh"
    assert path.exists()
    assert os.access(path, os.X_OK)


def test_service_sh_has_all_expected_labels():
    src = _read_script("service.sh")
    for label in EXPECTED_LABELS:
        assert label in src, f"service.sh must reference label: {label}"


def test_service_sh_uses_launchctl_bootstrap():
    src = _read_script("service.sh")
    assert "launchctl bootstrap" in src


def test_service_sh_uses_launchctl_bootout():
    src = _read_script("service.sh")
    assert "launchctl bootout" in src


def test_service_sh_creates_log_dir():
    src = _read_script("service.sh")
    assert "mkdir -p" in src
    assert "LOG_DIR" in src


def test_service_sh_substitutes_repo_placeholder():
    src = _read_script("service.sh")
    assert "REPO_PATH" in src or "{{REPO_PATH}}" in src or "REPO" in src


def test_service_sh_stops_dev_processes_on_install():
    src = _read_script("service.sh")
    assert "make" in src and "stop" in src, (
        "service-install must call 'make stop' to prevent port conflicts with 'make dev'"
    )


def test_service_sh_has_install_stop_restart_status_uninstall():
    src = _read_script("service.sh")
    for cmd in ("install", "stop", "restart", "status", "uninstall"):
        assert cmd in src, f"service.sh must handle command: {cmd}"


def test_service_sh_idempotent_bootout_before_bootstrap():
    src = _read_script("service.sh")
    # bootout must appear before bootstrap so reinstall is idempotent.
    bootout_pos = src.find("launchctl bootout")
    bootstrap_pos = src.find("launchctl bootstrap")
    assert bootout_pos != -1 and bootstrap_pos != -1
    assert bootout_pos < bootstrap_pos, (
        "bootout must appear before bootstrap to ensure idempotent install"
    )


def test_service_sh_uses_gui_domain():
    src = _read_script("service.sh")
    assert "gui/" in src, "Must use GUI user domain (gui/<uid>) not system domain"


# ── Template placeholder substitution integrity ───────────────────────────────


def test_templates_contain_no_hardcoded_username():
    """Plist templates must not embed any hardcoded user home path."""
    username = os.environ.get("USER", "")
    for label, path in TEMPLATE_FILES.items():
        raw = path.read_text()
        # Should not contain e.g. /Users/dominiclawrence — only {{HOME}} placeholder.
        if username:
            assert f"/Users/{username}" not in raw, (
                f"{label} template must use {{{{HOME}}}} placeholder, "
                f"not the hardcoded path /Users/{username}"
            )


def test_template_placeholders_are_substituted_in_service_sh():
    """service.sh must actually perform substitution for all three placeholders."""
    src = _read_script("service.sh")
    for placeholder in ("REPO_PATH", "HOME", "LOG_DIR"):
        assert placeholder in src, f"service.sh must substitute {{{{{placeholder}}}}}"


# ── Makefile service-* targets ────────────────────────────────────────────────


def _read_makefile() -> str:
    return MAKEFILE.read_text()


def test_makefile_has_service_install_target():
    assert "service-install" in _read_makefile()


def test_makefile_has_service_status_target():
    assert "service-status" in _read_makefile()


def test_makefile_has_service_restart_target():
    assert "service-restart" in _read_makefile()


def test_makefile_has_service_stop_target():
    assert "service-stop" in _read_makefile()


def test_makefile_has_service_uninstall_target():
    assert "service-uninstall" in _read_makefile()


def test_makefile_service_targets_call_service_sh():
    makefile = _read_makefile()
    assert "service.sh" in makefile


def test_makefile_documents_port_conflict_warning():
    makefile = _read_makefile()
    assert "8000" in makefile and "5173" in makefile


# ── Log path consistency ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "label,expected_stem",
    [
        ("com.aicontentengine.backend", "backend"),
        ("com.aicontentengine.observer", "observer"),
        ("com.aicontentengine.frontend", "frontend"),
    ],
)
def test_log_path_uses_correct_stem(label, expected_stem):
    raw = TEMPLATE_FILES[label].read_text()
    assert f"{expected_stem}.log" in raw
    assert f"{expected_stem}.err.log" in raw
