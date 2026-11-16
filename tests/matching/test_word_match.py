"""Where a query matches in a section's text."""

import pytest

from app.matching import word_match


class TestFindingAQuery:
    @pytest.mark.parametrize(
        ("query", "text", "spans"),
        [
            pytest.param("pay", "Repayment and pay.", ((2, 5), (14, 17)), id="every"),
            pytest.param("a  holding", "Hold a holding.", ((5, 14),), id="spacing"),
            pytest.param("a b", "a\nb", (), id="never-across-blocks"),
            pytest.param("of", "Proof of it.", ((6, 8),), id="short-whole-word"),
            pytest.param("", "Anything.", (), id="empty"),
            pytest.param("(rows*", "Hedge (rows*.", ((6, 12),), id="literal"),
        ],
    )
    def test_gives_each_match_start_and_end(self, query, text, spans):
        assert word_match.find(query, text) == spans


class TestTheTypoBudget:
    @pytest.mark.parametrize(
        ("word", "budget"),
        [("pay", 0), ("land", 1), ("guidance", 2), ("hedgerows", 2)],
    )
    def test_grows_with_the_word(self, word, budget):
        assert word_match.typo_budget(word) == budget


class TestFindingANearMatch:
    @pytest.mark.parametrize(
        ("query", "text", "spans"),
        [
            pytest.param("livestok", "for livestock.", ((4, 13),), id="missing-letter"),
            pytest.param("recieve", "Receive it", ((0, 7),), id="swapped-letters"),
            pytest.param("clam for", "a claim  for it", ((2, 12),), id="phrase"),
            pytest.param("clam for", "a claim\nfor it", (), id="never-across-blocks"),
            pytest.param("on", "Planting an area.", (), id="short-words-exact"),
            pytest.param("clam", "claimant", (), id="whole-words-only"),
            pytest.param(
                "hedgerws", "hedgerow, hedgerow", ((0, 8), (10, 18)), id="every"
            ),
            pytest.param("?!", "Anything.", (), id="no-words"),
        ],
    )
    def test_gives_each_match_start_and_end(self, query, text, spans):
        assert word_match.find_near(query, text) == spans

    def test_reads_the_query_words_without_punctuation(self):
        assert word_match.near_words("Can I claim?") == ["can", "i", "claim"]
