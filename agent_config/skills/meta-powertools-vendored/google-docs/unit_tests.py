#!/usr/bin/env python3
# (c) Meta Platforms, Inc. and affiliates. Confidential and proprietary.

"""
Unit tests for markdown parsing and inline formatting in google_api.py.

These tests exercise the pure-function parsing logic and do NOT require
a Google API connection or any network access.

Usage:
    python3 unit_tests.py
    python3 -m pytest unit_tests.py -v
"""

from __future__ import annotations

import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from google_api import (
    _add_inline_format_requests,
    _add_tab_id_to_request,
    _build_block_quote_requests,
    _build_bullet_list_requests,
    _build_code_block_requests,
    _build_horizontal_rule_requests,
    _build_numbered_list_requests,
    _build_text_line_requests,
    _get_element_text,
    _PARAGRAPH_RESET_FIELDS,
    _parse_html,
    _parse_markdown_blocks,
    _parse_table_lines,
    _split_table_row,
    _utf16_len,
    _utf16_offset,
    action_to_requests,
    get_document_formatting,
    strip_and_parse_inline_formatting,
)


class TestStripAndParseInlineFormatting(unittest.TestCase):
    """Tests for strip_and_parse_inline_formatting()."""

    # ---- Bold ----

    def test_bold_double_asterisk(self) -> None:
        text, ranges = strip_and_parse_inline_formatting("This is **bold** text")
        self.assertEqual(text, "This is bold text")
        self.assertEqual(len(ranges), 1)
        self.assertEqual(ranges[0]["style"], "bold")
        self.assertEqual(ranges[0]["start"], 8)
        self.assertEqual(ranges[0]["end"], 12)

    def test_bold_double_underscore(self) -> None:
        text, ranges = strip_and_parse_inline_formatting("This is __bold__ text")
        self.assertEqual(text, "This is bold text")
        self.assertEqual(len(ranges), 1)
        self.assertEqual(ranges[0]["style"], "bold")

    # ---- Italic ----

    def test_italic_asterisk(self) -> None:
        text, ranges = strip_and_parse_inline_formatting("This is *italic* text")
        self.assertEqual(text, "This is italic text")
        self.assertEqual(ranges[0]["style"], "italic")

    def test_italic_underscore(self) -> None:
        text, ranges = strip_and_parse_inline_formatting("An _italic_ word")
        self.assertEqual(text, "An italic word")
        self.assertEqual(ranges[0]["style"], "italic")

    def test_mid_word_underscores_not_italic(self) -> None:
        """Underscores inside snake_case identifiers should not trigger italic."""
        text, ranges = strip_and_parse_inline_formatting("execute_batched_actions")
        self.assertEqual(text, "execute_batched_actions")
        italic_ranges = [r for r in ranges if r["style"] == "italic"]
        self.assertEqual(italic_ranges, [])

    def test_mid_word_underscore_single(self) -> None:
        """A single underscore inside a word should not trigger italic."""
        text, ranges = strip_and_parse_inline_formatting("format_text is a function")
        self.assertEqual(text, "format_text is a function")
        italic_ranges = [r for r in ranges if r["style"] == "italic"]
        self.assertEqual(italic_ranges, [])

    def test_mid_word_underscores_caps_not_italic(self) -> None:
        """Underscores inside identifiers should NOT trigger italic."""
        text, ranges = strip_and_parse_inline_formatting(
            "Check NEEDS_ONBOARDING status"
        )
        self.assertEqual(text, "Check NEEDS_ONBOARDING status")
        self.assertEqual(len(ranges), 0)

    def test_multiple_underscored_identifiers_not_italic(self) -> None:
        """Multiple underscore-containing identifiers should remain literal."""
        text, ranges = strip_and_parse_inline_formatting(
            "use best_practices and task_completion"
        )
        self.assertEqual(text, "use best_practices and task_completion")
        italic_ranges = [r for r in ranges if r["style"] == "italic"]
        self.assertEqual(len(italic_ranges), 0)

    def test_underscore_italic_at_line_start(self) -> None:
        """Underscore italic should work at start of line."""
        text, ranges = strip_and_parse_inline_formatting("_italic_ at start")
        self.assertEqual(text, "italic at start")
        self.assertEqual(ranges[0]["style"], "italic")

    # ---- Strikethrough ----

    def test_strikethrough(self) -> None:
        text, ranges = strip_and_parse_inline_formatting("This is ~~deleted~~ text")
        self.assertEqual(text, "This is deleted text")
        self.assertEqual(len(ranges), 1)
        self.assertEqual(ranges[0]["style"], "strikethrough")

    # ---- Code ----

    def test_inline_code(self) -> None:
        text, ranges = strip_and_parse_inline_formatting("Use `my_func()` here")
        self.assertEqual(text, "Use my_func() here")
        self.assertEqual(ranges[0]["style"], "code")

    def test_code_span_protects_underscore(self) -> None:
        """Underscores inside backticks should not trigger italic."""
        text, ranges = strip_and_parse_inline_formatting("`my_var` and _italic_")
        self.assertIn("my_var", text)
        styles = [r["style"] for r in ranges]
        self.assertIn("code", styles)
        self.assertIn("italic", styles)

    def test_code_span_protects_asterisk(self) -> None:
        """Asterisks inside backticks should not trigger bold."""
        text, ranges = strip_and_parse_inline_formatting("`a**b` and **bold**")
        self.assertIn("a**b", text)
        styles = [r["style"] for r in ranges]
        self.assertIn("code", styles)
        self.assertIn("bold", styles)

    # ---- Double-backtick code spans ----

    def test_double_backtick_with_internal_backtick(self) -> None:
        """Double-backtick code spans can contain single backticks."""
        text, ranges = strip_and_parse_inline_formatting("Use ``x`y`` here")
        self.assertEqual(text, "Use x`y here")
        self.assertEqual(len(ranges), 1)
        self.assertEqual(ranges[0]["style"], "code")

    def test_double_backtick_showing_backtick_syntax(self) -> None:
        """`` `text` `` should render as `text` in code formatting."""
        text, ranges = strip_and_parse_inline_formatting("`` `text` ``")
        self.assertIn("`text`", text)
        self.assertEqual(ranges[0]["style"], "code")

    def test_mixed_single_and_double_backtick(self) -> None:
        """Single and double backtick code spans in the same line."""
        text, ranges = strip_and_parse_inline_formatting(
            "Use ``has `tick` inside`` and `simple`"
        )
        self.assertIn("has `tick` inside", text)
        self.assertIn("simple", text)
        code_ranges = [r for r in ranges if r["style"] == "code"]
        self.assertEqual(len(code_ranges), 2)

    # ---- Links ----

    def test_link(self) -> None:
        text, ranges = strip_and_parse_inline_formatting("[Google](https://google.com)")
        self.assertEqual(text, "Google")
        self.assertEqual(ranges[0]["style"], "link")
        self.assertEqual(ranges[0]["url"], "https://google.com")

    def test_link_with_nested_bold(self) -> None:
        """Links containing bold text should have both link and bold ranges."""
        text, ranges = strip_and_parse_inline_formatting(
            "[**Bold Link**](https://example.com)"
        )
        self.assertEqual(text, "Bold Link")
        styles = {r["style"] for r in ranges}
        self.assertIn("link", styles)
        self.assertIn("bold", styles)

    # ---- Escape handling ----

    def test_escaped_underscore(self) -> None:
        text, ranges = strip_and_parse_inline_formatting("sev\\_num")
        self.assertEqual(text, "sev_num")
        self.assertEqual(len(ranges), 0)

    def test_escaped_asterisk(self) -> None:
        text, ranges = strip_and_parse_inline_formatting("5 \\* 3")
        self.assertEqual(text, "5 * 3")
        self.assertEqual(len(ranges), 0)

    def test_escaped_backtick(self) -> None:
        text, ranges = strip_and_parse_inline_formatting("Use \\` for literal")
        self.assertIn("`", text)
        self.assertNotIn("\\", text)
        self.assertEqual(len(ranges), 0)

    def test_escape_inside_code_span_preserved(self) -> None:
        """Backslash-underscore inside a code span should remain literal."""
        text, ranges = strip_and_parse_inline_formatting("`sev\\_num`")
        # Inside code spans, content is preserved literally
        self.assertIn("sev\\_num", text)

    def test_escaped_backtick_inside_code_span_preserved(self) -> None:
        """Backslash-backtick inside a code span should remain literal."""
        text, ranges = strip_and_parse_inline_formatting("`path\\`to\\`file`")
        self.assertIn("path\\`to\\`file", text)
        self.assertEqual(len(ranges), 1)
        self.assertEqual(ranges[0]["style"], "code")

    def test_escaped_backtick_does_not_open_code_span(self) -> None:
        """\\` should not act as a code span delimiter."""
        text, ranges = strip_and_parse_inline_formatting("\\`not code\\`")
        self.assertEqual(text, "`not code`")
        self.assertEqual(len(ranges), 0)

    # ---- Nested / combined formatting ----

    def test_bold_code_nesting(self) -> None:
        """**`code`:** should produce bold and code ranges without overflow."""
        text, ranges = strip_and_parse_inline_formatting("**`code`:** description")
        self.assertEqual(text, "code: description")
        styles = {r["style"] for r in ranges}
        self.assertIn("bold", styles)
        self.assertIn("code", styles)
        # Verify no range extends past the text length
        for r in ranges:
            self.assertLessEqual(r["end"], len(text))
            self.assertGreaterEqual(r["start"], 0)

    def test_multiple_formats_same_line(self) -> None:
        text, ranges = strip_and_parse_inline_formatting(
            "**bold**, *italic*, and `code`"
        )
        styles = [r["style"] for r in ranges]
        self.assertIn("bold", styles)
        self.assertIn("italic", styles)
        self.assertIn("code", styles)
        # All ranges should be valid
        for r in ranges:
            self.assertLess(r["start"], r["end"])
            self.assertGreaterEqual(r["start"], 0)
            self.assertLessEqual(r["end"], len(text))

    def test_multiple_code_spans_with_interleaved_formatting(self) -> None:
        """Multiple code spans separated by other formatting."""
        text, ranges = strip_and_parse_inline_formatting(
            "**bold** then `code1` then *italic* then `code2`"
        )
        styles = [r["style"] for r in ranges]
        self.assertIn("bold", styles)
        self.assertIn("italic", styles)
        code_ranges = [r for r in ranges if r["style"] == "code"]
        self.assertEqual(len(code_ranges), 2)
        # Verify the code span content was restored correctly
        self.assertEqual(text[code_ranges[0]["start"] : code_ranges[0]["end"]], "code1")
        self.assertEqual(text[code_ranges[1]["start"] : code_ranges[1]["end"]], "code2")

    def test_no_formatting(self) -> None:
        """Plain text without any markdown markers."""
        text, ranges = strip_and_parse_inline_formatting("Just plain text")
        self.assertEqual(text, "Just plain text")
        self.assertEqual(len(ranges), 0)

    def test_empty_string(self) -> None:
        text, ranges = strip_and_parse_inline_formatting("")
        self.assertEqual(text, "")
        self.assertEqual(len(ranges), 0)


class TestParseMarkdownBlocks(unittest.TestCase):
    """Tests for _parse_markdown_blocks()."""

    def test_headings(self) -> None:
        blocks = _parse_markdown_blocks("# Heading 1\n## Heading 2\n### Heading 3")
        self.assertTrue(all(b[0] == "text" for b in blocks))
        self.assertEqual(blocks[0][1]["line"], "# Heading 1")

    def test_fenced_code_block(self) -> None:
        md = "```python\ndef hello():\n    pass\n```"
        blocks = _parse_markdown_blocks(md)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0][0], "code_block")
        self.assertEqual(blocks[0][1]["language"], "python")
        self.assertEqual(blocks[0][1]["lines"], ["def hello():", "    pass"])

    def test_code_block_without_language(self) -> None:
        md = "```\nsome code\n```"
        blocks = _parse_markdown_blocks(md)
        self.assertEqual(blocks[0][0], "code_block")
        self.assertEqual(blocks[0][1]["language"], "")

    def test_table(self) -> None:
        md = "| A | B |\n|---|---|\n| 1 | 2 |"
        blocks = _parse_markdown_blocks(md)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0][0], "table")
        self.assertEqual(blocks[0][1]["headers"], ["A", "B"])
        self.assertEqual(blocks[0][1]["rows"], [["1", "2"]])

    def test_bullet_list(self) -> None:
        md = "- item one\n- item two\n- item three"
        blocks = _parse_markdown_blocks(md)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0][0], "bullet_list")
        items = blocks[0][1]["items"]
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0]["text"], "item one")

    def test_bullet_list_nested(self) -> None:
        md = "- top\n  - nested\n- top again"
        blocks = _parse_markdown_blocks(md)
        self.assertEqual(blocks[0][0], "bullet_list")
        items = blocks[0][1]["items"]
        self.assertEqual(items[0]["indent"], 0)
        self.assertEqual(items[1]["indent"], 1)
        self.assertEqual(items[2]["indent"], 0)

    def test_numbered_list(self) -> None:
        md = "1. first\n2. second\n3. third"
        blocks = _parse_markdown_blocks(md)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0][0], "numbered_list")
        items = blocks[0][1]["items"]
        self.assertEqual(len(items), 3)

    def test_horizontal_rule_dashes(self) -> None:
        blocks = _parse_markdown_blocks("---")
        self.assertEqual(blocks[0][0], "horizontal_rule")

    def test_horizontal_rule_asterisks(self) -> None:
        blocks = _parse_markdown_blocks("***")
        self.assertEqual(blocks[0][0], "horizontal_rule")

    def test_horizontal_rule_underscores(self) -> None:
        blocks = _parse_markdown_blocks("___")
        self.assertEqual(blocks[0][0], "horizontal_rule")

    def test_block_quote(self) -> None:
        md = "> This is a quote\n> Second line"
        blocks = _parse_markdown_blocks(md)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0][0], "block_quote")
        self.assertEqual(blocks[0][1]["lines"], ["This is a quote", "Second line"])

    def test_block_quote_strips_prefix(self) -> None:
        """The > prefix and optional space should be stripped."""
        blocks = _parse_markdown_blocks(">no space\n> with space")
        lines = blocks[0][1]["lines"]
        self.assertEqual(lines[0], "no space")
        self.assertEqual(lines[1], "with space")

    def test_blank_lines_around_headings_removed(self) -> None:
        """Blank lines adjacent to headings should be removed."""
        md = "text\n\n## Heading\n\nmore text"
        blocks = _parse_markdown_blocks(md)
        # The blank lines around the heading should be stripped
        self.assertNotIn(
            True,
            [
                b[0] == "text" and b[1]["line"].strip() == ""
                for b in blocks
                if b[0] == "text" and blocks.index(b) > 0
            ],
        )

    def test_mixed_block_types(self) -> None:
        """A document with multiple block types should parse correctly."""
        md = (
            "# Title\n"
            "\n"
            "Some text\n"
            "\n"
            "```\ncode\n```\n"
            "\n"
            "- bullet\n"
            "\n"
            "1. numbered\n"
            "\n"
            "---\n"
            "\n"
            "> quote\n"
            "\n"
            "| A | B |\n|---|---|\n| 1 | 2 |"
        )
        blocks = _parse_markdown_blocks(md)
        types = [b[0] for b in blocks]
        self.assertIn("text", types)
        self.assertIn("code_block", types)
        self.assertIn("bullet_list", types)
        self.assertIn("numbered_list", types)
        self.assertIn("horizontal_rule", types)
        self.assertIn("block_quote", types)
        self.assertIn("table", types)


class TestListBlankLineHandling(unittest.TestCase):
    """Tests for blank-line handling around list blocks."""

    def test_bullet_list_single_blank_line_splits_list(self) -> None:
        """A single blank line between bullet items should split into two lists."""
        blocks = _parse_markdown_blocks("- A\n- B\n\n- C")
        bullet_blocks = [b for b in blocks if b[0] == "bullet_list"]
        self.assertEqual(len(bullet_blocks), 2)
        self.assertEqual(len(bullet_blocks[0][1]["items"]), 2)
        self.assertEqual(len(bullet_blocks[1][1]["items"]), 1)

    def test_numbered_list_single_blank_line_splits_list(self) -> None:
        """A single blank line between numbered items should split into two lists."""
        blocks = _parse_markdown_blocks("1. A\n2. B\n\n3. C")
        numbered_blocks = [b for b in blocks if b[0] == "numbered_list"]
        self.assertEqual(len(numbered_blocks), 2)
        self.assertEqual(len(numbered_blocks[0][1]["items"]), 2)
        self.assertEqual(len(numbered_blocks[1][1]["items"]), 1)

    def test_bullet_list_double_blank_line_breaks_list(self) -> None:
        """Two consecutive blank lines should split bullet list into separate blocks."""
        blocks = _parse_markdown_blocks("- A\n\n\n- B")
        bullet_blocks = [b for b in blocks if b[0] == "bullet_list"]
        self.assertEqual(len(bullet_blocks), 2)
        self.assertEqual(len(bullet_blocks[0][1]["items"]), 1)
        self.assertEqual(len(bullet_blocks[1][1]["items"]), 1)

    def test_numbered_list_double_blank_line_breaks_list(self) -> None:
        """Two consecutive blank lines should split numbered list into separate blocks."""
        blocks = _parse_markdown_blocks("1. A\n\n\n2. B")
        numbered_blocks = [b for b in blocks if b[0] == "numbered_list"]
        self.assertEqual(len(numbered_blocks), 2)
        self.assertEqual(len(numbered_blocks[0][1]["items"]), 1)
        self.assertEqual(len(numbered_blocks[1][1]["items"]), 1)

    def test_numbered_list_no_blank_lines_unchanged(self) -> None:
        """Standard numbered list without blank lines still works."""
        blocks = _parse_markdown_blocks("1. A\n2. B\n3. C")
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0][0], "numbered_list")
        self.assertEqual(len(blocks[0][1]["items"]), 3)

    def test_blank_line_after_list_before_paragraph_stripped(self) -> None:
        """Blank line between a list and a paragraph should be removed."""
        blocks = _parse_markdown_blocks("- A\n- B\n\nSome paragraph")
        types = [b[0] for b in blocks]
        self.assertEqual(types, ["bullet_list", "text"])
        self.assertEqual(blocks[1][1]["line"], "Some paragraph")

    def test_blank_line_after_list_before_heading_kept(self) -> None:
        """Blank line between a list and a heading should be preserved."""
        blocks = _parse_markdown_blocks("- A\n- B\n\n## Heading")
        types = [b[0] for b in blocks]
        self.assertIn("text", types)
        blank_blocks = [
            b for b in blocks if b[0] == "text" and b[1]["line"].strip() == ""
        ]
        self.assertEqual(len(blank_blocks), 1)


class TestBuildTextLineRequestsClearsIndent(unittest.TestCase):
    """Tests that _build_text_line_requests clears inherited indent properties."""

    def test_normal_text_fields_use_reset_mask(self) -> None:
        """Non-heading text style fields should use the full reset mask."""
        content = {"line": "Just text"}
        requests: list[dict] = []
        _build_text_line_requests(content, 1, requests)
        style_reqs = [r for r in requests if "updateParagraphStyle" in r]
        self.assertEqual(len(style_reqs), 1)
        fields = style_reqs[0]["updateParagraphStyle"]["fields"]
        self.assertEqual(fields, _PARAGRAPH_RESET_FIELDS)

    def test_heading_fields_use_reset_mask(self) -> None:
        """Heading style fields should also use the full reset mask."""
        content = {"line": "## Heading"}
        requests: list[dict] = []
        _build_text_line_requests(content, 1, requests)
        style_reqs = [r for r in requests if "updateParagraphStyle" in r]
        fields = style_reqs[0]["updateParagraphStyle"]["fields"]
        self.assertEqual(fields, _PARAGRAPH_RESET_FIELDS)


class TestParseTableLines(unittest.TestCase):
    """Tests for _parse_table_lines() and _split_table_row()."""

    def test_basic_table(self) -> None:
        lines = [
            "| Header 1 | Header 2 | Header 3 |",
            "|----------|----------|----------|",
            "| cell 1   | cell 2   | cell 3   |",
            "| cell 4   | cell 5   | cell 6   |",
        ]
        headers, rows = _parse_table_lines(lines)
        self.assertEqual(headers, ["Header 1", "Header 2", "Header 3"])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0], ["cell 1", "cell 2", "cell 3"])

    def test_separator_row_skipped(self) -> None:
        lines = [
            "| A | B |",
            "|---|---|",
            "| 1 | 2 |",
        ]
        headers, rows = _parse_table_lines(lines)
        self.assertEqual(headers, ["A", "B"])
        self.assertEqual(len(rows), 1)

    def test_alignment_separators(self) -> None:
        """Separator rows with alignment colons should be skipped."""
        lines = [
            "| Left | Center | Right |",
            "|:-----|:------:|------:|",
            "| a    |   b    |     c |",
        ]
        headers, rows = _parse_table_lines(lines)
        self.assertEqual(len(headers), 3)
        self.assertEqual(len(rows), 1)

    def test_row_padding(self) -> None:
        """Rows with fewer columns should be padded."""
        lines = [
            "| A | B | C |",
            "|---|---|---|",
            "| 1 |",
        ]
        headers, rows = _parse_table_lines(lines)
        self.assertEqual(len(rows[0]), 3)
        self.assertEqual(rows[0][0], "1")
        self.assertEqual(rows[0][1], "")
        self.assertEqual(rows[0][2], "")

    def test_pipe_in_backtick(self) -> None:
        """Pipes inside backtick code spans should not split cells."""
        lines = [
            "| Col A | Col B |",
            "|-------|-------|",
            "| `x|y` | normal |",
        ]
        headers, rows = _parse_table_lines(lines)
        self.assertEqual(len(headers), 2)
        self.assertEqual(rows[0][0], "`x|y`")

    def test_empty_table(self) -> None:
        headers, rows = _parse_table_lines([])
        self.assertEqual(headers, [])
        self.assertEqual(rows, [])

    def test_split_table_row_basic(self) -> None:
        result = _split_table_row(" foo | bar | baz ")
        self.assertEqual(result, ["foo", "bar", "baz"])

    def test_split_table_row_backtick_protection(self) -> None:
        result = _split_table_row(" foo | `a|b` | bar ")
        self.assertEqual(result, ["foo", "`a|b`", "bar"])


class TestBuildTextLineRequests(unittest.TestCase):
    """Tests for _build_text_line_requests()."""

    def _get_paragraph_style_requests(
        self,
        requests: list[dict],
    ) -> list[dict]:
        """Extract updateParagraphStyle requests."""
        return [r for r in requests if "updateParagraphStyle" in r]

    def test_heading_gets_heading_style(self) -> None:
        """Heading lines should produce an updateParagraphStyle with HEADING_X."""
        for level in range(1, 7):
            hashes = "#" * level
            content = {"line": f"{hashes} Title"}
            requests: list[dict] = []
            _build_text_line_requests(content, 1, requests)

            style_reqs = self._get_paragraph_style_requests(requests)
            self.assertEqual(
                len(style_reqs), 1, f"Expected 1 style request for h{level}"
            )
            named_style = style_reqs[0]["updateParagraphStyle"]["paragraphStyle"][
                "namedStyleType"
            ]
            self.assertEqual(named_style, f"HEADING_{level}")

    def test_normal_text_gets_normal_text_style(self) -> None:
        """Non-heading text should explicitly get NORMAL_TEXT style."""
        content = {"line": "Just a paragraph"}
        requests: list[dict] = []
        _build_text_line_requests(content, 1, requests)

        style_reqs = self._get_paragraph_style_requests(requests)
        self.assertEqual(len(style_reqs), 1, "Expected 1 style request for normal text")
        named_style = style_reqs[0]["updateParagraphStyle"]["paragraphStyle"][
            "namedStyleType"
        ]
        self.assertEqual(named_style, "NORMAL_TEXT")

    def test_empty_line_gets_normal_text_style(self) -> None:
        """Blank lines should also get NORMAL_TEXT to avoid inheriting heading style."""
        content = {"line": ""}
        requests: list[dict] = []
        _build_text_line_requests(content, 1, requests)

        style_reqs = self._get_paragraph_style_requests(requests)
        self.assertEqual(len(style_reqs), 1)
        named_style = style_reqs[0]["updateParagraphStyle"]["paragraphStyle"][
            "namedStyleType"
        ]
        self.assertEqual(named_style, "NORMAL_TEXT")

    def test_style_range_matches_inserted_text(self) -> None:
        """The updateParagraphStyle range should cover the inserted text."""
        content = {"line": "Hello world"}
        requests: list[dict] = []
        start_index = 5
        _build_text_line_requests(content, start_index, requests)

        insert_req = requests[0]["insertText"]
        inserted_text = insert_req["text"]
        self.assertEqual(insert_req["location"]["index"], start_index)

        style_req = [r for r in requests if "updateParagraphStyle" in r][0]
        rng = style_req["updateParagraphStyle"]["range"]
        self.assertEqual(rng["startIndex"], start_index)
        self.assertEqual(rng["endIndex"], start_index + len(inserted_text))

    def test_returns_updated_index(self) -> None:
        """Return value should be current_index + len(text + newline)."""
        content = {"line": "# Heading"}
        requests: list[dict] = []
        new_index = _build_text_line_requests(content, 10, requests)
        # "Heading" + "\n" = 8 chars
        self.assertEqual(new_index, 10 + len("Heading\n"))


class TestUtf16Helpers(unittest.TestCase):
    """Tests for _utf16_len() and _utf16_offset()."""

    def test_ascii(self) -> None:
        self.assertEqual(_utf16_len("hello"), 5)

    def test_bmp_characters(self) -> None:
        # CJK characters are BMP (single UTF-16 code unit each)
        self.assertEqual(_utf16_len("世界"), 2)

    def test_non_bmp_emoji(self) -> None:
        # U+1F600 is a non-BMP codepoint = 2 UTF-16 code units
        self.assertEqual(_utf16_len("😀"), 2)

    def test_mixed_ascii_and_emoji(self) -> None:
        # "Hi" = 2, "😀" = 2, "!" = 1 → total 5
        self.assertEqual(_utf16_len("Hi😀!"), 5)

    def test_multiple_non_bmp(self) -> None:
        self.assertEqual(_utf16_len("😀😃😄"), 6)

    def test_empty_string(self) -> None:
        self.assertEqual(_utf16_len(""), 0)

    def test_flag_emoji(self) -> None:
        # 🇺🇸 is U+1F1FA U+1F1F8 (2 non-BMP codepoints, each a surrogate pair)
        self.assertEqual(_utf16_len("🇺🇸"), 4)
        self.assertEqual(len("🇺🇸"), 2)  # 2 code points

    def test_flag_emoji_mixed(self) -> None:
        # G=1, o=1, space=1, 🇺🇸=4 → 7
        self.assertEqual(_utf16_len("Go 🇺🇸"), 7)
        self.assertEqual(len("Go 🇺🇸"), 5)  # 5 code points

    def test_multiple_flag_emojis(self) -> None:
        self.assertEqual(_utf16_len("🇺🇸🇬🇧"), 8)

    def test_utf16_offset_after_emoji(self) -> None:
        # After "😀" (1 codepoint), UTF-16 offset should be 2
        self.assertEqual(_utf16_offset("😀a", 1), 2)

    def test_utf16_offset_after_flag_emoji(self) -> None:
        # "🇺🇸" is 2 codepoints; after both → UTF-16 offset 4
        self.assertEqual(_utf16_offset("🇺🇸abc", 2), 4)

    def test_utf16_offset_between_flag_codepoints(self) -> None:
        # Between the two regional indicators → UTF-16 offset 2
        self.assertEqual(_utf16_offset("🇺🇸abc", 1), 2)

    def test_utf16_offset_between_emojis(self) -> None:
        # "😀😃" — offset 0=0, 1=2, 2=4
        self.assertEqual(_utf16_offset("😀😃", 0), 0)
        self.assertEqual(_utf16_offset("😀😃", 1), 2)
        self.assertEqual(_utf16_offset("😀😃", 2), 4)

    def test_math_symbol(self) -> None:
        # 𝕏 (U+1D54F) is non-BMP → 2 UTF-16 code units
        self.assertEqual(_utf16_len("𝕏"), 2)

    def test_cjk_extension_b(self) -> None:
        # 𠀀 (U+20000) is CJK Extension B, non-BMP → 2 UTF-16 code units
        self.assertEqual(_utf16_len("𠀀"), 2)


class TestAddInlineFormatRequestsUtf16(unittest.TestCase):
    """Tests for _add_inline_format_requests with UTF-16 conversion."""

    def test_bold_with_emoji_prefix(self) -> None:
        text = "😀bold"
        fmt_ranges = [{"start": 1, "end": 5, "style": "bold"}]
        requests: list[dict] = []
        base = 10
        _add_inline_format_requests(fmt_ranges, base, requests, text)
        self.assertEqual(len(requests), 1)
        req = requests[0]["updateTextStyle"]
        # "😀" = 2 UTF-16 code units, so start = 10 + 2 = 12
        self.assertEqual(req["range"]["startIndex"], 12)
        # "😀bold" → offset 5 = 2 + 4 = 6 UTF-16 units, so end = 10 + 6 = 16
        self.assertEqual(req["range"]["endIndex"], 16)

    def test_bold_with_flag_emoji_prefix(self) -> None:
        text = "🇺🇸bold"
        # code-point offsets: "🇺🇸" is 2 codepoints, "bold" is 4
        fmt_ranges = [{"start": 2, "end": 6, "style": "bold"}]
        requests: list[dict] = []
        base = 10
        _add_inline_format_requests(fmt_ranges, base, requests, text)
        req = requests[0]["updateTextStyle"]
        # "🇺🇸" = 4 UTF-16 code units → startIndex = 10 + 4 = 14
        self.assertEqual(req["range"]["startIndex"], 14)
        # "🇺🇸bold" → offset 6 = 4 + 4 = 8 → endIndex = 10 + 8 = 18
        self.assertEqual(req["range"]["endIndex"], 18)

    def test_format_between_flag_emojis(self) -> None:
        text = "🇺🇸bold🇬🇧"
        # "bold" starts at codepoint 2, ends at codepoint 6
        fmt_ranges = [{"start": 2, "end": 6, "style": "bold"}]
        requests: list[dict] = []
        base = 10
        _add_inline_format_requests(fmt_ranges, base, requests, text)
        req = requests[0]["updateTextStyle"]
        # start: 🇺🇸 = 4 UTF-16 units → 10 + 4 = 14
        self.assertEqual(req["range"]["startIndex"], 14)
        # end: 🇺🇸bold = 4 + 4 = 8 → 10 + 8 = 18
        self.assertEqual(req["range"]["endIndex"], 18)

    def test_ascii_text_unchanged(self) -> None:
        text = "hello bold world"
        fmt_ranges = [{"start": 6, "end": 10, "style": "bold"}]
        requests: list[dict] = []
        base = 5
        _add_inline_format_requests(fmt_ranges, base, requests, text)
        req = requests[0]["updateTextStyle"]
        # ASCII: UTF-16 offsets == code-point offsets
        self.assertEqual(req["range"]["startIndex"], 11)
        self.assertEqual(req["range"]["endIndex"], 15)


class TestBuildTextLineRequestsUtf16(unittest.TestCase):
    """Tests for _build_text_line_requests with UTF-16 index calculation."""

    def test_heading_with_emoji(self) -> None:
        content = {"line": "# Hello 😀"}
        requests: list[dict] = []
        new_index = _build_text_line_requests(content, 1, requests)
        # Stripped text: "Hello 😀\n" → _utf16_len = 6 + 2 + 1 = 9
        self.assertEqual(new_index, 1 + 9)

    def test_heading_with_flag_emoji(self) -> None:
        content = {"line": "# Hello 🇺🇸"}
        requests: list[dict] = []
        new_index = _build_text_line_requests(content, 1, requests)
        # "Hello 🇺🇸\n" → 6 + 4 + 1 = 11 UTF-16 code units
        self.assertEqual(new_index, 1 + 11)

    def test_plain_text_with_emoji(self) -> None:
        content = {"line": "😀 hi"}
        requests: list[dict] = []
        new_index = _build_text_line_requests(content, 1, requests)
        # "😀 hi\n" → 2 + 1 + 2 + 1 = 6 UTF-16 code units
        self.assertEqual(new_index, 1 + 6)

    def test_plain_text_with_flag_emoji(self) -> None:
        content = {"line": "🇺🇸 US"}
        requests: list[dict] = []
        new_index = _build_text_line_requests(content, 1, requests)
        # "🇺🇸 US\n" → 4 + 1 + 2 + 1 = 8 UTF-16 code units
        self.assertEqual(new_index, 1 + 8)


class TestBuildCodeBlockRequestsUtf16(unittest.TestCase):
    """Tests for _build_code_block_requests with UTF-16 index calculation."""

    def test_code_block_with_emoji(self) -> None:
        content = {"lines": ["print('😀')"]}
        requests: list[dict] = []
        new_index = _build_code_block_requests(content, 1, requests)
        # "print('😀')\n" → 7 + 2 + 2 + 1 = 12 UTF-16 code units
        code_text = "print('😀')\n"
        expected = 1 + _utf16_len(code_text)
        self.assertEqual(new_index, expected)

    def test_code_block_with_flag_emoji(self) -> None:
        content = {"lines": ["print('🇺🇸')"]}
        requests: list[dict] = []
        new_index = _build_code_block_requests(content, 1, requests)
        code_text = "print('🇺🇸')\n"
        expected = 1 + _utf16_len(code_text)
        self.assertEqual(new_index, expected)


class TestBuildBulletListRequestsUtf16(unittest.TestCase):
    """Tests for _build_bullet_list_requests with UTF-16 index calculation."""

    def test_bullet_list_with_flag_emoji(self) -> None:
        content = {"items": [{"text": "🇺🇸 America", "indent": 0}]}
        requests: list[dict] = []
        new_index = _build_bullet_list_requests(content, 1, requests)
        # "🇺🇸 America\n" → 4 + 1 + 7 + 1 = 13 UTF-16 code units
        item_text = "🇺🇸 America\n"
        expected = 1 + _utf16_len(item_text)
        self.assertEqual(new_index, expected)


class TestBuildNumberedListRequestsUtf16(unittest.TestCase):
    """Tests for _build_numbered_list_requests with UTF-16 index calculation."""

    def test_numbered_list_with_flag_emoji(self) -> None:
        content = {"items": [{"text": "🇺🇸 America", "indent": 0}]}
        requests: list[dict] = []
        new_index = _build_numbered_list_requests(content, 1, requests)
        item_text = "🇺🇸 America\n"
        expected = 1 + _utf16_len(item_text)
        self.assertEqual(new_index, expected)

    def test_numbered_list_multiple_emoji_items(self) -> None:
        content = {
            "items": [
                {"text": "🇺🇸 First", "indent": 0},
                {"text": "🇬🇧 Second", "indent": 0},
            ]
        }
        requests: list[dict] = []
        new_index = _build_numbered_list_requests(content, 1, requests)
        item1 = "🇺🇸 First\n"
        item2 = "🇬🇧 Second\n"
        expected = 1 + _utf16_len(item1) + _utf16_len(item2)
        self.assertEqual(new_index, expected)


class TestBuildBlockQuoteRequestsUtf16(unittest.TestCase):
    """Tests for _build_block_quote_requests with UTF-16 index calculation."""

    def test_block_quote_with_emoji(self) -> None:
        content = {"lines": ["🇺🇸 Quote text"]}
        requests: list[dict] = []
        new_index = _build_block_quote_requests(content, 1, requests)
        line_text = "🇺🇸 Quote text\n"
        expected = 1 + _utf16_len(line_text)
        self.assertEqual(new_index, expected)

    def test_block_quote_multiple_emoji_lines(self) -> None:
        content = {"lines": ["🇺🇸 Line one", "🇬🇧 Line two"]}
        requests: list[dict] = []
        new_index = _build_block_quote_requests(content, 1, requests)
        line1 = "🇺🇸 Line one\n"
        line2 = "🇬🇧 Line two\n"
        expected = 1 + _utf16_len(line1) + _utf16_len(line2)
        self.assertEqual(new_index, expected)


class TestTextLineFormatRangesUtf16(unittest.TestCase):
    """End-to-end tests verifying that _build_text_line_requests produces
    correct UTF-16 format range indices (not just index advance) when emoji
    precedes inline formatting."""

    def test_bold_after_flag_emoji_format_indices(self) -> None:
        """Repro case: 🇺🇸🇬🇧🇫🇷🇩🇪🇯🇵 **AAAAAAAAAA** BBBBBBBBBB
        Bold must land on AAAAAAAAAA at UTF-16 [22,32), not on emoji at [12,22)."""
        content = {"line": "🇺🇸🇬🇧🇫🇷🇩🇪🇯🇵 **AAAAAAAAAA** BBBBBBBBBB"}
        requests: list[dict] = []
        _build_text_line_requests(content, 1, requests)
        # Find the updateTextStyle request for bold
        bold_reqs = [
            r["updateTextStyle"]
            for r in requests
            if "updateTextStyle" in r
            and r["updateTextStyle"].get("textStyle", {}).get("bold")
        ]
        self.assertEqual(len(bold_reqs), 1)
        bold_range = bold_reqs[0]["range"]
        # 5 flags × 4 UTF-16 units + 1 space = 21, so AAAA starts at 1+21=22
        self.assertEqual(bold_range["startIndex"], 22)
        # AAAAAAAAAA = 10 chars, so ends at 32
        self.assertEqual(bold_range["endIndex"], 32)

    def test_multiline_index_accumulation(self) -> None:
        """Two lines with emoji: second line's insertText index must account
        for the first line's full UTF-16 length."""
        line1 = {"line": "🇺🇸🇬🇧🇫🇷🇩🇪🇯🇵 Hello"}
        line2 = {"line": "**bold**"}
        requests: list[dict] = []
        idx = _build_text_line_requests(line1, 1, requests)
        _build_text_line_requests(line2, idx, requests)
        # Line 1: "🇺🇸🇬🇧🇫🇷🇩🇪🇯🇵 Hello\n" → 5×4+1+5+1 = 27 UTF-16 units
        # idx after line 1 = 1 + 27 = 28
        self.assertEqual(idx, 28)
        # Line 2 insertText must be at index 28
        insert_reqs = [r for r in requests if "insertText" in r]
        self.assertEqual(insert_reqs[1]["insertText"]["location"]["index"], 28)


class TestSetParagraphStyle(unittest.TestCase):
    """Tests for set_paragraph_style request structure."""

    def test_paragraph_style_alignment_request(self) -> None:
        """Verify alignment is set correctly in an updateParagraphStyle request."""
        request = {
            "updateParagraphStyle": {
                "range": {"startIndex": 1, "endIndex": 10},
                "paragraphStyle": {"alignment": "CENTER"},
                "fields": "alignment",
            }
        }
        self.assertEqual(
            request["updateParagraphStyle"]["paragraphStyle"]["alignment"], "CENTER"
        )

    def test_paragraph_style_named_style(self) -> None:
        request = {
            "updateParagraphStyle": {
                "range": {"startIndex": 1, "endIndex": 10},
                "paragraphStyle": {"namedStyleType": "TITLE"},
                "fields": "namedStyleType",
            }
        }
        self.assertEqual(
            request["updateParagraphStyle"]["paragraphStyle"]["namedStyleType"], "TITLE"
        )

    def test_paragraph_style_spacing(self) -> None:
        request = {
            "updateParagraphStyle": {
                "range": {"startIndex": 1, "endIndex": 10},
                "paragraphStyle": {
                    "lineSpacing": 115,
                    "spaceAbove": {"magnitude": 6, "unit": "PT"},
                    "spaceBelow": {"magnitude": 10, "unit": "PT"},
                },
                "fields": "lineSpacing,spaceAbove,spaceBelow",
            }
        }
        style = request["updateParagraphStyle"]["paragraphStyle"]
        self.assertEqual(style["lineSpacing"], 115)
        self.assertEqual(style["spaceAbove"]["magnitude"], 6)
        self.assertEqual(style["spaceBelow"]["magnitude"], 10)

    def test_paragraph_style_shading(self) -> None:
        request = {
            "updateParagraphStyle": {
                "range": {"startIndex": 1, "endIndex": 10},
                "paragraphStyle": {
                    "shading": {
                        "backgroundColor": {
                            "color": {
                                "rgbColor": {"red": 1.0, "green": 1.0, "blue": 0.8}
                            }
                        }
                    }
                },
                "fields": "shading",
            }
        }
        rgb = request["updateParagraphStyle"]["paragraphStyle"]["shading"][
            "backgroundColor"
        ]["color"]["rgbColor"]
        self.assertAlmostEqual(rgb["blue"], 0.8)

    def test_tab_id_injected_for_paragraph_style(self) -> None:
        request = {
            "updateParagraphStyle": {
                "range": {"startIndex": 1, "endIndex": 10},
                "paragraphStyle": {"alignment": "CENTER"},
                "fields": "alignment",
            }
        }
        _add_tab_id_to_request(request, "tab456")
        self.assertEqual(request["updateParagraphStyle"]["range"]["tabId"], "tab456")


class TestUpdateTableCellStyle(unittest.TestCase):
    """Tests for update_table_cell_style request structure."""

    def test_tab_id_injected_for_update_table_cell_style(self) -> None:
        request = {
            "updateTableCellStyle": {
                "tableRange": {
                    "tableCellLocation": {
                        "tableStartLocation": {"index": 5},
                        "rowIndex": 0,
                        "columnIndex": 0,
                    },
                    "rowSpan": 1,
                    "columnSpan": 1,
                },
                "tableCellStyle": {
                    "backgroundColor": {
                        "color": {"rgbColor": {"red": 1.0, "green": 0.0, "blue": 0.0}}
                    }
                },
                "fields": "backgroundColor",
            }
        }
        _add_tab_id_to_request(request, "tab123")
        start_loc = request["updateTableCellStyle"]["tableRange"]["tableCellLocation"][
            "tableStartLocation"
        ]
        self.assertEqual(start_loc["tabId"], "tab123")

    def test_background_color_request_structure(self) -> None:
        """Verify the request dict structure for background_color."""
        request = {
            "updateTableCellStyle": {
                "tableRange": {
                    "tableCellLocation": {
                        "tableStartLocation": {"index": 5},
                        "rowIndex": 1,
                        "columnIndex": 2,
                    },
                    "rowSpan": 1,
                    "columnSpan": 1,
                },
                "tableCellStyle": {
                    "backgroundColor": {
                        "color": {"rgbColor": {"red": 0.2, "green": 0.6, "blue": 1.0}}
                    }
                },
                "fields": "backgroundColor",
            }
        }
        cell_style = request["updateTableCellStyle"]["tableCellStyle"]
        self.assertIn("backgroundColor", cell_style)
        rgb = cell_style["backgroundColor"]["color"]["rgbColor"]
        self.assertAlmostEqual(rgb["red"], 0.2)
        self.assertAlmostEqual(rgb["green"], 0.6)
        self.assertAlmostEqual(rgb["blue"], 1.0)


class TestFormatTextFontFamilyAndStrikethrough(unittest.TestCase):
    """Tests for format_text with font_family and strikethrough via action_to_requests."""

    def test_font_family_produces_weighted_font_family(self) -> None:
        requests = action_to_requests(
            {
                "action": "format_text",
                "start_index": 1,
                "end_index": 5,
                "font_family": "Roboto Mono",
            }
        )
        self.assertEqual(len(requests), 1)
        style = requests[0]["updateTextStyle"]["textStyle"]
        self.assertEqual(
            style["weightedFontFamily"],
            {"fontFamily": "Roboto Mono", "weight": 400},
        )
        self.assertIn(
            "weightedFontFamily",
            requests[0]["updateTextStyle"]["fields"],
        )

    def test_strikethrough_produces_strikethrough_field(self) -> None:
        requests = action_to_requests(
            {
                "action": "format_text",
                "start_index": 1,
                "end_index": 10,
                "strikethrough": True,
            }
        )
        self.assertEqual(len(requests), 1)
        style = requests[0]["updateTextStyle"]["textStyle"]
        self.assertTrue(style["strikethrough"])
        self.assertIn("strikethrough", requests[0]["updateTextStyle"]["fields"])

    def test_strikethrough_false(self) -> None:
        requests = action_to_requests(
            {
                "action": "format_text",
                "start_index": 1,
                "end_index": 10,
                "strikethrough": False,
            }
        )
        style = requests[0]["updateTextStyle"]["textStyle"]
        self.assertFalse(style["strikethrough"])

    def test_font_family_with_bold(self) -> None:
        requests = action_to_requests(
            {
                "action": "format_text",
                "start_index": 1,
                "end_index": 5,
                "bold": True,
                "font_family": "Arial",
            }
        )
        style = requests[0]["updateTextStyle"]["textStyle"]
        self.assertTrue(style["bold"])
        self.assertEqual(style["weightedFontFamily"]["fontFamily"], "Arial")
        fields = requests[0]["updateTextStyle"]["fields"]
        self.assertIn("bold", fields)
        self.assertIn("weightedFontFamily", fields)


class TestSetColumnWidths(unittest.TestCase):
    """Tests for set_column_widths request structure and tab_id injection."""

    def test_column_width_request_structure(self) -> None:
        """Verify updateTableColumnProperties request has correct structure."""
        request = {
            "updateTableColumnProperties": {
                "tableStartLocation": {"index": 5},
                "columnIndices": [0],
                "tableColumnProperties": {
                    "widthType": "FIXED_WIDTH",
                    "width": {"magnitude": 150.0, "unit": "PT"},
                },
                "fields": "widthType,width",
            }
        }
        props = request["updateTableColumnProperties"]
        self.assertEqual(props["tableStartLocation"]["index"], 5)
        self.assertEqual(props["columnIndices"], [0])
        self.assertEqual(props["tableColumnProperties"]["width"]["magnitude"], 150.0)

    def test_tab_id_injection_for_column_properties(self) -> None:
        """Verify _add_tab_id_to_request injects tabId for updateTableColumnProperties."""
        request = {
            "updateTableColumnProperties": {
                "tableStartLocation": {"index": 5},
                "columnIndices": [0],
                "tableColumnProperties": {
                    "widthType": "FIXED_WIDTH",
                    "width": {"magnitude": 150.0, "unit": "PT"},
                },
                "fields": "widthType,width",
            }
        }
        _add_tab_id_to_request(request, "tab456")
        start_loc = request["updateTableColumnProperties"]["tableStartLocation"]
        self.assertEqual(start_loc["tabId"], "tab456")

    def test_multiple_columns_produce_multiple_requests(self) -> None:
        """Verify that multiple column_widths entries produce multiple requests."""
        column_widths = [
            {"column_index": 0, "width_pt": 100.0},
            {"column_index": 1, "width_pt": 200.0},
            {"column_index": 2, "width_pt": 150.0},
        ]
        requests = []
        for col in column_widths:
            requests.append(
                {
                    "updateTableColumnProperties": {
                        "tableStartLocation": {"index": 5},
                        "columnIndices": [col["column_index"]],
                        "tableColumnProperties": {
                            "widthType": "FIXED_WIDTH",
                            "width": {"magnitude": col["width_pt"], "unit": "PT"},
                        },
                        "fields": "widthType,width",
                    }
                }
            )
        self.assertEqual(len(requests), 3)
        self.assertEqual(
            requests[0]["updateTableColumnProperties"]["columnIndices"], [0]
        )
        self.assertEqual(
            requests[1]["updateTableColumnProperties"]["tableColumnProperties"][
                "width"
            ]["magnitude"],
            200.0,
        )
        self.assertEqual(
            requests[2]["updateTableColumnProperties"]["columnIndices"], [2]
        )


class TestGetDocumentFormatting(unittest.TestCase):
    """Tests for get_document_formatting output structure."""

    @unittest.mock.patch("google_api.get_document")
    def test_extracts_paragraph_style(self, mock_get_doc: unittest.mock.Mock) -> None:
        """Verify paragraph style fields are extracted."""
        mock_get_doc.return_value = {
            "success": True,
            "data": {
                "title": "Test Doc",
                "tabs": [
                    {
                        "tabProperties": {"tabId": "t1"},
                        "documentTab": {
                            "body": {
                                "content": [
                                    {
                                        "startIndex": 0,
                                        "endIndex": 10,
                                        "paragraph": {
                                            "paragraphStyle": {
                                                "namedStyleType": "HEADING_1",
                                                "alignment": "CENTER",
                                            },
                                            "elements": [
                                                {
                                                    "textRun": {
                                                        "content": "Hello",
                                                        "textStyle": {"bold": True},
                                                    }
                                                }
                                            ],
                                        },
                                    }
                                ],
                            },
                            "lists": {},
                        },
                    }
                ],
            },
        }
        result = get_document_formatting("doc123")
        self.assertTrue(result["success"])
        elements = result["data"]["elements"]
        self.assertEqual(len(elements), 1)
        para = elements[0]
        self.assertEqual(para["type"], "paragraph")
        self.assertEqual(para["named_style"], "HEADING_1")
        self.assertEqual(para["alignment"], "CENTER")
        self.assertEqual(len(para["text_runs"]), 1)
        self.assertTrue(para["text_runs"][0]["bold"])

    @unittest.mock.patch("google_api.get_document")
    def test_extracts_table_info(self, mock_get_doc: unittest.mock.Mock) -> None:
        """Verify table cells are extracted."""
        mock_get_doc.return_value = {
            "success": True,
            "data": {
                "title": "Test Doc",
                "tabs": [
                    {
                        "tabProperties": {"tabId": "t1"},
                        "documentTab": {
                            "body": {
                                "content": [
                                    {
                                        "startIndex": 0,
                                        "table": {
                                            "rows": 1,
                                            "columns": 1,
                                            "tableRows": [
                                                {
                                                    "tableCells": [
                                                        {
                                                            "tableCellStyle": {
                                                                "backgroundColor": {
                                                                    "color": {
                                                                        "rgbColor": {
                                                                            "red": 1.0,
                                                                            "green": 0.0,
                                                                            "blue": 0.0,
                                                                        }
                                                                    }
                                                                }
                                                            },
                                                            "content": [
                                                                {
                                                                    "paragraph": {
                                                                        "elements": [
                                                                            {
                                                                                "textRun": {
                                                                                    "content": "Cell",
                                                                                }
                                                                            }
                                                                        ],
                                                                    }
                                                                }
                                                            ],
                                                        }
                                                    ],
                                                }
                                            ],
                                        },
                                    }
                                ],
                            },
                            "lists": {},
                        },
                    }
                ],
            },
        }
        result = get_document_formatting("doc123")
        self.assertTrue(result["success"])
        table = result["data"]["elements"][0]
        self.assertEqual(table["type"], "table")
        self.assertEqual(len(table["cells"]), 1)
        cell = table["cells"][0]
        self.assertAlmostEqual(cell["background_color"]["red"], 1.0)
        self.assertEqual(cell["paragraphs"][0]["text"], "Cell")


class TestParseHtmlTableCells(unittest.TestCase):
    """Tests for _parse_html table cell background colors and <th> detection."""

    def test_td_background_color_from_style(self) -> None:
        """<td style="background-color: #ff0000"> should produce background_color."""
        html = '<table><tr><td style="background-color: #ff0000">Red</td></tr></table>'
        elements = _parse_html(html)
        self.assertEqual(len(elements), 1)
        self.assertEqual(elements[0]["type"], "table")
        cell = elements[0]["rows"][0][0]
        self.assertIn("background_color", cell)
        self.assertAlmostEqual(cell["background_color"]["red"], 1.0)
        self.assertAlmostEqual(cell["background_color"]["green"], 0.0)
        self.assertAlmostEqual(cell["background_color"]["blue"], 0.0)

    def test_td_background_color_from_bgcolor_attr(self) -> None:
        """<td bgcolor="#00ff00"> should produce background_color."""
        html = '<table><tr><td bgcolor="#00ff00">Green</td></tr></table>'
        elements = _parse_html(html)
        cell = elements[0]["rows"][0][0]
        self.assertIn("background_color", cell)
        self.assertAlmostEqual(cell["background_color"]["green"], 1.0)

    def test_th_sets_is_header(self) -> None:
        """<th> should set is_header=True on the cell entry."""
        html = "<table><tr><th>Header</th></tr></table>"
        elements = _parse_html(html)
        cell = elements[0]["rows"][0][0]
        self.assertTrue(cell.get("is_header"))

    def test_td_without_style_has_no_background(self) -> None:
        """Plain <td> should not have background_color."""
        html = "<table><tr><td>Plain</td></tr></table>"
        elements = _parse_html(html)
        cell = elements[0]["rows"][0][0]
        self.assertNotIn("background_color", cell)
        self.assertNotIn("is_header", cell)

    def test_th_with_background_color(self) -> None:
        """<th style="background-color: #0000ff"> should have both is_header and background_color."""
        html = '<table><tr><th style="background-color: #0000ff">Blue Header</th></tr></table>'
        elements = _parse_html(html)
        cell = elements[0]["rows"][0][0]
        self.assertTrue(cell.get("is_header"))
        self.assertIn("background_color", cell)
        self.assertAlmostEqual(cell["background_color"]["blue"], 1.0)


class TestAddTabIdRemainingTypes(unittest.TestCase):
    """Tests for _add_tab_id_to_request with mergeTableCells, unmergeTableCells,
    updateTableRowStyle, and insertInlineImage."""

    def test_merge_table_cells(self) -> None:
        request = {
            "mergeTableCells": {
                "tableRange": {
                    "tableCellLocation": {
                        "tableStartLocation": {"index": 5},
                        "rowIndex": 0,
                        "columnIndex": 0,
                    },
                    "rowSpan": 2,
                    "columnSpan": 2,
                },
            }
        }
        _add_tab_id_to_request(request, "tab_merge")
        start_loc = request["mergeTableCells"]["tableRange"]["tableCellLocation"][
            "tableStartLocation"
        ]
        self.assertEqual(start_loc["tabId"], "tab_merge")

    def test_unmerge_table_cells(self) -> None:
        request = {
            "unmergeTableCells": {
                "tableRange": {
                    "tableCellLocation": {
                        "tableStartLocation": {"index": 5},
                        "rowIndex": 0,
                        "columnIndex": 0,
                    },
                    "rowSpan": 2,
                    "columnSpan": 2,
                },
            }
        }
        _add_tab_id_to_request(request, "tab_unmerge")
        start_loc = request["unmergeTableCells"]["tableRange"]["tableCellLocation"][
            "tableStartLocation"
        ]
        self.assertEqual(start_loc["tabId"], "tab_unmerge")

    def test_update_table_row_style(self) -> None:
        request = {
            "updateTableRowStyle": {
                "tableStartLocation": {"index": 5},
                "rowIndex": 0,
                "tableRowStyle": {"minRowHeight": {"magnitude": 50, "unit": "PT"}},
                "fields": "minRowHeight",
            }
        }
        _add_tab_id_to_request(request, "tab_row")
        start_loc = request["updateTableRowStyle"]["tableStartLocation"]
        self.assertEqual(start_loc["tabId"], "tab_row")

    def test_insert_inline_image(self) -> None:
        request = {
            "insertInlineImage": {
                "location": {"index": 10},
                "uri": "https://example.com/image.png",
                "objectSize": {
                    "width": {"magnitude": 200, "unit": "PT"},
                    "height": {"magnitude": 100, "unit": "PT"},
                },
            }
        }
        _add_tab_id_to_request(request, "tab_img")
        location = request["insertInlineImage"]["location"]
        self.assertEqual(location["tabId"], "tab_img")


class TestGetElementText(unittest.TestCase):
    """Tests for _get_element_text()."""

    def test_text_run(self) -> None:
        element = {"textRun": {"content": "Hello world"}}
        self.assertEqual(_get_element_text(element), "Hello world")

    def test_text_run_empty(self) -> None:
        element = {"textRun": {"content": ""}}
        self.assertEqual(_get_element_text(element), "")

    def test_person_chip_with_name(self) -> None:
        element = {
            "startIndex": 10,
            "endIndex": 11,
            "person": {
                "personId": "12345",
                "personProperties": {
                    "name": "Alexandre Dias",
                    "email": "alexdias@fb.com",
                },
            },
        }
        self.assertEqual(_get_element_text(element), "Alexandre Dias")

    def test_person_chip_no_name_falls_back_to_id(self) -> None:
        element = {
            "person": {
                "personId": "67890",
                "personProperties": {},
            },
        }
        self.assertEqual(_get_element_text(element), "67890")

    def test_rich_link_with_title(self) -> None:
        element = {
            "richLink": {
                "richLinkProperties": {
                    "title": "Project Doc",
                    "uri": "https://docs.google.com/doc/123",
                },
            },
        }
        self.assertEqual(_get_element_text(element), "Project Doc")

    def test_rich_link_no_title_falls_back_to_uri(self) -> None:
        element = {
            "richLink": {
                "richLinkProperties": {
                    "uri": "https://docs.google.com/doc/123",
                },
            },
        }
        self.assertEqual(_get_element_text(element), "https://docs.google.com/doc/123")

    def test_date_chip_with_display_text(self) -> None:
        element = {
            "startIndex": 17,
            "endIndex": 18,
            "dateElement": {
                "dateId": "kix.meaym1jb5gws",
                "textStyle": {},
                "dateElementProperties": {
                    "timestamp": "2026-02-02T12:00:00Z",
                    "locale": "en",
                    "dateFormat": "DATE_FORMAT_MONTH_DAY_YEAR_ABBREVIATED",
                    "timeFormat": "TIME_FORMAT_DISABLED",
                    "displayText": "Feb 2, 2026",
                },
            },
        }
        self.assertEqual(_get_element_text(element), "Feb 2, 2026")

    def test_date_chip_no_display_text(self) -> None:
        element = {
            "dateElement": {
                "dateElementProperties": {},
            },
        }
        self.assertEqual(_get_element_text(element), "")

    def test_unknown_element_returns_empty(self) -> None:
        element = {"inlineObjectElement": {"inlineObjectId": "obj1"}}
        self.assertEqual(_get_element_text(element), "")

    def test_empty_element_returns_empty(self) -> None:
        self.assertEqual(_get_element_text({}), "")


class TestBuildersResetNamedStyle(unittest.TestCase):
    """Each builder should include namedStyleType in updateParagraphStyle."""

    def _get_style_reqs(self, requests: list[dict]) -> list[dict]:
        return [r for r in requests if "updateParagraphStyle" in r]

    def test_code_block_sets_normal_text(self) -> None:
        content = {"lines": ["print('hello')"]}
        requests: list[dict] = []
        _build_code_block_requests(content, 1, requests)
        style_reqs = self._get_style_reqs(requests)
        self.assertTrue(len(style_reqs) >= 1)
        ps = style_reqs[0]["updateParagraphStyle"]["paragraphStyle"]
        self.assertEqual(ps["namedStyleType"], "NORMAL_TEXT")
        fields = style_reqs[0]["updateParagraphStyle"]["fields"]
        self.assertIn("namedStyleType", fields)

    def test_block_quote_sets_normal_text(self) -> None:
        content = {"lines": ["A quote"]}
        requests: list[dict] = []
        _build_block_quote_requests(content, 1, requests)
        style_reqs = self._get_style_reqs(requests)
        self.assertTrue(len(style_reqs) >= 1)
        ps = style_reqs[0]["updateParagraphStyle"]["paragraphStyle"]
        self.assertEqual(ps["namedStyleType"], "NORMAL_TEXT")

    def test_horizontal_rule_sets_normal_text(self) -> None:
        requests: list[dict] = []
        _build_horizontal_rule_requests(1, requests)
        style_reqs = self._get_style_reqs(requests)
        self.assertEqual(len(style_reqs), 1)
        ps = style_reqs[0]["updateParagraphStyle"]["paragraphStyle"]
        self.assertEqual(ps["namedStyleType"], "NORMAL_TEXT")

    def test_bullet_list_sets_normal_text_on_items(self) -> None:
        content = {"items": [{"text": "Item A", "indent": 0}]}
        requests: list[dict] = []
        _build_bullet_list_requests(content, 1, requests)
        style_reqs = self._get_style_reqs(requests)
        self.assertTrue(len(style_reqs) >= 1)
        ps = style_reqs[0]["updateParagraphStyle"]["paragraphStyle"]
        self.assertEqual(ps["namedStyleType"], "NORMAL_TEXT")

    def test_numbered_list_sets_normal_text_on_items(self) -> None:
        content = {"items": [{"text": "Item 1", "indent": 0}]}
        requests: list[dict] = []
        _build_numbered_list_requests(content, 1, requests)
        style_reqs = self._get_style_reqs(requests)
        self.assertTrue(len(style_reqs) >= 1)
        ps = style_reqs[0]["updateParagraphStyle"]["paragraphStyle"]
        self.assertEqual(ps["namedStyleType"], "NORMAL_TEXT")

    def test_bullet_list_uses_reset_fields(self) -> None:
        content = {"items": [{"text": "Item A", "indent": 0}]}
        requests: list[dict] = []
        _build_bullet_list_requests(content, 1, requests)
        style_reqs = self._get_style_reqs(requests)
        fields = style_reqs[0]["updateParagraphStyle"]["fields"]
        self.assertEqual(fields, _PARAGRAPH_RESET_FIELDS)

    def test_numbered_list_uses_reset_fields(self) -> None:
        content = {"items": [{"text": "Item 1", "indent": 0}]}
        requests: list[dict] = []
        _build_numbered_list_requests(content, 1, requests)
        style_reqs = self._get_style_reqs(requests)
        fields = style_reqs[0]["updateParagraphStyle"]["fields"]
        self.assertEqual(fields, _PARAGRAPH_RESET_FIELDS)


class TestHorizontalRuleClearBullets(unittest.TestCase):
    """Test _build_horizontal_rule_requests clear_bullets support."""

    def test_no_clear_bullets_by_default(self) -> None:
        requests: list[dict] = []
        _build_horizontal_rule_requests(1, requests)
        bullet_reqs = [r for r in requests if "deleteParagraphBullets" in r]
        self.assertEqual(len(bullet_reqs), 0)

    def test_clear_bullets_adds_delete_request(self) -> None:
        requests: list[dict] = []
        _build_horizontal_rule_requests(1, requests, clear_bullets=True)
        bullet_reqs = [r for r in requests if "deleteParagraphBullets" in r]
        self.assertEqual(len(bullet_reqs), 1)
        rng = bullet_reqs[0]["deleteParagraphBullets"]["range"]
        self.assertEqual(rng["startIndex"], 1)
        self.assertEqual(rng["endIndex"], 2)

    def test_uses_reset_field_mask(self) -> None:
        requests: list[dict] = []
        _build_horizontal_rule_requests(1, requests)
        style_reqs = [r for r in requests if "updateParagraphStyle" in r]
        self.assertEqual(len(style_reqs), 1)
        fields = style_reqs[0]["updateParagraphStyle"]["fields"]
        self.assertEqual(fields, _PARAGRAPH_RESET_FIELDS)


if __name__ == "__main__":
    unittest.main()
