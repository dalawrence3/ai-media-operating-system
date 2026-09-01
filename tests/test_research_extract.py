"""Tests for text extraction from HTML, PDF, plaintext, and Markdown."""

from __future__ import annotations

from app.research.extract import (
    extract_html,
    extract_markdown,
    extract_pdf,
    extract_text,
)
from app.research.models import DomainType, ExtractionMethod

# ---------------------------------------------------------------------------
# Minimal valid PDF builder (no external files required)
# ---------------------------------------------------------------------------


def _build_minimal_pdf(pages: list[str]) -> bytes:
    """Assemble a minimal valid PDF with one text stream per page."""
    # This is a hand-crafted minimal PDF.
    # Each page contains a text stream with the given content.
    import io as _io

    buf = _io.BytesIO()

    def w(s: str) -> None:
        buf.write(s.encode())

    def wb(b: bytes) -> None:
        buf.write(b)

    w("%PDF-1.4\n")

    page_obj_start = 3  # object IDs: 1=catalog, 2=pages, 3..n=page objects
    content_obj_start = page_obj_start + len(pages)
    # highest obj ID: 2 fixed + len(pages) page dicts + len(pages) content streams
    num_objs = content_obj_start + len(pages) - 1

    # We'll build object bytes and track offsets
    objects: dict[int, bytes] = {}

    # Catalog
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>\n"
    # Pages
    kids = " ".join(f"{page_obj_start + i} 0 R" for i in range(len(pages)))
    objects[2] = (f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>\n").encode()

    for i, text in enumerate(pages):
        content_id = content_obj_start + i
        page_id = page_obj_start + i
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET\n".encode()
        objects[content_id] = (
            f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream\n"
        )
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_id} 0 R >>\n"
        ).encode()

    # Write objects and track xref offsets
    xref: dict[int, int] = {}
    for obj_id in range(1, num_objs + 1):
        xref[obj_id] = buf.tell()
        w(f"{obj_id} 0 obj\n")
        wb(objects[obj_id])
        w("endobj\n")

    xref_offset = buf.tell()
    w(f"xref\n0 {num_objs + 1}\n")
    w("0000000000 65535 f \n")
    for obj_id in range(1, num_objs + 1):
        w(f"{xref[obj_id]:010d} 00000 n \n")
    w(f"trailer\n<< /Size {num_objs + 1} /Root 1 0 R >>\n")
    w(f"startxref\n{xref_offset}\n%%EOF\n")

    return buf.getvalue()


# ---------------------------------------------------------------------------
# HTML extraction
# ---------------------------------------------------------------------------


class TestExtractHtml:
    def test_extracts_body_text(self):
        html = b"<html><body><p>Hello world</p></body></html>"
        result = extract_html(html)
        assert result.raw_text is not None
        assert "Hello world" in result.raw_text

    def test_strips_script_tags(self):
        html = b"<html><body><p>Content</p><script>var x=1;</script></body></html>"
        result = extract_html(html)
        assert "var x" not in (result.raw_text or "")
        assert "Content" in (result.raw_text or "")

    def test_strips_style_tags(self):
        html = b"<html><body><style>.x{color:red}</style><p>Visible</p></body></html>"
        result = extract_html(html)
        assert "color" not in (result.raw_text or "")
        assert "Visible" in (result.raw_text or "")

    def test_extracts_title_from_title_tag(self):
        html = b"<html><head><title>My Title</title></head><body><p>text</p></body></html>"
        result = extract_html(html)
        assert result.title == "My Title"

    def test_extracts_og_title(self):
        html = (
            b"<html><head>"
            b'<meta property="og:title" content="OG Title"/>'
            b"</head><body><p>text</p></body></html>"
        )
        result = extract_html(html)
        assert result.title == "OG Title"

    def test_extracts_author_meta(self):
        html = (
            b"<html><head>"
            b'<meta name="author" content="Jane Doe"/>'
            b"</head><body><p>text</p></body></html>"
        )
        result = extract_html(html)
        assert result.author == "Jane Doe"

    def test_extracts_published_date(self):
        html = (
            b"<html><head>"
            b'<meta property="article:published_time" content="2024-03-15T10:00:00Z"/>'
            b"</head><body><p>text</p></body></html>"
        )
        result = extract_html(html)
        assert result.published_at == "2024-03-15"

    def test_extracts_time_datetime(self):
        html = (
            b'<html><body><time datetime="2024-06-01">June 2024</time><p>article</p></body></html>'
        )
        result = extract_html(html)
        assert result.published_at == "2024-06-01"

    def test_extraction_method_is_html_parser(self):
        result = extract_html(b"<p>hi</p>")
        assert result.extraction_method == ExtractionMethod.html_parser

    def test_government_domain_type(self):
        result = extract_html(b"<p>text</p>", url="https://example.gov/page")
        assert result.domain_type == DomainType.government

    def test_academic_domain_type(self):
        result = extract_html(b"<p>text</p>", url="https://university.edu/paper")
        assert result.domain_type == DomainType.academic

    def test_unknown_domain_type_for_generic(self):
        result = extract_html(b"<p>text</p>", url="https://example.com/page")
        assert result.domain_type == DomainType.unknown

    def test_word_count(self):
        html = b"<p>one two three four five</p>"
        result = extract_html(html)
        assert result.word_count == 5

    def test_suspected_truncation_for_very_short_content(self):
        html = b"<p>Short.</p>"
        result = extract_html(html)
        assert result.suspected_truncation is True

    def test_not_truncated_for_long_content(self):
        words = " ".join(["word"] * 400)
        html = f"<p>{words}</p>".encode()
        result = extract_html(html)
        assert result.suspected_truncation is False

    def test_empty_html_body(self):
        result = extract_html(b"<html><body></body></html>")
        assert result.raw_text is None

    # ------------------------------------------------------------------
    # Step 3 tests: generic HTML cleaning improvements (16D.2.1)
    # ------------------------------------------------------------------

    def test_template_tag_payload_not_in_output(self):
        """<template> elements carrying JS/HTML payloads must not appear in body text."""
        html = (
            b"<html><body>"
            b"<main><p>Scientific content about CRISPR editing pathways.</p></main>"
            b"<template>var analyticsPayload = {newrelic: true};</template>"
            b"</body></html>"
        )
        result = extract_html(html)
        assert result.raw_text is not None
        assert "analyticsPayload" not in (result.raw_text or "")
        assert "CRISPR editing pathways" in (result.raw_text or "")

    def test_inline_js_analytics_payload_excluded(self):
        """Minified inline JS analytics payloads must not dominate the extracted text."""
        analytics_js = b"x=" * 500  # simulate minified JS noise
        html = (
            b"<html><body>"
            b"<script>" + analytics_js + b"</script>"
            b"<main><p>DNA repair mechanisms include NHEJ and HDR pathways.</p></main>"
            b"</body></html>"
        )
        result = extract_html(html)
        raw = result.raw_text or ""
        assert "NHEJ and HDR pathways" in raw
        assert "x=x=" not in raw

    def test_nav_boilerplate_excluded_when_main_present(self):
        """Navigation boilerplate is excluded when a <main> content element is present."""
        html = (
            b"<html><body>"
            b"<nav>Home | About | Contact | Careers | FAQ</nav>"
            b"<main><p>Gene editing uses guide RNAs to direct Cas9 to target sequences.</p></main>"
            b"<footer>Copyright 2024</footer>"
            b"</body></html>"
        )
        result = extract_html(html)
        raw = result.raw_text or ""
        assert "guide RNAs" in raw
        assert "Home | About | Contact" not in raw

    def test_article_element_preferred_over_full_body(self):
        """<article> body content is preserved when used as content root."""
        html = (
            b"<html><body>"
            b"<aside>Sidebar advertising content unrelated to article</aside>"
            b"<article>"
            b"<p>Homology-directed repair requires a DNA template for precise edits.</p>"
            b"</article>"
            b"</body></html>"
        )
        result = extract_html(html)
        raw = result.raw_text or ""
        assert "Homology-directed repair" in raw

    def test_aria_hidden_elements_excluded(self):
        """Elements with aria-hidden=true (overlays, tooltips, trackers) are removed."""
        html = (
            b"<html><body>"
            b'<div aria-hidden="true">cookie_consent_tracking_widget_v2</div>'
            b"<main><p>CRISPR relies on Cas9 nuclease activity to cleave DNA.</p></main>"
            b"</body></html>"
        )
        result = extract_html(html)
        raw = result.raw_text or ""
        assert "cookie_consent_tracking" not in raw
        assert "Cas9 nuclease activity" in raw

    def test_form_and_button_elements_excluded(self):
        """Form UI elements are stripped from research text."""
        html = (
            b"<html><body>"
            b"<main>"
            b"<p>HDR pathway fidelity depends on template proximity and cell cycle phase.</p>"
            b"<form><button>Subscribe</button><input placeholder='Email'/></form>"
            b"</main>"
            b"</body></html>"
        )
        result = extract_html(html)
        raw = result.raw_text or ""
        assert "HDR pathway fidelity" in raw
        assert "Subscribe" not in raw

    def test_plain_text_path_not_regressed(self):
        """Plain-text extraction is unaffected by HTML cleaning changes."""
        from app.research.extract import extract_text

        result = extract_text(b"CRISPR cuts at a specific genomic locus directed by guide RNA.")
        assert result.raw_text is not None
        assert "CRISPR cuts" in result.raw_text

    def test_fallback_to_body_when_no_main_or_article(self):
        """Falls back to <body> text when no <main>/<article> element exists."""
        html = (
            b"<html><body>"
            b"<div><p>Cell repair mechanisms: NHEJ is error-prone, "
            b"HDR is template-guided.</p></div>"
            b"</body></html>"
        )
        result = extract_html(html)
        raw = result.raw_text or ""
        assert "NHEJ is error-prone" in raw

    def test_iframe_excluded(self):
        """<iframe> elements (often tracking pixels or embeds) are removed."""
        html = (
            b"<html><body>"
            b'<iframe src="https://tracking.example.com/pixel"></iframe>'
            b"<main><p>The cas9 protein creates blunt-ended double-strand breaks.</p></main>"
            b"</body></html>"
        )
        result = extract_html(html)
        raw = result.raw_text or ""
        assert "tracking.example.com" not in raw
        assert "double-strand breaks" in raw


# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------


class TestExtractPdf:
    def test_extracts_text_from_text_layer(self):
        pdf = _build_minimal_pdf(["Hello PDF world"])
        result = extract_pdf(pdf)
        assert result.raw_text is not None
        assert result.extraction_method == ExtractionMethod.pdf

    def test_page_separator_present(self):
        pdf = _build_minimal_pdf(["Page one content", "Page two content"])
        result = extract_pdf(pdf)
        assert result.raw_text is not None
        assert "--- Page 1 ---" in result.raw_text
        assert "--- Page 2 ---" in result.raw_text

    def test_malformed_pdf_returns_failed(self):
        result = extract_pdf(b"not a pdf")
        assert result.raw_text is None
        assert result.extraction_error is not None

    def test_empty_pdf_returns_failed(self):
        # A PDF where pypdf extracts empty text from all pages
        # We simulate with a PDF that has no content streams with text
        pdf = _build_minimal_pdf([""])
        result = extract_pdf(pdf)
        # Either raw_text is None or extraction_error notes no text
        assert (
            result.extraction_error is not None
            or result.raw_text is None
            or (result.raw_text.replace("--- Page 1 ---", "").strip() == "")
        )

    def test_pdf_extraction_method(self):
        pdf = _build_minimal_pdf(["text"])
        result = extract_pdf(pdf)
        assert result.extraction_method == ExtractionMethod.pdf

    def test_page_count_in_result(self):
        pdf = _build_minimal_pdf(["page1", "page2", "page3"])
        result = extract_pdf(pdf)
        assert result.page_count == 3


# ---------------------------------------------------------------------------
# Plaintext extraction
# ---------------------------------------------------------------------------


class TestExtractText:
    def test_extracts_ascii(self):
        result = extract_text(b"Hello world\nSecond line")
        assert result.raw_text == "Hello world\nSecond line"

    def test_extracts_utf8(self):
        result = extract_text("Héllo wörld".encode())
        assert result.raw_text == "Héllo wörld"

    def test_encoding_error_replaced(self):
        result = extract_text(b"hello \xff world")
        assert result.raw_text is not None
        assert "hello" in result.raw_text

    def test_word_count(self):
        result = extract_text(b"one two three")
        assert result.word_count == 3

    def test_extraction_method_is_plaintext(self):
        result = extract_text(b"text")
        assert result.extraction_method == ExtractionMethod.plaintext

    def test_empty_bytes(self):
        result = extract_text(b"")
        assert result.raw_text is None

    def test_domain_type_unknown(self):
        result = extract_text(b"hello")
        assert result.domain_type == DomainType.unknown


# ---------------------------------------------------------------------------
# Markdown extraction
# ---------------------------------------------------------------------------


class TestExtractMarkdown:
    def test_extracts_content(self):
        md = b"# Title\n\nSome **body** text.\n"
        result = extract_markdown(md)
        assert result.raw_text is not None
        assert "Title" in result.raw_text

    def test_extracts_title_from_h1(self):
        md = b"# My Document\n\nBody text here."
        result = extract_markdown(md)
        assert result.title == "My Document"

    def test_extracts_title_from_h2(self):
        md = b"## Section Title\n\nBody text."
        result = extract_markdown(md)
        assert result.title == "Section Title"

    def test_no_title_when_no_heading(self):
        md = b"Just plain text without any heading."
        result = extract_markdown(md)
        assert result.title is None

    def test_extraction_method_is_markdown(self):
        result = extract_markdown(b"# Hello")
        assert result.extraction_method == ExtractionMethod.markdown

    def test_empty_markdown(self):
        result = extract_markdown(b"")
        assert result.raw_text is None

    def test_raw_text_preserves_markdown_syntax(self):
        md = b"# Title\n\n**bold** and *italic*."
        result = extract_markdown(md)
        assert result.raw_text is not None
        assert "**bold**" in result.raw_text
