"""`GET /v1/search` response bodies, camelCase on the wire.

The fields start from the milestone 1 spike's keyword result: `documentId`,
`documentTitle`, `sectionNumber`, `heading`, `matches` and `snippet`. These are
added: `id` (the section ID), `versionId`, `anchor`, `url`, `status`,
`exact`, `total`, `queryId` and the "Too many matches" answer.

Within v1, changes must be additive. A breaking change is a new `v2` module
with its own router, converting from the same version-neutral `service` models,
so callers still on v1 keep working.
"""

from __future__ import annotations

import uuid

import pydantic
from pydantic import alias_generators

from app.index import chunk_index
from app.records import models
from app.search import service

TOO_MANY_MATCHES = "Too many matches. Be more specific."


class _Schema(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(
        alias_generator=alias_generators.to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
    )


class SnippetPart(_Schema):
    text: str
    match: bool


class SectionResult(_Schema):
    """One section holding the words, at its best-matching chunk."""

    id: str = pydantic.Field(description="The section ID")
    document_id: uuid.UUID
    document_title: str
    version_id: uuid.UUID
    status: models.DocumentStatus
    section_number: str | None
    heading: str = pydantic.Field(
        description="The section's heading, or the document title before any section",
    )
    heading_path: list[str]
    anchor: str = pydantic.Field(description="The section's fragment in the document")
    url: str = pydantic.Field(
        description="Deep link to the section: the document's URL and its anchor",
    )
    exact: bool = pydantic.Field(description="False for a near (typo-tolerant) match")
    matches: int
    snippet: list[SnippetPart]

    @classmethod
    def from_model(cls, result: service.SectionResult) -> SectionResult:
        hit = result.hit

        return cls(
            id=hit.section_id,
            document_id=hit.document_id,
            document_title=hit.title,
            version_id=hit.version_id,
            status=hit.status,
            section_number=hit.section_number,
            heading=hit.heading_path[-1] if hit.heading_path else hit.title,
            heading_path=list(hit.heading_path),
            anchor=hit.anchor,
            url=f"{hit.url}#{hit.anchor}" if hit.anchor else hit.url,
            exact=hit.exact,
            matches=hit.matches,
            snippet=snippet_of(result.excerpt),
        )


class WordSearchResponse(_Schema):
    query_id: str
    total: int = pydantic.Field(
        description="Every matching section, not just this page"
    )
    results: list[SectionResult]
    too_many_matches: bool = False
    message: str | None = None

    @classmethod
    def from_model(cls, results: service.WordResults) -> WordSearchResponse:
        return cls(
            query_id=results.query_id,
            total=results.total,
            results=[SectionResult.from_model(result) for result in results.results],
        )

    @classmethod
    def too_many(cls, error: service.TooManyMatchesError) -> WordSearchResponse:
        return cls(
            query_id=error.query_id,
            total=error.total,
            results=[],
            too_many_matches=True,
            message=TOO_MANY_MATCHES,
        )


class DocumentResult(_Schema):
    """One document holding the words, with its best sections."""

    document_id: uuid.UUID
    document_title: str
    version_id: uuid.UUID
    status: models.DocumentStatus
    url: str = pydantic.Field(description="Where a reader views the document")
    section_total: int = pydantic.Field(
        description="Every matching section in the document, not just those given",
    )
    sections: list[SectionResult] = pydantic.Field(
        description="At most `sections` of them, best first",
    )

    @classmethod
    def from_model(cls, result: service.DocumentResult) -> DocumentResult:
        best = result.sections[0].hit

        return cls(
            document_id=best.document_id,
            document_title=best.title,
            version_id=best.version_id,
            status=best.status,
            url=best.url,
            section_total=result.section_total,
            sections=[SectionResult.from_model(section) for section in result.sections],
        )


class DocumentSearchResponse(_Schema):
    """Word search with `group=document`: a page of documents, best first."""

    query_id: str
    total: int = pydantic.Field(
        description="Every matching document, not just this page; 0 when there "
        "are too many matches",
    )
    section_total: int = pydantic.Field(
        description="Every matching section, across every document",
    )
    results: list[DocumentResult]
    too_many_matches: bool = False
    message: str | None = None

    @classmethod
    def from_model(cls, results: service.DocumentResults) -> DocumentSearchResponse:
        return cls(
            query_id=results.query_id,
            total=results.total,
            section_total=results.section_total,
            results=[DocumentResult.from_model(result) for result in results.results],
        )

    @classmethod
    def too_many(cls, error: service.TooManyMatchesError) -> DocumentSearchResponse:
        return cls(
            query_id=error.query_id,
            total=0,
            section_total=error.total,
            results=[],
            too_many_matches=True,
            message=TOO_MANY_MATCHES,
        )


class TitleResult(_Schema):
    document_id: uuid.UUID
    document_title: str
    version_id: uuid.UUID
    status: models.DocumentStatus
    url: str = pydantic.Field(description="Where a reader views the document")
    score: float = pydantic.Field(description="0 to 1; closer titles score higher")

    @classmethod
    def from_model(cls, hit: chunk_index.TitleHit) -> TitleResult:
        return cls(
            document_id=hit.document_id,
            document_title=hit.title,
            version_id=hit.version_id,
            status=hit.status,
            url=hit.url,
            score=hit.score,
        )


class TitleSearchResponse(_Schema):
    query_id: str
    results: list[TitleResult]

    @classmethod
    def from_model(cls, results: service.TitleResults) -> TitleSearchResponse:
        return cls(
            query_id=results.query_id,
            results=[TitleResult.from_model(hit) for hit in results.results],
        )


def snippet_of(excerpt: service.Excerpt) -> list[SnippetPart]:
    """The excerpt in parts, its matches marked, empty parts left out."""
    parts: list[SnippetPart] = []
    start = 0

    for match_start, match_end in excerpt.matches:
        parts.append(SnippetPart(text=excerpt.text[start:match_start], match=False))
        parts.append(SnippetPart(text=excerpt.text[match_start:match_end], match=True))
        start = match_end

    parts.append(SnippetPart(text=excerpt.text[start:], match=False))
    return [part for part in parts if part.text]
