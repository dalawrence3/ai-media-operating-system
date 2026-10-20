"""Phase 18H.C — Topic-attribute observation stack tests.

Coverage:
A   attribute_bucket fixed-band boundaries
B   compute_topic_attribute_input_hash determinism / prompt-version sensitivity
C   save_topic_attribute_evaluation + get_topic_attributes round-trip
D   idempotent duplicate save (topic_id, input_hash) is a no-op
E   get_topic_attributes returns None for an uncharacterized topic
F   snapshot_semantic_fit_attributes_to_topic — forward path from a cached
    semantic-fit row with attribute columns
G   snapshot returns False (never raises) when no cached row / no columns exist
H   characterize_topic_directly — orphan path, zero-opportunity, cached
I   characterize_topic_directly — cache hit makes zero further provider calls
J   characterize_topic_directly — provider failure returns None, leaves topic
    untouched, no partial/fake row persisted
K   renewable-energy-shaped fixture: orphan topic + real publication end-to-end
L   list_topics_needing_attribute_characterization — orphan-only, skips
    already-characterized topics
M   run_topic_attribute_learning — MIN_VIEWS_FOR_LEARNING gate excludes
    low-exposure publications
N   run_topic_attribute_learning — zero provider calls (structural: no
    ai_provider parameter exists on the function at all)
O   tiny-N maturity classification n=1,2,3,4,10
P   cross-domain safeguard: same-domain evidence capped below directional
Q   cross-domain safeguard: >=2 domains permits normal maturity
R   raw vs decision-useful read models
S   no interaction/pairwise features are ever computed
"""

from __future__ import annotations

import json
import pathlib
import sqlite3

import pytest

from app.ai.fake import FakeProvider
from app.core.database import open_db
from app.learning.topic_attributes import (
    TOPIC_ATTRIBUTE_NAMES,
    TopicAttributeValues,
    attribute_bucket,
    characterize_topic_directly,
    compute_topic_attribute_input_hash,
    get_attribute_observations,
    get_decision_useful_attribute_coverage,
    get_topic_attributes,
    list_topics_needing_attribute_characterization,
    run_topic_attribute_learning,
    save_topic_attribute_evaluation,
    snapshot_semantic_fit_attributes_to_topic,
)

_NOW = "2026-09-05T00:00:00"
_CHANNEL = "ch-alpha"
_WORKSPACE = "ws-alpha"


# ── Fixture helpers ───────────────────────────────────────────────────────────


@pytest.fixture()
def conn(tmp_path: pathlib.Path) -> sqlite3.Connection:
    c = open_db(tmp_path / "test.db")
    c.execute("PRAGMA foreign_keys = OFF")
    yield c
    c.close()


def _insert_topic(
    conn: sqlite3.Connection,
    *,
    title: str,
    angle: str = "",
    promoted_opportunity_id: int | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO topics"
        " (title, angle, status, promoted_opportunity_id, created_at, updated_at)"
        " VALUES (?, ?, 'active', ?, ?, ?)",
        (title, angle, promoted_opportunity_id, _NOW, _NOW),
    )
    conn.commit()
    return cur.lastrowid


def _insert_semantic_fit_row_with_attributes(
    conn: sqlite3.Connection,
    *,
    opportunity_id: int,
    input_hash: str = "sf-hash-1",
    practical_utility: float | None = 0.8,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO opportunity_semantic_fit_results (
            opportunity_id, channel_id, prompt_version, input_hash, score, fit_label,
            rationale, provider_name, model, evaluated_at,
            practical_utility, immediate_payoff, curiosity, personal_relevance,
            surprise, broad_accessibility, niche_technicality, attribute_rationale
        ) VALUES (?,1,'3',?,0.85,'strong_fit','r','claude','claude-haiku-4-5-20251001',?,
                  ?,0.7,0.6,0.5,0.4,0.3,0.2,'rationale text')
        """,
        (opportunity_id, input_hash, _NOW, practical_utility),
    )
    conn.commit()
    return cur.lastrowid


def _insert_feature_snapshot(
    conn: sqlite3.Connection, *, publication_id: int, topic_id: int
) -> None:
    conn.execute(
        """
        INSERT INTO content_feature_snapshots (
            publication_id, topic_id, workspace_id, channel_id,
            feature_schema_version, extractor_version, input_hash, extracted_at, created_at,
            publishing_plan_id, production_plan_id, script_id,
            narration_run_id, caption_run_id, scene_manifest_id, render_manifest_id,
            voice_profile_id
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            publication_id,
            topic_id,
            _WORKSPACE,
            _CHANNEL,
            "features-v1",
            "extractor-v1",
            f"hash-{publication_id}",
            _NOW,
            _NOW,
            publication_id,
            publication_id,
            publication_id,
            publication_id,
            publication_id,
            publication_id,
            publication_id,
            1,
        ),
    )
    conn.commit()


def _insert_publishing_plan(
    conn: sqlite3.Connection, *, topic_id: int, plan_id: int | None = None
) -> None:
    """Minimal publishing_plans row so list_topics_needing_attribute_
    characterization's JOIN can find this topic as having a publication.
    FKs are off in this test DB, so the referenced ids need not exist."""
    pid = plan_id or topic_id
    conn.execute(
        """
        INSERT INTO publishing_plans (
            render_manifest_id, topic_id, production_plan_id, script_id,
            scene_manifest_id, narration_run_id, caption_run_id,
            input_hash, publishing_engine_version, metadata_version,
            provider, provider_version, title, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            pid,
            topic_id,
            pid,
            pid,
            pid,
            pid,
            pid,
            f"pp-hash-{pid}",
            "v1",
            "v1",
            "youtube",
            "v1",
            "title",
            _NOW,
            _NOW,
        ),
    )
    conn.commit()


def _insert_publication(
    conn: sqlite3.Connection, *, publication_id: int, topic_id: int, channel_id: str = _CHANNEL
) -> None:
    """Canonical publications -> publishing_plans -> topic lineage (closure
    audit Part 8 fix): run_topic_attribute_learning and
    get_domain_family_for_publication now discover (publication, topic)
    pairs and resolve domain_family through THIS lineage, not
    content_feature_snapshots — a publication can validly have zero
    feature-snapshot rows (confirmed live) and must still be visible to
    topic-attribute learning."""
    plan_cur = conn.execute(
        """
        INSERT INTO publishing_plans (
            render_manifest_id, topic_id, production_plan_id, script_id,
            scene_manifest_id, narration_run_id, caption_run_id,
            input_hash, publishing_engine_version, metadata_version,
            provider, provider_version, title, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            publication_id,
            topic_id,
            publication_id,
            publication_id,
            publication_id,
            publication_id,
            publication_id,
            f"pp-hash-pub-{publication_id}",
            "v1",
            "v1",
            "youtube",
            "v1",
            "title",
            _NOW,
            _NOW,
        ),
    )
    plan_id = plan_cur.lastrowid
    conn.execute(
        """
        INSERT INTO publications (
            id, publishing_plan_id, publishing_job_id, provider, provider_version,
            status, visibility, publishing_engine_version, input_hash, output_sha256,
            channel_id, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            publication_id,
            plan_id,
            publication_id,
            "youtube",
            "v1",
            "published",
            "public",
            "v1",
            f"pub-hash-{publication_id}",
            "sha",
            channel_id,
            _NOW,
            _NOW,
        ),
    )
    conn.commit()


def _insert_lifetime_metric(
    conn: sqlite3.Connection,
    *,
    publication_id: int,
    metric_name: str,
    value: float,
    topic_id: int = 1,
) -> None:
    conn.execute(
        """
        INSERT INTO analytics_aggregates (
            publication_id, topic_id, provider, period_type, period_key, metric_name,
            metric_value, snapshot_count, calculation_method, currency_code,
            source_snapshot_ids_json, input_hash, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            publication_id,
            topic_id,
            "youtube",
            "lifetime",
            "lifetime",
            metric_name,
            value,
            1,
            "sum",
            None,
            json.dumps([publication_id * 100]),
            f"agg-{publication_id}-{metric_name}",
            _NOW,
        ),
    )
    conn.commit()


def _values(practical_utility: float = 0.8, **overrides) -> TopicAttributeValues:
    base = dict(
        evaluated=True,
        practical_utility=practical_utility,
        immediate_payoff=0.7,
        curiosity=0.6,
        personal_relevance=0.5,
        surprise=0.4,
        broad_accessibility=0.3,
        niche_technicality=0.2,
        attribute_rationale="test rationale",
    )
    base.update(overrides)
    return TopicAttributeValues(**base)


_CHARACTERIZATION_JSON = json.dumps(
    {
        "practical_utility": 0.9,
        "immediate_payoff": 0.8,
        "curiosity": 0.7,
        "personal_relevance": 0.6,
        "surprise": 0.5,
        "broad_accessibility": 0.4,
        "niche_technicality": 0.3,
        "attribute_rationale": "orphan characterization rationale",
    }
)


# ── A: attribute_bucket ───────────────────────────────────────────────────────


def test_a_attribute_bucket_boundaries():
    assert attribute_bucket(0.0) == "low"
    assert attribute_bucket(0.39) == "low"
    assert attribute_bucket(0.4) == "medium"
    assert attribute_bucket(0.69) == "medium"
    assert attribute_bucket(0.7) == "high"
    assert attribute_bucket(1.0) == "high"
    assert attribute_bucket(None) is None


# ── B: input hash ─────────────────────────────────────────────────────────────


def test_b_input_hash_deterministic_and_prompt_version_sensitive():
    h1 = compute_topic_attribute_input_hash(topic_title="T", topic_angle="A", prompt_version="1")
    h2 = compute_topic_attribute_input_hash(topic_title="T", topic_angle="A", prompt_version="1")
    h3 = compute_topic_attribute_input_hash(topic_title="T", topic_angle="A", prompt_version="2")
    h4 = compute_topic_attribute_input_hash(topic_title="T2", topic_angle="A", prompt_version="1")
    assert h1 == h2
    assert h1 != h3
    assert h1 != h4


# ── C/D/E: canonical persistence ──────────────────────────────────────────────


def test_c_save_and_get_round_trip(conn):
    topic_id = _insert_topic(conn, title="Renewable energy explainer")
    save_topic_attribute_evaluation(
        conn,
        topic_id=topic_id,
        source_opportunity_id=None,
        source_semantic_fit_result_id=None,
        prompt_name="topic-attribute-characterization",
        prompt_version="1",
        provider_name="fake",
        model="claude-haiku-4-5-20251001",
        input_hash="h1",
        values=_values(),
    )
    result = get_topic_attributes(conn, topic_id)
    assert result is not None
    assert result.evaluated is True
    assert result.practical_utility == 0.8
    assert result.attribute_rationale == "test rationale"


def test_d_duplicate_save_is_idempotent_noop(conn):
    topic_id = _insert_topic(conn, title="T")
    for _ in range(2):
        save_topic_attribute_evaluation(
            conn,
            topic_id=topic_id,
            source_opportunity_id=None,
            source_semantic_fit_result_id=None,
            prompt_name="p",
            prompt_version="1",
            provider_name="fake",
            model="m",
            input_hash="same-hash",
            values=_values(),
        )
    rows = conn.execute(
        "SELECT COUNT(*) FROM topic_attribute_evaluations WHERE topic_id = ?", (topic_id,)
    ).fetchone()[0]
    assert rows == 1


def test_e_uncharacterized_topic_returns_none(conn):
    topic_id = _insert_topic(conn, title="Never characterized")
    assert get_topic_attributes(conn, topic_id) is None


# ── F/G: forward-path snapshot ────────────────────────────────────────────────


def test_f_snapshot_from_cached_semantic_fit_row(conn):
    topic_id = _insert_topic(conn, title="CRISPR gene editing", promoted_opportunity_id=4)
    _insert_semantic_fit_row_with_attributes(conn, opportunity_id=4, practical_utility=0.77)

    created = snapshot_semantic_fit_attributes_to_topic(conn, topic_id=topic_id, opportunity_id=4)
    assert created is True

    result = get_topic_attributes(conn, topic_id)
    assert result is not None
    assert result.practical_utility == 0.77
    # Provenance retained.
    row = conn.execute(
        "SELECT source_opportunity_id, source_semantic_fit_result_id"
        " FROM topic_attribute_evaluations WHERE topic_id = ?",
        (topic_id,),
    ).fetchone()
    assert row["source_opportunity_id"] == 4
    assert row["source_semantic_fit_result_id"] is not None


def test_f2_snapshot_is_idempotent_on_repeat_call(conn):
    topic_id = _insert_topic(conn, title="T", promoted_opportunity_id=4)
    _insert_semantic_fit_row_with_attributes(conn, opportunity_id=4)
    snapshot_semantic_fit_attributes_to_topic(conn, topic_id=topic_id, opportunity_id=4)
    snapshot_semantic_fit_attributes_to_topic(conn, topic_id=topic_id, opportunity_id=4)
    rows = conn.execute(
        "SELECT COUNT(*) FROM topic_attribute_evaluations WHERE topic_id = ?", (topic_id,)
    ).fetchone()[0]
    assert rows == 1


def test_g_snapshot_returns_false_when_no_cached_row(conn):
    topic_id = _insert_topic(conn, title="T", promoted_opportunity_id=999)
    created = snapshot_semantic_fit_attributes_to_topic(conn, topic_id=topic_id, opportunity_id=999)
    assert created is False
    assert get_topic_attributes(conn, topic_id) is None


def test_g2_snapshot_never_raises_on_missing_columns(conn):
    # Simulate an older cache table shape with no attribute columns at all.
    conn.execute("DROP TABLE opportunity_semantic_fit_results")
    conn.execute(
        """
        CREATE TABLE opportunity_semantic_fit_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            opportunity_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            prompt_version TEXT NOT NULL,
            input_hash TEXT NOT NULL,
            score REAL NOT NULL,
            fit_label TEXT NOT NULL DEFAULT '',
            rationale TEXT NOT NULL DEFAULT '',
            provider_name TEXT NOT NULL,
            model TEXT NOT NULL DEFAULT '',
            evaluated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    topic_id = _insert_topic(conn, title="T", promoted_opportunity_id=4)
    created = snapshot_semantic_fit_attributes_to_topic(conn, topic_id=topic_id, opportunity_id=4)
    assert created is False


# ── H/I/J: orphan direct-characterization path ───────────────────────────────


def test_h_characterize_topic_directly_persists_canonical_evaluation(conn):
    topic_id = _insert_topic(conn, title="Future of Renewable Energy", angle="")
    provider = FakeProvider(output=_CHARACTERIZATION_JSON)

    values = characterize_topic_directly(
        conn,
        topic_id=topic_id,
        primary_niche="science and technology explained",
        audience_description="curious adults",
        ai_provider=provider,
    )
    assert values is not None
    assert values.practical_utility == 0.9
    assert values.attribute_rationale == "orphan characterization rationale"

    persisted = get_topic_attributes(conn, topic_id)
    assert persisted is not None
    assert persisted.practical_utility == 0.9
    # No opportunity was fabricated.
    row = conn.execute(
        "SELECT source_opportunity_id FROM topic_attribute_evaluations WHERE topic_id = ?",
        (topic_id,),
    ).fetchone()
    assert row["source_opportunity_id"] is None


def test_i_characterize_topic_directly_cache_hit_skips_provider(conn):
    topic_id = _insert_topic(conn, title="Future of Renewable Energy")

    class _CountingProvider:
        name = "fake"
        calls = 0

        def complete(self, request):
            self.calls += 1
            return FakeProvider(output=_CHARACTERIZATION_JSON).complete(request)

    provider = _CountingProvider()
    v1 = characterize_topic_directly(
        conn, topic_id=topic_id, primary_niche="n", audience_description="a", ai_provider=provider
    )
    v2 = characterize_topic_directly(
        conn, topic_id=topic_id, primary_niche="n", audience_description="a", ai_provider=provider
    )
    assert v1 is not None and v2 is not None
    assert provider.calls == 1, "second call must be served from cache, not a fresh provider call"


def test_j_characterize_topic_directly_provider_failure_leaves_topic_untouched(conn):
    topic_id = _insert_topic(conn, title="Future of Renewable Energy")

    class _FailingProvider:
        name = "fake"

        def complete(self, request):
            raise RuntimeError("simulated provider failure")

    result = characterize_topic_directly(
        conn,
        topic_id=topic_id,
        primary_niche="n",
        audience_description="a",
        ai_provider=_FailingProvider(),
    )
    assert result is None
    assert get_topic_attributes(conn, topic_id) is None
    # Topic itself is untouched.
    topic_row = conn.execute("SELECT title FROM topics WHERE id = ?", (topic_id,)).fetchone()
    assert topic_row["title"] == "Future of Renewable Energy"


def test_j2_characterize_topic_directly_missing_topic_returns_none(conn):
    assert (
        characterize_topic_directly(
            conn,
            topic_id=999999,
            primary_niche="n",
            audience_description="a",
            ai_provider=FakeProvider(output=_CHARACTERIZATION_JSON),
        )
        is None
    )


# ── K: renewable-energy-shaped fixture, end-to-end ───────────────────────────


def test_k_renewable_energy_shaped_orphan_fixture_end_to_end(conn):
    """Manually-seeded topic, no promoted_opportunity_id, one real publication —
    exactly the historical renewable-energy shape. Attributes must be
    obtainable via the orphan path without fabricating any opportunity."""
    topic_id = _insert_topic(
        conn, title="[DEV] Future of Renewable Energy", angle="", promoted_opportunity_id=None
    )
    _insert_publication(conn, publication_id=1, topic_id=topic_id)
    _insert_lifetime_metric(
        conn, publication_id=1, metric_name="views", value=500, topic_id=topic_id
    )
    _insert_lifetime_metric(
        conn, publication_id=1, metric_name="average_view_percentage", value=0.6, topic_id=topic_id
    )

    values = characterize_topic_directly(
        conn,
        topic_id=topic_id,
        primary_niche="science and technology explained",
        audience_description="curious adults",
        ai_provider=FakeProvider(output=_CHARACTERIZATION_JSON),
    )
    assert values is not None
    assert get_topic_attributes(conn, topic_id) is not None

    result = run_topic_attribute_learning(conn, channel_id=_CHANNEL)
    assert result.topics_with_attributes == 1
    assert result.observations_computed > 0


# ── L: backfill census ────────────────────────────────────────────────────────


def test_l_list_topics_needing_characterization_orphan_only_skips_characterized(conn):
    orphan_needs_it = _insert_topic(conn, title="Orphan needing char", promoted_opportunity_id=None)
    orphan_done = _insert_topic(conn, title="Orphan already done", promoted_opportunity_id=None)
    linked = _insert_topic(conn, title="Opportunity-linked", promoted_opportunity_id=4)
    for t in (orphan_needs_it, orphan_done, linked):
        _insert_publication(conn, publication_id=t, topic_id=t)

    save_topic_attribute_evaluation(
        conn,
        topic_id=orphan_done,
        source_opportunity_id=None,
        source_semantic_fit_result_id=None,
        prompt_name="p",
        prompt_version="1",
        provider_name="fake",
        model="m",
        input_hash="h",
        values=_values(),
    )

    candidates = [c for c in list_topics_needing_attribute_characterization(conn) if c["orphan"]]
    ids = {c["topic_id"] for c in candidates}
    assert orphan_needs_it in ids
    assert orphan_done not in ids  # already characterized
    assert linked not in ids  # not an orphan


# ── M/N: aggregation gates ────────────────────────────────────────────────────


def test_m_min_views_gate_excludes_low_exposure_publication(conn):
    topic_id = _insert_topic(conn, title="T")
    save_topic_attribute_evaluation(
        conn,
        topic_id=topic_id,
        source_opportunity_id=None,
        source_semantic_fit_result_id=None,
        prompt_name="p",
        prompt_version="1",
        provider_name="fake",
        model="m",
        input_hash="h",
        values=_values(),
    )
    _insert_publication(conn, publication_id=1, topic_id=topic_id)
    _insert_lifetime_metric(
        conn, publication_id=1, metric_name="views", value=3, topic_id=topic_id
    )  # below gate

    result = run_topic_attribute_learning(conn, channel_id=_CHANNEL)
    assert result.publication_count == 0
    assert result.observations_computed == 0


def test_n_aggregation_makes_zero_provider_calls_structurally():
    import inspect

    sig = inspect.signature(run_topic_attribute_learning)
    assert "ai_provider" not in sig.parameters
    assert "provider" not in sig.parameters


# ── O: tiny-N maturity classification ────────────────────────────────────────


@pytest.mark.parametrize(
    "n,expected",
    [
        (1, "insufficient"),
        (2, "exploratory"),
        (3, "exploratory"),
        (4, "directional"),
        (10, "actionable"),
    ],
)
def test_o_tiny_n_maturity_classification(conn, monkeypatch, n, expected):
    # Isolate pure N-based classification from the cross-domain safeguard
    # (tested separately in P/Q) by giving every publication its own
    # distinct domain family — always >= 2 distinct families present.
    import app.learning.topic_attributes as ta_module

    monkeypatch.setattr(
        ta_module, "get_domain_family_for_publication", lambda conn, pub_id: f"domain-{pub_id}"
    )

    for i in range(1, n + 1):
        topic_id = _insert_topic(conn, title=f"T{i}")
        save_topic_attribute_evaluation(
            conn,
            topic_id=topic_id,
            source_opportunity_id=None,
            source_semantic_fit_result_id=None,
            prompt_name="p",
            prompt_version="1",
            provider_name="fake",
            model="m",
            input_hash=f"h{i}",
            values=_values(practical_utility=0.9),
        )
        _insert_publication(conn, publication_id=i, topic_id=topic_id)
        _insert_lifetime_metric(
            conn, publication_id=i, metric_name="views", value=100, topic_id=topic_id
        )

    run_topic_attribute_learning(conn, channel_id=_CHANNEL)
    rows = get_attribute_observations(conn, channel_id=_CHANNEL, attribute_name="practical_utility")
    assert len(rows) == 1
    assert rows[0].sample_maturity == expected
    assert rows[0].publication_count == n


# ── P/Q: cross-domain safeguard ───────────────────────────────────────────────


def test_p_same_domain_evidence_capped_below_directional(conn, monkeypatch):
    """10 publications, all attributed to the SAME single domain family, must
    never read as 'directional'/'actionable' — capped at 'exploratory'."""
    import app.learning.topic_attributes as ta_module

    monkeypatch.setattr(
        ta_module, "get_domain_family_for_publication", lambda conn, pub_id: "science"
    )

    for i in range(1, 11):
        topic_id = _insert_topic(conn, title=f"T{i}")
        save_topic_attribute_evaluation(
            conn,
            topic_id=topic_id,
            source_opportunity_id=None,
            source_semantic_fit_result_id=None,
            prompt_name="p",
            prompt_version="1",
            provider_name="fake",
            model="m",
            input_hash=f"h{i}",
            values=_values(practical_utility=0.9),
        )
        _insert_publication(conn, publication_id=i, topic_id=topic_id)
        _insert_lifetime_metric(
            conn, publication_id=i, metric_name="views", value=100, topic_id=topic_id
        )

    run_topic_attribute_learning(conn, channel_id=_CHANNEL)
    rows = get_attribute_observations(conn, channel_id=_CHANNEL, attribute_name="practical_utility")
    assert len(rows) == 1
    assert rows[0].publication_count == 10
    assert rows[0].distinct_domain_family_count == 1
    assert rows[0].sample_maturity == "exploratory", (
        "single-domain evidence must never read as directional/actionable"
    )


def test_q_two_domains_permits_normal_maturity(conn, monkeypatch):
    import app.learning.topic_attributes as ta_module

    domains = {i: ("science" if i <= 5 else "finance") for i in range(1, 11)}
    monkeypatch.setattr(
        ta_module, "get_domain_family_for_publication", lambda conn, pub_id: domains[pub_id]
    )

    for i in range(1, 11):
        topic_id = _insert_topic(conn, title=f"T{i}")
        save_topic_attribute_evaluation(
            conn,
            topic_id=topic_id,
            source_opportunity_id=None,
            source_semantic_fit_result_id=None,
            prompt_name="p",
            prompt_version="1",
            provider_name="fake",
            model="m",
            input_hash=f"h{i}",
            values=_values(practical_utility=0.9),
        )
        _insert_publication(conn, publication_id=i, topic_id=topic_id)
        _insert_lifetime_metric(
            conn, publication_id=i, metric_name="views", value=100, topic_id=topic_id
        )

    run_topic_attribute_learning(conn, channel_id=_CHANNEL)
    rows = get_attribute_observations(conn, channel_id=_CHANNEL, attribute_name="practical_utility")
    assert len(rows) == 1
    assert rows[0].distinct_domain_family_count == 2
    assert rows[0].sample_maturity == "actionable"


# ── R: raw vs decision-useful read models ────────────────────────────────────


def test_r_decision_useful_excludes_insufficient_and_low_support(conn):
    # n=1 topic (insufficient).
    t1 = _insert_topic(conn, title="Solo")
    save_topic_attribute_evaluation(
        conn,
        topic_id=t1,
        source_opportunity_id=None,
        source_semantic_fit_result_id=None,
        prompt_name="p",
        prompt_version="1",
        provider_name="fake",
        model="m",
        input_hash="h1",
        values=_values(practical_utility=0.9),
    )
    _insert_publication(conn, publication_id=1, topic_id=t1)
    _insert_lifetime_metric(conn, publication_id=1, metric_name="views", value=100, topic_id=t1)

    run_topic_attribute_learning(conn, channel_id=_CHANNEL)

    raw = get_attribute_observations(conn, channel_id=_CHANNEL, attribute_name="practical_utility")
    useful = get_decision_useful_attribute_coverage(conn, channel_id=_CHANNEL)
    assert len(raw) == 1
    assert raw[0].sample_maturity == "insufficient"
    assert all(o.attribute_name != "practical_utility" for o in useful), (
        "an n=1 insufficient row must never appear in the decision-useful view"
    )


def test_r2_decision_useful_includes_multi_domain_exploratory(conn, monkeypatch):
    import app.learning.topic_attributes as ta_module

    domains = {1: "science", 2: "finance"}
    monkeypatch.setattr(
        ta_module, "get_domain_family_for_publication", lambda conn, pub_id: domains[pub_id]
    )
    for i in (1, 2):
        topic_id = _insert_topic(conn, title=f"T{i}")
        save_topic_attribute_evaluation(
            conn,
            topic_id=topic_id,
            source_opportunity_id=None,
            source_semantic_fit_result_id=None,
            prompt_name="p",
            prompt_version="1",
            provider_name="fake",
            model="m",
            input_hash=f"h{i}",
            values=_values(practical_utility=0.9),
        )
        _insert_publication(conn, publication_id=i, topic_id=topic_id)
        _insert_lifetime_metric(
            conn, publication_id=i, metric_name="views", value=100, topic_id=topic_id
        )

    run_topic_attribute_learning(conn, channel_id=_CHANNEL)
    useful = [
        o
        for o in get_decision_useful_attribute_coverage(conn, channel_id=_CHANNEL)
        if o.attribute_name == "practical_utility"
    ]
    assert len(useful) == 1
    assert useful[0].sample_maturity == "exploratory"
    assert useful[0].distinct_domain_family_count == 2


# ── S: no interaction features ────────────────────────────────────────────────


def test_s_no_interaction_features_computed(conn):
    topic_id = _insert_topic(conn, title="T")
    save_topic_attribute_evaluation(
        conn,
        topic_id=topic_id,
        source_opportunity_id=None,
        source_semantic_fit_result_id=None,
        prompt_name="p",
        prompt_version="1",
        provider_name="fake",
        model="m",
        input_hash="h",
        values=_values(),
    )
    _insert_publication(conn, publication_id=1, topic_id=topic_id)
    _insert_lifetime_metric(
        conn, publication_id=1, metric_name="views", value=100, topic_id=topic_id
    )

    run_topic_attribute_learning(conn, channel_id=_CHANNEL)
    rows = get_attribute_observations(conn, channel_id=_CHANNEL)
    attribute_names = {r.attribute_name for r in rows}
    # Every observed attribute_name must be exactly one of the seven —
    # never a combined/interaction key like "practical_utility+curiosity".
    assert attribute_names <= set(TOPIC_ATTRIBUTE_NAMES)
    for name in attribute_names:
        assert "+" not in name and "*" not in name and "x" != name


# ── Closure-audit regression tests (Parts 3-29) ──────────────────────────────


def test_t_publication_without_feature_snapshot_still_discovered(conn):
    """Part 8 regression: confirmed live that publication #2 (topic 4,
    CRISPR) has ZERO content_feature_snapshot rows yet is a valid,
    opportunity-linked, published topic. Aggregation must not silently
    exclude it merely because a creative-feature extraction row is
    missing."""
    topic_id = _insert_topic(conn, title="CRISPR gene editing technology")
    save_topic_attribute_evaluation(
        conn,
        topic_id=topic_id,
        source_opportunity_id=None,
        source_semantic_fit_result_id=None,
        prompt_name="p",
        prompt_version="1",
        provider_name="fake",
        model="m",
        input_hash="h",
        values=_values(),
    )
    _insert_publication(conn, publication_id=1, topic_id=topic_id)
    # Deliberately NO content_feature_snapshot row for this publication.
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM content_feature_snapshots WHERE publication_id = 1"
        ).fetchone()[0]
        == 0
    )
    _insert_lifetime_metric(
        conn, publication_id=1, metric_name="views", value=500, topic_id=topic_id
    )

    result = run_topic_attribute_learning(conn, channel_id=_CHANNEL)
    assert result.publication_count == 1
    assert result.observations_computed > 0


def test_u_domain_family_persisted_and_normalized(conn):
    """Closure-audit Part 10-13: a topic-level semantic domain_family is
    now generated, persisted, and normalized for counting (case/whitespace
    insensitive) while the original human-readable text is preserved."""
    t1 = _insert_topic(conn, title="T1")
    save_topic_attribute_evaluation(
        conn,
        topic_id=t1,
        source_opportunity_id=None,
        source_semantic_fit_result_id=None,
        prompt_name="p",
        prompt_version="1",
        provider_name="fake",
        model="m",
        input_hash="h1",
        values=_values(domain_family="  Personal Finance  "),
    )
    attrs = get_topic_attributes(conn, t1)
    assert attrs.domain_family == "Personal Finance"  # original preserved, just stripped

    _insert_publication(conn, publication_id=1, topic_id=t1)
    from app.learning.topic_attributes import get_domain_family_for_publication

    assert (
        get_domain_family_for_publication(conn, 1) == "personal finance"
    )  # normalized for counting


def test_u2_empty_domain_family_normalizes_to_none_never_stored_as_blank(conn):
    t1 = _insert_topic(conn, title="T1")
    save_topic_attribute_evaluation(
        conn,
        topic_id=t1,
        source_opportunity_id=None,
        source_semantic_fit_result_id=None,
        prompt_name="p",
        prompt_version="1",
        provider_name="fake",
        model="m",
        input_hash="h1",
        values=_values(domain_family="   "),
    )
    row = conn.execute(
        "SELECT domain_family FROM topic_attribute_evaluations WHERE topic_id = ?", (t1,)
    ).fetchone()
    assert row["domain_family"] is None


def test_v_maturity_uses_distinct_topic_count_not_publication_count(conn, monkeypatch):
    """Parts 19-20 regression: the SAME topic republished 10 times must
    never alone manufacture 'actionable' semantic-attribute confidence —
    10 publications of 1 topic is 1 semantic sample, not 10."""
    import app.learning.topic_attributes as ta_module

    monkeypatch.setattr(
        ta_module, "get_domain_family_for_publication", lambda conn, pub_id: f"domain-{pub_id}"
    )
    topic_id = _insert_topic(conn, title="Republished topic")
    save_topic_attribute_evaluation(
        conn,
        topic_id=topic_id,
        source_opportunity_id=None,
        source_semantic_fit_result_id=None,
        prompt_name="p",
        prompt_version="1",
        provider_name="fake",
        model="m",
        input_hash="h",
        values=_values(practical_utility=0.9),
    )
    for i in range(1, 11):
        _insert_publication(conn, publication_id=i, topic_id=topic_id)
        _insert_lifetime_metric(
            conn, publication_id=i, metric_name="views", value=100, topic_id=topic_id
        )

    run_topic_attribute_learning(conn, channel_id=_CHANNEL)
    rows = get_attribute_observations(conn, channel_id=_CHANNEL, attribute_name="practical_utility")
    assert len(rows) == 1
    assert rows[0].publication_count == 10  # exposure/repetition still reported
    assert rows[0].distinct_topic_count == 1  # but only ONE semantic sample
    assert rows[0].sample_maturity == "insufficient", (
        "one topic republished 10 times must never alone reach actionable/directional maturity"
    )


def test_v2_distinct_topic_count_reaches_actionable_with_enough_topics(conn, monkeypatch):
    import app.learning.topic_attributes as ta_module

    monkeypatch.setattr(
        ta_module, "get_domain_family_for_publication", lambda conn, pub_id: f"domain-{pub_id}"
    )
    for i in range(1, 11):
        topic_id = _insert_topic(conn, title=f"T{i}")
        save_topic_attribute_evaluation(
            conn,
            topic_id=topic_id,
            source_opportunity_id=None,
            source_semantic_fit_result_id=None,
            prompt_name="p",
            prompt_version="1",
            provider_name="fake",
            model="m",
            input_hash=f"h{i}",
            values=_values(practical_utility=0.9),
        )
        _insert_publication(conn, publication_id=i, topic_id=topic_id)
        _insert_lifetime_metric(
            conn, publication_id=i, metric_name="views", value=100, topic_id=topic_id
        )

    run_topic_attribute_learning(conn, channel_id=_CHANNEL)
    rows = get_attribute_observations(conn, channel_id=_CHANNEL, attribute_name="practical_utility")
    assert rows[0].distinct_topic_count == 10
    assert rows[0].sample_maturity == "actionable"


def test_w_stale_observation_row_pruned_when_evidence_disappears(conn, monkeypatch):
    """Part 18 regression: a bucket with supporting evidence in one run
    must not remain visible after a later run recomputes with different
    membership (e.g. the topic's attribute value moved buckets)."""
    import app.learning.topic_attributes as ta_module

    monkeypatch.setattr(ta_module, "get_domain_family_for_publication", lambda conn, pub_id: "d1")
    topic_id = _insert_topic(conn, title="T")
    save_topic_attribute_evaluation(
        conn,
        topic_id=topic_id,
        source_opportunity_id=None,
        source_semantic_fit_result_id=None,
        prompt_name="p",
        prompt_version="1",
        provider_name="fake",
        model="m",
        input_hash="h-high",
        values=_values(practical_utility=0.9),  # high bucket
    )
    _insert_publication(conn, publication_id=1, topic_id=topic_id)
    _insert_lifetime_metric(
        conn, publication_id=1, metric_name="views", value=100, topic_id=topic_id
    )
    run_topic_attribute_learning(conn, channel_id=_CHANNEL)
    raw = get_attribute_observations(conn, channel_id=_CHANNEL, attribute_name="practical_utility")
    assert {r.attribute_bucket for r in raw} == {"high"}

    # Re-characterize the SAME topic into a new evaluation with a LOW value
    # (get_topic_attributes always returns the newest evaluation).
    save_topic_attribute_evaluation(
        conn,
        topic_id=topic_id,
        source_opportunity_id=None,
        source_semantic_fit_result_id=None,
        prompt_name="p",
        prompt_version="1",
        provider_name="fake",
        model="m",
        input_hash="h-low",
        values=_values(practical_utility=0.1),  # low bucket
    )
    run_topic_attribute_learning(conn, channel_id=_CHANNEL)
    raw_after = get_attribute_observations(
        conn, channel_id=_CHANNEL, attribute_name="practical_utility"
    )
    buckets_after = {r.attribute_bucket for r in raw_after}
    assert buckets_after == {"low"}, (
        "the stale 'high' bucket row must be pruned, not left visible forever"
    )


def test_x_malformed_semantic_fit_attribute_does_not_fail_eligibility_response():
    """Part 4/22/29-F regression: a non-numeric attribute field must not
    crash the whole semantic-fit structured response and turn a valid
    score/fit_label into a call failure."""
    from app.intelligence.experiments.eligibility_service import _SemanticFitOutput

    payload = {
        "score": 0.8,
        "fit_label": "strong_fit",
        "rationale": "r",
        "practical_utility": "not-a-number",
    }
    out = _SemanticFitOutput(**payload)
    assert out.score == 0.8
    assert out.fit_label == "strong_fit"
    assert out.practical_utility is None


def test_y_backfill_census_classifies_snapshot_vs_orphan_vs_needs_characterization(conn):
    """Part 6/29-D regression: an opportunity-linked topic whose cached
    semantic-fit predates the attribute columns must be classified
    'needs_characterization', never silently dropped from the census."""
    orphan = _insert_topic(conn, title="Orphan", promoted_opportunity_id=None)
    snap_ready = _insert_topic(conn, title="Snapshot-ready", promoted_opportunity_id=4)
    needs_char = _insert_topic(conn, title="Needs characterization", promoted_opportunity_id=5)

    for t in (orphan, snap_ready, needs_char):
        _insert_publication(conn, publication_id=t, topic_id=t)

    _insert_semantic_fit_row_with_attributes(
        conn, opportunity_id=4, input_hash="sf-4", practical_utility=0.8
    )
    # Opportunity 5: a cached row exists but predates attributes (all NULL).
    conn.execute(
        "INSERT INTO opportunity_semantic_fit_results"
        " (opportunity_id, channel_id, prompt_version, input_hash, score, fit_label,"
        "  rationale, provider_name, model, evaluated_at)"
        " VALUES (5,1,'2','sf-5',0.9,'strong_fit','r','claude','m', ?)",
        (_NOW,),
    )
    conn.commit()

    candidates = {
        c["topic_id"]: c["recovery_class"]
        for c in list_topics_needing_attribute_characterization(conn)
    }
    assert candidates[orphan] == "orphan"
    assert candidates[snap_ready] == "snapshot"
    assert candidates[needs_char] == "needs_characterization"


def test_z_get_domain_family_for_publication_resolves_via_canonical_lineage(conn):
    topic_id = _insert_topic(conn, title="T")
    save_topic_attribute_evaluation(
        conn,
        topic_id=topic_id,
        source_opportunity_id=None,
        source_semantic_fit_result_id=None,
        prompt_name="p",
        prompt_version="1",
        provider_name="fake",
        model="m",
        input_hash="h",
        values=_values(domain_family="Science"),
    )
    _insert_publication(conn, publication_id=1, topic_id=topic_id)

    from app.learning.topic_attributes import get_domain_family_for_publication

    assert get_domain_family_for_publication(conn, 1) == "science"
    assert get_domain_family_for_publication(conn, 99999) is None  # unresolvable publication


def test_aa_census_requires_actual_publication_not_merely_a_plan(conn):
    """Live-motivated regression: topic "history and society" had a
    publishing_plans row with status='rejected' and ZERO publications, yet
    the pre-fix census (JOIN publishing_plans alone) included it anyway.
    The canonical criterion is an actual publications row.

    A. plan-only, no publication -> excluded.
    B. real publication -> included.
    C. same topic with multiple publications -> included exactly once.
    D. orphan / opportunity-linked classification unaffected by the fix.
    """
    # A: plan exists, status irrelevant, no publication ever created.
    rejected_topic = _insert_topic(conn, title="history and society", promoted_opportunity_id=2)
    conn.execute(
        """
        INSERT INTO publishing_plans (
            render_manifest_id, topic_id, production_plan_id, script_id,
            scene_manifest_id, narration_run_id, caption_run_id,
            input_hash, publishing_engine_version, metadata_version,
            provider, provider_version, title, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            rejected_topic,
            rejected_topic,
            rejected_topic,
            rejected_topic,
            rejected_topic,
            rejected_topic,
            rejected_topic,
            "pp-hash-rejected",
            "v1",
            "v1",
            "youtube",
            "v1",
            "title",
            _NOW,
            _NOW,
        ),
    )
    conn.commit()
    # (deliberately no _insert_publication call for this topic)

    # B: a real, single-publication topic.
    published_topic = _insert_topic(
        conn, title="CRISPR gene editing technology", promoted_opportunity_id=4
    )
    _insert_publication(conn, publication_id=100, topic_id=published_topic)

    # C: same topic, THREE publications (mirrors live CRISPR: pubs 2/3/6, topic 4).
    multi_pub_topic = _insert_topic(conn, title="Multi-published topic", promoted_opportunity_id=5)
    _insert_publication(conn, publication_id=201, topic_id=multi_pub_topic)
    _insert_publication(conn, publication_id=202, topic_id=multi_pub_topic)
    _insert_publication(conn, publication_id=203, topic_id=multi_pub_topic)

    # D: orphan topic with a real publication.
    orphan_topic = _insert_topic(conn, title="Orphan topic", promoted_opportunity_id=None)
    _insert_publication(conn, publication_id=300, topic_id=orphan_topic)

    candidates = list_topics_needing_attribute_characterization(conn)
    topic_ids = {c["topic_id"] for c in candidates}
    by_id = {c["topic_id"]: c for c in candidates}

    # A: rejected/never-published topic must NOT appear.
    assert rejected_topic not in topic_ids

    # B: real published topic appears exactly once.
    assert published_topic in topic_ids
    assert sum(1 for c in candidates if c["topic_id"] == published_topic) == 1

    # C: multi-publication topic appears exactly ONCE, not three times.
    assert multi_pub_topic in topic_ids
    assert sum(1 for c in candidates if c["topic_id"] == multi_pub_topic) == 1

    # D: classification still correct post-fix.
    assert by_id[orphan_topic]["recovery_class"] == "orphan"
    assert by_id[published_topic]["recovery_class"] == "needs_characterization"
    assert by_id[multi_pub_topic]["recovery_class"] == "needs_characterization"
