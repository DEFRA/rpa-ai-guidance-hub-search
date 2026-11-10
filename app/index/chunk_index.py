"""The interface every chunk index implements: Tantivy now, MongoDB if it replaces it."""

import dataclasses
import uuid
from collections.abc import Sequence
from typing import Protocol

from app.records import ids, models

MAX_LIMIT = 1000


def _check_page(limit: int, offset: int = 0) -> None:
    if not 1 <= limit <= MAX_LIMIT:
        msg = f"limit must be 1 to {MAX_LIMIT}, got {limit}"
        raise ValueError(msg)

    if offset < 0:
        msg = f"offset must not be negative, got {offset}"
        raise ValueError(msg)


@dataclasses.dataclass(frozen=True)
class ChunkQuery:
    """Plain user text, never engine syntax; must not raise on any input.

    A section matches when `word_match.find` matches its headings or body, or
    nearly: within a typo budget per word, ranked below every exact match.
    `statuses` is the caller's role already resolved. `tag` is a document or
    version ID to search inside. A section number such as `4.2.1` should also
    match `section_number`.
    """

    text: str
    statuses: frozenset[models.DocumentStatus]
    tag: uuid.UUID | None = None
    limit: int = 10
    offset: int = 0

    def __post_init__(self) -> None:
        _check_page(self.limit, self.offset)


@dataclasses.dataclass(frozen=True)
class SectionHit:
    """The best chunk of one matching section; `score` only orders one result.

    `exact` is False for a near (typo-tolerant) match. `url` is the document's;
    the section's deep link is `{url}#{anchor}`.
    """

    section_id: ids.SectionId
    chunk_id: ids.ChunkId
    document_id: ids.DocumentId
    version_id: ids.VersionId
    title: str
    status: models.DocumentStatus
    heading_path: tuple[str, ...]
    section_number: str | None
    anchor: str
    url: str
    plain_text: str
    score: float
    exact: bool = True


@dataclasses.dataclass(frozen=True)
class SectionHits:
    """One page of hits; `total` counts every matching section."""

    hits: tuple[SectionHit, ...]
    total: int


@dataclasses.dataclass(frozen=True)
class TitleQuery:
    """Forgives typos, half words and words in another order."""

    text: str
    statuses: frozenset[models.DocumentStatus]
    limit: int = 10

    def __post_init__(self) -> None:
        _check_page(self.limit)


@dataclasses.dataclass(frozen=True)
class TitleHit:
    document_id: ids.DocumentId
    version_id: ids.VersionId
    title: str
    status: models.DocumentStatus
    url: str
    score: float


class ChunkWriter(Protocol):
    async def replace_document(
        self,
        header: models.DocumentHeader,
        chunks: Sequence[models.ChunkRecord],
    ) -> None:
        """Atomically make these the document's only chunks and title.

        Idempotent; [] removes the document.
        """
        ...

    async def remove_document(self, document_id: ids.DocumentId) -> None:
        """No-op if absent."""
        ...


class ChunkRetriever(Protocol):
    async def search(self, query: ChunkQuery) -> SectionHits:
        """Best first, one hit per section, `query.limit` from `query.offset`."""
        ...


class TitleRetriever(Protocol):
    async def search_titles(self, query: TitleQuery) -> list[TitleHit]:
        """Closest first, at most `query.limit`."""
        ...


class ChunkIndex(ChunkWriter, ChunkRetriever, TitleRetriever, Protocol):
    """An index the indexer writes to and search reads from."""
