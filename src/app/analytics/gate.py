"""Pre-flight gate for YouTube Analytics ingestion.

Analogous to publishing/upload_gate.py but for analytics reads.

Gate sequence (fail closed):
  1. Account connected with active credential profile
  2. Token readable and refreshed if expired
  3. yt-analytics.readonly scope present in stored grant

Does NOT require ACE_PUBLISHING_LIVE_ENABLED — analytics reads are independent
of the publishing live gate.
"""

from __future__ import annotations

from typing import Any

from app.analytics.errors import AnalyticsScopeNotGrantedError
from app.analytics.providers.youtube import YouTubeAnalyticsProvider
from app.oauth.client import YOUTUBE_ANALYTICS_SCOPE
from app.oauth.store import LocalFileTokenStore


def build_authenticated_analytics_provider(
    conn: Any,
    *,
    account_id: str,
    workspace_id: str,
    channel_id: str,
    oauth_client: Any,
    token_store: LocalFileTokenStore | None = None,
    monetary_scope_granted: bool = False,
) -> YouTubeAnalyticsProvider:
    """Run the analytics pre-flight gate and return a live YouTubeAnalyticsProvider.

    Gate sequence:
      1. Token readable and refreshed if expired
      2. yt-analytics.readonly scope present

    On any failure a typed error is raised before any analytics call is made.
    The returned provider is bound to the account's current access token.

    monetary_scope_granted should be True only when yt-analytics-monetary.readonly
    is confirmed present in the stored grant — this is intentionally deferred and
    defaults to False so revenue metrics are excluded from all initial ingests.
    """
    from app.oauth.flow import refresh_account_token
    from app.oauth.store import get_token_store

    store = token_store or get_token_store()

    stored = refresh_account_token(
        conn,
        account_id=account_id,
        workspace_id=workspace_id,
        channel_id=channel_id,
        oauth_client=oauth_client,
        token_store=store,
    )

    if not stored.has_scope(YOUTUBE_ANALYTICS_SCOPE):
        raise AnalyticsScopeNotGrantedError(
            f"The account's OAuth credential does not include {YOUTUBE_ANALYTICS_SCOPE!r}. "
            f"Granted scopes: {stored.scopes}. "
            "Use the 'Enable Analytics Permission' action to reauthorize with analytics scope."
        )

    client_secrets = _load_client_secrets(oauth_client)
    return YouTubeAnalyticsProvider(
        access_token=stored.access_token,
        refresh_token=stored.refresh_token,
        token_uri=client_secrets.get("token_uri"),
        client_id=client_secrets.get("client_id"),
        client_secret=client_secrets.get("client_secret"),
        monetary_scope_granted=monetary_scope_granted,
    )


def _load_client_secrets(oauth_client: Any) -> dict:
    try:
        return oauth_client.get_oauth_client_secrets()
    except Exception:
        return {"token_uri": None, "client_id": None, "client_secret": None}
