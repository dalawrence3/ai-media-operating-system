"""AuthService: register, login, token refresh, and logout operations.

All DB operations use the connection passed in (SQLite or CompatConnection).
No business objects, tokens, or passwords are ever logged.

Refresh token flow:
  1. generate_refresh_token() → (raw_token, token_hash)
  2. Store token_hash + metadata in auth_refresh_tokens. raw_token → caller.
  3. On refresh: hash incoming raw_token, look up by hash, issue new access token.
  4. On logout: set revoked_at on the token_hash row.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.auth.passwords import hash_password, needs_rehash, verify_password
from app.auth.tokens import (
    AuthConfigurationError,
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_refresh_token,
)


class AuthError(Exception):
    """Base auth error — map to HTTP 401."""


class UserNotFoundError(AuthError):
    """Email not registered."""


class InvalidCredentialsError(AuthError):
    """Password did not match."""


class AccountDisabledError(AuthError):
    """User account is inactive."""


class RefreshTokenError(AuthError):
    """Refresh token invalid, expired, or revoked."""


class AuthService:
    """Stateless service — receives a DB connection per call."""

    def __init__(self, secret_key: str, access_expire: int = 900, refresh_expire: int = 604800):
        if not secret_key or len(secret_key.encode()) < 32:
            raise AuthConfigurationError("AuthService requires a secret_key of at least 32 bytes")
        self._secret = secret_key
        self._access_expire = access_expire
        self._refresh_expire = refresh_expire

    # ── User management ────────────────────────────────────────────────────

    def register_user(self, conn: Any, email: str, plaintext_password: str) -> int:
        """Create a new user. Returns the new user's integer id.

        Raises ValueError on empty inputs or duplicate email.
        """
        if not email or not plaintext_password:
            raise ValueError("Email and password are required")
        email = email.strip().lower()
        pw_hash = hash_password(plaintext_password)
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
        cur = conn.execute(
            """
            INSERT INTO auth_users (email, password_hash, is_active, created_at, updated_at)
            VALUES (?, ?, 1, ?, ?)
            """,
            (email, pw_hash, now, now),
        )
        conn.commit()
        return cur.lastrowid

    def get_user_by_email(self, conn: Any, email: str) -> dict | None:
        """Return user row dict or None. Never returns password_hash to callers."""
        email = email.strip().lower()
        row = conn.execute(
            "SELECT id, email, is_active FROM auth_users WHERE email = ?",
            (email,),
        ).fetchone()
        return dict(row) if row else None

    def _get_user_with_hash(self, conn: Any, email: str) -> dict | None:
        """Internal: return full row including password_hash for verification."""
        email = email.strip().lower()
        row = conn.execute(
            "SELECT id, email, password_hash, is_active FROM auth_users WHERE email = ?",
            (email,),
        ).fetchone()
        return dict(row) if row else None

    def _get_workspace_roles(self, conn: Any, user_id: int) -> dict[str, str]:
        """Return {workspace_id: role} for all workspace memberships."""
        rows = conn.execute(
            "SELECT workspace_id, role FROM auth_workspace_roles WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        return {row["workspace_id"]: row["role"] for row in rows}

    # ── Login ──────────────────────────────────────────────────────────────

    def login(self, conn: Any, email: str, plaintext_password: str) -> dict[str, str]:
        """Authenticate and return {access_token, refresh_token, token_type}.

        raw_refresh_token is returned to caller and NEVER stored.
        Only its SHA-256 hash is persisted.

        Raises: UserNotFoundError, InvalidCredentialsError, AccountDisabledError.
        """
        user = self._get_user_with_hash(conn, email)
        if user is None:
            raise UserNotFoundError("Invalid email or password")

        if not verify_password(plaintext_password, user["password_hash"]):
            raise InvalidCredentialsError("Invalid email or password")

        if not user["is_active"]:
            raise AccountDisabledError("Account is disabled")

        workspace_roles = self._get_workspace_roles(conn, user["id"])
        access_token = create_access_token(
            user_id=user["id"],
            email=user["email"],
            workspace_roles=workspace_roles,
            secret_key=self._secret,
            expire_seconds=self._access_expire,
        )

        raw_refresh, refresh_hash = generate_refresh_token()
        self._store_refresh_token(conn, user["id"], refresh_hash)

        if needs_rehash(user["password_hash"]):
            new_hash = hash_password(plaintext_password)
            conn.execute(
                "UPDATE auth_users SET password_hash = ?, updated_at = ? WHERE id = ?",
                (new_hash, datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S"), user["id"]),
            )
            conn.commit()

        return {
            "access_token": access_token,
            "refresh_token": raw_refresh,
            "token_type": "bearer",
        }

    def _store_refresh_token(self, conn: Any, user_id: int, token_hash: str) -> None:
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=self._refresh_expire)
        conn.execute(
            """
            INSERT INTO auth_refresh_tokens (user_id, token_hash, expires_at, issued_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                user_id,
                token_hash,
                expires_at.strftime("%Y-%m-%dT%H:%M:%S"),
                now.strftime("%Y-%m-%dT%H:%M:%S"),
            ),
        )
        conn.commit()

    # ── Token refresh ──────────────────────────────────────────────────────

    def refresh(self, conn: Any, raw_refresh_token: str) -> dict[str, str]:
        """Issue a new access token given a valid raw refresh token.

        The raw token is hashed before any DB lookup.
        Raises RefreshTokenError on invalid/expired/revoked token.
        """
        token_hash = hash_refresh_token(raw_refresh_token)
        now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
        row = conn.execute(
            """
            SELECT rt.id, rt.user_id, rt.expires_at, rt.revoked_at,
                   u.email, u.is_active
            FROM auth_refresh_tokens rt
            JOIN auth_users u ON u.id = rt.user_id
            WHERE rt.token_hash = ?
            """,
            (token_hash,),
        ).fetchone()

        if row is None:
            raise RefreshTokenError("Refresh token not found")

        row = dict(row)
        if row["revoked_at"] is not None:
            raise RefreshTokenError("Refresh token has been revoked")

        if row["expires_at"] < now_str:
            raise RefreshTokenError("Refresh token has expired")

        if not row["is_active"]:
            raise RefreshTokenError("Account is disabled")

        workspace_roles = self._get_workspace_roles(conn, row["user_id"])
        access_token = create_access_token(
            user_id=row["user_id"],
            email=row["email"],
            workspace_roles=workspace_roles,
            secret_key=self._secret,
            expire_seconds=self._access_expire,
        )
        return {"access_token": access_token, "token_type": "bearer"}

    # ── Logout / revocation ────────────────────────────────────────────────

    def revoke_refresh_token(self, conn: Any, raw_refresh_token: str) -> bool:
        """Mark a refresh token as revoked. Returns True if found and revoked."""
        token_hash = hash_refresh_token(raw_refresh_token)
        now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
        cur = conn.execute(
            "UPDATE auth_refresh_tokens SET revoked_at = ? "
            "WHERE token_hash = ? AND revoked_at IS NULL",
            (now_str, token_hash),
        )
        conn.commit()
        return cur.rowcount > 0

    def revoke_all_user_tokens(self, conn: Any, user_id: int) -> int:
        """Revoke all active refresh tokens for a user (e.g., on password change).

        Returns the number of tokens revoked.
        """
        now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
        cur = conn.execute(
            "UPDATE auth_refresh_tokens SET revoked_at = ? "
            "WHERE user_id = ? AND revoked_at IS NULL",
            (now_str, user_id),
        )
        conn.commit()
        return cur.rowcount

    # ── Workspace role management ──────────────────────────────────────────

    def assign_workspace_role(self, conn: Any, user_id: int, workspace_id: str, role: str) -> None:
        """Grant or update a user's role in a workspace.

        Uses INSERT OR REPLACE semantics (ON CONFLICT DO UPDATE).
        """
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
        conn.execute(
            """
            INSERT INTO auth_workspace_roles (user_id, workspace_id, role, granted_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (user_id, workspace_id) DO UPDATE SET role = excluded.role,
                granted_at = excluded.granted_at
            """,
            (user_id, workspace_id, role, now),
        )
        conn.commit()

    def remove_workspace_role(self, conn: Any, user_id: int, workspace_id: str) -> bool:
        """Remove a user from a workspace. Returns True if removed."""
        cur = conn.execute(
            "DELETE FROM auth_workspace_roles WHERE user_id = ? AND workspace_id = ?",
            (user_id, workspace_id),
        )
        conn.commit()
        return cur.rowcount > 0

    # ── Token introspection ────────────────────────────────────────────────

    def decode_token(self, token: str) -> dict:
        """Decode and verify an access token. Raises TokenInvalidError/TokenExpiredError."""
        return decode_access_token(token, secret_key=self._secret)
