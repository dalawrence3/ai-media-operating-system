"""Command-line interface for the AI Content Production Engine."""

from __future__ import annotations

import sys
from typing import Annotated

import typer

from app import __version__
from app.core.config import get_config
from app.core.database import open_db
from app.core.logging import configure_logging, get_logger
from app.core.models import Run, Script, ScriptStatus, Source, SourceKind, Topic, TopicStatus
from app.core.repository import (
    create_run,
    create_script,
    create_source,
    create_topic,
    delete_source,
    delete_topic,
    list_runs,
    list_scripts,
    list_sources,
    list_topics,
    next_script_version,
    update_run_status,
    update_script_status,
    update_topic,
)
from app.intelligence.cli import channels_app, discover_app
from app.intelligence.repository import promote_opportunity
from app.intelligence.scoring_cli import scoring_app
from app.scenes.cli import scenes_app

app = typer.Typer(
    name="ace",
    help="AI Content Production Engine command-line interface.",
    no_args_is_help=True,
)
app.add_typer(channels_app, name="channels")
app.add_typer(discover_app, name="discover")
app.add_typer(scoring_app, name="intelligence")
app.add_typer(scenes_app, name="scenes")
logger = get_logger(__name__)


def _get_db():
    cfg = get_config()
    configure_logging(cfg.log_level)
    return open_db(cfg.db_path)


# ---------------------------------------------------------------------------
# Diagnostic commands
# ---------------------------------------------------------------------------


@app.command()
def version() -> None:
    """Print the installed application version."""
    typer.echo(f"ai-content-engine {__version__}")


@app.command()
def doctor() -> None:
    """Report basic environment diagnostics."""
    typer.echo(f"Python: {sys.version.split()[0]}")
    typer.echo(f"Platform: {sys.platform}")
    typer.echo("Status: OK")


# ---------------------------------------------------------------------------
# Topic commands
# ---------------------------------------------------------------------------

topics_app = typer.Typer(help="Manage content topics.", no_args_is_help=True)
app.add_typer(topics_app, name="topics")


@topics_app.command("add")
def topics_add(
    title: Annotated[str, typer.Argument(help="Topic title.")],
    angle: Annotated[str, typer.Option("--angle", "-a", help="Intended angle.")] = "",
) -> None:
    """Add a new topic."""
    conn = _get_db()
    topic = create_topic(conn, Topic(title=title, angle=angle))
    typer.echo(f"Created topic id={topic.id} title={topic.title!r}")


@topics_app.command("list")
def topics_list(
    status: Annotated[
        TopicStatus | None, typer.Option("--status", "-s", help="Filter by status.")
    ] = None,
) -> None:
    """List topics."""
    conn = _get_db()
    rows = list_topics(conn, status=status)
    if not rows:
        typer.echo("No topics found.")
        return
    for t in rows:
        typer.echo(f"[{t.id}] {t.title!r}  status={t.status.value}  angle={t.angle!r}")


@topics_app.command("promote")
def topics_promote(
    opportunity_id: Annotated[int, typer.Argument(help="Opportunity ID to promote.")],
    angle: Annotated[str | None, typer.Option("--angle", help="Override topic angle.")] = None,
    operator: Annotated[
        str, typer.Option("--operator", help="Actor name recorded in lifecycle event.")
    ] = "operator",
    allow_unscored: Annotated[
        bool,
        typer.Option("--allow-unscored", help="Promote without requiring a score."),
    ] = False,
) -> None:
    """Promote a discovered opportunity to an active topic."""
    from app.core.repository import get_topic_by_promoted_opportunity
    from app.intelligence.repository import list_scores_for_opportunity

    conn = _get_db()
    already_promoted = get_topic_by_promoted_opportunity(conn, opportunity_id) is not None

    try:
        topic, _event = promote_opportunity(
            conn,
            opportunity_id,
            angle_override=angle,
            operator=operator,
            allow_unscored=allow_unscored,
        )
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None

    scores = list_scores_for_opportunity(conn, opportunity_id)
    if allow_unscored and not scores:
        typer.echo(f"Warning: opportunity {opportunity_id} has no score.", err=True)
    score_line = ""
    if scores:
        s = scores[0]
        score_line = f"  Score: {s.composite_score:.2f}  Confidence: {s.confidence:.2f}"

    if already_promoted:
        typer.echo(
            f"Opportunity [{opportunity_id}] was already promoted"
            f" → Topic [{topic.id}] {topic.title!r}"
        )
    else:
        typer.echo(f"Promoted opportunity [{opportunity_id}] → Topic [{topic.id}] {topic.title!r}")
    if score_line:
        typer.echo(score_line)


@topics_app.command("archive")
def topics_archive(
    topic_id: Annotated[int, typer.Argument(help="Topic ID to archive.")],
) -> None:
    """Archive a topic (soft-delete)."""
    conn = _get_db()
    from app.core.repository import get_topic

    t = get_topic(conn, topic_id)
    if t is None:
        typer.echo(f"Topic {topic_id} not found.", err=True)
        raise typer.Exit(1)
    t.status = TopicStatus.archived
    update_topic(conn, t)
    typer.echo(f"Topic {topic_id} archived.")


@topics_app.command("delete")
def topics_delete(
    topic_id: Annotated[int, typer.Argument(help="Topic ID to delete permanently.")],
    confirm: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation.")] = False,
) -> None:
    """Permanently delete a topic and all its related data."""
    if not confirm:
        typer.confirm(f"Permanently delete topic {topic_id} and all related data?", abort=True)
    conn = _get_db()
    if not delete_topic(conn, topic_id):
        typer.echo(f"Topic {topic_id} not found.", err=True)
        raise typer.Exit(1)
    typer.echo(f"Topic {topic_id} deleted.")


# ---------------------------------------------------------------------------
# Source commands
# ---------------------------------------------------------------------------

sources_app = typer.Typer(help="Manage source material.", no_args_is_help=True)
app.add_typer(sources_app, name="sources")


@sources_app.command("add")
def sources_add(
    topic_id: Annotated[int, typer.Argument(help="Parent topic ID.")],
    kind: Annotated[SourceKind, typer.Argument(help="Source kind: url | file | note.")],
    reference: Annotated[str, typer.Argument(help="URL, file path, or note text.")],
    notes: Annotated[str, typer.Option("--notes", "-n", help="Optional notes.")] = "",
) -> None:
    """Add a source to a topic."""
    conn = _get_db()
    s = create_source(conn, Source(topic_id=topic_id, kind=kind, reference=reference, notes=notes))
    typer.echo(f"Created source id={s.id} topic_id={topic_id} kind={kind.value}")


@sources_app.command("list")
def sources_list(
    topic_id: Annotated[int, typer.Argument(help="Topic ID.")],
) -> None:
    """List sources for a topic."""
    conn = _get_db()
    rows = list_sources(conn, topic_id)
    if not rows:
        typer.echo("No sources found.")
        return
    for s in rows:
        typer.echo(f"[{s.id}] {s.kind.value}  {s.reference!r}")


@sources_app.command("delete")
def sources_delete(
    source_id: Annotated[int, typer.Argument(help="Source ID to delete.")],
    confirm: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation.")] = False,
) -> None:
    """Delete a source."""
    if not confirm:
        typer.confirm(f"Delete source {source_id}?", abort=True)
    conn = _get_db()
    if not delete_source(conn, source_id):
        typer.echo(f"Source {source_id} not found.", err=True)
        raise typer.Exit(1)
    typer.echo(f"Source {source_id} deleted.")


@sources_app.command("fetch")
def sources_fetch(
    url: Annotated[str, typer.Argument(help="URL to fetch.")],
    topic_id: Annotated[int, typer.Option("--topic", "-t", help="Parent topic ID.")],
    force: Annotated[
        bool, typer.Option("--force", help="Re-fetch even if content is unchanged.")
    ] = False,
) -> None:
    """Fetch a URL, extract content, and compute a quality score."""
    from datetime import UTC, datetime

    from app.research.errors import FetchError, SecurityError
    from app.research.extract import extract_html, extract_text
    from app.research.fetch import fetch_url
    from app.research.hashing import HASH_ALGORITHM, normalize_for_hash, sha256_hex
    from app.research.models import ExtractionStatus, FetchStatus
    from app.research.quality import score_quality
    from app.research.repository import (
        create_source_content,
        get_latest_source_content,
        get_or_create_source,
    )
    from app.research.validate import validate_url

    conn = _get_db()

    # 1. Validate topic exists
    from app.core.repository import get_topic

    if get_topic(conn, topic_id) is None:
        typer.echo(f"Error: Topic {topic_id} not found.", err=True)
        raise typer.Exit(1)

    # 2–5. URL validation (scheme, userinfo, SSRF)
    try:
        validate_url(url)
    except SecurityError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from None

    # 6. Get or create Source identity (only after validation passes)
    from app.core.models import SourceKind

    source, _created = get_or_create_source(conn, topic_id, SourceKind.url, url)

    # 7. HTTP fetch
    fetched_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    try:
        fetch_result = fetch_url(url)
    except SecurityError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from None
    except FetchError as exc:
        create_source_content(
            conn,
            source_id=source.id,  # type: ignore[arg-type]
            fetch_status=FetchStatus.failed,
            extraction_status=ExtractionStatus.failed,
            fetched_at=fetched_at,
            http_status=exc.http_status,
            extraction_error=str(exc),
        )
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from None

    retrieval_hash = sha256_hex(fetch_result.content)

    # Determine MIME type and extract text
    mime = (fetch_result.mime_type or "").lower()
    if "pdf" in mime:
        from app.research.extract import extract_pdf

        result = extract_pdf(fetch_result.content)
    elif mime.startswith("text/plain"):
        result = extract_text(fetch_result.content)
    else:
        result = extract_html(fetch_result.content, url=fetch_result.canonical_url)

    # Hashes
    normalized_hash: str | None = None
    if result.raw_text:
        normalized_hash = sha256_hex(normalize_for_hash(result.raw_text))

    # Idempotency: compare normalized_text_hash with latest successful content
    if not force and normalized_hash is not None:
        latest = get_latest_source_content(conn, source.id, require_successful=True)  # type: ignore[arg-type]
        if latest and latest.normalized_text_hash == normalized_hash:
            typer.echo(
                f"Source {source.id}: content unchanged (hash match). Use --force to re-fetch."
            )
            return

    # Determine fetch/extraction status and exit code
    if result.raw_text is None:
        fetch_status = FetchStatus.ok
        extraction_status = ExtractionStatus.failed
    elif result.is_partial or result.extraction_error:
        fetch_status = FetchStatus.ok
        extraction_status = ExtractionStatus.partial
    else:
        fetch_status = FetchStatus.ok
        extraction_status = ExtractionStatus.ok

    # Quality scoring (only when there is usable text)
    quality_score: float | None = None
    quality_factors_json: str | None = None
    quality_scorer_version: str | None = None
    if result.raw_text:
        from app.research.models import SourceContent

        sc_temp = SourceContent(
            source_id=source.id,  # type: ignore[arg-type]
            fetch_status=fetch_status,
            extraction_status=extraction_status,
            fetched_at=datetime.now(UTC),
            raw_text=result.raw_text,
            word_count=result.word_count,
            author=result.author,
            published_at=result.published_at,
            domain_type=result.domain_type,
            suspected_truncation=result.suspected_truncation,
        )
        qr = score_quality(sc_temp)
        quality_score = qr.score
        quality_factors_json = qr.factors_json
        quality_scorer_version = qr.scorer_version

    # 8. Persist SourceContent
    create_source_content(
        conn,
        source_id=source.id,  # type: ignore[arg-type]
        fetch_status=fetch_status,
        extraction_status=extraction_status,
        http_status=fetch_result.http_status,
        canonical_url=fetch_result.canonical_url,
        mime_type=fetch_result.mime_type,
        fetched_at=fetched_at,
        raw_text=result.raw_text,
        retrieval_hash=retrieval_hash,
        normalized_text_hash=normalized_hash,
        hash_algorithm=HASH_ALGORITHM,
        word_count=result.word_count,
        title=result.title,
        author=result.author,
        published_at=result.published_at,
        domain_type=result.domain_type.value if result.domain_type else None,
        extraction_method=result.extraction_method.value if result.extraction_method else None,
        extraction_error=result.extraction_error,
        suspected_truncation=result.suspected_truncation,
        quality_score=quality_score,
        quality_factors_json=quality_factors_json,
        quality_scorer_version=quality_scorer_version,
    )

    if extraction_status == ExtractionStatus.failed:
        typer.echo(
            f"Error: Source {source.id} created but extraction failed: {result.extraction_error}",
            err=True,
        )
        raise typer.Exit(1)

    host = fetch_result.canonical_url.split("/")[2] if "/" in fetch_result.canonical_url else url
    words = f"{result.word_count:,}" if result.word_count else "?"
    quality_str = f"{quality_score:.2f}" if quality_score is not None else "?"

    if extraction_status == ExtractionStatus.partial:
        typer.echo(f"Warning: partial extraction — {result.extraction_error}", err=True)

    typer.echo(f"Source {source.id}: fetched ({words} words, quality: {quality_str}) — {host}")


@sources_app.command("ingest-file")
def sources_ingest_file(
    path: Annotated[str, typer.Argument(help="Local file path to ingest.")],
    topic_id: Annotated[int, typer.Option("--topic", "-t", help="Parent topic ID.")],
    force: Annotated[
        bool, typer.Option("--force", help="Re-ingest even if content is unchanged.")
    ] = False,
) -> None:
    """Read a local file, extract content, and compute a quality score."""
    from datetime import UTC, datetime

    from app.research.errors import SecurityError
    from app.research.extract import extract_markdown, extract_pdf, extract_text
    from app.research.hashing import HASH_ALGORITHM, normalize_for_hash, sha256_hex
    from app.research.models import ExtractionStatus, FetchStatus
    from app.research.quality import score_quality
    from app.research.repository import (
        create_source_content,
        get_latest_source_content,
        get_or_create_source,
    )
    from app.research.validate import validate_file_path

    conn = _get_db()

    from app.core.repository import get_topic

    if get_topic(conn, topic_id) is None:
        typer.echo(f"Error: Topic {topic_id} not found.", err=True)
        raise typer.Exit(1)

    # File validation (steps 2–6)
    try:
        resolved = validate_file_path(path)
    except (SecurityError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from None

    from app.core.models import SourceKind

    source, _created = get_or_create_source(conn, topic_id, SourceKind.file, str(resolved))

    fetched_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    content = resolved.read_bytes()
    retrieval_hash = sha256_hex(content)

    suffix = resolved.suffix.lower()
    if suffix == ".pdf":
        result = extract_pdf(content)
    elif suffix == ".md":
        result = extract_markdown(content)
    else:
        result = extract_text(content)

    normalized_hash: str | None = None
    if result.raw_text:
        normalized_hash = sha256_hex(normalize_for_hash(result.raw_text))

    if not force and normalized_hash is not None:
        latest = get_latest_source_content(conn, source.id, require_successful=True)  # type: ignore[arg-type]
        if latest and latest.normalized_text_hash == normalized_hash:
            typer.echo(
                f"Source {source.id}: content unchanged (hash match). Use --force to re-ingest."
            )
            return

    if result.raw_text is None:
        fetch_status = FetchStatus.ok
        extraction_status = ExtractionStatus.failed
    elif result.is_partial or result.extraction_error:
        fetch_status = FetchStatus.ok
        extraction_status = ExtractionStatus.partial
    else:
        fetch_status = FetchStatus.ok
        extraction_status = ExtractionStatus.ok

    quality_score: float | None = None
    quality_factors_json: str | None = None
    quality_scorer_version: str | None = None
    if result.raw_text:
        from app.research.models import SourceContent

        sc_temp = SourceContent(
            source_id=source.id,  # type: ignore[arg-type]
            fetch_status=fetch_status,
            extraction_status=extraction_status,
            fetched_at=datetime.now(UTC),
            raw_text=result.raw_text,
            word_count=result.word_count,
            author=result.author,
            published_at=result.published_at,
            domain_type=result.domain_type,
            suspected_truncation=result.suspected_truncation,
        )
        qr = score_quality(sc_temp)
        quality_score = qr.score
        quality_factors_json = qr.factors_json
        quality_scorer_version = qr.scorer_version

    create_source_content(
        conn,
        source_id=source.id,  # type: ignore[arg-type]
        fetch_status=fetch_status,
        extraction_status=extraction_status,
        fetched_at=fetched_at,
        raw_text=result.raw_text,
        retrieval_hash=retrieval_hash,
        normalized_text_hash=normalized_hash,
        hash_algorithm=HASH_ALGORITHM,
        word_count=result.word_count,
        title=result.title,
        author=result.author,
        published_at=result.published_at,
        domain_type=result.domain_type.value if result.domain_type else None,
        extraction_method=result.extraction_method.value if result.extraction_method else None,
        extraction_error=result.extraction_error,
        suspected_truncation=result.suspected_truncation,
        quality_score=quality_score,
        quality_factors_json=quality_factors_json,
        quality_scorer_version=quality_scorer_version,
    )

    if extraction_status == ExtractionStatus.failed:
        typer.echo(
            f"Error: Source {source.id} created but extraction failed: {result.extraction_error}",
            err=True,
        )
        raise typer.Exit(1)

    words = f"{result.word_count:,}" if result.word_count else "?"
    quality_str = f"{quality_score:.2f}" if quality_score is not None else "?"

    if extraction_status == ExtractionStatus.partial:
        typer.echo(f"Warning: partial extraction — {result.extraction_error}", err=True)

    typer.echo(
        f"Source {source.id}: ingested ({words} words, quality: {quality_str}) — {resolved.name}"
    )


@sources_app.command("quality")
def sources_quality(
    source_id: Annotated[int, typer.Argument(help="Source ID.")],
) -> None:
    """Display the quality score breakdown for a source."""
    from app.research.repository import get_latest_source_content

    conn = _get_db()
    from app.core.repository import get_source

    if get_source(conn, source_id) is None:
        typer.echo(f"Error: Source {source_id} not found.", err=True)
        raise typer.Exit(1)

    sc = get_latest_source_content(conn, source_id, require_successful=True)
    if sc is None or sc.quality_score is None:
        typer.echo(
            f"Error: Source {source_id} has no extracted content. "
            f"Run 'ace sources fetch {source_id}' first.",
            err=True,
        )
        raise typer.Exit(1)

    factors = sc.quality_factors or {}
    typer.echo(f"Source {source_id} quality report (scorer: {sc.quality_scorer_version})")
    typer.echo(f"{'Factor':<24} {'Score':>6}")
    typer.echo("-" * 32)
    for factor, value in factors.items():
        typer.echo(f"  {factor:<22} {value:>6.2f}")
    typer.echo("-" * 32)
    typer.echo(f"  {'Composite':<22} {sc.quality_score:>6.2f}")
    if sc.word_count is not None:
        typer.echo(f"\nWords: {sc.word_count:,}")
    if sc.title:
        typer.echo(f"Title: {sc.title}")
    if sc.author:
        typer.echo(f"Author: {sc.author}")
    if sc.published_at:
        typer.echo(f"Published: {sc.published_at}")
    if sc.extraction_error:
        typer.echo(f"Note: {sc.extraction_error}", err=True)


# ---------------------------------------------------------------------------
# Phase 4.2 — claim extraction commands
# ---------------------------------------------------------------------------


@sources_app.command("extract-claims")
def sources_extract_claims(
    source_id: Annotated[int, typer.Argument(help="Source ID.")],
    force: Annotated[
        bool, typer.Option("--force", help="Skip idempotency check; always run.")
    ] = False,
    replace: Annotated[
        bool, typer.Option("--replace", help="Supersede the prior completed run.")
    ] = False,
    model: Annotated[
        str | None, typer.Option("--model", help="Override AI model (default: cfg.ai_model).")
    ] = None,
    prompt_version: Annotated[
        str, typer.Option("--prompt-version", help="Prompt version to use.")
    ] = "1",
) -> None:
    """Extract claims from the latest fetched content for SOURCE_ID."""
    from app.core.repository import get_source
    from app.research.errors import ClaimExtractionError
    from app.research.extractor import _build_provider, extract_claims
    from app.research.repository import get_latest_source_content

    cfg = get_config()
    configure_logging(cfg.log_level)
    conn = _get_db()

    if get_source(conn, source_id) is None:
        typer.echo(f"Error: Source {source_id} not found.", err=True)
        raise typer.Exit(1)

    sc = get_latest_source_content(conn, source_id, require_successful=True)
    if sc is None:
        typer.echo(
            f"Error: Source {source_id} has no successful content. "
            f"Run 'ace sources fetch {source_id}' first.",
            err=True,
        )
        raise typer.Exit(1)

    provider = _build_provider(cfg)
    if model:
        provider._model = model  # type: ignore[attr-defined]

    try:
        run = extract_claims(
            conn,
            sc,
            provider=provider,
            prompt_version=prompt_version,
            replace=replace or force,
        )
    except ClaimExtractionError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(
        f"Run {run.id}: {run.status.value} — "
        f"{run.accepted_claim_count} claim(s) accepted from {run.total_chunk_count} chunk(s)."
    )
    if run.was_truncated:
        typer.echo("Note: content was truncated before chunking.")


@sources_app.command("list-claims")
def sources_list_claims(
    topic_id: Annotated[int, typer.Argument(help="Topic ID.")],
    include_unsupported: Annotated[
        bool,
        typer.Option("--include-unsupported", help="Include no_quote and unsupported claims."),
    ] = False,
    all_runs: Annotated[
        bool, typer.Option("--all-runs", help="Show claims from all runs, not just active.")
    ] = False,
    source_id: Annotated[
        int | None, typer.Option("--source-id", help="Filter to a specific source.")
    ] = None,
    run_id: Annotated[
        int | None, typer.Option("--run-id", help="Filter to a specific extraction run.")
    ] = None,
) -> None:
    """List active evidence claims for TOPIC_ID."""
    from app.research.repository import list_active_evidence_for_topic, list_claims

    conn = _get_db()

    if run_id is not None:
        claims = list_claims(conn, run_id, include_unsupported=include_unsupported)
        typer.echo(f"{'ID':>6}  {'Type':<12}  {'Support':<12}  Claim")
        typer.echo("-" * 70)
        for c in claims:
            row = (
                f"{c.id:>6}  {c.claim_type.value:<12}"
                f"  {c.quote_support_status.value:<12}  {c.claim_text[:60]}"
            )
            typer.echo(row)
        typer.echo(f"\n{len(claims)} claim(s) from run {run_id}.")
        return

    evidence = list_active_evidence_for_topic(conn, topic_id)

    if source_id is not None:
        evidence = [e for e in evidence if e.source_id == source_id]

    if include_unsupported:
        pass  # active evidence already filtered; unsupported flag only affects run-level queries

    typer.echo(f"Active evidence for topic {topic_id}:")
    typer.echo(f"{'ID':>6}  {'Type':<12}  {'Support':<12}  {'Review':>7}  Claim")
    typer.echo("-" * 80)
    for e in evidence:
        review = "YES" if e.requires_date_review else "no"
        typer.echo(
            f"{e.claim_id:>6}  {e.claim_type.value:<12}  "
            f"{e.quote_support_status.value:<12}  {review:>7}  {e.claim_text[:50]}"
        )
    typer.echo(f"\n{len(evidence)} active evidence claim(s).")


@sources_app.command("claim-runs")
def sources_claim_runs(
    source_id: Annotated[int, typer.Argument(help="Source ID.")],
    include_superseded: Annotated[
        bool,
        typer.Option("--include-superseded", help="Show superseded runs as well."),
    ] = False,
) -> None:
    """Show claim extraction run history for SOURCE_ID."""
    from app.core.repository import get_source
    from app.research.repository import get_latest_source_content, list_claim_extraction_runs

    conn = _get_db()

    if get_source(conn, source_id) is None:
        typer.echo(f"Error: Source {source_id} not found.", err=True)
        raise typer.Exit(1)

    sc = get_latest_source_content(conn, source_id, require_successful=False)
    if sc is None:
        typer.echo(f"Source {source_id} has no content records.")
        return

    runs = list_claim_extraction_runs(conn, sc.id, include_superseded=include_superseded)
    if not runs:
        typer.echo(f"No claim extraction runs for source {source_id}.")
        return

    hdr = f"{'ID':>6}  {'Status':<10}  {'Claims':>6}  {'Chunks':>6}  {'Superseded':>10}  Started"
    typer.echo(hdr)
    typer.echo("-" * 70)
    for r in runs:
        sup = r.superseded_at[:10] if r.superseded_at else "-"
        claims = r.accepted_claim_count if r.accepted_claim_count is not None else "-"
        typer.echo(
            f"{r.id:>6}  {r.status.value:<10}  {str(claims):>6}  "
            f"{r.total_chunk_count:>6}  {sup:>10}  {r.started_at[:19]}"
        )


# ---------------------------------------------------------------------------
# Script commands
# ---------------------------------------------------------------------------

scripts_app = typer.Typer(help="Manage scripts.", no_args_is_help=True)
app.add_typer(scripts_app, name="scripts")


@scripts_app.command("add")
def scripts_add(
    topic_id: Annotated[int, typer.Argument(help="Parent topic ID.")],
    body: Annotated[str, typer.Argument(help="Script body text.")],
) -> None:
    """Add a new script version for a topic."""
    conn = _get_db()
    version = next_script_version(conn, topic_id)
    s = create_script(conn, Script(topic_id=topic_id, version=version, body=body))
    typer.echo(f"Created script id={s.id} topic_id={topic_id} version={version}")


@scripts_app.command("list")
def scripts_list(
    topic_id: Annotated[int, typer.Argument(help="Topic ID.")],
) -> None:
    """List scripts for a topic."""
    conn = _get_db()
    rows = list_scripts(conn, topic_id)
    if not rows:
        typer.echo("No scripts found.")
        return
    for s in rows:
        preview = s.body[:60].replace("\n", " ")
        typer.echo(f"[{s.id}] v{s.version}  status={s.status.value}  {preview!r}")


@scripts_app.command("generate")
def scripts_generate(
    topic_id: Annotated[int, typer.Argument(help="Topic ID to generate a script for.")],
    allow_no_evidence: Annotated[
        bool,
        typer.Option("--allow-no-evidence", help="Generate even when no evidence exists."),
    ] = False,
    replace: Annotated[
        int | None,
        typer.Option("--replace", help="Generation run ID to supersede with this run."),
    ] = None,
    tone: Annotated[str, typer.Option("--tone", help="Script tone.")] = "conversational",
    audience: Annotated[str, typer.Option("--audience", help="Target audience description.")] = "",
    target_duration: Annotated[
        int, typer.Option("--target-duration", help="Target duration in seconds.")
    ] = 45,
    prompt_version: Annotated[
        str, typer.Option("--prompt-version", help="Prompt version to use.")
    ] = "1",
) -> None:
    """Generate a script for TOPIC_ID using active evidence and an LLM."""
    from app.content.errors import NoActiveEvidenceError, ScriptGenerationError
    from app.content.generator import generate_script
    from app.core.repository import get_topic
    from app.research.errors import AIError
    from app.research.extractor import _build_provider
    from app.research.repository import list_active_evidence_for_topic

    cfg = get_config()
    configure_logging(cfg.log_level)
    conn = _get_db()

    topic = get_topic(conn, topic_id)
    if topic is None:
        typer.echo(f"Error: Topic {topic_id} not found.", err=True)
        raise typer.Exit(1)

    evidence = list_active_evidence_for_topic(conn, topic_id)

    provider = _build_provider(cfg)

    try:
        result = generate_script(
            conn,
            provider,
            topic,
            evidence,
            allow_no_evidence=allow_no_evidence,
            replace_run_id=replace,
            target_duration_s=target_duration,
            tone=tone,
            audience=audience,
            prompt_version=prompt_version,
        )
    except NoActiveEvidenceError as exc:
        typer.echo(f"Error: {exc}", err=True)
        typer.echo("Tip: use --allow-no-evidence to generate without evidence.", err=True)
        raise typer.Exit(1) from None
    except (ScriptGenerationError, AIError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from None

    if result.was_idempotent:
        typer.echo(f"Idempotent: returning existing run {result.run_id}.")
    typer.echo(
        f"Script {result.script_id} generated  "
        f"(run {result.run_id}, {result.word_count} words, ~{result.duration_s}s)"
    )
    if result.warnings:
        for w in result.warnings:
            typer.echo(f"  Warning: {w}", err=True)


@scripts_app.command("approve")
def scripts_approve(
    script_id: Annotated[int, typer.Argument(help="Script ID to approve.")],
) -> None:
    """Approve a script (atomically supersedes the previous approved script)."""
    from app.core.repository import approve_script, get_script

    conn = _get_db()
    if get_script(conn, script_id) is None:
        typer.echo(f"Script {script_id} not found.", err=True)
        raise typer.Exit(1)
    approve_script(conn, script_id)
    typer.echo(f"Script {script_id} approved.")


@scripts_app.command("reject")
def scripts_reject(
    script_id: Annotated[int, typer.Argument(help="Script ID to reject.")],
) -> None:
    """Mark a script as rejected."""
    conn = _get_db()
    s = update_script_status(conn, script_id, ScriptStatus.rejected)
    if s is None:
        typer.echo(f"Script {script_id} not found.", err=True)
        raise typer.Exit(1)
    typer.echo(f"Script {script_id} rejected.")


@scripts_app.command("show")
def scripts_show(
    script_id: Annotated[int, typer.Argument(help="Script ID to display.")],
) -> None:
    """Display full details and body of a script."""
    import json as _json

    from app.content.schemas import GeneratedScript
    from app.core.repository import get_script

    conn = _get_db()
    s = get_script(conn, script_id)
    if s is None:
        typer.echo(f"Script {script_id} not found.", err=True)
        raise typer.Exit(1)

    typer.echo(f"Script {s.id}  v{s.version}  [{s.status.value}]  topic={s.topic_id}")
    if s.approved_at:
        typer.echo(f"  Approved at:   {s.approved_at}")
    if s.superseded_at:
        typer.echo(f"  Superseded at: {s.superseded_at}")
    typer.echo("")

    if s.body_json:
        try:
            gs = GeneratedScript.model_validate(_json.loads(s.body_json))
            typer.echo(f"Title: {gs.title}")
            typer.echo("")
            for sec in gs.sections:
                typer.echo(f"[{sec.section_type.upper()}]")
                typer.echo(sec.text)
                if sec.cited_claim_ids:
                    typer.echo(f"  Citations: {sec.cited_claim_ids}")
                typer.echo("")
        except Exception:
            typer.echo(s.body)
    else:
        typer.echo(s.body)


@scripts_app.command("runs")
def scripts_runs(
    topic_id: Annotated[int, typer.Argument(help="Topic ID.")],
) -> None:
    """List script generation runs for a topic."""
    from app.content.repository import list_generation_runs

    conn = _get_db()
    runs = list_generation_runs(conn, topic_id)
    if not runs:
        typer.echo(f"No generation runs for topic {topic_id}.")
        return

    typer.echo(f"{'ID':>6}  {'Status':<10}  {'Script':>7}  {'Words':>5}  {'Dur':>4}  Started")
    typer.echo("-" * 70)
    for r in runs:
        sup = " [superseded]" if r.superseded_at else ""
        words = str(r.computed_word_count) if r.computed_word_count is not None else "-"
        dur = f"{r.computed_duration_s}s" if r.computed_duration_s is not None else "-"
        script_col = str(r.script_id) if r.script_id else "-"
        typer.echo(
            f"{r.id:>6}  {r.status.value:<10}  {script_col:>7}"
            f"  {words:>5}  {dur:>4}  {r.started_at[:19]}{sup}"
        )


@scripts_app.command("citations")
def scripts_citations(
    script_id: Annotated[int, typer.Argument(help="Script ID.")],
) -> None:
    """List citations for a script."""
    from app.content.repository import list_citations
    from app.core.repository import get_script

    conn = _get_db()
    if get_script(conn, script_id) is None:
        typer.echo(f"Script {script_id} not found.", err=True)
        raise typer.Exit(1)

    cits = list_citations(conn, script_id)
    if not cits:
        typer.echo(f"No citations for script {script_id}.")
        return

    typer.echo(f"{'Order':>5}  {'Section':>7}  ClaimID")
    typer.echo("-" * 30)
    for c in cits:
        typer.echo(f"{c.citation_order:>5}  {c.section_index:>7}  {c.claim_id}")


# ---------------------------------------------------------------------------
# Run commands
# ---------------------------------------------------------------------------

runs_app = typer.Typer(help="Manage production runs.", no_args_is_help=True)
app.add_typer(runs_app, name="runs")


@runs_app.command("create")
def runs_create(
    topic_id: Annotated[int, typer.Argument(help="Topic ID for the run.")],
    script_id: Annotated[
        int | None, typer.Option("--script-id", help="Optional script ID.")
    ] = None,
) -> None:
    """Create a new run for a topic."""
    conn = _get_db()
    r = create_run(conn, Run(topic_id=topic_id, script_id=script_id))
    typer.echo(f"Created run id={r.id} topic_id={topic_id} status={r.status.value}")


@runs_app.command("list")
def runs_list(
    topic_id: Annotated[int, typer.Argument(help="Topic ID.")],
) -> None:
    """List runs for a topic."""
    conn = _get_db()
    rows = list_runs(conn, topic_id)
    if not rows:
        typer.echo("No runs found.")
        return
    for r in rows:
        typer.echo(f"[{r.id}]  status={r.status.value}  script_id={r.script_id}")


@runs_app.command("update-status")
def runs_update_status(
    run_id: Annotated[int, typer.Argument(help="Run ID.")],
    status: Annotated[str, typer.Argument(help="New status: pending|running|completed|failed.")],
    error: Annotated[str | None, typer.Option("--error", help="Error message.")] = None,
) -> None:
    """Update the status of a run."""
    from app.core.models import RunStatus

    try:
        s = RunStatus(status)
    except ValueError:
        typer.echo(f"Invalid status {status!r}.", err=True)
        raise typer.Exit(1) from None
    conn = _get_db()
    r = update_run_status(conn, run_id, s, error=error)
    if r is None:
        typer.echo(f"Run {run_id} not found.", err=True)
        raise typer.Exit(1)
    typer.echo(f"Run {run_id} status={r.status.value}")


# ---------------------------------------------------------------------------
# AI commands (Phase 2)
# ---------------------------------------------------------------------------

ai_app = typer.Typer(help="AI provider diagnostics.", no_args_is_help=True)
app.add_typer(ai_app, name="ai")

ai_prompts_app = typer.Typer(help="Inspect the prompt registry.", no_args_is_help=True)
ai_app.add_typer(ai_prompts_app, name="prompts")


@ai_prompts_app.command("list")
def ai_prompts_list() -> None:
    """List all available versioned prompts."""
    from app.ai.registry import PromptRegistry

    registry = PromptRegistry()
    prompts = registry.list_all()
    if not prompts:
        typer.echo("No prompts found.")
        return
    for p in prompts:
        typer.echo(f"{p.name}  v{p.version}  —  {p.description}")


@ai_prompts_app.command("show")
def ai_prompts_show(
    name: Annotated[str, typer.Argument(help="Prompt name.")],
    version: Annotated[str, typer.Argument(help="Prompt version.")] = "1",
) -> None:
    """Show metadata and content for a specific prompt version."""
    from app.ai.errors import PromptMetadataError, PromptNotFoundError
    from app.ai.registry import PromptRegistry

    registry = PromptRegistry()
    try:
        p = registry.get(name, version)
    except PromptNotFoundError:
        typer.echo(f"Prompt {name!r} v{version} not found.", err=True)
        raise typer.Exit(1) from None
    except PromptMetadataError as exc:
        typer.echo(f"Prompt metadata error: {exc}", err=True)
        raise typer.Exit(1) from None

    typer.echo(f"Name:        {p.name}")
    typer.echo(f"Version:     {p.version}")
    typer.echo(f"Description: {p.description}")
    typer.echo("")
    typer.echo("── System ──")
    typer.echo(p.system.strip())
    typer.echo("")
    typer.echo("── User template ──")
    typer.echo(p.user_template.strip())


@ai_app.command("demo")
def ai_demo(
    text: Annotated[
        str, typer.Option("--text", "-t", help="Text to echo.")
    ] = "Hello from Phase 2!",
    live: Annotated[
        bool,
        typer.Option(
            "--live", help="Use the live Claude provider (requires ACE_ANTHROPIC_API_KEY)."
        ),  # noqa: E501
    ] = False,
) -> None:
    """Run the demo-echo prompt and display the structured result.

    Uses the fake provider by default.  Pass --live to use Claude (credentials
    required; live API calls are not verified during Phase 2).
    """
    from datetime import UTC, datetime

    from app.ai.errors import AIError
    from app.ai.fake import FakeProvider
    from app.ai.registry import PromptRegistry
    from app.ai.schemas import EchoOutput
    from app.ai.usage import record_ai_call

    cfg = get_config()
    configure_logging(cfg.log_level)

    if live and not cfg.dry_run:
        if not cfg.anthropic_api_key:
            typer.echo("Error: ACE_ANTHROPIC_API_KEY is not set. Cannot use --live mode.", err=True)
            raise typer.Exit(1)
        typer.echo(
            "Note: --live mode is implemented but live API calls are not executed "
            "during Phase 2 verification. Use the fake provider instead.",
            err=True,
        )
        raise typer.Exit(1)

    registry = PromptRegistry()
    prompt = registry.get("demo-echo", "1")
    user = prompt.format_user(text=text)

    from app.ai.provider import AIRequest

    request = AIRequest(
        system=prompt.system,
        user=user,
        model="fake",
        response_schema=EchoOutput,
        prompt_name=prompt.name,
        prompt_version=prompt.version,
    )

    import json as _json

    provider = FakeProvider(output=_json.dumps({"echo": text}))
    started = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")

    try:
        response = provider.complete(request)
        status = "success"
        error_cat = None
        error_msg = None
    except AIError as exc:
        typer.echo(f"AI error: {exc}", err=True)
        conn = _get_db()
        record_ai_call(
            conn,
            request,
            None,
            status="failed",
            error_category=type(exc).__name__,
            error_message=str(exc),
            started_at=started,
        )
        raise typer.Exit(1) from None

    conn = _get_db()
    call_id = record_ai_call(
        conn,
        request,
        response,
        status=status,
        error_category=error_cat,
        error_message=error_msg,
        started_at=started,
    )

    typer.echo(f"Provider:      {response.provider_name}")
    typer.echo(f"Model:         {response.model}")
    typer.echo(f"Prompt:        {prompt.name} v{prompt.version}")
    typer.echo(f"Input tokens:  {response.input_tokens}")
    typer.echo(f"Output tokens: {response.output_tokens}")
    typer.echo(f"Duration:      {response.duration_ms} ms")
    typer.echo(f"Retries:       {response.retry_count}")
    typer.echo(f"Usage id:      {call_id}")
    typer.echo("")
    if response.parsed is not None:
        typer.echo(f"Result: {response.parsed.model_dump_json(indent=2)}")
    else:
        typer.echo(f"Raw: {response.raw_text}")


# ---------------------------------------------------------------------------
# Production plan commands (Phase 6 M6.1)
# ---------------------------------------------------------------------------

production_app = typer.Typer(help="Manage production plans.", no_args_is_help=True)
app.add_typer(production_app, name="production")


@production_app.command("plan")
def production_plan(
    topic_id: Annotated[int, typer.Argument(help="Topic ID.")],
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show the plan without persisting it."),
    ] = False,
) -> None:
    """Build a production plan from the active approved script for TOPIC_ID."""
    from app.content.repository import get_active_approved_generated_script
    from app.production.renderer import build_production_plan, plan_draft_to_json_summary
    from app.production.repository import get_or_create_production_plan

    conn = _get_db()
    approved = get_active_approved_generated_script(conn, topic_id)
    if approved is None:
        typer.echo(f"No active approved script for topic_id={topic_id}.", err=True)
        raise typer.Exit(1)

    draft = build_production_plan(approved)

    if dry_run:
        typer.echo(plan_draft_to_json_summary(draft))
        return

    plan, created = get_or_create_production_plan(conn, draft)
    conn.commit()
    if created:
        typer.echo(f"Created production plan id={plan.id} topic_id={topic_id} status={plan.status}")
    else:
        typer.echo(
            f"Production plan already exists id={plan.id} topic_id={topic_id} status={plan.status}"
        )
    typer.echo(f"  segments={len(plan.title and [1] or [])}  input_hash={plan.input_hash[:12]}…")


@production_app.command("show")
def production_show(
    plan_id: Annotated[int, typer.Argument(help="Production plan ID.")],
) -> None:
    """Show a production plan and its segments."""
    from app.production.repository import (
        get_production_plan_by_id,
        get_production_segments,
    )

    conn = _get_db()
    plan = get_production_plan_by_id(conn, plan_id)
    if plan is None:
        typer.echo(f"Production plan id={plan_id} not found.", err=True)
        raise typer.Exit(1)

    typer.echo(
        f"Plan id={plan.id}  status={plan.status}"
        f"  topic={plan.topic_id}  script={plan.script_id} v{plan.script_version}"
    )
    typer.echo(f"  title={plan.title!r}  format={plan.format}")
    typer.echo(f"  duration={plan.total_estimated_duration_s}s  words={plan.total_word_count}")
    typer.echo(f"  input_hash={plan.input_hash[:16]}…")
    if plan.warnings:
        typer.echo(f"  warnings={plan.warnings}")

    segments = get_production_segments(conn, plan.id)
    typer.echo(f"  segments ({len(segments)}):")
    for seg in segments:
        typer.echo(
            f"    [{seg.segment_index}] {seg.section_type}  {seg.estimated_duration_s}s"
            f"  {seg.narration_text[:60]!r}"
        )


@production_app.command("list")
def production_list(
    topic_id: Annotated[int, typer.Argument(help="Topic ID.")],
) -> None:
    """List production plans for a topic."""
    from app.production.repository import list_production_plans

    conn = _get_db()
    plans = list_production_plans(conn, topic_id)
    if not plans:
        typer.echo("No production plans found.")
        return
    for plan in plans:
        active = " [active]" if plan.status == "approved" and plan.superseded_at is None else ""
        typer.echo(f"[{plan.id}] status={plan.status}{active}  created={plan.created_at}")


@production_app.command("approve")
def production_approve(
    plan_id: Annotated[int, typer.Argument(help="Production plan ID.")],
    actor: Annotated[str | None, typer.Option("--actor", help="Reviewer name.")] = None,
    model: Annotated[str | None, typer.Option("--model", help="Model used for review.")] = None,
    prompt_hash: Annotated[
        str | None, typer.Option("--prompt-hash", help="Prompt hash used for review.")
    ] = None,
) -> None:
    """Approve a draft production plan."""
    from app.production.errors import IllegalTransitionError, NoPlanError
    from app.production.repository import approve_production_plan

    conn = _get_db()
    try:
        plan = approve_production_plan(
            conn, plan_id, actor=actor, model=model, prompt_hash=prompt_hash
        )
        conn.commit()
        typer.echo(
            f"Approved plan id={plan.id}  topic={plan.topic_id}  approved_at={plan.approved_at}"
        )
    except NoPlanError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    except IllegalTransitionError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


@production_app.command("reject")
def production_reject(
    plan_id: Annotated[int, typer.Argument(help="Production plan ID.")],
    reason: Annotated[str, typer.Argument(help="Rejection reason code.")],
    notes: Annotated[str | None, typer.Option("--notes", help="Additional notes.")] = None,
    actor: Annotated[str | None, typer.Option("--actor", help="Reviewer name.")] = None,
    model: Annotated[str | None, typer.Option("--model", help="Model used for review.")] = None,
    prompt_hash: Annotated[
        str | None, typer.Option("--prompt-hash", help="Prompt hash used for review.")
    ] = None,
) -> None:
    """Reject a draft production plan.

    REASON must be one of: segment_structure, narration_wording, pacing,
    duration, citation_mapping, evidence_concern, format_mismatch, other.
    (Requires --notes when reason is 'other'.)
    """
    from app.production.errors import (
        IllegalTransitionError,
        InvalidReasonCodeError,
        NoPlanError,
    )
    from app.production.repository import reject_production_plan

    conn = _get_db()
    try:
        plan = reject_production_plan(
            conn,
            plan_id,
            reason_code=reason,
            notes=notes,
            actor=actor,
            model=model,
            prompt_hash=prompt_hash,
        )
        conn.commit()
        typer.echo(f"Rejected plan id={plan.id}  reason={reason}  rejected_at={plan.rejected_at}")
    except (NoPlanError, IllegalTransitionError, InvalidReasonCodeError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


@production_app.command("feedback")
def production_feedback(
    plan_id: Annotated[int, typer.Argument(help="Production plan ID.")],
) -> None:
    """Show review events (feedback history) for a production plan."""
    from app.production.repository import (
        get_production_plan_by_id,
        list_production_plan_review_events,
    )

    conn = _get_db()
    plan = get_production_plan_by_id(conn, plan_id)
    if plan is None:
        typer.echo(f"Production plan id={plan_id} not found.", err=True)
        raise typer.Exit(1)

    events = list_production_plan_review_events(conn, plan_id)
    if not events:
        typer.echo(f"No review events for plan id={plan_id}.")
        return
    for ev in events:
        code = f"  reason={ev.reason_code}" if ev.reason_code else ""
        notes_str = f"  notes={ev.notes!r}" if ev.notes else ""
        actor_str = f"  actor={ev.actor}" if ev.actor else ""
        typer.echo(f"[{ev.id}] {ev.decision}{code}{notes_str}{actor_str}  {ev.created_at}")


# ---------------------------------------------------------------------------
# Narration commands
# ---------------------------------------------------------------------------

narration_app = typer.Typer(help="Manage audio narration.", no_args_is_help=True)
app.add_typer(narration_app, name="narration")


@narration_app.command("voices")
def narration_voices() -> None:
    """List voice profiles."""
    from app.narration.repository import list_voice_profiles

    conn = _get_db()
    profiles = list_voice_profiles(conn)
    if not profiles:
        typer.echo("No voice profiles found.")
        return
    for vp in profiles:
        default_flag = " [default]" if vp.is_default else ""
        typer.echo(f"[{vp.id}] {vp.name}  {vp.provider}/{vp.model}  {vp.language}{default_flag}")


@narration_app.command("add-voice")
def narration_add_voice(
    name: Annotated[str, typer.Argument(help="Human-readable name for this voice profile.")],
    provider: Annotated[str, typer.Option(help="TTS provider name (e.g. 'fake').")] = "fake",
    model: Annotated[str, typer.Option(help="Provider model identifier.")] = "fake/FAKE",
    voice_id: Annotated[str, typer.Option(help="Provider voice ID.")] = "fake-voice",
    language: Annotated[str, typer.Option(help="BCP-47 language tag.")] = "en-US",
    speaking_rate: Annotated[float, typer.Option(help="Speaking rate multiplier.")] = 1.0,
    is_default: Annotated[bool, typer.Option("--default", help="Set as default voice.")] = False,
) -> None:
    """Register a new voice profile."""
    from app.narration.models import VoiceProfileCreate
    from app.narration.repository import create_voice_profile

    conn = _get_db()
    vpc = VoiceProfileCreate(
        channel_id=None,
        provider=provider,
        model=model,
        voice_id=voice_id,
        name=name,
        language=language,
        speaking_rate=speaking_rate,
        style=None,
        stability=None,
        similarity_boost=None,
        settings_json="{}",
        is_default=is_default,
    )
    vp = create_voice_profile(conn, vpc)
    conn.commit()
    typer.echo(f"Created voice profile id={vp.id}  {vp.name}")


@narration_app.command("narrate")
def narration_narrate(
    topic_id: Annotated[int, typer.Argument(help="Topic ID to narrate.")],
    voice_profile_id: Annotated[int, typer.Option("--voice", help="Voice profile ID.")] = 1,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Plan without synthesising audio.")
    ] = False,
) -> None:
    """Synthesise audio for the active production plan of TOPIC_ID."""
    from app.narration.fake import FakeTTSProvider
    from app.narration.orchestrator import narrate_plan
    from app.production.repository import get_approved_production_plan_full

    cfg = get_config()
    conn = _get_db()

    plan = get_approved_production_plan_full(conn, topic_id)
    if plan is None:
        typer.echo(f"No approved production plan for topic_id={topic_id}.", err=True)
        raise typer.Exit(1)

    provider = FakeTTSProvider()
    result = narrate_plan(
        conn,
        plan_id=plan.plan_id,
        plan_input_hash=plan.input_hash,
        voice_profile_id=voice_profile_id,
        artifacts_path=cfg.artifacts_path,
        provider=provider,
        dry_run=dry_run,
    )
    conn.commit()

    if dry_run:
        typer.echo(f"Dry-run: {len(result.skipped_segment_ids)} segment(s) would be synthesised.")
        return

    typer.echo(f"Run id={result.run_id}: synthesised {len(result.assets)} segment(s).")


@narration_app.command("runs")
def narration_runs(
    topic_id: Annotated[int, typer.Argument(help="Topic ID.")],
) -> None:
    """List narration runs for the active production plan of TOPIC_ID."""
    from app.narration.repository import list_narration_runs
    from app.production.repository import get_approved_production_plan_full

    conn = _get_db()
    plan = get_approved_production_plan_full(conn, topic_id)
    if plan is None:
        typer.echo(f"No approved production plan for topic_id={topic_id}.", err=True)
        raise typer.Exit(1)

    runs = list_narration_runs(conn, plan_id=plan.plan_id)
    if not runs:
        typer.echo("No narration runs found.")
        return
    for run in runs:
        typer.echo(f"[{run.id}] status={run.status}  created={run.created_at}")


@narration_app.command("approve")
def narration_approve(
    run_id: Annotated[int, typer.Argument(help="Narration run ID to approve.")],
    actor: Annotated[str | None, typer.Option(help="Actor identifier.")] = None,
    notes: Annotated[str | None, typer.Option(help="Approval notes.")] = None,
) -> None:
    """Approve a completed narration run."""
    from app.narration.repository import approve_narration_run

    conn = _get_db()
    run = approve_narration_run(conn, run_id, actor=actor, notes=notes)
    conn.commit()
    typer.echo(f"Run id={run.id} approved.")


@narration_app.command("reject-run")
def narration_reject_run(
    run_id: Annotated[int, typer.Argument(help="Narration run ID to reject.")],
    reason_code: Annotated[str | None, typer.Option(help="Rejection reason code.")] = None,
    notes: Annotated[str | None, typer.Option(help="Rejection notes.")] = None,
    actor: Annotated[str | None, typer.Option(help="Actor identifier.")] = None,
) -> None:
    """Reject a completed narration run."""
    from app.narration.repository import reject_narration_run

    conn = _get_db()
    run = reject_narration_run(conn, run_id, reason_code=reason_code, notes=notes, actor=actor)
    conn.commit()
    typer.echo(f"Run id={run.id} rejected.")


@narration_app.command("reject-segment")
def narration_reject_segment(
    asset_id: Annotated[int, typer.Argument(help="Segment asset ID to reject.")],
    reason_code: Annotated[str, typer.Option(help="Rejection reason code.")] = "other",
    notes: Annotated[str | None, typer.Option(help="Rejection notes.")] = None,
    actor: Annotated[str | None, typer.Option(help="Actor identifier.")] = None,
) -> None:
    """Reject a synthesised segment asset."""
    from app.narration.repository import reject_narration_segment_asset

    conn = _get_db()
    asset = reject_narration_segment_asset(
        conn, asset_id, reason_code=reason_code, notes=notes, actor=actor
    )
    conn.commit()
    typer.echo(f"Asset id={asset.id} rejected (reason={reason_code}).")


@narration_app.command("regenerate-segment")
def narration_regenerate_segment(
    asset_id: Annotated[int, typer.Argument(help="Rejected segment asset ID to regenerate.")],
    actor: Annotated[str | None, typer.Option(help="Actor identifier.")] = None,
) -> None:
    """Regenerate a rejected segment asset."""
    from app.narration.fake import FakeTTSProvider
    from app.narration.orchestrator import regenerate_segment

    cfg = get_config()
    conn = _get_db()
    provider = FakeTTSProvider()
    replacement = regenerate_segment(
        conn,
        source_asset_id=asset_id,
        artifacts_path=cfg.artifacts_path,
        provider=provider,
        actor=actor,
    )
    typer.echo(f"Regenerated: new asset id={replacement.id} status={replacement.status}.")


@narration_app.command("events")
def narration_events(
    run_id: Annotated[int, typer.Argument(help="Narration run ID.")],
) -> None:
    """List review events for a narration run."""
    from app.narration.repository import list_narration_review_events

    conn = _get_db()
    events = list_narration_review_events(conn, run_id)
    if not events:
        typer.echo(f"No review events for run id={run_id}.")
        return
    for ev in events:
        seg_str = f"  segment={ev.segment_id}" if ev.segment_id else ""
        code_str = f"  reason={ev.reason_code}" if ev.reason_code else ""
        actor_str = f"  actor={ev.actor}" if ev.actor else ""
        typer.echo(f"[{ev.id}] {ev.event_type}{seg_str}{code_str}{actor_str}  {ev.created_at}")


# ---------------------------------------------------------------------------
# Captions — Phase 6 M6.3A
# ---------------------------------------------------------------------------

captions_app = typer.Typer(help="Manage caption and timing artifacts.", no_args_is_help=True)
app.add_typer(captions_app, name="captions")


@captions_app.command("generate")
def captions_generate(
    plan_id: Annotated[int, typer.Option("--plan-id", "-p", help="Production plan ID.")],
    artifacts_path: Annotated[
        str, typer.Option("--artifacts-path", help="Root path for stored artifacts.")
    ] = "artifacts",
    language: Annotated[str, typer.Option("--language", help="BCP-47 language tag.")] = "en-US",
    experiment_id: Annotated[
        str | None, typer.Option("--experiment-id", help="Experiment label (optional).")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Validate without writing to DB or filesystem.")
    ] = False,
) -> None:
    """Generate SRT/VTT/JSON captions for an approved narration run.

    With --dry-run, loads and validates the narration handoff, segments and
    times all cues in memory, and reports the expected output — no DB writes
    and no files are created.
    """
    from pathlib import Path

    from app.captions.constants import (
        CAPTION_EXPORTER_VERSION,
        CAPTION_SCHEMA_VERSION,
        CAPTION_SEGMENTATION_VERSION,
        CAPTION_STYLE_VERSION,
        CAPTION_TIMING_ALGORITHM_VERSION,
    )
    from app.captions.errors import CaptionError
    from app.captions.hashing import NarrationSegmentHashInput, compute_caption_input_hash
    from app.captions.orchestrator import _build_cues_for_segment, generate_captions
    from app.captions.validation import validate_caption_cues
    from app.narration.repository import get_approved_narration_run_full

    if dry_run:
        conn = _get_db()
        try:
            handoff = get_approved_narration_run_full(conn, plan_id, experiment_id=experiment_id)
            if handoff is None:
                typer.echo(f"No active approved narration run for plan {plan_id}.", err=True)
                raise typer.Exit(1) from None
            seg_inputs = [
                NarrationSegmentHashInput(
                    segment_id=s.segment_id,
                    asset_id=s.asset_id,
                    audio_sha256=s.audio_sha256,
                    narration_text_hash=s.narration_text_hash,
                    duration_ms=s.duration_ms,
                )
                for s in handoff.segments
            ]
            input_hash = compute_caption_input_hash(
                narration_run_id=handoff.run_id,
                narration_run_input_hash=handoff.input_hash,
                segments=seg_inputs,
                caption_schema_version=CAPTION_SCHEMA_VERSION,
                segmentation_version=CAPTION_SEGMENTATION_VERSION,
                timing_algorithm_version=CAPTION_TIMING_ALGORITHM_VERSION,
                style_version=CAPTION_STYLE_VERSION,
                exporter_version=CAPTION_EXPORTER_VERSION,
                language=language,
                experiment_id=experiment_id,
            )
            all_cues = []
            offset = 0
            for seg in handoff.segments:
                seg_cues = _build_cues_for_segment(seg, offset, run_id=0)
                all_cues.extend(seg_cues)
                offset += len(seg_cues)
            seg_id_to_text = {s.segment_id: s.narration_text for s in handoff.segments}
            seg_id_to_dur = {s.segment_id: s.duration_ms for s in handoff.segments}
            seg_id_to_asset = {s.segment_id: s.asset_id for s in handoff.segments}
            seg_id_to_hash = {s.segment_id: s.narration_text_hash for s in handoff.segments}
            seg_id_to_audio = {s.segment_id: s.audio_sha256 for s in handoff.segments}
            result = validate_caption_cues(
                cues=all_cues,
                segment_id_to_narration_text=seg_id_to_text,
                segment_id_to_duration_ms=seg_id_to_dur,
                segment_id_to_asset_id=seg_id_to_asset,
                segment_id_to_text_hash=seg_id_to_hash,
                segment_id_to_audio_sha256=seg_id_to_audio,
            )
            if not result.ok:
                for err in result.errors:
                    typer.echo(f"  ERROR: {err}", err=True)
                raise typer.Exit(1) from None
            total_ms = sum(s.duration_ms for s in handoff.segments)
            typer.echo(
                f"[dry-run] plan={plan_id}  narration_run={handoff.run_id}"
                f"  cues={len(all_cues)}  duration_ms={total_ms}"
                f"  input_hash={input_hash[:12]}..."
            )
            typer.echo("  Validation passed — no writes performed.")
        except CaptionError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from None
        return

    conn = _get_db()
    try:
        run = generate_captions(
            conn,
            plan_id=plan_id,
            artifacts_path=Path(artifacts_path),
            language=language,
            experiment_id=experiment_id,
        )
    except CaptionError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None
    typer.echo(
        f"Caption run id={run.id}  status={run.status}"
        f"  cues={run.total_cue_count}  duration_ms={run.total_duration_ms}"
    )
    if run.srt_path:
        typer.echo(f"  SRT  → {run.srt_path}")
    if run.vtt_path:
        typer.echo(f"  VTT  → {run.vtt_path}")
    if run.json_path:
        typer.echo(f"  JSON → {run.json_path}")


@captions_app.command("runs")
def captions_runs(
    plan_id: Annotated[int, typer.Argument(help="Production plan ID.")],
) -> None:
    """List caption runs for a plan (newest first).

    Shows all caption runs across all narration runs for the plan,
    distinguishing running, completed, approved, rejected, failed, and
    superseded approved runs.
    """
    from app.captions.repository import list_caption_runs_by_plan

    conn = _get_db()
    runs = list_caption_runs_by_plan(conn, plan_id)
    if not runs:
        typer.echo(f"No caption runs for plan id={plan_id}.")
        return
    for r in runs:
        sup = "  [superseded]" if r.superseded_at else ""
        exp = f"  exp={r.experiment_id}" if r.experiment_id else ""
        typer.echo(
            f"[{r.id}] status={r.status}  cues={r.total_cue_count}"
            f"  duration_ms={r.total_duration_ms}{exp}{sup}"
        )


@captions_app.command("show")
def captions_show(
    run_id: Annotated[int, typer.Argument(help="Caption run ID.")],
) -> None:
    """Show detailed metadata, cue timeline, and review status for a caption run."""
    from app.captions.repository import (
        get_caption_cues,
        get_caption_run,
        list_caption_review_events,
    )

    conn = _get_db()
    run = get_caption_run(conn, run_id)
    if run is None:
        typer.echo(f"Caption run {run_id} not found.", err=True)
        raise typer.Exit(1) from None

    typer.echo(f"Caption run [{run.id}]")
    typer.echo(f"  status          : {run.status}")
    typer.echo(f"  narration_run   : {run.narration_run_id}")
    typer.echo(f"  plan            : {run.plan_id}")
    typer.echo(f"  script          : {run.script_id}")
    typer.echo(f"  topic           : {run.topic_id}")
    typer.echo(f"  experiment      : {run.experiment_id or '(none)'}")
    typer.echo(f"  language        : {run.language}")
    typer.echo(f"  total_cues      : {run.total_cue_count}")
    typer.echo(f"  duration_ms     : {run.total_duration_ms}")
    typer.echo(f"  created_at      : {run.created_at}")
    typer.echo(f"  updated_at      : {run.updated_at}")
    if run.approved_at:
        typer.echo(f"  approved_at     : {run.approved_at}")
    if run.rejected_at:
        typer.echo(f"  rejected_at     : {run.rejected_at}")
    if run.superseded_at:
        typer.echo(f"  superseded_at   : {run.superseded_at}")
        typer.echo(f"  superseded_by   : {run.superseded_by_run_id}")
    if run.failure_reason:
        typer.echo(f"  failure_reason  : {run.failure_reason}")
    typer.echo("  --- versions ---")
    typer.echo(f"  schema          : {run.caption_schema_version}")
    typer.echo(f"  segmentation    : {run.segmentation_version}")
    typer.echo(f"  timing          : {run.timing_algorithm_version}")
    typer.echo(f"  style           : {run.style_version}")
    typer.echo(f"  exporter        : {run.exporter_version}")
    typer.echo(f"  input_hash      : {run.input_hash}")
    typer.echo("  --- exports ---")
    typer.echo(f"  SRT   path      : {run.srt_path or '(none)'}")
    typer.echo(f"  SRT   sha256    : {run.srt_sha256 or '(none)'}")
    typer.echo(f"  VTT   path      : {run.vtt_path or '(none)'}")
    typer.echo(f"  VTT   sha256    : {run.vtt_sha256 or '(none)'}")
    typer.echo(f"  JSON  path      : {run.json_path or '(none)'}")
    typer.echo(f"  JSON  sha256    : {run.json_sha256 or '(none)'}")

    # Review-blocking cue rejections
    events = list_caption_review_events(conn, run_id)
    rejections = [ev for ev in events if ev.event_type == "cue_rejected"]
    if rejections:
        typer.echo(f"  *** {len(rejections)} cue rejection(s) — approval is blocked ***")
        for ev in rejections:
            typer.echo(f"    cue {ev.cue_id}  reason={ev.reason_code}  sev={ev.severity}")

    # Cue timeline
    cues = get_caption_cues(conn, run_id)
    if not cues:
        typer.echo("  (no cues)")
        return
    typer.echo(f"  --- cues ({len(cues)}) ---")
    for cue in cues:
        warn_str = "  !" + ",".join(cue.warnings) if cue.warnings else ""
        typer.echo(
            f"  [{cue.cue_index:>3}] seg={cue.segment_id}"
            f"  asset={cue.narration_asset_id}"
            f"  {cue.start_ms}–{cue.end_ms}ms"
            f"  src={cue.timing_source}"
            f"{warn_str}"
        )
        for line in cue.lines:
            typer.echo(f"         {line!r}")


@captions_app.command("approve")
def captions_approve(
    run_id: Annotated[int, typer.Argument(help="Caption run ID to approve.")],
    actor: Annotated[str | None, typer.Option("--actor", help="Reviewer name.")] = None,
    notes: Annotated[str | None, typer.Option("--notes", help="Approval notes.")] = None,
) -> None:
    """Approve a completed caption run."""
    from app.captions.errors import CaptionError
    from app.captions.repository import approve_caption_run

    conn = _get_db()
    try:
        run = approve_caption_run(conn, run_id, actor=actor, notes=notes)
        conn.commit()
    except CaptionError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None
    typer.echo(f"Caption run {run_id} approved at {run.approved_at}.")


@captions_app.command("reject-run")
def captions_reject_run(
    run_id: Annotated[int, typer.Argument(help="Caption run ID to reject.")],
    reason_code: Annotated[
        str | None, typer.Option("--reason-code", "-r", help="Rejection reason code.")
    ] = None,
    notes: Annotated[str | None, typer.Option("--notes", help="Rejection notes.")] = None,
    severity: Annotated[int | None, typer.Option("--severity", help="Severity 1–5.")] = None,
    actor: Annotated[str | None, typer.Option("--actor", help="Reviewer name.")] = None,
) -> None:
    """Reject a completed or approved caption run."""
    from app.captions.errors import CaptionError
    from app.captions.repository import reject_caption_run

    conn = _get_db()
    try:
        run = reject_caption_run(
            conn,
            run_id,
            reason_code=reason_code,
            notes=notes,
            severity=severity,
            actor=actor,
        )
        conn.commit()
    except CaptionError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None
    typer.echo(f"Caption run {run_id} rejected at {run.rejected_at}.")


@captions_app.command("reject-cue")
def captions_reject_cue(
    run_id: Annotated[int, typer.Argument(help="Caption run ID.")],
    cue_id: Annotated[int, typer.Argument(help="Caption cue ID.")],
    reason_code: Annotated[str, typer.Option("--reason-code", "-r", help="Rejection reason code.")],
    notes: Annotated[str | None, typer.Option("--notes", help="Rejection notes.")] = None,
    severity: Annotated[int | None, typer.Option("--severity", help="Severity 1–5.")] = None,
    actor: Annotated[str | None, typer.Option("--actor", help="Reviewer name.")] = None,
    expected_correction: Annotated[
        str | None, typer.Option("--correction", help="Expected correction text.")
    ] = None,
) -> None:
    """Flag a specific caption cue for rejection."""
    from app.captions.errors import CaptionError
    from app.captions.repository import record_cue_rejection

    conn = _get_db()
    try:
        event = record_cue_rejection(
            conn,
            run_id,
            cue_id,
            reason_code=reason_code,
            notes=notes,
            severity=severity,
            actor=actor,
            expected_correction=expected_correction,
        )
        conn.commit()
    except CaptionError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None
    typer.echo(
        f"Cue rejection recorded: event id={event.id}  reason={event.reason_code}  cue={cue_id}"
    )


@captions_app.command("export")
def captions_export(
    run_id: Annotated[int, typer.Argument(help="Caption run ID.")],
    artifacts_path: Annotated[
        str, typer.Option("--artifacts-path", help="Root path for stored artifacts.")
    ] = "artifacts",
    fmt: Annotated[
        str, typer.Option("--format", "-f", help="Format: srt, vtt, json, or all.")
    ] = "all",
) -> None:
    """Regenerate export files from canonical DB cues (repair or recreate exports).

    Reads canonical caption_cues rows, renders the requested format(s),
    writes through .tmp → atomic replace, recalculates SHA-256, and
    updates the stored metadata.  Never alters cue rows or run status.
    """
    from pathlib import Path

    from app.captions.errors import CaptionError
    from app.captions.exporters import render_json, render_srt, render_vtt
    from app.captions.repository import (
        get_caption_cues,
        require_caption_run,
        update_caption_run_export_metadata,
    )
    from app.captions.storage import (
        cleanup_stale_temp_files,
        compute_export_sha256,
        relative_artifact_path,
        resolve_artifacts_path,
        write_export_atomic,
    )
    from app.captions.storage import json_path as _json_path
    from app.captions.storage import srt_path as _srt_path
    from app.captions.storage import vtt_path as _vtt_path

    valid_formats = {"srt", "vtt", "json", "all"}
    if fmt not in valid_formats:
        typer.echo(f"Unknown format {fmt!r}. Choose from: srt, vtt, json, all.", err=True)
        raise typer.Exit(1) from None

    conn = _get_db()
    try:
        run = require_caption_run(conn, run_id)
    except CaptionError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None

    arts = resolve_artifacts_path(Path(artifacts_path))
    cleanup_stale_temp_files(arts, max_age_seconds=3600)

    cues = get_caption_cues(conn, run_id)

    do_srt = fmt in ("srt", "all")
    do_vtt = fmt in ("vtt", "all")
    do_json = fmt in ("json", "all")

    kwargs: dict = {}
    try:
        if do_srt:
            srt_content = render_srt(cues)
            srt_dest = _srt_path(arts, run.plan_id, run.narration_run_id, run_id)
            write_export_atomic(srt_dest, srt_content)
            kwargs["srt_path"] = relative_artifact_path(arts, srt_dest)
            kwargs["srt_sha256"] = compute_export_sha256(srt_content)
            typer.echo(f"  SRT  → {kwargs['srt_path']}")

        if do_vtt:
            vtt_content = render_vtt(cues)
            vtt_dest = _vtt_path(arts, run.plan_id, run.narration_run_id, run_id)
            write_export_atomic(vtt_dest, vtt_content)
            kwargs["vtt_path"] = relative_artifact_path(arts, vtt_dest)
            kwargs["vtt_sha256"] = compute_export_sha256(vtt_content)
            typer.echo(f"  VTT  → {kwargs['vtt_path']}")

        if do_json:
            json_content = render_json(
                cues,
                caption_run_id=run.id,
                narration_run_id=run.narration_run_id,
                plan_id=run.plan_id,
                script_id=run.script_id,
                topic_id=run.topic_id,
                experiment_id=run.experiment_id,
                caption_schema_version=run.caption_schema_version,
                segmentation_version=run.segmentation_version,
                timing_algorithm_version=run.timing_algorithm_version,
                style_version=run.style_version,
                exporter_version=run.exporter_version,
                language=run.language,
            )
            json_dest = _json_path(arts, run.plan_id, run.narration_run_id, run_id)
            write_export_atomic(json_dest, json_content)
            kwargs["json_path"] = relative_artifact_path(arts, json_dest)
            kwargs["json_sha256"] = compute_export_sha256(json_content)
            typer.echo(f"  JSON → {kwargs['json_path']}")

        if kwargs:
            update_caption_run_export_metadata(conn, run_id, **kwargs)
            conn.commit()
    except CaptionError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None

    typer.echo(f"Export complete for caption run {run_id}.")


@captions_app.command("events")
def captions_events(
    run_id: Annotated[int, typer.Argument(help="Caption run ID.")],
) -> None:
    """List review events for a caption run."""
    from app.captions.repository import list_caption_review_events

    conn = _get_db()
    events = list_caption_review_events(conn, run_id)
    if not events:
        typer.echo(f"No review events for caption run id={run_id}.")
        return
    for ev in events:
        cue_str = f"  cue={ev.cue_id}" if ev.cue_id else ""
        code_str = f"  reason={ev.reason_code}" if ev.reason_code else ""
        actor_str = f"  actor={ev.actor}" if ev.actor else ""
        typer.echo(f"[{ev.id}] {ev.event_type}{cue_str}{code_str}{actor_str}  {ev.created_at}")


# ---------------------------------------------------------------------------
# Narration — ElevenLabs smoke test (M6.3C)
# ---------------------------------------------------------------------------


@narration_app.command("smoke-test")
def narration_smoke_test(
    voice_id: Annotated[
        str,
        typer.Option(
            "--voice-id",
            help="ElevenLabs voice ID to use (e.g. 'JBFqnCBsd6RMkjVDRZzb' for George).",
        ),
    ],
    model: Annotated[
        str,
        typer.Option(
            "--model",
            help="ElevenLabs model to use.",
        ),
    ] = "eleven_multilingual_v2",
    text: Annotated[
        str,
        typer.Option(
            "--text",
            help="Text to synthesise.",
        ),
    ] = "Hello from the AI Content Engine. ElevenLabs integration is working.",
    output: Annotated[
        str | None,
        typer.Option(
            "--output",
            "-o",
            help="Write the WAV bytes to this file (default: print summary only).",
        ),
    ] = None,
) -> None:
    """Manual ElevenLabs smoke test.  ALWAYS skipped in CI.

    Requires ACE_ELEVENLABS_API_KEY and ACE_TTS_LIVE_ENABLED=true to be set.
    This command is for manual verification after M6.3C is merged.

    Example:
        ACE_ELEVENLABS_API_KEY=... ACE_TTS_LIVE_ENABLED=true \\
            ace narration smoke-test --voice-id JBFqnCBsd6RMkjVDRZzb
    """
    from app.narration.errors import ProviderCredentialError, SynthesisError
    from app.narration.protocol import TTSRequest
    from app.narration.providers.elevenlabs import (
        ELEVENLABS_PROVIDER_NAME,
        ElevenLabsTTSProvider,
    )

    cfg = get_config()
    configure_logging(cfg.log_level)

    if not cfg.tts_live_enabled:
        typer.echo(
            "Smoke test skipped: ACE_TTS_LIVE_ENABLED is not set to true.\n"
            "To run live: export ACE_TTS_LIVE_ENABLED=true ACE_ELEVENLABS_API_KEY=<key>",
            err=True,
        )
        raise typer.Exit(0)

    if not cfg.elevenlabs_api_key:
        typer.echo("Error: ACE_ELEVENLABS_API_KEY is not set.", err=True)
        raise typer.Exit(1)

    provider = ElevenLabsTTSProvider()
    try:
        provider.initialize()
    except ProviderCredentialError as exc:
        typer.echo(f"Credential error: {exc}", err=True)
        raise typer.Exit(1) from None

    request = TTSRequest(
        text=text,
        provider=ELEVENLABS_PROVIDER_NAME,
        model=model,
        voice_id=voice_id,
        language="en",
        speaking_rate=1.0,
        output_format="wav",
        sample_rate_hz=22050,
    )

    typer.echo(f"Synthesising {len(text)} chars via {model} voice_id={voice_id} ...")
    try:
        response = provider.synthesize(request)
    except SynthesisError as exc:
        typer.echo(f"Synthesis failed: {exc}", err=True)
        raise typer.Exit(1) from None

    typer.echo(
        f"OK  bytes={len(response.audio_bytes)}  "
        f"duration={response.duration_seconds:.2f}s  "
        f"latency={response.latency_ms}ms  "
        f"chars_billed={response.characters_billed}"
    )
    if response.request_id:
        typer.echo(f"    request_id={response.request_id}")

    if output:
        import os

        with open(output, "wb") as fh:
            fh.write(response.audio_bytes)
        typer.echo(f"    WAV written to {os.path.abspath(output)}")

    provider.shutdown()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()
