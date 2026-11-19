"""Where words are in one document: every exact match, by its place in the text
of the section that holds it, so that a page can mark each one.

A section's text is read as the hub reads it (the API's `guidance/reading.py`,
which the UI's `search-places.js` mirrors): the heading with its number, then
each line of each block on a line of its own, a table giving a line per cell.
Whitespace within a line is one space. Offsets are into that text, so it must
stay in step with the hub's reading, or the page cannot place the matches.

Only exact matches (`word_match.find`): a page marks words that are there.
"""

import logging

from app.index import chunk_index
from app.matching import word_match
from app.records import ids, models, section_store, tables
from app.search import service

logger = logging.getLogger(__name__)


class DocumentMatchFinder:
    def __init__(
        self,
        index: chunk_index.ChunkIndex,
        sections: section_store.SectionStore,
    ) -> None:
        self._index = index
        self._sections = sections

    async def matches_in(
        self,
        document_id: ids.DocumentId,
        text: str,
        statuses: frozenset[models.DocumentStatus],
    ) -> service.DocumentMatches | None:
        """The indexed version's matches; `None` when nothing is indexed for it,
        or its status is not one of `statuses`.

        The status is the index's, as search filters on it: the stored sections
        are keyed by version alone, so theirs may be ahead of the index.
        """
        version_id = await self._index.indexed_version(document_id, statuses)

        if version_id is None:
            return None

        stored = await self._sections.get(document_id, version_id)

        if stored is None:
            logger.warning(
                "Document %s version %s is indexed but has no stored sections",
                document_id,
                version_id,
            )
            return None

        _, records = stored
        found = (
            _section_matches(text, record)
            for record in sorted(records, key=lambda record: record.ordinal)
        )

        return service.DocumentMatches(
            version_id=version_id,
            sections=tuple(section for section in found if section.matches),
        )


def reader_text(section: models.SectionRecord) -> str:
    """The section's text as the hub reads it: heading, then a line per line."""
    lines = [line for block in section.blocks for line in _lines(block)]
    heading = _heading(section)

    if heading is not None:
        lines.insert(0, heading)

    return "\n".join(" ".join(line.split()) for line in lines)


def _section_matches(
    text: str,
    section: models.SectionRecord,
) -> service.SectionMatches:
    read = reader_text(section)

    return service.SectionMatches(
        anchor=section.anchor or None,
        matches=tuple(
            service.Match(start=start, end=end, text=read[start:end])
            for start, end in word_match.find(text, read)
        ),
    )


def _heading(section: models.SectionRecord) -> str | None:
    """As rendered: `"4.2 Evidence"`, the number split off when indexed put back."""
    if not section.heading_path:
        return None

    if section.section_number is None:
        return section.heading_path[-1]

    return f"{section.section_number} {section.heading_path[-1]}"


def _lines(block: models.Block) -> tuple[str, ...]:
    """A table's cells are read from its Markdown, where an escaped pipe is
    still told apart from the one between cells."""
    if block.kind is not models.BlockKind.TABLE:
        return block.lines

    return tuple(cell for row in tables.rows(block.markdown) for cell in row)
