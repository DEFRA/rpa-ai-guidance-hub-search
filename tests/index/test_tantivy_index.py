"""The write half of the Tantivy index: one document's entries replaced in one commit."""

import dataclasses
import uuid

import pytest
import tantivy

from app.index import index_schema, tantivy_index, tantivy_schema
from app.records import ids
from tests.support import chunks as fixtures

Section = fixtures.Section
NEXT_VERSION_ID = ids.VersionId(uuid.UUID("0199b8a2-0000-7000-8000-0000000000ac"))
OTHER_DOCUMENT_ID = ids.DocumentId(uuid.UUID("0199b8a2-0000-7000-8000-0000000000aa"))

CLAIMS = fixtures.document(
    [
        Section("1 Overview", 1, ("Read this first.", "Then this.")),
        Section("2 Eligibility", 1, ("Applicants must hold a holding number.",)),
    ],
)


@pytest.fixture
def directory(tmp_path):
    return tmp_path / "index"


@pytest.fixture
def chunk_index(directory):
    return tantivy_index.TantivyChunkIndex(directory)


def _entries(directory, field=None, value=None):
    """Stored content entries, read through a separately opened index."""
    return [
        entry
        for entry in _stored(directory, field, value)
        if entry.get("kind") != [index_schema.EntryKind.MARKER.value]
    ]


def _markers(directory):
    return [
        entry
        for entry in _stored(directory)
        if entry.get("kind") == [index_schema.EntryKind.MARKER.value]
    ]


def _stored(directory, field=None, value=None):
    index = tantivy.Index.open(str(directory))
    tantivy_schema.register_tokenizers(index)
    searcher = index.searcher()

    query = (
        tantivy.Query.all_query()
        if field is None
        else tantivy.Query.term_query(index.schema, field, value)
    )

    hits = searcher.search(query, 1000).hits

    return sorted(
        (searcher.doc(address).to_dict() for _, address in hits),
        key=lambda doc: (doc.get("kind"), doc.get("ordinal", [0])),
    )


def _values(entries, field):
    return [entry[field][0] for entry in entries if field in entry]


class TestReplacingADocument:
    async def test_adds_its_section_entries_and_one_title_entry(
        self,
        chunk_index,
        directory,
    ):
        header, chunks = CLAIMS

        await chunk_index.replace_document(header, chunks)

        entries = _entries(directory)

        assert _values(entries, index_schema.KIND) == [
            "section",
            "section",
            "section",
            "title",
        ]
        assert _values(entries, index_schema.CHUNK_ID) == [
            chunk.chunk_id for chunk in chunks
        ]
        assert _values(entries, index_schema.TITLE) == ["Claims Guidance"] * 4

    async def test_stores_what_a_hit_needs(self, chunk_index, directory):
        header, chunks = CLAIMS

        await chunk_index.replace_document(header, chunks)

        entry = _entries(directory, index_schema.CHUNK_ID, chunks[2].chunk_id)[0]

        assert {
            name: entry[name][0]
            for name in (
                "section_id",
                "document_id",
                "version_id",
                "status",
                "anchor",
                "section_number",
                "body",
                "level",
                "ordinal",
            )
        } == {
            "section_id": chunks[2].section_id,
            "document_id": str(header.document_id),
            "version_id": str(header.version_id),
            "status": "published",
            "anchor": "2-eligibility",
            "section_number": "2",
            "body": "Applicants must hold a holding number.",
            "level": 1,
            "ordinal": 2,
        }
        assert entry["heading_path"] == ["Eligibility"]

    async def test_re_running_the_same_version_changes_nothing(
        self,
        chunk_index,
        directory,
    ):
        await chunk_index.replace_document(*CLAIMS)
        once = _entries(directory)

        await chunk_index.replace_document(*CLAIMS)

        assert _entries(directory) == once

    async def test_a_newer_version_leaves_nothing_of_the_old_one(
        self,
        chunk_index,
        directory,
    ):
        await chunk_index.replace_document(*CLAIMS)

        newer = fixtures.document(
            [Section("1 Overview", 1, ("Rewritten.",))],
            fixtures.header(version_id=NEXT_VERSION_ID),
        )

        await chunk_index.replace_document(*newer)

        assert set(_values(_entries(directory), index_schema.VERSION_ID)) == {
            str(NEXT_VERSION_ID),
        }

    async def test_leaves_other_documents_alone(self, chunk_index, directory):
        other = fixtures.document(
            [Section("1 Woodland", 1)],
            fixtures.header(document_id=OTHER_DOCUMENT_ID, title="Woodland Guidance"),
        )

        await chunk_index.replace_document(*other)

        await chunk_index.replace_document(*CLAIMS)
        await chunk_index.replace_document(CLAIMS[0], [])

        assert set(_values(_entries(directory), index_schema.DOCUMENT_ID)) == {
            str(OTHER_DOCUMENT_ID),
        }

    async def test_stores_topics_and_meta(self, chunk_index, directory):
        header, chunks = CLAIMS

        tagged = dataclasses.replace(
            chunks[0],
            topics=("claims", "livestock"),
            meta={"source_page": 12},
        )

        await chunk_index.replace_document(header, [tagged])

        entry = _entries(directory, index_schema.CHUNK_ID, tagged.chunk_id)[0]

        assert entry["meta"] == [{"source_page": 12}]
        assert [
            _entries(directory, index_schema.TOPICS, tantivy.Facet.from_string(topic))
            for topic in ("/claims", "/livestock", "/woodland")
        ] == [[entry], [entry], []]


class TestRemovingADocument:
    async def test_removes_every_entry(self, chunk_index, directory):
        await chunk_index.replace_document(*CLAIMS)

        await chunk_index.remove_document(CLAIMS[0].document_id)

        assert _entries(directory) == []

    async def test_is_fine_when_the_document_is_absent(self, chunk_index, directory):
        await chunk_index.remove_document(OTHER_DOCUMENT_ID)

        assert _entries(directory) == []


class TestRemovingAVersion:
    async def test_removes_every_entry_when_it_is_the_indexed_version(
        self, chunk_index, directory
    ):
        header, _ = CLAIMS
        await chunk_index.replace_document(*CLAIMS)

        await chunk_index.remove_version(header.document_id, header.version_id)

        assert _entries(directory) == []

    async def test_leaves_the_document_when_another_version_is_indexed(
        self, chunk_index, directory
    ):
        header, _ = CLAIMS
        await chunk_index.replace_document(*CLAIMS)

        await chunk_index.remove_version(header.document_id, NEXT_VERSION_ID)

        assert len(_entries(directory)) == 4

    async def test_leaves_other_documents(self, chunk_index, directory):
        header, _ = CLAIMS
        other = fixtures.document(
            [Section("1 Woodland", 1)],
            fixtures.header(document_id=OTHER_DOCUMENT_ID),
        )
        await chunk_index.replace_document(*CLAIMS)
        await chunk_index.replace_document(*other)

        await chunk_index.remove_version(header.document_id, header.version_id)

        assert _values(_entries(directory), index_schema.DOCUMENT_ID) == [
            str(OTHER_DOCUMENT_ID),
            str(OTHER_DOCUMENT_ID),
        ]


class TestLatestVersion:
    async def test_is_none_for_a_document_never_seen(self, chunk_index):
        assert await chunk_index.latest_version(OTHER_DOCUMENT_ID) is None

    async def test_survives_removing_the_version(self, chunk_index, directory):
        header, _ = CLAIMS
        await chunk_index.replace_document(*CLAIMS)

        await chunk_index.remove_version(header.document_id, header.version_id)

        assert await chunk_index.indexed_version(header.document_id) is None
        assert await chunk_index.latest_version(header.document_id) == (
            header.version_id
        )
        assert len(_markers(directory)) == 1

    async def test_survives_a_version_with_no_chunks(self, chunk_index):
        header, _ = CLAIMS

        await chunk_index.replace_document(header, [])

        assert await chunk_index.indexed_version(header.document_id) is None
        assert await chunk_index.latest_version(header.document_id) == (
            header.version_id
        )

    async def test_records_a_version_removed_before_it_was_applied(self, chunk_index):
        header, _ = CLAIMS
        await chunk_index.replace_document(*CLAIMS)

        await chunk_index.remove_version(header.document_id, NEXT_VERSION_ID)

        assert await chunk_index.indexed_version(header.document_id) == (
            header.version_id
        )
        assert await chunk_index.latest_version(header.document_id) == NEXT_VERSION_ID

    async def test_never_goes_back_to_an_older_version(self, chunk_index):
        header, _ = CLAIMS
        await chunk_index.replace_document(
            dataclasses.replace(header, version_id=NEXT_VERSION_ID), []
        )

        await chunk_index.remove_version(header.document_id, header.version_id)

        assert await chunk_index.latest_version(header.document_id) == NEXT_VERSION_ID

    async def test_is_forgotten_with_the_document(self, chunk_index):
        header, _ = CLAIMS
        await chunk_index.replace_document(*CLAIMS)

        await chunk_index.remove_document(header.document_id)

        assert await chunk_index.latest_version(header.document_id) is None


class TestIndexedVersion:
    async def test_is_the_version_last_replaced(self, chunk_index):
        header, chunks = CLAIMS
        await chunk_index.replace_document(header, chunks)
        await chunk_index.replace_document(
            dataclasses.replace(header, version_id=NEXT_VERSION_ID),
            [
                dataclasses.replace(chunk, version_id=NEXT_VERSION_ID)
                for chunk in chunks
            ],
        )

        assert await chunk_index.indexed_version(header.document_id) == NEXT_VERSION_ID

    async def test_is_none_for_an_absent_document(self, chunk_index):
        assert await chunk_index.indexed_version(OTHER_DOCUMENT_ID) is None


class TestOpeningTheIndex:
    async def test_reopens_what_an_earlier_instance_wrote(self, directory):
        await tantivy_index.TantivyChunkIndex(directory).replace_document(*CLAIMS)

        reopened = tantivy_index.TantivyChunkIndex(directory)
        await reopened.replace_document(*CLAIMS)

        assert len(_entries(directory)) == 4

    async def test_search_is_not_built_yet(self, chunk_index):
        with pytest.raises(NotImplementedError):
            await chunk_index.search(None)

        with pytest.raises(NotImplementedError):
            await chunk_index.search_titles(None)
