"""In-memory `ChunkSetStore`."""

from app.records import chunk_sets


class InMemoryChunkSetStore:
    def __init__(self) -> None:
        self.sets: dict[str, chunk_sets.ChunkSet] = {}

    async def put(self, chunk_set: chunk_sets.ChunkSet) -> str:
        self.sets[chunk_set.key] = chunk_set
        return chunk_set.key

    async def get(self, key: str) -> chunk_sets.ChunkSet | None:
        return self.sets.get(key)
