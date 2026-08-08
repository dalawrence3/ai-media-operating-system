#!/usr/bin/env bash
# AI Content Engine — PostgreSQL backup script
#
# Usage:
#   ACE_DATABASE_URL=postgresql://ace:pass@host:5432/ace ./scripts/backup.sh
#
# Produces a timestamped compressed dump in BACKUP_DIR (default: ./backups/).
# Suitable for cron or a one-shot container job.
#
# STOP: Do NOT run this against a production database without operator review.
#       Do NOT commit BACKUP_DIR to version control.

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-./backups}"
TIMESTAMP=$(date -u +"%Y%m%dT%H%M%SZ")
BACKUP_FILE="${BACKUP_DIR}/ace_backup_${TIMESTAMP}.sql.gz"

if [[ -z "${ACE_DATABASE_URL:-}" ]]; then
    echo "ERROR: ACE_DATABASE_URL is not set." >&2
    exit 1
fi

mkdir -p "${BACKUP_DIR}"

echo "[backup] Starting PostgreSQL dump → ${BACKUP_FILE}"
pg_dump "${ACE_DATABASE_URL}" | gzip > "${BACKUP_FILE}"

SIZE=$(du -sh "${BACKUP_FILE}" | cut -f1)
echo "[backup] Done. Size: ${SIZE}. File: ${BACKUP_FILE}"

# Keep only the last N backups (default: 14).
KEEP="${BACKUP_RETAIN_COUNT:-14}"
ls -t "${BACKUP_DIR}"/ace_backup_*.sql.gz 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm --
echo "[backup] Retention: kept ${KEEP} most recent backups."
