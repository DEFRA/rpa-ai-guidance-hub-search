"""In-memory `ChunkIndex`: `word_match.find` for words, difflib for titles."""

import difflib
from collections.abc import Sequence

from app.index import chunk_index, index_schema
from app.matching import word_match
from app.records import ids, models

_TITLE_CUTOFF = 0.6


class InMemoryChunkIndex:
    def __init__(self) -> None:
        self._documents: dict[
            ids.DocumentId,
            tuple[models.DocumentHeader, tuple[models.ChunkRecord, ...]],
        ] = {}

    async def replace_document(
        self,
        header: models.DocumentHeader,
        chunks: Sequence[models.ChunkRecord],
    ) -> None:
        for chunk in chunks:
            index_schema.to_index_document(header, chunk)

        if chunks:
            self._documents[header.document_id] = (header, tuple(chunks))
        else:
            self._documents.pop(header.document_id, None)

    async def remove_document(self, document_id: ids.DocumentId) -> None:
        self._documents.pop(document_id, None)

    async def search(self, query: chunk_index.ChunkQuery) -> chunk_index.SectionHits:
        weights = index_schema.weights()
        best: dict[ids.SectionId, chunk_index.SectionHit] = {}

        for header, chunks in self._visible(query.statuses):
            if query.tag is not None and query.tag not in {
                header.document_id,
                header.version_id,
            }:
                continue

            for chunk in chunks:
                headings = "\n".join(chunk.heading_path)
                score = weights[index_schema.HEADING_PATH] * len(
                    word_match.find(query.text, headings),
                ) + weights[index_schema.BODY] * len(
                    word_match.find(query.text, chunk.plain_text),
                )
                kept = best.get(chunk.section_id)

                if score and (kept is None or score > kept.score):
                    best[chunk.section_id] = _hit(header, chunk, score)

        ranked = sorted(best.values(), key=lambda hit: hit.score, reverse=True)
        page = ranked[query.offset : query.offset + query.limit]
        return chunk_index.SectionHits(hits=tuple(page), total=len(ranked))

    async def search_titles(
        self,
        query: chunk_index.TitleQuery,
    ) -> list[chunk_index.TitleHit]:
        hits = [
            chunk_index.TitleHit(
                document_id=header.document_id,
                version_id=header.version_id,
                title=header.title,
                status=header.status,
                url=header.url,
                score=_closeness(query.text, header.title),
            )
            for header, _ in self._visible(query.statuses)
        ]
        hits = [hit for hit in hits if hit.score >= _TITLE_CUTOFF]
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[: query.limit]

    def _visible(
        self,
        statuses: frozenset[models.DocumentStatus],
    ) -> list[tuple[models.DocumentHeader, tuple[models.ChunkRecord, ...]]]:
        return [
            entry for entry in self._documents.values() if entry[0].status in statuses
        ]


def _closeness(text: str, title: str) -> float:
    def ratio(left: str, right: str) -> float:
        return difflib.SequenceMatcher(None, left, right).ratio()

    def sort_words(value: str) -> str:
        return " ".join(sorted(value.casefold().split()))

    return max(
        ratio(text.casefold(), title.casefold()),
        ratio(sort_words(text), sort_words(title)),
    )


def _hit(
    header: models.DocumentHeader,
    chunk: models.ChunkRecord,
    score: float,
) -> chunk_index.SectionHit:
    return chunk_index.SectionHit(
        section_id=chunk.section_id,
        chunk_id=chunk.chunk_id,
        document_id=chunk.document_id,
        version_id=chunk.version_id,
        title=header.title,
        status=chunk.status,
        heading_path=chunk.heading_path,
        section_number=chunk.section_number,
        anchor=chunk.anchor,
        url=header.url,
        plain_text=chunk.plain_text,
        score=score,
    )
