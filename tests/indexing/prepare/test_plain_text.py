"""Inline Markdown reduced to the text a reader sees."""

import pytest

from app.indexing.prepare import plain_text


class TestStripInline:
    @pytest.mark.parametrize(
        ("markdown", "text"),
        [
            pytest.param("A ![](ab12.png) map", "A map", id="image"),
            pytest.param("[open query]{.red} now", "open query now", id="red-span"),
            pytest.param("[agreed]{.blue}", "agreed", id="blue-span"),
            pytest.param("<u>never</u> crop", "never crop", id="underline"),
            pytest.param("note<sup>1</sup>", "note1", id="superscript"),
            pytest.param("H<sub>2</sub>O", "H2O", id="subscript"),
            pytest.param("Print it<br>twice", "Print it twice", id="break"),
            pytest.param("5 \\* 3 \\[draft\\]", "5 * 3 [draft]", id="escapes"),
            pytest.param("Date &amp; time &lt;b&gt;", "Date & time <b>", id="entities"),
            pytest.param("**bold** *italic* ~~gone~~", "bold italic gone", id="marks"),
            pytest.param("[see 1.1](#11-x)", "see 1.1", id="link"),
            pytest.param("[a](<file:///a b.pdf>)", "a", id="angle-link"),
            pytest.param(
                "[Matrix (2021)](https://e.com/m(2021).xlsx?x=1) now",
                "Matrix (2021) now",
                id="parentheses-in-target",
            ),
            pytest.param("![](a(1).png) map", "map", id="parentheses-in-image"),
            pytest.param(
                "[[**bold red link**]{.red}](https://example.com)",
                "bold red link",
                id="nested",
            ),
            pytest.param("  spaced   out  ", "spaced out", id="whitespace"),
        ],
    )
    def test_strips(self, markdown, text):
        assert plain_text.strip_inline(markdown) == text

    def test_keeps_an_escaped_entity_as_typed(self):
        assert plain_text.strip_inline("&amp;amp;") == "&amp;"

    def test_keeps_an_entity_after_an_escaped_ampersand_as_typed(self):
        assert plain_text.strip_inline("\\&amp; x") == "&amp; x"


class TestLinks:
    def test_finds_each_link_with_its_plain_label(self):
        markdown = "See [**this**](#1-a), ![](x.png) and [that](<https://e.com/a b>)."

        assert plain_text.links(markdown) == [
            ("this", "#1-a"),
            ("that", "https://e.com/a b"),
        ]

    def test_keeps_balanced_parentheses_in_a_target(self):
        assert plain_text.links("[m](https://e.com/m(2021).xlsx)") == [
            ("m", "https://e.com/m(2021).xlsx"),
        ]

    def test_decodes_entities_in_a_target(self):
        assert plain_text.links("[p](https://e.com/?a=1&amp;b=2)") == [
            ("p", "https://e.com/?a=1&b=2"),
        ]

    def test_keeps_an_escaped_ampersand_in_a_target_as_typed(self):
        assert plain_text.links("[p](https://e.com/?q=\\&amp;)") == [
            ("p", "https://e.com/?q=&amp;"),
        ]

    def test_finds_a_link_round_a_colour_span(self):
        assert plain_text.links("[[case]{.red}](#2-b)") == [("case", "#2-b")]

    def test_ignores_escaped_brackets(self):
        assert plain_text.links("\\[not\\](a link)") == []
