"""`ChunkIndex` on a Tantivy index in a local directory.

Only the write half is built so far. `search` and `search_titles` raise
`NotImplementedError`.

Tantivy allows one writer per index. Each write opens a writer, commits once
and reloads, so a document's entries change in a single commit.
"""

import asyncio
import pathlib
import uuid
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

        await asyncio.to_thread(
            self._replace,
            header.document_id,
            header.version_id,
            documents,
        )

    async def remove_document(self, document_id: ids.DocumentId) -> None:
        """Forgets the document entirely, marker included."""
        await asyncio.to_thread(self._forget, document_id)

    async def remove_version(
        self,
        document_id: ids.DocumentId,
        version_id: ids.VersionId,
    ) -> None:
        """Deletes the entries carrying both ids, chunks and title alike.

        The marker moves to this version unless a newer one is already there.
        """
        await asyncio.to_thread(self._remove_version, document_id, version_id)

    async def indexed_version(
        self,
        document_id: ids.DocumentId,
    ) -> ids.VersionId | None:
        return await asyncio.to_thread(self._version_of, document_id, content=True)

    async def latest_version(
        self,
        document_id: ids.DocumentId,
    ) -> ids.VersionId | None:
        return await asyncio.to_thread(self._version_of, document_id, content=False)

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
        version_id: ids.VersionId,
        documents: Sequence[Mapping[str, index_schema.IndexValue]],
    ) -> None:
        writer = self._index.writer(heap_size=WRITER_HEAP_BYTES)
        writer.delete_documents_by_query(self._entries(document_id, content=True))

        for document in documents:
            writer.add_document(_to_tantivy(document))

        self._mark(writer, document_id, version_id)
        self._commit(writer)

    def _forget(self, document_id: ids.DocumentId) -> None:
        writer = self._index.writer(heap_size=WRITER_HEAP_BYTES)
        writer.delete_documents_by_term(index_schema.DOCUMENT_ID, str(document_id))
        self._commit(writer)

    def _remove_version(
        self,
        document_id: ids.DocumentId,
        version_id: ids.VersionId,
    ) -> None:
        both = tantivy.Query.boolean_query(
            [
                (tantivy.Occur.Must, self._entries(document_id, content=True)),
                (tantivy.Occur.Must, self._term(index_schema.VERSION_ID, version_id)),
            ],
        )
        latest = self._version_of(document_id, content=False)
        writer = self._index.writer(heap_size=WRITER_HEAP_BYTES)
        writer.delete_documents_by_query(both)

        if latest is None or not ids.is_older(version_id, latest):
            self._mark(writer, document_id, version_id)

        self._commit(writer)

    def _mark(
        self,
        writer: tantivy.IndexWriter,
        document_id: ids.DocumentId,
        version_id: ids.VersionId,
    ) -> None:
        writer.delete_documents_by_query(self._entries(document_id, content=False))
        writer.add_document(
            _to_tantivy(index_schema.to_marker_document(document_id, version_id)),
        )

    def _version_of(
        self,
        document_id: ids.DocumentId,
        *,
        content: bool,
    ) -> ids.VersionId | None:
        searcher = self._index.searcher()
        hits = searcher.search(
            self._entries(document_id, content=content), limit=1
        ).hits

        if not hits:
            return None

        _, address = hits[0]
        (version_id,) = searcher.doc(address)[index_schema.VERSION_ID]
        return ids.VersionId(uuid.UUID(version_id))

    def _entries(self, document_id: ids.DocumentId, *, content: bool) -> tantivy.Query:
        """The document's content entries, or its one marker."""
        marker = tantivy.Query.term_query(
            self._index.schema,
            index_schema.KIND,
            index_schema.EntryKind.MARKER.value,
        )
        return tantivy.Query.boolean_query(
            [
                (tantivy.Occur.Must, self._term(index_schema.DOCUMENT_ID, document_id)),
                (tantivy.Occur.MustNot if content else tantivy.Occur.Must, marker),
            ],
        )

    def _term(self, field: str, value: uuid.UUID) -> tantivy.Query:
        return tantivy.Query.term_query(self._index.schema, field, str(value))

    def _commit(self, writer: tantivy.IndexWriter) -> None:
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
