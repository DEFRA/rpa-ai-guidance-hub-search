"""The keyword index's chunking: a section's blocks packed up to a size.

Within a section, blocks are packed greedily up to `max_tokens`, which aims at
roughly 300 to 800 tokens per chunk:

- A table is always a chunk of its own.
- A block bigger than `max_tokens` is never split, so it gets a chunk to itself.
- A section with no blocks still gets one empty chunk, so its heading can be found.

Tokens are whitespace-separated words (`models.token_count`).
"""

from collections.abc import Sequence

from app.records import models

MAX_TOKENS = 800


class PackedChunking:
    """Implements `chunking.ChunkingStrategy`."""

    def __init__(self, max_tokens: int = MAX_TOKENS) -> None:
        self._max_tokens = max_tokens

    @property
    def name(self) -> str:
        return f"packed-{self._max_tokens}"

    def chunk(
        self,
        sections: Sequence[models.SectionRecord],
    ) -> list[models.ChunkRecord]:
        chunks: list[models.ChunkRecord] = []

        for section in sections:
            for part, blocks in enumerate(self._pack(section.blocks)):
                chunks.append(
                    models.ChunkRecord.of_section(
                        section,
                        strategy=self.name,
                        part=part,
                        ordinal=len(chunks),
                        blocks=blocks,
                    ),
                )

        return chunks

    def _pack(
        self,
        section_blocks: Sequence[models.Block],
    ) -> list[tuple[models.Block, ...]]:
        parts: list[tuple[models.Block, ...]] = []
        current: list[models.Block] = []
        size = 0

        for block in section_blocks:
            if block.kind is models.BlockKind.TABLE:
                if current:
                    parts.append(tuple(current))

                parts.append((block,))
                current, size = [], 0
                continue

            if current and size + block.token_count > self._max_tokens:
                parts.append(tuple(current))
                current, size = [], 0

            current.append(block)
            size += block.token_count

        if current or not parts:
            parts.append(tuple(current))

        return parts
