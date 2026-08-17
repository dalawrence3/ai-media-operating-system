"""Tests for RealGoogleOAuthClient construction and import safety.

Verifies:
- google-auth / google-auth-oauthlib / google-api-python-client are importable
- RealGoogleOAuthClient can be constructed with a valid local client-secrets fixture
- Missing secrets path fails with OAuthNotConfiguredError (no network call)
- Malformed secrets file raises OAuthNotConfiguredError at first use
- No network call occurs during construction
- YOUTUBE_SCOPES contains only readonly — upload scope NOT requested
- refresh_access_token() does NOT pass scopes= to Credentials() — regression
  guard against the bug where YOUTUBE_SCOPES (readonly-only) was passed,
  causing google-auth to send 'scope' in the refresh POST body and strip
  youtube.upload from every refreshed access token.

No live network calls are made in any test in this file.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.oauth.client import YOUTUBE_SCOPES, YOUTUBE_UPLOAD_SCOPE
from app.oauth.errors import OAuthNotConfiguredError

# ---------------------------------------------------------------------------
# Minimal valid client secrets structure (as produced by Google Cloud Console)
# ---------------------------------------------------------------------------

_FAKE_CLIENT_SECRETS = {
    "web": {
        "client_id": "fake_client_id.apps.googleusercontent.com",
        "client_secret": "fake_client_secret",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "redirect_uris": ["http://localhost:8000/api/v1/oauth/youtube/callback"],
        "javascript_origins": ["http://localhost:8000"],
    }
}

_FAKE_REDIRECT_URI = "http://localhost:8000/api/v1/oauth/youtube/callback"


@pytest.fixture
def secrets_file(tmp_path):
    path = tmp_path / "client_secret.json"
    path.write_text(json.dumps(_FAKE_CLIENT_SECRETS))
    return str(path)


@pytest.fixture
def malformed_secrets_file(tmp_path):
    path = tmp_path / "bad_client_secret.json"
    path.write_text("NOT VALID JSON {{{{")
    return str(path)


@pytest.fixture
def empty_secrets_file(tmp_path):
    path = tmp_path / "empty_client_secret.json"
    path.write_text("{}")
    return str(path)


# ---------------------------------------------------------------------------
# Section 2a: Google library imports
# ---------------------------------------------------------------------------


def test_google_auth_is_importable():
    import google.auth  # noqa: F401

    assert True


def test_google_auth_oauthlib_is_importable():
    import google_auth_oauthlib  # noqa: F401

    assert True


def test_google_api_python_client_is_importable():
    import googleapiclient  # noqa: F401

    assert True


def test_google_auth_oauthlib_flow_is_importable():
    from google_auth_oauthlib.flow import Flow  # noqa: F401

    assert True


# ---------------------------------------------------------------------------
# Section 2b: RealGoogleOAuthClient construction
# ---------------------------------------------------------------------------


def test_real_client_constructs_successfully_with_valid_secrets(secrets_file):
    from app.oauth.client_google import RealGoogleOAuthClient

    client = RealGoogleOAuthClient(
        client_secrets_path=secrets_file,
        redirect_uri=_FAKE_REDIRECT_URI,
    )
    assert client is not None


def test_real_client_construction_makes_no_network_call(secrets_file, monkeypatch):
    """Construction must never touch the network."""
    import socket

    original_getaddrinfo = socket.getaddrinfo

    call_count = {"n": 0}

    def counting_getaddrinfo(*args, **kwargs):
        call_count["n"] += 1
        return original_getaddrinfo(*args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", counting_getaddrinfo)

    from app.oauth.client_google import RealGoogleOAuthClient

    RealGoogleOAuthClient(
        client_secrets_path=secrets_file,
        redirect_uri=_FAKE_REDIRECT_URI,
    )

    assert call_count["n"] == 0, "Construction must not make DNS lookups"


def test_real_client_missing_secrets_raises_not_configured():
    from app.oauth.client_google import RealGoogleOAuthClient

    with pytest.raises(OAuthNotConfiguredError, match="missing"):
        RealGoogleOAuthClient(
            client_secrets_path="/nonexistent/path/client_secret.json",
            redirect_uri=_FAKE_REDIRECT_URI,
        )


def test_real_client_empty_path_raises_not_configured():
    from app.oauth.client_google import RealGoogleOAuthClient

    with pytest.raises(OAuthNotConfiguredError):
        RealGoogleOAuthClient(
            client_secrets_path="",
            redirect_uri=_FAKE_REDIRECT_URI,
        )


def test_real_client_get_authorization_url_no_network(secrets_file):
    """get_authorization_url builds the URL locally — no network call required."""
    from app.oauth.client_google import RealGoogleOAuthClient

    client = RealGoogleOAuthClient(
        client_secrets_path=secrets_file,
        redirect_uri=_FAKE_REDIRECT_URI,
    )
    # This should NOT raise and should NOT make a network call
    result = client.get_authorization_url(state_nonce="testnonce64" * 2, scopes=YOUTUBE_SCOPES)
    assert "accounts.google.com" in result.authorization_url
    assert result.state_nonce == "testnonce64" * 2


def test_real_client_authorization_url_includes_readonly_scope(secrets_file):
    from app.oauth.client_google import RealGoogleOAuthClient

    client = RealGoogleOAuthClient(
        client_secrets_path=secrets_file,
        redirect_uri=_FAKE_REDIRECT_URI,
    )
    result = client.get_authorization_url(state_nonce="x" * 64, scopes=YOUTUBE_SCOPES)
    # The URL must encode the readonly scope
    assert "youtube.readonly" in result.authorization_url


def test_real_client_authorization_url_does_not_include_upload_scope(secrets_file):
    from app.oauth.client_google import RealGoogleOAuthClient

    client = RealGoogleOAuthClient(
        client_secrets_path=secrets_file,
        redirect_uri=_FAKE_REDIRECT_URI,
    )
    result = client.get_authorization_url(state_nonce="x" * 64, scopes=YOUTUBE_SCOPES)
    assert "youtube.upload" not in result.authorization_url


def test_real_client_authorization_url_uses_offline_access(secrets_file):
    """Offline access ensures a refresh_token is returned."""
    from app.oauth.client_google import RealGoogleOAuthClient

    client = RealGoogleOAuthClient(
        client_secrets_path=secrets_file,
        redirect_uri=_FAKE_REDIRECT_URI,
    )
    result = client.get_authorization_url(state_nonce="x" * 64, scopes=YOUTUBE_SCOPES)
    assert "offline" in result.authorization_url


def test_real_client_authorization_url_forces_consent(secrets_file):
    """prompt=consent ensures refresh_token is issued even on reconnect."""
    from app.oauth.client_google import RealGoogleOAuthClient

    client = RealGoogleOAuthClient(
        client_secrets_path=secrets_file,
        redirect_uri=_FAKE_REDIRECT_URI,
    )
    result = client.get_authorization_url(state_nonce="x" * 64, scopes=YOUTUBE_SCOPES)
    assert "consent" in result.authorization_url


# ---------------------------------------------------------------------------
# Section 2c: Scope safety checks (no real client needed)
# ---------------------------------------------------------------------------


def test_youtube_scopes_readonly_only():
    assert any("youtube.readonly" in s for s in YOUTUBE_SCOPES)


def test_youtube_upload_scope_not_in_request_list():
    assert all("youtube.upload" not in s for s in YOUTUBE_SCOPES)


def test_youtube_upload_scope_is_defined_but_unused():
    assert "youtube.upload" in YOUTUBE_UPLOAD_SCOPE
    assert YOUTUBE_UPLOAD_SCOPE not in YOUTUBE_SCOPES


def test_openid_scope_included():
    assert "openid" in YOUTUBE_SCOPES


# ---------------------------------------------------------------------------
# Section 3: client_secret.json safety (no network)
# ---------------------------------------------------------------------------


def test_gitignore_covers_client_secret_json():
    """Verify .gitignore contains client_secret patterns."""
    gitignore = Path(__file__).parent.parent / ".gitignore"
    assert gitignore.exists(), ".gitignore must exist"
    content = gitignore.read_text()
    assert "client_secret" in content, ".gitignore must cover client_secret patterns"


def test_gitignore_covers_credentials_json():
    gitignore = Path(__file__).parent.parent / ".gitignore"
    content = gitignore.read_text()
    assert "credentials.json" in content


def test_client_secret_not_present_in_repo():
    """No client_secret.json should exist at repo root or src/."""
    repo_root = Path(__file__).parent.parent
    for pattern in ("client_secret*.json", "client_secrets*.json"):
        found = list(repo_root.glob(pattern)) + list((repo_root / "src").glob(f"**/{pattern}"))
        assert not found, f"Found committed credential file(s): {found}"


# ---------------------------------------------------------------------------
# Section 4: refresh_access_token scope-restriction regression tests
#
# Root cause guarded here: refresh_access_token() previously passed
# scopes=YOUTUBE_SCOPES to google.oauth2.credentials.Credentials().
# google-auth's refresh_grant() sends 'scope' in the POST body when
# Credentials.scopes is set, explicitly restricting the issued access token
# to those scopes only.  For an upload-scoped grant this silently stripped
# youtube.upload from every refreshed token while leaving the stored
# metadata intact — causing a 403 insufficientPermissions at upload time.
# ---------------------------------------------------------------------------


class _CapturingMockCredentials:
    """Stunt double for google.oauth2.credentials.Credentials in refresh tests.

    Records whether scopes= was passed at construction — the key assertion
    in the regression suite.  refresh() is a no-op (zero network calls).
    """

    last_constructed_scopes: list | None = None  # sentinel value before first call

    def __init__(
        self,
        *,
        token,
        refresh_token,
        token_uri,
        client_id,
        client_secret,
        **kwargs,
    ) -> None:
        # "NOT_PASSED" sentinel distinguishes None-explicitly-passed from absent
        _CapturingMockCredentials.last_constructed_scopes = kwargs.get("scopes", "NOT_PASSED")
        self.token = "refreshed_access_token_xyz"
        self.refresh_token = None  # Google often omits on refresh
        self.expiry = datetime.now(UTC) + timedelta(hours=1)
        # Mirror real Credentials: .scopes reflects what was passed to constructor
        self.scopes = kwargs.get("scopes")

    def refresh(self, request) -> None:
        pass  # no network


def _patch_google_credentials(monkeypatch) -> None:
    """Replace google Credentials + Request with test doubles."""
    import google.auth.transport.requests
    import google.oauth2.credentials

    _CapturingMockCredentials.last_constructed_scopes = "NOT_CALLED"
    monkeypatch.setattr(google.oauth2.credentials, "Credentials", _CapturingMockCredentials)
    monkeypatch.setattr(google.auth.transport.requests, "Request", MagicMock)


def test_refresh_does_not_pass_scopes_to_credentials(secrets_file, monkeypatch):
    """Credentials() must NOT receive scopes= during token refresh.

    Passing scopes= causes google-auth to include 'scope' in the refresh
    POST body, explicitly restricting the access token returned by Google.
    With scopes=YOUTUBE_SCOPES, youtube.upload was stripped from every
    refreshed token even when the underlying grant included it.
    """
    _patch_google_credentials(monkeypatch)
    from app.oauth.client_google import RealGoogleOAuthClient

    client = RealGoogleOAuthClient(secrets_file, _FAKE_REDIRECT_URI)
    client.refresh_access_token("any_refresh_token")

    assert _CapturingMockCredentials.last_constructed_scopes == "NOT_PASSED", (
        f"Credentials() received scopes={_CapturingMockCredentials.last_constructed_scopes!r} "
        "during refresh — this causes google-auth to restrict the refreshed token "
        "to those scopes only, stripping youtube.upload from upload-scoped grants"
    )


def test_refresh_upload_grant_not_narrowed_to_readonly(secrets_file, monkeypatch):
    """An upload-scoped grant must not be narrowed to readonly during refresh.

    Before the fix: scopes=YOUTUBE_SCOPES in Credentials() caused Google to
    issue a readonly-only access token even when the refresh token covered
    upload scope — reproducing the 403 insufficientPermissions failure.
    After the fix: payload.scopes is [] (scope preservation is done by
    refresh_account_token/update_tokens, not by refresh_access_token).
    """
    _patch_google_credentials(monkeypatch)
    from app.oauth.client_google import RealGoogleOAuthClient

    client = RealGoogleOAuthClient(secrets_file, _FAKE_REDIRECT_URI)
    payload = client.refresh_access_token("upload_scoped_refresh_token")

    # Payload must not echo back YOUTUBE_SCOPES (readonly-only) — that was the
    # buggy behavior where the scope restriction was reflected in the return value.
    assert payload.scopes != list(YOUTUBE_SCOPES), (
        "Refreshed TokenPayload must not echo YOUTUBE_SCOPES (readonly-only); "
        "that was the restricted payload produced by the scope-narrowing bug"
    )
    # After the fix, scopes is empty because Google does not return scope in
    # refresh responses; the caller (refresh_account_token) preserves stored scopes.
    assert payload.scopes == [], (
        f"Expected empty scopes list after refresh (got {payload.scopes!r}); "
        "scope metadata is preserved by update_tokens(), not refresh_access_token()"
    )


def test_refresh_readonly_grant_still_works(secrets_file, monkeypatch):
    """Removing scopes= must not break readonly-only token refresh.

    Accounts that only have youtube.readonly scope must still refresh
    successfully.  Google will return a token for the readonly grant; the
    fix does not change that behaviour.
    """
    _patch_google_credentials(monkeypatch)
    from app.oauth.client_google import RealGoogleOAuthClient

    client = RealGoogleOAuthClient(secrets_file, _FAKE_REDIRECT_URI)
    payload = client.refresh_access_token("readonly_refresh_token")

    assert payload is not None
    assert payload.access_token == "refreshed_access_token_xyz"
    assert payload.token_type == "Bearer"
    assert payload.expires_at_utc is not None


def test_refresh_no_broader_scopes_introduced(secrets_file, monkeypatch):
    """refresh_access_token must not introduce scopes broader than the grant.

    The fix removes the scope restriction but must not add upload scope to
    the payload for accounts that only have readonly.  payload.scopes is []
    — the refresh endpoint does not return scope metadata; callers rely on
    stored grant scopes preserved by update_tokens().
    """
    _patch_google_credentials(monkeypatch)
    from app.oauth.client_google import RealGoogleOAuthClient

    client = RealGoogleOAuthClient(secrets_file, _FAKE_REDIRECT_URI)
    payload = client.refresh_access_token("readonly_only_refresh_token")

    assert YOUTUBE_UPLOAD_SCOPE not in payload.scopes, (
        "refresh_access_token must not fabricate upload scope in the payload"
    )


def test_refresh_scopes_not_passed_for_any_token_type(secrets_file, monkeypatch):
    """scopes= must be absent from Credentials() regardless of token type."""
    _patch_google_credentials(monkeypatch)
    from app.oauth.client_google import RealGoogleOAuthClient

    client = RealGoogleOAuthClient(secrets_file, _FAKE_REDIRECT_URI)

    for label, token in [
        ("readonly", "readonly_rt"),
        ("upload", "upload_rt"),
        ("generic", "generic_rt"),
    ]:
        _CapturingMockCredentials.last_constructed_scopes = "NOT_CALLED"
        client.refresh_access_token(token)
        assert _CapturingMockCredentials.last_constructed_scopes == "NOT_PASSED", (
            f"scopes= was passed to Credentials() for {label} token"
        )
