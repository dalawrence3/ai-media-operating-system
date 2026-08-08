"""SCHEMA_VERSION 20 — Auth, RBAC, and Object Storage registry.

New tables:
  auth_users              — local user accounts (Argon2id password hash, never plaintext)
  auth_refresh_tokens     — hashed refresh token fingerprints for revocation / rotation
  auth_workspace_roles    — user → workspace → role assignments (RBAC)
  obj_storage_objects     — canonical content-addressed object references (no binaries stored)

Security invariants:
  - password_hash stores only the Argon2id hash; plaintext is NEVER persisted
  - refresh token fingerprint is a SHA-256 hash of the raw token; raw value is NEVER persisted
  - auth_workspace_roles enforces deny-by-default; absence of a row means no access
  - obj_storage_objects records metadata + provenance; binary content lives in object storage

Revision: 0002
"""

from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


_UPGRADE_SQL = """
-- auth_users: local user accounts.
-- password_hash stores Argon2id output only; plaintext never persisted.
CREATE TABLE IF NOT EXISTS auth_users (
    id              BIGSERIAL PRIMARY KEY,
    email           TEXT    NOT NULL UNIQUE,
    display_name    TEXT    NOT NULL DEFAULT '',
    password_hash   TEXT    NOT NULL,
    is_active       INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at      TEXT    NOT NULL DEFAULT '',
    updated_at      TEXT    NOT NULL DEFAULT '',
    last_login_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_auth_users_email ON auth_users (email);
CREATE INDEX IF NOT EXISTS idx_auth_users_active ON auth_users (is_active);

-- auth_refresh_tokens: hashed fingerprints only; raw token is never stored.
-- token_hash: SHA-256(raw_token_bytes) stored as hex string.
-- Rotation: each use may issue a new token (new row); old row is marked revoked.
CREATE TABLE IF NOT EXISTS auth_refresh_tokens (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT  NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
    token_hash      TEXT    NOT NULL UNIQUE,
    issued_at       TEXT    NOT NULL DEFAULT '',
    expires_at      TEXT    NOT NULL DEFAULT '',
    revoked_at      TEXT,
    last_used_at    TEXT,
    device_hint     TEXT    NOT NULL DEFAULT '',
    ip_hint         TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_art_user ON auth_refresh_tokens (user_id, expires_at);
CREATE INDEX IF NOT EXISTS idx_art_hash ON auth_refresh_tokens (token_hash);

-- auth_workspace_roles: user → workspace → role (deny-by-default).
-- Roles: owner | admin | operator | reviewer | analyst
-- A user with no row for a workspace has no access (deny-by-default enforced in code).
CREATE TABLE IF NOT EXISTS auth_workspace_roles (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT  NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
    workspace_id    TEXT    NOT NULL,
    role            TEXT    NOT NULL CHECK (role IN (
                                'owner', 'admin', 'operator', 'reviewer', 'analyst')),
    granted_at      TEXT    NOT NULL DEFAULT '',
    granted_by      TEXT    NOT NULL DEFAULT '',
    UNIQUE (user_id, workspace_id)
);

CREATE INDEX IF NOT EXISTS idx_awr_user ON auth_workspace_roles (user_id);
CREATE INDEX IF NOT EXISTS idx_awr_workspace ON auth_workspace_roles (workspace_id, role);

-- obj_storage_objects: canonical reference registry for stored artifacts.
-- Binary content lives in object storage (local filesystem or S3-compatible).
-- This table records metadata, provenance, and integrity hashes only.
CREATE TABLE IF NOT EXISTS obj_storage_objects (
    id              BIGSERIAL PRIMARY KEY,
    workspace_id    TEXT    NOT NULL,
    channel_id      TEXT,
    storage_backend TEXT    NOT NULL DEFAULT 'local'
                            CHECK (storage_backend IN ('local', 's3')),
    bucket          TEXT    NOT NULL DEFAULT '',
    object_key      TEXT    NOT NULL,
    sha256          TEXT    NOT NULL,
    byte_size       BIGINT  NOT NULL,
    content_type    TEXT    NOT NULL DEFAULT 'application/octet-stream',
    source_entity_type  TEXT NOT NULL DEFAULT '',
    source_entity_id    TEXT NOT NULL DEFAULT '',
    created_at      TEXT    NOT NULL DEFAULT '',
    deleted_at      TEXT,
    UNIQUE (storage_backend, bucket, object_key)
);

CREATE INDEX IF NOT EXISTS idx_oso_workspace ON obj_storage_objects (workspace_id);
CREATE INDEX IF NOT EXISTS idx_oso_sha256 ON obj_storage_objects (sha256);
CREATE INDEX IF NOT EXISTS idx_oso_source
    ON obj_storage_objects (source_entity_type, source_entity_id);
"""

_DOWNGRADE_SQL = [
    "DROP TABLE IF EXISTS obj_storage_objects CASCADE",
    "DROP TABLE IF EXISTS auth_workspace_roles CASCADE",
    "DROP TABLE IF EXISTS auth_refresh_tokens CASCADE",
    "DROP TABLE IF EXISTS auth_users CASCADE",
]


def upgrade() -> None:
    op.execute(_UPGRADE_SQL)


def downgrade() -> None:
    for sql in _DOWNGRADE_SQL:
        op.execute(sql)
