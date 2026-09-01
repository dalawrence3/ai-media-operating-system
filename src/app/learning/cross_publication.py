"""Phase 12C — Cross-Publication Learning Foundation.

Answers: "Across publications from the same channel, how have different
structured content characteristics been associated with outcomes?"

Responsibilities:
  - Aggregate content_feature_snapshots + analytics_aggregates per channel.
  - Compute channel-scoped performance baselines (one per metric).
  - Compute feature × bucket → metric association observations.
  - Classify evidence maturity (insufficient / exploratory / directional / actionable).
  - Expose exploration coverage so Phase 14 can identify undertested hypotheses.

Design invariants:
  - OBSERVATIONAL ONLY. Results describe associations, not causal effects.
    Every persisted row carries observation_type='association'.
  - Each publication contributes exactly once per metric, via its lifetime
    aggregate only.  Daily/weekly/monthly rows are never read here.
  - Seeds excluded: input_hash NOT LIKE 'seed-%' on analytics_aggregates.
  - Zero is preserved: a publication with observed 0 views is different from
    a publication with no analytics row.  Missing analytics → publication
    excluded from that metric's comparison.  NULL is never substituted for
    absent data.
  - Channel-scoped: comparisons never cross channel_id boundaries.
  - Age normalization: NOT implemented in V1. average_view_duration and
    average_view_percentage (ratio metrics) are less age-biased than raw
    view counts and are preferred for cross-publication comparison.
  - No optimization applied. Phase 12A (application framework) remains the
    controlled mechanism for changing production parameters.

Persistence model:
  - channel_performance_baselines: one row per (channel_id, metric_name).
    Upserted when evidence changes (input_hash changes).
  - feature_performance_observations: one row per
    (channel_id, feature_name, feature_bucket, metric_name).
    Upserted when evidence changes.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel

# ── Version constants ─────────────────────────────────────────────────────────

CROSS_PUB_SCHEMA_VERSION: str = "cross-pub-v1"
CROSS_PUB_OBSERVER_VERSION: str = "observer-v1"

# ── Evidence maturity classification ─────────────────────────────────────────

MATURITY_INSUFFICIENT = "insufficient"  # n < 2:  no cross-publication inference possible
MATURITY_EXPLORATORY = "exploratory"  # 2–3:    directionally suggestive only
MATURITY_DIRECTIONAL = "directional"  # 4–9:    pattern visible but not robust
MATURITY_ACTIONABLE = "actionable"  # >= 10:  sufficient for systematic consideration

# Semantic note: 'actionable' here means "enough evidence to consider the pattern".
# It does NOT mean "apply this change automatically" — that remains Phase 12A's domain.


def _classify_maturity(n: int) -> str:
    if n < 2:
        return MATURITY_INSUFFICIENT
    if n < 4:
        return MATURITY_EXPLORATORY
    if n < 10:
        return MATURITY_DIRECTIONAL
    return MATURITY_ACTIONABLE


# ── V1 comparable feature definitions ────────────────────────────────────────
# Each entry maps a content_feature_snapshots column name to its bucketing rule.
#
# Numeric features: value is floor-bucketed into bands of the given width.
#   bucket label = "{floor:.4g}–{floor+width:.4g}"
# Categorical features: value used as-is as the bucket label (string/int → str).
# Boolean features: 0/1 integer → "false"/"true" label.

NUMERIC_FEATURES: dict[str, float] = {
    "narration_speaking_rate": 0.1,
    "script_word_count": 50.0,
    "narration_actual_duration_s": 15.0,
    "scene_count": 3.0,
    "publish_hour_utc": 6.0,
    "words_per_second": 0.5,
    "scenes_per_minute": 2.0,
    "avg_scene_duration_ms": 1000.0,
    "caption_cues_per_second": 0.25,
    "hook_word_count": 10.0,
    # ── Realized visual composition (Phase 18E) ──────────────────────────────
    # Bucket widths are deliberately coarse. With single-digit publications per
    # channel, a narrow bucket produces one observation per bucket and an
    # `insufficient` maturity for all of them — precision that describes
    # nothing. 0.1 on a runtime fraction means "roughly a tenth of the video",
    # which is the finest distinction this sample size can support.
    "visual_meaningful_runtime_pct": 0.1,
    "visual_text_card_runtime_pct": 0.1,
    "visual_generated_diagram_runtime_pct": 0.1,
    "visual_retrieved_imagery_runtime_pct": 0.1,
    "visual_changes_per_minute": 2.0,
    "visual_max_meaningful_gap_s": 5.0,
    "visual_distinct_assets": 3.0,
    "visual_asset_reuse_ratio": 0.1,
    "visual_provider_fallback_rate": 0.1,
}

CATEGORICAL_FEATURES: frozenset[str] = frozenset(
    {
        "script_format",
        "narration_provider",
        "narration_voice_id",
        "narration_language",
        "publish_visibility",
        "publish_day_of_week",
        "scene_dominant_shot_type",
        "scene_dominant_transition",
        # Phase 18E. visual_style is the PLANNED treatment; the other two are
        # what actually came out. Keeping them separate is what lets the
        # learner tell "minimalist performs well here" apart from "retrieval
        # was broken that week".
        "visual_style",
        "visual_dominant_family",
        "visual_quality_status",
    }
)

BOOLEAN_FEATURES: frozenset[str] = frozenset(
    {
        "has_hook",
        "has_cta",
        "scene_has_ai_generated_assets",
        "render_caption_burn_in",
        "learning_application_used",
        "publish_made_for_kids",
        "visual_opening_meaningful",
    }
)

ALL_COMPARABLE_FEATURES: frozenset[str] = (
    frozenset(NUMERIC_FEATURES.keys()) | CATEGORICAL_FEATURES | BOOLEAN_FEATURES
)


# ── Feature provenance (Phase 18E) ───────────────────────────────────────────
# What a correlation on a feature is even allowed to imply depends on who
# chose its value. These sets are documentation with teeth: a planner may only
# act on a feature it can actually set.

PLANNER_CONTROLLED_FEATURES: frozenset[str] = frozenset(
    {
        "narration_speaking_rate",
        "has_hook",
        "has_cta",
        "script_format",
        "narration_voice_id",
        "publish_day_of_week",
        "render_caption_burn_in",
        "visual_style",
    }
)

# Emergent properties of the finished video. A planner cannot set these
# directly; it influences them through the treatment it chooses.
REALIZED_PRODUCTION_FEATURES: frozenset[str] = frozenset(
    {
        "visual_meaningful_runtime_pct",
        "visual_text_card_runtime_pct",
        "visual_generated_diagram_runtime_pct",
        "visual_retrieved_imagery_runtime_pct",
        "visual_changes_per_minute",
        "visual_max_meaningful_gap_s",
        "visual_distinct_assets",
        "visual_asset_reuse_ratio",
        "visual_dominant_family",
        "visual_opening_meaningful",
    }
)

# Properties of the infrastructure, not of the content. A correlation here is
# evidence about the pipeline's health, never about audience preference, and
# must never be fed back as a creative instruction.
PROVIDER_RELIABILITY_FEATURES: frozenset[str] = frozenset(
    {
        "visual_provider_fallback_rate",
        "visual_quality_status",
    }
)


# Analytics metrics used as comparison outcomes.
# Excluded: revenue_estimate (currency complexity), ctr (often suppressed),
# impressions (Reporting API only, not always present).
# All other CANONICAL_METRICS are included; missing rows simply exclude
# that publication from the metric-specific comparison.
OUTCOME_METRICS: frozenset[str] = frozenset(
    {
        "views",
        "engaged_views",
        "watch_time_seconds",
        "average_view_duration",
        "average_view_percentage",
        "likes",
        "dislikes",
        "comments",
        "shares",
        "subscribers_gained",
        "subscribers_lost",
    }
)


# ── Bucketing ─────────────────────────────────────────────────────────────────


def _numeric_bucket(value: float, width: float) -> str:
    """Return a deterministic bucket label for a numeric value.

    Floor-based: 1.05 with width=0.1 → bucket "1–1.1".
    Stable across runs: same value + width → same label.
    """
    floor_val = math.floor(value / width) * width
    return f"{floor_val:.4g}–{floor_val + width:.4g}"


def _boolean_bucket(value: object) -> str:
    """Normalize 0/1/True/False/None to 'true'/'false'/'unknown'."""
    if value is None:
        return "unknown"
    return "true" if int(value) else "false"


def feature_bucket(feature_name: str, value: object) -> str | None:
    """Return the bucket label for (feature_name, value), or None if unbucketable.

    Returns None for NULL values (NULL is never substituted for absent data).
    """
    if value is None:
        return None
    if feature_name in NUMERIC_FEATURES:
        try:
            return _numeric_bucket(float(value), NUMERIC_FEATURES[feature_name])
        except (TypeError, ValueError):
            return None
    if feature_name in BOOLEAN_FEATURES:
        return _boolean_bucket(value)
    if feature_name in CATEGORICAL_FEATURES:
        return str(value)
    return None


# ── Statistics ────────────────────────────────────────────────────────────────


def _compute_stats(values: list[float]) -> dict[str, object]:
    """Compute descriptive statistics for a non-empty list of floats.

    std_dev is None when n < 2 (cannot compute sample std dev with one value).
    """
    n = len(values)
    if n == 0:
        return {
            "publication_count": 0,
            "mean": None,
            "median": None,
            "min_value": None,
            "max_value": None,
            "std_dev": None,
        }
    total = sum(values)
    mean = total / n
    sorted_v = sorted(values)
    if n % 2 == 1:
        median = sorted_v[n // 2]
    else:
        median = (sorted_v[n // 2 - 1] + sorted_v[n // 2]) / 2.0
    std_dev: float | None = None
    if n >= 2:
        variance = sum((v - mean) ** 2 for v in values) / (n - 1)
        std_dev = math.sqrt(variance)
    return {
        "publication_count": n,
        "mean": mean,
        "median": median,
        "min_value": sorted_v[0],
        "max_value": sorted_v[-1],
        "std_dev": std_dev,
    }


# ── Hashing ───────────────────────────────────────────────────────────────────


def _stable_json(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _baseline_hash(
    *,
    channel_id: str,
    metric_name: str,
    period_type: str,
    source_publication_ids: list[int],
    schema_version: str,
    observer_version: str,
) -> str:
    payload = _stable_json(
        {
            "channel_id": channel_id,
            "metric_name": metric_name,
            "period_type": period_type,
            "source_publication_ids": sorted(source_publication_ids),
            "schema_version": schema_version,
            "observer_version": observer_version,
        }
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _observation_hash(
    *,
    channel_id: str,
    feature_name: str,
    feature_bucket: str,
    metric_name: str,
    period_type: str,
    source_publication_ids: list[int],
    schema_version: str,
    observer_version: str,
) -> str:
    payload = _stable_json(
        {
            "channel_id": channel_id,
            "feature_name": feature_name,
            "feature_bucket": feature_bucket,
            "metric_name": metric_name,
            "period_type": period_type,
            "source_publication_ids": sorted(source_publication_ids),
            "schema_version": schema_version,
            "observer_version": observer_version,
        }
    )
    return hashlib.sha256(payload.encode()).hexdigest()


# ── Domain models ─────────────────────────────────────────────────────────────


class ChannelPerformanceBaseline(BaseModel):
    """Frozen projection of a channel_performance_baselines row."""

    model_config = {"frozen": True}

    id: int
    channel_id: str
    workspace_id: str | None
    metric_name: str
    period_type: str
    publication_count: int
    mean: float | None
    median: float | None
    min_value: float | None
    max_value: float | None
    std_dev: float | None
    sample_maturity: str
    source_publication_ids_json: str
    source_snapshot_ids_json: str
    comparison_schema_version: str
    observer_version: str
    input_hash: str
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> ChannelPerformanceBaseline:
        return cls(**dict(row))

    @property
    def source_publication_ids(self) -> list[int]:
        try:
            return json.loads(self.source_publication_ids_json)
        except (json.JSONDecodeError, TypeError):
            return []

    @property
    def source_snapshot_ids(self) -> list[int]:
        try:
            return json.loads(self.source_snapshot_ids_json)
        except (json.JSONDecodeError, TypeError):
            return []


class FeaturePerformanceObservation(BaseModel):
    """Frozen projection of a feature_performance_observations row.

    Semantics: "In this channel, publications where {feature_name} fell into
    bucket '{feature_bucket}' had {observation_type} with outcome metric
    {metric_name}: mean={mean}, median={median} (n={publication_count})."

    observation_type is always 'association' — this is NEVER a causal claim.
    """

    model_config = {"frozen": True}

    id: int
    channel_id: str
    workspace_id: str | None
    feature_name: str
    feature_bucket: str
    metric_name: str
    period_type: str
    publication_count: int
    mean: float | None
    median: float | None
    min_value: float | None
    max_value: float | None
    std_dev: float | None
    baseline_mean: float | None
    baseline_median: float | None
    abs_diff_from_baseline: float | None
    rel_diff_from_baseline: float | None
    sample_maturity: str
    observation_type: str
    source_publication_ids_json: str
    source_snapshot_ids_json: str
    comparison_schema_version: str
    observer_version: str
    input_hash: str
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> FeaturePerformanceObservation:
        return cls(**dict(row))

    @property
    def source_publication_ids(self) -> list[int]:
        try:
            return json.loads(self.source_publication_ids_json)
        except (json.JSONDecodeError, TypeError):
            return []

    @property
    def source_snapshot_ids(self) -> list[int]:
        try:
            return json.loads(self.source_snapshot_ids_json)
        except (json.JSONDecodeError, TypeError):
            return []


@dataclass
class CrossPublicationResult:
    """Summary of one cross-publication learning run for a channel."""

    channel_id: str
    workspace_id: str | None
    publication_count: int
    metrics_with_data: list[str]
    baselines_computed: int
    observations_computed: int
    schema_version: str
    observer_version: str
    computed_at: str


# ── DB helpers ────────────────────────────────────────────────────────────────


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _get_channel_publication_snapshots(
    conn: sqlite3.Connection,
    channel_id: str,
) -> list[dict]:
    """Return all feature snapshots for a channel, as plain dicts."""
    rows = conn.execute(
        """
        SELECT publication_id, id AS snapshot_id, topic_id, workspace_id, channel_id
          FROM content_feature_snapshots
         WHERE channel_id = ?
         ORDER BY publication_id ASC
        """,
        (channel_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _get_feature_values_for_channel(
    conn: sqlite3.Connection,
    channel_id: str,
    features: frozenset[str],
) -> dict[int, dict[str, object]]:
    """Return {pub_id: {feature_name: value}} for all requested features.

    Only publications belonging to this channel are included.
    NULL column values are included as-is (not coerced to 0 or excluded).
    """
    if not features:
        return {}
    cols = ", ".join(f'"{f}"' for f in sorted(features))
    rows = conn.execute(
        f"SELECT publication_id, {cols} FROM content_feature_snapshots WHERE channel_id = ?",
        (channel_id,),
    ).fetchall()
    result: dict[int, dict[str, object]] = {}
    for row in rows:
        pub_id = row["publication_id"]
        result[pub_id] = {f: row[f] for f in features}
    return result


def _get_lifetime_metrics(
    conn: sqlite3.Connection,
    pub_ids: list[int],
) -> dict[int, dict[str, tuple[float, list[int]]]]:
    """Return {pub_id: {metric_name: (value, [snapshot_ids])}} for lifetime non-seed rows.

    A publication absent from a metric's results has no valid analytics for that
    metric.  This is DIFFERENT from having observed zero — callers must NOT
    substitute 0 for absent entries.

    Only OUTCOME_METRICS are returned; internal metrics (e.g. revenue_estimate)
    are included if present.

    Seed rows (input_hash LIKE 'seed-%') are excluded — see maturity.py.
    """
    if not pub_ids:
        return {}
    placeholders = ",".join("?" * len(pub_ids))
    rows = conn.execute(
        f"""
        SELECT publication_id, metric_name, metric_value, source_snapshot_ids_json
          FROM analytics_aggregates
         WHERE publication_id IN ({placeholders})
           AND period_type = 'lifetime'
           AND input_hash NOT LIKE 'seed-%'
        """,
        pub_ids,
    ).fetchall()
    result: dict[int, dict[str, tuple[float, list[int]]]] = {}
    for row in rows:
        pub_id = row["publication_id"]
        metric = row["metric_name"]
        if metric not in OUTCOME_METRICS:
            continue
        value = float(row["metric_value"])
        try:
            snap_ids: list[int] = json.loads(row["source_snapshot_ids_json"]) or []
        except (json.JSONDecodeError, TypeError):
            snap_ids = []
        result.setdefault(pub_id, {})[metric] = (value, snap_ids)
    return result


def _upsert_channel_baseline(
    conn: sqlite3.Connection,
    *,
    channel_id: str,
    workspace_id: str | None,
    metric_name: str,
    period_type: str,
    stats: dict[str, object],
    source_publication_ids: list[int],
    source_snapshot_ids: list[int],
    input_hash: str,
) -> ChannelPerformanceBaseline:
    now = _now()
    pub_ids_json = json.dumps(sorted(source_publication_ids))
    snap_ids_json = json.dumps(sorted(source_snapshot_ids))
    n = int(stats.get("publication_count", 0))
    conn.execute(
        """
        INSERT INTO channel_performance_baselines (
            channel_id, workspace_id, metric_name, period_type,
            publication_count, mean, median, min_value, max_value, std_dev,
            sample_maturity,
            source_publication_ids_json, source_snapshot_ids_json,
            comparison_schema_version, observer_version, input_hash,
            created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(channel_id, metric_name)
        DO UPDATE SET
            workspace_id                 = excluded.workspace_id,
            period_type                  = excluded.period_type,
            publication_count            = excluded.publication_count,
            mean                         = excluded.mean,
            median                       = excluded.median,
            min_value                    = excluded.min_value,
            max_value                    = excluded.max_value,
            std_dev                      = excluded.std_dev,
            sample_maturity              = excluded.sample_maturity,
            source_publication_ids_json  = excluded.source_publication_ids_json,
            source_snapshot_ids_json     = excluded.source_snapshot_ids_json,
            comparison_schema_version    = excluded.comparison_schema_version,
            observer_version             = excluded.observer_version,
            input_hash                   = excluded.input_hash,
            updated_at                   = excluded.updated_at
        """,
        (
            channel_id,
            workspace_id,
            metric_name,
            period_type,
            n,
            stats.get("mean"),
            stats.get("median"),
            stats.get("min_value"),
            stats.get("max_value"),
            stats.get("std_dev"),
            _classify_maturity(n),
            pub_ids_json,
            snap_ids_json,
            CROSS_PUB_SCHEMA_VERSION,
            CROSS_PUB_OBSERVER_VERSION,
            input_hash,
            now,
            now,
        ),
    )
    row = conn.execute(
        "SELECT * FROM channel_performance_baselines WHERE channel_id = ? AND metric_name = ?",
        (channel_id, metric_name),
    ).fetchone()
    return ChannelPerformanceBaseline.from_row(row)  # type: ignore[arg-type]


def _upsert_feature_observation(
    conn: sqlite3.Connection,
    *,
    channel_id: str,
    workspace_id: str | None,
    feature_name: str,
    bucket: str,
    metric_name: str,
    period_type: str,
    stats: dict[str, object],
    baseline_mean: float | None,
    baseline_median: float | None,
    source_publication_ids: list[int],
    source_snapshot_ids: list[int],
    input_hash: str,
) -> FeaturePerformanceObservation:
    now = _now()
    mean = stats.get("mean")
    n = int(stats.get("publication_count", 0))
    abs_diff: float | None = None
    rel_diff: float | None = None
    if mean is not None and baseline_mean is not None:
        abs_diff = mean - baseline_mean
        if baseline_mean != 0.0:
            rel_diff = abs_diff / baseline_mean
    pub_ids_json = json.dumps(sorted(source_publication_ids))
    snap_ids_json = json.dumps(sorted(source_snapshot_ids))
    conn.execute(
        """
        INSERT INTO feature_performance_observations (
            channel_id, workspace_id, feature_name, feature_bucket,
            metric_name, period_type,
            publication_count, mean, median, min_value, max_value, std_dev,
            baseline_mean, baseline_median,
            abs_diff_from_baseline, rel_diff_from_baseline,
            sample_maturity, observation_type,
            source_publication_ids_json, source_snapshot_ids_json,
            comparison_schema_version, observer_version, input_hash,
            created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(channel_id, feature_name, feature_bucket, metric_name)
        DO UPDATE SET
            workspace_id                 = excluded.workspace_id,
            period_type                  = excluded.period_type,
            publication_count            = excluded.publication_count,
            mean                         = excluded.mean,
            median                       = excluded.median,
            min_value                    = excluded.min_value,
            max_value                    = excluded.max_value,
            std_dev                      = excluded.std_dev,
            baseline_mean                = excluded.baseline_mean,
            baseline_median              = excluded.baseline_median,
            abs_diff_from_baseline       = excluded.abs_diff_from_baseline,
            rel_diff_from_baseline       = excluded.rel_diff_from_baseline,
            sample_maturity              = excluded.sample_maturity,
            observation_type             = excluded.observation_type,
            source_publication_ids_json  = excluded.source_publication_ids_json,
            source_snapshot_ids_json     = excluded.source_snapshot_ids_json,
            comparison_schema_version    = excluded.comparison_schema_version,
            observer_version             = excluded.observer_version,
            input_hash                   = excluded.input_hash,
            updated_at                   = excluded.updated_at
        """,
        (
            channel_id,
            workspace_id,
            feature_name,
            bucket,
            metric_name,
            period_type,
            n,
            mean,
            stats.get("median"),
            stats.get("min_value"),
            stats.get("max_value"),
            stats.get("std_dev"),
            baseline_mean,
            baseline_median,
            abs_diff,
            rel_diff,
            _classify_maturity(n),
            "association",
            pub_ids_json,
            snap_ids_json,
            CROSS_PUB_SCHEMA_VERSION,
            CROSS_PUB_OBSERVER_VERSION,
            input_hash,
            now,
            now,
        ),
    )
    row = conn.execute(
        """
        SELECT * FROM feature_performance_observations
         WHERE channel_id = ? AND feature_name = ? AND feature_bucket = ? AND metric_name = ?
        """,
        (channel_id, feature_name, bucket, metric_name),
    ).fetchone()
    return FeaturePerformanceObservation.from_row(row)  # type: ignore[arg-type]


# ── Public API ────────────────────────────────────────────────────────────────


def run_cross_publication_learning(
    conn: sqlite3.Connection,
    *,
    channel_id: str,
    workspace_id: str | None = None,
) -> CrossPublicationResult:
    """Compute and persist cross-publication baselines and feature observations.

    This is the primary entry point for Phase 12C.  Call it after new publications
    have been feature-extracted and their analytics aggregated.  It is idempotent:
    calling it again with the same evidence produces the same result.

    Cross-channel isolation: only publications in channel_id contribute.
    Workspace isolation: workspace_id is recorded for provenance but channel_id
    is the primary scope key.

    Phase 13 / Phase 14 compatibility: results are queryable directly via
    get_channel_baselines(), get_feature_observations(), and
    get_exploration_coverage() — no recomputation needed.

    IMPORTANT: This function is read-only on analytics tables.  It only writes
    to channel_performance_baselines and feature_performance_observations.
    """
    now = _now()

    # Step 1: Fetch all publications for this channel that have feature snapshots.
    pub_snapshots = _get_channel_publication_snapshots(conn, channel_id)
    pub_ids = [p["publication_id"] for p in pub_snapshots]
    pub_count = len(pub_ids)

    # Step 2: Fetch all feature values for comparable features.
    feature_values = (
        _get_feature_values_for_channel(conn, channel_id, ALL_COMPARABLE_FEATURES)
        if pub_ids
        else {}
    )

    # Step 3: Fetch all lifetime non-seed analytics metrics for these publications.
    lifetime_metrics = _get_lifetime_metrics(conn, pub_ids)

    # Step 4: Compute channel baselines (one per metric, over all pubs with that metric).
    metrics_with_data: list[str] = []
    baselines: dict[str, ChannelPerformanceBaseline] = {}
    baselines_computed = 0

    # Group values and snapshot IDs per metric.
    metric_data: dict[str, tuple[list[float], list[int], list[int]]] = {}
    for pub_id in pub_ids:
        for metric_name, (value, snap_ids) in lifetime_metrics.get(pub_id, {}).items():
            entry = metric_data.setdefault(metric_name, ([], [], []))
            entry[0].append(value)
            entry[1].append(pub_id)
            entry[2].extend(snap_ids)

    for metric_name, (values, src_pub_ids, src_snap_ids) in metric_data.items():
        if not values:
            continue
        metrics_with_data.append(metric_name)
        stats = _compute_stats(values)
        h = _baseline_hash(
            channel_id=channel_id,
            metric_name=metric_name,
            period_type="lifetime",
            source_publication_ids=src_pub_ids,
            schema_version=CROSS_PUB_SCHEMA_VERSION,
            observer_version=CROSS_PUB_OBSERVER_VERSION,
        )
        baseline = _upsert_channel_baseline(
            conn,
            channel_id=channel_id,
            workspace_id=workspace_id,
            metric_name=metric_name,
            period_type="lifetime",
            stats=stats,
            source_publication_ids=src_pub_ids,
            source_snapshot_ids=list(set(src_snap_ids)),
            input_hash=h,
        )
        baselines[metric_name] = baseline
        baselines_computed += 1

    # Step 5: Compute feature × bucket → metric observations.
    observations_computed = 0

    for feat_name in ALL_COMPARABLE_FEATURES:
        # Build {bucket: [pub_ids]} grouping for this feature.
        bucket_pubs: dict[str, list[int]] = {}
        for pub_id in pub_ids:
            fv = feature_values.get(pub_id, {})
            raw_val = fv.get(feat_name)
            bkt = feature_bucket(feat_name, raw_val)
            if bkt is None:
                continue
            bucket_pubs.setdefault(bkt, []).append(pub_id)

        for bkt, bkt_pub_ids in bucket_pubs.items():
            # For each metric: collect values only from pubs in this bucket.
            metric_bucket_data: dict[str, tuple[list[float], list[int]]] = {}
            for pub_id in bkt_pub_ids:
                for metric_name, (value, snap_ids) in lifetime_metrics.get(pub_id, {}).items():
                    entry = metric_bucket_data.setdefault(metric_name, ([], []))
                    entry[0].append(value)
                    entry[1].extend(snap_ids)

            for metric_name, (values, snap_ids) in metric_bucket_data.items():
                if not values:
                    continue
                stats = _compute_stats(values)
                baseline = baselines.get(metric_name)
                h = _observation_hash(
                    channel_id=channel_id,
                    feature_name=feat_name,
                    feature_bucket=bkt,
                    metric_name=metric_name,
                    period_type="lifetime",
                    source_publication_ids=bkt_pub_ids,
                    schema_version=CROSS_PUB_SCHEMA_VERSION,
                    observer_version=CROSS_PUB_OBSERVER_VERSION,
                )
                _upsert_feature_observation(
                    conn,
                    channel_id=channel_id,
                    workspace_id=workspace_id,
                    feature_name=feat_name,
                    bucket=bkt,
                    metric_name=metric_name,
                    period_type="lifetime",
                    stats=stats,
                    baseline_mean=baseline.mean if baseline else None,
                    baseline_median=baseline.median if baseline else None,
                    source_publication_ids=bkt_pub_ids,
                    source_snapshot_ids=list(set(snap_ids)),
                    input_hash=h,
                )
                observations_computed += 1

    conn.commit()

    return CrossPublicationResult(
        channel_id=channel_id,
        workspace_id=workspace_id,
        publication_count=pub_count,
        metrics_with_data=sorted(set(metrics_with_data)),
        baselines_computed=baselines_computed,
        observations_computed=observations_computed,
        schema_version=CROSS_PUB_SCHEMA_VERSION,
        observer_version=CROSS_PUB_OBSERVER_VERSION,
        computed_at=now,
    )


# ── Query helpers ─────────────────────────────────────────────────────────────


def get_channel_baselines(
    conn: sqlite3.Connection,
    *,
    channel_id: str,
    metric_name: str | None = None,
) -> list[ChannelPerformanceBaseline]:
    """Return persisted baselines for a channel, optionally filtered by metric."""
    clauses = ["channel_id = ?"]
    params: list[object] = [channel_id]
    if metric_name is not None:
        clauses.append("metric_name = ?")
        params.append(metric_name)
    where = "WHERE " + " AND ".join(clauses)
    rows = conn.execute(
        f"SELECT * FROM channel_performance_baselines {where} ORDER BY metric_name",
        params,
    ).fetchall()
    return [ChannelPerformanceBaseline.from_row(r) for r in rows]


def get_feature_observations(
    conn: sqlite3.Connection,
    *,
    channel_id: str,
    feature_name: str | None = None,
    metric_name: str | None = None,
) -> list[FeaturePerformanceObservation]:
    """Return persisted feature observations for a channel."""
    clauses = ["channel_id = ?"]
    params: list[object] = [channel_id]
    if feature_name is not None:
        clauses.append("feature_name = ?")
        params.append(feature_name)
    if metric_name is not None:
        clauses.append("metric_name = ?")
        params.append(metric_name)
    where = "WHERE " + " AND ".join(clauses)
    rows = conn.execute(
        f"""
        SELECT * FROM feature_performance_observations
        {where}
        ORDER BY feature_name, feature_bucket, metric_name
        """,
        params,
    ).fetchall()
    return [FeaturePerformanceObservation.from_row(r) for r in rows]


def get_exploration_coverage(
    conn: sqlite3.Connection,
    *,
    channel_id: str,
    feature_name: str | None = None,
) -> dict[str, dict[str, dict[str, object]]]:
    """Return exploration coverage for a channel.

    Returns:
        {feature_name: {bucket: {publication_count, sample_maturity, pub_ids}}}

    This powers Phase 14's experiment planner: it can identify feature values
    that have been tested, how many times, and how mature the evidence is.
    Untested feature values (absent from this dict) are underexplored.
    """
    obs = get_feature_observations(conn, channel_id=channel_id, feature_name=feature_name)

    coverage: dict[str, dict[str, dict[str, object]]] = {}
    seen: set[tuple[str, str]] = set()

    for ob in obs:
        key = (ob.feature_name, ob.feature_bucket)
        if key in seen:
            continue
        seen.add(key)
        feat_entry = coverage.setdefault(ob.feature_name, {})
        feat_entry[ob.feature_bucket] = {
            "publication_count": ob.publication_count,
            "sample_maturity": ob.sample_maturity,
            "source_publication_ids": ob.source_publication_ids,
        }

    return coverage
