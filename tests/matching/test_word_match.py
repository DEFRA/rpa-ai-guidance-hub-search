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
