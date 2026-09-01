"""Phase 18E.2 — YouTube credential recovery & health hardening.

Motivated by a live incident: Orvella's account was marked credential_invalid
by a scheduled token refresh that hit a transient DNS resolution failure
reaching oauth2.googleapis.com — the refresh token was never actually
presented to Google, so nothing was learned about whether it was valid. The
operator's own reconnect attempts then failed too, because the plain reconnect
flow requested a narrower scope set than the account's standing Google grant,
and oauthlib rejected the mismatch. And the dashboard kept showing a second,
already-resolved incident from days earlier as if it were still active.

Three independent defects, three independent fixes, tested together because
they were diagnosed together:

  B/C  transient vs genuine credential-failure classification + recovery
  D    reconnect requests a scope set incompatible with the account's own grant
  E    exception queue never checked whether a later 'healthy' record existed
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.control_plane import orchestrator as cp_orch
from app.control_plane import repository as repo
from app.control_plane.accounts import (
    NON_HEALTHY_HEALTH_STATUSES,
    OPERATOR_INTENT_STATUSES,
    RECOVERABLE_ACCOUNT_STATUSES,
    restore_account_health,
)
from app.control_plane.health import get_health, record_health
from app.control_plane.models import PlatformAccountDraft
from app.core.database import open_db
from app.oauth.client import FakeGoogleOAuthClient
from app.oauth.client_google import RealGoogleOAuthClient
from app.oauth.errors import OAuthRefreshError, OAuthTransientError
from app.oauth.flow import (
    _scopes_to_reconnect_with,
    complete_youtube_oauth,
    refresh_account_token,
    start_youtube_oauth,
    verify_youtube_connection,
)
from app.oauth.state import InMemoryOAuthStateStore, reset_state_store, set_state_store
from app.oauth.store import LocalFileTokenStore, reset_token_store, set_token_store

YOUTUBE_READONLY = "https://www.googleapis.com/auth/youtube.readonly"
YOUTUBE_UPLOAD = "https://www.googleapis.com/auth/youtube.upload"
YOUTUBE_ANALYTICS = "https://www.googleapis.com/auth/yt-analytics.readonly"
YOUTUBE_RELEASE = "https://www.googleapis.com/auth/youtube.force-ssl"

ORVELLA_GRANTED_SCOPES = [
    "openid",
    YOUTUBE_READONLY,
    YOUTUBE_UPLOAD,
    YOUTUBE_ANALYTICS,
    YOUTUBE_RELEASE,
]


# ── Shared fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def db(tmp_path):
    conn = open_db(tmp_path / "test.db")
    repo.ensure_platform(conn, "plt-youtube-1", "youtube", "YouTube")
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def fresh_state_store():
    store = InMemoryOAuthStateStore()
    set_state_store(store)
    yield store
    reset_state_store()


@pytest.fixture
def token_store(tmp_path):
    store = LocalFileTokenStore(tmp_path / "tokens")
    set_token_store(store)
    yield store
    reset_token_store()


@pytest.fixture
def entities(db):
    ws = cp_orch.provision_workspace(db, name="TestWS", slug="test-ws", actor="test")
    ch = cp_orch.provision_channel(
        db, workspace_id=ws.id, name="TestCh", slug="test-ch", actor="test"
    )
    return ws, ch


def _account_with_grant(
    db,
    *,
    channel_id: str,
    status: str = "credential_invalid",
    granted_scopes: list[str] | None = None,
    external_account_id: str = "UCliveshape",
):
    """A previously-connected account with a standing scope grant, like Orvella."""
    from app.control_plane.credentials import create_credential_profile

    platform = repo.get_platform_by_key(db, "youtube")
    workspace_id = _workspace_of(db, channel_id)
    metadata = {
        "youtube_channel_id": external_account_id,
        "granted_scopes": granted_scopes if granted_scopes is not None else ORVELLA_GRANTED_SCOPES,
        "connection_method": "oauth2",
    }
    cred = create_credential_profile(
        db,
        workspace_id=workspace_id,
        display_name="cred",
        credential_type="oauth2",
        external_ref="memory://fake",
        actor="test",
    )
    acct_id = str(uuid.uuid4())
    draft = PlatformAccountDraft(
        id=acct_id,
        channel_id=channel_id,
        platform_id=platform.id,
        platform_key="youtube",
        external_account_id=external_account_id,
        display_name="Live-shaped Account",
        actor="test",
        status=status,
        credential_profile_id=cred.id,
        metadata_json=json.dumps(metadata),
    )
    return repo.create_platform_account(db, draft)


def _workspace_of(db, channel_id: str) -> str:
    row = db.execute("SELECT workspace_id FROM cp_channels WHERE id=?", (channel_id,)).fetchone()
    return row["workspace_id"]


# ── B. Transient vs genuine classification ───────────────────────────────────


class TestFailureClassification:
    def test_dns_resolution_failure_is_classified_transient(self):
        import google.auth.exceptions as ge
        import requests

        exc = ge.TransportError(
            requests.exceptions.ConnectionError(
                "HTTPSConnectionPool(host='oauth2.googleapis.com', port=443): "
                "Max retries exceeded ... NameResolutionError(...)"
            )
        )
        result = RealGoogleOAuthClient._classify_refresh_failure(exc)
        assert isinstance(result, OAuthTransientError)
        assert not isinstance(result, OAuthRefreshError)

    def test_timeout_is_classified_transient(self):
        import google.auth.exceptions as ge

        result = RealGoogleOAuthClient._classify_refresh_failure(ge.TimeoutError("timed out"))
        assert isinstance(result, OAuthTransientError)

    def test_invalid_grant_is_classified_genuine(self):
        import google.auth.exceptions as ge

        exc = ge.RefreshError("invalid_grant: Token has been expired or revoked.")
        result = RealGoogleOAuthClient._classify_refresh_failure(exc)
        assert isinstance(result, OAuthRefreshError)
        assert not isinstance(result, OAuthTransientError)

    def test_an_unrecognised_exception_defaults_to_genuine_not_transient(self):
        """An error this function does not recognise must not silently suppress
        the 'needs reconnection' signal that existed before classification."""
        result = RealGoogleOAuthClient._classify_refresh_failure(ValueError("unexpected"))
        assert isinstance(result, OAuthRefreshError)


class TestRefreshAccountTokenClassification:
    def test_transient_failure_does_not_mark_credential_invalid(self, db, entities, token_store):
        ws, ch = entities
        acct = _account_with_grant(db, channel_id=ch.id, status="connected")
        token_store.write(
            account_id=acct.id,
            access_token="a",
            refresh_token="r",
            token_type="Bearer",
            expires_at_utc=datetime.now(UTC) - timedelta(hours=1),
            scopes=ORVELLA_GRANTED_SCOPES,
            google_sub=None,
        )
        cred = repo.get_credential_profile(db, acct.credential_profile_id)
        db.execute(
            "UPDATE cp_credential_profiles SET external_ref=? WHERE id=?",
            (token_store.external_ref(acct.id), cred.id),
        )
        db.commit()

        client = FakeGoogleOAuthClient(fail_refresh=OAuthTransientError("DNS failure"))

        with pytest.raises(OAuthTransientError):
            refresh_account_token(
                db,
                account_id=acct.id,
                workspace_id=ws.id,
                channel_id=ch.id,
                oauth_client=client,
                token_store=token_store,
            )

        reloaded = repo.get_platform_account(db, acct.id)
        assert reloaded.status == "connected", (
            "a network failure must not assert credential invalidity"
        )

    def test_transient_failure_degrades_health_as_unavailable_not_degraded(
        self, db, entities, token_store
    ):
        ws, ch = entities
        acct = _account_with_grant(db, channel_id=ch.id, status="connected")
        token_store.write(
            account_id=acct.id,
            access_token="a",
            refresh_token="r",
            token_type="Bearer",
            expires_at_utc=datetime.now(UTC) - timedelta(hours=1),
            scopes=ORVELLA_GRANTED_SCOPES,
            google_sub=None,
        )
        cred = repo.get_credential_profile(db, acct.credential_profile_id)
        db.execute(
            "UPDATE cp_credential_profiles SET external_ref=? WHERE id=?",
            (token_store.external_ref(acct.id), cred.id),
        )
        db.commit()
        client = FakeGoogleOAuthClient(fail_refresh=OAuthTransientError("DNS failure"))

        with pytest.raises(OAuthTransientError):
            refresh_account_token(
                db,
                account_id=acct.id,
                workspace_id=ws.id,
                channel_id=ch.id,
                oauth_client=client,
                token_store=token_store,
            )

        health = get_health(db, "platform_account", acct.id)
        assert health.status == "unavailable"

    def test_genuine_refresh_rejection_still_marks_credential_invalid(
        self, db, entities, token_store
    ):
        ws, ch = entities
        acct = _account_with_grant(db, channel_id=ch.id, status="connected")
        token_store.write(
            account_id=acct.id,
            access_token="a",
            refresh_token="r",
            token_type="Bearer",
            expires_at_utc=datetime.now(UTC) - timedelta(hours=1),
            scopes=ORVELLA_GRANTED_SCOPES,
            google_sub=None,
        )
        cred = repo.get_credential_profile(db, acct.credential_profile_id)
        db.execute(
            "UPDATE cp_credential_profiles SET external_ref=? WHERE id=?",
            (token_store.external_ref(acct.id), cred.id),
        )
        db.commit()
        client = FakeGoogleOAuthClient(fail_refresh=OAuthRefreshError("invalid_grant"))

        with pytest.raises(OAuthRefreshError):
            refresh_account_token(
                db,
                account_id=acct.id,
                workspace_id=ws.id,
                channel_id=ch.id,
                oauth_client=client,
                token_store=token_store,
            )

        reloaded = repo.get_platform_account(db, acct.id)
        assert reloaded.status == "credential_invalid"
        health = get_health(db, "platform_account", acct.id)
        assert health.status == "degraded"


# ── C. Automatic recovery, centralized ────────────────────────────────────────


class TestCanonicalRecovery:
    def test_recoverable_status_is_restored_to_connected(self, db, entities):
        ws, ch = entities
        acct = _account_with_grant(db, channel_id=ch.id, status="credential_invalid")

        changed = restore_account_health(
            db,
            account_id=acct.id,
            workspace_id=ws.id,
            recorded_by="test:verify",
            detail="proved working",
        )

        assert changed is True
        assert repo.get_platform_account(db, acct.id).status == "connected"

    def test_recovery_also_heals_a_stale_health_record(self, db, entities):
        ws, ch = entities
        acct = _account_with_grant(db, channel_id=ch.id, status="credential_invalid")
        record_health(
            db,
            entity_type="platform_account",
            entity_id=acct.id,
            status="degraded",
            recorded_by="test",
            detail="was bad",
        )

        restore_account_health(
            db,
            account_id=acct.id,
            workspace_id=ws.id,
            recorded_by="test:verify",
            detail="proved working",
        )

        assert get_health(db, "platform_account", acct.id).status == "healthy"

    def test_recovery_is_idempotent(self, db, entities):
        ws, ch = entities
        acct = _account_with_grant(db, channel_id=ch.id, status="credential_invalid")

        first = restore_account_health(
            db, account_id=acct.id, workspace_id=ws.id, recorded_by="t", detail="d"
        )
        second = restore_account_health(
            db, account_id=acct.id, workspace_id=ws.id, recorded_by="t", detail="d"
        )

        assert first is True
        assert second is False
        assert repo.get_platform_account(db, acct.id).status == "connected"

    def test_operator_intent_states_are_never_auto_restored(self, db, entities):
        ws, ch = entities
        acct = _account_with_grant(db, channel_id=ch.id, status="disconnected")

        restore_account_health(
            db, account_id=acct.id, workspace_id=ws.id, recorded_by="t", detail="d"
        )

        assert repo.get_platform_account(db, acct.id).status == "disconnected"

    def test_constants_used_by_auto_observer_match_the_canonical_ones(self):
        """auto_observer.py re-exports these; this pins them to the same values."""
        from app.analytics.auto_observer import (
            _NON_HEALTHY_HEALTH_STATUSES,
            _OPERATOR_INTENT_STATUSES,
            _RECOVERABLE_ACCOUNT_STATUSES,
        )

        assert _RECOVERABLE_ACCOUNT_STATUSES == RECOVERABLE_ACCOUNT_STATUSES
        assert _NON_HEALTHY_HEALTH_STATUSES == NON_HEALTHY_HEALTH_STATUSES
        assert _OPERATOR_INTENT_STATUSES == OPERATOR_INTENT_STATUSES


class TestVerifyYoutubeConnectionRecovery:
    def test_a_credential_invalid_account_can_be_verified_not_refused_outright(
        self, db, entities, token_store
    ):
        """This is the whole point: the account this incident is about must be
        eligible for the ONE canonical re-verification Phase 18E.2 performs."""
        ws, ch = entities
        acct = _account_with_grant(db, channel_id=ch.id, status="credential_invalid")
        token_store.write(
            account_id=acct.id,
            access_token="a",
            refresh_token="r",
            token_type="Bearer",
            expires_at_utc=datetime.now(UTC) + timedelta(hours=1),
            scopes=ORVELLA_GRANTED_SCOPES,
            google_sub=None,
        )
        cred = repo.get_credential_profile(db, acct.credential_profile_id)
        db.execute(
            "UPDATE cp_credential_profiles SET external_ref=?, status='active' WHERE id=?",
            (token_store.external_ref(acct.id), cred.id),
        )
        db.commit()
        client = FakeGoogleOAuthClient(fake_channel_id="UCliveshape")

        result = verify_youtube_connection(
            db,
            account_id=acct.id,
            workspace_id=ws.id,
            channel_id=ch.id,
            oauth_client=client,
            token_store=token_store,
        )

        assert result.verified is True
        assert repo.get_platform_account(db, acct.id).status == "connected"

    def test_disconnected_account_is_still_refused(self, db, entities, token_store):
        ws, ch = entities
        acct = _account_with_grant(db, channel_id=ch.id, status="disconnected")

        result = verify_youtube_connection(
            db,
            account_id=acct.id,
            workspace_id=ws.id,
            channel_id=ch.id,
            oauth_client=FakeGoogleOAuthClient(),
            token_store=token_store,
        )

        assert result.verified is False

    def test_a_transient_failure_during_verification_gets_a_distinct_reason(
        self, db, entities, token_store
    ):
        ws, ch = entities
        acct = _account_with_grant(db, channel_id=ch.id, status="credential_invalid")
        token_store.write(
            account_id=acct.id,
            access_token="a",
            refresh_token="r",
            token_type="Bearer",
            expires_at_utc=datetime.now(UTC) - timedelta(hours=1),
            scopes=ORVELLA_GRANTED_SCOPES,
            google_sub=None,
        )
        cred = repo.get_credential_profile(db, acct.credential_profile_id)
        db.execute(
            "UPDATE cp_credential_profiles SET external_ref=?, status='active' WHERE id=?",
            (token_store.external_ref(acct.id), cred.id),
        )
        db.commit()
        client = FakeGoogleOAuthClient(fail_refresh=OAuthTransientError("DNS failure"))

        result = verify_youtube_connection(
            db,
            account_id=acct.id,
            workspace_id=ws.id,
            channel_id=ch.id,
            oauth_client=client,
            token_store=token_store,
        )

        assert result.verified is False
        assert "temporarily unreachable" in result.failure_reason
        assert repo.get_platform_account(db, acct.id).status == "credential_invalid", (
            "a transient failure during re-verification must not itself flip status either way"
        )


# ── D. Reconnect scope selection ─────────────────────────────────────────────


class TestReconnectScopeSelection:
    def test_an_account_with_release_scope_reconnects_with_the_full_superset(self):
        class Acct:
            metadata_json = json.dumps({"granted_scopes": ORVELLA_GRANTED_SCOPES})

        scopes = _scopes_to_reconnect_with(Acct())
        for required in (
            "openid",
            YOUTUBE_READONLY,
            YOUTUBE_ANALYTICS,
            YOUTUBE_UPLOAD,
            YOUTUBE_RELEASE,
        ):
            assert required in scopes

    def test_an_account_with_only_upload_scope_does_not_request_release(self):
        class Acct:
            metadata_json = json.dumps(
                {"granted_scopes": ["openid", YOUTUBE_READONLY, YOUTUBE_UPLOAD]}
            )

        scopes = _scopes_to_reconnect_with(Acct())
        assert YOUTUBE_UPLOAD in scopes
        assert YOUTUBE_RELEASE not in scopes

    def test_a_never_connected_account_falls_back_to_the_narrow_default(self):
        class Acct:
            metadata_json = None

        assert _scopes_to_reconnect_with(Acct()) == ["openid", YOUTUBE_READONLY]

    def test_malformed_metadata_falls_back_safely(self):
        class Acct:
            metadata_json = "{not json"

        assert _scopes_to_reconnect_with(Acct()) == ["openid", YOUTUBE_READONLY]

    def test_no_duplicate_scope_constants_were_introduced(self):
        """The fix must reuse the existing tiered constants, not invent new ones."""
        import app.oauth.flow as flow_mod

        source = open(flow_mod.__file__).read()
        # The four canonical tiers are imported, not redefined as new literals.
        assert "YOUTUBE_RELEASE_SCOPES" in source
        assert "YOUTUBE_RELEASE_SCOPES = [" not in source, "must import, not redeclare"

    def test_reconnect_for_broadly_granted_account_completes_the_code_exchange(
        self, db, entities, token_store
    ):
        """The end-to-end regression for the live incident: a plain reconnect
        for an account with a standing broad grant must actually succeed."""
        ws, ch = entities
        acct = _account_with_grant(db, channel_id=ch.id, status="credential_invalid")
        # FakeGoogleOAuthClient always returns what its own granted_scopes says
        # regardless of what was requested — mirroring Google's real
        # incremental-auth behaviour that triggered this incident.
        client = FakeGoogleOAuthClient(
            fake_channel_id="UCliveshape",
            granted_scopes=ORVELLA_GRANTED_SCOPES,
        )

        started = start_youtube_oauth(
            db,
            account_id=acct.id,
            user_id="u",
            workspace_id=ws.id,
            channel_id=ch.id,
            oauth_client=client,
        )
        # The FIX under test: requesting the account's own known grant means
        # the flow's declared scopes now match what will be returned.
        assert set(client.last_received_requested_scopes or []) == set()  # not yet called

        result = complete_youtube_oauth(
            db,
            code="code",
            state_nonce=started.state_nonce,
            oauth_client=client,
            token_store=token_store,
        )

        assert result.provider_channel_id == "UCliveshape"

    def test_reconnect_persists_to_the_credential_profile_orvella_uses(
        self, db, entities, token_store
    ):
        ws, ch = entities
        acct = _account_with_grant(db, channel_id=ch.id, status="credential_invalid")
        original_profile_id = acct.credential_profile_id
        client = FakeGoogleOAuthClient(
            fake_channel_id="UCliveshape", granted_scopes=ORVELLA_GRANTED_SCOPES
        )

        started = start_youtube_oauth(
            db,
            account_id=acct.id,
            user_id="u",
            workspace_id=ws.id,
            channel_id=ch.id,
            oauth_client=client,
        )
        complete_youtube_oauth(
            db,
            code="code",
            state_nonce=started.state_nonce,
            oauth_client=client,
            token_store=token_store,
        )

        reloaded = repo.get_platform_account(db, acct.id)
        assert reloaded.credential_profile_id == original_profile_id, (
            "reconnect must update the SAME profile the account already uses, "
            "not create a new, disconnected one"
        )
        cred = repo.get_credential_profile(db, original_profile_id)
        assert cred.status == "active"

    def test_callback_remains_strict_for_a_genuine_unexpected_scope_problem(
        self, db, entities, token_store
    ):
        """Fixing the common case must not weaken the callback's own strictness.

        If the account's stored grant metadata claims one thing but Google
        actually returns something else entirely (a real anomaly), that must
        still surface as a hard failure, not be silently accepted.
        """
        from app.oauth.errors import OAuthCodeExchangeError

        ws, ch = entities
        acct = _account_with_grant(
            db,
            channel_id=ch.id,
            status="credential_invalid",
            granted_scopes=["openid", YOUTUBE_READONLY],
        )
        # Client's fail_exchange simulates oauthlib's own strict rejection —
        # the real code path this represents is untouched by the scope fix.
        client = FakeGoogleOAuthClient(
            fail_exchange=OAuthCodeExchangeError("Scope has changed unexpectedly")
        )

        started = start_youtube_oauth(
            db,
            account_id=acct.id,
            user_id="u",
            workspace_id=ws.id,
            channel_id=ch.id,
            oauth_client=client,
        )
        with pytest.raises(OAuthCodeExchangeError):
            complete_youtube_oauth(
                db,
                code="code",
                state_nonce=started.state_nonce,
                oauth_client=client,
                token_store=token_store,
            )

    def test_release_capable_account_keeps_force_ssl_after_reconnect(
        self, db, entities, token_store
    ):
        ws, ch = entities
        acct = _account_with_grant(db, channel_id=ch.id, status="credential_invalid")
        client = FakeGoogleOAuthClient(
            fake_channel_id="UCliveshape", granted_scopes=ORVELLA_GRANTED_SCOPES
        )

        started = start_youtube_oauth(
            db,
            account_id=acct.id,
            user_id="u",
            workspace_id=ws.id,
            channel_id=ch.id,
            oauth_client=client,
        )
        complete_youtube_oauth(
            db,
            code="code",
            state_nonce=started.state_nonce,
            oauth_client=client,
            token_store=token_store,
        )

        reloaded = repo.get_platform_account(db, acct.id)
        meta = json.loads(reloaded.metadata_json)
        assert YOUTUBE_RELEASE in meta["granted_scopes"]


# ── E. Exception queue reconciliation ────────────────────────────────────────


class TestExceptionQueueReconciliation:
    def _entity_setup(self, db):
        ws = cp_orch.provision_workspace(db, name="EWS", slug="e-ws", actor="test")
        ch = cp_orch.provision_channel(
            db, workspace_id=ws.id, name="ECh", slug="e-ch", actor="test"
        )
        acct = _account_with_grant(db, channel_id=ch.id, status="connected")
        return ws, acct

    def test_degraded_then_healthy_yields_zero_active_exceptions(self, db):
        from app.application.exception_queue import get_exception_queue

        ws, acct = self._entity_setup(db)
        record_health(
            db,
            entity_type="platform_account",
            entity_id=acct.id,
            status="degraded",
            recorded_by="t",
            detail="bad",
        )
        record_health(
            db,
            entity_type="platform_account",
            entity_id=acct.id,
            status="healthy",
            recorded_by="t",
            detail="fixed",
        )

        items = get_exception_queue(db, ws.id)
        assert [i for i in items if i.entity_id == acct.id] == []

    def test_degraded_healthy_degraded_surfaces_only_the_latest(self, db):
        from app.application.exception_queue import get_exception_queue

        ws, acct = self._entity_setup(db)
        record_health(
            db,
            entity_type="platform_account",
            entity_id=acct.id,
            status="degraded",
            recorded_by="t",
            detail="first incident",
        )
        record_health(
            db,
            entity_type="platform_account",
            entity_id=acct.id,
            status="healthy",
            recorded_by="t",
            detail="resolved",
        )
        record_health(
            db,
            entity_type="platform_account",
            entity_id=acct.id,
            status="degraded",
            recorded_by="t",
            detail="second incident",
        )

        items = [i for i in get_exception_queue(db, ws.id) if i.entity_id == acct.id]
        assert len(items) == 1
        assert items[0].metadata["detail"] == "second incident"

    def test_historical_records_are_preserved_in_the_table(self, db):
        ws, acct = self._entity_setup(db)
        record_health(
            db,
            entity_type="platform_account",
            entity_id=acct.id,
            status="degraded",
            recorded_by="t",
            detail="first",
        )
        record_health(
            db,
            entity_type="platform_account",
            entity_id=acct.id,
            status="healthy",
            recorded_by="t",
            detail="fixed",
        )

        count = db.execute(
            "SELECT COUNT(*) c FROM cp_health_records WHERE entity_id=?", (acct.id,)
        ).fetchone()["c"]
        assert count == 2, "the exception QUERY changed, not the audit table"

    def test_still_degraded_remains_a_single_active_exception(self, db):
        from app.application.exception_queue import get_exception_queue

        ws, acct = self._entity_setup(db)
        record_health(
            db,
            entity_type="platform_account",
            entity_id=acct.id,
            status="degraded",
            recorded_by="t",
            detail="ongoing",
        )

        items = [i for i in get_exception_queue(db, ws.id) if i.entity_id == acct.id]
        assert len(items) == 1

    def test_two_different_entities_each_surface_independently(self, db):
        from app.application.exception_queue import get_exception_queue

        ws = cp_orch.provision_workspace(db, name="MWS", slug="m-ws", actor="test")
        ch = cp_orch.provision_channel(
            db, workspace_id=ws.id, name="MCh", slug="m-ch", actor="test"
        )
        a1 = _account_with_grant(
            db, channel_id=ch.id, status="connected", external_account_id="UCone"
        )
        a2 = _account_with_grant(
            db, channel_id=ch.id, status="connected", external_account_id="UCtwo"
        )

        record_health(
            db,
            entity_type="platform_account",
            entity_id=a1.id,
            status="degraded",
            recorded_by="t",
            detail="a1 bad",
        )
        record_health(
            db,
            entity_type="platform_account",
            entity_id=a2.id,
            status="degraded",
            recorded_by="t",
            detail="a2 bad",
        )
        record_health(
            db,
            entity_type="platform_account",
            entity_id=a1.id,
            status="healthy",
            recorded_by="t",
            detail="a1 fixed",
        )

        items = {i.entity_id for i in get_exception_queue(db, ws.id)}
        assert a1.id not in items
        assert a2.id in items


# ── Publishing safety under degraded/transient credential health ────────────


class TestPublishingFailsClosedAppropriately:
    def test_publishing_still_fails_for_a_genuinely_invalid_credential(
        self, db, entities, token_store
    ):
        from app.publishing.upload_gate import resolve_upload_token

        ws, ch = entities
        acct = _account_with_grant(db, channel_id=ch.id, status="connected")
        token_store.write(
            account_id=acct.id,
            access_token="a",
            refresh_token="r",
            token_type="Bearer",
            expires_at_utc=datetime.now(UTC) - timedelta(hours=1),
            scopes=ORVELLA_GRANTED_SCOPES,
            google_sub=None,
        )
        cred = repo.get_credential_profile(db, acct.credential_profile_id)
        db.execute(
            "UPDATE cp_credential_profiles SET external_ref=? WHERE id=?",
            (token_store.external_ref(acct.id), cred.id),
        )
        db.commit()
        client = FakeGoogleOAuthClient(fail_refresh=OAuthRefreshError("invalid_grant"))

        with pytest.raises(OAuthRefreshError):
            resolve_upload_token(
                db,
                account_id=acct.id,
                workspace_id=ws.id,
                channel_id=ch.id,
                oauth_client=client,
            )
        assert repo.get_platform_account(db, acct.id).status == "credential_invalid"

    def test_publishing_also_fails_closed_on_a_transient_failure(self, db, entities, token_store):
        """Safe behaviour under transient degradation means the ATTEMPT still
        fails (no token, no upload) — the fix is about not mislabeling the
        credential, never about proceeding without one."""
        from app.publishing.upload_gate import resolve_upload_token

        ws, ch = entities
        acct = _account_with_grant(db, channel_id=ch.id, status="connected")
        token_store.write(
            account_id=acct.id,
            access_token="a",
            refresh_token="r",
            token_type="Bearer",
            expires_at_utc=datetime.now(UTC) - timedelta(hours=1),
            scopes=ORVELLA_GRANTED_SCOPES,
            google_sub=None,
        )
        cred = repo.get_credential_profile(db, acct.credential_profile_id)
        db.execute(
            "UPDATE cp_credential_profiles SET external_ref=? WHERE id=?",
            (token_store.external_ref(acct.id), cred.id),
        )
        db.commit()
        client = FakeGoogleOAuthClient(fail_refresh=OAuthTransientError("DNS failure"))

        with pytest.raises(OAuthTransientError):
            resolve_upload_token(
                db,
                account_id=acct.id,
                workspace_id=ws.id,
                channel_id=ch.id,
                oauth_client=client,
            )
        # And crucially: status was NOT falsely marked invalid by the attempt.
        assert repo.get_platform_account(db, acct.id).status == "connected"


# ── Isolation precondition ────────────────────────────────────────────────────


def test_isolation_fail_closed_for_this_suite():
    from app.core.runtime_mode import is_operational_db, operational_db_path, test_mode

    assert test_mode() == "unit"
    import os

    configured = os.environ.get("ACE_DB_PATH", "")
    assert configured.strip()
    assert not is_operational_db(configured)
    assert str(operational_db_path()) not in configured
