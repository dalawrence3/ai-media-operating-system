"""OAuth CSRF state management.

The state token is a cryptographically random nonce. The full binding
(workspace_id, channel_id, account_id, user_id) is stored server-side,
keyed by the nonce. Callback receives only the nonce; all workspace/account
binding comes from the server-side record, never from query parameters.

State lifecycle:
  1. generate_state()  → stores claims, returns nonce
  2. Nonce sent to Google as `state` parameter in authorization URL
  3. Google returns nonce in callback `state` parameter
  4. validate_state()  → looks up claims, deletes key (one-time use), returns claims
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import dataclass

from app.oauth.errors import (
    OAuthStateExpiredError,
    OAuthStateNotFoundError,
    OAuthStateReplayError,
)

# State TTL: 10 minutes. Short enough to limit replay window, long enough for a human
# to read the consent screen and authorize.
STATE_TTL_SECONDS = 600


@dataclass(frozen=True)
class OAuthStateClaims:
    """The binding stored server-side for a pending OAuth state nonce."""

    nonce: str
    user_id: str
    workspace_id: str
    channel_id: str
    account_id: str
    created_at: float  # Unix timestamp
    code_verifier: str | None = None  # PKCE RFC 7636; None when PKCE not used

    def is_expired(self) -> bool:
        return (time.monotonic() - self.created_at) > STATE_TTL_SECONDS

    def to_dict(self) -> dict:
        return {
            "nonce": self.nonce,
            "user_id": self.user_id,
            "workspace_id": self.workspace_id,
            "channel_id": self.channel_id,
            "account_id": self.account_id,
            "created_at": self.created_at,
            "code_verifier": self.code_verifier,
        }

    @classmethod
    def from_dict(cls, d: dict) -> OAuthStateClaims:
        return cls(
            nonce=d["nonce"],
            user_id=d["user_id"],
            workspace_id=d["workspace_id"],
            channel_id=d["channel_id"],
            account_id=d["account_id"],
            created_at=float(d["created_at"]),
            code_verifier=d.get("code_verifier"),  # None for records predating PKCE
        )


# ---------------------------------------------------------------------------
# In-memory state store (used in tests and local dev without Redis)
# ---------------------------------------------------------------------------


class InMemoryOAuthStateStore:
    """Thread-safe in-memory OAuth state store with TTL enforcement.

    Suitable for single-process development and tests.
    In production with multiple API workers, use RedisOAuthStateStore.
    """

    def __init__(self) -> None:
        self._store: dict[str, OAuthStateClaims] = {}
        self._lock = threading.Lock()

    def store(self, claims: OAuthStateClaims) -> None:
        with self._lock:
            self._store[claims.nonce] = claims

    def pop(self, nonce: str) -> OAuthStateClaims:
        """Retrieve and atomically delete the state (one-time use).

        Raises OAuthStateNotFoundError, OAuthStateExpiredError, OAuthStateReplayError.
        """
        with self._lock:
            claims = self._store.get(nonce)
            if claims is None:
                raise OAuthStateNotFoundError(
                    f"No OAuth state found for nonce (expired or unknown): {nonce[:8]}..."
                )
            del self._store[nonce]
        if claims.is_expired():
            raise OAuthStateExpiredError(
                "OAuth state has expired. Please restart the connection flow."
            )
        return claims

    def clear_all(self) -> None:
        with self._lock:
            self._store.clear()


# ---------------------------------------------------------------------------
# Redis state store (production multi-process)
# ---------------------------------------------------------------------------


class RedisOAuthStateStore:
    """Redis-backed OAuth state store with native TTL.

    State keys use the pattern: oauth:state:{nonce}
    TTL is set to STATE_TTL_SECONDS. Redis handles expiry atomically.
    """

    def __init__(self, redis_client: object) -> None:
        self._redis = redis_client

    def store(self, claims: OAuthStateClaims) -> None:
        key = f"oauth:state:{claims.nonce}"
        self._redis.set(key, json.dumps(claims.to_dict()), ex=STATE_TTL_SECONDS)  # type: ignore[attr-defined]

    def pop(self, nonce: str) -> OAuthStateClaims:
        key = f"oauth:state:{nonce}"
        # Atomic get-and-delete: GETDEL is Redis 6.2+; fallback uses pipeline.
        raw = self._redis.getdel(key)  # type: ignore[attr-defined]
        if raw is None:
            raise OAuthStateNotFoundError(
                f"No OAuth state found for nonce (expired or unknown): {nonce[:8]}..."
            )
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            raise OAuthStateReplayError("OAuth state payload is corrupt.") from exc
        claims = OAuthStateClaims.from_dict(data)
        if claims.is_expired():
            raise OAuthStateExpiredError(
                "OAuth state has expired. Please restart the connection flow."
            )
        return claims


# ---------------------------------------------------------------------------
# Module-level singleton (swappable for tests)
# ---------------------------------------------------------------------------

_state_store: InMemoryOAuthStateStore | RedisOAuthStateStore | None = None
_store_lock = threading.Lock()


def get_state_store() -> InMemoryOAuthStateStore | RedisOAuthStateStore:
    global _state_store
    with _store_lock:
        if _state_store is None:
            _state_store = InMemoryOAuthStateStore()
    return _state_store


def set_state_store(store: InMemoryOAuthStateStore | RedisOAuthStateStore) -> None:
    global _state_store
    with _store_lock:
        _state_store = store


def reset_state_store() -> None:
    global _state_store
    with _store_lock:
        _state_store = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_state(
    *,
    user_id: str,
    workspace_id: str,
    channel_id: str,
    account_id: str,
    code_verifier: str | None = None,
) -> str:
    """Generate a cryptographically random state nonce and store the binding.

    Returns the nonce string to include in the authorization URL.
    code_verifier is the PKCE verifier to bind to this nonce; None when PKCE is not used.
    """
    nonce = secrets.token_hex(32)  # 256 bits of randomness
    claims = OAuthStateClaims(
        nonce=nonce,
        user_id=user_id,
        workspace_id=workspace_id,
        channel_id=channel_id,
        account_id=account_id,
        created_at=time.monotonic(),
        code_verifier=code_verifier,
    )
    get_state_store().store(claims)
    return nonce


def validate_state(nonce: str) -> OAuthStateClaims:
    """Validate and consume (delete) the OAuth state for the given nonce.

    Raises:
        OAuthStateNotFoundError: nonce is unknown or already consumed.
        OAuthStateExpiredError: nonce was found but TTL has elapsed.
        OAuthStateReplayError: store data is corrupt (Redis path only).
    """
    return get_state_store().pop(nonce)
