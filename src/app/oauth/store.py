"""OAuth token persistence (local file backend).

Token values (access_token, refresh_token) are NEVER stored in SQLite.
They live in JSON files under ACE_YOUTUBE_TOKEN_DIR, referenced by path
stored in cp_credential_profiles.external_ref.

File naming: {token_dir}/{account_id}.json
File permissions: 0o600 (owner read/write only)

The token file is the source of truth for a connected account's credentials.
Revoking a credential removes this file (after attempting Google token revocation).

In production, this local-file backend should be replaced by a Vault or
AWS Secrets Manager backend where external_ref is a Vault path/ARN and
the read/write operations call the secrets API. The protocol here is the
same; only the backend changes.

Token file schema:
{
  "account_id": "<platform_account_id>",
  "access_token": "<token>",
  "refresh_token": "<token or null>",
  "token_type": "Bearer",
  "expires_at_utc": "<ISO-8601 UTC>",
  "scopes": ["https://www.googleapis.com/auth/youtube.readonly"],
  "google_sub": "<google account subject>",
  "stored_at_utc": "<ISO-8601 UTC>"
}
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.oauth.errors import OAuthTokenStoreError


@dataclass(frozen=True)
class StoredTokenPayload:
    """Deserialized OAuth token payload read from the token store.

    Never log any field except account_id. Never return to the frontend.
    """

    account_id: str
    access_token: str
    refresh_token: str | None
    token_type: str
    expires_at_utc: datetime | None
    scopes: list[str]
    google_sub: str | None
    stored_at_utc: datetime

    def is_expired(self) -> bool:
        if self.expires_at_utc is None:
            return False
        return datetime.now(UTC) >= self.expires_at_utc

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


class LocalFileTokenStore:
    """Persists OAuth tokens as permission-restricted JSON files.

    external_ref pattern: file:///{absolute_path}
    The prefix is stripped when reading/writing; it is stored in
    cp_credential_profiles.external_ref so the location is traceable.
    """

    PREFIX = "file://"

    def __init__(self, token_dir: str | Path) -> None:
        self._dir = Path(token_dir)

    def _ensure_dir(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._dir.chmod(0o700)

    def _path_for(self, account_id: str) -> Path:
        safe_id = account_id.replace("/", "_").replace(".", "_")
        return self._dir / f"{safe_id}.json"

    def external_ref(self, account_id: str) -> str:
        """Return the external_ref string to store in cp_credential_profiles."""
        return f"{self.PREFIX}{self._path_for(account_id)}"

    def write(
        self,
        *,
        account_id: str,
        access_token: str,
        refresh_token: str | None,
        token_type: str,
        expires_at_utc: datetime | None,
        scopes: list[str],
        google_sub: str | None,
    ) -> str:
        """Write token to file and return the external_ref string.

        File is written atomically (.tmp → rename) with 0o600 permissions.
        Never logs token values.
        """
        self._ensure_dir()
        path = self._path_for(account_id)
        tmp_path = path.with_suffix(".tmp")
        payload = {
            "account_id": account_id,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": token_type,
            "expires_at_utc": expires_at_utc.isoformat() if expires_at_utc else None,
            "scopes": scopes,
            "google_sub": google_sub,
            "stored_at_utc": datetime.now(UTC).isoformat(),
        }
        try:
            tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)
            tmp_path.rename(path)
        except OSError as exc:
            raise OAuthTokenStoreError(
                f"Failed to write OAuth token for account {account_id}: {exc}"
            ) from exc
        return self.external_ref(account_id)

    def read(self, external_ref: str) -> StoredTokenPayload:
        """Read and deserialize a token from its external_ref path.

        Raises OAuthTokenStoreError if the file is missing or corrupt.
        """
        if not external_ref.startswith(self.PREFIX):
            raise OAuthTokenStoreError(
                f"external_ref does not have expected prefix '{self.PREFIX}': {external_ref}"
            )
        path = Path(external_ref[len(self.PREFIX) :])
        if not path.exists():
            raise OAuthTokenStoreError(
                "Token file not found for external_ref. Account may need reconnection."
            )
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise OAuthTokenStoreError(f"Token file is unreadable or corrupt: {exc}") from exc

        expires_at = None
        if data.get("expires_at_utc"):
            try:
                expires_at = datetime.fromisoformat(data["expires_at_utc"])
            except ValueError:
                pass

        return StoredTokenPayload(
            account_id=data["account_id"],
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            token_type=data.get("token_type", "Bearer"),
            expires_at_utc=expires_at,
            scopes=data.get("scopes", []),
            google_sub=data.get("google_sub"),
            stored_at_utc=datetime.fromisoformat(data["stored_at_utc"]),
        )

    def update_tokens(
        self,
        external_ref: str,
        *,
        access_token: str,
        refresh_token: str | None,
        expires_at_utc: datetime | None,
    ) -> None:
        """Update only the token fields; preserve account_id, scopes, google_sub.

        Preserves existing refresh_token if refresh_token argument is None
        (Google does not always return a new refresh_token on token refresh).
        """
        existing = self.read(external_ref)
        effective_refresh = refresh_token if refresh_token is not None else existing.refresh_token
        self.write(
            account_id=existing.account_id,
            access_token=access_token,
            refresh_token=effective_refresh,
            token_type=existing.token_type,
            expires_at_utc=expires_at_utc,
            scopes=existing.scopes,
            google_sub=existing.google_sub,
        )

    def delete(self, external_ref: str) -> None:
        """Remove the token file. Silent if the file does not exist."""
        if not external_ref.startswith(self.PREFIX):
            return
        path = Path(external_ref[len(self.PREFIX) :])
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise OAuthTokenStoreError(f"Failed to delete token file: {exc}") from exc


# ---------------------------------------------------------------------------
# Singleton accessor (swappable for tests)
# ---------------------------------------------------------------------------

_token_store: LocalFileTokenStore | None = None


def get_token_store() -> LocalFileTokenStore:
    global _token_store
    if _token_store is None:
        from app.core.config import get_config

        cfg = get_config()
        _token_store = LocalFileTokenStore(cfg.youtube_token_dir)
    return _token_store


def set_token_store(store: LocalFileTokenStore) -> None:
    global _token_store
    _token_store = store


def reset_token_store() -> None:
    global _token_store
    _token_store = None
