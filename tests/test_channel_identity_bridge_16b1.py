"""Phase 16B.1 — Multi-channel identity bridge tests (Step 20).

Validates Option-A architecture: channels.cp_channel_id TEXT UNIQUE → cp_channels.id.

Scenarios A–AC covering:
  - Schema migration idempotency (A–C)
  - Bridge lookups (D–H)
  - Bootstrap (idempotency, FK guard, UNIQUE guard) (I–N)
  - Multi-channel isolation (O–T)
  - Round-trip consistency (U–W)
  - Fail-loud contract (X–Z)
  - Backward compatibility (AA–AC)
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from app.core.database import open_db
from app.intelligence.channel_bridge import (
    ChannelNotBootstrappedError,
    bootstrap_intelligence_channel,
    get_cp_channel_id_for_intelligence_channel,
    get_intelligence_channel_id,
    require_intelligence_channel_id,
)
from app.intelligence.models import Channel, MaturityStage, OperatingMode, Platform
from app.intelligence.repository import (
    create_channel,
    get_channel,
    list_channels,
)


def _uid() -> str:
    return str(uuid.uuid4())


_NOW = "2025-01-01T00:00:00"


@pytest.fixture()
def db(tmp_path: Path):
    path = tmp_path / "bridge_test.db"
    conn = open_db(path)
    yield conn
    conn.close()


def _seed_cp_channel(conn: sqlite3.Connection, ws_id: str = "ws-test") -> str:
    """Seed minimal cp_workspaces + cp_channels rows; return cp_channel UUID."""
    cp_ch_id = _uid()
    conn.execute(
        "INSERT OR IGNORE INTO cp_workspaces "
        "(id, name, slug, status, actor, created_at, updated_at) "
        "VALUES (?, 'Test WS', 'test-ws', 'active', 'test', ?, ?)",
        (ws_id, _NOW, _NOW),
    )
    conn.execute(
        "INSERT INTO cp_channels "
        "(id, workspace_id, name, slug, status, actor, created_at, updated_at) "
        "VALUES (?, ?, 'Test Channel', 'test-ch', 'active', 'test', ?, ?)",
        (cp_ch_id, ws_id, _NOW, _NOW),
    )
    conn.commit()
    return cp_ch_id


# ---------------------------------------------------------------------------
# A–C: Schema migration idempotency
# ---------------------------------------------------------------------------


def test_A_schema_version_is_42(db):
    """Schema must be at version 42 after open_db."""
    version = db.execute(
        "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
    ).fetchone()[0]
    assert version == 51


def test_B_cp_channel_id_column_exists_on_channels(db):
    """channels.cp_channel_id column must exist after migration."""
    cols = {r[1] for r in db.execute("PRAGMA table_info(channels)").fetchall()}
    assert "cp_channel_id" in cols


def test_C_migration_is_idempotent(tmp_path):
    """Opening the same DB twice must not raise or corrupt the version."""
    path = tmp_path / "idem.db"
    c1 = open_db(path)
    c1.close()
    c2 = open_db(path)
    version = c2.execute(
        "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
    ).fetchone()[0]
    c2.close()
    assert version == 51


# ---------------------------------------------------------------------------
# D–H: Bridge lookups
# ---------------------------------------------------------------------------


def test_D_get_intelligence_channel_id_returns_none_when_missing(db):
    """Unmapped cp_channel_id must return None."""
    result = get_intelligence_channel_id(db, _uid())
    assert result is None


def test_E_get_intelligence_channel_id_returns_int_when_mapped(db):
    """Mapped cp_channel_id must return the integer channel id."""
    cp_id = _seed_cp_channel(db)
    ch = bootstrap_intelligence_channel(db, cp_id, channel_name="Alpha")
    db.commit()
    result = get_intelligence_channel_id(db, cp_id)
    assert result == ch.id
    assert isinstance(result, int)


def test_F_get_cp_channel_id_for_intelligence_channel_returns_none_when_missing(db):
    """An intelligence channel without cp_channel_id must return None."""
    ch = create_channel(
        db,
        Channel(
            channel_name="Legacy",
            platform=Platform.youtube,
            operating_mode=OperatingMode.manual,
            current_maturity_stage=MaturityStage.validation,
        ),
    )
    db.commit()
    result = get_cp_channel_id_for_intelligence_channel(db, ch.id)  # type: ignore[arg-type]
    assert result is None


def test_G_get_cp_channel_id_for_intelligence_channel_returns_uuid_when_mapped(db):
    """Bootstrapped channel must return the cp_channel_id UUID."""
    cp_id = _seed_cp_channel(db)
    ch = bootstrap_intelligence_channel(db, cp_id, channel_name="Bravo")
    db.commit()
    result = get_cp_channel_id_for_intelligence_channel(db, ch.id)  # type: ignore[arg-type]
    assert result == cp_id


def test_H_lookup_is_exact_no_prefix_match(db):
    """A truncated cp_channel_id must not match the full UUID."""
    cp_id = _seed_cp_channel(db)
    bootstrap_intelligence_channel(db, cp_id, channel_name="Charlie")
    db.commit()
    result = get_intelligence_channel_id(db, cp_id[:8])  # prefix only
    assert result is None


# ---------------------------------------------------------------------------
# I–N: Bootstrap
# ---------------------------------------------------------------------------


def test_I_bootstrap_creates_channel_row(db):
    """bootstrap_intelligence_channel must insert a channels row."""
    before = len(list_channels(db))
    cp_id = _seed_cp_channel(db)
    bootstrap_intelligence_channel(db, cp_id, channel_name="Delta")
    db.commit()
    after = len(list_channels(db))
    assert after == before + 1


def test_J_bootstrap_is_idempotent(db):
    """Calling bootstrap twice must return the same channel without duplicating rows."""
    cp_id = _seed_cp_channel(db)
    ch1 = bootstrap_intelligence_channel(db, cp_id, channel_name="Echo")
    db.commit()
    ch2 = bootstrap_intelligence_channel(db, cp_id, channel_name="Echo-2")
    db.commit()
    assert ch1.id == ch2.id
    assert len(list_channels(db)) == 1


def test_K_bootstrap_sets_cp_channel_id(db):
    """Bootstrapped channel must have cp_channel_id stored in DB."""
    cp_id = _seed_cp_channel(db)
    ch = bootstrap_intelligence_channel(db, cp_id, channel_name="Foxtrot")
    db.commit()
    row = db.execute("SELECT cp_channel_id FROM channels WHERE id = ?", (ch.id,)).fetchone()
    assert row["cp_channel_id"] == cp_id


def test_L_bootstrap_stores_platform_channel_id(db):
    """Optional platform_channel_id is persisted alongside cp_channel_id."""
    cp_id = _seed_cp_channel(db)
    yt_id = "UCeJ6ZQ_rITBSWKNAhK8vwTA"
    ch = bootstrap_intelligence_channel(db, cp_id, channel_name="Golf", platform_channel_id=yt_id)
    db.commit()
    fetched = get_channel(db, ch.id)  # type: ignore[arg-type]
    assert fetched is not None
    assert fetched.platform_channel_id == yt_id
    assert fetched.cp_channel_id == cp_id


def test_M_bootstrap_fk_guard_rejects_nonexistent_cp_channel(db):
    """Bootstrap with a cp_channel_id that does not exist in cp_channels must fail."""
    fake_cp_id = _uid()
    with pytest.raises(sqlite3.IntegrityError):
        bootstrap_intelligence_channel(db, fake_cp_id, channel_name="Hotel")
        db.commit()


def test_N_bootstrap_unique_guard_rejects_duplicate_cp_channel_id(db):
    """Two channels with the same cp_channel_id must violate the UNIQUE index."""
    cp_id = _seed_cp_channel(db)
    # Insert first channel directly
    db.execute(
        "INSERT INTO channels (platform, channel_name, cp_channel_id, operating_mode, "
        "current_maturity_stage, created_at, updated_at) "
        "VALUES ('youtube', 'India', ?, 'manual', 'validation', ?, ?)",
        (cp_id, _NOW, _NOW),
    )
    db.commit()
    # Attempt to insert a second channel with the same cp_channel_id
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO channels (platform, channel_name, cp_channel_id, operating_mode, "
            "current_maturity_stage, created_at, updated_at) "
            "VALUES ('youtube', 'Juliet', ?, 'manual', 'validation', ?, ?)",
            (cp_id, _NOW, _NOW),
        )
        db.commit()


# ---------------------------------------------------------------------------
# O–T: Multi-channel isolation
# ---------------------------------------------------------------------------


def _seed_two_channels(db) -> tuple[str, str, int, int]:
    """Seed two independent cp_channels and bootstrap both. Returns (cp1, cp2, int1, int2)."""
    cp1 = _seed_cp_channel(db, ws_id="ws-multi-1")
    cp2_id = _uid()
    db.execute(
        "INSERT INTO cp_channels "
        "(id, workspace_id, name, slug, status, actor, created_at, updated_at) "
        "VALUES (?, 'ws-multi-1', 'Channel Two', 'ch-two', 'active', 'test', ?, ?)",
        (cp2_id, _NOW, _NOW),
    )
    db.commit()

    ch1 = bootstrap_intelligence_channel(db, cp1, channel_name="Kilo")
    ch2 = bootstrap_intelligence_channel(db, cp2_id, channel_name="Lima")
    db.commit()
    return cp1, cp2_id, ch1.id, ch2.id  # type: ignore[return-value]


def test_O_two_channels_have_distinct_integer_ids(db):
    """Two bootstrapped channels must have different integer IDs."""
    _, _, id1, id2 = _seed_two_channels(db)
    assert id1 != id2


def test_P_lookup_cp1_returns_int1_not_int2(db):
    """Looking up cp_channel_1 must return int_id_1, not int_id_2."""
    cp1, cp2, id1, id2 = _seed_two_channels(db)
    assert get_intelligence_channel_id(db, cp1) == id1
    assert get_intelligence_channel_id(db, cp2) == id2


def test_Q_reverse_lookup_is_isolated(db):
    """Reverse lookup for int_id_1 must return cp1, not cp2."""
    cp1, cp2, id1, id2 = _seed_two_channels(db)
    assert get_cp_channel_id_for_intelligence_channel(db, id1) == cp1
    assert get_cp_channel_id_for_intelligence_channel(db, id2) == cp2


def test_R_opportunity_unique_index_allows_same_cluster_per_channel(db):
    """The opportunities UNIQUE index must allow the same canonical cluster for two channels."""
    _, _, id1, id2 = _seed_two_channels(db)

    db.execute("PRAGMA foreign_keys = OFF")
    cur = db.execute(
        "INSERT INTO market_canonical_clusters "
        "(canonical_label, normalized_label, semantic_fingerprint, created_at, updated_at) "
        "VALUES ('Cluster A', 'cluster_a', 'fp-a', ?, ?)",
        (_NOW, _NOW),
    )
    cluster_id = cur.lastrowid
    db.execute(
        "INSERT INTO opportunities "
        "(channel_id, canonical_cluster_id, discovery_run_id, normalized_topic, raw_topic, "
        "created_at, updated_at) "
        "VALUES (?, ?, 0, 'Cluster A', 'Cluster A', ?, ?)",
        (id1, cluster_id, _NOW, _NOW),
    )
    db.execute(
        "INSERT INTO opportunities "
        "(channel_id, canonical_cluster_id, discovery_run_id, normalized_topic, raw_topic, "
        "created_at, updated_at) "
        "VALUES (?, ?, 0, 'Cluster A', 'Cluster A', ?, ?)",
        (id2, cluster_id, _NOW, _NOW),
    )
    db.commit()

    count = db.execute(
        "SELECT COUNT(*) FROM opportunities WHERE canonical_cluster_id = ?", (cluster_id,)
    ).fetchone()[0]
    assert count == 2


def test_S_opportunity_unique_index_rejects_duplicate_cluster_same_channel(db):
    """The UNIQUE index must reject a duplicate (channel_id, canonical_cluster_id) pair."""
    _, _, id1, _ = _seed_two_channels(db)
    db.execute("PRAGMA foreign_keys = OFF")
    cur = db.execute(
        "INSERT INTO market_canonical_clusters "
        "(canonical_label, normalized_label, semantic_fingerprint, created_at, updated_at) "
        "VALUES ('Dup Cluster', 'dup_cluster', 'fp-dup', ?, ?)",
        (_NOW, _NOW),
    )
    cluster_id = cur.lastrowid
    db.execute(
        "INSERT INTO opportunities "
        "(channel_id, canonical_cluster_id, discovery_run_id, normalized_topic, raw_topic, "
        "created_at, updated_at) "
        "VALUES (?, ?, 0, 'Dup Cluster', 'Dup Cluster', ?, ?)",
        (id1, cluster_id, _NOW, _NOW),
    )
    db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO opportunities "
            "(channel_id, canonical_cluster_id, discovery_run_id, normalized_topic, raw_topic, "
            "created_at, updated_at) "
            "VALUES (?, ?, 0, 'Dup Cluster', 'Dup Cluster', ?, ?)",
            (id1, cluster_id, _NOW, _NOW),
        )
        db.commit()


def test_T_list_channels_returns_both(db):
    """list_channels must include all bootstrapped channels."""
    _seed_two_channels(db)
    channels = list_channels(db)
    assert len(channels) == 2
    cp_ids = {ch.cp_channel_id for ch in channels}
    assert None not in cp_ids  # both have cp_channel_id set


# ---------------------------------------------------------------------------
# U–W: Round-trip consistency
# ---------------------------------------------------------------------------


def test_U_create_channel_persists_cp_channel_id(db):
    """create_channel with cp_channel_id set must persist it."""
    cp_id = _seed_cp_channel(db)
    ch = create_channel(
        db,
        Channel(
            channel_name="Mike",
            platform=Platform.youtube,
            cp_channel_id=cp_id,
            operating_mode=OperatingMode.manual,
            current_maturity_stage=MaturityStage.validation,
        ),
    )
    db.commit()
    fetched = get_channel(db, ch.id)  # type: ignore[arg-type]
    assert fetched is not None
    assert fetched.cp_channel_id == cp_id


def test_V_get_channel_returns_cp_channel_id(db):
    """get_channel must populate the cp_channel_id field."""
    cp_id = _seed_cp_channel(db)
    ch = bootstrap_intelligence_channel(db, cp_id, channel_name="November")
    db.commit()
    fetched = get_channel(db, ch.id)  # type: ignore[arg-type]
    assert fetched is not None
    assert fetched.cp_channel_id == cp_id


def test_W_channel_without_cp_id_has_none(db):
    """A legacy channel inserted without cp_channel_id must have None in the model."""
    ch = create_channel(
        db,
        Channel(
            channel_name="Oscar",
            platform=Platform.youtube,
            operating_mode=OperatingMode.manual,
            current_maturity_stage=MaturityStage.validation,
        ),
    )
    db.commit()
    fetched = get_channel(db, ch.id)  # type: ignore[arg-type]
    assert fetched is not None
    assert fetched.cp_channel_id is None


# ---------------------------------------------------------------------------
# X–Z: Fail-loud contract
# ---------------------------------------------------------------------------


def test_X_require_intelligence_channel_id_raises_when_missing(db):
    """require_intelligence_channel_id must raise ChannelNotBootstrappedError if not mapped."""
    with pytest.raises(ChannelNotBootstrappedError) as exc_info:
        require_intelligence_channel_id(db, _uid())
    assert "cp_channel_id" in str(exc_info.value)


def test_Y_channel_not_bootstrapped_error_carries_cp_channel_id(db):
    """ChannelNotBootstrappedError must expose cp_channel_id attribute."""
    cp_id = _uid()
    err = ChannelNotBootstrappedError(cp_id)
    assert err.cp_channel_id == cp_id
    assert cp_id in str(err)


def test_Z_require_returns_int_when_mapped(db):
    """require_intelligence_channel_id must return the integer id when mapped."""
    cp_id = _seed_cp_channel(db)
    ch = bootstrap_intelligence_channel(db, cp_id, channel_name="Papa")
    db.commit()
    result = require_intelligence_channel_id(db, cp_id)
    assert result == ch.id
    assert isinstance(result, int)


# ---------------------------------------------------------------------------
# AA–AC: Backward compatibility (channels without cp_channel_id)
# ---------------------------------------------------------------------------


def test_AA_legacy_channel_does_not_violate_unique_index(db):
    """Multiple channels without cp_channel_id must coexist (NULL is not UNIQUE)."""
    for name in ("Quebec", "Romeo", "Sierra"):
        create_channel(
            db,
            Channel(
                channel_name=name,
                platform=Platform.youtube,
                operating_mode=OperatingMode.manual,
                current_maturity_stage=MaturityStage.validation,
            ),
        )
    db.commit()
    count = db.execute("SELECT COUNT(*) FROM channels WHERE cp_channel_id IS NULL").fetchone()[0]
    assert count == 3


def test_AB_mixed_legacy_and_bridged_channels_coexist(db):
    """Legacy channels (no cp_channel_id) and bridged channels must coexist."""
    cp_id = _seed_cp_channel(db)
    bootstrap_intelligence_channel(db, cp_id, channel_name="Tango")
    create_channel(
        db,
        Channel(
            channel_name="Uniform",
            platform=Platform.youtube,
            operating_mode=OperatingMode.manual,
            current_maturity_stage=MaturityStage.validation,
        ),
    )
    db.commit()
    all_channels = list_channels(db)
    assert len(all_channels) == 2
    bridged = [ch for ch in all_channels if ch.cp_channel_id is not None]
    legacy = [ch for ch in all_channels if ch.cp_channel_id is None]
    assert len(bridged) == 1
    assert len(legacy) == 1


def test_AC_channel_from_model_without_cp_id_round_trips(db):
    """Channel model with cp_channel_id=None must serialize and round-trip without error."""
    ch = Channel(
        channel_name="Victor",
        platform=Platform.youtube,
        operating_mode=OperatingMode.manual,
        current_maturity_stage=MaturityStage.validation,
    )
    assert ch.cp_channel_id is None
    created = create_channel(db, ch)
    db.commit()
    fetched = get_channel(db, created.id)  # type: ignore[arg-type]
    assert fetched is not None
    assert fetched.cp_channel_id is None
    assert fetched.channel_name == "Victor"
