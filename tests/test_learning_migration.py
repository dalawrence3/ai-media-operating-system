"""Tests for Phase 11 schema migration — version 16 → 17."""

from __future__ import annotations

import pathlib
import sqlite3
import tempfile

import pytest

from app.core.database import SCHEMA_VERSION, open_db


class TestSchemaVersion:
    def test_schema_version_is_31(self):
        assert SCHEMA_VERSION == 51

    def test_fresh_db_is_at_version_17(self):
        with tempfile.TemporaryDirectory() as d:
            conn = open_db(pathlib.Path(d) / "test.db")
            row = conn.execute("SELECT version FROM schema_version").fetchone()
            assert row[0] == SCHEMA_VERSION

    def test_learning_tables_exist(self):
        with tempfile.TemporaryDirectory() as d:
            conn = open_db(pathlib.Path(d) / "test.db")
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "learning_runs" in tables
            assert "optimization_recommendations" in tables
            assert "recommendation_review_events" in tables

    def test_learning_runs_columns(self):
        with tempfile.TemporaryDirectory() as d:
            conn = open_db(pathlib.Path(d) / "test.db")
            cols = {r[1] for r in conn.execute("PRAGMA table_info(learning_runs)").fetchall()}
            required = {
                "id",
                "topic_id",
                "publication_id",
                "publication_count",
                "recommendation_count",
                "status",
                "engine_version",
                "schema_version",
                "input_hash",
                "error",
                "created_at",
                "completed_at",
            }
            assert required <= cols

    def test_optimization_recommendations_columns(self):
        with tempfile.TemporaryDirectory() as d:
            conn = open_db(pathlib.Path(d) / "test.db")
            cols = {
                r[1]
                for r in conn.execute("PRAGMA table_info(optimization_recommendations)").fetchall()
            }
            required = {
                "id",
                "learning_run_id",
                "topic_id",
                "publication_id",
                "domain",
                "subsystem",
                "measure",
                "title",
                "explanation",
                "expected_improvement",
                "evidence_json",
                "confidence",
                "confidence_score",
                "affected_subsystem",
                "subsystem_entity_type",
                "subsystem_entity_id",
                "experiment_id",
                "engine_version",
                "schema_version",
                "input_hash",
                "status",
                "superseded_at",
                "superseded_by_id",
                "created_at",
            }
            assert required <= cols

    def test_recommendation_review_events_columns(self):
        with tempfile.TemporaryDirectory() as d:
            conn = open_db(pathlib.Path(d) / "test.db")
            cols = {
                r[1]
                for r in conn.execute("PRAGMA table_info(recommendation_review_events)").fetchall()
            }
            required = {
                "id",
                "recommendation_id",
                "topic_id",
                "event_type",
                "reviewer",
                "notes",
                "expected_outcome",
                "input_hash",
                "created_at",
            }
            assert required <= cols


class TestMigrationFrom16:
    def _build_v16_db(self, path: pathlib.Path) -> sqlite3.Connection:
        """Create a v16 database by patching SCHEMA_VERSION temporarily."""
        import app.core.database as db_mod

        original = db_mod.SCHEMA_VERSION
        db_mod.SCHEMA_VERSION = 16
        try:
            conn = open_db(path)
            return conn
        finally:
            db_mod.SCHEMA_VERSION = original

    def test_migration_from_16_to_17_adds_tables(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = pathlib.Path(d) / "migrate.db"
            conn16 = self._build_v16_db(db_path)
            conn16.close()

            # Re-open with v17 — should migrate
            conn17 = open_db(db_path)
            row = conn17.execute("SELECT version FROM schema_version").fetchone()
            assert row[0] == SCHEMA_VERSION

            tables = {
                r[0]
                for r in conn17.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "learning_runs" in tables
            assert "optimization_recommendations" in tables
            assert "recommendation_review_events" in tables


class TestMigrationFrom24:
    """v25 adds 'skipped' to learning_run_generator_results.status CHECK constraint."""

    def _build_v24_db(self, path: pathlib.Path) -> sqlite3.Connection:
        import app.core.database as db_mod

        original = db_mod.SCHEMA_VERSION
        db_mod.SCHEMA_VERSION = 24
        try:
            conn = open_db(path)
            return conn
        finally:
            db_mod.SCHEMA_VERSION = original

    def test_migration_from_24_to_25_updates_constraint(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = pathlib.Path(d) / "migrate24.db"
            conn24 = self._build_v24_db(db_path)
            conn24.close()

            conn25 = open_db(db_path)
            row = conn25.execute("SELECT version FROM schema_version").fetchone()
            assert row[0] == SCHEMA_VERSION

    def test_v25_allows_skipped_status(self):
        with tempfile.TemporaryDirectory() as d:
            conn = open_db(pathlib.Path(d) / "test.db")
            conn.execute("INSERT INTO topics (title, angle) VALUES ('T', 'a')")
            conn.commit()
            conn.execute(
                """
                INSERT INTO learning_runs
                    (topic_id, status, engine_version, schema_version, input_hash, created_at)
                VALUES (1,'completed','1.0.0','1.0.0','h',strftime('%Y-%m-%dT%H:%M:%S','now'))
                """
            )
            conn.commit()
            # 'skipped' must be accepted without IntegrityError
            conn.execute(
                """
                INSERT INTO learning_run_generator_results
                    (learning_run_id, generator_name, status, recommendation_count)
                VALUES (1,'retention','skipped',0)
                """
            )
            conn.commit()
            row = conn.execute(
                "SELECT status FROM learning_run_generator_results WHERE generator_name='retention'"
            ).fetchone()
            assert row[0] == "skipped"

    def test_v25_rejects_invalid_status(self):
        with tempfile.TemporaryDirectory() as d:
            conn = open_db(pathlib.Path(d) / "test.db")
            conn.execute("INSERT INTO topics (title, angle) VALUES ('T', 'a')")
            conn.commit()
            conn.execute(
                """
                INSERT INTO learning_runs
                    (topic_id, status, engine_version, schema_version, input_hash, created_at)
                VALUES (1,'completed','1.0.0','1.0.0','h',strftime('%Y-%m-%dT%H:%M:%S','now'))
                """
            )
            conn.commit()
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT INTO learning_run_generator_results
                        (learning_run_id, generator_name, status, recommendation_count)
                    VALUES (1,'retention','invalid_status',0)
                    """
                )


class TestConstraints:
    def test_confidence_check_constraint(self):
        with tempfile.TemporaryDirectory() as d:
            conn = open_db(pathlib.Path(d) / "test.db")
            conn.execute("INSERT INTO topics (title, angle) VALUES ('T', 'a')")
            conn.commit()
            conn.execute(
                """
                INSERT INTO learning_runs
                    (topic_id, status, engine_version, schema_version, input_hash, created_at)
                VALUES (1,'completed','1.0.0','1.0.0','h',strftime('%Y-%m-%dT%H:%M:%S','now'))
                """
            )
            conn.commit()
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT INTO optimization_recommendations
                        (learning_run_id, topic_id, domain, subsystem, measure,
                         title, explanation, expected_improvement,
                         confidence, confidence_score,
                         engine_version, schema_version, input_hash, created_at)
                    VALUES (1,1,'scripts','hook_effectiveness','ctr',
                            'T','E','I','very_high',0.9,'1','1','h',
                            strftime('%Y-%m-%dT%H:%M:%S','now'))
                    """
                )

    def test_status_check_constraint(self):
        with tempfile.TemporaryDirectory() as d:
            conn = open_db(pathlib.Path(d) / "test.db")
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT INTO learning_runs
                        (topic_id, status, engine_version, schema_version, input_hash)
                    VALUES (1,'invalid_status','1','1','h')
                    """
                )

    def test_domain_check_constraint(self):
        with tempfile.TemporaryDirectory() as d:
            conn = open_db(pathlib.Path(d) / "test.db")
            conn.execute("INSERT INTO topics (title, angle) VALUES ('T', 'a')")
            conn.commit()
            conn.execute(
                """
                INSERT INTO learning_runs
                    (topic_id, status, engine_version, schema_version, input_hash, created_at)
                VALUES (1,'completed','1.0.0','1.0.0','h',strftime('%Y-%m-%dT%H:%M:%S','now'))
                """
            )
            conn.commit()
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT INTO optimization_recommendations
                        (learning_run_id, topic_id, domain, subsystem, measure,
                         title, explanation, expected_improvement,
                         confidence, confidence_score,
                         engine_version, schema_version, input_hash, created_at)
                    VALUES (1,1,'invalid_domain','hook_effectiveness','ctr',
                            'T','E','I','low',0.2,'1','1','h',
                            strftime('%Y-%m-%dT%H:%M:%S','now'))
                    """
                )


class TestMigrationFrom26:
    """v27 adds content_feature_snapshots table and indexes."""

    def _build_v26_db(self, path: pathlib.Path) -> sqlite3.Connection:
        import app.core.database as db_mod

        original = db_mod.SCHEMA_VERSION
        db_mod.SCHEMA_VERSION = 26
        try:
            conn = open_db(path)
            return conn
        finally:
            db_mod.SCHEMA_VERSION = original

    def test_migration_from_26_to_27_completes(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = pathlib.Path(d) / "migrate26.db"
            conn26 = self._build_v26_db(db_path)
            conn26.close()

            conn27 = open_db(db_path)
            row = conn27.execute("SELECT version FROM schema_version").fetchone()
            assert row[0] == SCHEMA_VERSION

    def test_v27_content_feature_snapshots_table_exists(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = pathlib.Path(d) / "migrate26b.db"
            conn26 = self._build_v26_db(db_path)
            conn26.close()

            conn27 = open_db(db_path)
            tables = {
                r[0]
                for r in conn27.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "content_feature_snapshots" in tables

    def test_v27_indexes_created(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = pathlib.Path(d) / "migrate26c.db"
            conn26 = self._build_v26_db(db_path)
            conn26.close()

            conn27 = open_db(db_path)
            indexes = {
                r[0]
                for r in conn27.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                ).fetchall()
            }
            assert "idx_cfs_topic" in indexes
            assert "idx_cfs_speaking_rate" in indexes
            assert "idx_cfs_format" in indexes
            assert "idx_cfs_provider" in indexes


class TestMigrationFrom27:
    """v28 adds channel_performance_baselines and feature_performance_observations."""

    def _build_v27_db(self, path: pathlib.Path) -> sqlite3.Connection:
        import app.core.database as db_mod

        original = db_mod.SCHEMA_VERSION
        db_mod.SCHEMA_VERSION = 27
        try:
            conn = open_db(path)
            return conn
        finally:
            db_mod.SCHEMA_VERSION = original

    def test_migration_from_27_to_28_completes(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = pathlib.Path(d) / "migrate27.db"
            conn27 = self._build_v27_db(db_path)
            conn27.close()

            conn28 = open_db(db_path)
            row = conn28.execute("SELECT version FROM schema_version").fetchone()
            assert row[0] == SCHEMA_VERSION

    def test_v28_channel_baselines_table_exists(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = pathlib.Path(d) / "migrate27b.db"
            conn27 = self._build_v27_db(db_path)
            conn27.close()

            conn28 = open_db(db_path)
            tables = {
                r[0]
                for r in conn28.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "channel_performance_baselines" in tables
            assert "feature_performance_observations" in tables

    def test_v28_indexes_created(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = pathlib.Path(d) / "migrate27c.db"
            conn27 = self._build_v27_db(db_path)
            conn27.close()

            conn28 = open_db(db_path)
            indexes = {
                r[0]
                for r in conn28.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                ).fetchall()
            }
            assert "idx_cpb_channel" in indexes
            assert "idx_fpo_channel" in indexes
            assert "idx_fpo_feature" in indexes
            assert "idx_fpo_metric" in indexes


class TestMigrationFrom28:
    """v29 adds market_collection_jobs, market_intelligence_observations, and quota tables."""

    def _build_v28_db(self, path: pathlib.Path) -> sqlite3.Connection:
        import app.core.database as db_mod

        original = db_mod.SCHEMA_VERSION
        db_mod.SCHEMA_VERSION = 28
        try:
            conn = open_db(path)
            return conn
        finally:
            db_mod.SCHEMA_VERSION = original

    def test_migration_from_28_to_29_completes(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = pathlib.Path(d) / "migrate28.db"
            conn28 = self._build_v28_db(db_path)
            conn28.close()

            conn29 = open_db(db_path)
            row = conn29.execute("SELECT version FROM schema_version").fetchone()
            assert row[0] == SCHEMA_VERSION

    def test_v29_market_collection_jobs_table_exists(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = pathlib.Path(d) / "migrate28b.db"
            conn28 = self._build_v28_db(db_path)
            conn28.close()

            conn29 = open_db(db_path)
            tables = {
                r[0]
                for r in conn29.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "market_collection_jobs" in tables
            assert "market_intelligence_observations" in tables
            assert "market_job_observations" in tables
            assert "market_job_quota_usage" in tables

    def test_v29_market_indexes_created(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = pathlib.Path(d) / "migrate28c.db"
            conn28 = self._build_v28_db(db_path)
            conn28.close()

            conn29 = open_db(db_path)
            indexes = {
                r[0]
                for r in conn29.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                ).fetchall()
            }
            assert "idx_mcj_channel" in indexes
            assert "idx_mcj_status" in indexes
            assert "idx_mio_signal_type" in indexes
            assert "idx_mio_video" in indexes
            assert "idx_mjo_observation" in indexes
            assert "idx_mjqu_job" in indexes


class TestMigrationFrom29:
    """v30 adds market_velocity_estimates table (Phase 13C)."""

    def _build_v29_db(self, path: pathlib.Path) -> sqlite3.Connection:
        import app.core.database as db_mod

        original = db_mod.SCHEMA_VERSION
        db_mod.SCHEMA_VERSION = 29
        try:
            conn = open_db(path)
            return conn
        finally:
            db_mod.SCHEMA_VERSION = original

    def test_migration_from_29_to_30_completes(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = pathlib.Path(d) / "migrate29.db"
            conn29 = self._build_v29_db(db_path)
            conn29.close()

            conn30 = open_db(db_path)
            row = conn30.execute("SELECT version FROM schema_version").fetchone()
            assert row[0] == SCHEMA_VERSION

    def test_v30_velocity_table_exists(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = pathlib.Path(d) / "migrate29b.db"
            conn29 = self._build_v29_db(db_path)
            conn29.close()

            conn30 = open_db(db_path)
            tables = {
                r[0]
                for r in conn30.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "market_velocity_estimates" in tables

    def test_v30_velocity_indexes_created(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = pathlib.Path(d) / "migrate29c.db"
            conn29 = self._build_v29_db(db_path)
            conn29.close()

            conn30 = open_db(db_path)
            indexes = {
                r[0]
                for r in conn30.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                ).fetchall()
            }
            assert "idx_mve_video" in indexes
            assert "idx_mve_end_obs" in indexes
            assert "idx_mve_maturity" in indexes


class TestMigrationFrom30:
    """v31 adds market_exploration_runs, market_exploration_probes,
    market_probe_evidence (Phase 13D-A)."""

    def _build_v30_db(self, path: pathlib.Path) -> sqlite3.Connection:
        import app.core.database as db_mod

        original = db_mod.SCHEMA_VERSION
        db_mod.SCHEMA_VERSION = 30
        try:
            conn = open_db(path)
            return conn
        finally:
            db_mod.SCHEMA_VERSION = original

    def test_migration_from_30_to_31_completes(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = pathlib.Path(d) / "migrate30.db"
            conn30 = self._build_v30_db(db_path)
            conn30.close()

            conn31 = open_db(db_path)
            row = conn31.execute("SELECT version FROM schema_version").fetchone()
            assert row[0] == SCHEMA_VERSION

    def test_v31_exploration_runs_table_exists(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = pathlib.Path(d) / "migrate30b.db"
            conn30 = self._build_v30_db(db_path)
            conn30.close()

            conn31 = open_db(db_path)
            tables = {
                r[0]
                for r in conn31.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "market_exploration_runs" in tables
            assert "market_exploration_probes" in tables
            assert "market_probe_evidence" in tables

    def test_v31_exploration_indexes_created(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = pathlib.Path(d) / "migrate30c.db"
            conn30 = self._build_v30_db(db_path)
            conn30.close()

            conn31 = open_db(db_path)
            indexes = {
                r[0]
                for r in conn31.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                ).fetchall()
            }
            assert "idx_mer_channel" in indexes
            assert "idx_mer_status" in indexes
            assert "idx_mep_run" in indexes
            assert "idx_mep_probe_type" in indexes
            assert "idx_mpe_probe" in indexes


class TestMigrationFrom31:
    """v32 adds market_interpretation_runs, market_topic_clusters,
    market_cluster_members, market_cluster_signals (Phase 13E)."""

    def _build_v31_db(self, path: pathlib.Path) -> sqlite3.Connection:
        import app.core.database as db_mod

        original = db_mod.SCHEMA_VERSION
        db_mod.SCHEMA_VERSION = 31
        try:
            conn = open_db(path)
            return conn
        finally:
            db_mod.SCHEMA_VERSION = original

    def test_migration_from_31_to_32_completes(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = pathlib.Path(d) / "migrate31.db"
            conn31 = self._build_v31_db(db_path)
            conn31.close()

            conn32 = open_db(db_path)
            row = conn32.execute("SELECT version FROM schema_version").fetchone()
            assert row[0] == SCHEMA_VERSION

    def test_v32_interpretation_tables_exist(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = pathlib.Path(d) / "migrate31b.db"
            conn31 = self._build_v31_db(db_path)
            conn31.close()

            conn32 = open_db(db_path)
            tables = {
                r[0]
                for r in conn32.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "market_interpretation_runs" in tables
            assert "market_topic_clusters" in tables
            assert "market_cluster_members" in tables
            assert "market_cluster_signals" in tables

    def test_v32_interpretation_indexes_created(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = pathlib.Path(d) / "migrate31c.db"
            conn31 = self._build_v31_db(db_path)
            conn31.close()

            conn32 = open_db(db_path)
            indexes = {
                r[0]
                for r in conn32.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                ).fetchall()
            }
            assert "idx_mir_platform_provider" in indexes
            assert "idx_mir_status" in indexes
            assert "idx_mtc_run" in indexes
            assert "idx_mcs_cluster" in indexes
            assert "idx_mcs_run" in indexes

    def test_v32_exploration_tables_still_present(self):
        """Ensure v31 tables survive v32 migration."""
        with tempfile.TemporaryDirectory() as d:
            db_path = pathlib.Path(d) / "migrate31d.db"
            conn31 = self._build_v31_db(db_path)
            conn31.close()

            conn32 = open_db(db_path)
            tables = {
                r[0]
                for r in conn32.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "market_exploration_runs" in tables
            assert "market_exploration_probes" in tables
            assert "market_probe_evidence" in tables


class TestMigrationFrom32:
    """v33 adds market_canonical_clusters table and two new columns (Phase 13E.1)."""

    def _build_v32_db(self, path: pathlib.Path) -> sqlite3.Connection:
        import app.core.database as db_mod

        original = db_mod.SCHEMA_VERSION
        db_mod.SCHEMA_VERSION = 32
        try:
            conn = open_db(path)
            return conn
        finally:
            db_mod.SCHEMA_VERSION = original

    def test_migration_from_32_to_33_completes(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = pathlib.Path(d) / "migrate32.db"
            conn32 = self._build_v32_db(db_path)
            conn32.close()

            conn33 = open_db(db_path)
            row = conn33.execute("SELECT version FROM schema_version").fetchone()
            assert row[0] == SCHEMA_VERSION

    def test_v33_canonical_table_exists(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = pathlib.Path(d) / "migrate32b.db"
            conn32 = self._build_v32_db(db_path)
            conn32.close()

            conn33 = open_db(db_path)
            tables = {
                r[0]
                for r in conn33.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "market_canonical_clusters" in tables

    def test_v33_canonical_cluster_id_column_on_topic_clusters(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = pathlib.Path(d) / "migrate32c.db"
            conn32 = self._build_v32_db(db_path)
            conn32.close()

            conn33 = open_db(db_path)
            cols = {
                r[1] for r in conn33.execute("PRAGMA table_info(market_topic_clusters)").fetchall()
            }
            assert "canonical_cluster_id" in cols

    def test_v33_market_region_label_column_on_probes(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = pathlib.Path(d) / "migrate32d.db"
            conn32 = self._build_v32_db(db_path)
            conn32.close()

            conn33 = open_db(db_path)
            cols = {
                r[1]
                for r in conn33.execute("PRAGMA table_info(market_exploration_probes)").fetchall()
            }
            assert "market_region_label" in cols
