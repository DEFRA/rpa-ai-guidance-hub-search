"""The in-memory index meets the same contract every real index must."""

import pytest

from app.index import chunk_index as chunk_index_module
from tests.fakes import chunk_index as fake_chunk_index
from tests.support import chunk_index_contract


@pytest.fixture
def chunk_index() -> chunk_index_module.ChunkIndex:
    return fake_chunk_index.InMemoryChunkIndex()


class TestInMemoryChunkIndex(chunk_index_contract.ChunkIndexContract):
    pass
