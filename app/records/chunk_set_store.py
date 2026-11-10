"""Where prepared chunk sets wait for the apply step.

Preparing a version puts its chunk set here; applying gets it back by the key
the prepare step handed over. Keys are deterministic, so preparing the same
version at the same status again overwrites the same object.
"""

from typing import Protocol

from app.records import chunk_sets


class ChunkSetStore(Protocol):
    async def put(self, chunk_set: chunk_sets.ChunkSet) -> str:
        """Stores the set and returns its key."""
        ...

    async def get(self, key: str) -> chunk_sets.ChunkSet | None:
        """`None` when nothing was staged under `key`."""
        ...
