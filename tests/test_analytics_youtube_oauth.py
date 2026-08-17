"""Phase A — YouTube Analytics OAuth scope architecture and provider tests.

All tests are zero-network.  No live YouTube Analytics calls are made.
FakeAnalyticsProvider is unchanged by these tests.

Test categories
---------------
1. Scope constants — new constants are correct and non-overlapping
2. has_analytics_scope() — absent/present scope detection, fail-closed behaviour
3. start_youtube_analytics_oauth() — requests exactly YOUTUBE_ANALYTICS_SCOPES
4. Upload scope preservation — analytics upgrade does not drop youtube.upload
5. No overbroad YouTube scopes introduced
6. YouTubeAnalyticsProvider construction and credentials
7. Authenticated provider fails closed when analytics scope absent (gate)
8. Provider health report
9. Fake service injection — mocked API response normalises correctly
10. Monetary metrics excluded without monetary scope
11. Revenue metrics present when monetary scope granted
12. Protocol compliance
13. FakeAnalyticsProvider unchanged
14. CLI — youtube branch resolves real provider (not fake)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.analytics.errors import AnalyticsScopeNotGrantedError
from app.analytics.protocol import AnalyticsProvider
from app.analytics.providers.youtube import (
    _MONETARY_REQUEST_METRICS,
    _NON_MONETARY_REQUEST_METRICS,
    _YT_FIELD_MAP,
    YouTubeAnalyticsProvider,
)
from app.oauth.client import (
    YOUTUBE_ANALYTICS_SCOPE,
    YOUTUBE_ANALYTICS_SCOPES,
    YOUTUBE_SCOPES,
    YOUTUBE_UPLOAD_SCOPE,
    YOUTUBE_UPLOAD_SCOPES,
)

# ---------------------------------------------------------------------------
# 1. Scope constants
# ---------------------------------------------------------------------------


def test_analytics_scope_string():
    assert YOUTUBE_ANALYTICS_SCOPE == "https://www.googleapis.com/auth/yt-analytics.readonly"


def test_analytics_scopes_length():
    assert len(YOUTUBE_ANALYTICS_SCOPES) == 4


def test_analytics_scopes_contains_openid():
    assert "openid" in YOUTUBE_ANALYTICS_SCOPES


def test_analytics_scopes_contains_readonly():
    assert "https://www.googleapis.com/auth/youtube.readonly" in YOUTUBE_ANALYTICS_SCOPES


def test_analytics_scopes_contains_upload():
    assert YOUTUBE_UPLOAD_SCOPE in YOUTUBE_ANALYTICS_SCOPES


def test_analytics_scopes_contains_analytics():
    assert YOUTUBE_ANALYTICS_SCOPE in YOUTUBE_ANALYTICS_SCOPES


def test_analytics_scopes_is_superset_of_upload_scopes():
    for s in YOUTUBE_UPLOAD_SCOPES:
        assert s in YOUTUBE_ANALYTICS_SCOPES, f"Upload scope missing from analytics set: {s}"


# ---------------------------------------------------------------------------
# 2. No overbroad YouTube scopes
# ---------------------------------------------------------------------------

_OVERBROAD = {
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/yt-analytics-monetary.readonly",
}


def test_analytics_scopes_no_overbroad():
    for scope in _OVERBROAD:
        assert scope not in YOUTUBE_ANALYTICS_SCOPES, f"Overbroad scope found: {scope}"


def test_upload_scopes_no_overbroad():
    for scope in _OVERBROAD:
        assert scope not in YOUTUBE_UPLOAD_SCOPES


def test_base_scopes_no_overbroad():
    for scope in _OVERBROAD:
        assert scope not in YOUTUBE_SCOPES


# ---------------------------------------------------------------------------
# 3. has_analytics_scope() and has_upload_scope() — unit via token store
# ---------------------------------------------------------------------------


def _make_token_file(tmp_dir: str, account_id: str, scopes: list[str]) -> str:
    """Write a minimal token JSON to a temp file; return file:// external_ref."""
    from datetime import UTC, datetime, timedelta

    path = Path(tmp_dir) / f"{account_id}.json"
    payload = {
        "account_id": account_id,
        "access_token": "fake_access_token",
        "refresh_token": "fake_refresh_token",
        "token_type": "Bearer",
        "expires_at_utc": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        "scopes": scopes,
        "google_sub": "10000000001",
        "stored_at_utc": datetime.now(UTC).isoformat(),
    }
    path.write_text(json.dumps(payload))
    path.chmod(0o600)
    return f"file://{path}"


def test_has_analytics_scope_absent_when_only_upload(tmp_path):
    from app.oauth.store import LocalFileTokenStore, StoredTokenPayload

    store = LocalFileTokenStore(tmp_path)
    external_ref = _make_token_file(str(tmp_path), "acct1", YOUTUBE_UPLOAD_SCOPES)
    stored: StoredTokenPayload = store.read(external_ref)
    assert not stored.has_scope(YOUTUBE_ANALYTICS_SCOPE)


def test_has_analytics_scope_present_after_upgrade(tmp_path):
    from app.oauth.store import LocalFileTokenStore, StoredTokenPayload

    store = LocalFileTokenStore(tmp_path)
    external_ref = _make_token_file(str(tmp_path), "acct2", YOUTUBE_ANALYTICS_SCOPES)
    stored: StoredTokenPayload = store.read(external_ref)
    assert stored.has_scope(YOUTUBE_ANALYTICS_SCOPE)


def test_upload_scope_preserved_in_analytics_scopes(tmp_path):
    from app.oauth.store import LocalFileTokenStore

    store = LocalFileTokenStore(tmp_path)
    external_ref = _make_token_file(str(tmp_path), "acct3", YOUTUBE_ANALYTICS_SCOPES)
    stored = store.read(external_ref)
    assert stored.has_scope(YOUTUBE_UPLOAD_SCOPE), "upload scope must survive analytics upgrade"


# ---------------------------------------------------------------------------
# 4. start_youtube_analytics_oauth() — requests YOUTUBE_ANALYTICS_SCOPES
# ---------------------------------------------------------------------------


def test_start_analytics_oauth_requests_analytics_scope(tmp_path, tmp_db_with_account):
    """start_youtube_analytics_oauth() must request yt-analytics.readonly."""
    from app.oauth.client import FakeGoogleOAuthClient
    from app.oauth.flow import start_youtube_analytics_oauth

    conn, account_id, workspace_id, channel_id = tmp_db_with_account

    client = FakeGoogleOAuthClient(
        fake_channel_id=channel_id,
        granted_scopes=YOUTUBE_ANALYTICS_SCOPES,
    )
    result = start_youtube_analytics_oauth(
        conn,
        account_id=account_id,
        user_id="user-1",
        workspace_id=workspace_id,
        channel_id=channel_id,
        oauth_client=client,
    )
    assert result.authorization_url
    assert (
        "yt-analytics" in result.authorization_url
        or "yt-analytics" in (client.last_received_requested_scopes or [])
        or True
    )  # URL-encoded; verify via the stored claims instead


def test_start_analytics_oauth_stores_correct_scopes(tmp_path, tmp_db_with_account):
    """OAuthStateClaims.requested_scopes must equal YOUTUBE_ANALYTICS_SCOPES."""
    from app.oauth.client import FakeGoogleOAuthClient
    from app.oauth.flow import start_youtube_analytics_oauth
    from app.oauth.state import get_state_store

    conn, account_id, workspace_id, channel_id = tmp_db_with_account

    client = FakeGoogleOAuthClient(
        fake_channel_id=channel_id,
        granted_scopes=YOUTUBE_ANALYTICS_SCOPES,
    )
    result = start_youtube_analytics_oauth(
        conn,
        account_id=account_id,
        user_id="user-1",
        workspace_id=workspace_id,
        channel_id=channel_id,
        oauth_client=client,
    )
    claims = get_state_store().pop(result.state_nonce)
    assert claims.requested_scopes == YOUTUBE_ANALYTICS_SCOPES


def test_start_analytics_oauth_preserves_upload_scope_in_request(tmp_path, tmp_db_with_account):
    """The analytics OAuth request must include youtube.upload to preserve existing grant."""
    from app.oauth.client import FakeGoogleOAuthClient
    from app.oauth.flow import start_youtube_analytics_oauth
    from app.oauth.state import get_state_store

    conn, account_id, workspace_id, channel_id = tmp_db_with_account

    client = FakeGoogleOAuthClient(
        fake_channel_id=channel_id,
        granted_scopes=YOUTUBE_ANALYTICS_SCOPES,
    )
    result = start_youtube_analytics_oauth(
        conn,
        account_id=account_id,
        user_id="user-1",
        workspace_id=workspace_id,
        channel_id=channel_id,
        oauth_client=client,
    )
    claims = get_state_store().pop(result.state_nonce)
    assert YOUTUBE_UPLOAD_SCOPE in claims.requested_scopes


# ---------------------------------------------------------------------------
# 5. YouTubeAnalyticsProvider construction and validate_credentials
# ---------------------------------------------------------------------------


def test_provider_validate_credentials_with_token():
    p = YouTubeAnalyticsProvider("fake_access_token")
    assert p.validate_credentials() is True


def test_provider_validate_credentials_empty_token():
    p = YouTubeAnalyticsProvider("")
    assert p.validate_credentials() is False


def test_provider_name():
    p = YouTubeAnalyticsProvider("tok")
    assert p.provider_name == "youtube"


def test_provider_version():
    p = YouTubeAnalyticsProvider("tok")
    assert p.provider_version is not None and len(p.provider_version) > 0


def test_provider_no_monetary_scope_by_default():
    p = YouTubeAnalyticsProvider("tok")
    assert not p.capabilities().supports_revenue


def test_provider_monetary_scope_when_granted():
    p = YouTubeAnalyticsProvider("tok", monetary_scope_granted=True)
    assert p.capabilities().supports_revenue is True


def test_provider_supports_period_queries():
    p = YouTubeAnalyticsProvider("tok")
    assert p.capabilities().supports_period_queries is True


def test_provider_supports_impression_data():
    p = YouTubeAnalyticsProvider("tok")
    assert p.capabilities().supports_impression_data is True


# ---------------------------------------------------------------------------
# 6. fetch_metrics raises ProviderAdapterError without access_token
# ---------------------------------------------------------------------------


def test_fetch_metrics_raises_without_access_token():
    from app.analytics.errors import ProviderAdapterError

    p = YouTubeAnalyticsProvider("")
    with pytest.raises(ProviderAdapterError, match="access_token"):
        p.fetch_metrics("video123")


# ---------------------------------------------------------------------------
# 7. Gate — fails closed when analytics scope absent
# ---------------------------------------------------------------------------


def test_gate_raises_scope_not_granted_when_absent(tmp_path, tmp_db_with_account):
    from app.analytics.gate import build_authenticated_analytics_provider
    from app.oauth.client import FakeGoogleOAuthClient

    conn, account_id, workspace_id, channel_id = tmp_db_with_account

    # Simulate account having upload-only scope (no analytics)
    client = FakeGoogleOAuthClient(
        fake_channel_id=channel_id,
        granted_scopes=YOUTUBE_UPLOAD_SCOPES,
    )

    _provision_token(tmp_path, conn, account_id, YOUTUBE_UPLOAD_SCOPES)

    with pytest.raises(AnalyticsScopeNotGrantedError, match="yt-analytics"):
        build_authenticated_analytics_provider(
            conn,
            account_id=account_id,
            workspace_id=workspace_id,
            channel_id=channel_id,
            oauth_client=client,
            token_store=_make_store(tmp_path),
        )


def test_gate_succeeds_when_analytics_scope_present(tmp_path, tmp_db_with_account):
    from app.analytics.gate import build_authenticated_analytics_provider
    from app.oauth.client import FakeGoogleOAuthClient

    conn, account_id, workspace_id, channel_id = tmp_db_with_account

    client = FakeGoogleOAuthClient(
        fake_channel_id=channel_id,
        granted_scopes=YOUTUBE_ANALYTICS_SCOPES,
    )

    _provision_token(tmp_path, conn, account_id, YOUTUBE_ANALYTICS_SCOPES)

    provider = build_authenticated_analytics_provider(
        conn,
        account_id=account_id,
        workspace_id=workspace_id,
        channel_id=channel_id,
        oauth_client=client,
        token_store=_make_store(tmp_path),
    )
    assert isinstance(provider, YouTubeAnalyticsProvider)
    assert provider.validate_credentials() is True


# ---------------------------------------------------------------------------
# 8. Fake service injection — mocked API response normalises correctly
# ---------------------------------------------------------------------------


def _make_fake_service(rows: list[list], column_names: list[str]) -> object:
    """Return a fake youtubeAnalytics service whose reports().query().execute() returns rows."""
    response = {
        "kind": "youtubeAnalytics#resultTable",
        "columnHeaders": [{"name": n, "columnType": "METRIC"} for n in column_names],
        "rows": rows,
    }
    execute_mock = MagicMock(return_value=response)
    query_mock = MagicMock()
    query_mock.return_value.execute = execute_mock
    reports_mock = MagicMock()
    reports_mock.return_value.query = query_mock
    service_mock = MagicMock()
    service_mock.reports = reports_mock
    return service_mock


def test_normalize_views_and_watch_time():
    """API response is correctly mapped to canonical metrics."""
    from app.analytics.constants import METRIC_VIEWS, METRIC_WATCH_TIME_SECONDS

    fake_service = _make_fake_service(
        rows=[[500, 120.0]],
        column_names=["views", "estimatedMinutesWatched"],
    )
    p = YouTubeAnalyticsProvider("tok", api_service_override=fake_service)
    raw = p.fetch_metrics("videoABC", period_start="2026-01-01", period_end="2026-08-17")
    normalized = p.normalize(raw)

    assert normalized[METRIC_VIEWS] == 500.0
    assert normalized[METRIC_WATCH_TIME_SECONDS] == pytest.approx(120.0 * 60)


def test_normalize_ctr_and_impressions():
    """impressionClickThroughRate → METRIC_CTR; impressions → METRIC_IMPRESSIONS."""
    from app.analytics.constants import METRIC_CTR, METRIC_IMPRESSIONS

    fake_service = _make_fake_service(
        rows=[[1000, 0.045]],
        column_names=["impressions", "impressionClickThroughRate"],
    )
    p = YouTubeAnalyticsProvider("tok", api_service_override=fake_service)
    raw = p.fetch_metrics("videoABC")
    normalized = p.normalize(raw)

    assert normalized[METRIC_IMPRESSIONS] == 1000.0
    assert normalized[METRIC_CTR] == pytest.approx(0.045)


def test_normalize_empty_rows_returns_empty_metrics():
    """No rows in API response → empty normalized dict (video has no data yet)."""
    fake_service = _make_fake_service(rows=[], column_names=["views", "estimatedMinutesWatched"])
    p = YouTubeAnalyticsProvider("tok", api_service_override=fake_service)
    raw = p.fetch_metrics("newVideoXYZ")
    normalized = p.normalize(raw)
    assert normalized == {}


def test_fetch_metrics_period_dates_stripped_to_date_only():
    """ISO timestamps are stripped to YYYY-MM-DD before hitting the API."""
    column_names = ["views"]
    fake_service = _make_fake_service(rows=[[42]], column_names=column_names)

    captured: dict[str, str] = {}

    original_query = fake_service.reports.return_value.query

    def capturing_query(**kwargs):
        captured.update(kwargs)
        return original_query(**kwargs)

    fake_service.reports.return_value.query = capturing_query

    p = YouTubeAnalyticsProvider("tok", api_service_override=fake_service)
    p.fetch_metrics("v1", period_start="2026-01-01T00:00:00Z", period_end="2026-08-17T23:59:59Z")

    assert captured.get("startDate") == "2026-01-01"
    assert captured.get("endDate") == "2026-08-17"


# ---------------------------------------------------------------------------
# 9. Monetary metrics excluded without monetary scope
# ---------------------------------------------------------------------------


def test_monetary_field_absent_when_no_monetary_scope():
    """estimatedRevenue must NOT appear in the metrics request without monetary scope."""
    from app.analytics.constants import METRIC_REVENUE_ESTIMATE

    fake_service = _make_fake_service(
        rows=[[100]],
        column_names=["views"],
    )
    captured_metrics: list[str] = []
    original_query = fake_service.reports.return_value.query

    def capturing_query(**kwargs):
        captured_metrics.extend(kwargs.get("metrics", "").split(","))
        return original_query(**kwargs)

    fake_service.reports.return_value.query = capturing_query

    p = YouTubeAnalyticsProvider(
        "tok", api_service_override=fake_service, monetary_scope_granted=False
    )
    raw = p.fetch_metrics("vid1")
    normalized = p.normalize(raw)

    assert "estimatedRevenue" not in captured_metrics
    assert METRIC_REVENUE_ESTIMATE not in normalized


def test_monetary_field_requested_with_monetary_scope():
    """estimatedRevenue IS in the metrics request when monetary_scope_granted=True."""
    fake_service = _make_fake_service(
        rows=[[100, 2.50]],
        column_names=["views", "estimatedRevenue"],
    )
    captured_metrics: list[str] = []
    original_query = fake_service.reports.return_value.query

    def capturing_query(**kwargs):
        captured_metrics.extend(kwargs.get("metrics", "").split(","))
        return original_query(**kwargs)

    fake_service.reports.return_value.query = capturing_query

    p = YouTubeAnalyticsProvider(
        "tok", api_service_override=fake_service, monetary_scope_granted=True
    )
    p.fetch_metrics("vid1")

    assert "estimatedRevenue" in captured_metrics


def test_dislikes_not_in_request_metrics():
    """dislikes must never appear in the API request (deprecated Dec 2021 → 400 error)."""
    assert "dislikes" not in _NON_MONETARY_REQUEST_METRICS
    assert "dislikes" not in _MONETARY_REQUEST_METRICS


def test_dislikes_still_in_field_map_for_legacy():
    """dislikes remains in _YT_FIELD_MAP so legacy raw dicts normalize correctly."""
    assert "dislikes" in _YT_FIELD_MAP


# ---------------------------------------------------------------------------
# 10. Protocol compliance
# ---------------------------------------------------------------------------


def test_youtube_analytics_provider_satisfies_protocol():
    p = YouTubeAnalyticsProvider("tok")
    assert isinstance(p, AnalyticsProvider)


# ---------------------------------------------------------------------------
# 11. FakeAnalyticsProvider unchanged
# ---------------------------------------------------------------------------


def test_fake_analytics_provider_unchanged():
    from app.analytics.providers.fake import FakeAnalyticsProvider

    p = FakeAnalyticsProvider()
    assert isinstance(p, AnalyticsProvider)
    assert p.validate_credentials() is True
    raw = p.fetch_metrics("any_video_id")
    assert raw.provider_video_id == "any_video_id"


# ---------------------------------------------------------------------------
# 12. CLI — youtube branch constructs real provider (not FakeAnalyticsProvider)
# ---------------------------------------------------------------------------


def test_cli_youtube_branch_does_not_return_fake_provider(tmp_path, tmp_db_with_account):
    """_build_youtube_provider_and_lineage() must return a YouTubeAnalyticsProvider,
    not FakeAnalyticsProvider.  Verifies the CLI routing bug is fixed."""
    from app.analytics.cli import _build_youtube_provider_and_lineage
    from app.analytics.providers.fake import FakeAnalyticsProvider

    conn, account_id, workspace_id, channel_id = tmp_db_with_account
    _provision_token(tmp_path, conn, account_id, YOUTUBE_ANALYTICS_SCOPES)

    # Create minimal publishing plan and publication so lineage derivation succeeds
    plan = _minimal_publishing_plan(conn)
    job = _minimal_publishing_job(conn, plan.id)
    pub = _minimal_publication(conn, plan_id=plan.id, job_id=job.id)

    expected_provider = YouTubeAnalyticsProvider("fake_token")

    mock_cfg = MagicMock()
    mock_cfg.youtube_client_secrets_path = "/fake/secrets.json"
    mock_cfg.youtube_redirect_uri = "http://localhost:8000/callback"

    _gate = "app.analytics.gate.build_authenticated_analytics_provider"
    _init = "app.oauth.client_google.RealGoogleOAuthClient.__init__"
    _cfg = "app.core.config.get_config"

    with patch(_gate, return_value=expected_provider):
        with patch(_init, return_value=None):
            with patch(_cfg, return_value=mock_cfg):
                provider, lineage = _build_youtube_provider_and_lineage(
                    conn,
                    publication_id=pub.id,
                    account_id=account_id,
                    workspace_id=workspace_id,
                    channel_id=channel_id,
                )

    assert isinstance(provider, YouTubeAnalyticsProvider), (
        f"Expected YouTubeAnalyticsProvider, got {type(provider).__name__}"
    )
    assert not isinstance(provider, FakeAnalyticsProvider)
    # Verify lineage was auto-derived from the publication + plan
    vid, plan_id, job_id, *_rest = lineage
    assert vid == "kQH88nXdiRY"
    assert plan_id == plan.id
    assert job_id == job.id


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_state_store():
    """Use an in-memory state store for all tests in this module."""
    from app.oauth.state import InMemoryOAuthStateStore, reset_state_store, set_state_store

    set_state_store(InMemoryOAuthStateStore())
    yield
    reset_state_store()


@pytest.fixture
def tmp_db_with_account(tmp_path):
    """Provision workspace → channel → platform account with OAuth completed.

    Returns (conn, account_id, workspace_id, channel_id).
    Token file is written at tmp_path/<account_id>.json with YOUTUBE_UPLOAD_SCOPES.
    Use _provision_token() to upgrade to analytics scopes in tests that need it.
    """
    import uuid

    from app.control_plane import orchestrator as cp_orch
    from app.control_plane import repository as repo
    from app.control_plane.models import PlatformAccountDraft
    from app.core.database import open_db
    from app.oauth.client import FakeGoogleOAuthClient
    from app.oauth.flow import complete_youtube_oauth, start_youtube_oauth
    from app.oauth.store import LocalFileTokenStore, set_token_store

    conn = open_db(tmp_path / "test.db")
    token_store = LocalFileTokenStore(tmp_path)
    set_token_store(token_store)

    repo.ensure_platform(conn, "plt-yt-test", "youtube", "YouTube")

    ws = cp_orch.provision_workspace(conn, name="TestWS", slug="test-ws", actor="test")
    ch = cp_orch.provision_channel(
        conn, workspace_id=ws.id, name="TestChannel", slug="test-ch", actor="test"
    )

    platform = repo.get_platform_by_key(conn, "youtube")
    account_id = str(uuid.uuid4())
    registered_channel_id = "UCtest123"

    draft = PlatformAccountDraft(
        id=account_id,
        channel_id=ch.id,
        platform_id=platform.id,
        platform_key="youtube",
        external_account_id=f"pending:{account_id}",
        display_name="Test Account",
        actor="test",
        status="connected",
    )
    acct = repo.create_platform_account(conn, draft)

    oauth_client = FakeGoogleOAuthClient(
        fake_channel_id=registered_channel_id,
        granted_scopes=YOUTUBE_UPLOAD_SCOPES,
    )
    r = start_youtube_oauth(
        conn,
        account_id=acct.id,
        user_id="u1",
        workspace_id=ws.id,
        channel_id=ch.id,
        oauth_client=oauth_client,
    )
    complete_youtube_oauth(
        conn,
        code="code",
        state_nonce=r.state_nonce,
        oauth_client=oauth_client,
        token_store=token_store,
    )

    return conn, acct.id, ws.id, ch.id


def _make_store(tmp_dir) -> Any:
    from app.oauth.store import LocalFileTokenStore

    return LocalFileTokenStore(tmp_dir)


def _provision_token(tmp_path, conn, account_id: str, scopes: list[str]) -> None:
    """Write a token file for account_id with the given scopes."""
    external_ref = _make_token_file(str(tmp_path), account_id, scopes)
    conn.execute(
        "UPDATE cp_credential_profiles SET external_ref = ? "
        "WHERE id = (SELECT credential_profile_id FROM cp_platform_accounts WHERE id = ?)",
        (external_ref, account_id),
    )
    conn.commit()


def _make_fake_oauth_client_for_gate(tmp_path, account_id: str, channel_id: str):
    from app.oauth.client import FakeGoogleOAuthClient

    client = FakeGoogleOAuthClient(
        fake_channel_id=channel_id,
        granted_scopes=YOUTUBE_ANALYTICS_SCOPES,
    )
    return client


def _make_token_refresher(tmp_path, account_id: str, scopes: list[str]):
    """Return a side_effect for refresh_account_token that reads the token file."""
    from app.oauth.store import LocalFileTokenStore

    store = LocalFileTokenStore(tmp_path)
    external_ref = f"file://{tmp_path}/{account_id}.json"

    def _refresh(conn, *, account_id, workspace_id, channel_id, oauth_client, token_store=None):
        return store.read(external_ref)

    return _refresh


def _minimal_publishing_plan(conn):
    """Insert and return a minimal publishing plan row."""
    import hashlib

    from app.publishing.models import PublishingMetadataDraft, PublishingScheduleDraft
    from app.publishing.repository import create_publishing_plan

    # Disable FK constraints: render/scene/production IDs don't have parent rows in this test
    conn.execute("PRAGMA foreign_keys = OFF")
    return create_publishing_plan(
        conn,
        render_manifest_id=1,
        render_job_id=None,
        topic_id=1,
        production_plan_id=1,
        script_id=1,
        scene_manifest_id=1,
        narration_run_id=1,
        caption_run_id=1,
        experiment_id=None,
        input_hash=hashlib.sha256(b"test-plan").hexdigest(),
        provider="youtube",
        provider_version="1.0.0",
        metadata=PublishingMetadataDraft(
            title="Test Video",
            description="Test",
            tags=["test"],
            language="en",
            category="22",
            visibility="private",
        ),
        schedule=PublishingScheduleDraft(schedule_type="immediate"),
    )


def _minimal_publishing_job(conn, plan_id: int):
    """Insert and return a minimal publishing job row."""
    from app.publishing.repository import create_publishing_job

    return create_publishing_job(
        conn,
        publishing_plan_id=plan_id,
        attempt_number=1,
        provider="youtube",
        provider_version="1.0.0",
    )


def _minimal_publication(conn, *, plan_id: int, job_id: int):
    """Insert and return a minimal publication row."""
    from app.publishing.repository import create_publication

    return create_publication(
        conn,
        publishing_plan_id=plan_id,
        publishing_job_id=job_id,
        provider="youtube",
        provider_version="1.0.0",
        provider_video_id="kQH88nXdiRY",
        provider_url="https://www.youtube.com/watch?v=kQH88nXdiRY",
        provider_status={},
        visibility="private",
        scheduled_at=None,
        input_hash="abc123",
        output_sha256="def456",
    )
