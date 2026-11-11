"""Section bodies split into whole blocks, one plain-text line per block line."""

import chonkie
import pytest

from app.indexing.prepare import blocks
from app.records import models

Kind = models.BlockKind


@pytest.fixture(scope="module")
def chef():
    return chonkie.MarkdownChef()


def _kinds(body, chef):
    return [(block.kind, block.lines) for block in blocks.parse(body, chef)]


class TestParse:
    def test_blank_lines_separate_paragraphs(self, chef):
        assert _kinds("One\nstill one.\n\nTwo.", chef) == [
            (Kind.PARAGRAPH, ("One still one.",)),
            (Kind.PARAGRAPH, ("Two.",)),
        ]

    def test_a_hard_break_starts_a_new_line(self, chef):
        assert _kinds("Line one\\\nline two", chef) == [
            (Kind.PARAGRAPH, ("Line one", "line two")),
        ]

    def test_a_list_is_one_block_with_a_line_per_item(self, chef):
        body = "- first\n  wrapped\n- second\n\n  continued\n\n1. numbered"

        assert _kinds(body, chef) == [
            (Kind.LIST, ("first wrapped", "second", "continued")),
            (Kind.LIST, ("numbered",)),
        ]

    def test_a_blank_line_between_items_keeps_one_list(self, chef):
        assert _kinds("- first\n\n- second\n\n1. numbered\n\n2. next", chef) == [
            (Kind.LIST, ("first", "second")),
            (Kind.LIST, ("numbered", "next")),
        ]

    def test_an_escaped_backslash_is_not_a_hard_break(self, chef):
        assert _kinds("Line one\\\\\nline two", chef) == [
            (Kind.PARAGRAPH, ("Line one\\ line two",)),
        ]

    def test_an_unindented_quote_ends_a_list(self, chef):
        assert _kinds("- item\n> quote", chef) == [
            (Kind.LIST, ("item",)),
            (Kind.BLOCKQUOTE, ("quote",)),
        ]

    def test_a_blockquote_is_one_block_with_a_line_per_paragraph(self, chef):
        body = "> Dear [name]\n>\n> We will visit.\n>\n> - gates\n> - stock"

        assert _kinds(body, chef) == [
            (Kind.BLOCKQUOTE, ("Dear [name]", "We will visit.", "gates", "stock")),
        ]

    def test_a_table_is_its_own_block_with_a_line_per_row(self, chef):
        body = (
            "Before.\n"
            "| Item | Notes |\n"
            "| ---- | ----- |\n"
            "| Map  | Print<br>it |\n"
            "| A \\| B | 5 &amp; 6 |\n"
            "After."
        )

        assert _kinds(body, chef) == [
            (Kind.PARAGRAPH, ("Before.",)),
            (Kind.TABLE, ("Item | Notes", "Map | Print it", "A | B | 5 & 6")),
            (Kind.PARAGRAPH, ("After.",)),
        ]

    def test_a_pipe_in_prose_is_not_a_table(self, chef):
        assert _kinds("Cost | per hectare\n|---|---|\n| 1 | 2 |", chef) == [
            (Kind.PARAGRAPH, ("Cost | per hectare |---|---| | 1 | 2 |",)),
        ]

    def test_keeps_each_blocks_raw_markdown(self, chef):
        (block,) = blocks.parse("- **one**\n- two\n", chef)

        assert block.markdown == "- **one**\n- two"

    def test_an_image_only_paragraph_has_no_text(self, chef):
        (block,) = blocks.parse("![](ab.png)", chef)

        assert (block.markdown, block.lines) == ("![](ab.png)", ())

    def test_an_empty_body_has_no_blocks(self, chef):
        assert blocks.parse("\n\n", chef) == []


class TestTokenCount:
    def test_counts_whitespace_separated_words(self):
        assert models.token_count("one two\nthree") == 3
