"""`ChunkIndex` on a Tantivy index in a local directory.

Tantivy allows one writer per index. Each write opens a writer, commits once
and reloads, so a document's entries change in a single commit.

Word search runs in four steps:

1. **Shortlist.** Every 3-character run of each query word must appear in a
   section's text, or every run in its headings. A query of 1 or 2
   characters matches whole words instead.
2. **Rank.** The shortlist is scored with BM25 on the weighted fields.
3. **Check.** Each hit is checked with `word_match.find`, which drops the
   false positives that n-grams let through.
4. **Answer.** Chunks collapse to their section's best chunk, the one with
   the most matches.

Near matches shortlist sections by fuzzy terms on the unstemmed body, check
them with `word_match.find_near`, and rank below every exact match.

Title search shortlists title entries by shared n-grams or fuzzy word starts,
then scores them with `title_match`.
"""

import asyncio
import json
import pathlib
import re
import uuid
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import tantivy

from app.index import chunk_index, index_schema, tantivy_schema
from app.matching import title_match, word_match
from app.records import ids, models

WRITER_HEAP_BYTES = 50_000_000

# A section number such as `4.2.1` or `A.1`, as the whole query.
_CLAUSE = re.compile(r"\d+(?:\.\d+)*|[A-Z]+(?:\.\d+)+")
_CLAUSE_BOOST = 5.0
_SHORT = 2
_NOT_A_WORD = re.compile(r"[^\w\s]|_")

type _Fields = dict[str, list[Any]]


class IncompatibleIndexError(Exception):
    """The index on disk was built with other fields; it needs a rebuild."""


class TantivyChunkIndex:
    def __init__(self, directory: pathlib.Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        path = str(directory)
        if tantivy.Index.exists(path):
            _check_fields(directory)
            self._index = tantivy.Index.open(path)
        else:
            self._index = tantivy.Index(tantivy_schema.build_schema(), path=path)

        tantivy_schema.register_tokenizers(self._index)
        self._ngrams = tantivy_schema.ngram_analyzer()

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
        return await asyncio.to_thread(self._search, query)

    async def search_titles(
        self,
        query: chunk_index.TitleQuery,
    ) -> list[chunk_index.TitleHit]:
        return await asyncio.to_thread(self._search_titles, query)

    def _search(self, query: chunk_index.ChunkQuery) -> chunk_index.SectionHits:
        text = " ".join(query.text.split())

        if not text:
            return chunk_index.SectionHits(hits=(), total=0)

        searcher = self._index.searcher()
        filters = self._section_filters(query)
        exact = self._exact_hits(searcher, text, filters)
        near = self._near_hits(searcher, text, filters, exact.keys())
        ranked = [*_ranked(exact.values()), *_ranked(near.values())]
        page = ranked[query.offset : query.offset + query.limit]

        return chunk_index.SectionHits(hits=tuple(page), total=len(ranked))

    def _exact_hits(
        self,
        searcher: tantivy.Searcher,
        text: str,
        filters: list[tantivy.Query],
    ) -> dict[ids.SectionId, chunk_index.SectionHit]:
        clause = text if _CLAUSE.fullmatch(text) else None
        shortlist = self._shortlist(text, clause)
        scoring = self._ranking(text, clause)
        query = _all_of([shortlist, *filters], scoring)
        best: dict[ids.SectionId, chunk_index.SectionHit] = {}

        for score, fields in _candidates(searcher, query):
            headings = "\n".join(fields.get(index_schema.HEADING_PATH, []))
            body = _first(fields, index_schema.BODY) or ""
            number = _first(fields, index_schema.SECTION_NUMBER)
            matches = (
                len(word_match.find(text, headings))
                + len(word_match.find(text, body))
                + (1 if clause is not None and clause == number else 0)
            )

            if matches:
                _keep_best(best, _hit(fields, score, matches, exact=True))

        return best

    def _near_hits(
        self,
        searcher: tantivy.Searcher,
        text: str,
        filters: list[tantivy.Query],
        exact: Iterable[ids.SectionId],
    ) -> dict[ids.SectionId, chunk_index.SectionHit]:
        words = word_match.near_words(text)

        if not words:
            return {}

        schema = self._index.schema
        fuzzy = [
            tantivy.Query.fuzzy_term_query(
                schema,
                index_schema.BODY_EXACT,
                word,
                distance=word_match.typo_budget(word),
                transposition_cost_one=True,
            )
            for word in words
        ]
        query = _all_of([*fuzzy, *filters], None)
        found = set(exact)
        best: dict[ids.SectionId, chunk_index.SectionHit] = {}

        for score, fields in _candidates(searcher, query):
            if fields[index_schema.SECTION_ID][0] in found:
                continue

            matches = len(
                word_match.find_near(text, _first(fields, index_schema.BODY) or "")
            )

            if matches:
                _keep_best(best, _hit(fields, score, matches, exact=False))

        return best

    def _shortlist(self, text: str, clause: str | None) -> tantivy.Query | None:
        """What a section must contain to be checked; None lets every section through."""
        schema = self._index.schema
        options: list[tantivy.Query] = []

        if len(text) <= _SHORT:
            word = text.casefold()
            options = [
                tantivy.Query.term_query(schema, index_schema.BODY_EXACT, word),
                tantivy.Query.term_query(schema, index_schema.HEADING_PATH, word),
            ]
        else:
            runs = [run for word in text.split() for run in self._ngrams.analyze(word)]

            if not runs:
                return None

            options = [
                _every(schema, field, runs)
                for field in (index_schema.BODY_NGRAM, index_schema.HEADING_NGRAM)
            ]

        if clause is not None:
            options.append(
                tantivy.Query.term_query(schema, index_schema.SECTION_NUMBER, clause),
            )

        return tantivy.Query.boolean_query(
            [(tantivy.Occur.Should, option) for option in options],
        )

    def _ranking(self, text: str, clause: str | None) -> tantivy.Query | None:
        """BM25 on the weighted fields; user text never reaches the query syntax."""
        words = _NOT_A_WORD.sub(" ", text).casefold().split()
        parts: list[tantivy.Query] = []

        if words:
            parsed, _ = self._index.parse_query_lenient(
                " ".join(words),
                tantivy_schema.searched_fields(),
                tantivy_schema.field_boosts(),
            )
            parts.append(parsed)

        if clause is not None:
            parts.append(
                tantivy.Query.boost_query(
                    tantivy.Query.term_query(
                        self._index.schema,
                        index_schema.SECTION_NUMBER,
                        clause,
                    ),
                    _CLAUSE_BOOST,
                ),
            )

        if not parts:
            return None

        return tantivy.Query.boolean_query(
            [(tantivy.Occur.Should, part) for part in parts],
        )

    def _section_filters(self, query: chunk_index.ChunkQuery) -> list[tantivy.Query]:
        schema = self._index.schema
        filters = [
            tantivy.Query.term_query(
                schema,
                index_schema.KIND,
                index_schema.EntryKind.SECTION.value,
            ),
            _status_filter(schema, query.statuses),
        ]

        if query.tag is not None:
            filters.append(_tag_filter(schema, query.tag))

        return filters

    def _search_titles(
        self,
        query: chunk_index.TitleQuery,
    ) -> list[chunk_index.TitleHit]:
        text = " ".join(query.text.split())

        if not text:
            return []

        schema = self._index.schema
        filters = [
            tantivy.Query.term_query(
                schema,
                index_schema.KIND,
                index_schema.EntryKind.TITLE.value,
            ),
            _status_filter(schema, query.statuses),
            self._title_shortlist(text),
        ]

        hits = [
            chunk_index.TitleHit(
                document_id=ids.DocumentId(
                    uuid.UUID(fields[index_schema.DOCUMENT_ID][0])
                ),
                version_id=ids.VersionId(uuid.UUID(fields[index_schema.VERSION_ID][0])),
                title=fields[index_schema.TITLE][0],
                status=models.DocumentStatus(fields[index_schema.STATUS][0]),
                url=fields[index_schema.URL][0],
                score=title_match.score(text, fields[index_schema.TITLE][0]),
            )
            for _, fields in _candidates(self._index.searcher(), _all_of(filters, None))
        ]
        close = [hit for hit in hits if hit.score]
        close.sort(key=lambda hit: (-hit.score, hit.title, str(hit.document_id)))
        return close[: query.limit]

    def _title_shortlist(self, text: str) -> tantivy.Query | None:
        """Titles sharing an n-gram, or with a word whose start is near a query word.

        The fuzzy terms catch typos too short to share an n-gram ("Gudia");
        None lets every title through.
        """
        schema = self._index.schema
        runs = self._ngrams.analyze(text)
        options = [
            tantivy.Query.fuzzy_term_query(
                schema,
                index_schema.TITLE,
                word,
                distance=word_match.typo_budget(word),
                transposition_cost_one=True,
                prefix=True,
            )
            for word in word_match.near_words(text)
            if word_match.typo_budget(word)
        ]

        if runs:
            options.append(
                tantivy.Query.term_set_query(schema, index_schema.TITLE_NGRAM, runs),
            )

        if not options:
            return None

        return tantivy.Query.boolean_query(
            [(tantivy.Occur.Should, option) for option in options],
        )

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


def _check_fields(directory: pathlib.Path) -> None:
    meta = json.loads((directory / "meta.json").read_text())
    stored = [field["name"] for field in meta["schema"]]
    expected = [field.name for field in index_schema.CHUNK_INDEX]

    if stored != expected:
        msg = (
            f"index at {directory} has fields {stored}, expected {expected}; "
            "rebuild it from the chunk files"
        )
        raise IncompatibleIndexError(msg)


def _every(schema: tantivy.Schema, field: str, terms: Sequence[str]) -> tantivy.Query:
    return tantivy.Query.boolean_query(
        [
            (tantivy.Occur.Must, tantivy.Query.term_query(schema, field, term))
            for term in dict.fromkeys(terms)
        ],
    )


def _all_of(
    required: Sequence[tantivy.Query | None],
    scoring: tantivy.Query | None,
) -> tantivy.Query:
    """Every required query must match, scored by `scoring` alone."""
    clauses = [
        (tantivy.Occur.Must, tantivy.Query.const_score_query(query, 0.0))
        for query in required
        if query is not None
    ]

    if scoring is not None:
        clauses.append((tantivy.Occur.Should, scoring))

    return tantivy.Query.boolean_query(clauses)


def _status_filter(
    schema: tantivy.Schema,
    statuses: frozenset[models.DocumentStatus],
) -> tantivy.Query:
    return tantivy.Query.term_set_query(
        schema,
        index_schema.STATUS,
        sorted(status.value for status in statuses),
    )


def _tag_filter(schema: tantivy.Schema, tag: uuid.UUID) -> tantivy.Query:
    return tantivy.Query.boolean_query(
        [
            (
                tantivy.Occur.Should,
                tantivy.Query.term_query(schema, index_schema.DOCUMENT_ID, str(tag)),
            ),
            (
                tantivy.Occur.Should,
                tantivy.Query.term_query(schema, index_schema.VERSION_ID, str(tag)),
            ),
        ],
    )


def _candidates(
    searcher: tantivy.Searcher,
    query: tantivy.Query,
) -> list[tuple[float, _Fields]]:
    """Every match, best first. The corpus is small enough to check them all."""
    if searcher.num_docs == 0:
        return []

    result = searcher.search(query, limit=searcher.num_docs)
    return [(score, searcher.doc(address).to_dict()) for score, address in result.hits]


def _first(fields: _Fields, name: str) -> Any:
    values = fields.get(name)
    return values[0] if values else None


def _hit(
    fields: _Fields,
    score: float,
    matches: int,
    *,
    exact: bool,
) -> chunk_index.SectionHit:
    return chunk_index.SectionHit(
        section_id=ids.SectionId(fields[index_schema.SECTION_ID][0]),
        chunk_id=ids.ChunkId(fields[index_schema.CHUNK_ID][0]),
        document_id=ids.DocumentId(uuid.UUID(fields[index_schema.DOCUMENT_ID][0])),
        version_id=ids.VersionId(uuid.UUID(fields[index_schema.VERSION_ID][0])),
        title=fields[index_schema.TITLE][0],
        status=models.DocumentStatus(fields[index_schema.STATUS][0]),
        heading_path=tuple(fields.get(index_schema.HEADING_PATH, [])),
        section_number=_first(fields, index_schema.SECTION_NUMBER),
        anchor=_first(fields, index_schema.ANCHOR) or "",
        url=fields[index_schema.URL][0],
        plain_text=_first(fields, index_schema.BODY) or "",
        score=score,
        exact=exact,
        matches=matches,
    )


def _keep_best(
    best: dict[ids.SectionId, chunk_index.SectionHit],
    hit: chunk_index.SectionHit,
) -> None:
    """A section's best chunk has the most matches, then the higher score."""
    kept = best.get(hit.section_id)

    if kept is None or (hit.matches, hit.score) > (kept.matches, kept.score):
        best[hit.section_id] = hit


def _ranked(
    hits: Iterable[chunk_index.SectionHit],
) -> list[chunk_index.SectionHit]:
    return sorted(
        hits,
        key=lambda hit: (-hit.score, -hit.matches, str(hit.document_id), hit.anchor),
    )


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
