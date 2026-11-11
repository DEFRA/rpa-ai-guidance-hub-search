"""Anchors match the API's, so `#anchor` links in `content.md` resolve.

The cases are the API's own anchor test cases, kept identical on purpose.
"""

import pytest

from app.indexing.prepare import anchors


class TestSlugify:
    @pytest.mark.parametrize(
        ("heading", "anchor"),
        [
            pytest.param("Evidence Required", "evidence-required", id="lower-case"),
            pytest.param("3.1 Overview", "31-overview", id="number-dot-dropped"),
            pytest.param(
                "Annex A - Case types",
                "annex-a---case-types",
                id="hyphens-stay",
            ),
            pytest.param("What, exactly?", "what-exactly", id="punctuation-goes"),
            pytest.param("3 Overview", "3-overview", id="numbered"),
        ],
    )
    def test_gives_the_api_anchor(self, heading, anchor):
        assert anchors.slugify(heading) == anchor


class TestAnchorsOfHeadings:
    def test_numbered_headings_never_collide(self):
        assert anchors.of_headings(["1 Overview", "2 Overview"]) == [
            "1-overview",
            "2-overview",
        ]

    def test_duplicate_headings_are_suffixed_in_order(self):
        assert anchors.of_headings(["Case types", "Case types", "Case types"]) == [
            "case-types",
            "case-types-1",
            "case-types-2",
        ]
