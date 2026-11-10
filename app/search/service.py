"""What `GET /search` calls: word search (`q`, `tag`), by section or by document,
and title search (`title`)."""

import dataclasses
import uuid
from typing import Protocol

from app.index import chunk_index
from app.search import routing

MAX_MATCHES = 1000


class TooManyMatchesError(Exception):
    """More than `MAX_MATCHES` sections match; the caller should narrow the query."""

    def __init__(self, query_id: str, total: int) -> None:
        super().__init__(f"{total} sections match; at most {MAX_MATCHES} are served")
        self.query_id = query_id
        self.total = total


@dataclasses.dataclass(frozen=True)
class WordSearch:
    text: str
    role: routing.Role
    tag: uuid.UUID | None = None
    limit: int = 10
    offset: int = 0


@dataclasses.dataclass(frozen=True)
class DocumentSearch:
    """Word search answered by document: `limit` and `offset` count documents.

    Each document gives at most `sections` of its matching sections.
    """

    text: str
    role: routing.Role
    tag: uuid.UUID | None = None
    limit: int = 10
    offset: int = 0
    sections: int = 3


@dataclasses.dataclass(frozen=True)
class TitleSearch:
    text: str
    role: routing.Role
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
    """One document's matching sections, best first.

    `sections` is never empty; its first is the document's best section.
    `section_total` counts every one of its matching sections.
    """

    sections: tuple[SectionResult, ...]
    section_total: int


@dataclasses.dataclass(frozen=True)
class DocumentResults:
    """`total` counts every matching document, `section_total` their sections."""

    query_id: str
    total: int
    section_total: int
    results: tuple[DocumentResult, ...]


@dataclasses.dataclass(frozen=True)
class TitleResults:
    query_id: str
    results: tuple[chunk_index.TitleHit, ...]


class SearchService(Protocol):
    async def search_words(self, request: WordSearch) -> WordResults:
        """Raises `TooManyMatchesError` past `MAX_MATCHES`."""
        ...

    async def search_documents(self, request: DocumentSearch) -> DocumentResults:
        """Documents ordered by their best section.

        Raises `TooManyMatchesError` past `MAX_MATCHES` sections.
        """
        ...

    async def search_titles(self, request: TitleSearch) -> TitleResults: ...
