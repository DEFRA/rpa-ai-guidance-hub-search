"""`SearchService` for keyword search, over any `ChunkIndex`."""

import itertools
import logging
import uuid

from app.index import chunk_index
from app.records import ids
from app.search import excerpts, service

logger = logging.getLogger(__name__)


class KeywordSearch:
    def __init__(self, index: chunk_index.ChunkIndex) -> None:
        self._index = index

    async def search_words(self, request: service.WordSearch) -> service.WordResults:
        query_id = _query_id()
        hits = await self._index.search(
            chunk_index.ChunkQuery(
                text=request.text,
                statuses=request.statuses,
                tag=request.tag,
                limit=request.limit,
                offset=request.offset,
            ),
        )
        # Never the query text: a person may have typed anything into it.
        logger.info(
            "Word search %s found %d sections; returned %s",
            query_id,
            hits.total,
            [(hit.chunk_id, round(hit.score, 3)) for hit in hits.hits],
        )

        if hits.total > service.MAX_MATCHES:
            raise service.TooManyMatchesError(query_id, hits.total)

        return service.WordResults(
            query_id=query_id,
            total=hits.total,
            results=tuple(
                service.SectionResult(
                    hit=hit, excerpt=excerpts.of_hit(request.text, hit)
                )
                for hit in hits.hits
            ),
        )

    async def search_documents(
        self,
        request: service.DocumentSearch,
    ) -> service.DocumentResults:
        """Groups every matching section by document, then pages the documents.

        The index gives hits best first, so grouping in order of first
        appearance ranks a document where its best section does. The titles
        the words cover are given apart, at most `MAX_TITLE_COVERS`, and a
        document may be in both; its result then carries `title_match`.
        Excerpts are made only for the sections given back.
        """
        query_id = _query_id()
        query = chunk_index.ChunkQuery(
            text=request.text,
            statuses=request.statuses,
            tag=request.tag,
            # Every match: past `MAX_MATCHES` (at most `MAX_LIMIT`) none are
            # served anyway.
            limit=chunk_index.MAX_LIMIT,
        )
        hits = await self._index.search(query)

        if hits.total > service.MAX_MATCHES:
            raise service.TooManyMatchesError(query_id, hits.total)

        titles = {
            hit.document_id: hit for hit in await self._index.search_title_covers(query)
        }
        by_document: dict[ids.DocumentId, list[chunk_index.SectionHit]] = {}

        for hit in hits.hits:
            by_document.setdefault(hit.document_id, []).append(hit)

        documents = list(by_document)
        page = documents[request.offset : request.offset + request.limit]
        logger.info(
            "Document search %s found %d sections in %d documents and %d titles; "
            "returned %s",
            query_id,
            hits.total,
            len(by_document),
            len(titles),
            [(str(document_id), len(by_document[document_id])) for document_id in page],
        )

        return service.DocumentResults(
            query_id=query_id,
            total=len(documents),
            document_total=len(by_document.keys() | titles.keys()),
            section_total=hits.total,
            results=tuple(
                _document_result(
                    request,
                    titles.get(document_id),
                    by_document[document_id],
                )
                for document_id in page
            ),
            titles=tuple(
                _document_result(request, title, by_document.get(document_id, []))
                for document_id, title in itertools.islice(
                    titles.items(),
                    service.MAX_TITLE_COVERS,
                )
            ),
        )

    async def search_titles(self, request: service.TitleSearch) -> service.TitleResults:
        query_id = _query_id()
        hits = await self._index.search_titles(
            chunk_index.TitleQuery(
                text=request.text,
                statuses=request.statuses,
                limit=request.limit,
            ),
        )
        logger.info(
            "Title search %s returned %s",
            query_id,
            [(str(hit.document_id), round(hit.score, 3)) for hit in hits],
        )
        return service.TitleResults(query_id=query_id, results=tuple(hits))


def _document_result(
    request: service.DocumentSearch,
    title: chunk_index.TitleCoverHit | None,
    sections: list[chunk_index.SectionHit],
) -> service.DocumentResult:
    """A title with no sections is still a document: `title` or `sections`
    is always given."""
    document = title or sections[0]

    return service.DocumentResult(
        document_id=document.document_id,
        version_id=document.version_id,
        title=document.title,
        status=document.status,
        url=document.url,
        sections=tuple(
            service.SectionResult(hit=hit, excerpt=excerpts.of_hit(request.text, hit))
            for hit in sections[: request.sections]
        ),
        section_total=len(sections),
        title_match=title,
    )


def _query_id() -> str:
    return uuid.uuid4().hex
