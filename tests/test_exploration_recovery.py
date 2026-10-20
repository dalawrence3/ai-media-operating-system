"""Phase 7A, §9/§18 — bounded no-eligible-candidate recovery."""

from __future__ import annotations

from datetime import UTC, datetime

from app.intelligence.exploration.cell import ExplorationCell
from app.intelligence.exploration.format_contract import known_format_specs
from app.intelligence.exploration.recovery import (
    DiscoveryOutcome,
    _default_candidate_pool,
    attempt_recovery_discovery,
    last_recovery_attempt,
    should_attempt_recovery,
)
from app.intelligence.exploration.repository import list_recent_cells
from app.intelligence.exploration.taxonomy import FORMAT_SEEDS


def _seed_channel(db, channel_id: int = 1) -> None:
    db.execute("INSERT INTO channels (id, channel_name) VALUES (?, ?)", (channel_id, "channel-1"))
    db.commit()


class TestDefaultCandidatePoolIsProductionCapableOnly:
    """Phase 7C, §1 — the one candidate pool that actually feeds real,
    production-triggering recovery discovery must never offer a format
    with no production contract; selecting one would trigger discovery
    for content script generation cannot actually execute."""

    def test_every_candidate_format_has_a_production_contract(self):
        pool = _default_candidate_pool()
        assert pool  # sanity: not accidentally empty
        executable = set(known_format_specs())
        for cell in pool:
            assert cell.format in executable

    def test_unsupported_seed_formats_are_excluded(self):
        pool = _default_candidate_pool()
        pool_formats = {cell.format for cell in pool}
        unsupported = set(FORMAT_SEEDS) - set(known_format_specs())
        assert unsupported, "expected at least one seed format with no production contract"
        assert pool_formats.isdisjoint(unsupported)

    def test_format_seeds_taxonomy_itself_is_unchanged(self):
        """Unsupported formats are excluded from the PRODUCTION-facing pool,
        never deleted from the taxonomy — they remain valid for pure
        exploration/simulation elsewhere (simulation.py's own pool)."""
        executable = set(known_format_specs())
        assert set(FORMAT_SEEDS) - executable, "taxonomy must still list unsupported seeds"


class TestShouldAttemptRecoveryStaleness:
    def test_below_streak_floor_does_not_attempt(self, db):
        should, reason = should_attempt_recovery(
            db, 1, consecutive_no_eligible_streak=1, last_recovery_attempt_at=None
        )
        assert should is False
        assert "consecutive" in reason

    def test_at_or_above_streak_floor_with_no_prior_attempt_proceeds(self, db):
        should, reason = should_attempt_recovery(
            db, 1, consecutive_no_eligible_streak=3, last_recovery_attempt_at=None
        )
        assert should is True

    def test_within_cooldown_does_not_attempt(self, db):
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        recent_attempt = "2026-01-01T10:00:00"  # 2h ago, cooldown default 6h
        should, reason = should_attempt_recovery(
            db,
            1,
            consecutive_no_eligible_streak=5,
            last_recovery_attempt_at=recent_attempt,
            now=now,
        )
        assert should is False
        assert "cooldown" in reason

    def test_past_cooldown_proceeds(self, db):
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        old_attempt = "2026-01-01T02:00:00"  # 10h ago
        should, _ = should_attempt_recovery(
            db, 1, consecutive_no_eligible_streak=5, last_recovery_attempt_at=old_attempt, now=now
        )
        assert should is True


class TestNoInfiniteRetry:
    def test_second_call_within_cooldown_does_not_re_attempt(self, db):
        _seed_channel(db)

        def fake_discover(conn, *, channel_id, cell):
            return DiscoveryOutcome(attempted=True, probes_created=2)

        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        first = attempt_recovery_discovery(
            db,
            1,
            workspace_id="ws",
            consecutive_no_eligible_streak=3,
            discover_fn=fake_discover,
            now=now,
        )
        db.commit()
        assert first.recovery_attempted is True

        second = attempt_recovery_discovery(
            db,
            1,
            workspace_id="ws",
            consecutive_no_eligible_streak=3,
            discover_fn=fake_discover,
            now=now,  # same instant — well within cooldown
        )
        assert second.recovery_attempted is False

    def test_exactly_one_discovery_call_per_attempt(self, db):
        """The orchestrator must never loop internally — discover_fn is
        called at most once per attempt_recovery_discovery invocation."""
        _seed_channel(db)
        calls = []

        def counting_discover(conn, *, channel_id, cell):
            calls.append(cell)
            return DiscoveryOutcome(attempted=True, probes_created=1)

        attempt_recovery_discovery(
            db,
            1,
            workspace_id="ws",
            consecutive_no_eligible_streak=5,
            discover_fn=counting_discover,
        )
        assert len(calls) == 1


class TestFailedDiscoveryRemainsSafelyUnfilled:
    def test_discovery_error_is_reported_not_swallowed(self, db):
        _seed_channel(db)

        def failing_discover(conn, *, channel_id, cell):
            return DiscoveryOutcome(attempted=True, error="provider unavailable")

        result = attempt_recovery_discovery(
            db,
            1,
            workspace_id="ws",
            consecutive_no_eligible_streak=5,
            discover_fn=failing_discover,
        )
        assert result.recovery_attempted is True
        assert "failed" in result.reason.lower()

    def test_discovery_never_attempted_is_reported(self, db):
        _seed_channel(db)

        def unavailable_discover(conn, *, channel_id, cell):
            return DiscoveryOutcome(attempted=False, error="no active channel profile")

        result = attempt_recovery_discovery(
            db,
            1,
            workspace_id="ws",
            consecutive_no_eligible_streak=5,
            discover_fn=unavailable_discover,
        )
        assert result.recovery_attempted is True
        assert result.outcome.attempted is False

    def test_attempt_is_still_recorded_even_on_failure(self, db):
        """A failed attempt still updates the cooldown ledger — a stale
        pool must not be hammered on every single hourly tick just because
        discovery kept failing."""
        _seed_channel(db)

        def failing_discover(conn, *, channel_id, cell):
            return DiscoveryOutcome(attempted=True, error="boom")

        attempt_recovery_discovery(
            db,
            1,
            workspace_id="ws",
            consecutive_no_eligible_streak=5,
            discover_fn=failing_discover,
        )
        db.commit()
        assert last_recovery_attempt(db, 1) is not None


class TestRecoveryPicksUnderExploredCell:
    def test_never_picks_a_recently_saturated_domain(self, db):
        _seed_channel(db)
        from app.intelligence.exploration.repository import record_cell_selection

        # Saturate 'science' heavily so it should not win exploration ranking.
        for i in range(5):
            record_cell_selection(
                db,
                channel_id=1,
                workspace_id="ws",
                cell=ExplorationCell(
                    domain="science", format="curiosity_explainer", audience_value="curiosity"
                ),
                selected_at=f"2026-01-0{i + 1}T00:00:00",
            )
        db.commit()

        captured = {}

        def capture_discover(conn, *, channel_id, cell):
            captured["cell"] = cell
            return DiscoveryOutcome(attempted=True, probes_created=1)

        attempt_recovery_discovery(
            db,
            1,
            workspace_id="ws",
            consecutive_no_eligible_streak=5,
            discover_fn=capture_discover,
        )
        assert captured["cell"].domain != "science"


class TestRecoveryRecordsToLedger:
    def test_successful_attempt_appears_in_recent_cells(self, db):
        _seed_channel(db)

        def fake_discover(conn, *, channel_id, cell):
            return DiscoveryOutcome(attempted=True, probes_created=1)

        attempt_recovery_discovery(
            db,
            1,
            workspace_id="ws",
            consecutive_no_eligible_streak=5,
            discover_fn=fake_discover,
        )
        db.commit()
        recent = list_recent_cells(db, 1)
        assert len(recent) == 1
