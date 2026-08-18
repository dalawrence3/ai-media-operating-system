"""Audience retention curve ingestion, scene attribution, and persistence.

YouTube Analytics API query
---------------------------
Retention is fetched via a dimensional query (NOT the scalar user-activity query):

    GET youtubeanalytics.googleapis.com/v2/reports
    ids=channel==MINE
    dimensions=elapsedVideoTimeRatio
    metrics=audienceWatchRatio,relativeRetentionPerformance
    filters=video==<provider_video_id>
    startDate=YYYY-MM-DD
    endDate=YYYY-MM-DD

The API returns one row per elapsedVideoTimeRatio bucket (typically 0.00, 0.01, …,
1.00 — 101 points for a short video).  relativeRetentionPerformance may be absent
for videos with insufficient viewership data.

This module is the ONLY place that issues the retention-dimensional API call.
It does NOT call the scalar user-activity query and does NOT write to
analytics_metrics.  Retention series live in analytics_retention_points.

Scene attribution
-----------------
Each retention point is mapped to the scene whose [start_ms, end_ms) window
contains the point's elapsed milliseconds.  Scene metadata (scene_index,
section_type) is derived from scene_manifest_scenes + production_segments.
The total video duration is derived from MAX(end_ms) over the manifest's scenes.

No video content or narration text is duplicated here — only integer IDs and
section_type strings are stored for attribution.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


# ── Domain models ─────────────────────────────────────────────────────────────


@dataclass
class RetentionPoint:
    """One bucket of the audience retention curve."""

    elapsed_ratio: float
    audience_watch_ratio: float
    relative_retention: float | None = None

    # Derived time values — populated after duration is known
    elapsed_ms: int | None = None
    elapsed_seconds: float | None = None

    # Scene attribution — populated after attribution step
    scene_index: int | None = None
    section_type: str | None = None


@dataclass
class SceneEntry:
    """Minimal scene row needed for attribution."""

    scene_index: int
    start_ms: int
    end_ms: int
    section_type: str  # from production_segments via segment_id


@dataclass
class RetentionCurve:
    """Full retention result for one publication/video in one period."""

    provider_video_id: str
    scene_manifest_id: int
    video_duration_ms: int | None
    period_start: str | None
    period_end: str | None
    points: list[RetentionPoint] = field(default_factory=list)


# ── Provider-side fetch (YouTube adapter) ────────────────────────────────────


def fetch_retention_from_service(
    service: object,
    provider_video_id: str,
    *,
    period_start: str,
    period_end: str,
) -> list[dict[str, object]]:
    """Call the YouTube Analytics API dimensional retention query.

    Returns a list of raw row dicts with keys:
      elapsedVideoTimeRatio, audienceWatchRatio,
      relativeRetentionPerformance (may be absent).

    Raises ProviderAdapterError on any API failure.
    """
    from app.analytics.errors import ProviderAdapterError

    try:
        response = (
            service.reports()  # type: ignore[attr-defined]
            .query(
                ids="channel==MINE",
                startDate=period_start[:10],
                endDate=period_end[:10],
                dimensions="elapsedVideoTimeRatio",
                metrics="audienceWatchRatio,relativeRetentionPerformance",
                filters=f"video=={provider_video_id}",
            )
            .execute()
        )
    except Exception as exc:
        raise ProviderAdapterError(
            f"YouTube retention API call failed for video {provider_video_id!r}: {exc}"
        ) from exc

    column_headers: list[dict] = response.get("columnHeaders", [])
    col_names = [h["name"] for h in column_headers]
    rows: list[list] = response.get("rows", [])

    result: list[dict[str, object]] = []
    for row in rows:
        result.append(dict(zip(col_names, row, strict=False)))
    return result


def parse_retention_rows(raw_rows: list[dict[str, object]]) -> list[RetentionPoint]:
    """Convert raw API rows into RetentionPoint objects."""
    points: list[RetentionPoint] = []
    for row in raw_rows:
        elapsed = row.get("elapsedVideoTimeRatio")
        watch = row.get("audienceWatchRatio")
        if elapsed is None or watch is None:
            continue
        relative = row.get("relativeRetentionPerformance")
        points.append(
            RetentionPoint(
                elapsed_ratio=float(elapsed),
                audience_watch_ratio=float(watch),
                relative_retention=float(relative) if relative is not None else None,
            )
        )
    return points


# ── Scene attribution ────────────────────────────────────────────────────────


def load_scene_catalog(
    conn: sqlite3.Connection, scene_manifest_id: int
) -> tuple[list[SceneEntry], int | None]:
    """Load scenes for a manifest and compute total video duration.

    Returns (scenes_sorted_by_index, total_duration_ms).
    total_duration_ms is MAX(end_ms) across all scenes, or None if no scenes.
    """
    rows = conn.execute(
        """
        SELECT sms.scene_index, sms.start_ms, sms.end_ms,
               ps.section_type
        FROM scene_manifest_scenes sms
        JOIN production_segments ps ON ps.id = sms.segment_id
        WHERE sms.manifest_id = ?
        ORDER BY sms.scene_index
        """,
        (scene_manifest_id,),
    ).fetchall()

    if not rows:
        return [], None

    scenes = [
        SceneEntry(
            scene_index=r["scene_index"],
            start_ms=r["start_ms"],
            end_ms=r["end_ms"],
            section_type=r["section_type"],
        )
        for r in rows
    ]
    total_duration_ms = max(s.end_ms for s in scenes)
    return scenes, total_duration_ms


def attribute_scene(elapsed_ms: int, scenes: list[SceneEntry]) -> SceneEntry | None:
    """Return the scene whose [start_ms, end_ms) contains elapsed_ms.

    The last scene's end_ms boundary is inclusive to handle elapsed_ratio=1.0.
    Returns None if scenes is empty or elapsed_ms is out of range.
    """
    if not scenes:
        return None
    for scene in scenes:
        if scene.start_ms <= elapsed_ms < scene.end_ms:
            return scene
    # Handle the exactly-at-end case (elapsed_ratio == 1.0)
    last = scenes[-1]
    if elapsed_ms == last.end_ms:
        return last
    return None


def attribute_retention_curve(
    curve: RetentionCurve,
    scenes: list[SceneEntry],
) -> None:
    """Mutate each RetentionPoint in curve.points with elapsed_ms and scene attribution.

    Requires curve.video_duration_ms to be set.  If it is None, elapsed_ms is
    left as None and scene attribution is skipped.
    """
    duration_ms = curve.video_duration_ms
    for point in curve.points:
        if duration_ms is not None:
            ms = round(point.elapsed_ratio * duration_ms)
            point.elapsed_ms = ms
            point.elapsed_seconds = ms / 1000.0
            scene = attribute_scene(ms, scenes)
            if scene is not None:
                point.scene_index = scene.scene_index
                point.section_type = scene.section_type


# ── Persistence ───────────────────────────────────────────────────────────────


def persist_retention_curve(
    conn: sqlite3.Connection,
    curve: RetentionCurve,
    *,
    snapshot_id: int,
    publication_id: int,
) -> int:
    """Insert all retention points for a curve.  Returns the count inserted."""
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    count = 0
    for point in curve.points:
        conn.execute(
            """
            INSERT INTO analytics_retention_points (
                snapshot_id, publication_id, scene_manifest_id,
                elapsed_ratio, elapsed_ms, elapsed_seconds,
                audience_watch_ratio, relative_retention,
                scene_index, section_type,
                period_start, period_end, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                snapshot_id,
                publication_id,
                curve.scene_manifest_id,
                point.elapsed_ratio,
                point.elapsed_ms,
                point.elapsed_seconds,
                point.audience_watch_ratio,
                point.relative_retention,
                point.scene_index,
                point.section_type,
                curve.period_start,
                curve.period_end,
                now,
            ),
        )
        count += 1
    conn.commit()
    return count


# ── Read-back / inspection ────────────────────────────────────────────────────


@dataclass(frozen=True)
class RetentionRow:
    """One attributed retention point as read back from the DB."""

    id: int
    snapshot_id: int
    publication_id: int
    scene_manifest_id: int
    elapsed_ratio: float
    elapsed_ms: int | None
    elapsed_seconds: float | None
    audience_watch_ratio: float
    relative_retention: float | None
    scene_index: int | None
    section_type: str | None
    period_start: str | None
    period_end: str | None
    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> RetentionRow:
        return cls(**dict(row))

    def format_line(self) -> str:
        """Human-readable one-liner for CLI display."""
        elapsed = f"{self.elapsed_seconds:.1f}s" if self.elapsed_seconds is not None else "?s"
        scene = f"scene {self.scene_index}" if self.scene_index is not None else "no scene"
        section = self.section_type or "-"
        awr = f"{self.audience_watch_ratio:.4f}"
        rel = f"  rel={self.relative_retention:.4f}" if self.relative_retention is not None else ""
        return f"elapsed={elapsed:<8}  {scene:<8}  {section:<12}  awr={awr}{rel}"


def list_retention_for_snapshot(conn: sqlite3.Connection, snapshot_id: int) -> list[RetentionRow]:
    """Return all retention points for a snapshot, ordered by elapsed_ratio."""
    rows = conn.execute(
        "SELECT * FROM analytics_retention_points WHERE snapshot_id = ? ORDER BY elapsed_ratio",
        (snapshot_id,),
    ).fetchall()
    return [RetentionRow.from_row(r) for r in rows]


def list_retention_for_publication(
    conn: sqlite3.Connection,
    publication_id: int,
    *,
    snapshot_id: int | None = None,
) -> list[RetentionRow]:
    """Return retention points for a publication (newest snapshot first by default)."""
    if snapshot_id is not None:
        return list_retention_for_snapshot(conn, snapshot_id)
    rows = conn.execute(
        "SELECT * FROM analytics_retention_points WHERE publication_id = ? "
        "ORDER BY snapshot_id DESC, elapsed_ratio",
        (publication_id,),
    ).fetchall()
    return [RetentionRow.from_row(r) for r in rows]
