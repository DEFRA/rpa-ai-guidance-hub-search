"""`SearchService` for keyword search, over any `ChunkIndex`."""

import logging
import uuid

from app.index import chunk_index
from app.search import excerpts, routing, service

logger = logging.getLogger(__name__)


class KeywordSearch:
    def __init__(self, index: chunk_index.ChunkIndex) -> None:
        self._index = index

    async def search_words(self, request: service.WordSearch) -> service.WordResults:
        query_id = _query_id()
        hits = await self._index.search(
            chunk_index.ChunkQuery(
                text=request.text,
                statuses=routing.statuses_for(request.role),
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

        A document ranks where its best section does. The index gives hits best
        first, so grouping in order of first appearance keeps that order.
        Excerpts are made only for the sections given back.
        """
        query_id = _query_id()
        hits = await self._index.search(
            chunk_index.ChunkQuery(
                text=request.text,
                statuses=routing.statuses_for(request.role),
                tag=request.tag,
                # Every match: past `MAX_MATCHES` (at most `MAX_LIMIT`) none
                # are served anyway.
                limit=chunk_index.MAX_LIMIT,
            ),
        )

        if hits.total > service.MAX_MATCHES:
            raise service.TooManyMatchesError(query_id, hits.total)

        by_document: dict[uuid.UUID, list[chunk_index.SectionHit]] = {}

        for hit in hits.hits:
            by_document.setdefault(hit.document_id, []).append(hit)

        documents = list(by_document.values())
        page = documents[request.offset : request.offset + request.limit]
        logger.info(
            "Document search %s found %d sections in %d documents; returned %s",
            query_id,
            hits.total,
            len(documents),
            [(str(sections[0].document_id), len(sections)) for sections in page],
        )

        return service.DocumentResults(
            query_id=query_id,
            total=len(documents),
            section_total=hits.total,
            results=tuple(
                service.DocumentResult(
                    sections=tuple(
                        service.SectionResult(
                            hit=hit,
                            excerpt=excerpts.of_hit(request.text, hit),
                        )
                        for hit in sections[: request.sections]
                    ),
                    section_total=len(sections),
                )
                for sections in page
            ),
        )

    async def search_titles(self, request: service.TitleSearch) -> service.TitleResults:
        query_id = _query_id()
        hits = await self._index.search_titles(
            chunk_index.TitleQuery(
                text=request.text,
                statuses=routing.statuses_for(request.role),
                limit=request.limit,
            ),
        )
        logger.info(
            "Title search %s returned %s",
            query_id,
            [(str(hit.document_id), round(hit.score, 3)) for hit in hits],
        )
        return service.TitleResults(query_id=query_id, results=tuple(hits))


def _query_id() -> str:
    return uuid.uuid4().hex
