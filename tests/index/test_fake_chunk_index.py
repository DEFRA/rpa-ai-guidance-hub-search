"""The in-memory index meets the same contract every real index must."""

import pytest

from app.index import chunk_index
from tests.fakes import chunk_index as fake_chunk_index
from tests.index import chunk_index_contract


class TestInMemoryChunkIndex(chunk_index_contract.ChunkIndexContract):
    @pytest.fixture
    def chunk_index(self) -> chunk_index.ChunkIndex:
        return fake_chunk_index.InMemoryChunkIndex()
