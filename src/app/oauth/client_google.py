"""Real Google OAuth 2.0 client using google-auth-oauthlib.

DEPENDENCIES (all in pyproject.toml):
  - google-auth>=2.0,<3.0
  - google-auth-oauthlib>=1.0,<2.0
  - google-api-python-client>=2.0,<3.0

These packages are installed. OAuthNotConfiguredError is still raised at
construction time if client_secrets_path is missing or YOUTUBE_CLIENT_SECRETS_PATH
is unset.

OPERATOR SETUP REQUIRED BEFORE USE:
  1. Create a Google Cloud project
  2. Enable YouTube Data API v3
  3. Configure OAuth consent screen (External, not Internal for real channels)
  4. Create OAuth 2.0 credentials (Application type: Web application)
  5. Register redirect URI: http://localhost:8000/api/v1/oauth/youtube/callback
  6. Download client_secret.json to a secure path
  7. Set YOUTUBE_CLIENT_SECRETS_PATH=<path to client_secret.json>
  8. Set ACE_YOUTUBE_REDIRECT_URI=http://localhost:8000/api/v1/oauth/youtube/callback

Do NOT commit client_secret.json to version control.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.oauth.client import (
    YOUTUBE_SCOPES,
    AuthorizationURLResult,
    ChannelIdentity,
    TokenPayload,
)
from app.oauth.errors import (
    OAuthChannelVerificationError,
    OAuthCodeExchangeError,
    OAuthNotConfiguredError,
    OAuthProviderError,
    OAuthRefreshError,
)


class RealGoogleOAuthClient:
    """Live Google OAuth 2.0 client.

    Wraps google-auth-oauthlib.flow.Flow and google-api-python-client.
    Raises OAuthNotConfiguredError at construction time if the required
    libraries are not installed or configuration is missing.
    """

    def __init__(
        self,
        client_secrets_path: str,
        redirect_uri: str,
    ) -> None:
        # Lazy import — fails loudly if google libs are not installed.
        try:
            from google_auth_oauthlib.flow import Flow  # type: ignore[import]
        except ImportError as exc:
            raise OAuthNotConfiguredError(
                "google-auth-oauthlib is not installed. Run: pip install google-auth-oauthlib"
            ) from exc

        import os

        if not os.path.isfile(client_secrets_path):
            raise OAuthNotConfiguredError(
                f"YOUTUBE_CLIENT_SECRETS_PATH points to a missing file: {client_secrets_path}. "
                "Download client_secret.json from Google Cloud Console and set the path."
            )

        # oauthlib/requests-oauthlib refuses to send tokens over plain HTTP unless
        # OAUTHLIB_INSECURE_TRANSPORT is set.  For localhost development the redirect
        # URI is HTTP by design.  Set the flag process-wide when the redirect URI is
        # not HTTPS — production redirect URIs should always be HTTPS, so this gate
        # is effectively dev-only.
        if not redirect_uri.startswith("https://"):
            os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

        self._Flow = Flow
        self._client_secrets_path = client_secrets_path
        self._redirect_uri = redirect_uri

    def _make_flow(self, state: str | None = None, scopes: list[str] | None = None) -> object:
        flow = self._Flow.from_client_secrets_file(  # type: ignore[attr-defined]
            self._client_secrets_path,
            scopes=scopes if scopes is not None else YOUTUBE_SCOPES,
            state=state,
        )
        flow.redirect_uri = self._redirect_uri
        return flow

    def get_authorization_url(
        self,
        state_nonce: str,
        scopes: list[str],
    ) -> AuthorizationURLResult:
        flow = self._make_flow(scopes=scopes)
        # access_type=offline → ensures a refresh_token is included in the response
        # prompt=consent → forces consent screen (ensures refresh_token on reconnect)
        auth_url, _ = flow.authorization_url(  # type: ignore[attr-defined]
            access_type="offline",
            prompt="consent",
            state=state_nonce,
            include_granted_scopes="true",
        )
        # google-auth-oauthlib >= 1.2 auto-generates a PKCE code_challenge embedded in
        # auth_url and stores the matching code_verifier on the flow object.  Google
        # enforces PKCE for Web Application clients and rejects exchanges that omit the
        # verifier.  We extract it here so the caller can bind it to the server-side
        # state and pass it back to fetch_token during the callback.
        code_verifier: str | None = getattr(flow, "code_verifier", None)
        return AuthorizationURLResult(
            authorization_url=auth_url,
            state_nonce=state_nonce,
            code_verifier=code_verifier,
        )

    def exchange_code(
        self,
        code: str,
        state_nonce: str,
        code_verifier: str | None = None,
        requested_scopes: list[str] | None = None,
    ) -> TokenPayload:
        try:
            # Reconstruct the Flow with the same scopes used to build the authorization URL.
            # oauthlib compares the flow's declared scopes against Google's token response; if
            # the flow uses YOUTUBE_SCOPES but Google returns YOUTUBE_UPLOAD_SCOPES, the library
            # raises "Scope has changed from ... to ..." and the exchange fails. Passing the
            # original requested_scopes eliminates that mismatch.
            flow = self._make_flow(state=state_nonce, scopes=requested_scopes)
            # code_verifier must match the code_challenge sent in the authorization URL.
            # Omitting it causes Google to reject with (invalid_grant) Missing code verifier.
            flow.fetch_token(code=code, code_verifier=code_verifier)  # type: ignore[attr-defined]
            credentials = flow.credentials  # type: ignore[attr-defined]
        except Exception as exc:
            raise OAuthCodeExchangeError(f"Authorization code exchange failed: {exc}") from exc

        expires_at = None
        if credentials.expiry:
            expires_at = credentials.expiry.replace(tzinfo=UTC)

        scopes = list(credentials.scopes or YOUTUBE_SCOPES)
        return TokenPayload(
            access_token=credentials.token,
            refresh_token=credentials.refresh_token,
            token_type="Bearer",
            expires_at_utc=expires_at or (datetime.now(UTC) + timedelta(hours=1)),
            scopes=scopes,
            google_sub=None,  # Google sub is in the id_token; extract if needed
        )

    def refresh_access_token(self, refresh_token: str) -> TokenPayload:
        try:
            import json

            from google.auth.transport.requests import Request  # type: ignore[import]
            from google.oauth2.credentials import Credentials  # type: ignore[import]

            secrets_data = json.loads(open(self._client_secrets_path).read())
            web = secrets_data.get("web") or secrets_data.get("installed") or {}
            credentials = Credentials(
                token=None,
                refresh_token=refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=web["client_id"],
                client_secret=web["client_secret"],
                # No scopes= here: google-auth sends 'scope' in the refresh
                # POST body when this is set, explicitly restricting the issued
                # access token.  Omitting it lets Google return a token for the
                # full original grant (including youtube.upload when present).
                # Stored grant scopes are preserved by refresh_account_token via
                # update_tokens(), which keeps existing.scopes intact.
            )
            credentials.refresh(Request())
        except Exception as exc:
            raise OAuthRefreshError(f"Token refresh failed: {exc}") from exc

        expires_at = None
        if credentials.expiry:
            expires_at = credentials.expiry.replace(tzinfo=UTC)

        return TokenPayload(
            access_token=credentials.token,
            refresh_token=credentials.refresh_token,
            token_type="Bearer",
            expires_at_utc=expires_at or (datetime.now(UTC) + timedelta(hours=1)),
            # Google does not return scopes in refresh responses; report empty so
            # callers rely on the stored grant scopes preserved by update_tokens().
            scopes=list(credentials.scopes or []),
            google_sub=None,
        )

    def revoke_token(self, token: str) -> None:
        try:
            import requests  # type: ignore[import]

            resp = requests.post(
                "https://oauth2.googleapis.com/revoke",
                params={"token": token},
                headers={"Content-type": "application/x-www-form-urlencoded"},
                timeout=10,
            )
            if resp.status_code not in (200, 400):
                # 400 means already revoked — acceptable
                raise OAuthProviderError(f"Token revocation returned HTTP {resp.status_code}")
        except OAuthProviderError:
            raise
        except Exception as exc:
            raise OAuthProviderError(f"Token revocation request failed: {exc}") from exc

    def get_oauth_client_secrets(self) -> dict:
        """Return token_uri, client_id, client_secret from the secrets file.

        Used by build_authenticated_youtube_provider to construct Credentials
        with refresh capability for long-running uploads.
        """
        import json

        try:
            secrets_data = json.loads(open(self._client_secrets_path).read())
            web = secrets_data.get("web") or secrets_data.get("installed") or {}
            return {
                "token_uri": web.get("token_uri", "https://oauth2.googleapis.com/token"),
                "client_id": web.get("client_id"),
                "client_secret": web.get("client_secret"),
            }
        except Exception:
            return {
                "token_uri": "https://oauth2.googleapis.com/token",
                "client_id": None,
                "client_secret": None,
            }

    def get_channel_identity(self, access_token: str) -> ChannelIdentity:
        try:
            from google.oauth2.credentials import Credentials  # type: ignore[import]
            from googleapiclient.discovery import build  # type: ignore[import]

            credentials = Credentials(token=access_token)
            service = build("youtube", "v3", credentials=credentials)
            response = service.channels().list(part="snippet", mine=True).execute()
        except Exception as exc:
            raise OAuthChannelVerificationError(
                f"YouTube channel identity lookup failed: {exc}"
            ) from exc

        items = response.get("items", [])
        if not items:
            raise OAuthChannelVerificationError(
                "The authenticated Google account has no associated YouTube channel. "
                "The account must have a YouTube channel to connect."
            )
        channel = items[0]
        return ChannelIdentity(
            channel_id=channel["id"],
            channel_title=channel.get("snippet", {}).get("title", "Unknown Channel"),
            verified_at_utc=datetime.now(UTC),
        )
