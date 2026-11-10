"""The canonical document header and section record, and chunks, as stored."""

import dataclasses
import datetime as dt
from typing import ClassVar

import pytest

from app.records import ids, models, source_metadata
from tests.support import chunks as fixtures

HEADER = fixtures.header()

REFERENCE = models.Reference(
    kind=models.ReferenceKind.INTERNAL,
    text="see section 3.2",
    anchor="32-payments",
    section_id=ids.SectionId("section-32"),
)

SECTION = dataclasses.replace(
    fixtures.sections([fixtures.Section("4.2.1 Concurrent claims", 3)])[1][0],
    blocks=(
        fixtures.paragraph("First **part**."),
        models.Block(models.BlockKind.LIST, "- one\n- two", ("one", "two")),
    ),
    references=(
        REFERENCE,
        models.Reference(
            kind=models.ReferenceKind.EXTERNAL,
            text="GOV.UK",
            url="https://www.gov.uk/claims",
        ),
    ),
    topics=("claims",),
    meta={"source_page": 12},
)


class TestADocumentHeaderAsStored:
    def test_is_stored_with_text_ids_and_an_iso_timestamp(self):
        assert HEADER.to_record() == {
            "document_id": "0199b8a2-0000-7000-8000-000000000001",
            "version_id": "0199b8a2-0000-7000-8000-000000000002",
            "status": "published",
            "source_type": "guide",
            "title": "Claims Guidance",
            "url": "/guides/0199b8a2-0000-7000-8000-000000000001",
            "content_url": (
                "s3://managed-docs/0199b8a2-0000-7000-8000-000000000001/"
                "0199b8a2-0000-7000-8000-000000000002/content.md"
            ),
            "occurred_at": "2026-11-03T09:30:00+00:00",
            "source": source_metadata.GuideMetadata.empty().to_record(),
        }

    def test_reads_back_as_it_was_stored(self):
        assert models.DocumentHeader.from_record(HEADER.to_record()) == HEADER

    def test_reads_back_its_source_metadata(self):
        header = dataclasses.replace(
            HEADER,
            source=source_metadata.GuideMetadata(schemes=("SFI",), scheme_year=2026),
        )

        assert models.DocumentHeader.from_record(header.to_record()) == header

    def test_stored_without_source_metadata_reads_as_empty(self):
        stored = {k: v for k, v in HEADER.to_record().items() if k != "source"}

        assert models.DocumentHeader.from_record(stored).source == (
            source_metadata.GuideMetadata.empty()
        )

    def test_refuses_another_source_types_metadata(self):
        @dataclasses.dataclass(frozen=True)
        class PageMetadata:
            SOURCE_TYPE: ClassVar[str] = "govuk_page"

        with pytest.raises(ValueError, match="'guide' documents"):
            dataclasses.replace(HEADER, source=PageMetadata())

    def test_ignores_fields_it_does_not_know(self):
        stored = {**HEADER.to_record(), "summary": "Not a field."}

        assert models.DocumentHeader.from_record(stored) == HEADER

    def test_refuses_a_timestamp_without_a_timezone(self):
        with pytest.raises(ValueError, match="timezone"):
            dataclasses.replace(HEADER, occurred_at=dt.datetime(2026, 11, 3))  # noqa: DTZ001


class TestADocumentHeadersLinks:
    def test_deep_link_to_a_section(self):
        assert HEADER.link("payments") == f"{HEADER.url}#payments"

    def test_link_to_the_document_without_an_anchor(self):
        assert HEADER.link("") == HEADER.url


class TestASectionRecordAsStored:
    def test_is_stored_with_every_design_field(self):
        stored = SECTION.to_record()

        assert set(stored) == {
            "section_id",
            "document_id",
            "version_id",
            "status",
            "source_type",
            "parent_section_id",
            "heading_path",
            "section_number",
            "anchor",
            "level",
            "ordinal",
            "blocks",
            "content_hash",
            "references",
            "topics",
            "meta",
        }
        assert stored["heading_path"] == ["Concurrent claims"]
        assert stored["section_number"] == "4.2.1"
        assert stored["blocks"][1] == {
            "kind": "list",
            "markdown": "- one\n- two",
            "lines": ["one", "two"],
        }
        assert stored["references"][0] == {
            "kind": "internal",
            "text": "see section 3.2",
            "anchor": "32-payments",
            "section_id": "section-32",
            "url": None,
        }

    def test_reads_back_as_it_was_stored(self):
        assert models.SectionRecord.from_record(SECTION.to_record()) == SECTION

    def test_ignores_fields_it_does_not_know(self):
        stored = {**SECTION.to_record(), "added_later": True}

        assert models.SectionRecord.from_record(stored) == SECTION

    @pytest.mark.parametrize(
        ("key", "attribute", "empty"),
        [
            pytest.param("parent_section_id", "parent_section_id", None, id="parent"),
            pytest.param("section_number", "section_number", None, id="number"),
            pytest.param("references", "references", (), id="references"),
            pytest.param("topics", "topics", (), id="topics"),
            pytest.param("meta", "meta", {}, id="meta"),
        ],
    )
    def test_reads_an_absent_optional_field_as_empty(self, key, attribute, empty):
        stored = SECTION.to_record()
        del stored[key]

        assert getattr(models.SectionRecord.from_record(stored), attribute) == empty


class TestASectionsText:
    def test_joins_its_blocks_markdown(self):
        assert SECTION.text == "First **part**.\n\n- one\n- two"

    def test_has_one_plain_line_per_block_line(self):
        assert SECTION.plain_text == "First **part**.\none\ntwo"

    def test_counts_plain_words(self):
        assert SECTION.token_count == 4

    def test_is_hashed_whole(self):
        assert SECTION.content_hash == models.content_hash(SECTION.text)

    def test_hash_ignores_version_and_status(self):
        later = dataclasses.replace(
            SECTION,
            version_id=fixtures.VERSION_ID.__class__(
                "0199b8a2-0000-7000-8000-0000000000ff",
            ),
            status=models.DocumentStatus.READY_FOR_PUBLISH,
        )

        assert later.content_hash == SECTION.content_hash


class TestAChunkOfASection:
    def test_copies_the_sections_identity(self):
        chunk = models.ChunkRecord.of_section(
            SECTION,
            strategy="test",
            part=1,
            ordinal=7,
            blocks=SECTION.blocks[1:],
        )

        assert (chunk.section_id, chunk.heading_path, chunk.anchor, chunk.topics) == (
            SECTION.section_id,
            SECTION.heading_path,
            SECTION.anchor,
            SECTION.topics,
        )
        assert (chunk.strategy, chunk.part, chunk.ordinal) == ("test", 1, 7)
        assert (chunk.text, chunk.plain_text, chunk.token_count) == (
            "- one\n- two",
            "one\ntwo",
            2,
        )
        assert chunk.chunk_id == ids.chunk_id("test", SECTION.section_id, 1)

    def test_reads_back_as_it_was_stored(self):
        chunk = models.ChunkRecord.of_section(
            SECTION,
            strategy="test",
            part=0,
            ordinal=0,
            blocks=SECTION.blocks,
        )

        assert models.ChunkRecord.from_record(chunk.to_record()) == chunk


class TestAContentHash:
    def test_is_the_sha256_of_the_text(self):
        assert models.content_hash("abc") == (
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        )
