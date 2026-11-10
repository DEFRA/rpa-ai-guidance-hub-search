"""A document as any source hands it over, before it becomes canonical sections.

Each source (a guide's Markdown today; GOV.UK pages later) has a reader that
fetches one version and turns it into a `SourceDocument`. Everything after that
(section ids, heading paths, parents, references, chunking) is shared.
"""

import dataclasses
from collections.abc import Sequence
from typing import Protocol

from app.indexing import jobs
from app.records import ids, models


@dataclasses.dataclass(frozen=True)
class SourceSection:
    """One heading and its blocks, in document order.

    `heading` is the heading's plain text without its number, or `None` for
    text before the first heading. `level` is 1 for the top section level, 0
    for that leading text. `anchor` is the source's own fragment for the
    heading. `section_id` is the source's id for the section, when it has one;
    otherwise one is hashed from the heading path.
    """

    heading: str | None
    section_number: str | None
    level: int
    anchor: str
    blocks: tuple[models.Block, ...]
    section_id: ids.SectionId | None = None


@dataclasses.dataclass(frozen=True)
class SourceDocument:
    header: models.DocumentHeader
    sections: Sequence[SourceSection]


class SourceReader(Protocol):
    async def read(self, job: jobs.IndexJob) -> SourceDocument:
        """Raises if the content can't be read or isn't well formed."""
        ...
