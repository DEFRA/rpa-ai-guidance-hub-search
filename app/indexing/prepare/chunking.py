"""Chunking strategies: canonical sections in, chunks for one index out.

Each index picks a strategy. The keyword index packs blocks up to a size; a
retrieval index may want more context per chunk. Strategies are named, and the
name is part of every chunk id, so two strategies' chunks never collide.
Rename a strategy (e.g. `packed-800` to `packed-800-v2`) whenever its output
changes.
"""

from collections.abc import Sequence
from typing import Protocol

from app.records import models


class ChunkingStrategy(Protocol):
    @property
    def name(self) -> str: ...

    def chunk(
        self,
        sections: Sequence[models.SectionRecord],
    ) -> list[models.ChunkRecord]:
        """Sync and CPU-bound. A chunk never crosses a section."""
        ...
