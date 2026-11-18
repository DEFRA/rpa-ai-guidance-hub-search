"""Whether a word search names a document's title."""

import pytest

from app.matching import title_cover
from tests.support import titles

SERIES_TITLE = titles.SERIES_TITLE
TYPO_TITLE = titles.TYPO_TITLE
MOST_WORDS = f"{titles.SERIES} {titles.CODE} example rule limit"


class TestCoveringATitle:
    @pytest.mark.parametrize(
        "query",
        [
            pytest.param(MOST_WORDS, id="most-words"),
            pytest.param("Alpha Beta Topic", id="three-word-start"),
            pytest.param("alpha beta topic", id="any-case"),
            pytest.param("Alpha-Beta topic", id="hyphens"),
            pytest.param("the Alpha Beta Topic", id="stop-words"),
            pytest.param("Alpha Beta", id="two-word-start"),
            pytest.param(
                f"{titles.SERIES} {titles.CODE} {titles.OTHER_CODE}", id="codes"
            ),
        ],
    )
    def test_covers(self, query):
        assert title_cover.covers(query, SERIES_TITLE)

    @pytest.mark.parametrize(
        "query",
        [
            pytest.param("Sample Reports", id="plural"),
            pytest.param("sample reprot", id="typo"),
            pytest.param("report layout overview", id="most-words-not-the-start"),
            pytest.param("sample reports notes", id="plural-and-more-words"),
        ],
    )
    def test_forgives_plurals_and_typos(self, query):
        assert title_cover.covers(query, TYPO_TITLE)

    @pytest.mark.parametrize(
        "query",
        [
            pytest.param("example rule over another", id="words-not-in-title"),
            pytest.param("limit", id="one-keyword"),
            pytest.param("example rule limit", id="too-little-of-title"),
            pytest.param("Alpha", id="start-too-short"),
            pytest.param(f"{titles.SERIES} {titles.SHORTER_CODE}", id="code-start"),
            pytest.param(f"{titles.SERIES} {titles.NEAR_CODE}", id="code-typo"),
            pytest.param("", id="empty"),
            pytest.param("the of and", id="only-stop-words"),
            pytest.param("Limi", id="part-of-a-word"),
        ],
    )
    def test_does_not_cover(self, query):
        assert title_cover.covers(query, SERIES_TITLE) == ()

    def test_never_forgives_a_code_in_the_title(self):
        title = f"{titles.SERIES} - ABCD1"

        assert title_cover.covers(f"{titles.SERIES} ABCD", title) == ()

    def test_covers_a_short_title_with_most_of_its_words(self):
        assert title_cover.covers("hedge cutting", "Hedge cutting rules")

    def test_covers_a_title_one_keyword_names_whole(self):
        assert title_cover.covers("hedges", "Hedge")

    def test_does_not_cover_with_one_keyword_of_a_longer_title(self):
        assert title_cover.covers("guidance", "Claims Guidance") == ()

    def test_marks_the_title_words_a_near_word_matches(self):
        spans = title_cover.covers("Sample Reports", TYPO_TITLE)

        assert [TYPO_TITLE[start:end] for start, end in spans] == [
            "Sample Report",
        ]

    def test_marks_runs_of_query_words(self):
        spans = title_cover.covers(MOST_WORDS, SERIES_TITLE)

        assert [SERIES_TITLE[start:end] for start, end in spans] == [
            titles.SERIES,
            titles.CODE,
            "Example Rule Limit",
        ]


class TestCoverage:
    def test_is_the_share_of_title_words_in_the_query(self):
        assert title_cover.coverage(MOST_WORDS, SERIES_TITLE) == pytest.approx(7 / 8)

    def test_is_zero_when_not_covered(self):
        assert title_cover.coverage("limit", SERIES_TITLE) == 0

    def test_counts_a_start_below_the_share(self):
        assert title_cover.coverage("Alpha Beta", SERIES_TITLE) == pytest.approx(2 / 8)

    def test_counts_near_words(self):
        assert title_cover.coverage("Sample Reports", TYPO_TITLE) == pytest.approx(
            2 / 5
        )
