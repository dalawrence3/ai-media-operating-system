"""Tests for content hashing and normalization."""

from __future__ import annotations

import hashlib

from app.research.hashing import normalize_for_hash, sha256_hex


class TestNormalizeForHash:
    def test_crlf_normalized_to_lf(self):
        assert "\n" in normalize_for_hash("line1\r\nline2")
        assert "\r" not in normalize_for_hash("line1\r\nline2")

    def test_cr_only_normalized_to_lf(self):
        assert "\r" not in normalize_for_hash("line1\rline2")

    def test_horizontal_whitespace_collapsed(self):
        result = normalize_for_hash("hello   world\ttab")
        assert "   " not in result
        assert "\t" not in result
        assert "hello world" in result

    def test_newlines_not_collapsed_below_3(self):
        result = normalize_for_hash("a\n\nb")
        assert "\n\n" in result

    def test_three_newlines_collapsed_to_two(self):
        result = normalize_for_hash("a\n\n\nb")
        assert "\n\n\n" not in result
        assert "\n\n" in result

    def test_many_newlines_collapsed_to_two(self):
        result = normalize_for_hash("a\n\n\n\n\n\nb")
        assert "\n\n\n" not in result

    def test_leading_trailing_whitespace_stripped(self):
        assert normalize_for_hash("  hello  ") == "hello"

    def test_unicode_nfc(self):
        # ä can be U+00E4 (precomposed) or U+0061 U+0308 (decomposed)
        composed = "ä"
        decomposed = "ä"
        assert normalize_for_hash(composed) == normalize_for_hash(decomposed)

    def test_nbsp_treated_as_whitespace(self):
        # non-breaking space U+00A0 is horizontal whitespace
        result = normalize_for_hash("hello world")
        assert " " not in result
        assert "hello world" in result

    def test_empty_string(self):
        assert normalize_for_hash("") == ""

    def test_whitespace_only(self):
        assert normalize_for_hash("   \n  \t  ") == ""

    def test_deterministic(self):
        text = "Hello,\r\n  world!\n\n\nAnother\tparagraph."
        assert normalize_for_hash(text) == normalize_for_hash(text)

    def test_punctuation_preserved(self):
        text = "It's 100% correct, isn't it?"
        result = normalize_for_hash(text)
        assert "100%" in result
        assert "It's" in result


class TestSha256Hex:
    def test_bytes_input(self):
        h = sha256_hex(b"hello")
        assert h == hashlib.sha256(b"hello").hexdigest()

    def test_str_input_encodes_as_utf8(self):
        h = sha256_hex("hello")
        assert h == hashlib.sha256(b"hello").hexdigest()

    def test_hex_format(self):
        h = sha256_hex(b"test")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_empty_bytes(self):
        h = sha256_hex(b"")
        assert len(h) == 64

    def test_deterministic(self):
        assert sha256_hex(b"same") == sha256_hex(b"same")

    def test_different_inputs_differ(self):
        assert sha256_hex(b"a") != sha256_hex(b"b")
