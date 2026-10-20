"""Phase 7A — persistence for `exploration_cell_selections`.

Deliberately thin: every read returns plain `ExplorationCell` values (or
counts), so `portfolio.py`'s pure allocator never needs to know about SQL,
and the same allocator call works identically against real DB-backed
history or the in-memory history the simulation harness (§16) builds.
"""

from __future__ import annotations

import sqlite3

from app.intelligence.exploration.cell import ExplorationCell

_ALLOCATOR_VERSION = "v1"


def record_cell_selection(
    conn: sqlite3.Connection,
    *,
    channel_id: int,
    workspace_id: str | None,
    cell: ExplorationCell,
    selected_at: str,
    source: str = "portfolio_allocator",
    opportunity_id: int | None = None,
) -> int:
    """Persist one allocator decision. Returns the new row id.

    Does not commit — callers control their own transaction boundary,
    matching every other repository function in this codebase.
    """
    if source not in ("portfolio_allocator", "recovery_discovery"):
        raise ValueError(
            f"source must be 'portfolio_allocator' or 'recovery_discovery', got {source!r}"
        )
    cur = conn.execute(
        """INSERT INTO exploration_cell_selections
               (channel_id, workspace_id, domain, format, audience_value,
                designation, rationale, policy_version, opportunity_id,
                source, selected_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            channel_id,
            workspace_id,
            cell.domain,
            cell.format,
            cell.audience_value,
            cell.designation,
            cell.rationale,
            _ALLOCATOR_VERSION,
            opportunity_id,
            source,
            selected_at,
        ),
    )
    return int(cur.lastrowid)


def link_selection_to_opportunity(
    conn: sqlite3.Connection, selection_id: int, opportunity_id: int
) -> None:
    """Back-fill the opportunity a selection eventually produced, once
    Level B discovery (§7) finds one inside the chosen cell. Never
    fabricated — a selection with no resulting opportunity simply stays
    NULL, which the diversity guard and simulation treat identically to any
    other "tried, didn't land" cell.

    Phase 7C.2 — this function performs NO compatibility check itself; it
    is the low-level write primitive. Real callers should use
    `link_selection_to_opportunity_if_compatible` below, which is the
    only path that should ever be wired into production discovery
    completion. This one stays available for callers (tests, an operator
    override) that have already made the compatibility decision
    themselves.
    """
    conn.execute(
        "UPDATE exploration_cell_selections SET opportunity_id = ? WHERE id = ?",
        (opportunity_id, selection_id),
    )


def resolve_opportunity_domain_context(conn: sqlite3.Connection, opportunity_id: int) -> str:
    """Assemble the richest available free text describing an
    opportunity's actual subject, for domain-compatibility classification
    (see domain_compatibility.py).

    Combines the opportunity's own title/normalized_topic/topic_summary
    with two already-computed real signals from discovery, at zero extra
    cost:
      - the topic cluster's own label (market_topic_clusters.cluster_label,
        via opportunities.canonical_cluster_id)
      - the ORIGINAL cold-start probe's own domain_family (Phase 18H.B.1,
        market_exploration_probes.domain_family) — domain_family lives on
        the PROBE, not the topic cluster (no persisted path there; see
        _apply_v53_domain_family's own docstring), so reaching it means
        joining market_topic_clusters -> market_cluster_members ->
        market_exploration_probes, the real membership link already
        written by interpretation. Missing pieces are simply omitted,
        never fabricated.
    """
    opp_row = conn.execute(
        "SELECT title, normalized_topic, topic_summary, canonical_cluster_id "
        "FROM opportunities WHERE id = ?",
        (opportunity_id,),
    ).fetchone()
    if opp_row is None:
        return ""

    parts = [
        opp_row["title"] or "",
        opp_row["normalized_topic"] or "",
        opp_row["topic_summary"] or "",
    ]

    canonical_cluster_id = opp_row["canonical_cluster_id"]
    if canonical_cluster_id is not None:
        cluster_row = conn.execute(
            """SELECT id, cluster_label FROM market_topic_clusters
                WHERE canonical_cluster_id = ?
                ORDER BY id DESC LIMIT 1""",
            (canonical_cluster_id,),
        ).fetchone()
        if cluster_row is not None:
            parts.append(cluster_row["cluster_label"] or "")
            domain_family_rows = conn.execute(
                """SELECT DISTINCT p.domain_family
                     FROM market_cluster_members m
                     JOIN market_exploration_probes p ON p.id = m.probe_id
                    WHERE m.cluster_id = ? AND p.domain_family IS NOT NULL""",
                (cluster_row["id"],),
            ).fetchall()
            for r in domain_family_rows:
                parts.append(r["domain_family"] or "")
        canonical_row = conn.execute(
            "SELECT canonical_label FROM market_canonical_clusters WHERE id = ?",
            (canonical_cluster_id,),
        ).fetchone()
        if canonical_row is not None:
            parts.append(canonical_row["canonical_label"] or "")

    return " ".join(p for p in parts if p)


def link_selection_to_opportunity_if_compatible(
    conn: sqlite3.Connection,
    selection_id: int,
    opportunity_id: int,
    *,
    domain: str,
):
    """The real, safe linkage path (Phase 7C.2, §4/§6): only attributes
    an opportunity to an exploration cell when it is genuinely
    domain-compatible (compatible or adjacent-but-valid). An incompatible
    opportunity is left completely alone — still a perfectly valid
    opportunity in the general pool, just never wrongly attributed to
    this cell. The selection's own `opportunity_id` stays NULL in that
    case, exactly like "tried, found nothing usable" — never a fabricated
    link.

    Returns the `DomainCompatibilityResult` either way, so the caller can
    report exactly why a link did or did not happen.
    """
    from app.intelligence.exploration.domain_compatibility import classify_domain_compatibility

    context_text = resolve_opportunity_domain_context(conn, opportunity_id)
    result = classify_domain_compatibility(domain, context_text)
    if result.eligible_for_linkage:
        link_selection_to_opportunity(conn, selection_id, opportunity_id)
    return result


def list_recent_cells(
    conn: sqlite3.Connection, channel_id: int, *, limit: int = 20
) -> list[ExplorationCell]:
    """Most recent selections, oldest-first (matches the ordering
    `diversity.py`'s window functions expect)."""
    rows = conn.execute(
        """SELECT domain, format, audience_value, designation, rationale
             FROM exploration_cell_selections
            WHERE channel_id = ?
            ORDER BY selected_at DESC, id DESC
            LIMIT ?""",
        (channel_id, limit),
    ).fetchall()
    cells = [
        ExplorationCell(
            domain=r["domain"],
            format=r["format"],
            audience_value=r["audience_value"],
            designation=r["designation"],
            rationale=r["rationale"] or "",
        )
        for r in rows
    ]
    return list(reversed(cells))


def resolve_cell_for_publication(
    conn: sqlite3.Connection, publication_id: int
) -> ExplorationCell | None:
    """Phase 7B, §3 — the durable lineage proof: exploration cell selection
    -> opportunity -> topic -> publishing plan -> publication.

    The join path already exists in full, with no new schema: `topics.
    promoted_opportunity_id` is the SAME bridge `topic_attributes.py`'s own
    `get_domain_family_for_publication` already relies on (see that
    function's docstring) between the intelligence-domain `opportunities`
    table and the core-domain `topics`/`publishing_plans`/`publications`
    chain. `exploration_cell_selections.opportunity_id` slots into that
    exact bridge at its intelligence-domain end — nothing new to build.

    Returns None for any publication whose topic was never promoted from an
    opportunity, whose opportunity was never linked to a cell selection (the
    ordinary path today, before Level B discovery — §7 — starts calling
    `link_selection_to_opportunity`), or that simply predates this feature
    entirely. All three read identically as "no attributable cell" — never
    fabricated, exactly like `topic_attribute_evaluations.domain_family`
    reading NULL for a topic characterized before that column existed.
    """
    row = conn.execute(
        """SELECT ecs.domain, ecs.format, ecs.audience_value, ecs.designation, ecs.rationale
             FROM publications pub
             JOIN publishing_plans pp ON pub.publishing_plan_id = pp.id
             JOIN topics t ON pp.topic_id = t.id
             JOIN exploration_cell_selections ecs
               ON ecs.opportunity_id = t.promoted_opportunity_id
            WHERE pub.id = ?
            ORDER BY ecs.id DESC
            LIMIT 1""",
        (publication_id,),
    ).fetchone()
    if row is None:
        return None
    return ExplorationCell(
        domain=row["domain"],
        format=row["format"],
        audience_value=row["audience_value"],
        designation=row["designation"],
        rationale=row["rationale"] or "",
    )


def resolve_cell_for_topic(conn: sqlite3.Connection, topic_id: int) -> ExplorationCell | None:
    """Phase 7B, §16/§17 — the format-propagation resolution step:
    topic -> opportunity -> exploration cell.

    Same bridge as `resolve_cell_for_publication` (`topics.
    promoted_opportunity_id`), read from the opposite end. Returns None —
    never raises — whenever the topic wasn't promoted from an opportunity,
    that opportunity was never linked to a cell selection, or the topic
    simply predates this feature: every one of those is the ordinary case
    for the vast majority of topics today, and callers must treat None as
    "use the default format", not an error.
    """
    row = conn.execute(
        """SELECT ecs.domain, ecs.format, ecs.audience_value, ecs.designation, ecs.rationale
             FROM topics t
             JOIN exploration_cell_selections ecs
               ON ecs.opportunity_id = t.promoted_opportunity_id
            WHERE t.id = ?
            ORDER BY ecs.id DESC
            LIMIT 1""",
        (topic_id,),
    ).fetchone()
    if row is None:
        return None
    return ExplorationCell(
        domain=row["domain"],
        format=row["format"],
        audience_value=row["audience_value"],
        designation=row["designation"],
        rationale=row["rationale"] or "",
    )


def resolve_format_for_topic(conn: sqlite3.Connection, topic_id: int) -> str:
    """Convenience wrapper over `resolve_cell_for_topic` for callers that
    only need the format string. Always returns
    `format_contract.DEFAULT_FORMAT` rather than None."""
    from app.intelligence.exploration.format_contract import DEFAULT_FORMAT

    cell = resolve_cell_for_topic(conn, topic_id)
    return cell.format if cell is not None else DEFAULT_FORMAT


def compute_trial_counts(
    conn: sqlite3.Connection, channel_id: int, *, lookback: int = 200
) -> dict[tuple[str, str], int]:
    """How many times each (domain, format) pair has been selected, over
    the last `lookback` selections — the allocator's novelty signal."""
    from app.intelligence.exploration.taxonomy import normalize_domain, normalize_format

    rows = conn.execute(
        """SELECT domain, format FROM (
               SELECT domain, format, selected_at, id
                 FROM exploration_cell_selections
                WHERE channel_id = ?
                ORDER BY selected_at DESC, id DESC
                LIMIT ?
           )""",
        (channel_id, lookback),
    ).fetchall()
    counts: dict[tuple[str, str], int] = {}
    for r in rows:
        key = (normalize_domain(r["domain"]), normalize_format(r["format"]))
        counts[key] = counts.get(key, 0) + 1
    return counts
