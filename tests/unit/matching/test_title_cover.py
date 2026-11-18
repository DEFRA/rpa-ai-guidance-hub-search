"""Whether a word search names a document's title."""

import pytest

from app.matching import title_cover

MAX_WEIGHT = "CS MA Claim - AB12 or OP3 Maximum Eligible Weight"
FINAL_PAYMENT = "Final Payment Case Check Guide"


class TestCoveringATitle:
    @pytest.mark.parametrize(
        "query",
        [
            pytest.param("CS MA Claim AB12 maximum eligible weight", id="most-words"),
            pytest.param("CS MA Claim", id="three-word-start"),
            pytest.param("cs ma claim", id="any-case"),
            pytest.param("CS-MA claim", id="hyphens"),
            pytest.param("the CS MA Claim", id="stop-words"),
            pytest.param("CS MA", id="two-word-start"),
            pytest.param("CS MA Claim AB12 OP3", id="codes"),
        ],
    )
    def test_covers(self, query):
        assert title_cover.covers(query, MAX_WEIGHT)

    @pytest.mark.parametrize(
        "query",
        [
            pytest.param("Final Payments", id="plural"),
            pytest.param("final paymnet", id="typo"),
            pytest.param("payment case check", id="most-words-not-the-start"),
            pytest.param("final payments guide", id="plural-and-more-words"),
        ],
    )
    def test_forgives_plurals_and_typos(self, query):
        assert title_cover.covers(query, FINAL_PAYMENT)

    @pytest.mark.parametrize(
        "query",
        [
            pytest.param("eligible weight over declared", id="words-not-in-title"),
            pytest.param("weight", id="one-keyword"),
            pytest.param("maximum eligible weight", id="too-little-of-title"),
            pytest.param("CS", id="start-too-short"),
            pytest.param("CS MA Claim AB1", id="code-start"),
            pytest.param("CS MA Claim AB13", id="code-typo"),
            pytest.param("", id="empty"),
            pytest.param("the of and", id="only-stop-words"),
            pytest.param("Weigh", id="part-of-a-word"),
        ],
    )
    def test_does_not_cover(self, query):
        assert title_cover.covers(query, MAX_WEIGHT) == ()

    def test_covers_a_short_title_with_most_of_its_words(self):
        assert title_cover.covers("hedge cutting", "Hedge cutting rules")

    def test_covers_a_title_one_keyword_names_whole(self):
        assert title_cover.covers("hedges", "Hedge")

    def test_does_not_cover_with_one_keyword_of_a_longer_title(self):
        assert title_cover.covers("guidance", "Claims Guidance") == ()

    def test_marks_the_title_words_a_near_word_matches(self):
        spans = title_cover.covers("Final Payments", FINAL_PAYMENT)

        assert [FINAL_PAYMENT[start:end] for start, end in spans] == [
            "Final Payment",
        ]

    def test_marks_runs_of_query_words(self):
        spans = title_cover.covers(
            "CS MA Claim AB12 maximum eligible weight", MAX_WEIGHT
        )

        assert [MAX_WEIGHT[start:end] for start, end in spans] == [
            "CS MA Claim",
            "AB12",
            "Maximum Eligible Weight",
        ]


class TestCoverage:
    def test_is_the_share_of_title_words_in_the_query(self):
        assert title_cover.coverage(
            "CS MA Claim AB12 maximum eligible weight",
            MAX_WEIGHT,
        ) == pytest.approx(7 / 8)

    def test_is_zero_when_not_covered(self):
        assert title_cover.coverage("weight", MAX_WEIGHT) == 0

    def test_counts_a_start_below_the_share(self):
        assert title_cover.coverage("CS MA", MAX_WEIGHT) == pytest.approx(2 / 8)

    def test_counts_near_words(self):
        assert title_cover.coverage("Final Payments", FINAL_PAYMENT) == pytest.approx(
            2 / 5
        )
