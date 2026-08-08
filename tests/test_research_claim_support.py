"""Tests for quote support classification and offset/page provenance (Phase 4.2)."""

from __future__ import annotations

from app.research.claim_support import classify_quote_support, derive_page_number
from app.research.models import ClaimSupportStatus


class TestClassifyQuoteSupport:
    # --- exact match ---

    def test_exact_match(self):
        raw = "The sky is blue and the grass is green."
        status, start, end = classify_quote_support("sky is blue", raw)
        assert status == ClaimSupportStatus.exact
        assert raw[start:end] == "sky is blue"

    def test_exact_match_at_start(self):
        raw = "Hello world."
        status, start, end = classify_quote_support("Hello", raw)
        assert status == ClaimSupportStatus.exact
        assert start == 0
        assert end == 5

    def test_exact_match_at_end(self):
        raw = "Hello world."
        status, start, end = classify_quote_support("world.", raw)
        assert status == ClaimSupportStatus.exact
        assert end == len(raw)

    def test_exact_match_full_text(self):
        raw = "Short text."
        status, start, end = classify_quote_support(raw, raw)
        assert status == ClaimSupportStatus.exact
        assert start == 0
        assert end == len(raw)

    # --- normalized match ---

    def test_normalized_match_extra_spaces(self):
        raw = "The  quick  brown fox."
        quote = "The quick brown fox."
        status, start, end = classify_quote_support(quote, raw)
        assert status == ClaimSupportStatus.normalized
        assert start is not None and end is not None
        assert start >= 0

    def test_normalized_match_crlf_in_raw(self):
        raw = "Line one.\r\nLine two."
        quote = "Line one.\nLine two."
        status, start, end = classify_quote_support(quote, raw)
        assert status == ClaimSupportStatus.normalized

    def test_normalized_match_multiple_newlines(self):
        raw = "First.\n\nSecond."
        quote = "First.\nSecond."
        status, _, _ = classify_quote_support(quote, raw)
        assert status == ClaimSupportStatus.normalized

    def test_normalized_preserves_original_quote(self):
        # The returned offsets point into raw_text, not the quote.
        raw = "The  quick  fox jumps."
        quote = "quick  fox"  # exact substring (multiple spaces preserved in raw)
        status, start, end = classify_quote_support(quote, raw)
        # This could be exact if the double spaces are present in raw
        assert status in (ClaimSupportStatus.exact, ClaimSupportStatus.normalized)

    # --- unsupported ---

    def test_unsupported_no_match(self):
        raw = "The sky is blue."
        status, start, end = classify_quote_support("grass is green", raw)
        assert status == ClaimSupportStatus.unsupported
        assert start is None
        assert end is None

    def test_unsupported_case_mismatch(self):
        # Normalization does NOT include case folding; case mismatch → unsupported.
        raw = "Water boils at 100°C."
        status, _, _ = classify_quote_support("water boils at 100°c.", raw)
        assert status == ClaimSupportStatus.unsupported

    # --- no_quote ---

    def test_none_quote(self):
        status, start, end = classify_quote_support(None, "Some text.")
        assert status == ClaimSupportStatus.no_quote
        assert start is None
        assert end is None

    def test_empty_string_quote(self):
        status, start, end = classify_quote_support("", "Some text.")
        assert status == ClaimSupportStatus.no_quote

    def test_whitespace_only_quote(self):
        status, _, _ = classify_quote_support("   ", "Some text.")
        assert status == ClaimSupportStatus.no_quote

    # --- offset correctness ---

    def test_exact_offset_correctness(self):
        raw = "abcdefghij"
        quote = "cde"
        status, start, end = classify_quote_support(quote, raw)
        assert status == ClaimSupportStatus.exact
        assert raw[start:end] == quote

    def test_normalized_offset_points_to_raw(self):
        raw = "Hello   world."
        quote = "Hello world."
        status, start, end = classify_quote_support(quote, raw)
        assert status == ClaimSupportStatus.normalized
        assert start is not None
        # start should be at 'H'
        assert start == 0

    def test_first_occurrence_returned_for_repeated_text(self):
        raw = "abc abc abc"
        status, start, end = classify_quote_support("abc", raw)
        assert status == ClaimSupportStatus.exact
        assert start == 0


class TestDerivePageNumber:
    _PDF_RAW = (
        "--- Page 1 ---\nContent on page one. Some text here.\n\n"
        "--- Page 2 ---\nContent on page two. More text.\n\n"
        "--- Page 3 ---\nContent on page three."
    )

    def test_quote_on_page_1(self):
        start = self._PDF_RAW.index("Content on page one")
        page = derive_page_number(self._PDF_RAW, start, "pdf")
        assert page == 1

    def test_quote_on_page_2(self):
        start = self._PDF_RAW.index("Content on page two")
        page = derive_page_number(self._PDF_RAW, start, "pdf")
        assert page == 2

    def test_quote_on_page_3(self):
        start = self._PDF_RAW.index("Content on page three")
        page = derive_page_number(self._PDF_RAW, start, "pdf")
        assert page == 3

    def test_repeated_text_returns_last_preceding_page(self):
        raw = (
            "--- Page 1 ---\nrepeated text.\n\n"
            "--- Page 2 ---\nrepeated text again."
        )
        # Quote at page-2 occurrence
        start = raw.rindex("repeated text")
        page = derive_page_number(raw, start, "pdf")
        assert page == 2

    def test_quote_within_separator_line(self):
        # quote_start falls inside "--- Page 2 ---\n"
        sep_start = self._PDF_RAW.index("--- Page 2 ---")
        page = derive_page_number(self._PDF_RAW, sep_start + 4, "pdf")
        assert page == 2

    def test_non_pdf_returns_none(self):
        assert derive_page_number(self._PDF_RAW, 0, "html_parser") is None
        assert derive_page_number(self._PDF_RAW, 0, "plaintext") is None
        assert derive_page_number(self._PDF_RAW, 0, None) is None

    def test_no_offset_returns_none(self):
        assert derive_page_number(self._PDF_RAW, None, "pdf") is None

    def test_normalized_match_uses_offset(self):
        raw = "--- Page 1 ---\nSome  content here."
        quote = "Some content here."
        status, start, end = classify_quote_support(quote, raw)
        assert status == ClaimSupportStatus.normalized
        page = derive_page_number(raw, start, "pdf")
        assert page == 1
