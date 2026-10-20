"""Phase 5, §13 — candidate-supply diagnostics.

`candidate_supply_report` is a pure read-only aggregation over the SAME
`visual_candidate_evidence` rows Phase 18G already persists — no new
schema. These tests seed that table directly (real schema, via the `db`
fixture) rather than going through a full render, since the point is to
pin the aggregation logic itself.
"""

from __future__ import annotations

from app.visuals.candidate_supply import candidate_supply_report


def _seed_beat(db, scene_manifest_id: int, beat_index: int) -> int:
    db.execute(
        """INSERT INTO visual_beats (
            scene_manifest_id, beat_index, scene_index, segment_id,
            start_ms, end_ms, duration_ms, narration_text, visual_intent,
            engine_version, planner_version
        ) VALUES (?, ?, 0, 1, 0, 3000, 3000, 'text', 'entity', '1.0', '1.0')""",
        (scene_manifest_id, beat_index),
    )
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def _seed_evidence(db, visual_beat_id: int, *, decision: str, rejection_reason: str | None) -> None:
    db.execute(
        """INSERT INTO visual_candidate_evidence (
            visual_beat_id, provider, provider_asset_id, asset_key, decision, rejection_reason
        ) VALUES (?, 'pexels', '1', 'pexels:1', ?, ?)""",
        (visual_beat_id, decision, rejection_reason),
    )


class TestCandidateSupplyReport:
    def test_empty_render_reports_zero_everything(self, db):
        report = candidate_supply_report(db, scene_manifest_id=999)
        assert report.total_beats == 0
        assert report.candidates_retrieved == 0

    def test_beat_with_no_evidence_rows_counts_as_zero_candidates(self, db):
        db.execute("PRAGMA foreign_keys = OFF")
        _seed_beat(db, 1, 0)
        report = candidate_supply_report(db, scene_manifest_id=1)
        assert report.total_beats == 1
        assert report.beats_with_zero_candidates == 1
        assert report.candidates_retrieved == 0

    def test_selected_and_eligible_not_selected_both_count_as_accept(self, db):
        db.execute("PRAGMA foreign_keys = OFF")
        beat_id = _seed_beat(db, 1, 0)
        _seed_evidence(db, beat_id, decision="selected", rejection_reason=None)
        _seed_evidence(db, beat_id, decision="eligible_not_selected", rejection_reason=None)
        report = candidate_supply_report(db, scene_manifest_id=1)
        assert report.candidates_accept == 2
        assert report.candidates_verify == 0
        assert report.candidates_reject == 0

    def test_needs_verification_counts_as_verify_not_reject(self, db):
        db.execute("PRAGMA foreign_keys = OFF")
        beat_id = _seed_beat(db, 1, 0)
        _seed_evidence(db, beat_id, decision="rejected", rejection_reason="needs_verification")
        report = candidate_supply_report(db, scene_manifest_id=1)
        assert report.candidates_verify == 1
        assert report.candidates_reject == 0
        assert report.rejection_reasons == {}

    def test_true_rejections_are_grouped_by_reason(self, db):
        db.execute("PRAGMA foreign_keys = OFF")
        beat_id = _seed_beat(db, 1, 0)
        _seed_evidence(db, beat_id, decision="rejected", rejection_reason="weak_relevance_evidence")
        _seed_evidence(db, beat_id, decision="rejected", rejection_reason="weak_relevance_evidence")
        _seed_evidence(db, beat_id, decision="rejected", rejection_reason="matches_avoid_concept")
        report = candidate_supply_report(db, scene_manifest_id=1)
        assert report.candidates_reject == 3
        assert report.rejection_reasons == {
            "weak_relevance_evidence": 2,
            "matches_avoid_concept": 1,
        }

    def test_per_beat_rates_and_dict_export(self, db):
        db.execute("PRAGMA foreign_keys = OFF")
        beat0 = _seed_beat(db, 1, 0)
        _seed_beat(db, 1, 1)  # a second beat with zero candidates
        _seed_evidence(db, beat0, decision="selected", rejection_reason=None)
        _seed_evidence(db, beat0, decision="rejected", rejection_reason="below_relevance_floor")
        report = candidate_supply_report(db, scene_manifest_id=1)
        assert report.total_beats == 2
        assert report.beats_with_any_candidate == 1
        assert report.beats_with_zero_candidates == 1
        assert report.candidates_retrieved_per_beat == 1.0  # 2 candidates / 2 beats
        d = report.as_dict()
        assert d["candidates_reject"] == 1
        assert d["rejection_reasons"] == {"below_relevance_floor": 1}

    def test_unknown_rejection_reason_is_not_silently_dropped(self, db):
        db.execute("PRAGMA foreign_keys = OFF")
        beat_id = _seed_beat(db, 1, 0)
        _seed_evidence(db, beat_id, decision="rejected", rejection_reason=None)
        report = candidate_supply_report(db, scene_manifest_id=1)
        assert report.rejection_reasons == {"unknown": 1}
