# Backup & Restore Runbook

## Overview

The AI Content Engine uses PostgreSQL as the canonical data store. Redis is
ephemeral (job queue transport only — no canonical state). Object storage
artefacts (audio, video, thumbnails) are stored in the local filesystem or S3.

## Backup strategy

| Component | Source of truth | Backup method | Frequency |
|-----------|----------------|---------------|-----------|
| PostgreSQL | All canonical state | `pg_dump \| gzip` | Every 6 hours |
| Object storage (local) | Rendered artefacts | Rsync / S3 sync | Daily |
| Object storage (S3) | Rendered artefacts | S3 versioning + lifecycle | Managed by provider |
| Redis | Job queue state | **Not backed up** — queues are ephemeral | N/A |

## Running a backup

```bash
# Set the database URL (never commit this value)
export ACE_DATABASE_URL=postgresql://ace:PASSWORD@host:5432/ace

# Run the backup script
./scripts/backup.sh

# Backups are written to ./backups/ by default
# Override with BACKUP_DIR=/mnt/backup ./scripts/backup.sh
```

Retention: the script keeps the 14 most recent backups (override with
`BACKUP_RETAIN_COUNT=30`).

## Restore procedure

```bash
# 1. Stop all application services to prevent writes during restore
docker compose stop api worker scheduler

# 2. Drop and recreate the database
psql $ACE_DATABASE_URL -c "DROP DATABASE ace;"
psql $ACE_DATABASE_URL -c "CREATE DATABASE ace OWNER ace;"

# 3. Restore from backup
gunzip -c backups/ace_backup_20260807T120000Z.sql.gz | psql $ACE_DATABASE_URL

# 4. Re-run migrations to ensure schema is current
ACE_DATABASE_URL=$ACE_DATABASE_URL python -m alembic upgrade head

# 5. Restart services
docker compose start api worker scheduler
```

## RTO / RPO targets

| Metric | Target |
|--------|--------|
| RPO (Recovery Point Objective) | ≤ 6 hours (last backup) |
| RTO (Recovery Time Objective) | ≤ 30 minutes |

## Disaster recovery: full environment loss

1. Provision a new PostgreSQL instance (same version, same region).
2. Create database and user.
3. Restore from the most recent backup (step 3 above).
4. Update `ACE_DATABASE_URL` in the environment / secrets manager.
5. Re-deploy the application stack (`docker compose up -d`).
6. Verify `/api/ready` returns `{"status": "ready"}`.
7. Run a smoke test against the restored data.

## Backup verification

Periodically restore to a staging database and run:

```bash
python -m pytest tests/ -q
```

A passing test suite against restored data confirms structural integrity.

## Security

- Backup files contain all application data. Store them encrypted at rest.
- Never commit backup files to version control.
- Rotate database passwords after any suspected credential exposure.
- The backup script uses `ACE_DATABASE_URL` from the environment — never
  hard-codes credentials.
