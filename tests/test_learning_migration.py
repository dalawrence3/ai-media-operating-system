"""Tests for Phase 11 schema migration — version 16 → 17."""

from __future__ import annotations

import pathlib
import sqlite3
import tempfile

import pytest

from app.core.database import SCHEMA_VERSION, open_db


class TestSchemaVersion:
    def test_schema_version_is_17(self):
        assert SCHEMA_VERSION == 19

    def test_fresh_db_is_at_version_17(self):
        with tempfile.TemporaryDirectory() as d:
            conn = open_db(pathlib.Path(d) / "test.db")
            row = conn.execute("SELECT version FROM schema_version").fetchone()
            assert row[0] == 19

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
            cols = {
                r[1]
                for r in conn.execute("PRAGMA table_info(learning_runs)").fetchall()
            }
            required = {
                "id", "topic_id", "publication_id", "publication_count",
                "recommendation_count", "status", "engine_version",
                "schema_version", "input_hash", "error", "created_at", "completed_at",
            }
            assert required <= cols

    def test_optimization_recommendations_columns(self):
        with tempfile.TemporaryDirectory() as d:
            conn = open_db(pathlib.Path(d) / "test.db")
            cols = {
                r[1]
                for r in conn.execute(
                    "PRAGMA table_info(optimization_recommendations)"
                ).fetchall()
            }
            required = {
                "id", "learning_run_id", "topic_id", "publication_id",
                "domain", "subsystem", "measure",
                "title", "explanation", "expected_improvement",
                "evidence_json", "confidence", "confidence_score",
                "affected_subsystem", "subsystem_entity_type", "subsystem_entity_id",
                "experiment_id", "engine_version", "schema_version", "input_hash",
                "status", "superseded_at", "superseded_by_id", "created_at",
            }
            assert required <= cols

    def test_recommendation_review_events_columns(self):
        with tempfile.TemporaryDirectory() as d:
            conn = open_db(pathlib.Path(d) / "test.db")
            cols = {
                r[1]
                for r in conn.execute(
                    "PRAGMA table_info(recommendation_review_events)"
                ).fetchall()
            }
            required = {
                "id", "recommendation_id", "topic_id", "event_type",
                "reviewer", "notes", "expected_outcome", "input_hash", "created_at",
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
            assert row[0] == 19

            tables = {
                r[0]
                for r in conn17.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "learning_runs" in tables
            assert "optimization_recommendations" in tables
            assert "recommendation_review_events" in tables


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
