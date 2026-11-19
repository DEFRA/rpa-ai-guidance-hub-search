"""What `GET /search` calls: word search (`q`, `tag`), by section or by document,
and title search (`title`)."""

import dataclasses
import uuid
from typing import Protocol

from app.index import chunk_index
from app.records import ids, models

MAX_MATCHES = 1000
MAX_TITLE_COVERS = 50


class TooManyMatchesError(Exception):
    """More than `MAX_MATCHES` sections match; the caller should narrow the query."""

    def __init__(self, query_id: str, total: int) -> None:
        super().__init__(f"{total} sections match; at most {MAX_MATCHES} are served")
        self.query_id = query_id
        self.total = total


@dataclasses.dataclass(frozen=True)
class WordSearch:
    text: str
    statuses: frozenset[models.DocumentStatus]
    tag: uuid.UUID | None = None
    limit: int = 10
    offset: int = 0


@dataclasses.dataclass(frozen=True)
class DocumentSearch:
    """Word search answered by document: `limit` and `offset` count documents.

    Each gives at most `sections` of its matching sections. The titles the
    words cover are found too (`title_cover`).
    """

    text: str
    statuses: frozenset[models.DocumentStatus]
    tag: uuid.UUID | None = None
    limit: int = 10
    offset: int = 0
    sections: int = 3


@dataclasses.dataclass(frozen=True)
class TitleSearch:
    text: str
    statuses: frozenset[models.DocumentStatus]
    limit: int = 10


@dataclasses.dataclass(frozen=True)
class Excerpt:
    """The first sentence holding a match; `matches` are offsets into `text`."""

    text: str
    matches: tuple[tuple[int, int], ...]


@dataclasses.dataclass(frozen=True)
class SectionResult:
    hit: chunk_index.SectionHit
    excerpt: Excerpt


@dataclasses.dataclass(frozen=True)
class WordResults:
    query_id: str
    total: int
    results: tuple[SectionResult, ...]


@dataclasses.dataclass(frozen=True)
class DocumentResult:
    """One document holding the words in its sections, or whose title they
    cover, or both.

    `sections` are its matching sections, best first; empty when only its
    title is covered. `section_total` counts every one of its matching
    sections. `title_match` is set when the words cover its title.
    """

    document_id: ids.DocumentId
    version_id: ids.VersionId
    title: str
    status: models.DocumentStatus
    url: str
    sections: tuple[SectionResult, ...]
    section_total: int
    title_match: chunk_index.TitleCoverHit | None = None


@dataclasses.dataclass(frozen=True)
class DocumentResults:
    """A page of the documents whose sections hold the words, and every title
    they cover.

    `total` counts the documents paged through, `section_total` every matching
    section. `titles` are the documents whose title the words cover, most
    covered first, at most `MAX_TITLE_COVERS`; they may also be in `results`.
    `document_total` counts every document in either, past that cap too.
    """

    query_id: str
    total: int
    section_total: int
    results: tuple[DocumentResult, ...]
    titles: tuple[DocumentResult, ...] = ()
    document_total: int = 0


@dataclasses.dataclass(frozen=True)
class TitleResults:
    query_id: str
    results: tuple[chunk_index.TitleHit, ...]


@dataclasses.dataclass(frozen=True)
class Match:
    """One place in a section's text (`end` exclusive) and the words there."""

    start: int
    end: int
    text: str


@dataclasses.dataclass(frozen=True)
class SectionMatches:
    """`anchor` is None for the text before the document's first heading."""

    anchor: str | None
    matches: tuple[Match, ...]


@dataclasses.dataclass(frozen=True)
class DocumentMatches:
    """Every section of the indexed version holding the words, in order."""

    version_id: ids.VersionId
    sections: tuple[SectionMatches, ...]


class SearchService(Protocol):
    async def search_words(self, request: WordSearch) -> WordResults:
        """Raises `TooManyMatchesError` past `MAX_MATCHES`."""
        ...

    async def search_documents(self, request: DocumentSearch) -> DocumentResults:
        """Documents by their best section, and the titles the words cover.

        Raises `TooManyMatchesError` past `MAX_MATCHES` sections.
        """
        ...

    async def search_titles(self, request: TitleSearch) -> TitleResults: ...
