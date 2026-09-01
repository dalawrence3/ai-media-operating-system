"""Phase 16B.2 — Generic Channel Onboarding Tests.

22 tests A–V covering:
  A  Happy path — all 5 components created
  B  Full idempotency — second call returns existing rows, was_*_created=False
  C  Partial state — channel exists, no profile → profile created on retry
  D  Partial state — channel+profile exist, no scoring policy → policy created
  E  Two channels get independent rows
  F  Channel isolation — profile of A not shared with B
  G  primary_niche stored correctly
  H  secondary_niches JSON round-trip
  I  excluded_topics JSON round-trip
  J  audience_description stored correctly
  K  brand_voice stored with non-default value
  L  content_style stored with non-default value
  M  Scoring policy weights match model defaults
  N  Scoring policy is_default=True and status=active after bootstrap
  O  channel.cp_channel_id matches input
  P  channel.platform_channel_id matches input
  Q  FK violation with foreign_keys=ON raises IntegrityError
  R  Empty primary_niche raises ValidationError
  S  primary_niche exceeding max_length=100 raises ValidationError
  T  operating_mode stored correctly
  U  maturity_stage stored correctly
  V  Third channel bootstrap does not affect first two (extensibility proof)
"""

from __future__ import annotations

import pathlib
import sqlite3
import tempfile

import pytest
from pydantic import ValidationError

from app.core.database import open_db
from app.intelligence.models import (
    BrandVoice,
    ContentStyle,
    MaturityStage,
    OperatingMode,
    PolicyStatus,
)
from app.intelligence.onboarding import ChannelOnboardingResult, bootstrap_channel_intelligence
from app.intelligence.repository import (
    get_active_profile_version,
    get_default_scoring_policy,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

_CP_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_CP_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_CP_C = "cccccccc-cccc-cccc-cccc-cccccccccccc"

_NICHE_A = "science and technology explained"
_NICHE_B = "history and society"
_NICHE_C = "nature and the cosmos"

_YT_A = "UCaaaaaaaaaaaaaaaaaaaaaaA"
_YT_B = "UCbbbbbbbbbbbbbbbbbbbbbbB"


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as d:
        conn = open_db(pathlib.Path(d) / "test.db")
        conn.execute("PRAGMA foreign_keys = OFF")
        yield conn


@pytest.fixture
def db_fk_on():
    """Fixture with foreign keys ON — for FK violation tests."""
    with tempfile.TemporaryDirectory() as d:
        conn = open_db(pathlib.Path(d) / "test.db")
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn


def _seed_cp_channel(conn: sqlite3.Connection, cp_id: str, name: str = "Test Chan") -> None:
    slug = name.lower().replace(" ", "-")
    conn.execute(
        "INSERT OR IGNORE INTO cp_channels"
        " (id, workspace_id, name, slug, actor, created_at, updated_at)"
        " VALUES (?, 'ws-test', ?, ?, 'test', '2024-01-01', '2024-01-01')",
        (cp_id, name, slug),
    )


def _bootstrap_a(conn: sqlite3.Connection, **kwargs: object) -> ChannelOnboardingResult:
    return bootstrap_channel_intelligence(
        conn,
        _CP_A,
        channel_name="Channel Alpha",
        primary_niche=_NICHE_A,
        platform_channel_id=_YT_A,
        **kwargs,  # type: ignore[arg-type]
    )


def _bootstrap_b(conn: sqlite3.Connection, **kwargs: object) -> ChannelOnboardingResult:
    return bootstrap_channel_intelligence(
        conn,
        _CP_B,
        channel_name="Channel Beta",
        primary_niche=_NICHE_B,
        platform_channel_id=_YT_B,
        **kwargs,  # type: ignore[arg-type]
    )


# ── A: Happy path ─────────────────────────────────────────────────────────────


def test_a_happy_path_creates_all_five_components(db: sqlite3.Connection) -> None:
    r = _bootstrap_a(db)

    assert r.channel.id is not None
    assert r.profile.id is not None
    assert r.strategy.id is not None
    assert r.capacity.id is not None
    assert r.scoring_policy.id is not None

    assert r.was_channel_created is True
    assert r.was_profile_created is True
    assert r.was_scoring_policy_created is True


# ── B: Full idempotency ───────────────────────────────────────────────────────


def test_b_idempotency_second_call_returns_existing_rows(db: sqlite3.Connection) -> None:
    r1 = _bootstrap_a(db)
    r2 = _bootstrap_a(db)

    assert r2.channel.id == r1.channel.id
    assert r2.profile.id == r1.profile.id
    assert r2.strategy.id == r1.strategy.id
    assert r2.capacity.id == r1.capacity.id
    assert r2.scoring_policy.id == r1.scoring_policy.id

    assert r2.was_channel_created is False
    assert r2.was_profile_created is False
    assert r2.was_scoring_policy_created is False


# ── C: Partial state — channel exists, no profile ────────────────────────────


def test_c_partial_state_channel_exists_no_profile_creates_profile(
    db: sqlite3.Connection,
) -> None:
    from app.intelligence.channel_bridge import bootstrap_intelligence_channel as _bridge

    bare_ch = _bridge(
        db,
        _CP_A,
        channel_name="Channel Alpha",
        platform_channel_id=_YT_A,
    )
    db.commit()
    assert bare_ch.id is not None
    assert get_active_profile_version(db, bare_ch.id) is None

    r = _bootstrap_a(db)

    assert r.was_channel_created is False
    assert r.was_profile_created is True
    assert r.profile.id is not None
    assert r.channel.id == bare_ch.id


# ── D: Partial state — profile exists, no scoring policy ─────────────────────


def test_d_partial_state_profile_exists_no_scoring_policy_creates_policy(
    db: sqlite3.Connection,
) -> None:
    # Full bootstrap to get channel+profile
    r1 = _bootstrap_a(db)

    # Manually delete the scoring policy to simulate partial state
    db.execute("DELETE FROM scoring_policies WHERE channel_id = ?", (r1.channel.id,))
    db.commit()
    assert get_default_scoring_policy(db, r1.channel.id) is None

    # Second bootstrap should recover scoring policy
    r2 = _bootstrap_a(db)

    assert r2.was_channel_created is False
    assert r2.was_profile_created is False
    assert r2.was_scoring_policy_created is True
    assert r2.scoring_policy.id is not None


# ── E: Two channels get independent rows ─────────────────────────────────────


def test_e_two_channels_get_independent_rows(db: sqlite3.Connection) -> None:
    r_a = _bootstrap_a(db)
    r_b = _bootstrap_b(db)

    assert r_a.channel.id != r_b.channel.id
    assert r_a.profile.id != r_b.profile.id
    assert r_a.strategy.id != r_b.strategy.id
    assert r_a.scoring_policy.id != r_b.scoring_policy.id


# ── F: Profile isolation between channels ────────────────────────────────────


def test_f_channel_isolation_profile_not_shared(db: sqlite3.Connection) -> None:
    r_a = _bootstrap_a(db)
    r_b = _bootstrap_b(db)

    assert r_a.profile.channel_id != r_b.profile.channel_id
    assert r_a.profile.primary_niche == _NICHE_A
    assert r_b.profile.primary_niche == _NICHE_B
    assert r_a.profile.primary_niche != r_b.profile.primary_niche


# ── G: primary_niche stored correctly ────────────────────────────────────────


def test_g_primary_niche_stored_correctly(db: sqlite3.Connection) -> None:
    r = _bootstrap_a(db)
    assert r.profile.primary_niche == _NICHE_A


# ── H: secondary_niches JSON round-trip ──────────────────────────────────────


def test_h_secondary_niches_json_round_trip(db: sqlite3.Connection) -> None:
    secondaries = ["history and society", "nature and the cosmos"]
    r = bootstrap_channel_intelligence(
        db,
        _CP_A,
        channel_name="Channel Alpha",
        primary_niche=_NICHE_A,
        secondary_niches=secondaries,
    )
    assert r.profile.secondary_niches == secondaries


# ── I: excluded_topics JSON round-trip ───────────────────────────────────────


def test_i_excluded_topics_json_round_trip(db: sqlite3.Connection) -> None:
    excluded = ["gambling", "adult content", "graphic violence"]
    r = bootstrap_channel_intelligence(
        db,
        _CP_A,
        channel_name="Channel Alpha",
        primary_niche=_NICHE_A,
        excluded_topics=excluded,
    )
    assert r.profile.excluded_topics == excluded


# ── J: audience_description stored correctly ─────────────────────────────────


def test_j_audience_description_stored_correctly(db: sqlite3.Connection) -> None:
    desc = "Curious adults and young learners seeking accessible explanations."
    r = bootstrap_channel_intelligence(
        db,
        _CP_A,
        channel_name="Channel Alpha",
        primary_niche=_NICHE_A,
        audience_description=desc,
    )
    assert r.profile.audience_description == desc


# ── K: brand_voice stored with non-default value ─────────────────────────────


def test_k_brand_voice_non_default_stored_correctly(db: sqlite3.Connection) -> None:
    r = bootstrap_channel_intelligence(
        db,
        _CP_A,
        channel_name="Channel Alpha",
        primary_niche=_NICHE_A,
        brand_voice=BrandVoice.authoritative,
    )
    assert r.profile.brand_voice == BrandVoice.authoritative


# ── L: content_style stored with non-default value ───────────────────────────


def test_l_content_style_non_default_stored_correctly(db: sqlite3.Connection) -> None:
    r = bootstrap_channel_intelligence(
        db,
        _CP_A,
        channel_name="Channel Alpha",
        primary_niche=_NICHE_A,
        content_style=ContentStyle.story_driven,
    )
    assert r.profile.content_style == ContentStyle.story_driven


# ── M: Scoring policy weights match model defaults ───────────────────────────


def test_m_scoring_policy_weights_match_model_defaults(db: sqlite3.Connection) -> None:
    r = _bootstrap_a(db)
    sp = r.scoring_policy

    assert sp.weight_trend_strength == pytest.approx(0.05)
    assert sp.weight_audience_demand == pytest.approx(0.20)
    assert sp.weight_competition == pytest.approx(0.15)
    assert sp.weight_evergreen_value == pytest.approx(0.20)
    assert sp.weight_audience_fit == pytest.approx(0.30)
    assert sp.weight_content_novelty == pytest.approx(0.10)


# ── N: Scoring policy is_default=True and status=active ──────────────────────


def test_n_scoring_policy_is_default_and_active(db: sqlite3.Connection) -> None:
    r = _bootstrap_a(db)
    assert r.scoring_policy.is_default is True
    assert r.scoring_policy.status == PolicyStatus.active


# ── O: cp_channel_id matches input ───────────────────────────────────────────


def test_o_channel_cp_channel_id_matches_input(db: sqlite3.Connection) -> None:
    r = _bootstrap_a(db)
    assert r.channel.cp_channel_id == _CP_A


# ── P: platform_channel_id matches input ─────────────────────────────────────


def test_p_channel_platform_channel_id_matches_input(db: sqlite3.Connection) -> None:
    r = _bootstrap_a(db)
    assert r.channel.platform_channel_id == _YT_A


# ── Q: FK violation with foreign_keys=ON raises ───────────────────────────────


def test_q_missing_cp_channel_fk_raises_with_fk_enabled(
    db_fk_on: sqlite3.Connection,
) -> None:
    bogus_cp_id = "00000000-0000-0000-0000-000000000000"
    with pytest.raises(sqlite3.IntegrityError):
        bootstrap_channel_intelligence(
            db_fk_on,
            bogus_cp_id,
            channel_name="Ghost Channel",
            primary_niche=_NICHE_A,
        )


# ── R: Empty primary_niche raises ValidationError ────────────────────────────


def test_r_empty_primary_niche_raises_validation_error(db: sqlite3.Connection) -> None:
    with pytest.raises((ValidationError, ValueError)):
        bootstrap_channel_intelligence(
            db,
            _CP_A,
            channel_name="Channel Alpha",
            primary_niche="",
        )


# ── S: primary_niche too long raises ValidationError ─────────────────────────


def test_s_primary_niche_too_long_raises_validation_error(db: sqlite3.Connection) -> None:
    too_long = "x" * 101
    with pytest.raises((ValidationError, ValueError)):
        bootstrap_channel_intelligence(
            db,
            _CP_A,
            channel_name="Channel Alpha",
            primary_niche=too_long,
        )


# ── T: operating_mode stored correctly ───────────────────────────────────────


def test_t_operating_mode_stored_correctly(db: sqlite3.Connection) -> None:
    r = bootstrap_channel_intelligence(
        db,
        _CP_A,
        channel_name="Channel Alpha",
        primary_niche=_NICHE_A,
        operating_mode=OperatingMode.manual,
    )
    assert r.channel.operating_mode == OperatingMode.manual


# ── U: maturity_stage stored correctly ───────────────────────────────────────


def test_u_maturity_stage_stored_correctly(db: sqlite3.Connection) -> None:
    r = _bootstrap_a(db)
    assert r.channel.current_maturity_stage == MaturityStage.validation
    assert r.profile.maturity_stage == MaturityStage.validation


# ── V: Third channel bootstrap does not affect first two ─────────────────────


def test_v_third_channel_does_not_affect_first_two(db: sqlite3.Connection) -> None:
    r_a = _bootstrap_a(db)
    r_b = _bootstrap_b(db)

    r_c = bootstrap_channel_intelligence(
        db,
        _CP_C,
        channel_name="Channel Gamma",
        primary_niche=_NICHE_C,
    )

    # A and B are unchanged
    from app.intelligence.repository import get_active_profile_version as _gpv
    from app.intelligence.repository import get_channel

    ch_a_now = get_channel(db, r_a.channel.id)
    ch_b_now = get_channel(db, r_b.channel.id)

    assert ch_a_now.current_profile_version_id == r_a.profile.id
    assert ch_b_now.current_profile_version_id == r_b.profile.id

    profile_a_now = _gpv(db, r_a.channel.id)
    assert profile_a_now.primary_niche == _NICHE_A

    profile_b_now = _gpv(db, r_b.channel.id)
    assert profile_b_now.primary_niche == _NICHE_B

    # C is independent
    assert r_c.channel.id not in (r_a.channel.id, r_b.channel.id)
    assert r_c.profile.primary_niche == _NICHE_C
