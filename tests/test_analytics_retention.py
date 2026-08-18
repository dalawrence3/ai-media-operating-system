"""Zero-network tests for audience retention curve ingestion and scene attribution.

All tests are zero-network.  No live YouTube Analytics calls are made.
Tests cover:
  1. Retention API query shape (dimensional, correct fields)
  2. Raw row parsing → RetentionPoint objects
  3. Elapsed-time derivation from elapsed_ratio × duration
  4. Scene attribution — interior, boundary, first/last scene, out-of-range
  5. Relative retention absent/present
  6. Empty rows
  7. Persistence and read-back
  8. DB schema — table and column existence
  9. Phase A scalar metrics unchanged
 10. format_line output
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.analytics.retention import (
    RetentionCurve,
    RetentionPoint,
    SceneEntry,
    attribute_retention_curve,
    attribute_scene,
    fetch_retention_from_service,
    list_retention_for_publication,
    list_retention_for_snapshot,
    load_scene_catalog,
    parse_retention_rows,
    persist_retention_curve,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SCENES = [
    SceneEntry(scene_index=0, start_ms=0, end_ms=8081, section_type="hook"),
    SceneEntry(scene_index=1, start_ms=8081, end_ms=12632, section_type="body"),
    SceneEntry(scene_index=2, start_ms=12632, end_ms=18251, section_type="body"),
    SceneEntry(scene_index=3, start_ms=18251, end_ms=25960, section_type="body"),
    SceneEntry(scene_index=4, start_ms=25960, end_ms=37198, section_type="body"),
    SceneEntry(scene_index=5, start_ms=37198, end_ms=46811, section_type="body"),
    SceneEntry(scene_index=6, start_ms=46811, end_ms=54892, section_type="conclusion"),
    SceneEntry(scene_index=7, start_ms=54892, end_ms=58607, section_type="cta"),
]
_DURATION_MS = 58607


def _make_fake_retention_service(rows: list[list], column_names: list[str]) -> object:
    """Return a fake youtubeAnalytics service for retention dimensional queries."""
    response = {
        "kind": "youtubeAnalytics#resultTable",
        "columnHeaders": [
            {"name": n, "columnType": "DIMENSION" if i == 0 else "METRIC"}
            for i, n in enumerate(column_names)
        ],
        "rows": rows,
    }
    execute_mock = MagicMock(return_value=response)
    query_mock = MagicMock()
    query_mock.return_value.execute = execute_mock
    reports_mock = MagicMock()
    reports_mock.return_value.query = query_mock
    service_mock = MagicMock()
    service_mock.reports = reports_mock
    return service_mock


# ---------------------------------------------------------------------------
# 1. Retention API query shape
# ---------------------------------------------------------------------------


def test_retention_query_uses_elapsed_ratio_dimension():
    """The retention query must use dimensions=elapsedVideoTimeRatio."""
    service = _make_fake_retention_service(
        rows=[[0.0, 1.0, 1.0]],
        column_names=[
            "elapsedVideoTimeRatio",
            "audienceWatchRatio",
            "relativeRetentionPerformance",
        ],
    )
    captured: dict[str, str] = {}
    original_query = service.reports.return_value.query

    def capturing(**kwargs):
        captured.update(kwargs)
        return original_query(**kwargs)

    service.reports.return_value.query = capturing
    fetch_retention_from_service(
        service, "videoABC", period_start="2026-01-01", period_end="2026-08-17"
    )
    assert captured.get("dimensions") == "elapsedVideoTimeRatio"


def test_retention_query_requests_audience_watch_ratio():
    """audienceWatchRatio must be in the metrics string."""
    service = _make_fake_retention_service(rows=[], column_names=[])
    captured: dict = {}
    original_query = service.reports.return_value.query

    def capturing(**kwargs):
        captured.update(kwargs)
        return original_query(**kwargs)

    service.reports.return_value.query = capturing
    fetch_retention_from_service(service, "vid", period_start="2026-01-01", period_end="2026-08-17")
    assert "audienceWatchRatio" in captured.get("metrics", "")


def test_retention_query_requests_relative_retention():
    """relativeRetentionPerformance must be in the metrics string."""
    service = _make_fake_retention_service(rows=[], column_names=[])
    captured: dict = {}
    original_query = service.reports.return_value.query

    def capturing(**kwargs):
        captured.update(kwargs)
        return original_query(**kwargs)

    service.reports.return_value.query = capturing
    fetch_retention_from_service(service, "vid", period_start="2026-01-01", period_end="2026-08-17")
    assert "relativeRetentionPerformance" in captured.get("metrics", "")


def test_retention_query_filters_by_video():
    """The query must filter to the specific video ID."""
    service = _make_fake_retention_service(rows=[], column_names=[])
    captured: dict = {}
    original_query = service.reports.return_value.query

    def capturing(**kwargs):
        captured.update(kwargs)
        return original_query(**kwargs)

    service.reports.return_value.query = capturing
    fetch_retention_from_service(
        service, "kQH88nXdiRY", period_start="2026-01-01", period_end="2026-08-17"
    )
    assert "video==kQH88nXdiRY" in captured.get("filters", "")


def test_retention_query_strips_date_to_date_only():
    """Full ISO timestamps must be stripped to YYYY-MM-DD before the API call."""
    service = _make_fake_retention_service(rows=[], column_names=[])
    captured: dict = {}
    original_query = service.reports.return_value.query

    def capturing(**kwargs):
        captured.update(kwargs)
        return original_query(**kwargs)

    service.reports.return_value.query = capturing
    fetch_retention_from_service(
        service,
        "vid",
        period_start="2026-01-01T00:00:00Z",
        period_end="2026-08-17T23:59:59Z",
    )
    assert captured.get("startDate") == "2026-01-01"
    assert captured.get("endDate") == "2026-08-17"


# ---------------------------------------------------------------------------
# 2. Row parsing → RetentionPoint
# ---------------------------------------------------------------------------


def test_parse_retention_rows_basic():
    """Three rows parse to three RetentionPoint objects."""
    raw = [
        {
            "elapsedVideoTimeRatio": 0.0,
            "audienceWatchRatio": 1.0,
            "relativeRetentionPerformance": 1.1,
        },
        {
            "elapsedVideoTimeRatio": 0.5,
            "audienceWatchRatio": 0.6,
            "relativeRetentionPerformance": 0.9,
        },
        {
            "elapsedVideoTimeRatio": 1.0,
            "audienceWatchRatio": 0.35,
            "relativeRetentionPerformance": 0.8,
        },
    ]
    points = parse_retention_rows(raw)
    assert len(points) == 3
    assert points[0].elapsed_ratio == pytest.approx(0.0)
    assert points[0].audience_watch_ratio == pytest.approx(1.0)
    assert points[0].relative_retention == pytest.approx(1.1)
    assert points[1].elapsed_ratio == pytest.approx(0.5)
    assert points[2].elapsed_ratio == pytest.approx(1.0)


def test_parse_retention_rows_relative_absent():
    """relativeRetentionPerformance is None when absent from API response."""
    raw = [{"elapsedVideoTimeRatio": 0.25, "audienceWatchRatio": 0.8}]
    points = parse_retention_rows(raw)
    assert len(points) == 1
    assert points[0].relative_retention is None


def test_parse_retention_rows_empty():
    """Empty rows list → empty points list (no data for this period)."""
    assert parse_retention_rows([]) == []


def test_parse_retention_rows_skips_incomplete():
    """Rows missing required fields are silently skipped."""
    raw = [
        {"elapsedVideoTimeRatio": 0.1},  # missing audienceWatchRatio
        {"audienceWatchRatio": 0.9},  # missing elapsed ratio
        {"elapsedVideoTimeRatio": 0.5, "audienceWatchRatio": 0.7},  # valid
    ]
    points = parse_retention_rows(raw)
    assert len(points) == 1
    assert points[0].elapsed_ratio == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# 3. Elapsed-time derivation
# ---------------------------------------------------------------------------


def test_elapsed_ms_derived_from_ratio_and_duration():
    """elapsed_ms = round(elapsed_ratio × video_duration_ms)."""
    curve = RetentionCurve(
        provider_video_id="v",
        scene_manifest_id=1,
        video_duration_ms=58607,
        period_start="2026-01-01",
        period_end="2026-08-17",
        points=[RetentionPoint(elapsed_ratio=0.5, audience_watch_ratio=0.7)],
    )
    attribute_retention_curve(curve, _SCENES)
    assert curve.points[0].elapsed_ms == round(0.5 * 58607)  # 29304
    assert curve.points[0].elapsed_seconds == pytest.approx(round(0.5 * 58607) / 1000.0)


def test_elapsed_none_when_duration_unknown():
    """elapsed_ms stays None when video_duration_ms is None."""
    curve = RetentionCurve(
        provider_video_id="v",
        scene_manifest_id=1,
        video_duration_ms=None,
        period_start=None,
        period_end=None,
        points=[RetentionPoint(elapsed_ratio=0.5, audience_watch_ratio=0.7)],
    )
    attribute_retention_curve(curve, _SCENES)
    assert curve.points[0].elapsed_ms is None
    assert curve.points[0].elapsed_seconds is None


# ---------------------------------------------------------------------------
# 4. Scene attribution
# ---------------------------------------------------------------------------


def test_attribute_scene_interior_hook():
    """A timestamp well inside the hook maps to scene 0 / hook."""
    scene = attribute_scene(1000, _SCENES)
    assert scene is not None
    assert scene.scene_index == 0
    assert scene.section_type == "hook"


def test_attribute_scene_at_hook_start():
    """start_ms=0 is the first point of the hook."""
    scene = attribute_scene(0, _SCENES)
    assert scene is not None
    assert scene.scene_index == 0


def test_attribute_scene_at_boundary_enters_next():
    """At exactly start_ms of scene 1, attribution crosses to scene 1 (body)."""
    scene = attribute_scene(8081, _SCENES)
    assert scene is not None
    assert scene.scene_index == 1
    assert scene.section_type == "body"


def test_attribute_scene_last_scene_interior():
    """A timestamp inside the CTA scene maps to scene 7 / cta."""
    scene = attribute_scene(56000, _SCENES)
    assert scene is not None
    assert scene.scene_index == 7
    assert scene.section_type == "cta"


def test_attribute_scene_at_exact_end_maps_to_last():
    """elapsed_ms == max end_ms maps to the last scene (inclusive boundary)."""
    scene = attribute_scene(58607, _SCENES)
    assert scene is not None
    assert scene.scene_index == 7


def test_attribute_scene_conclusion():
    """A timestamp inside conclusion maps to scene 6 / conclusion."""
    scene = attribute_scene(50000, _SCENES)
    assert scene is not None
    assert scene.scene_index == 6
    assert scene.section_type == "conclusion"


def test_attribute_scene_beyond_end_returns_none():
    """A timestamp past the video end returns None."""
    assert attribute_scene(99999, _SCENES) is None


def test_attribute_scene_empty_scenes():
    """Empty scene list always returns None."""
    assert attribute_scene(1000, []) is None


def test_attribution_populates_scene_index_and_type():
    """attribute_retention_curve() fills scene_index and section_type on each point."""
    curve = RetentionCurve(
        provider_video_id="v",
        scene_manifest_id=4,
        video_duration_ms=_DURATION_MS,
        period_start="2026-01-01",
        period_end="2026-08-17",
        points=[
            RetentionPoint(elapsed_ratio=0.0, audience_watch_ratio=1.0),  # scene 0 hook
            RetentionPoint(elapsed_ratio=0.5, audience_watch_ratio=0.65),  # mid-body
            RetentionPoint(elapsed_ratio=1.0, audience_watch_ratio=0.3),  # scene 7 cta
        ],
    )
    attribute_retention_curve(curve, _SCENES)
    assert curve.points[0].scene_index == 0
    assert curve.points[0].section_type == "hook"
    assert curve.points[2].scene_index == 7
    assert curve.points[2].section_type == "cta"


def test_attribution_scene_index_none_when_out_of_range():
    """Points outside all scene windows get scene_index=None."""
    curve = RetentionCurve(
        provider_video_id="v",
        scene_manifest_id=4,
        video_duration_ms=100000,  # longer than actual scenes
        period_start=None,
        period_end=None,
        points=[RetentionPoint(elapsed_ratio=0.99, audience_watch_ratio=0.1)],
    )
    attribute_retention_curve(curve, _SCENES)
    # elapsed_ms = round(0.99 * 100000) = 99000 — past all scene end_ms
    assert curve.points[0].elapsed_ms == 99000
    assert curve.points[0].scene_index is None
    assert curve.points[0].section_type is None


# ---------------------------------------------------------------------------
# 5. Persistence and read-back
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db(tmp_path):
    from app.core.database import open_db

    return open_db(tmp_path / "test.db")


def _make_snapshot(conn) -> int:
    """Insert a minimal analytics_snapshots row; return its id."""
    from datetime import UTC, datetime

    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    cur = conn.execute(
        """
        INSERT INTO analytics_snapshots (
            publication_id, publishing_plan_id, publishing_job_id,
            render_manifest_id, scene_manifest_id, production_plan_id,
            script_id, topic_id, narration_run_id, caption_run_id,
            provider, provider_version, adapter_version,
            engine_version, analytics_schema_version, db_schema_version,
            input_hash, raw_metrics_json, ingested_at, created_at
        ) VALUES (1,1,1,1,4,1,1,1,1,1,'youtube','1.0.0','1.0.0',
                  '1.0.0','1.0.0',22,'hash-ret-test','{}',?,?)
        """,
        (now, now),
    )
    conn.commit()
    return cur.lastrowid


def test_persist_and_read_back(tmp_db):
    """Persisted retention points are read back correctly."""
    snap_id = _make_snapshot(tmp_db)
    curve = RetentionCurve(
        provider_video_id="kQH88nXdiRY",
        scene_manifest_id=4,
        video_duration_ms=_DURATION_MS,
        period_start="2026-08-17",
        period_end="2026-08-17",
        points=[
            RetentionPoint(
                elapsed_ratio=0.0,
                audience_watch_ratio=1.0,
                relative_retention=1.05,
                elapsed_ms=0,
                elapsed_seconds=0.0,
                scene_index=0,
                section_type="hook",
            ),
            RetentionPoint(
                elapsed_ratio=0.5,
                audience_watch_ratio=0.65,
                relative_retention=None,
                elapsed_ms=29304,
                elapsed_seconds=29.304,
                scene_index=3,
                section_type="body",
            ),
        ],
    )
    n = persist_retention_curve(tmp_db, curve, snapshot_id=snap_id, publication_id=1)
    assert n == 2

    rows = list_retention_for_snapshot(tmp_db, snap_id)
    assert len(rows) == 2
    assert rows[0].elapsed_ratio == pytest.approx(0.0)
    assert rows[0].audience_watch_ratio == pytest.approx(1.0)
    assert rows[0].relative_retention == pytest.approx(1.05)
    assert rows[0].scene_index == 0
    assert rows[0].section_type == "hook"
    assert rows[1].relative_retention is None
    assert rows[1].scene_index == 3
    assert rows[1].section_type == "body"


def test_persist_empty_curve(tmp_db):
    """Persisting a curve with no points stores nothing and returns 0."""
    snap_id = _make_snapshot(tmp_db)
    curve = RetentionCurve(
        provider_video_id="v",
        scene_manifest_id=4,
        video_duration_ms=None,
        period_start=None,
        period_end=None,
        points=[],
    )
    n = persist_retention_curve(tmp_db, curve, snapshot_id=snap_id, publication_id=1)
    assert n == 0
    assert list_retention_for_snapshot(tmp_db, snap_id) == []


def test_list_retention_for_publication(tmp_db):
    """list_retention_for_publication returns all points for a publication."""
    snap_id = _make_snapshot(tmp_db)
    curve = RetentionCurve(
        provider_video_id="v",
        scene_manifest_id=4,
        video_duration_ms=_DURATION_MS,
        period_start="2026-08-17",
        period_end="2026-08-17",
        points=[
            RetentionPoint(
                elapsed_ratio=0.1,
                audience_watch_ratio=0.9,
                elapsed_ms=5860,
                elapsed_seconds=5.86,
                scene_index=0,
                section_type="hook",
            ),
        ],
    )
    persist_retention_curve(tmp_db, curve, snapshot_id=snap_id, publication_id=1)
    rows = list_retention_for_publication(tmp_db, 1)
    assert len(rows) == 1
    assert rows[0].publication_id == 1


# ---------------------------------------------------------------------------
# 6. DB schema
# ---------------------------------------------------------------------------


def test_retention_table_exists(tmp_db):
    """analytics_retention_points table must be created by migration."""
    tables = {
        r[0] for r in tmp_db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "analytics_retention_points" in tables


def test_retention_table_columns(tmp_db):
    """analytics_retention_points must have the required columns."""
    cols = {
        r[1] for r in tmp_db.execute("PRAGMA table_info(analytics_retention_points)").fetchall()
    }
    required = {
        "id",
        "snapshot_id",
        "publication_id",
        "scene_manifest_id",
        "elapsed_ratio",
        "elapsed_ms",
        "elapsed_seconds",
        "audience_watch_ratio",
        "relative_retention",
        "scene_index",
        "section_type",
        "period_start",
        "period_end",
        "created_at",
    }
    assert required <= cols


def test_schema_version_is_22(tmp_db):
    """Schema must be at version 22 after migration."""
    from app.core.database import SCHEMA_VERSION

    assert SCHEMA_VERSION == 23
    version = tmp_db.execute("SELECT version FROM schema_version").fetchone()[0]
    assert version == 23


# ---------------------------------------------------------------------------
# 7. load_scene_catalog from DB
# ---------------------------------------------------------------------------


def _populate_scene_catalog(conn) -> None:
    """Insert minimal segments and scene manifest for load_scene_catalog tests.

    FK constraints are disabled so we don't need real parent rows for plan,
    render_manifest, or narration_assets — only the join between
    scene_manifest_scenes and production_segments matters here.
    """
    conn.execute("PRAGMA foreign_keys = OFF")
    # production segments (plan_id 99 is fictional — FK off)
    for i, section in enumerate(["hook", "body", "conclusion", "cta"]):
        conn.execute(
            "INSERT INTO production_segments "
            "(id, plan_id, segment_index, section_index, section_type, "
            " narration_text, created_at) "
            "VALUES (?, 99, ?, 0, ?, '', '2026-01-01')",
            (200 + i, i, section),
        )
    # scene manifest (all FK parent IDs are fictional — FK off)
    conn.execute(
        "INSERT INTO scene_manifests "
        "(id, caption_run_id, narration_run_id, plan_id, script_id, topic_id,"
        " input_hash, manifest_schema_version, planner_version, status, created_at)"
        " VALUES (99, 99, 99, 99, 99, 99, 'hash-test', '1.0', '1.0', 'approved', '2026-01-01')"
    )
    # scene_manifest_scenes (4 scenes)
    for i, (start, end) in enumerate(
        [
            (0, 8081),
            (8081, 25960),
            (25960, 54892),
            (54892, 58607),
        ]
    ):
        conn.execute(
            "INSERT INTO scene_manifest_scenes "
            "(manifest_id, scene_index, segment_id, narration_text,"
            " start_ms, end_ms, duration_ms,"
            " shot_type, camera_movement, transition_in, transition_out,"
            " visual_objective, visual_rationale, confidence, created_at)"
            " VALUES (99, ?, ?, '', ?, ?, ?, '', '', '', '', '', '', 0.9, '2026-01-01')",
            (i, 200 + i, start, end, end - start),
        )
    conn.commit()


def test_load_scene_catalog_returns_scenes_and_duration(tmp_db):
    """load_scene_catalog returns the correct scenes and total duration."""
    _populate_scene_catalog(tmp_db)
    scenes, duration_ms = load_scene_catalog(tmp_db, 99)
    assert len(scenes) == 4
    assert duration_ms == 58607
    assert scenes[0].section_type == "hook"
    assert scenes[3].section_type == "cta"


def test_load_scene_catalog_empty_manifest(tmp_db):
    """An empty or absent manifest returns ([], None)."""
    scenes, duration_ms = load_scene_catalog(tmp_db, 9999)
    assert scenes == []
    assert duration_ms is None


# ---------------------------------------------------------------------------
# 8. format_line output
# ---------------------------------------------------------------------------


def test_format_line_with_all_fields():
    """format_line produces a readable line with all fields present."""
    from datetime import UTC, datetime

    from app.analytics.retention import RetentionRow

    rr = RetentionRow(
        id=1,
        snapshot_id=1,
        publication_id=1,
        scene_manifest_id=4,
        elapsed_ratio=0.21,
        elapsed_ms=12400,
        elapsed_seconds=12.4,
        audience_watch_ratio=0.7532,
        relative_retention=1.02,
        scene_index=2,
        section_type="body",
        period_start="2026-08-17",
        period_end="2026-08-17",
        created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S"),
    )
    line = rr.format_line()
    assert "12.4s" in line
    assert "scene 2" in line
    assert "body" in line
    assert "0.7532" in line
    assert "1.0200" in line


def test_format_line_without_relative_retention():
    """format_line omits rel= when relative_retention is None."""
    from datetime import UTC, datetime

    from app.analytics.retention import RetentionRow

    rr = RetentionRow(
        id=1,
        snapshot_id=1,
        publication_id=1,
        scene_manifest_id=4,
        elapsed_ratio=0.5,
        elapsed_ms=29303,
        elapsed_seconds=29.3,
        audience_watch_ratio=0.65,
        relative_retention=None,
        scene_index=3,
        section_type="body",
        period_start=None,
        period_end=None,
        created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S"),
    )
    line = rr.format_line()
    assert "rel=" not in line


# ---------------------------------------------------------------------------
# 9. provider fetch_retention raises without credentials
# ---------------------------------------------------------------------------


def test_fetch_retention_raises_without_token():
    """fetch_retention() must raise ProviderAdapterError without an access token."""
    from app.analytics.errors import ProviderAdapterError
    from app.analytics.providers.youtube import YouTubeAnalyticsProvider

    p = YouTubeAnalyticsProvider("")
    with pytest.raises(ProviderAdapterError, match="access_token"):
        p.fetch_retention("vid", period_start="2026-01-01", period_end="2026-08-17")


def test_fetch_retention_calls_service_with_correct_params():
    """fetch_retention() delegates to fetch_retention_from_service with correct args."""
    from app.analytics.providers.youtube import YouTubeAnalyticsProvider

    fake_service = _make_fake_retention_service(
        rows=[[0.0, 1.0, 1.0]],
        column_names=[
            "elapsedVideoTimeRatio",
            "audienceWatchRatio",
            "relativeRetentionPerformance",
        ],
    )
    p = YouTubeAnalyticsProvider("tok", api_service_override=fake_service)
    result = p.fetch_retention("kQH88nXdiRY", period_start="2026-01-01", period_end="2026-08-17")
    assert isinstance(result, list)
    assert len(result) == 1
    assert "elapsedVideoTimeRatio" in result[0]


# ---------------------------------------------------------------------------
# 10. Phase A scalar metrics unchanged by retention addition
# ---------------------------------------------------------------------------


def test_phase_a_scalar_metrics_still_complete():
    """Phase A scalar metrics must be unchanged after retention module introduction."""
    from app.analytics.providers.youtube import _NON_MONETARY_REQUEST_METRICS

    expected = {
        "engagedViews",
        "views",
        "estimatedMinutesWatched",
        "averageViewDuration",
        "averageViewPercentage",
        "likes",
        "comments",
        "shares",
        "subscribersGained",
        "subscribersLost",
    }
    actual = set(_NON_MONETARY_REQUEST_METRICS)
    assert expected == actual, f"Phase A metrics changed: {expected.symmetric_difference(actual)}"


def test_retention_not_in_scalar_metrics():
    """'audienceWatchRatio' must NOT appear in the scalar metrics request."""
    from app.analytics.providers.youtube import (
        _MONETARY_REQUEST_METRICS,
        _NON_MONETARY_REQUEST_METRICS,
    )

    assert "audienceWatchRatio" not in _NON_MONETARY_REQUEST_METRICS
    assert "audienceWatchRatio" not in _MONETARY_REQUEST_METRICS
    assert "elapsedVideoTimeRatio" not in _NON_MONETARY_REQUEST_METRICS
