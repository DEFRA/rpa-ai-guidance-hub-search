"""Queries refuse pages they can't serve."""

import pytest

from app.index import chunk_index
from app.records import models

STATUSES = frozenset(models.DocumentStatus)


class TestAChunkQuery:
    @pytest.mark.parametrize(
        ("limit", "offset"),
        [
            pytest.param(0, 0, id="no-limit"),
            pytest.param(1001, 0, id="over-the-maximum"),
            pytest.param(10, -1, id="negative-offset"),
        ],
    )
    def test_refuses_a_page_it_cannot_serve(self, limit, offset):
        with pytest.raises(ValueError, match="limit|offset"):
            chunk_index.ChunkQuery("x", STATUSES, limit=limit, offset=offset)

    def test_allows_the_maximum_limit(self):
        query = chunk_index.ChunkQuery("x", STATUSES, limit=chunk_index.MAX_LIMIT)

        assert query.limit == 1000


class TestATitleQuery:
    def test_refuses_a_limit_over_the_maximum(self):
        with pytest.raises(ValueError, match="limit"):
            chunk_index.TitleQuery("x", STATUSES, limit=1001)


class TestTheTypoBudget:
    @pytest.mark.parametrize(
        ("word", "budget"),
        [
            pytest.param("bed", 0, id="three-letters"),
            pytest.param("beds", 1, id="four-letters"),
            pytest.param("holding", 1, id="seven-letters"),
            pytest.param("woodland", 2, id="eight-letters"),
        ],
    )
    def test_grows_with_the_word(self, word, budget):
        assert chunk_index.typo_budget(word) == budget
