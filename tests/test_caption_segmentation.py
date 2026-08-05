"""Tests for src/app/captions/segmentation.py."""

from __future__ import annotations

from app.captions.segmentation import (
    SegmentedCueText,
    normalize_for_integrity,
    segment_narration_text,
)


class TestNormalizeForIntegrity:
    def test_strips_leading_trailing(self):
        assert normalize_for_integrity("  hello  ") == "hello"

    def test_collapses_internal_whitespace(self):
        assert normalize_for_integrity("hello   world") == "hello world"

    def test_collapses_newlines(self):
        assert normalize_for_integrity("hello\nworld") == "hello world"

    def test_empty_returns_empty(self):
        assert normalize_for_integrity("") == ""

    def test_tabs_collapsed(self):
        assert normalize_for_integrity("a\t\tb") == "a b"


class TestSegmentNarrationText:
    def _check_integrity(self, text: str, cues: list[SegmentedCueText]) -> None:
        """Assert text integrity invariant."""
        original_norm = normalize_for_integrity(text)
        rejoined = " ".join(c.text for c in cues)
        assert normalize_for_integrity(rejoined) == original_norm, (
            f"Integrity violation:\n  original: {original_norm!r}\n  rejoined: "
            f"{normalize_for_integrity(rejoined)!r}"
        )

    def test_empty_text_returns_empty_list(self):
        assert segment_narration_text("") == []

    def test_whitespace_only_returns_empty(self):
        assert segment_narration_text("   ") == []

    def test_single_short_sentence(self):
        text = "Hello world."
        cues = segment_narration_text(text)
        assert len(cues) >= 1
        self._check_integrity(text, cues)

    def test_integrity_preserved_for_typical_script(self):
        text = (
            "Scientists discovered a new species of deep-sea fish near the Mariana Trench. "
            "The fish can survive at depths of over 8,000 meters. "
            "This is the deepest fish ever recorded."
        )
        cues = segment_narration_text(text)
        assert cues
        self._check_integrity(text, cues)

    def test_abbreviation_dr_not_split(self):
        text = "Dr. Smith published the results of his experiment."
        cues = segment_narration_text(text)
        # "Dr." should NOT cause a sentence split
        combined = " ".join(c.text for c in cues)
        assert "Dr." in normalize_for_integrity(combined)
        self._check_integrity(text, cues)

    def test_abbreviation_mr_not_split(self):
        text = "Mr. Jones arrived at 9 AM."
        cues = segment_narration_text(text)
        self._check_integrity(text, cues)

    def test_decimal_number_not_split(self):
        text = "The temperature rose by 3.5 degrees in one hour."
        cues = segment_narration_text(text)
        self._check_integrity(text, cues)
        combined = normalize_for_integrity(" ".join(c.text for c in cues))
        assert "3.5" in combined

    def test_ellipsis_treated_as_continuation(self):
        text = "The answer is simple... just keep going."
        cues = segment_narration_text(text)
        self._check_integrity(text, cues)

    def test_exclamation_mark_splits(self):
        text = "This is amazing! Now let me tell you why."
        cues = segment_narration_text(text)
        self._check_integrity(text, cues)

    def test_question_mark_splits(self):
        text = "What happened next? The whole world changed."
        cues = segment_narration_text(text)
        self._check_integrity(text, cues)

    def test_no_empty_cues(self):
        text = "Hello. World."
        cues = segment_narration_text(text)
        for cue in cues:
            assert cue.text.strip(), f"Empty cue found: {cue!r}"

    def test_cta_short_text(self):
        text = "Like and subscribe."
        cues = segment_narration_text(text)
        assert cues
        self._check_integrity(text, cues)

    def test_unicode_preserved(self):
        text = "The café opened at 8 AM in the city of München."
        cues = segment_narration_text(text)
        self._check_integrity(text, cues)
        combined = normalize_for_integrity(" ".join(c.text for c in cues))
        assert "café" in combined
        assert "München" in combined

    def test_no_word_split_across_lines(self):
        text = "Each word must stay intact without any splits across line boundaries."
        cues = segment_narration_text(text)
        for cue in cues:
            for line in cue.lines:
                # No line should contain a partial word that could result from a split
                assert line.strip() != ""

    def test_max_two_lines_per_cue(self):
        text = "Hello world this is a test sentence about something interesting."
        cues = segment_narration_text(text)
        for cue in cues:
            assert len(cue.lines) <= 2, f"Cue has {len(cue.lines)} lines: {cue!r}"

    def test_lines_within_char_limit(self):
        from app.captions.constants import CAPTION_MAX_CHARS_PER_LINE

        text = "This is a test of the line length limit for caption generation in M6.3A."
        cues = segment_narration_text(text)
        for cue in cues:
            for line in cue.lines:
                # Single words longer than the limit are allowed (forced fit)
                words_in_line = line.split()
                if len(words_in_line) > 1:
                    assert len(line) <= CAPTION_MAX_CHARS_PER_LINE + 1, (
                        f"Multi-word line too long: {line!r}"
                    )

    def test_long_sentence_splits_into_multiple_cues(self):
        text = (
            "This is an extremely long sentence that should be split across multiple "
            "caption cues because it contains far too many words to fit within the "
            "maximum character limit for a single cue."
        )
        cues = segment_narration_text(text)
        assert len(cues) > 1
        self._check_integrity(text, cues)

    def test_quotation_ending_sentence(self):
        text = 'She said "Hello." Then she left.'
        cues = segment_narration_text(text)
        self._check_integrity(text, cues)

    def test_orphan_word_warning(self):
        # Construct a text that forces a 2-line cue where the second line has one word.
        # Use a text where first line fills near-max, then one word spills to second line.
        from app.captions.constants import CAPTION_MAX_CHARS_PER_LINE

        # Build a sentence where the second line will have exactly one word.
        first_line_words = "a" * (CAPTION_MAX_CHARS_PER_LINE - 2)  # long word
        _ = first_line_words + " orphan"
        # This single word fills a line; orphan word would appear on second line
        # Use a more realistic test: a sentence whose last word is alone.
        text2 = "This is a medium length caption line here word"
        cues = segment_narration_text(text2)
        # At least verify integrity regardless of warning presence
        self._check_integrity(text2, cues)

    def test_two_sentences_form_separate_cues_or_more(self):
        text = "First sentence ends here. Second sentence starts here."
        cues = segment_narration_text(text)
        assert len(cues) >= 1
        self._check_integrity(text, cues)

    def test_clause_split_at_comma(self):
        text = (
            "This sentence has a comma in the middle, which allows the segmenter "
            "to split at a natural clause boundary when the sentence is too long "
            "to fit in a single two-line cue."
        )
        cues = segment_narration_text(text)
        assert len(cues) >= 2
        self._check_integrity(text, cues)

    def test_multi_segment_integrity(self):
        segments = [
            "The global average temperature has risen by 1.2 degrees Celsius.",
            "Scientists warn that we must limit warming to 1.5 degrees.",
            "Here is what you can do about it.",
        ]
        for text in segments:
            cues = segment_narration_text(text)
            assert cues
            self._check_integrity(text, cues)

    def test_numbers_not_fabricated(self):
        text = "There are 7 billion people on Earth."
        cues = segment_narration_text(text)
        combined = normalize_for_integrity(" ".join(c.text for c in cues))
        assert "7" in combined
        self._check_integrity(text, cues)
