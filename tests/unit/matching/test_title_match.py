"""How close a title search is to a title."""

import pytest

from app.matching import title_match

TITLE = "Claims Guidance"


class TestScoringATitle:
    @pytest.mark.parametrize(
        "query",
        [
            pytest.param("Claims Guidance", id="exact"),
            pytest.param("claims guidance", id="any-case"),
            pytest.param("Clams Guidanse", id="typos"),
            pytest.param("Guidance Claims", id="word-order"),
            pytest.param("Clai Gui", id="half-words"),
            pytest.param("Gudia", id="typo-in-a-half-word"),
        ],
    )
    def test_forgives(self, query):
        assert title_match.is_close(query, TITLE)

    def test_scores_whole_words_above_starts_above_typos(self):
        assert title_match.score("Claims Guidance", TITLE) == 1.0
        assert title_match.score("Claims Guid", TITLE) == pytest.approx(0.875)
        assert title_match.score("Claims Gudiance", TITLE) == pytest.approx(0.75)

    def test_finds_a_few_words_of_a_long_title(self):
        assert title_match.is_close(
            "land use compatability",
            "Investigate Land Use and Land Cover Compatibility Cases at Sign-off",
        )

    @pytest.mark.parametrize(
        "query",
        [
            pytest.param("zzzz qqqq", id="nonsense"),
            pytest.param("Claims Hedges", id="one-word-unmatched"),
            pytest.param("ms", id="short-inside-a-word"),
            pytest.param("aims", id="end-of-a-word"),
        ],
    )
    def test_rejects(self, query):
        assert not title_match.is_close(query, TITLE)
