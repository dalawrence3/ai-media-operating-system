"""Phase 18C — scheduled publishing and explicit authorization.

Covers the two-level authorization model, its audit and immediate
revocation, the re-check between upload and release, the publication rate
limit, slot-driven timing (not-due / grace window / missed), pre-upload
revalidation, provider-health and OAuth blocks, upload and release
idempotency, uncertain-upload reconciliation, private-upload resume,
external-public/local-stale reconciliation, concurrent-worker protection,
bounded retries, queue capacity after success, the analytics handoff,
multi-channel isolation, and — the safety property that matters most —
that NO public side effect occurs while either authorization layer is false.

Every provider interaction is faked. This suite performs no network calls
and can never touch a real YouTube channel.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.control_plane import repository as cp_repo
from app.control_plane.models import ChannelDraft, WorkspaceDraft
from app.core.database import open_db
from app.intelligence.autonomy.models import PublishOutcome, PublishStatus
from app.intelligence.autonomy.publishing_cycle import run_publishing_cycle
from app.intelligence.autonomy.repository import (
    find_slot_ready_to_publish,
    get_slot,
    reschedule_slot_to_new_time,
)
from app.publishing.authorization import (
    BlockReason,
    evaluate_publishing_authorization,
    get_channel_publishing_authorization,
    grant_channel_publishing_authorization,
    revoke_channel_publishing_authorization,
    update_publishing_limits,
)


def _uid() -> str:
    return str(uuid.uuid4())


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


# ── Fakes ─────────────────────────────────────────────────────────────────────


class _FakeConfig:
    """Stands in for the process Config so the two global env gates can be
    driven independently without touching the real environment."""

    def __init__(self, publishing: bool = True, release: bool = True):
        self.publishing_live_enabled = publishing
        self.release_public_enabled = release


class _FakeYTClient:
    """Models YouTube's own state, independent of what the local DB believes.

    That independence is the entire point: reconciliation exists precisely
    for the cases where the two disagree.
    """

    def __init__(
        self,
        *,
        privacy_status: str = "private",
        recent_videos: list[dict] | None = None,
        raise_on_get: Exception | None = None,
        raise_on_update: Exception | None = None,
        video_missing: bool = False,
    ):
        self.privacy_status = privacy_status
        self.recent_videos = recent_videos or []
        self.raise_on_get = raise_on_get
        self.raise_on_update = raise_on_update
        self.video_missing = video_missing
        self.update_calls: list[tuple[str, dict]] = []
        self.get_calls: list[str] = []

    def get_video(self, video_id: str, parts: list[str]) -> dict:
        self.get_calls.append(video_id)
        if self.raise_on_get:
            raise self.raise_on_get
        if self.video_missing:
            return {"items": []}
        return {"items": [{"id": video_id, "status": {"privacyStatus": self.privacy_status}}]}

    def update_video(self, video_id: str, snippet: dict, status: dict) -> dict:
        if self.raise_on_update:
            raise self.raise_on_update
        self.update_calls.append((video_id, status))
        self.privacy_status = status.get("privacyStatus", self.privacy_status)
        return {"id": video_id, "status": status}

    def list_my_recent_videos(self, max_results: int = 10) -> list[dict]:
        return list(self.recent_videos)[:max_results]


class _FakeProvider:
    """Publishing provider whose upload can be made to succeed or fail."""

    provider_name = "youtube"
    provider_version = "test-1.0"

    def __init__(
        self,
        *,
        video_id: str = "yt_test_vid_001",
        raise_on_upload: Exception | None = None,
    ):
        self.video_id = video_id
        self.raise_on_upload = raise_on_upload
        self.upload_calls: list = []

    def prepare_package(self, package):
        return package

    def upload(self, package):
        self.upload_calls.append(package)
        if self.raise_on_upload:
            raise self.raise_on_upload
        from app.publishing.protocol import UploadResult

        return UploadResult(
            provider_video_id=self.video_id,
            provider_url=f"https://youtu.be/{self.video_id}",
            provider_response={"id": self.video_id},
        )


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def db(tmp_path: Path):
    conn = open_db(tmp_path / "publishing_test.db")
    conn.execute("PRAGMA foreign_keys = OFF")
    yield conn
    conn.close()


@pytest.fixture()
def workspace(db):
    ws = cp_repo.create_workspace(
        db, WorkspaceDraft(id=_uid(), name="Test WS", slug=f"ws-{_uid()[:8]}", actor="cli")
    )
    db.commit()
    return ws


@pytest.fixture()
def channel(db, workspace):
    ch = cp_repo.create_channel(
        db,
        ChannelDraft(
            id=_uid(),
            workspace_id=workspace.id,
            name="Test Channel",
            slug="test-channel",
            actor="cli",
        ),
    )
    db.commit()
    return ch


@pytest.fixture(autouse=True)
def _permissive_config(monkeypatch):
    """Default both global gates ON so tests exercise the channel layer.

    Tests that care about the global gates pass their own config explicitly.
    This never touches the real environment.
    """
    import app.publishing.authorization as auth_mod

    # Grant the release scope by default, the same way both global gates are
    # defaulted on: these tests exercise the OTHER authorization layers, and a
    # test environment has no token store to read a real scope from. Tests that
    # care about the release scope override this explicitly.
    auth_mod._REAL_HAS_RELEASE_SCOPE = auth_mod._has_release_scope
    monkeypatch.setattr(auth_mod, "_has_release_scope", lambda *a, **k: True)

    real = auth_mod.evaluate_publishing_authorization

    def _patched(conn, *, channel_id, config=None, exclude_publication_id=None):
        return real(
            conn,
            channel_id=channel_id,
            config=config or _FakeConfig(),
            exclude_publication_id=exclude_publication_id,
        )

    monkeypatch.setattr(auth_mod, "evaluate_publishing_authorization", _patched)
    import app.intelligence.autonomy.publishing_cycle as pc

    monkeypatch.setattr(pc, "evaluate_publishing_authorization", _patched)
    yield


def _seed_account(conn: sqlite3.Connection, channel_id: str, *, status: str = "connected") -> str:
    account_id = _uid()
    now = _iso(datetime.now(UTC))
    conn.execute(
        "INSERT OR IGNORE INTO cp_platforms (id, platform_key, display_name, created_at) "
        "VALUES ('yt', 'youtube', 'YouTube', ?)",
        (now,),
    )
    conn.execute(
        """INSERT INTO cp_platform_accounts
           (id, channel_id, platform_id, platform_key, external_account_id,
            display_name, status, actor, created_at, updated_at)
           VALUES (?, ?, 'yt', 'youtube', ?, 'Test Account', ?, 'cli', ?, ?)""",
        (account_id, channel_id, f"UC{_uid()[:16]}", status, now, now),
    )
    conn.commit()
    return account_id


def _seed_ready_slot(
    conn: sqlite3.Connection,
    channel_id: str,
    workspace_id: str,
    *,
    scheduled_for_utc: str,
    tmp_path: Path,
    experiment_id: str | None = None,
    slot_key: str | None = None,
) -> tuple[int, int]:
    """Create a slot in exactly the state Phase 18B leaves behind: filled,
    production ready, with a real approved render and draft publishing plan.

    Returns (slot_id, plan_id).
    """
    now = _iso(datetime.now(UTC))
    exp_id = experiment_id or f"exp-{_uid()[:8]}"

    video = tmp_path / f"render_{_uid()[:8]}.mp4"
    video.write_bytes(b"\x00" * 2048)

    conn.execute(
        "INSERT OR IGNORE INTO topics (id, title, angle, status, created_at, updated_at) "
        "VALUES (1, 'Test Topic', 'test angle', 'approved', ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO scripts (topic_id, body, status, created_at, updated_at) "
        "VALUES (1, 'script body text', 'approved', ?, ?)",
        (now, now),
    )
    script_id = conn.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
    conn.execute(
        """INSERT INTO production_plans
           (topic_id, script_id, script_version, input_hash, script_body_hash,
            plan_schema_version, renderer_version, duration_algorithm_version,
            status, created_at, updated_at)
           VALUES (1, ?, 1, ?, ?, 'v1', 'v1', 'v1', 'approved', ?, ?)""",
        (script_id, _uid()[:16], _uid()[:16], now, now),
    )
    plan_prod_id = conn.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
    conn.execute(
        """INSERT INTO narration_runs
           (plan_id, plan_input_hash, voice_profile_id, voice_profile_version,
            language, speaking_rate, settings_json, output_format, sample_rate_hz,
            input_hash, status, created_at, updated_at)
           VALUES (?, ?, 1, 1, 'en', 1.0, '{}', 'mp3', 44100, ?, 'approved', ?, ?)""",
        (plan_prod_id, _uid()[:16], _uid()[:16], now, now),
    )
    narration_id = conn.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
    conn.execute(
        """INSERT INTO caption_runs
           (narration_run_id, plan_id, script_id, topic_id, input_hash,
            caption_schema_version, segmentation_version, timing_algorithm_version,
            style_version, exporter_version, status, created_at, updated_at)
           VALUES (?, ?, ?, 1, ?, 'v1', 'v1', 'v1', 'v1', 'v1', 'approved', ?, ?)""",
        (narration_id, plan_prod_id, script_id, _uid()[:16], now, now),
    )
    caption_id = conn.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
    conn.execute(
        """INSERT INTO scene_manifests
           (caption_run_id, narration_run_id, plan_id, script_id, topic_id,
            input_hash, manifest_schema_version, planner_version,
            status, created_at, updated_at)
           VALUES (?, ?, ?, ?, 1, ?, 'v1', 'v1', 'approved', ?, ?)""",
        (caption_id, narration_id, plan_prod_id, script_id, _uid()[:16], now, now),
    )
    scene_id = conn.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]

    conn.execute(
        """INSERT INTO render_manifests
           (scene_manifest_id, narration_run_id, caption_run_id, topic_id, plan_id,
            script_id, experiment_id, input_hash, render_schema_version,
            compositor_version, total_scene_count, total_duration_ms,
            width, height, fps, status, approved_at, created_at, updated_at)
           VALUES (?, ?, ?, 1, ?, ?, ?, ?, 'v1', 'v1', 4, 60000, 1080, 1920, 30,
                   'approved', ?, ?, ?)""",
        (
            scene_id,
            narration_id,
            caption_id,
            plan_prod_id,
            script_id,
            exp_id,
            _uid()[:16],
            now,
            now,
            now,
        ),
    )
    rm_id = conn.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]

    conn.execute(
        """INSERT INTO render_jobs
           (render_manifest_id, backend_version, width, height, fps,
            status, output_path, output_sha256, duration_s,
            video_codec, audio_codec, created_at, updated_at)
           VALUES (?, 'v1', 1080, 1920, 30, 'completed', ?, ?, 60.0,
                   'h264', 'aac', ?, ?)""",
        (rm_id, str(video), "a" * 64, now, now),
    )

    # Use the real repository function rather than a hand-rolled insert, so the
    # plan under test is byte-for-byte the shape production creates.
    from app.publishing.models import PublishingMetadataDraft, PublishingScheduleDraft
    from app.publishing.repository import create_publishing_plan

    rj_id = conn.execute(
        "SELECT id FROM render_jobs WHERE render_manifest_id = ?", (rm_id,)
    ).fetchone()["id"]
    plan = create_publishing_plan(
        conn,
        render_manifest_id=rm_id,
        render_job_id=rj_id,
        topic_id=1,
        production_plan_id=plan_prod_id,
        script_id=script_id,
        scene_manifest_id=scene_id,
        narration_run_id=narration_id,
        caption_run_id=caption_id,
        experiment_id=exp_id,
        input_hash=_uid()[:16],
        provider="youtube",
        provider_version="test-1.0",
        metadata=PublishingMetadataDraft(
            title="Autonomous Test Video",
            description="desc",
            tags=[],
            language="en",
            visibility="private",
        ),
        schedule=PublishingScheduleDraft(
            schedule_type="scheduled", scheduled_at=scheduled_for_utc, timezone="UTC"
        ),
    )
    plan_id = plan.id

    conn.execute(
        """INSERT INTO publishing_slots
           (channel_id, workspace_id, slot_key, scheduled_for_local, timezone,
            scheduled_for_utc, state, brief_id, opportunity_id, reserved_at, filled_at,
            created_at, updated_at, experiment_id, production_status,
            production_publishing_plan_id, production_ready_at)
           VALUES (?, ?, ?, ?, 'UTC', ?, 'filled', ?, 1, ?, ?, ?, ?, ?, 'ready', ?, ?)""",
        (
            channel_id,
            workspace_id,
            slot_key or f"slot-{_uid()[:8]}",
            scheduled_for_utc,
            scheduled_for_utc,
            _uid(),
            now,
            now,
            now,
            now,
            exp_id,
            plan_id,
            now,
        ),
    )
    slot_id = conn.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
    conn.commit()
    return slot_id, plan_id


def _run(db, channel, workspace, *, provider=None, yt_client=None, now=None):
    """Invoke the cycle with injected fakes."""
    prov = provider if provider is not None else _FakeProvider()
    yt = yt_client if yt_client is not None else _FakeYTClient()
    return run_publishing_cycle(
        db,
        cp_channel_id=channel.id,
        workspace_id=workspace.id,
        provider_factory=lambda conn, **kw: prov,
        yt_client_factory=lambda conn, **kw: yt,
        now=now,
    )


def _due_time() -> str:
    return _iso(datetime.now(UTC) - timedelta(minutes=5))


def _future_time(hours: int = 6) -> str:
    return _iso(datetime.now(UTC) + timedelta(hours=hours))


# ── Authorization: the two levels ─────────────────────────────────────────────


def test_channel_authorization_is_false_by_default(db, channel):
    """A channel that has never been configured is not authorized. Absence of
    a row must never be read as permission."""
    assert get_channel_publishing_authorization(db, channel.id) is None
    decision = evaluate_publishing_authorization(db, channel_id=channel.id, config=_FakeConfig())
    assert decision.allowed is False
    assert BlockReason.channel_not_authorized in decision.blocked_by


def test_global_publishing_gate_alone_blocks_everything(db, channel, workspace):
    _seed_account(db, channel.id)
    grant_channel_publishing_authorization(
        db, channel_id=channel.id, workspace_id=workspace.id, actor="operator"
    )
    decision = evaluate_publishing_authorization(
        db, channel_id=channel.id, config=_FakeConfig(publishing=False, release=True)
    )
    assert decision.allowed is False
    assert BlockReason.global_publishing_gate_off in decision.blocked_by
    assert decision.channel_authorized is True  # channel layer passed; global did not


def test_global_release_gate_alone_blocks_everything(db, channel, workspace):
    _seed_account(db, channel.id)
    grant_channel_publishing_authorization(
        db, channel_id=channel.id, workspace_id=workspace.id, actor="operator"
    )
    decision = evaluate_publishing_authorization(
        db, channel_id=channel.id, config=_FakeConfig(publishing=True, release=False)
    )
    assert decision.allowed is False
    assert BlockReason.global_release_gate_off in decision.blocked_by


def test_both_global_gates_on_but_channel_unauthorized_still_blocks(db, channel):
    _seed_account(db, channel.id)
    decision = evaluate_publishing_authorization(db, channel_id=channel.id, config=_FakeConfig())
    assert decision.allowed is False
    assert BlockReason.channel_not_authorized in decision.blocked_by


def test_all_layers_true_allows(db, channel, workspace):
    _seed_account(db, channel.id)
    grant_channel_publishing_authorization(
        db, channel_id=channel.id, workspace_id=workspace.id, actor="operator"
    )
    decision = evaluate_publishing_authorization(db, channel_id=channel.id, config=_FakeConfig())
    assert decision.allowed is True
    assert decision.blocked_by == []


def test_authorization_is_not_inferred_from_automation_policy(db, channel, workspace):
    """Turning on decision and production automation must not authorize
    publishing — the whole point of keeping three separate controls."""
    from app.intelligence.autonomy.repository import upsert_autonomy_policy

    _seed_account(db, channel.id)
    upsert_autonomy_policy(
        db,
        channel_id=channel.id,
        workspace_id=workspace.id,
        actor="test",
        decision_automation_enabled=True,
        production_automation_enabled=True,
        timezone="UTC",
    )
    db.commit()
    decision = evaluate_publishing_authorization(db, channel_id=channel.id, config=_FakeConfig())
    assert decision.allowed is False
    assert BlockReason.channel_not_authorized in decision.blocked_by


# ── Authorization: audit and revocation ───────────────────────────────────────


def test_grant_records_who_and_when_and_emits_audit_event(db, channel, workspace):
    auth = grant_channel_publishing_authorization(
        db, channel_id=channel.id, workspace_id=workspace.id, actor="operator:alice"
    )
    assert auth.authorized is True
    assert auth.authorized_by == "operator:alice"
    assert auth.authorized_at is not None
    assert auth.policy_version == 1

    events = db.execute(
        "SELECT event_type, actor FROM cp_events "
        "WHERE event_type = 'channel.publishing_authorization_granted'"
    ).fetchall()
    assert len(events) == 1
    assert events[0]["actor"] == "operator:alice"


def test_revocation_is_immediate_and_audited(db, channel, workspace):
    _seed_account(db, channel.id)
    grant_channel_publishing_authorization(
        db, channel_id=channel.id, workspace_id=workspace.id, actor="operator:alice"
    )
    assert (
        evaluate_publishing_authorization(db, channel_id=channel.id, config=_FakeConfig()).allowed
        is True
    )

    auth = revoke_channel_publishing_authorization(
        db,
        channel_id=channel.id,
        workspace_id=workspace.id,
        actor="operator:bob",
        reason="emergency stop",
    )
    assert auth.authorized is False
    assert auth.revoked_by == "operator:bob"
    assert auth.policy_version == 2

    # Takes effect on the very next evaluation, with no restart or cache flush.
    assert (
        evaluate_publishing_authorization(db, channel_id=channel.id, config=_FakeConfig()).allowed
        is False
    )

    events = db.execute(
        "SELECT payload_json FROM cp_events "
        "WHERE event_type = 'channel.publishing_authorization_revoked'"
    ).fetchall()
    assert len(events) == 1
    assert "emergency stop" in events[0]["payload_json"]


def test_updating_limits_never_grants_authorization(db, channel, workspace):
    """Tuning a rate limit must not be the thing that authorizes a channel."""
    update_publishing_limits(
        db,
        channel_id=channel.id,
        workspace_id=workspace.id,
        actor="operator",
        max_publications_per_24h=5,
    )
    auth = get_channel_publishing_authorization(db, channel.id)
    assert auth is not None
    assert auth.authorized is False
    assert auth.max_publications_per_24h == 5


def test_revoking_a_never_authorized_channel_is_still_audited(db, channel, workspace):
    auth = revoke_channel_publishing_authorization(
        db, channel_id=channel.id, workspace_id=workspace.id, actor="operator"
    )
    assert auth.authorized is False
    assert auth.revoked_at is not None


# ── Rate limit ────────────────────────────────────────────────────────────────


def test_rate_limit_blocks_when_reached(db, channel, workspace, tmp_path):
    _seed_account(db, channel.id)
    grant_channel_publishing_authorization(
        db,
        channel_id=channel.id,
        workspace_id=workspace.id,
        actor="operator",
        max_publications_per_24h=1,
    )
    now = _iso(datetime.now(UTC))
    db.execute(
        """INSERT INTO publications
           (publishing_plan_id, publishing_job_id, provider, provider_version,
            provider_video_id, status, visibility, publishing_engine_version,
            input_hash, output_sha256, channel_id, workspace_id, created_at, updated_at)
           VALUES (1, 1, 'youtube', 'v1', 'existing_vid', 'published', 'public', 'v1',
                   'h', 's', ?, ?, ?, ?)""",
        (channel.id, workspace.id, now, now),
    )
    db.commit()

    decision = evaluate_publishing_authorization(db, channel_id=channel.id, config=_FakeConfig())
    assert decision.allowed is False
    assert BlockReason.rate_limit_reached in decision.blocked_by
    assert decision.publications_last_24h == 1


def test_rate_limit_ignores_publications_older_than_24h(db, channel, workspace):
    _seed_account(db, channel.id)
    grant_channel_publishing_authorization(
        db,
        channel_id=channel.id,
        workspace_id=workspace.id,
        actor="operator",
        max_publications_per_24h=1,
    )
    old = _iso(datetime.now(UTC) - timedelta(hours=30))
    db.execute(
        """INSERT INTO publications
           (publishing_plan_id, publishing_job_id, provider, provider_version,
            provider_video_id, status, visibility, publishing_engine_version,
            input_hash, output_sha256, channel_id, workspace_id, created_at, updated_at)
           VALUES (1, 1, 'youtube', 'v1', 'old_vid', 'published', 'public', 'v1',
                   'h', 's', ?, ?, ?, ?)""",
        (channel.id, workspace.id, old, old),
    )
    db.commit()
    decision = evaluate_publishing_authorization(db, channel_id=channel.id, config=_FakeConfig())
    assert decision.allowed is True
    assert decision.publications_last_24h == 0


def test_rate_limited_cycle_does_not_upload(db, channel, workspace, tmp_path):
    _seed_account(db, channel.id)
    grant_channel_publishing_authorization(
        db,
        channel_id=channel.id,
        workspace_id=workspace.id,
        actor="operator",
        max_publications_per_24h=1,
    )
    now = _iso(datetime.now(UTC))
    db.execute(
        """INSERT INTO publications
           (publishing_plan_id, publishing_job_id, provider, provider_version,
            provider_video_id, status, visibility, publishing_engine_version,
            input_hash, output_sha256, channel_id, workspace_id, created_at, updated_at)
           VALUES (1, 1, 'youtube', 'v1', 'existing_vid', 'published', 'public', 'v1',
                   'h', 's', ?, ?, ?, ?)""",
        (channel.id, workspace.id, now, now),
    )
    db.commit()
    _seed_ready_slot(db, channel.id, workspace.id, scheduled_for_utc=_due_time(), tmp_path=tmp_path)

    provider = _FakeProvider()
    result = _run(db, channel, workspace, provider=provider)

    assert result.outcome is PublishOutcome.BLOCKED
    assert result.failure_category == "RATE_LIMIT_BLOCKED"
    assert provider.upload_calls == []


# ── Account / provider health ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "status", ["disconnected", "credential_invalid", "quota_limited", "paused"]
)
def test_unhealthy_account_blocks_publishing(db, channel, workspace, status):
    _seed_account(db, channel.id, status=status)
    grant_channel_publishing_authorization(
        db, channel_id=channel.id, workspace_id=workspace.id, actor="operator"
    )
    decision = evaluate_publishing_authorization(db, channel_id=channel.id, config=_FakeConfig())
    assert decision.allowed is False
    assert BlockReason.account_unhealthy in decision.blocked_by


def test_credential_expiring_does_not_block(db, channel, workspace):
    """The refresh path handles an expiring credential; blocking on it would
    stall publishing for a credential that still works."""
    _seed_account(db, channel.id, status="credential_expiring")
    grant_channel_publishing_authorization(
        db, channel_id=channel.id, workspace_id=workspace.id, actor="operator"
    )
    decision = evaluate_publishing_authorization(db, channel_id=channel.id, config=_FakeConfig())
    assert decision.allowed is True


def test_no_account_at_all_blocks(db, channel, workspace):
    grant_channel_publishing_authorization(
        db, channel_id=channel.id, workspace_id=workspace.id, actor="operator"
    )
    decision = evaluate_publishing_authorization(db, channel_id=channel.id, config=_FakeConfig())
    assert decision.allowed is False
    assert BlockReason.no_account in decision.blocked_by


# ── Slot-driven timing ────────────────────────────────────────────────────────


def _authorize(db, channel, workspace, **kw):
    _seed_account(db, channel.id)
    return grant_channel_publishing_authorization(
        db, channel_id=channel.id, workspace_id=workspace.id, actor="operator", **kw
    )


def test_future_slot_does_not_publish_early(db, channel, workspace, tmp_path):
    _authorize(db, channel, workspace)
    _seed_ready_slot(
        db, channel.id, workspace.id, scheduled_for_utc=_future_time(6), tmp_path=tmp_path
    )
    provider = _FakeProvider()
    result = _run(db, channel, workspace, provider=provider)

    assert result.outcome is PublishOutcome.NOT_DUE
    assert provider.upload_calls == []


def test_due_slot_within_grace_window_publishes(db, channel, workspace, tmp_path):
    _authorize(db, channel, workspace)
    slot_id, _ = _seed_ready_slot(
        db, channel.id, workspace.id, scheduled_for_utc=_due_time(), tmp_path=tmp_path
    )
    provider = _FakeProvider()
    yt = _FakeYTClient()
    result = _run(db, channel, workspace, provider=provider, yt_client=yt)

    assert result.outcome is PublishOutcome.RELEASED, result.reason
    assert result.uploaded is True
    assert result.released is True
    assert len(provider.upload_calls) == 1
    assert yt.privacy_status == "public"
    assert get_slot(db, slot_id).publish_status is PublishStatus.released


def test_slot_past_grace_window_is_marked_missed_not_published(db, channel, workspace, tmp_path):
    _authorize(db, channel, workspace, missed_slot_grace_minutes=30)
    slot_id, _ = _seed_ready_slot(
        db,
        channel.id,
        workspace.id,
        scheduled_for_utc=_iso(datetime.now(UTC) - timedelta(hours=5)),
        tmp_path=tmp_path,
    )
    provider = _FakeProvider()
    result = _run(db, channel, workspace, provider=provider)

    assert result.outcome is PublishOutcome.MISSED
    assert result.failure_category == "MISSED_SLOT"
    assert provider.upload_calls == []
    assert get_slot(db, slot_id).publish_status is PublishStatus.skipped_missed


def test_a_missed_slot_is_never_picked_up_again(db, channel, workspace, tmp_path):
    """The Phase 18B slot's exact situation: already missed, must not publish
    just because publishing became enabled."""
    _authorize(db, channel, workspace, missed_slot_grace_minutes=30)
    _seed_ready_slot(
        db,
        channel.id,
        workspace.id,
        scheduled_for_utc=_iso(datetime.now(UTC) - timedelta(days=2)),
        tmp_path=tmp_path,
    )
    _run(db, channel, workspace)  # marks it missed

    provider = _FakeProvider()
    second = _run(db, channel, workspace, provider=provider)
    assert second.outcome is PublishOutcome.NO_SLOT_TO_PUBLISH
    assert provider.upload_calls == []


def test_rescheduling_preserves_missed_history_and_creates_a_new_slot(
    db, channel, workspace, tmp_path
):
    _authorize(db, channel, workspace, missed_slot_grace_minutes=30)
    old_time = _iso(datetime.now(UTC) - timedelta(days=2))
    slot_id, plan_id = _seed_ready_slot(
        db, channel.id, workspace.id, scheduled_for_utc=old_time, tmp_path=tmp_path
    )
    _run(db, channel, workspace)
    assert get_slot(db, slot_id).publish_status is PublishStatus.skipped_missed

    new_time = _future_time(3)
    new_slot = reschedule_slot_to_new_time(
        db,
        slot_id,
        new_scheduled_for_utc=new_time,
        new_scheduled_for_local=new_time,
        new_slot_key="verification-slot",
        timezone="UTC",
        actor="operator",
    )

    # Original keeps its honest history.
    old = get_slot(db, slot_id)
    assert old.scheduled_for_utc == old_time
    assert old.publish_status is PublishStatus.skipped_missed

    # New slot carries the production lineage forward and is publishable.
    assert new_slot.id != slot_id
    assert new_slot.rescheduled_from_slot_id == slot_id
    assert new_slot.production_publishing_plan_id == plan_id
    assert new_slot.publish_status is None
    assert find_slot_ready_to_publish(db, channel.id).id == new_slot.id


# ── The upload/release split and the re-check between them ────────────────────


def test_authorization_revoked_between_upload_and_release_stops_the_release(
    db, channel, workspace, tmp_path, monkeypatch
):
    """The single most important safety property in this phase."""
    _authorize(db, channel, workspace)
    slot_id, _ = _seed_ready_slot(
        db, channel.id, workspace.id, scheduled_for_utc=_due_time(), tmp_path=tmp_path
    )

    provider = _FakeProvider()
    yt = _FakeYTClient()

    # Revoke authorization at the exact moment the upload completes.
    original_upload = provider.upload

    def _upload_then_revoke(package):
        res = original_upload(package)
        revoke_channel_publishing_authorization(
            db,
            channel_id=channel.id,
            workspace_id=workspace.id,
            actor="operator",
            reason="mid-flight emergency stop",
        )
        return res

    provider.upload = _upload_then_revoke

    result = _run(db, channel, workspace, provider=provider, yt_client=yt)

    assert result.outcome is PublishOutcome.UPLOADED_PENDING_RELEASE
    assert result.uploaded is True
    assert result.released is False
    # The decisive assertion: no public release was attempted.
    assert yt.update_calls == []
    assert yt.privacy_status == "private"

    slot = get_slot(db, slot_id)
    assert slot.publish_status is PublishStatus.uploaded
    assert slot.publish_failure_category == "AUTHORIZATION_BLOCKED"


def test_uploaded_slot_resumes_at_release_without_reuploading(db, channel, workspace, tmp_path):
    _authorize(db, channel, workspace)
    slot_id, _ = _seed_ready_slot(
        db, channel.id, workspace.id, scheduled_for_utc=_due_time(), tmp_path=tmp_path
    )

    # First run uploads, then authorization is withdrawn before release.
    provider = _FakeProvider()
    yt = _FakeYTClient()
    original_upload = provider.upload

    def _upload_then_revoke(package):
        res = original_upload(package)
        revoke_channel_publishing_authorization(
            db, channel_id=channel.id, workspace_id=workspace.id, actor="operator"
        )
        return res

    provider.upload = _upload_then_revoke
    _run(db, channel, workspace, provider=provider, yt_client=yt)
    assert len(provider.upload_calls) == 1

    # Operator restores authorization; the next cycle resumes at release.
    grant_channel_publishing_authorization(
        db, channel_id=channel.id, workspace_id=workspace.id, actor="operator"
    )
    provider2 = _FakeProvider()
    result = _run(db, channel, workspace, provider=provider2, yt_client=yt)

    assert result.outcome is PublishOutcome.RELEASED, result.reason
    assert provider2.upload_calls == [], "must not re-upload an already-uploaded slot"
    assert yt.privacy_status == "public"
    assert get_slot(db, slot_id).publish_status is PublishStatus.released


def test_upload_always_uses_private_visibility(db, channel, workspace, tmp_path):
    """An upload can never itself publish, whatever the plan says."""
    _authorize(db, channel, workspace)
    _seed_ready_slot(db, channel.id, workspace.id, scheduled_for_utc=_due_time(), tmp_path=tmp_path)
    db.execute("UPDATE publishing_plans SET visibility = 'public'")
    db.commit()

    provider = _FakeProvider()
    _run(db, channel, workspace, provider=provider)
    assert provider.upload_calls[0].visibility == "private"


# ── Idempotency and reconciliation ────────────────────────────────────────────


def test_release_is_idempotent_when_youtube_is_already_public(db, channel, workspace, tmp_path):
    """Local state stale, provider already public: reconcile, do not re-update."""
    _authorize(db, channel, workspace)
    slot_id, _ = _seed_ready_slot(
        db, channel.id, workspace.id, scheduled_for_utc=_due_time(), tmp_path=tmp_path
    )
    yt = _FakeYTClient(privacy_status="public")
    result = _run(db, channel, workspace, yt_client=yt)

    assert result.outcome is PublishOutcome.RELEASED
    assert yt.update_calls == [], "must not issue an update for an already-public video"
    assert get_slot(db, slot_id).publish_status is PublishStatus.released


def test_rerunning_after_release_is_a_pure_no_op(db, channel, workspace, tmp_path):
    _authorize(db, channel, workspace, max_publications_per_24h=5)
    _seed_ready_slot(db, channel.id, workspace.id, scheduled_for_utc=_due_time(), tmp_path=tmp_path)
    first = _run(db, channel, workspace)
    assert first.outcome is PublishOutcome.RELEASED

    before = db.execute("SELECT COUNT(*) n FROM publications").fetchone()["n"]
    provider = _FakeProvider()
    second = _run(db, channel, workspace, provider=provider)

    assert second.outcome is PublishOutcome.NO_SLOT_TO_PUBLISH
    assert provider.upload_calls == []
    assert db.execute("SELECT COUNT(*) n FROM publications").fetchone()["n"] == before


def test_uncertain_upload_is_not_retried_blindly(db, channel, workspace, tmp_path):
    """A timeout mid-upload must never lead to a second upload attempt."""
    _authorize(db, channel, workspace)
    slot_id, _ = _seed_ready_slot(
        db, channel.id, workspace.id, scheduled_for_utc=_due_time(), tmp_path=tmp_path
    )

    flaky = _FakeProvider(raise_on_upload=TimeoutError("connection reset mid-transfer"))
    first = _run(db, channel, workspace, provider=flaky)
    assert first.failure_category == "UPLOAD_STATE_UNCERTAIN"
    assert len(flaky.upload_calls) == 1

    # Provider reports no matching video → attempt resolved as never-uploaded.
    provider2 = _FakeProvider()
    yt_empty = _FakeYTClient(recent_videos=[])
    second = _run(db, channel, workspace, provider=provider2, yt_client=yt_empty)
    assert second.outcome is PublishOutcome.RELEASED, second.reason
    assert len(provider2.upload_calls) == 1


def test_uncertain_upload_that_actually_succeeded_is_adopted_not_duplicated(
    db, channel, workspace, tmp_path
):
    """The duplicate-prevention case that matters: the upload DID land."""
    _authorize(db, channel, workspace)
    _seed_ready_slot(db, channel.id, workspace.id, scheduled_for_utc=_due_time(), tmp_path=tmp_path)

    flaky = _FakeProvider(raise_on_upload=TimeoutError("connection reset mid-transfer"))
    _run(db, channel, workspace, provider=flaky)

    # The provider actually has the video, under the plan's title.
    yt = _FakeYTClient(
        recent_videos=[{"video_id": "orphan_vid_42", "title": "Autonomous Test Video"}]
    )
    provider2 = _FakeProvider(video_id="would_be_a_duplicate")
    result = _run(db, channel, workspace, provider=provider2, yt_client=yt)

    assert provider2.upload_calls == [], "must NOT upload a second copy"
    assert result.provider_video_id == "orphan_vid_42"
    assert result.outcome in (PublishOutcome.RELEASED, PublishOutcome.UPLOADED_PENDING_RELEASE)


def test_uncertain_upload_stays_uncertain_when_provider_cannot_be_queried(
    db, channel, workspace, tmp_path
):
    """Refusing to publish is recoverable; a duplicate public video is not."""
    _authorize(db, channel, workspace)
    _seed_ready_slot(db, channel.id, workspace.id, scheduled_for_utc=_due_time(), tmp_path=tmp_path)
    flaky = _FakeProvider(raise_on_upload=TimeoutError("connection reset"))
    _run(db, channel, workspace, provider=flaky)

    class _BrokenLookupClient(_FakeYTClient):
        def list_my_recent_videos(self, max_results: int = 10):
            raise RuntimeError("provider unreachable")

    provider2 = _FakeProvider()
    result = _run(db, channel, workspace, provider=provider2, yt_client=_BrokenLookupClient())
    assert result.outcome is PublishOutcome.NEEDS_RECONCILIATION
    assert provider2.upload_calls == []


def test_upload_failure_that_proves_no_video_is_terminal_not_uncertain(
    db, channel, workspace, tmp_path
):
    _authorize(db, channel, workspace)
    _seed_ready_slot(db, channel.id, workspace.id, scheduled_for_utc=_due_time(), tmp_path=tmp_path)
    provider = _FakeProvider(raise_on_upload=RuntimeError("Video file not found: x.mp4"))
    result = _run(db, channel, workspace, provider=provider)
    assert result.failure_category == "UPLOAD_FAILED_TERMINAL"


# ── Pre-upload revalidation ───────────────────────────────────────────────────


def test_render_unapproved_since_production_blocks_upload(db, channel, workspace, tmp_path):
    _authorize(db, channel, workspace)
    _seed_ready_slot(db, channel.id, workspace.id, scheduled_for_utc=_due_time(), tmp_path=tmp_path)
    db.execute("UPDATE render_manifests SET status = 'rejected'")
    db.commit()

    provider = _FakeProvider()
    result = _run(db, channel, workspace, provider=provider)
    assert result.outcome is PublishOutcome.FAILED
    assert result.failure_category == "PREUPLOAD_VALIDATION_FAILED"
    assert provider.upload_calls == []


def test_missing_video_file_blocks_upload(db, channel, workspace, tmp_path):
    _authorize(db, channel, workspace)
    _seed_ready_slot(db, channel.id, workspace.id, scheduled_for_utc=_due_time(), tmp_path=tmp_path)
    path = db.execute("SELECT output_path FROM render_jobs").fetchone()["output_path"]
    Path(path).unlink()

    provider = _FakeProvider()
    result = _run(db, channel, workspace, provider=provider)
    assert result.outcome is PublishOutcome.FAILED
    assert result.failure_category == "PREUPLOAD_VALIDATION_FAILED"
    assert provider.upload_calls == []
    assert result.preflight_passed is False


# ── Concurrency, retries, queue ───────────────────────────────────────────────


def test_concurrent_worker_is_blocked_by_the_slot_lease(db, channel, workspace, tmp_path):
    _authorize(db, channel, workspace)
    slot_id, _ = _seed_ready_slot(
        db, channel.id, workspace.id, scheduled_for_utc=_due_time(), tmp_path=tmp_path
    )
    from app.control_plane.jobs import start_operation

    start_operation(
        db,
        workspace_id=workspace.id,
        operation_type="autonomy_publishing_cycle",
        idempotency_key=f"autonomy_publishing:{channel.id}:{slot_id}",
        actor="worker-1",
        input_data={},
    )
    db.commit()

    provider = _FakeProvider()
    result = _run(db, channel, workspace, provider=provider)
    assert result.outcome is PublishOutcome.ALREADY_RUNNING
    assert result.already_running is True
    assert provider.upload_calls == []


def test_retries_are_bounded(db, channel, workspace, tmp_path):
    _authorize(db, channel, workspace)
    _seed_ready_slot(db, channel.id, workspace.id, scheduled_for_utc=_due_time(), tmp_path=tmp_path)
    db.execute("UPDATE render_manifests SET status = 'rejected'")  # always fails preflight
    db.commit()

    from app.intelligence.autonomy.repository import MAX_PUBLISH_RETRIES

    attempts = 0
    for _ in range(MAX_PUBLISH_RETRIES + 3):
        result = _run(db, channel, workspace)
        if result.outcome is PublishOutcome.NO_SLOT_TO_PUBLISH:
            break
        attempts += 1
    assert attempts == MAX_PUBLISH_RETRIES, (
        "a permanently failing slot must stop being retried at the bound"
    )


def test_blocking_does_not_consume_retry_budget(db, channel, workspace, tmp_path):
    """A revoked authorization is an operator decision, not a failure to
    give up on — restoring it must let the slot proceed."""
    _seed_account(db, channel.id)
    slot_id, _ = _seed_ready_slot(
        db, channel.id, workspace.id, scheduled_for_utc=_due_time(), tmp_path=tmp_path
    )
    for _ in range(5):
        assert _run(db, channel, workspace).outcome is PublishOutcome.BLOCKED
    assert get_slot(db, slot_id).publish_retry_count == 0

    grant_channel_publishing_authorization(
        db, channel_id=channel.id, workspace_id=workspace.id, actor="operator"
    )
    assert _run(db, channel, workspace).outcome is PublishOutcome.RELEASED


def test_queue_capacity_opens_after_a_successful_publication(db, channel, workspace, tmp_path):
    _authorize(db, channel, workspace, max_publications_per_24h=5)
    slot_id, _ = _seed_ready_slot(
        db, channel.id, workspace.id, scheduled_for_utc=_due_time(), tmp_path=tmp_path
    )
    assert find_slot_ready_to_publish(db, channel.id).id == slot_id

    assert _run(db, channel, workspace).outcome is PublishOutcome.RELEASED
    # The released slot no longer occupies the publishing queue.
    assert find_slot_ready_to_publish(db, channel.id) is None


# ── Analytics handoff and lineage ─────────────────────────────────────────────


def test_successful_release_registers_the_publication_for_observation(
    db, channel, workspace, tmp_path
):
    _authorize(db, channel, workspace)
    _seed_ready_slot(db, channel.id, workspace.id, scheduled_for_utc=_due_time(), tmp_path=tmp_path)
    result = _run(db, channel, workspace)

    assert result.outcome is PublishOutcome.RELEASED
    assert result.observation_schedule_id is not None
    row = db.execute(
        "SELECT operation_type, is_active FROM app_schedule_definitions WHERE id = ?",
        (result.observation_schedule_id,),
    ).fetchone()
    assert row["operation_type"] == "analytics_observation"
    assert row["is_active"] == 1


def test_full_lineage_survives_publication(db, channel, workspace, tmp_path):
    _authorize(db, channel, workspace)
    slot_id, plan_id = _seed_ready_slot(
        db, channel.id, workspace.id, scheduled_for_utc=_due_time(), tmp_path=tmp_path
    )
    result = _run(db, channel, workspace)
    assert result.outcome is PublishOutcome.RELEASED

    slot = get_slot(db, slot_id)
    pub = db.execute("SELECT * FROM publications WHERE id = ?", (slot.publication_id,)).fetchone()
    plan = db.execute("SELECT * FROM publishing_plans WHERE id = ?", (plan_id,)).fetchone()

    assert pub["publishing_plan_id"] == plan_id
    assert pub["channel_id"] == channel.id
    assert pub["workspace_id"] == workspace.id
    assert pub["visibility"] == "public"
    assert pub["status"] == "published"
    assert plan["experiment_id"] == slot.experiment_id
    assert slot.experiment_id is not None


# ── Isolation and the safety property ─────────────────────────────────────────


def test_channel_isolation(db, workspace, tmp_path):
    """Authorizing one channel must not authorize another."""
    ch_a = cp_repo.create_channel(
        db, ChannelDraft(id=_uid(), workspace_id=workspace.id, name="A", slug="a", actor="cli")
    )
    ch_b = cp_repo.create_channel(
        db, ChannelDraft(id=_uid(), workspace_id=workspace.id, name="B", slug="b", actor="cli")
    )
    db.commit()
    _seed_account(db, ch_a.id)
    _seed_account(db, ch_b.id)

    grant_channel_publishing_authorization(
        db, channel_id=ch_a.id, workspace_id=workspace.id, actor="operator"
    )
    assert (
        evaluate_publishing_authorization(db, channel_id=ch_a.id, config=_FakeConfig()).allowed
        is True
    )
    assert (
        evaluate_publishing_authorization(db, channel_id=ch_b.id, config=_FakeConfig()).allowed
        is False
    )

    _seed_ready_slot(db, ch_b.id, workspace.id, scheduled_for_utc=_due_time(), tmp_path=tmp_path)
    provider = _FakeProvider()
    result = run_publishing_cycle(
        db,
        cp_channel_id=ch_a.id,
        workspace_id=workspace.id,
        provider_factory=lambda conn, **kw: provider,
        yt_client_factory=lambda conn, **kw: _FakeYTClient(),
    )
    assert result.outcome is PublishOutcome.NO_SLOT_TO_PUBLISH
    assert provider.upload_calls == []


def test_no_public_side_effect_with_either_authorization_false(
    db, channel, workspace, tmp_path, monkeypatch
):
    """The phase's headline safety guarantee, asserted end to end."""
    import app.intelligence.autonomy.publishing_cycle as pc
    import app.publishing.authorization as auth_mod

    _seed_account(db, channel.id)
    grant_channel_publishing_authorization(
        db, channel_id=channel.id, workspace_id=workspace.id, actor="operator"
    )
    _seed_ready_slot(db, channel.id, workspace.id, scheduled_for_utc=_due_time(), tmp_path=tmp_path)

    for cfg in (
        _FakeConfig(publishing=False, release=True),
        _FakeConfig(publishing=True, release=False),
        _FakeConfig(publishing=False, release=False),
    ):
        real = auth_mod.get_channel_publishing_authorization

        def _eval(conn, *, channel_id, config=None, exclude_publication_id=None, _cfg=cfg):
            from app.publishing.authorization import (
                evaluate_publishing_authorization as _real_eval,
            )

            return _real_eval(
                conn,
                channel_id=channel_id,
                config=_cfg,
                exclude_publication_id=exclude_publication_id,
            )

        monkeypatch.setattr(pc, "evaluate_publishing_authorization", _eval)

        provider = _FakeProvider()
        yt = _FakeYTClient()
        result = run_publishing_cycle(
            db,
            cp_channel_id=channel.id,
            workspace_id=workspace.id,
            provider_factory=lambda conn, _provider=provider, **kw: _provider,
            yt_client_factory=lambda conn, _yt=yt, **kw: _yt,
        )
        assert result.outcome is PublishOutcome.BLOCKED
        assert provider.upload_calls == [], "no upload may occur with a global gate off"
        assert yt.update_calls == [], "no release may occur with a global gate off"

    assert db.execute("SELECT COUNT(*) n FROM publications").fetchone()["n"] == 0
    assert real is not None  # keep the reference meaningful for linters


def test_publishing_cycle_never_calls_the_legacy_upload_orchestrator(db):
    """Phase 18C owns its own upload/release state machine. Reaching into the
    legacy single-shot orchestrator would silently reintroduce the
    upload-and-publish-in-one-breath behaviour this phase exists to replace."""
    import inspect

    import app.intelligence.autonomy.publishing_cycle as pc

    source = inspect.getsource(pc)
    body = source.split('"""', 2)[-1]  # exclude the module docstring
    assert "start_publishing_job" not in body
    assert "retry_publishing_job" not in body


def test_publishing_lease_is_committed_as_completed(db, channel, workspace, tmp_path):
    """A finished cycle must leave no 'pending' lease behind.

    update_operation_status() does not commit on its own; without an explicit
    commit the lease survives as 'pending' and every later cycle for the slot
    returns ALREADY_RUNNING forever — which would permanently block a resume
    after a successful upload. Found on the live system after Phase 18B left
    exactly such a stale production lease.
    """
    _authorize(db, channel, workspace)
    slot_id, _ = _seed_ready_slot(
        db, channel.id, workspace.id, scheduled_for_utc=_due_time(), tmp_path=tmp_path
    )
    result = _run(db, channel, workspace)
    assert result.outcome is PublishOutcome.RELEASED

    leases = db.execute(
        "SELECT status FROM cp_operation_executions WHERE operation_type = ?",
        ("autonomy_publishing_cycle",),
    ).fetchall()
    assert leases, "the cycle should have taken a lease"
    assert all(r["status"] == "completed" for r in leases), (
        f"lease left un-finalized: {[r['status'] for r in leases]}"
    )


# ── Release scope as an explicit blocker ──────────────────────────────────────


def test_missing_release_scope_blocks_autonomous_publishing(db, channel, workspace, monkeypatch):
    """An upload-capable credential is NOT release-capable.

    youtube.upload authorizes videos.insert; changing privacyStatus is a
    videos.update call needing youtube.force-ssl. Without this check the cycle
    would upload to the real channel and then be unable to publish, leaving an
    orphan private video behind — exactly what was caught on the live system
    during the Phase 18C pre-flight.
    """
    import app.publishing.authorization as auth_mod

    _seed_account(db, channel.id)
    grant_channel_publishing_authorization(
        db, channel_id=channel.id, workspace_id=workspace.id, actor="operator"
    )
    monkeypatch.setattr(auth_mod, "_has_release_scope", lambda *a, **k: False)

    decision = auth_mod.evaluate_publishing_authorization(
        db, channel_id=channel.id, config=_FakeConfig()
    )
    assert decision.allowed is False
    assert BlockReason.release_scope_missing in decision.blocked_by
    assert decision.release_scope_granted is False


def test_release_scope_present_clears_that_blocker(db, channel, workspace, monkeypatch):
    import app.publishing.authorization as auth_mod

    _seed_account(db, channel.id)
    grant_channel_publishing_authorization(
        db, channel_id=channel.id, workspace_id=workspace.id, actor="operator"
    )
    monkeypatch.setattr(auth_mod, "_has_release_scope", lambda *a, **k: True)

    decision = auth_mod.evaluate_publishing_authorization(
        db, channel_id=channel.id, config=_FakeConfig()
    )
    assert decision.allowed is True
    assert BlockReason.release_scope_missing not in decision.blocked_by
    assert decision.release_scope_granted is True


def test_missing_release_scope_prevents_any_upload(db, channel, workspace, tmp_path, monkeypatch):
    """The block must stop the cycle BEFORE the provider call, not after."""
    import app.intelligence.autonomy.publishing_cycle as pc
    import app.publishing.authorization as auth_mod

    _seed_account(db, channel.id)
    grant_channel_publishing_authorization(
        db, channel_id=channel.id, workspace_id=workspace.id, actor="operator"
    )
    _seed_ready_slot(db, channel.id, workspace.id, scheduled_for_utc=_due_time(), tmp_path=tmp_path)

    def _eval(conn, *, channel_id, config=None, exclude_publication_id=None):
        return auth_mod.evaluate_publishing_authorization(
            conn,
            channel_id=channel_id,
            config=_FakeConfig(),
            exclude_publication_id=exclude_publication_id,
        )

    monkeypatch.setattr(auth_mod, "_has_release_scope", lambda *a, **k: False)
    monkeypatch.setattr(pc, "evaluate_publishing_authorization", _eval)

    provider = _FakeProvider()
    yt = _FakeYTClient()
    result = run_publishing_cycle(
        db,
        cp_channel_id=channel.id,
        workspace_id=workspace.id,
        provider_factory=lambda conn, **kw: provider,
        yt_client_factory=lambda conn, **kw: yt,
    )

    assert result.outcome is PublishOutcome.BLOCKED
    assert "release_scope_missing" in result.blocked_by
    assert provider.upload_calls == [], "must not upload without release permission"
    assert yt.update_calls == []
    assert db.execute("SELECT COUNT(*) n FROM publications").fetchone()["n"] == 0


def test_release_scope_check_defaults_to_false_when_unavailable(db, channel, workspace):
    """No token store in this environment — absence of evidence is never permission.

    Reaches past the autouse fixture's default to exercise the real helper.
    """
    import app.publishing.authorization as auth_mod

    real = auth_mod._REAL_HAS_RELEASE_SCOPE
    account_id = _seed_account(db, channel.id)
    assert real(db, account_id=account_id, channel_id=channel.id) is False


def test_rescheduling_moves_production_lineage_and_retires_the_old_slot(
    db, channel, workspace, tmp_path
):
    """A rescheduled slot must stop being a publishable candidate.

    Releasing only experiment_id/brief_id (which unique indexes force) left the
    old slot with state='filled', production_status='ready' and the publishing
    plan still attached — so it still satisfied find_slot_ready_to_publish, and
    because candidates are ordered by scheduled_for_utc ASC the *missed* slot
    was selected ahead of its own replacement. Caught during the live Phase 18C
    verification by a scope assertion, before any provider call.
    """
    _authorize(db, channel, workspace)
    old_time = _iso(datetime.now(UTC) - timedelta(days=2))
    old_id, plan_id = _seed_ready_slot(
        db, channel.id, workspace.id, scheduled_for_utc=old_time, tmp_path=tmp_path
    )

    new_time = _future_time(3)
    new_slot = reschedule_slot_to_new_time(
        db,
        old_id,
        new_scheduled_for_utc=new_time,
        new_scheduled_for_local=new_time,
        new_slot_key="verification-slot",
        timezone="UTC",
        actor="operator",
    )

    old = get_slot(db, old_id)
    # Terminal and no longer holding the artifact.
    assert old.publish_status is PublishStatus.skipped_missed
    assert old.production_status is None
    assert old.production_publishing_plan_id is None
    assert old.experiment_id is None
    # Honest history retained.
    assert old.scheduled_for_utc == old_time

    # The replacement owns the lineage outright.
    assert new_slot.production_publishing_plan_id == plan_id
    assert new_slot.production_status is not None

    # And it — not the missed slot — is what publishing selects.
    selected = find_slot_ready_to_publish(db, channel.id)
    assert selected is not None
    assert selected.id == new_slot.id, (
        f"publishing selected slot {selected.id}; the missed slot must never win"
    )

    # Exactly one slot may claim the plan.
    claimants = db.execute(
        "SELECT COUNT(*) n FROM publishing_slots WHERE production_publishing_plan_id = ?",
        (plan_id,),
    ).fetchone()["n"]
    assert claimants == 1
