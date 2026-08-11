"""Tests for YouTube private upload capability.

All Google/YouTube API calls are mocked. No real network calls. No real uploads.

Coverage:
  - Upload scope present / missing
  - Disconnected account rejected
  - Channel identity mismatch rejected
  - Cross-workspace / cross-account isolation
  - Live publishing gate (ACE_PUBLISHING_LIVE_ENABLED)
  - Non-existent / empty / wrong-type artifact rejected
  - Privacy other than 'private' rejected in provider
  - Successful private upload (mocked client)
  - YouTube video ID returned; no OAuth tokens in result
  - Retryable vs non-retryable error classification
  - has_upload_scope helper
  - start_youtube_upload_oauth scope upgrade flow
  - YOUTUBE_UPLOAD_SCOPES constant
  - FakeGoogleOAuthClient granted_scopes parameter
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest

from app.control_plane import orchestrator as cp_orch
from app.control_plane import repository as repo
from app.control_plane.models import PlatformAccountDraft
from app.core.database import open_db
from app.oauth.client import (
    YOUTUBE_SCOPES,
    YOUTUBE_UPLOAD_SCOPE,
    YOUTUBE_UPLOAD_SCOPES,
    FakeGoogleOAuthClient,
)
from app.oauth.state import InMemoryOAuthStateStore, reset_state_store, set_state_store
from app.oauth.store import (
    LocalFileTokenStore,
    StoredTokenPayload,
    reset_token_store,
    set_token_store,
)
from app.publishing.errors import (
    LivePublishingNotEnabledError,
    ProviderUploadError,
    PublishingValidationError,
    UploadAccountMismatchError,
    UploadFileError,
    UploadScopeNotGrantedError,
)
from app.publishing.protocol import UploadPackage
from app.publishing.providers.youtube import (
    FakeYouTubeAPIClient,
    YouTubePublishingProvider,
)
from app.publishing.upload_gate import (
    check_live_publishing_gate,
    check_upload_scope,
    validate_video_file,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path: Any) -> sqlite3.Connection:
    conn = open_db(tmp_path / "test.db")
    repo.ensure_platform(conn, "plt-yt-1", "youtube", "YouTube")
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def fresh_state_store() -> Any:
    store = InMemoryOAuthStateStore()
    set_state_store(store)
    yield store
    reset_state_store()


@pytest.fixture()
def token_store(tmp_path: Any) -> Any:
    store = LocalFileTokenStore(tmp_path / "tokens")
    set_token_store(store)
    yield store
    reset_token_store()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_connected_account(
    db: sqlite3.Connection,
    token_store: LocalFileTokenStore,
    *,
    channel_id_suffix: str = "",
    granted_scopes: list[str] | None = None,
) -> tuple[str, str, str, str]:
    """Provision workspace → channel → platform account → complete OAuth.

    Returns (workspace_id, channel_id, account_id, registered_channel_id).
    """
    suffix = channel_id_suffix
    ws = cp_orch.provision_workspace(db, name=f"WS{suffix}", slug=f"ws{suffix}", actor="test")
    ch = cp_orch.provision_channel(
        db, workspace_id=ws.id, name=f"Ch{suffix}", slug=f"ch{suffix}", actor="test"
    )

    platform = repo.get_platform_by_key(db, "youtube")
    acct_id = str(uuid.uuid4())
    registered_channel_id = f"UCtest_{suffix or 'default'}"

    draft = PlatformAccountDraft(
        id=acct_id,
        channel_id=ch.id,
        platform_id=platform.id,
        platform_key="youtube",
        external_account_id=f"pending:{acct_id}",
        display_name=f"Test Channel {suffix}",
        actor="test",
        status="connected",
    )
    acct = repo.create_platform_account(db, draft)

    scopes = granted_scopes if granted_scopes is not None else YOUTUBE_SCOPES
    oauth_client = FakeGoogleOAuthClient(
        fake_channel_id=registered_channel_id,
        granted_scopes=scopes,
    )

    from app.oauth.flow import complete_youtube_oauth, start_youtube_oauth

    r = start_youtube_oauth(
        db,
        account_id=acct.id,
        user_id="u1",
        workspace_id=ws.id,
        channel_id=ch.id,
        oauth_client=oauth_client,
    )
    complete_youtube_oauth(
        db,
        code="code",
        state_nonce=r.state_nonce,
        oauth_client=oauth_client,
        token_store=token_store,
    )

    return ws.id, ch.id, acct.id, registered_channel_id


def _stored_token(
    *,
    account_id: str = "acc_1",
    scopes: list[str] | None = None,
    expired: bool = False,
) -> StoredTokenPayload:
    expires = (
        datetime.now(UTC) - timedelta(hours=1)
        if expired
        else datetime.now(UTC) + timedelta(hours=1)
    )
    return StoredTokenPayload(
        account_id=account_id,
        access_token="fake_access_token",
        refresh_token="fake_refresh_token",
        token_type="Bearer",
        expires_at_utc=expires,
        scopes=scopes if scopes is not None else YOUTUBE_SCOPES,
        google_sub="100000000001",
        stored_at_utc=datetime.now(UTC),
    )


def _package(**kw: Any) -> UploadPackage:
    defaults = dict(
        plan_id=1,
        file_path="/tmp/video.mp4",
        file_sha256="abc123",
        title="Test Private Video",
        description="Upload test.",
        tags=["test"],
        language="en",
        category="22",
        visibility="private",
        made_for_kids=False,
        scheduled_at=None,
        captions_path=None,
        playlist_id=None,
    )
    defaults.update(kw)
    return UploadPackage(**defaults)


def _get_cred_ref(conn: sqlite3.Connection, account_id: str) -> str:
    cur = conn.execute(
        """
        SELECT cp.external_ref
        FROM cp_platform_accounts a
        JOIN cp_credential_profiles cp ON a.credential_profile_id = cp.id
        WHERE a.id = ?
        """,
        (account_id,),
    )
    row = cur.fetchone()
    assert row is not None, f"No credential profile for account {account_id}"
    return row[0]


def _upgrade_token_to_upload_scope(conn: sqlite3.Connection, account_id: str) -> None:
    """Rewrite the token file to include YOUTUBE_UPLOAD_SCOPES in place."""
    from app.oauth.store import get_token_store

    store = get_token_store()
    cred_ref = _get_cred_ref(conn, account_id)
    stored = store.read(cred_ref)
    store.write(
        account_id=account_id,
        access_token=stored.access_token,
        refresh_token=stored.refresh_token,
        token_type=stored.token_type,
        expires_at_utc=datetime.now(UTC) + timedelta(hours=1),
        scopes=YOUTUBE_UPLOAD_SCOPES,
        google_sub=stored.google_sub,
    )


# ---------------------------------------------------------------------------
# 1. YOUTUBE_UPLOAD_SCOPES constant
# ---------------------------------------------------------------------------


class TestUploadScopesConstant:
    def test_includes_readonly(self) -> None:
        assert "https://www.googleapis.com/auth/youtube.readonly" in YOUTUBE_UPLOAD_SCOPES

    def test_includes_upload(self) -> None:
        assert YOUTUBE_UPLOAD_SCOPE in YOUTUBE_UPLOAD_SCOPES

    def test_is_superset_of_readonly_scopes(self) -> None:
        for scope in YOUTUBE_SCOPES:
            assert scope in YOUTUBE_UPLOAD_SCOPES

    def test_upload_scope_string(self) -> None:
        assert YOUTUBE_UPLOAD_SCOPE == "https://www.googleapis.com/auth/youtube.upload"


# ---------------------------------------------------------------------------
# 2. FakeGoogleOAuthClient granted_scopes parameter
# ---------------------------------------------------------------------------


class TestFakeClientGrantedScopes:
    def test_default_scopes_readonly_only(self) -> None:
        client = FakeGoogleOAuthClient()
        token = client.exchange_code("code", "nonce")
        assert YOUTUBE_UPLOAD_SCOPE not in token.scopes
        assert "https://www.googleapis.com/auth/youtube.readonly" in token.scopes

    def test_granted_scopes_override(self) -> None:
        client = FakeGoogleOAuthClient(granted_scopes=YOUTUBE_UPLOAD_SCOPES)
        token = client.exchange_code("code", "nonce")
        assert YOUTUBE_UPLOAD_SCOPE in token.scopes

    def test_refresh_preserves_granted_scopes(self) -> None:
        client = FakeGoogleOAuthClient(granted_scopes=YOUTUBE_UPLOAD_SCOPES)
        refreshed = client.refresh_access_token("refresh_tok")
        assert YOUTUBE_UPLOAD_SCOPE in refreshed.scopes

    def test_default_refresh_scopes_readonly_only(self) -> None:
        client = FakeGoogleOAuthClient()
        refreshed = client.refresh_access_token("refresh_tok")
        assert YOUTUBE_UPLOAD_SCOPE not in refreshed.scopes


# ---------------------------------------------------------------------------
# 3. check_upload_scope
# ---------------------------------------------------------------------------


class TestCheckUploadScope:
    def test_scope_present_passes(self) -> None:
        token = _stored_token(scopes=YOUTUBE_UPLOAD_SCOPES)
        check_upload_scope(token)  # must not raise

    def test_readonly_only_raises(self) -> None:
        token = _stored_token(scopes=YOUTUBE_SCOPES)
        with pytest.raises(UploadScopeNotGrantedError, match="youtube.upload"):
            check_upload_scope(token)

    def test_empty_scopes_raises(self) -> None:
        token = _stored_token(scopes=[])
        with pytest.raises(UploadScopeNotGrantedError):
            check_upload_scope(token)

    def test_error_includes_granted_scopes(self) -> None:
        token = _stored_token(scopes=["openid"])
        with pytest.raises(UploadScopeNotGrantedError) as exc_info:
            check_upload_scope(token)
        assert "openid" in str(exc_info.value)

    def test_upload_scope_not_subclass_of_provider_error(self) -> None:
        assert not issubclass(UploadScopeNotGrantedError, ProviderUploadError)


# ---------------------------------------------------------------------------
# 4. Live publishing gate
# ---------------------------------------------------------------------------


class TestLivePublishingGate:
    def test_disabled_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ACE_PUBLISHING_LIVE_ENABLED", "")
        from app.core.config import reset_config

        reset_config()
        try:
            with pytest.raises(LivePublishingNotEnabledError, match="ACE_PUBLISHING_LIVE_ENABLED"):
                check_live_publishing_gate()
        finally:
            reset_config()

    def test_enabled_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ACE_PUBLISHING_LIVE_ENABLED", "true")
        from app.core.config import reset_config

        reset_config()
        try:
            check_live_publishing_gate()  # must not raise
        finally:
            reset_config()

    def test_default_is_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ACE_PUBLISHING_LIVE_ENABLED", raising=False)
        from app.core.config import reset_config

        reset_config()
        try:
            with pytest.raises(LivePublishingNotEnabledError):
                check_live_publishing_gate()
        finally:
            reset_config()

    def test_not_retryable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert not issubclass(LivePublishingNotEnabledError, ProviderUploadError)


# ---------------------------------------------------------------------------
# 5. validate_video_file
# ---------------------------------------------------------------------------


class TestValidateVideoFile:
    def test_valid_mp4(self, tmp_path: Any) -> None:
        f = tmp_path / "video.mp4"
        f.write_bytes(b"\x00" * 100)
        validate_video_file(str(f))  # must not raise

    def test_valid_mov(self, tmp_path: Any) -> None:
        f = tmp_path / "video.mov"
        f.write_bytes(b"\x00" * 100)
        validate_video_file(str(f))

    def test_valid_mkv(self, tmp_path: Any) -> None:
        f = tmp_path / "video.mkv"
        f.write_bytes(b"\x00" * 100)
        validate_video_file(str(f))

    def test_valid_webm(self, tmp_path: Any) -> None:
        f = tmp_path / "video.webm"
        f.write_bytes(b"\x00" * 100)
        validate_video_file(str(f))

    def test_nonexistent_raises(self) -> None:
        with pytest.raises(UploadFileError, match="not found"):
            validate_video_file("/nonexistent/path/video.mp4")

    def test_empty_file_raises(self, tmp_path: Any) -> None:
        f = tmp_path / "empty.mp4"
        f.write_bytes(b"")
        with pytest.raises(UploadFileError, match="empty"):
            validate_video_file(str(f))

    def test_pdf_raises(self, tmp_path: Any) -> None:
        f = tmp_path / "document.pdf"
        f.write_bytes(b"\x00" * 100)
        with pytest.raises(UploadFileError, match="supported video"):
            validate_video_file(str(f))

    def test_txt_raises(self, tmp_path: Any) -> None:
        f = tmp_path / "notes.txt"
        f.write_bytes(b"hello")
        with pytest.raises(UploadFileError):
            validate_video_file(str(f))

    def test_file_error_not_retryable(self) -> None:
        assert not issubclass(UploadFileError, ProviderUploadError)


# ---------------------------------------------------------------------------
# 6. Privacy hard-lock in YouTubePublishingProvider
# ---------------------------------------------------------------------------


class TestPrivacyLock:
    def test_private_accepted(self) -> None:
        provider = YouTubePublishingProvider(api_client=FakeYouTubeAPIClient())
        provider.upload(_package(visibility="private"))  # must not raise

    def test_public_rejected(self) -> None:
        provider = YouTubePublishingProvider(api_client=FakeYouTubeAPIClient())
        with pytest.raises(PublishingValidationError, match="locked to 'private'"):
            provider.upload(_package(visibility="public"))

    def test_unlisted_rejected(self) -> None:
        provider = YouTubePublishingProvider(api_client=FakeYouTubeAPIClient())
        with pytest.raises(PublishingValidationError, match="locked to 'private'"):
            provider.upload(_package(visibility="unlisted"))

    def test_privacy_check_is_in_upload_not_prepare(self) -> None:
        # prepare_package does not check privacy — only upload does
        provider = YouTubePublishingProvider(api_client=FakeYouTubeAPIClient())
        pkg = _package(visibility="unlisted")
        # prepare_package passes (privacy check is not there)
        provider.prepare_package(pkg)
        # but upload rejects
        with pytest.raises(PublishingValidationError):
            provider.upload(pkg)

    def test_publish_step_rejects_public(self) -> None:
        """publish() must also hard-lock to private — second-layer defense."""
        from app.publishing.protocol import UploadResult

        provider = YouTubePublishingProvider(api_client=FakeYouTubeAPIClient())
        fake_result = UploadResult(
            provider_video_id="yt_fake_001",
            provider_url="https://www.youtube.com/watch?v=yt_fake_001",
            provider_response={},
        )
        with pytest.raises(PublishingValidationError, match="locked to 'private'"):
            provider.publish(fake_result, visibility="public")

    def test_publish_step_accepts_private(self) -> None:
        provider = YouTubePublishingProvider(api_client=FakeYouTubeAPIClient())
        from app.publishing.protocol import UploadResult

        fake_result = UploadResult(
            provider_video_id="yt_fake_001",
            provider_url="https://www.youtube.com/watch?v=yt_fake_001",
            provider_response={},
        )
        result = provider.publish(fake_result, visibility="private")
        assert result.status in ("published", "scheduled")


# ---------------------------------------------------------------------------
# 7. Successful private upload (mocked)
# ---------------------------------------------------------------------------


class TestSuccessfulPrivateUpload:
    def test_upload_returns_video_id(self) -> None:
        provider = YouTubePublishingProvider(
            api_client=FakeYouTubeAPIClient(video_id="yt_priv_001")
        )
        result = provider.upload(_package())
        assert result.provider_video_id == "yt_priv_001"

    def test_upload_has_url(self) -> None:
        provider = YouTubePublishingProvider(
            api_client=FakeYouTubeAPIClient(video_id="yt_priv_001")
        )
        result = provider.upload(_package())
        assert result.provider_url is not None
        assert "yt_priv_001" in result.provider_url

    def test_no_token_fields_in_result(self) -> None:
        provider = YouTubePublishingProvider(api_client=FakeYouTubeAPIClient())
        result = provider.upload(_package())
        for key in ("access_token", "refresh_token", "token", "credential"):
            assert key not in result.provider_response

    def test_no_token_values_in_serialized_result(self) -> None:
        provider = YouTubePublishingProvider(api_client=FakeYouTubeAPIClient())
        result = provider.upload(_package())
        serialized = str(result)
        for sensitive in ("fake_access_token", "fake_refresh_token"):
            assert sensitive not in serialized

    def test_provider_name_is_youtube(self) -> None:
        provider = YouTubePublishingProvider(api_client=FakeYouTubeAPIClient())
        assert provider.provider_name == "youtube"


# ---------------------------------------------------------------------------
# 8. Retryable vs non-retryable error classification
# ---------------------------------------------------------------------------


class TestRetryClassification:
    def test_network_error_is_provider_upload_error(self) -> None:
        client = FakeYouTubeAPIClient(raise_on_insert=RuntimeError("connection reset"))
        provider = YouTubePublishingProvider(api_client=client)
        with pytest.raises(ProviderUploadError, match="connection reset"):
            provider.upload(_package())

    def test_scope_error_not_retryable(self) -> None:
        assert not issubclass(UploadScopeNotGrantedError, ProviderUploadError)

    def test_file_error_not_retryable(self) -> None:
        assert not issubclass(UploadFileError, ProviderUploadError)

    def test_live_gate_error_not_retryable(self) -> None:
        assert not issubclass(LivePublishingNotEnabledError, ProviderUploadError)

    def test_account_mismatch_not_retryable(self) -> None:
        assert not issubclass(UploadAccountMismatchError, ProviderUploadError)

    def test_privacy_error_not_retryable(self) -> None:
        assert not issubclass(PublishingValidationError, ProviderUploadError)


# ---------------------------------------------------------------------------
# 9. has_upload_scope helper
# ---------------------------------------------------------------------------


class TestHasUploadScope:
    def test_returns_false_when_readonly_only(
        self, db: sqlite3.Connection, token_store: LocalFileTokenStore
    ) -> None:
        from app.oauth.flow import has_upload_scope

        ws_id, ch_id, acc_id, _ = _make_connected_account(db, token_store)
        result = has_upload_scope(db, account_id=acc_id, workspace_id=ws_id, channel_id=ch_id)
        assert result is False

    def test_returns_true_after_scope_upgrade(
        self, db: sqlite3.Connection, token_store: LocalFileTokenStore
    ) -> None:
        from app.oauth.flow import has_upload_scope

        ws_id, ch_id, acc_id, _ = _make_connected_account(db, token_store)
        _upgrade_token_to_upload_scope(db, acc_id)
        assert has_upload_scope(db, account_id=acc_id, workspace_id=ws_id, channel_id=ch_id) is True

    def test_returns_false_for_nonexistent_account(self, db: sqlite3.Connection) -> None:
        from app.oauth.flow import has_upload_scope

        assert (
            has_upload_scope(
                db, account_id="no_such_account", workspace_id="ws_x", channel_id="ch_x"
            )
            is False
        )

    def test_returns_false_after_disconnect(
        self, db: sqlite3.Connection, token_store: LocalFileTokenStore
    ) -> None:
        from app.oauth.flow import disconnect_youtube_account, has_upload_scope

        ws_id, ch_id, acc_id, _ = _make_connected_account(db, token_store)
        _upgrade_token_to_upload_scope(db, acc_id)
        disconnect_youtube_account(
            db,
            account_id=acc_id,
            workspace_id=ws_id,
            channel_id=ch_id,
            actor="test",
            token_store=token_store,
        )
        result = has_upload_scope(db, account_id=acc_id, workspace_id=ws_id, channel_id=ch_id)
        assert result is False


# ---------------------------------------------------------------------------
# 10. start_youtube_upload_oauth (scope upgrade flow)
# ---------------------------------------------------------------------------


class TestStartYouTubeUploadOAuth:
    def test_returns_authorization_url(
        self, db: sqlite3.Connection, token_store: LocalFileTokenStore
    ) -> None:
        from app.oauth.flow import start_youtube_upload_oauth

        ws_id, ch_id, acc_id, _ = _make_connected_account(db, token_store)
        oauth_client = FakeGoogleOAuthClient(granted_scopes=YOUTUBE_UPLOAD_SCOPES)
        result = start_youtube_upload_oauth(
            db,
            account_id=acc_id,
            user_id="u1",
            workspace_id=ws_id,
            channel_id=ch_id,
            oauth_client=oauth_client,
        )
        assert result.authorization_url

    def test_requests_upload_scope(
        self, db: sqlite3.Connection, token_store: LocalFileTokenStore
    ) -> None:
        from app.oauth.flow import start_youtube_upload_oauth

        ws_id, ch_id, acc_id, _ = _make_connected_account(db, token_store)
        captured: list[list[str]] = []

        class SpyClient(FakeGoogleOAuthClient):
            def get_authorization_url(self, state_nonce: str, scopes: list[str]):
                captured.append(list(scopes))
                return super().get_authorization_url(state_nonce, scopes)

        start_youtube_upload_oauth(
            db,
            account_id=acc_id,
            user_id="u1",
            workspace_id=ws_id,
            channel_id=ch_id,
            oauth_client=SpyClient(),
        )
        assert len(captured) == 1
        assert YOUTUBE_UPLOAD_SCOPE in captured[0]

    def test_upgrade_uses_broader_scopes_than_readonly_flow(
        self, db: sqlite3.Connection, token_store: LocalFileTokenStore
    ) -> None:
        from app.oauth.flow import start_youtube_oauth, start_youtube_upload_oauth

        ws_id, ch_id, acc_id, _ = _make_connected_account(db, token_store)
        captured: dict[str, list[str]] = {}

        class SpyClient(FakeGoogleOAuthClient):
            def get_authorization_url(self, state_nonce: str, scopes: list[str]):
                captured[state_nonce] = list(scopes)
                return super().get_authorization_url(state_nonce, scopes)

        spy = SpyClient()

        reset_state_store()
        set_state_store(InMemoryOAuthStateStore())
        r1 = start_youtube_oauth(
            db,
            account_id=acc_id,
            user_id="u",
            workspace_id=ws_id,
            channel_id=ch_id,
            oauth_client=spy,
        )
        reset_state_store()
        set_state_store(InMemoryOAuthStateStore())
        r2 = start_youtube_upload_oauth(
            db,
            account_id=acc_id,
            user_id="u",
            workspace_id=ws_id,
            channel_id=ch_id,
            oauth_client=spy,
        )

        assert YOUTUBE_UPLOAD_SCOPE not in captured[r1.state_nonce]
        assert YOUTUBE_UPLOAD_SCOPE in captured[r2.state_nonce]

    def test_nonexistent_account_raises(self, db: sqlite3.Connection) -> None:
        from app.oauth.errors import OAuthAccountNotFoundError
        from app.oauth.flow import start_youtube_upload_oauth

        with pytest.raises(OAuthAccountNotFoundError):
            start_youtube_upload_oauth(
                db,
                account_id="no_such",
                user_id="u1",
                workspace_id="ws_x",
                channel_id="ch_x",
                oauth_client=FakeGoogleOAuthClient(),
            )


# ---------------------------------------------------------------------------
# 11. build_authenticated_youtube_provider — full gate
# ---------------------------------------------------------------------------


class TestBuildAuthenticatedProvider:
    def test_rejects_when_live_disabled(
        self,
        db: sqlite3.Connection,
        token_store: LocalFileTokenStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.core.config import reset_config
        from app.publishing.upload_gate import build_authenticated_youtube_provider

        monkeypatch.delenv("ACE_PUBLISHING_LIVE_ENABLED", raising=False)
        reset_config()
        try:
            ws_id, ch_id, acc_id, _ = _make_connected_account(db, token_store)
            with pytest.raises(LivePublishingNotEnabledError):
                build_authenticated_youtube_provider(
                    db,
                    account_id=acc_id,
                    workspace_id=ws_id,
                    channel_id=ch_id,
                    oauth_client=FakeGoogleOAuthClient(),
                    token_store=token_store,
                )
        finally:
            reset_config()

    def test_rejects_missing_upload_scope(
        self,
        db: sqlite3.Connection,
        token_store: LocalFileTokenStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.core.config import reset_config
        from app.publishing.upload_gate import build_authenticated_youtube_provider

        monkeypatch.setenv("ACE_PUBLISHING_LIVE_ENABLED", "true")
        reset_config()
        try:
            ws_id, ch_id, acc_id, _ = _make_connected_account(db, token_store)
            # Token has readonly only
            with pytest.raises(UploadScopeNotGrantedError):
                build_authenticated_youtube_provider(
                    db,
                    account_id=acc_id,
                    workspace_id=ws_id,
                    channel_id=ch_id,
                    oauth_client=FakeGoogleOAuthClient(),
                    token_store=token_store,
                )
        finally:
            reset_config()

    def test_rejects_channel_identity_mismatch(
        self,
        db: sqlite3.Connection,
        token_store: LocalFileTokenStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.core.config import reset_config
        from app.publishing.upload_gate import build_authenticated_youtube_provider

        monkeypatch.setenv("ACE_PUBLISHING_LIVE_ENABLED", "true")
        reset_config()
        try:
            ws_id, ch_id, acc_id, _ = _make_connected_account(db, token_store)
            _upgrade_token_to_upload_scope(db, acc_id)

            # Client returns DIFFERENT channel ID than registered
            wrong_client = FakeGoogleOAuthClient(
                fake_channel_id="UCwrong_channel_xyz",
                granted_scopes=YOUTUBE_UPLOAD_SCOPES,
            )
            with pytest.raises(UploadAccountMismatchError):
                build_authenticated_youtube_provider(
                    db,
                    account_id=acc_id,
                    workspace_id=ws_id,
                    channel_id=ch_id,
                    oauth_client=wrong_client,
                    token_store=token_store,
                )
        finally:
            reset_config()

    def test_succeeds_returns_provider(
        self,
        db: sqlite3.Connection,
        token_store: LocalFileTokenStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.core.config import reset_config
        from app.publishing.upload_gate import build_authenticated_youtube_provider

        monkeypatch.setenv("ACE_PUBLISHING_LIVE_ENABLED", "true")
        reset_config()
        try:
            ws_id, ch_id, acc_id, reg_ch_id = _make_connected_account(db, token_store)
            _upgrade_token_to_upload_scope(db, acc_id)

            good_client = FakeGoogleOAuthClient(
                fake_channel_id=reg_ch_id,
                granted_scopes=YOUTUBE_UPLOAD_SCOPES,
            )
            with patch(
                "app.publishing.upload_gate.RealYouTubeAPIClient",
                return_value=FakeYouTubeAPIClient(),
            ):
                provider = build_authenticated_youtube_provider(
                    db,
                    account_id=acc_id,
                    workspace_id=ws_id,
                    channel_id=ch_id,
                    oauth_client=good_client,
                    token_store=token_store,
                )
            assert isinstance(provider, YouTubePublishingProvider)
        finally:
            reset_config()

    def test_returned_provider_can_upload(
        self,
        db: sqlite3.Connection,
        token_store: LocalFileTokenStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.core.config import reset_config
        from app.publishing.upload_gate import build_authenticated_youtube_provider

        monkeypatch.setenv("ACE_PUBLISHING_LIVE_ENABLED", "true")
        reset_config()
        try:
            ws_id, ch_id, acc_id, reg_ch_id = _make_connected_account(db, token_store)
            _upgrade_token_to_upload_scope(db, acc_id)

            good_client = FakeGoogleOAuthClient(
                fake_channel_id=reg_ch_id,
                granted_scopes=YOUTUBE_UPLOAD_SCOPES,
            )
            with patch(
                "app.publishing.upload_gate.RealYouTubeAPIClient",
                return_value=FakeYouTubeAPIClient(video_id="yt_gate_vid"),
            ):
                provider = build_authenticated_youtube_provider(
                    db,
                    account_id=acc_id,
                    workspace_id=ws_id,
                    channel_id=ch_id,
                    oauth_client=good_client,
                    token_store=token_store,
                )
            result = provider.upload(_package())
            assert result.provider_video_id == "yt_gate_vid"
        finally:
            reset_config()


# ---------------------------------------------------------------------------
# 12. Cross-account / cross-workspace isolation
# ---------------------------------------------------------------------------


class TestIsolation:
    def test_account_a_scope_independent_of_account_b(
        self,
        db: sqlite3.Connection,
        token_store: LocalFileTokenStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.core.config import reset_config
        from app.oauth.flow import has_upload_scope
        from app.publishing.upload_gate import build_authenticated_youtube_provider

        monkeypatch.setenv("ACE_PUBLISHING_LIVE_ENABLED", "true")
        reset_config()
        try:
            ws_a, ch_a, acc_a, _ = _make_connected_account(db, token_store, channel_id_suffix="aa")
            ws_b, ch_b, acc_b, reg_ch_b = _make_connected_account(
                db, token_store, channel_id_suffix="bb"
            )

            # Only upgrade B's token
            _upgrade_token_to_upload_scope(db, acc_b)

            # A still has readonly only
            assert not has_upload_scope(db, account_id=acc_a, workspace_id=ws_a, channel_id=ch_a)
            # B has upload
            assert has_upload_scope(db, account_id=acc_b, workspace_id=ws_b, channel_id=ch_b)

            # A cannot use B's account_id for upload (wrong channel)
            from app.oauth.errors import OAuthAccountNotFoundError as _AccountNotFound

            with pytest.raises(_AccountNotFound):
                build_authenticated_youtube_provider(
                    db,
                    account_id=acc_b,
                    workspace_id=ws_a,
                    channel_id=ch_a,
                    oauth_client=FakeGoogleOAuthClient(
                        fake_channel_id=reg_ch_b,
                        granted_scopes=YOUTUBE_UPLOAD_SCOPES,
                    ),
                    token_store=token_store,
                )
        finally:
            reset_config()

    def test_wrong_channel_id_raises(
        self,
        db: sqlite3.Connection,
        token_store: LocalFileTokenStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The gate validates channel_id; workspace isolation is enforced at the API layer."""
        from app.core.config import reset_config
        from app.oauth.errors import OAuthAccountNotFoundError
        from app.publishing.upload_gate import build_authenticated_youtube_provider

        monkeypatch.setenv("ACE_PUBLISHING_LIVE_ENABLED", "true")
        reset_config()
        try:
            ws_id, ch_id, acc_id, reg_ch_id = _make_connected_account(
                db, token_store, channel_id_suffix="iso"
            )
            _upgrade_token_to_upload_scope(db, acc_id)

            good_client = FakeGoogleOAuthClient(
                fake_channel_id=reg_ch_id,
                granted_scopes=YOUTUBE_UPLOAD_SCOPES,
            )
            # Wrong channel_id for the account → rejected
            with pytest.raises(OAuthAccountNotFoundError):
                build_authenticated_youtube_provider(
                    db,
                    account_id=acc_id,
                    workspace_id=ws_id,
                    channel_id="wrong_channel_entirely",
                    oauth_client=good_client,
                    token_store=token_store,
                )
        finally:
            reset_config()


# ---------------------------------------------------------------------------
# 13. Disconnected account rejected
# ---------------------------------------------------------------------------


class TestDisconnectedAccountRejected:
    def test_disconnected_account_cannot_upload(
        self,
        db: sqlite3.Connection,
        token_store: LocalFileTokenStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.core.config import reset_config
        from app.oauth.flow import disconnect_youtube_account
        from app.publishing.upload_gate import build_authenticated_youtube_provider

        monkeypatch.setenv("ACE_PUBLISHING_LIVE_ENABLED", "true")
        reset_config()
        try:
            ws_id, ch_id, acc_id, _ = _make_connected_account(db, token_store)
            _upgrade_token_to_upload_scope(db, acc_id)

            disconnect_youtube_account(
                db,
                account_id=acc_id,
                workspace_id=ws_id,
                channel_id=ch_id,
                actor="test",
                token_store=token_store,
            )

            from app.oauth.errors import OAuthTokenStoreError as _TokenStoreError

            with pytest.raises(_TokenStoreError):
                build_authenticated_youtube_provider(
                    db,
                    account_id=acc_id,
                    workspace_id=ws_id,
                    channel_id=ch_id,
                    oauth_client=FakeGoogleOAuthClient(),
                    token_store=token_store,
                )
        finally:
            reset_config()


# ---------------------------------------------------------------------------
# 14. RealGoogleOAuthClient scope passthrough regression tests
# ---------------------------------------------------------------------------


class TestRealOAuthClientScopePassthrough:
    """Regression for the defect that caused the live scope upgrade to silently fail.

    _make_flow() hardcoded YOUTUBE_SCOPES and ignored the `scopes` parameter
    received by get_authorization_url(). Google would see only the already-granted
    readonly scope, skip showing a new consent screen, and redirect back without
    granting youtube.upload.
    """

    def _make_real_client(self, tmp_path):
        import json

        from app.oauth.client_google import RealGoogleOAuthClient

        secrets_file = tmp_path / "client_secrets.json"
        secrets_file.write_text(
            json.dumps(
                {
                    "web": {
                        "client_id": "fake_client_id",
                        "client_secret": "fake_client_secret",
                        "redirect_uris": ["http://localhost/callback"],
                        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                        "token_uri": "https://oauth2.googleapis.com/token",
                    }
                }
            )
        )
        return RealGoogleOAuthClient(
            client_secrets_path=str(secrets_file),
            redirect_uri="http://localhost/callback",
        )

    def _capture_flow(self, client):
        """Replace _Flow on the instance with a capturing stub; return the capture list."""
        from unittest.mock import MagicMock

        captured_scopes: list[list[str]] = []
        mock_instance = MagicMock()
        mock_instance.authorization_url.return_value = (
            "https://accounts.google.com/auth?mock=1",
            "state",
        )
        mock_instance.code_verifier = None

        class _CapturingFlow:
            @classmethod
            def from_client_secrets_file(cls, path, scopes, state=None, **kw):
                captured_scopes.append(list(scopes))
                return mock_instance

        client._Flow = _CapturingFlow
        return captured_scopes

    def test_upload_scopes_reach_flow_constructor(self, tmp_path):
        """get_authorization_url(scopes=YOUTUBE_UPLOAD_SCOPES) must pass those scopes
        to Flow.from_client_secrets_file — not silently swap them for readonly."""
        from app.oauth.client import YOUTUBE_UPLOAD_SCOPE, YOUTUBE_UPLOAD_SCOPES

        client = self._make_real_client(tmp_path)
        captured = self._capture_flow(client)

        client.get_authorization_url(state_nonce="nonce1", scopes=YOUTUBE_UPLOAD_SCOPES)

        assert len(captured) == 1
        assert YOUTUBE_UPLOAD_SCOPE in captured[0], (
            f"youtube.upload must be forwarded to Flow.from_client_secrets_file; got {captured[0]}"
        )

    def test_readonly_scopes_do_not_include_upload_scope(self, tmp_path):
        """Normal connect flow must pass only YOUTUBE_SCOPES — no youtube.upload."""
        from app.oauth.client import YOUTUBE_SCOPES, YOUTUBE_UPLOAD_SCOPE

        client = self._make_real_client(tmp_path)
        captured = self._capture_flow(client)

        client.get_authorization_url(state_nonce="nonce2", scopes=YOUTUBE_SCOPES)

        assert len(captured) == 1
        assert YOUTUBE_UPLOAD_SCOPE not in captured[0], (
            f"youtube.upload must NOT appear in readonly connect flow; got {captured[0]}"
        )

    def test_different_scope_sets_produce_different_flow_scopes(self, tmp_path):
        """Two calls with different scope sets must each reach the Flow with the correct set."""
        from app.oauth.client import YOUTUBE_SCOPES, YOUTUBE_UPLOAD_SCOPE, YOUTUBE_UPLOAD_SCOPES

        client = self._make_real_client(tmp_path)
        captured = self._capture_flow(client)

        client.get_authorization_url(state_nonce="n1", scopes=YOUTUBE_SCOPES)
        client.get_authorization_url(state_nonce="n2", scopes=YOUTUBE_UPLOAD_SCOPES)

        assert YOUTUBE_UPLOAD_SCOPE not in captured[0]
        assert YOUTUBE_UPLOAD_SCOPE in captured[1]


# ---------------------------------------------------------------------------
# 15. Callback scope persistence — has_upload_scope reflects stored grants
# ---------------------------------------------------------------------------


class TestCallbackPersistsGrantedScopes:
    """Verify that complete_youtube_oauth() stores whatever scopes Google grants,
    and that has_upload_scope() accurately reflects those stored scopes.

    These are the end-to-end tests for the upgrade success/failure path.
    """

    def _run_oauth(
        self, db, token_store, ws_id, ch_id, acc_id, reg_ch_id, granted_scopes, *, upgrade=False
    ):
        from app.oauth.flow import (
            complete_youtube_oauth,
            start_youtube_oauth,
            start_youtube_upload_oauth,
        )

        client = FakeGoogleOAuthClient(
            fake_channel_id=reg_ch_id,
            granted_scopes=granted_scopes,
        )
        start_fn = start_youtube_upload_oauth if upgrade else start_youtube_oauth
        r = start_fn(
            db,
            account_id=acc_id,
            user_id="u",
            workspace_id=ws_id,
            channel_id=ch_id,
            oauth_client=client,
        )
        complete_youtube_oauth(
            db,
            code="code",
            state_nonce=r.state_nonce,
            oauth_client=client,
            token_store=token_store,
        )

    def test_callback_with_readonly_only_leaves_upload_missing(
        self, db: sqlite3.Connection, token_store: LocalFileTokenStore
    ) -> None:
        from app.oauth.flow import has_upload_scope

        ws_id, ch_id, acc_id, reg_ch_id = _make_connected_account(db, token_store)
        # Re-auth callback granting only readonly (no upgrade)
        self._run_oauth(db, token_store, ws_id, ch_id, acc_id, reg_ch_id, YOUTUBE_SCOPES)

        assert (
            has_upload_scope(
                db,
                account_id=acc_id,
                workspace_id=ws_id,
                channel_id=ch_id,
                token_store=token_store,
            )
            is False
        ), "Upload scope must not be granted when callback returns only youtube.readonly"

    def test_upgrade_callback_with_upload_scope_sets_granted(
        self, db: sqlite3.Connection, token_store: LocalFileTokenStore
    ) -> None:
        from app.oauth.flow import has_upload_scope

        ws_id, ch_id, acc_id, reg_ch_id = _make_connected_account(db, token_store)
        # Upgrade callback granting both readonly and upload
        self._run_oauth(
            db, token_store, ws_id, ch_id, acc_id, reg_ch_id, YOUTUBE_UPLOAD_SCOPES, upgrade=True
        )

        assert (
            has_upload_scope(
                db,
                account_id=acc_id,
                workspace_id=ws_id,
                channel_id=ch_id,
                token_store=token_store,
            )
            is True
        ), "Upload scope must be granted when upgrade callback returns youtube.upload"

    def test_initial_connect_followed_by_upgrade_callback(
        self, db: sqlite3.Connection, token_store: LocalFileTokenStore
    ) -> None:
        """Readonly connect then upgrade callback → upload granted."""
        from app.oauth.flow import has_upload_scope

        ws_id, ch_id, acc_id, reg_ch_id = _make_connected_account(
            db, token_store, granted_scopes=YOUTUBE_SCOPES
        )
        # Confirm not yet granted
        assert not has_upload_scope(
            db,
            account_id=acc_id,
            workspace_id=ws_id,
            channel_id=ch_id,
            token_store=token_store,
        )
        # Simulate successful upgrade callback
        self._run_oauth(
            db, token_store, ws_id, ch_id, acc_id, reg_ch_id, YOUTUBE_UPLOAD_SCOPES, upgrade=True
        )
        # Now granted
        assert has_upload_scope(
            db,
            account_id=acc_id,
            workspace_id=ws_id,
            channel_id=ch_id,
            token_store=token_store,
        )


# ---------------------------------------------------------------------------
# 16. Scope constant safety — no over-broad YouTube scopes
# ---------------------------------------------------------------------------


class TestScopeConstantSafety:
    """Verify scope constants don't include over-broad or dangerous YouTube scopes."""

    def test_upload_scopes_excludes_broad_youtube_manage_scope(self):
        """YOUTUBE_UPLOAD_SCOPES must not include youtube (manage all) or youtube.force-ssl."""
        broad = {
            "https://www.googleapis.com/auth/youtube",
            "https://www.googleapis.com/auth/youtube.force-ssl",
        }
        included = broad & set(YOUTUBE_UPLOAD_SCOPES)
        assert not included, f"Unexpected broad scopes in YOUTUBE_UPLOAD_SCOPES: {included}"

    def test_upgrade_adds_exactly_one_new_scope(self):
        """The upgrade adds exactly youtube.upload and nothing else beyond YOUTUBE_SCOPES."""
        added = set(YOUTUBE_UPLOAD_SCOPES) - set(YOUTUBE_SCOPES)
        assert added == {YOUTUBE_UPLOAD_SCOPE}, (
            f"Expected only youtube.upload to be added; got extra: {added}"
        )

    def test_initial_connect_scopes_excludes_upload(self):
        """YOUTUBE_SCOPES (initial connect) must not contain youtube.upload."""
        assert YOUTUBE_UPLOAD_SCOPE not in YOUTUBE_SCOPES
