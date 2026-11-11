"""The engine-neutral chunk index schema."""

import dataclasses
import hashlib

import pytest

from app.index import index_schema
from app.records import ids, models
from tests.support import chunks as fixtures

HEADER, CHUNKS = fixtures.document([fixtures.Section("4.2.1 Concurrent claims", 3)])

# The fields each schema version was released with. Changing `CHUNK_INDEX`
# without bumping `SCHEMA_VERSION` fails here: add the new version's line.
RELEASED_FIELDS = {
    1: "91fcd65ac9858576afa97e56033112ed4b37bb6c58128c042709150632b825c2",
}


class TestTheSchemaVersion:
    def test_names_the_fields_it_was_released_with(self):
        fingerprint = hashlib.sha256(
            repr(index_schema.CHUNK_INDEX).encode(),
        ).hexdigest()

        assert RELEASED_FIELDS.get(index_schema.SCHEMA_VERSION) == fingerprint


class TestTheChunkIndexFields:
    def test_have_unique_names(self):
        names = [field.name for field in index_schema.CHUNK_INDEX]

        assert len(names) == len(set(names))

    def test_weight_title_heading_and_body_as_designed(self):
        assert index_schema.weights() == {
            "title": 2,
            "heading_path": 3,
            "body": 1,
            "body_exact": 1,
        }

    def test_stem_the_body_but_keep_an_exact_unstored_copy(self):
        fields = {field.name: field for field in index_schema.CHUNK_INDEX}

        assert fields["body"].kind == index_schema.FieldKind.TEXT
        assert fields["body_exact"].kind == index_schema.FieldKind.TEXT_EXACT
        assert not fields["body_exact"].stored

    def test_match_identities_and_filters_whole(self):
        fields = {field.name: field for field in index_schema.CHUNK_INDEX}

        for name in ["chunk_id", "document_id", "version_id", "status", "source_type"]:
            assert fields[name].kind == index_schema.FieldKind.KEYWORD, name


class TestAnIndexField:
    @pytest.mark.parametrize(
        "kind",
        [
            pytest.param(kind, id=kind.value)
            for kind in index_schema.FieldKind
            if kind
            not in {index_schema.FieldKind.TEXT, index_schema.FieldKind.TEXT_EXACT}
        ],
    )
    def test_can_only_be_weighted_if_it_is_text(self, kind):
        with pytest.raises(ValueError, match="only text fields"):
            index_schema.IndexField("x", kind, weight=1)

    @pytest.mark.parametrize("weight", [0, -1])
    def test_needs_a_positive_weight(self, weight):
        with pytest.raises(ValueError, match="positive"):
            index_schema.IndexField("x", index_schema.FieldKind.TEXT, weight=weight)


class TestAChunkAsAnIndexDocument:
    def test_has_a_value_for_exactly_the_index_fields(self):
        document = index_schema.to_index_document(HEADER, CHUNKS[0])

        assert set(document) == {field.name for field in index_schema.CHUNK_INDEX}

    def test_indexes_the_plain_text_as_the_body(self):
        chunk = dataclasses.replace(CHUNKS[0], text="**Bold**", plain_text="Bold")

        document = index_schema.to_index_document(HEADER, chunk)

        assert (document["body"], document["body_exact"]) == ("Bold", "Bold")

    def test_takes_the_title_and_url_from_the_header(self):
        document = index_schema.to_index_document(HEADER, CHUNKS[0])

        assert (document["title"], document["url"]) == ("Claims Guidance", HEADER.url)

    def test_is_a_section_entry_with_a_partial_match_copy_of_the_body(self):
        document = index_schema.to_index_document(HEADER, CHUNKS[0])

        assert document["kind"] == "section"
        assert document["section_id"] == CHUNKS[0].section_id
        assert document["body_ngram"] == CHUNKS[0].plain_text
        assert document["title_ngram"] is None

    def test_refuses_a_chunk_from_another_version(self):
        other = fixtures.header(document_id=ids.DocumentId(fixtures.VERSION_ID))

        with pytest.raises(ValueError, match="does not belong"):
            index_schema.to_index_document(other, CHUNKS[0])

    def test_refuses_a_chunk_with_another_status(self):
        published = fixtures.header(status=models.DocumentStatus.PUBLISHED)
        draft = dataclasses.replace(
            CHUNKS[0],
            status=models.DocumentStatus.READY_FOR_PUBLISH,
        )

        with pytest.raises(ValueError, match="does not belong"):
            index_schema.to_index_document(published, draft)


class TestADocumentAsATitleEntry:
    def test_has_only_the_document_fields_and_its_title(self):
        document = index_schema.to_title_document(HEADER)

        assert {name for name, value in document.items() if value is not None} == {
            "kind",
            "document_id",
            "version_id",
            "status",
            "source_type",
            "url",
            "title",
            "title_ngram",
        }
        assert (document["kind"], document["title_ngram"]) == (
            "title",
            "Claims Guidance",
        )

    def test_has_a_value_for_exactly_the_index_fields(self):
        document = index_schema.to_title_document(HEADER)

        assert set(document) == {field.name for field in index_schema.CHUNK_INDEX}
