"""`ChunkIndex` on a Tantivy index in a local directory.

Only the write half is built so far. `search` and `search_titles` raise
`NotImplementedError`.

Tantivy allows one writer per index. Each write opens a writer, commits once
and reloads, so a document's entries change in a single commit.
"""

import asyncio
import pathlib
from collections.abc import Mapping, Sequence

import tantivy

from app.index import chunk_index, index_schema, tantivy_schema
from app.records import ids, models

WRITER_HEAP_BYTES = 50_000_000


class TantivyChunkIndex:
    def __init__(self, directory: pathlib.Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        path = str(directory)
        self._index = (
            tantivy.Index.open(path)
            if tantivy.Index.exists(path)
            else tantivy.Index(tantivy_schema.build_schema(), path=path)
        )
        tantivy_schema.register_tokenizers(self._index)

    async def replace_document(
        self,
        header: models.DocumentHeader,
        chunks: Sequence[models.ChunkRecord],
    ) -> None:
        """Removes every entry for the document, then adds its sections and title."""
        documents = [index_schema.to_index_document(header, chunk) for chunk in chunks]

        if documents:
            documents.append(index_schema.to_title_document(header))

        await asyncio.to_thread(self._replace, header.document_id, documents)

    async def remove_document(self, document_id: ids.DocumentId) -> None:
        await asyncio.to_thread(self._replace, document_id, [])

    async def search(self, query: chunk_index.ChunkQuery) -> chunk_index.SectionHits:
        msg = "word search is not built yet"
        raise NotImplementedError(msg)

    async def search_titles(
        self,
        query: chunk_index.TitleQuery,
    ) -> list[chunk_index.TitleHit]:
        msg = "title search is not built yet"
        raise NotImplementedError(msg)

    def _replace(
        self,
        document_id: ids.DocumentId,
        documents: Sequence[Mapping[str, index_schema.IndexValue]],
    ) -> None:
        writer = self._index.writer(heap_size=WRITER_HEAP_BYTES)
        writer.delete_documents_by_term(index_schema.DOCUMENT_ID, str(document_id))

        for document in documents:
            writer.add_document(_to_tantivy(document))

        writer.commit()
        writer.wait_merging_threads()
        self._index.reload()


def _to_tantivy(fields: Mapping[str, index_schema.IndexValue]) -> tantivy.Document:
    document = tantivy.Document()

    for name, value in fields.items():
        _add(document, name, value)

    return document


def _add(document: tantivy.Document, name: str, value: index_schema.IndexValue) -> None:
    match value:
        case None:
            return
        case str():
            document.add_text(name, value)
        case int():
            document.add_integer(name, value)
        case tuple() if name == index_schema.TOPICS:
            for topic in value:
                document.add_facet(name, tantivy.Facet.from_string(f"/{topic}"))
        case tuple():
            for item in value:
                document.add_text(name, item)
        case _:
            document.add_json(name, dict(value))
