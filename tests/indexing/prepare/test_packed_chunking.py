"""Packing a section's blocks into chunks for the keyword index."""

import dataclasses

from app.indexing.prepare import packed_chunking
from app.records import ids, models
from tests.support import chunks as fixtures


def _sections(*parts):
    return fixtures.sections([fixtures.Section("1 Overview", 1, parts)])[1]


class TestPackedChunking:
    def test_is_named_by_its_size(self):
        assert packed_chunking.PackedChunking(300).name == "packed-300"

    def test_names_every_chunk_by_the_strategy(self):
        sections = _sections("One.", "Two.")

        chunks = packed_chunking.PackedChunking(300).chunk(sections)

        assert {chunk.strategy for chunk in chunks} == {"packed-300"}
        assert chunks[0].chunk_id == ids.chunk_id(
            "packed-300",
            sections[0].section_id,
            0,
        )

    def test_packs_blocks_up_to_the_size(self):
        sections = _sections(*(" ".join(["word"] * 2) for _ in range(5)))

        chunks = packed_chunking.PackedChunking(4).chunk(sections)

        assert [chunk.token_count for chunk in chunks] == [4, 4, 2]
        assert [chunk.part for chunk in chunks] == [0, 1, 2]

    def test_gives_an_empty_section_one_empty_chunk(self):
        sections = _sections()

        (chunk,) = packed_chunking.PackedChunking().chunk(sections)

        assert (chunk.text, chunk.token_count) == ("", 0)

    def test_keeps_a_table_alone(self):
        table = models.Block(models.BlockKind.TABLE, "| A |", ("A",))
        (section,) = _sections("Hi.")
        section = dataclasses.replace(
            section,
            blocks=(*section.blocks, table, fixtures.paragraph("Bye.")),
        )

        chunks = packed_chunking.PackedChunking().chunk([section])

        assert [chunk.plain_text for chunk in chunks] == ["Hi.", "A", "Bye."]

    def test_keeps_a_leading_table_alone(self):
        table = models.Block(models.BlockKind.TABLE, "| A |", ("A",))
        (section,) = _sections("Bye.")
        section = dataclasses.replace(section, blocks=(table, *section.blocks))

        chunks = packed_chunking.PackedChunking().chunk([section])

        assert [chunk.plain_text for chunk in chunks] == ["A", "Bye."]
