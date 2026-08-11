"""Google OAuth 2.0 client Protocol and injectable implementations.

The GoogleOAuthClient Protocol hides all google-auth / google-auth-oauthlib
import details behind a clean boundary. Tests inject FakeGoogleOAuthClient;
the real implementation is in client_google.py (lazy-imports the Google libs).

This separation means:
  - All automated tests run without google-auth-oauthlib installed.
  - The real implementation is only needed when an operator performs a live
    OAuth flow.
  - Scopes, redirect URIs, and token formats are defined here (protocol level),
    not inside the Google-specific implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

# Scopes for this milestone: identity verification only.
# youtube.upload will be added when live publishing is implemented.
YOUTUBE_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/youtube.readonly",
]

# Scope needed for video uploads (future milestone).
YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"


@dataclass(frozen=True)
class TokenPayload:
    """OAuth token set returned after authorization-code exchange or refresh.

    Never log, serialize to the DB, or return to the frontend.
    access_token is short-lived; refresh_token (if present) is long-lived.
    Google does NOT always return a refresh_token on refresh — only on the
    initial authorization exchange (or when access_type=offline + prompt=consent).
    """

    access_token: str
    refresh_token: str | None  # None on token refresh if still valid
    token_type: str
    expires_at_utc: datetime
    scopes: list[str]
    google_sub: str | None = None  # Google account subject identifier


@dataclass(frozen=True)
class ChannelIdentity:
    """YouTube channel identity returned by the Data API channels.list endpoint.

    Used to verify which YouTube channel an OAuth token belongs to.
    """

    channel_id: str  # Provider channel ID, e.g. "UCxxxxxxxxxxxxxx"
    channel_title: str
    verified_at_utc: datetime


@dataclass(frozen=True)
class AuthorizationURLResult:
    """Result of generating the Google authorization URL."""

    authorization_url: str
    state_nonce: str  # The nonce embedded in the URL and stored server-side
    code_verifier: str | None = None  # PKCE RFC 7636; None only from legacy/fake clients


@runtime_checkable
class GoogleOAuthClient(Protocol):
    """Minimal Google OAuth surface needed for YouTube account connection.

    Implementations:
      - FakeGoogleOAuthClient: deterministic test double, zero network
      - RealGoogleOAuthClient (client_google.py): requires google-auth-oauthlib
    """

    def get_authorization_url(
        self,
        state_nonce: str,
        scopes: list[str],
    ) -> AuthorizationURLResult:
        """Build the Google authorization URL embedding the state nonce.

        The returned URL is sent to the user's browser via redirect.
        state_nonce is embedded in the URL and will be returned by Google
        in the callback's `state` query parameter.
        """
        ...

    def exchange_code(
        self,
        code: str,
        state_nonce: str,
        code_verifier: str | None = None,
    ) -> TokenPayload:
        """Exchange an authorization code for access + refresh tokens.

        code_verifier is the PKCE verifier (RFC 7636); must match the code_challenge
        sent in the authorization URL. Google enforces this for all Web Application flows.
        Raises OAuthCodeExchangeError on any Google-side failure.
        """
        ...

    def refresh_access_token(self, refresh_token: str) -> TokenPayload:
        """Use a refresh token to obtain a new access token.

        Google may or may not return a new refresh_token.
        The TokenPayload.refresh_token field is None if not returned.
        Raises OAuthRefreshError on failure.
        """
        ...

    def revoke_token(self, token: str) -> None:
        """Attempt to revoke a token (access or refresh).

        Best-effort: callers should not fail the disconnect flow if revocation
        fails (e.g. token already expired/revoked). Log the failure.
        Raises OAuthProviderError on non-revocation failures.
        """
        ...

    def get_channel_identity(self, access_token: str) -> ChannelIdentity:
        """Call YouTube Data API channels.list?part=snippet&mine=true.

        Returns the authenticated user's default YouTube channel identity.
        Raises OAuthChannelVerificationError if no channel is found.
        """
        ...


# ---------------------------------------------------------------------------
# Fake implementation for tests
# ---------------------------------------------------------------------------


class FakeGoogleOAuthClient:
    """Deterministic test double — zero network calls, injectable responses.

    Usage in tests:
        client = FakeGoogleOAuthClient(
            fake_channel_id="UCtest123",
            fake_channel_title="Test Channel",
        )
    """

    def __init__(
        self,
        *,
        fake_channel_id: str = "UCtest_channel_001",
        fake_channel_title: str = "Fake Test Channel",
        fake_access_token: str = "fake_access_token_aaa",
        fake_refresh_token: str = "fake_refresh_token_bbb",
        fake_google_sub: str = "1000000000000000001",
        fail_exchange: Exception | None = None,
        fail_refresh: Exception | None = None,
        fail_revoke: Exception | None = None,
        fail_identity: Exception | None = None,
        # When set, the second identity call returns a DIFFERENT channel
        alternate_channel_id: str | None = None,
        alternate_channel_title: str | None = None,
    ) -> None:
        self._channel_id = fake_channel_id
        self._channel_title = fake_channel_title
        self._access_token = fake_access_token
        self._refresh_token = fake_refresh_token
        self._google_sub = fake_google_sub
        self._fail_exchange = fail_exchange
        self._fail_refresh = fail_refresh
        self._fail_revoke = fail_revoke
        self._fail_identity = fail_identity
        self._alternate_channel_id = alternate_channel_id
        self._alternate_channel_title = alternate_channel_title
        self._identity_call_count = 0
        self.revoke_calls: list[str] = []
        self.last_received_code_verifier: str | None = None  # set by exchange_code for spy tests

    def get_authorization_url(
        self,
        state_nonce: str,
        scopes: list[str],
    ) -> AuthorizationURLResult:
        scope_str = "+".join(s.replace(":", "%3A").replace("/", "%2F") for s in scopes)
        url = (
            f"https://accounts.google.com/o/oauth2/auth?"
            f"client_id=fake_client_id&"
            f"scope={scope_str}&"
            f"state={state_nonce}&"
            f"response_type=code&"
            f"access_type=offline"
        )
        # Return a deterministic fake verifier so PKCE propagation can be tested end-to-end.
        return AuthorizationURLResult(
            authorization_url=url,
            state_nonce=state_nonce,
            code_verifier="fake_pkce_code_verifier",
        )

    def exchange_code(
        self,
        code: str,
        state_nonce: str,
        code_verifier: str | None = None,
    ) -> TokenPayload:
        self.last_received_code_verifier = code_verifier  # spy for PKCE tests
        if self._fail_exchange is not None:
            raise self._fail_exchange
        return TokenPayload(
            access_token=self._access_token,
            refresh_token=self._refresh_token,
            token_type="Bearer",
            expires_at_utc=datetime.now(UTC) + timedelta(hours=1),
            scopes=YOUTUBE_SCOPES,
            google_sub=self._google_sub,
        )

    def refresh_access_token(self, refresh_token: str) -> TokenPayload:
        if self._fail_refresh is not None:
            raise self._fail_refresh
        return TokenPayload(
            access_token=self._access_token + "_refreshed",
            refresh_token=None,  # Google often omits on refresh
            token_type="Bearer",
            expires_at_utc=datetime.now(UTC) + timedelta(hours=1),
            scopes=YOUTUBE_SCOPES,
            google_sub=self._google_sub,
        )

    def revoke_token(self, token: str) -> None:
        self.revoke_calls.append(token)
        if self._fail_revoke is not None:
            raise self._fail_revoke

    def get_channel_identity(self, access_token: str) -> ChannelIdentity:
        if self._fail_identity is not None:
            raise self._fail_identity
        self._identity_call_count += 1
        if self._alternate_channel_id is not None and self._identity_call_count > 1:
            return ChannelIdentity(
                channel_id=self._alternate_channel_id,
                channel_title=self._alternate_channel_title or "Alternate Channel",
                verified_at_utc=datetime.now(UTC),
            )
        return ChannelIdentity(
            channel_id=self._channel_id,
            channel_title=self._channel_title,
            verified_at_utc=datetime.now(UTC),
        )
