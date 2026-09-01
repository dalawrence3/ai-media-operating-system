#!/usr/bin/env bash
# AI Content Engine — launchd service lifecycle manager.
#
# Usage: scripts/service.sh <install|stop|restart|status|uninstall>
#
# install    — generate plists from templates, load all three LaunchAgents.
#              Stops any 'make dev' PID-managed processes first.
# stop       — unload all LaunchAgents (plists remain installed).
# restart    — unload then reload from installed plists.
# status     — show launchd state + HTTP health checks.
# uninstall  — unload and remove all plist files from ~/Library/LaunchAgents/.
#
# Services managed:
#   com.aicontentengine.backend   — FastAPI on :8000
#   com.aicontentengine.observer  — analytics scheduler daemon
#   com.aicontentengine.frontend  — Vite dev server on :5173
#
# Plists are generated from templates in config/launchd/*.plist.template and
# installed to ~/Library/LaunchAgents/. Each plist uses RunAtLoad + KeepAlive
# so services start at login and restart automatically after crashes.
#
# Sleep / power semantics:
#   Services run while the macOS user session is active. launchd restarts them
#   after login or crash. They pause during system sleep and cannot provide
#   true 24/7 operation while the Mac is powered off.
#
# Conflict with 'make dev':
#   'make service-install' calls 'make stop' to clean up PID-managed dev
#   processes. Do NOT run 'make dev' while launchd services are active —
#   both would bind to the same ports (8000 and 5173).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "${SCRIPT_DIR}/.." && pwd)"
UID_VAL=$(id -u)
DOMAIN="gui/${UID_VAL}"
LOG_DIR="${HOME}/.local/share/ai-content-engine/logs"
PLIST_DIR="${HOME}/Library/LaunchAgents"
TEMPLATE_DIR="${REPO}/config/launchd"

# Service labels — must match plist Label fields.
LABELS=(
  com.aicontentengine.backend
  com.aicontentengine.observer
  com.aicontentengine.frontend
)

CMD="${1:-help}"

# ── Helpers ────────────────────────────────────────────────────────────────────

_generate_plists() {
  mkdir -p "$LOG_DIR" "$PLIST_DIR"
  local tmpl_file plist_name
  for tmpl_file in "${TEMPLATE_DIR}"/*.plist.template; do
    plist_name="$(basename "$tmpl_file" .template)"
    sed \
      -e "s|{{REPO_PATH}}|${REPO}|g" \
      -e "s|{{HOME}}|${HOME}|g" \
      -e "s|{{LOG_DIR}}|${LOG_DIR}|g" \
      "$tmpl_file" > "${PLIST_DIR}/${plist_name}"
  done
}

_bootstrap_service() {
  local label="$1" plist="${PLIST_DIR}/${label}.plist"
  if [ ! -f "$plist" ]; then
    echo "  ERROR: plist not found: ${plist}" >&2
    return 1
  fi
  # Bootout first (no-op if not loaded) to allow idempotent reinstall.
  launchctl bootout "${DOMAIN}/${label}" 2>/dev/null || true
  sleep 0.5
  launchctl bootstrap "${DOMAIN}" "${plist}"
}

_bootout_service() {
  local label="$1"
  launchctl bootout "${DOMAIN}/${label}" 2>/dev/null || true
}

_service_loaded() {
  local label="$1"
  launchctl print "${DOMAIN}/${label}" &>/dev/null
}

_service_status_line() {
  local label="$1"
  if _service_loaded "$label"; then
    local state
    state=$(launchctl print "${DOMAIN}/${label}" 2>/dev/null \
            | grep -E "^\s+state = " | awk '{print $NF}' || echo "unknown")
    printf "  %-45s LOADED (%s)\n" "${label}" "${state}"
  else
    printf "  %-45s NOT LOADED\n" "${label}"
  fi
}

_http_check() {
  local label="$1" url="$2"
  if curl -sf --max-time 3 "$url" > /dev/null 2>&1; then
    printf "  %-30s OK\n" "${label}"
  else
    printf "  %-30s NOT RESPONDING\n" "${label}"
  fi
}

_stop_dev_processes() {
  # Stop PID-managed processes from 'make dev' if they exist.
  if [ -f "${REPO}/Makefile" ] && command -v make &>/dev/null; then
    make -C "${REPO}" stop 2>/dev/null || true
  fi
}

# ── Commands ───────────────────────────────────────────────────────────────────

case "$CMD" in

  install)
    echo "[service] Stopping any existing 'make dev' processes…"
    _stop_dev_processes

    echo "[service] Generating LaunchAgent plists from templates…"
    _generate_plists

    echo "[service] Bootstrapping LaunchAgents…"
    for label in "${LABELS[@]}"; do
      _bootstrap_service "$label"
      printf "  Loaded: %s\n" "${label}"
    done

    echo ""
    echo "[service] Installation complete."
    echo "  Services start automatically at login and restart after crashes."
    echo "  Logs: ${LOG_DIR}/"
    echo "  Run 'make service-status' to verify."
    echo ""
    echo "  NOTE: Do NOT run 'make dev' while launchd services are active."
    echo "        Both 'make dev' and the launchd services bind to ports 8000 and 5173."
    ;;

  stop)
    echo "[service] Stopping LaunchAgents…"
    for label in "${LABELS[@]}"; do
      _bootout_service "$label"
      printf "  Stopped: %s\n" "${label}"
    done
    echo "[service] Done. Plists remain installed; run 'make service-install' to reload."
    ;;

  restart)
    echo "[service] Restarting LaunchAgents…"
    for label in "${LABELS[@]}"; do
      _bootout_service "$label"
    done
    sleep 1
    _restart_failed=0
    for label in "${LABELS[@]}"; do
      _plist="${PLIST_DIR}/${label}.plist"
      if [ -f "$_plist" ]; then
        launchctl bootstrap "${DOMAIN}" "${_plist}"
        printf "  Restarted: %s\n" "${label}"
      else
        printf "  WARN: plist not found for %s — run 'make service-install' first.\n" "${label}"
        _restart_failed=1
      fi
    done
    if [ "$_restart_failed" -eq 1 ]; then
      echo "[service] Some services could not be restarted. Run 'make service-install'." >&2
      exit 1
    fi
    ;;

  status)
    echo "[service] LaunchAgent status:"
    for label in "${LABELS[@]}"; do
      _service_status_line "$label"
    done
    echo ""
    echo "[service] HTTP health:"
    _http_check "Backend  :8000" "http://127.0.0.1:8000/health"
    _http_check "Frontend :5173" "http://localhost:5173"
    ;;

  uninstall)
    echo "[service] Unloading and removing LaunchAgents…"
    for label in "${LABELS[@]}"; do
      _bootout_service "$label"
      _plist="${PLIST_DIR}/${label}.plist"
      if [ -f "$_plist" ]; then
        rm -f "$_plist"
        printf "  Removed: %s.plist\n" "${label}"
      else
        printf "  Already absent: %s.plist\n" "${label}"
      fi
    done
    echo "[service] Uninstall complete. Run 'make service-install' to reinstall."
    ;;

  help|*)
    echo "Usage: $0 <install|stop|restart|status|uninstall>"
    echo ""
    echo "  install    Install and start all LaunchAgents (idempotent)"
    echo "  stop       Stop all LaunchAgents (keep plists)"
    echo "  restart    Restart all LaunchAgents"
    echo "  status     Show launchd + HTTP health"
    echo "  uninstall  Stop and remove all plist files"
    exit 1
    ;;

esac
