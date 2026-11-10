"""What `GET /search` calls: word search (`q`, `tag`) and title search (`title`)."""

import dataclasses
import uuid
from typing import Protocol

from app.index import chunk_index
from app.search import routing

MAX_MATCHES = 1000


class TooManyMatchesError(Exception):
    """More than `MAX_MATCHES` sections match; the caller should narrow the query."""


@dataclasses.dataclass(frozen=True)
class WordSearch:
    text: str
    role: routing.Role
    tag: uuid.UUID | None = None
    limit: int = 10
    offset: int = 0


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
class TitleResults:
    query_id: str
    results: tuple[chunk_index.TitleHit, ...]


class SearchService(Protocol):
    async def search_words(self, request: WordSearch) -> WordResults:
        """Raises `TooManyMatchesError` past `MAX_MATCHES`."""
        ...

    async def search_titles(self, request: TitleSearch) -> TitleResults: ...
