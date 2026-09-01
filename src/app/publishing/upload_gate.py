"""Pre-flight gate for YouTube private video uploads.

Every check in this module is fail-closed: on any failure a typed error is
raised before any network call is made. The sequence is:

  1. ACE_PUBLISHING_LIVE_ENABLED=true  (LivePublishingNotEnabledError)
  2. Account connected + active credential  (via oauth.flow helpers)
  3. Token readable and refreshed if expired  (OAuthRefreshError)
  4. youtube.upload scope present  (UploadScopeNotGrantedError)
  5. Live channel identity matches registered external_account_id  (UploadAccountMismatchError)
  6. Video file exists, non-zero, supported extension  (UploadFileError)

build_authenticated_youtube_provider() runs steps 1-5 and returns a live
YouTubePublishingProvider bound to the account's access token.

validate_video_file() runs step 6 independently (call before queueing a job).
"""

from __future__ import annotations

import mimetypes
import os
from typing import Any

from app.oauth.client import YOUTUBE_RELEASE_SCOPE, YOUTUBE_UPLOAD_SCOPE
from app.oauth.store import LocalFileTokenStore, StoredTokenPayload
from app.publishing.errors import (
    LivePublishingNotEnabledError,
    ReleaseScopeNotGrantedError,
    UploadAccountMismatchError,
    UploadFileError,
    UploadScopeNotGrantedError,
)
from app.publishing.providers.youtube import (
    SUPPORTED_VIDEO_EXTENSIONS,
    RealYouTubeAPIClient,
    YouTubePublishingProvider,
)

_MAX_TITLE_BYTES = 100 * 4  # UTF-8 upper bound; YouTube counts codepoints
_MAX_DESCRIPTION_BYTES = 5000 * 4


def check_live_publishing_gate() -> None:
    """Raise unless live publishing is enabled AND this is not a test runtime.

    Phase 18E adds the test-mode arm. This is the single chokepoint every live
    upload passes through, which makes it the right place to guarantee that no
    test run can ever create a real provider publication — an effect that
    database isolation cannot undo, because it does not live in the database.
    """
    from app.core.config import get_config
    from app.core.runtime_mode import assert_live_effect_allowed

    assert_live_effect_allowed("provider_upload")

    if not get_config().publishing_live_enabled:
        raise LivePublishingNotEnabledError(
            "ACE_PUBLISHING_LIVE_ENABLED is not set to 'true'. "
            "Set this environment variable explicitly before attempting a live upload. "
            "Default is False — all publishing uses the fake provider in CI and development."
        )


def validate_video_file(file_path: str) -> None:
    """Raise UploadFileError if the file cannot safely be uploaded.

    Checks: file exists, non-zero size, supported video extension / MIME type.
    Does not read or parse the file contents.
    """
    if not os.path.isfile(file_path):
        raise UploadFileError(f"Video file not found: {file_path!r}")
    if os.path.getsize(file_path) == 0:
        raise UploadFileError(f"Video file is empty (0 bytes): {file_path!r}")

    ext = os.path.splitext(file_path)[1].lower()
    mime_type, _ = mimetypes.guess_type(file_path)
    is_video_mime = mime_type is not None and mime_type.startswith("video/")
    if ext not in SUPPORTED_VIDEO_EXTENSIONS and not is_video_mime:
        raise UploadFileError(
            f"File {file_path!r} does not appear to be a supported video format. "
            f"Extension {ext!r} is not in the allowed set "
            f"{sorted(SUPPORTED_VIDEO_EXTENSIONS)}, "
            f"and guessed MIME type is {mime_type!r}."
        )


def check_upload_scope(stored_token: StoredTokenPayload) -> None:
    """Raise UploadScopeNotGrantedError if youtube.upload is not in granted scopes."""
    if not stored_token.has_scope(YOUTUBE_UPLOAD_SCOPE):
        raise UploadScopeNotGrantedError(
            f"The account's OAuth credential does not include '{YOUTUBE_UPLOAD_SCOPE}'. "
            f"Granted scopes: {stored_token.scopes}. "
            "Use the 'Enable Upload Permission' action to reauthorize with upload scope."
        )


def check_release_scope(stored_token: StoredTokenPayload) -> None:
    """Raise ReleaseScopeNotGrantedError if youtube.force-ssl is not in granted scopes."""
    if not stored_token.has_scope(YOUTUBE_RELEASE_SCOPE):
        raise ReleaseScopeNotGrantedError(
            f"The account's OAuth credential does not include '{YOUTUBE_RELEASE_SCOPE}'. "
            f"Granted scopes: {stored_token.scopes}. "
            "Use the 'Upgrade Release Scope' OAuth flow to reauthorize with youtube.force-ssl."
        )


def _verify_channel_identity_matches(
    conn: Any,
    *,
    account_id: str,
    workspace_id: str,
    channel_id: str,
    access_token: str,
    oauth_client: Any,
) -> None:
    """Call YouTube channels.list and compare channel ID against the registered one.

    Fail closed: raises UploadAccountMismatchError on any mismatch.
    """
    from app.control_plane.repository import get_platform_account
    from app.oauth.errors import OAuthChannelVerificationError

    acct = get_platform_account(conn, account_id)

    try:
        identity = oauth_client.get_channel_identity(access_token)
    except OAuthChannelVerificationError as exc:
        raise UploadAccountMismatchError(
            f"Could not verify YouTube channel identity for account {account_id}: {exc}"
        ) from exc

    registered = acct.external_account_id
    if identity.channel_id != registered:
        raise UploadAccountMismatchError(
            f"Channel identity mismatch for account {account_id}: "
            f"registered={registered!r}, live={identity.channel_id!r}. "
            "Upload rejected — the credential belongs to a different channel."
        )


def resolve_upload_token(
    conn: Any,
    *,
    account_id: str,
    workspace_id: str,
    channel_id: str,
    oauth_client: Any,
    token_store: LocalFileTokenStore | None = None,
) -> StoredTokenPayload:
    """Load and (if expired) refresh the account's OAuth token.

    Returns the current StoredTokenPayload with a valid access token.
    Propagates OAuthRefreshError / OAuthTokenStoreError on failure.
    """
    from app.oauth.flow import refresh_account_token

    return refresh_account_token(
        conn,
        account_id=account_id,
        workspace_id=workspace_id,
        channel_id=channel_id,
        oauth_client=oauth_client,
        token_store=token_store,
    )


def build_authenticated_youtube_provider(
    conn: Any,
    *,
    account_id: str,
    workspace_id: str,
    channel_id: str,
    oauth_client: Any,
    token_store: LocalFileTokenStore | None = None,
) -> YouTubePublishingProvider:
    """Run the full upload pre-flight gate and return a live YouTubePublishingProvider.

    Gate sequence:
      1. ACE_PUBLISHING_LIVE_ENABLED=true
      2. Token readable and refreshed if expired
      3. youtube.upload scope present
      4. Live channel identity matches registered external_account_id

    On any failure a typed error is raised before any upload is attempted.
    The returned provider is bound to the account's current access token.
    """
    # Step 1: live publishing gate
    check_live_publishing_gate()

    # Step 2: resolve (and refresh if needed) the account token
    stored = resolve_upload_token(
        conn,
        account_id=account_id,
        workspace_id=workspace_id,
        channel_id=channel_id,
        oauth_client=oauth_client,
        token_store=token_store,
    )

    # Step 3: upload scope check
    check_upload_scope(stored)

    # Step 4: live channel identity verification
    _verify_channel_identity_matches(
        conn,
        account_id=account_id,
        workspace_id=workspace_id,
        channel_id=channel_id,
        access_token=stored.access_token,
        oauth_client=oauth_client,
    )

    # All gates passed — construct a live provider with full refresh credentials
    # so google-auth can auto-refresh the access token mid-upload if it expires.
    client_secrets = _load_client_secrets(oauth_client)
    api_client = RealYouTubeAPIClient(
        access_token=stored.access_token,
        refresh_token=stored.refresh_token,
        token_uri=client_secrets.get("token_uri"),
        client_id=client_secrets.get("client_id"),
        client_secret=client_secrets.get("client_secret"),
    )
    provider = YouTubePublishingProvider(api_client=api_client)
    provider.initialize()
    return provider


def _load_client_secrets(oauth_client: Any) -> dict:
    """Extract token_uri/client_id/client_secret from the OAuth client."""
    try:
        return oauth_client.get_oauth_client_secrets()
    except Exception:
        return {"token_uri": None, "client_id": None, "client_secret": None}
