"""Tests for GoogleOAuthClient Protocol and FakeGoogleOAuthClient."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.oauth.client import (
    YOUTUBE_SCOPES,
    AuthorizationURLResult,
    ChannelIdentity,
    FakeGoogleOAuthClient,
    GoogleOAuthClient,
    TokenPayload,
)
from app.oauth.errors import OAuthChannelVerificationError, OAuthCodeExchangeError

# ---------------------------------------------------------------------------
# Protocol check
# ---------------------------------------------------------------------------


def test_fake_client_satisfies_protocol():
    client = FakeGoogleOAuthClient()
    assert isinstance(client, GoogleOAuthClient)


# ---------------------------------------------------------------------------
# get_authorization_url
# ---------------------------------------------------------------------------


def test_authorization_url_contains_state_nonce():
    client = FakeGoogleOAuthClient()
    result = client.get_authorization_url(state_nonce="testnonce123", scopes=YOUTUBE_SCOPES)
    assert isinstance(result, AuthorizationURLResult)
    assert "testnonce123" in result.authorization_url
    assert result.state_nonce == "testnonce123"


def test_authorization_url_is_google_domain():
    client = FakeGoogleOAuthClient()
    result = client.get_authorization_url(state_nonce="n", scopes=YOUTUBE_SCOPES)
    assert "accounts.google.com" in result.authorization_url


# ---------------------------------------------------------------------------
# exchange_code
# ---------------------------------------------------------------------------


def test_exchange_code_returns_token_payload():
    client = FakeGoogleOAuthClient(
        fake_access_token="test_access", fake_refresh_token="test_refresh"
    )
    token = client.exchange_code("auth_code_123", "state_nonce_abc")
    assert isinstance(token, TokenPayload)
    assert token.access_token == "test_access"
    assert token.refresh_token == "test_refresh"
    assert token.expires_at_utc > datetime.now(UTC)
    assert "youtube.readonly" in " ".join(token.scopes)


def test_exchange_code_raises_on_configured_failure():
    client = FakeGoogleOAuthClient(fail_exchange=OAuthCodeExchangeError("network error"))
    with pytest.raises(OAuthCodeExchangeError, match="network error"):
        client.exchange_code("code", "state")


# ---------------------------------------------------------------------------
# refresh_access_token
# ---------------------------------------------------------------------------


def test_refresh_returns_new_access_token():
    client = FakeGoogleOAuthClient(fake_access_token="original")
    token = client.refresh_access_token("refresh_token_xxx")
    assert token.access_token == "original_refreshed"
    # Google often omits refresh_token on refresh
    assert token.refresh_token is None


# ---------------------------------------------------------------------------
# revoke_token
# ---------------------------------------------------------------------------


def test_revoke_records_call():
    client = FakeGoogleOAuthClient()
    client.revoke_token("token_to_revoke")
    assert "token_to_revoke" in client.revoke_calls


def test_revoke_raises_on_configured_failure():
    from app.oauth.errors import OAuthProviderError

    client = FakeGoogleOAuthClient(fail_revoke=OAuthProviderError("revoke failed"))
    with pytest.raises(OAuthProviderError):
        client.revoke_token("token")


# ---------------------------------------------------------------------------
# get_channel_identity
# ---------------------------------------------------------------------------


def test_get_channel_identity_returns_identity():
    client = FakeGoogleOAuthClient(
        fake_channel_id="UCtest123",
        fake_channel_title="My Test Channel",
    )
    identity = client.get_channel_identity("access_token")
    assert isinstance(identity, ChannelIdentity)
    assert identity.channel_id == "UCtest123"
    assert identity.channel_title == "My Test Channel"
    assert isinstance(identity.verified_at_utc, datetime)


def test_get_channel_identity_raises_on_configured_failure():
    client = FakeGoogleOAuthClient(fail_identity=OAuthChannelVerificationError("no channel"))
    with pytest.raises(OAuthChannelVerificationError):
        client.get_channel_identity("access_token")


# ---------------------------------------------------------------------------
# YOUTUBE_SCOPES sanity
# ---------------------------------------------------------------------------


def test_youtube_scopes_contains_readonly():
    assert any("youtube.readonly" in s for s in YOUTUBE_SCOPES)


def test_youtube_scopes_does_not_include_upload():
    assert all("youtube.upload" not in s for s in YOUTUBE_SCOPES)
