"""Authentication and authorization for the AI Content Engine (M15.5).

Architecture:
  - Argon2id password hashing via pwdlib (never bcrypt/MD5/SHA-1).
  - Short-lived JWT access tokens signed with HS256 (PyJWT).
  - Long-lived refresh tokens: only the SHA-256 hash is stored in PostgreSQL/SQLite.
    The raw token is returned to the caller once and never persisted.
  - RBAC: owner/admin/operator/reviewer/analyst; deny-by-default.
  - secret_key must be ≥32 bytes; endpoints fail closed if not configured.
"""

from app.auth.passwords import hash_password, verify_password
from app.auth.rbac import Role, has_permission, require_role
from app.auth.service import AuthService
from app.auth.tokens import (
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_refresh_token,
)

__all__ = [
    "AuthService",
    "Role",
    "create_access_token",
    "decode_access_token",
    "generate_refresh_token",
    "hash_password",
    "hash_refresh_token",
    "has_permission",
    "require_role",
    "verify_password",
]
