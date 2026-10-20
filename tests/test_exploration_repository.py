"""Phase 7A, §19/§18 — exploration_cell_selections persistence.

Uses the real `db` fixture (full migrated schema) so this exercises the
actual v57 migration, not a hand-rolled stub table.
"""

from __future__ import annotations

import pytest

from app.intelligence.exploration.cell import ExplorationCell
from app.intelligence.exploration.repository import (
    compute_trial_counts,
    link_selection_to_opportunity,
    link_selection_to_opportunity_if_compatible,
    list_recent_cells,
    record_cell_selection,
    resolve_cell_for_publication,
    resolve_opportunity_domain_context,
)


def _cell(domain: str, fmt: str = "curiosity_explainer") -> ExplorationCell:
    return ExplorationCell(domain=domain, format=fmt, audience_value="curiosity")


def _seed_channel(db, channel_id: int) -> None:
    db.execute(
        "INSERT INTO channels (id, channel_name) VALUES (?, ?)",
        (channel_id, f"channel-{channel_id}"),
    )


@pytest.fixture(autouse=True)
def _seed_channels(db):
    _seed_channel(db, 1)
    _seed_channel(db, 2)
    db.commit()


class TestRecordAndListSelections:
    def test_record_then_list_round_trips(self, db):
        record_cell_selection(
            db,
            channel_id=1,
            workspace_id="ws",
            cell=_cell("personal_finance"),
            selected_at="2026-01-01T00:00:00",
        )
        db.commit()
        recent = list_recent_cells(db, 1)
        assert len(recent) == 1
        assert recent[0].domain == "personal_finance"

    def test_list_is_oldest_first(self, db):
        record_cell_selection(
            db,
            channel_id=1,
            workspace_id="ws",
            cell=_cell("science"),
            selected_at="2026-01-01T00:00:00",
        )
        record_cell_selection(
            db,
            channel_id=1,
            workspace_id="ws",
            cell=_cell("history"),
            selected_at="2026-01-02T00:00:00",
        )
        db.commit()
        recent = list_recent_cells(db, 1)
        assert [c.domain for c in recent] == ["science", "history"]

    def test_channel_scoped(self, db):
        record_cell_selection(
            db,
            channel_id=1,
            workspace_id="ws",
            cell=_cell("science"),
            selected_at="2026-01-01T00:00:00",
        )
        record_cell_selection(
            db,
            channel_id=2,
            workspace_id="ws",
            cell=_cell("history"),
            selected_at="2026-01-01T00:00:00",
        )
        db.commit()
        assert [c.domain for c in list_recent_cells(db, 1)] == ["science"]
        assert [c.domain for c in list_recent_cells(db, 2)] == ["history"]

    def test_invalid_source_rejected(self, db):
        with pytest.raises(ValueError):
            record_cell_selection(
                db,
                channel_id=1,
                workspace_id="ws",
                cell=_cell("science"),
                selected_at="2026-01-01T00:00:00",
                source="not_a_real_source",
            )


class TestLinkSelectionToOpportunity:
    def test_link_backfills_opportunity_id(self, db):
        db.execute("PRAGMA foreign_keys = OFF")
        db.execute(
            """INSERT INTO opportunities
                   (id, channel_id, discovery_run_id, normalized_topic, raw_topic, title,
                    current_lifecycle_state, created_at, updated_at)
               VALUES (42, 1, 0, 'x', 'x', 'x', 'new', datetime('now'), datetime('now'))"""
        )
        selection_id = record_cell_selection(
            db,
            channel_id=1,
            workspace_id="ws",
            cell=_cell("personal_finance"),
            selected_at="2026-01-01T00:00:00",
        )
        db.commit()
        row = db.execute(
            "SELECT opportunity_id FROM exploration_cell_selections WHERE id = ?",
            (selection_id,),
        ).fetchone()
        assert row["opportunity_id"] is None

        link_selection_to_opportunity(db, selection_id, 42)
        db.commit()
        row = db.execute(
            "SELECT opportunity_id FROM exploration_cell_selections WHERE id = ?",
            (selection_id,),
        ).fetchone()
        assert row["opportunity_id"] == 42


class TestComputeTrialCounts:
    def test_counts_by_normalized_domain_format_pair(self, db):
        record_cell_selection(
            db,
            channel_id=1,
            workspace_id="ws",
            cell=_cell("science"),
            selected_at="2026-01-01T00:00:00",
        )
        record_cell_selection(
            db,
            channel_id=1,
            workspace_id="ws",
            cell=_cell("Science"),
            selected_at="2026-01-02T00:00:00",
        )
        record_cell_selection(
            db,
            channel_id=1,
            workspace_id="ws",
            cell=_cell("history"),
            selected_at="2026-01-03T00:00:00",
        )
        db.commit()
        counts = compute_trial_counts(db, 1)
        assert counts[("science", "curiosity_explainer")] == 2
        assert counts[("history", "curiosity_explainer")] == 1

    def test_empty_history_yields_empty_counts(self, db):
        assert compute_trial_counts(db, 1) == {}


class TestOldPublicationRecordsRemainCompatible:
    """§18 — old rows/queries created before this migration must not break."""

    def test_migration_is_idempotent(self, db):
        # The `db` fixture already ran the migration once; re-running the
        # same executescript-based DDL again must not raise.
        from app.core.database import _apply_v57_exploration_portfolio

        _apply_v57_exploration_portfolio(db)  # no-op on tables that already exist

    def test_opportunity_id_is_nullable_for_selections_with_no_result(self, db):
        record_cell_selection(
            db,
            channel_id=1,
            workspace_id="ws",
            cell=_cell("personal_finance"),
            selected_at="2026-01-01T00:00:00",
        )
        db.commit()
        row = db.execute("SELECT opportunity_id FROM exploration_cell_selections").fetchone()
        assert row["opportunity_id"] is None


class TestResolveCellForPublicationLineage:
    """Phase 7B, §3 — the full lineage proof: exploration cell selection ->
    opportunity -> topic -> publishing plan -> publication. Seeds the real
    chain (FK-disabled for the unrelated NOT NULL production tables this
    join doesn't touch) rather than mocking the join away."""

    def _seed_full_chain(
        self, db, *, opportunity_id: int, topic_id: int, plan_id: int, publication_id: int
    ) -> None:
        db.execute("PRAGMA foreign_keys = OFF")
        db.execute(
            """INSERT INTO opportunities
                   (id, channel_id, discovery_run_id, normalized_topic, raw_topic, title,
                    current_lifecycle_state, created_at, updated_at)
               VALUES (?, 1, 0, 'x', 'x', 'x', 'new', datetime('now'), datetime('now'))""",
            (opportunity_id,),
        )
        db.execute(
            "INSERT INTO topics (id, title, promoted_opportunity_id) VALUES (?, 'x', ?)",
            (topic_id, opportunity_id),
        )
        db.execute(
            """INSERT INTO publishing_plans
                   (id, render_manifest_id, topic_id, production_plan_id, script_id,
                    scene_manifest_id, narration_run_id, caption_run_id, input_hash,
                    publishing_engine_version, metadata_version, provider, provider_version,
                    title, description, visibility, schedule_type, status,
                    created_at, updated_at)
               VALUES (?, 1, ?, 1, 1, 1, 1, 1, ?, '1.0.0', '1.0.0', 'youtube', '1.0.0',
                       'x', 'x', 'private', 'scheduled', 'draft',
                       datetime('now'), datetime('now'))""",
            (plan_id, topic_id, f"hash-{plan_id}"),
        )
        db.execute(
            """INSERT INTO publications
                   (id, publishing_plan_id, publishing_job_id, provider, provider_version,
                    publishing_engine_version, input_hash, output_sha256,
                    status, visibility, channel_id, created_at, updated_at)
               VALUES (?, ?, 1, 'youtube', '1.0.0', '1.0.0', ?, ?, 'uploading', 'private', 1,
                       datetime('now'), datetime('now'))""",
            (publication_id, plan_id, f"pub-hash-{publication_id}", f"sha-{publication_id}"),
        )
        db.commit()

    def test_publication_resolves_back_to_its_originating_cell(self, db):
        self._seed_full_chain(db, opportunity_id=1, topic_id=1, plan_id=1, publication_id=1)
        selection_id = record_cell_selection(
            db,
            channel_id=1,
            workspace_id="ws",
            cell=_cell("personal_finance", "practical_micro_lesson"),
            selected_at="2026-01-01T00:00:00",
        )
        db.commit()
        link_selection_to_opportunity(db, selection_id, 1)
        db.commit()

        cell = resolve_cell_for_publication(db, 1)
        assert cell is not None
        assert cell.domain == "personal_finance"
        assert cell.format == "practical_micro_lesson"

    def test_publication_without_linked_cell_resolves_to_none(self, db):
        """Old publications (pre-Phase-7A, or simply never linked) must
        remain valid — resolving to None, never raising or fabricating."""
        self._seed_full_chain(db, opportunity_id=2, topic_id=2, plan_id=2, publication_id=2)
        assert resolve_cell_for_publication(db, 2) is None

    def test_nonexistent_publication_resolves_to_none(self, db):
        assert resolve_cell_for_publication(db, 999) is None


def _seed_opportunity(
    db,
    opportunity_id: int,
    *,
    channel_id: int = 1,
    title: str = "",
    normalized_topic: str = "x",
    topic_summary: str = "",
    canonical_cluster_id: int | None = None,
) -> None:
    db.execute("PRAGMA foreign_keys = OFF")
    db.execute(
        """INSERT INTO opportunities
               (id, channel_id, discovery_run_id, normalized_topic, raw_topic, title,
                topic_summary, current_lifecycle_state, canonical_cluster_id,
                created_at, updated_at)
           VALUES (?, ?, 0, ?, ?, ?, ?, 'new', ?, datetime('now'), datetime('now'))""",
        (
            opportunity_id,
            channel_id,
            normalized_topic,
            normalized_topic,
            title,
            topic_summary,
            canonical_cluster_id,
        ),
    )


class TestResolveOpportunityDomainContext:
    def test_combines_title_topic_and_summary(self, db):
        _seed_opportunity(
            db,
            10,
            title="Everyday conversational phrases",
            normalized_topic="everyday conversational phrases",
            topic_summary="practical phrases for new language learners",
        )
        db.commit()
        context = resolve_opportunity_domain_context(db, 10)
        assert "conversational" in context
        assert "phrases" in context
        assert "language" in context

    def test_includes_the_original_probes_own_domain_family(self, db):
        """domain_family lives on the ORIGINAL cold-start probe, not the
        topic cluster — reaching it means joining through
        market_cluster_members, the real membership link interpretation
        itself writes (see resolve_opportunity_domain_context's own
        docstring)."""
        db.execute("PRAGMA foreign_keys = OFF")
        db.execute(
            """INSERT INTO market_canonical_clusters
                   (id, canonical_label, normalized_label, semantic_fingerprint)
               VALUES (1, 'science of cooking food', 'science of cooking food', 'fp1')"""
        )
        db.execute(
            """INSERT INTO market_exploration_runs
                   (id, channel_id, input_hash, created_at)
               VALUES (1, 1, 'run-hash', datetime('now'))"""
        )
        db.execute(
            """INSERT INTO market_exploration_probes
                   (id, exploration_run_id, query_text, normalized_query, probe_type,
                    domain_family, status, input_hash, created_at)
               VALUES (1, 1, 'science of cooking food', 'science cooking food',
                       'market_region', 'Food & Culinary Science', 'selected',
                       'probe-hash-1', datetime('now'))"""
        )
        db.execute(
            """INSERT INTO market_interpretation_runs
                   (id, evidence_cutoff, input_hash, status, started_at)
               VALUES (1, datetime('now'), 'interp-hash-1', 'completed', datetime('now'))"""
        )
        db.execute(
            """INSERT INTO market_topic_clusters
                   (id, interpretation_run_id, cluster_label, normalized_label,
                    input_hash, canonical_cluster_id, created_at)
               VALUES (1, 1, 'science of cooking food', 'science of cooking food',
                       'ih1', 1, datetime('now'))"""
        )
        db.execute("INSERT INTO market_cluster_members (cluster_id, probe_id) VALUES (1, 1)")
        _seed_opportunity(db, 11, title="science of cooking food", canonical_cluster_id=1)
        db.commit()

        context = resolve_opportunity_domain_context(db, 11)
        assert "Food & Culinary Science" in context

    def test_missing_opportunity_returns_empty_string(self, db):
        assert resolve_opportunity_domain_context(db, 999) == ""


class TestLinkSelectionToOpportunityIfCompatible:
    """§8.F/§8.H — the safe linkage path: compatible opportunities get
    linked, incompatible ones stay in the general pool untouched, and no
    opportunity is ever fabricated."""

    def test_compatible_opportunity_is_linked(self, db):
        _seed_opportunity(
            db,
            20,
            title="everyday vocabulary and pronunciation for beginners",
            normalized_topic="everyday vocabulary and pronunciation for beginners",
        )
        selection_id = record_cell_selection(
            db,
            channel_id=1,
            workspace_id="ws",
            cell=_cell("language_learning", "practical_micro_lesson"),
            selected_at="2026-01-01T00:00:00",
        )
        db.commit()

        result = link_selection_to_opportunity_if_compatible(
            db, selection_id, 20, domain="language_learning"
        )
        db.commit()

        assert result.eligible_for_linkage is True
        row = db.execute(
            "SELECT opportunity_id FROM exploration_cell_selections WHERE id = ?",
            (selection_id,),
        ).fetchone()
        assert row["opportunity_id"] == 20

    def test_incompatible_opportunity_is_never_linked_but_remains_usable(self, db):
        """§8.F — the exact real regression: a language_learning cell must
        never inherit a food-science opportunity. The opportunity itself
        stays completely valid and untouched in the general pool."""
        _seed_opportunity(
            db,
            21,
            title="science of cooking food",
            normalized_topic="science of cooking food chemical reactions heat",
        )
        selection_id = record_cell_selection(
            db,
            channel_id=1,
            workspace_id="ws",
            cell=_cell("language_learning", "practical_micro_lesson"),
            selected_at="2026-01-01T00:00:00",
        )
        db.commit()

        result = link_selection_to_opportunity_if_compatible(
            db, selection_id, 21, domain="language_learning"
        )
        db.commit()

        assert result.eligible_for_linkage is False
        selection_row = db.execute(
            "SELECT opportunity_id FROM exploration_cell_selections WHERE id = ?",
            (selection_id,),
        ).fetchone()
        assert selection_row["opportunity_id"] is None

        # The opportunity itself is untouched — still a normal, valid,
        # usable opportunity in the general pool.
        opp_row = db.execute(
            "SELECT current_lifecycle_state, title FROM opportunities WHERE id = ?",
            (21,),
        ).fetchone()
        assert opp_row["current_lifecycle_state"] == "new"
        assert opp_row["title"] == "science of cooking food"

    def test_no_compatible_opportunity_leaves_selection_unlinked(self, db):
        """§8.H — recovery must not fabricate a link when nothing
        discovered is actually compatible."""
        _seed_opportunity(db, 22, title="astrophysics and the edge of the universe")
        selection_id = record_cell_selection(
            db,
            channel_id=1,
            workspace_id="ws",
            cell=_cell("personal_finance", "curiosity_explainer"),
            selected_at="2026-01-01T00:00:00",
        )
        db.commit()

        result = link_selection_to_opportunity_if_compatible(
            db, selection_id, 22, domain="personal_finance"
        )
        db.commit()

        assert result.classification == "incompatible"
        recent = list_recent_cells(db, 1)
        assert recent[-1].domain == "personal_finance"  # the cell selection itself still exists
