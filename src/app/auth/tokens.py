"""JWT access tokens and refresh token generation/hashing.

Security invariants:
  - Access tokens: HS256, short-lived (default 15 min), signed with secret_key.
  - Refresh tokens: 32-byte cryptographically random raw value, never persisted.
    Only SHA-256(raw_token) is stored in auth_refresh_tokens.
  - secret_key must be ≥32 bytes; creation/decoding raise ConfigurationError if not.
  - Tokens are never logged.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt


class AuthConfigurationError(Exception):
    """Raised when auth is not properly configured (e.g., missing secret_key)."""


class TokenExpiredError(Exception):
    """Raised when a JWT has expired."""


class TokenInvalidError(Exception):
    """Raised when a JWT fails signature verification or is malformed."""


_MIN_SECRET_BYTES = 32
_ALGORITHM = "HS256"
_REFRESH_TOKEN_BYTES = 32


def _validate_secret(secret_key: str) -> None:
    if not secret_key or len(secret_key.encode()) < _MIN_SECRET_BYTES:
        raise AuthConfigurationError(
            "ACE_SECRET_KEY must be set to a random value of at least 32 bytes "
            "before authentication endpoints can be used."
        )


def create_access_token(
    user_id: int,
    email: str,
    workspace_roles: dict[str, str],
    *,
    secret_key: str,
    expire_seconds: int = 900,
) -> str:
    """Create a signed JWT access token.

    Claims:
      sub  — str(user_id)
      email — user email address
      roles — {workspace_id: role} mapping
      iat  — issued-at (UTC)
      exp  — expiry (UTC)
    """
    _validate_secret(secret_key)
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "email": email,
        "roles": workspace_roles,
        "iat": now,
        "exp": now + timedelta(seconds=expire_seconds),
    }
    return jwt.encode(payload, secret_key, algorithm=_ALGORITHM)


def decode_access_token(token: str, *, secret_key: str) -> dict[str, Any]:
    """Decode and verify a JWT access token. Returns the claims dict.

    Raises:
      AuthConfigurationError — secret_key not configured
      TokenExpiredError      — token has expired
      TokenInvalidError      — signature invalid / malformed
    """
    _validate_secret(secret_key)
    try:
        return jwt.decode(token, secret_key, algorithms=[_ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpiredError("Access token has expired") from exc
    except jwt.PyJWTError as exc:
        raise TokenInvalidError(f"Invalid access token: {exc}") from exc


def generate_refresh_token() -> tuple[str, str]:
    """Generate a refresh token pair.

    Returns:
      (raw_token, token_hash)

    raw_token  — 32-byte hex string; return to caller, NEVER store.
    token_hash — SHA-256 of raw_token; persist this in auth_refresh_tokens.
    """
    raw = secrets.token_hex(_REFRESH_TOKEN_BYTES)
    return raw, hash_refresh_token(raw)


def hash_refresh_token(raw_token: str) -> str:
    """Return the SHA-256 hex digest of a raw refresh token."""
    return hashlib.sha256(raw_token.encode()).hexdigest()
