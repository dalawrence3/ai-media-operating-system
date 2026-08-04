"""Text extraction from HTML, PDF, plaintext, and Markdown sources."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from app.research.constants import PDF_MAX_PAGES
from app.research.models import DomainType, ExtractionMethod


@dataclass
class ExtractResult:
    """Output of a single extraction attempt."""

    raw_text: str | None
    word_count: int | None
    title: str | None
    author: str | None
    published_at: str | None
    domain_type: DomainType
    extraction_method: ExtractionMethod
    extraction_error: str | None = None
    suspected_truncation: bool = False
    is_partial: bool = False
    page_count: int | None = field(default=None, repr=False)


def _count_words(text: str) -> int:
    return len(text.split())


def _infer_domain_type(url: str | None) -> DomainType:
    if not url:
        return DomainType.unknown
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return DomainType.unknown
    host = host.lower()
    parts = host.split(".")
    # Check for government TLDs
    if any(p in {"gov", "gsi", "gc", "gouv", "gob"} for p in parts):
        return DomainType.government
    # Check for academic TLDs
    if any(p in {"edu", "ac"} for p in parts):
        return DomainType.academic
    return DomainType.unknown


# ---------------------------------------------------------------------------
# HTML extraction
# ---------------------------------------------------------------------------

def extract_html(html_bytes: bytes, url: str | None = None) -> ExtractResult:
    """Extract text and metadata from HTML bytes using stdlib html.parser."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_bytes, "html.parser")

    # Remove non-content tags
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "noscript"]):
        tag.decompose()

    # Title: <title> > og:title > twitter:title
    title: str | None = None
    if soup.title and soup.title.string:
        title = soup.title.string.strip() or None
    if not title:
        og = soup.find("meta", property="og:title")
        if og and og.get("content"):
            title = str(og["content"]).strip() or None
    if not title:
        tw = soup.find("meta", attrs={"name": "twitter:title"})
        if tw and tw.get("content"):
            title = str(tw["content"]).strip() or None

    # Author
    author: str | None = None
    for attrs in (
        {"name": "author"},
        {"property": "article:author"},
        {"name": "twitter:creator"},
    ):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            v = str(tag["content"]).strip()
            if v:
                author = v
                break

    # Publication date
    published_at: str | None = None
    for attrs in (
        {"property": "article:published_time"},
        {"name": "date"},
        {"name": "pubdate"},
        {"itemprop": "datePublished"},
    ):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            v = str(tag["content"]).strip()
            if v:
                published_at = v[:10]  # keep ISO date portion
                break
    if not published_at:
        time_tag = soup.find("time", attrs={"datetime": True})
        if time_tag:
            v = str(time_tag["datetime"]).strip()
            if v:
                published_at = v[:10]

    # Body text
    body = soup.get_text(separator="\n", strip=True)
    word_count = _count_words(body) if body.strip() else 0

    domain_type = _infer_domain_type(url)

    # Suspected truncation: HTTP 200, html_parser, very few words → likely paywall
    suspected_truncation = word_count < 300

    return ExtractResult(
        raw_text=body if body.strip() else None,
        word_count=word_count if body.strip() else None,
        title=title,
        author=author,
        published_at=published_at,
        domain_type=domain_type,
        extraction_method=ExtractionMethod.html_parser,
        suspected_truncation=suspected_truncation,
    )


# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------

def extract_pdf(pdf_bytes: bytes) -> ExtractResult:
    """Extract text from PDF bytes using pypdf."""
    import io

    import pypdf
    import pypdf.errors

    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    except pypdf.errors.FileNotDecryptedError:
        return ExtractResult(
            raw_text=None,
            word_count=None,
            title=None,
            author=None,
            published_at=None,
            domain_type=DomainType.unknown,
            extraction_method=ExtractionMethod.pdf,
            extraction_error="PDF is encrypted and cannot be opened without a password.",
        )
    except Exception as exc:
        return ExtractResult(
            raw_text=None,
            word_count=None,
            title=None,
            author=None,
            published_at=None,
            domain_type=DomainType.unknown,
            extraction_method=ExtractionMethod.pdf,
            extraction_error=f"PDF could not be opened: {exc}",
        )

    # Metadata
    meta = reader.metadata or {}
    title: str | None = None
    author: str | None = None
    if meta.get("/Title"):
        title = str(meta["/Title"]).strip() or None
    if meta.get("/Author"):
        author = str(meta["/Author"]).strip() or None

    total_pages = len(reader.pages)
    truncated = total_pages > PDF_MAX_PAGES
    pages_to_read = min(total_pages, PDF_MAX_PAGES)

    parts: list[str] = []
    failed_pages: list[int] = []

    for i in range(pages_to_read):
        page_num = i + 1
        try:
            text = reader.pages[i].extract_text() or ""
            parts.append(f"--- Page {page_num} ---\n{text}")
        except Exception:
            failed_pages.append(page_num)

    raw_text = "\n".join(parts) if parts else None
    word_count = _count_words(raw_text) if raw_text else None

    # Compose error / partial notes
    error_parts: list[str] = []
    if truncated:
        error_parts.append(
            f"Truncated: source has {total_pages} pages; only first {PDF_MAX_PAGES} extracted."
        )
    if failed_pages:
        error_parts.append(f"Failed on pages: {failed_pages}.")
    extraction_error = " ".join(error_parts) or None

    is_partial = bool(failed_pages or truncated)

    if raw_text and (not raw_text.strip()):
        return ExtractResult(
            raw_text=None,
            word_count=None,
            title=title,
            author=author,
            published_at=None,
            domain_type=DomainType.unknown,
            extraction_method=ExtractionMethod.pdf,
            extraction_error="PDF contains no extractable text. It may be image-only.",
            page_count=total_pages,
        )

    if not raw_text:
        return ExtractResult(
            raw_text=None,
            word_count=None,
            title=title,
            author=author,
            published_at=None,
            domain_type=DomainType.unknown,
            extraction_method=ExtractionMethod.pdf,
            extraction_error=extraction_error
            or "PDF contains no extractable text. It may be image-only.",
            page_count=total_pages,
        )

    return ExtractResult(
        raw_text=raw_text,
        word_count=word_count,
        title=title,
        author=author,
        published_at=None,
        domain_type=DomainType.unknown,
        extraction_method=ExtractionMethod.pdf,
        extraction_error=extraction_error,
        is_partial=is_partial,
        page_count=total_pages,
    )


# ---------------------------------------------------------------------------
# Plaintext and Markdown
# ---------------------------------------------------------------------------

def extract_text(content: bytes, *, encoding: str = "utf-8") -> ExtractResult:
    """Extract text from a plain-text file."""
    text = content.decode(encoding, errors="replace")
    word_count = _count_words(text) if text.strip() else 0
    return ExtractResult(
        raw_text=text if text.strip() else None,
        word_count=word_count if text.strip() else None,
        title=None,
        author=None,
        published_at=None,
        domain_type=DomainType.unknown,
        extraction_method=ExtractionMethod.plaintext,
    )


def extract_markdown(content: bytes, *, encoding: str = "utf-8") -> ExtractResult:
    """Extract text from a Markdown file.

    Keeps the raw Markdown text for storage and word counting;
    no rendering or HTML conversion is performed.
    """
    text = content.decode(encoding, errors="replace")
    # Strip ATX headings hashes for word count but preserve text
    display_text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Remove inline Markdown syntax for word count only; raw_text is the full Markdown
    word_count = _count_words(display_text) if display_text.strip() else 0
    return ExtractResult(
        raw_text=text if text.strip() else None,
        word_count=word_count if text.strip() else None,
        title=_extract_markdown_title(text),
        author=None,
        published_at=None,
        domain_type=DomainType.unknown,
        extraction_method=ExtractionMethod.markdown,
    )


def _extract_markdown_title(text: str) -> str | None:
    """Return the first ATX heading as a title, if present."""
    for line in text.splitlines():
        m = re.match(r"^#{1,2}\s+(.+)", line.strip())
        if m:
            return m.group(1).strip() or None
    return None
