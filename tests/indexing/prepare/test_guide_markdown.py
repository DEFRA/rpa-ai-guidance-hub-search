"""A guide's `content.md` becomes canonical sections, then packed chunks."""

import dataclasses
import logging
import uuid

import pytest

from app.indexing.prepare import guide_markdown, packed_chunking, sectioning
from app.records import ids, models, source_metadata
from tests.support import guides

NESTED = """# Claims Guidance

Read this first.

## 1 Overview

Overview text.

### 1.1 Eligibility

Who can claim.

#### 1.1.1 Concurrent claims

Two at once.

## 2 Payments

Payment text.

## Annex A

### A.1 Detail

Annex detail.
"""


def _sections(markdown, section_ids=None):
    document = guide_markdown.GuideMarkdownParser().parse(
        guides.job(),
        markdown,
        section_ids,
    )
    return document.header, sectioning.build(document)


def _chunk(markdown, section_ids=None):
    header, sections = _sections(markdown, section_ids)
    return header, packed_chunking.PackedChunking().chunk(sections)


def _by_anchor(chunks):
    return {chunk.anchor: chunk for chunk in chunks if chunk.part == 0}


def _paragraphs(count, words=100):
    return "\n\n".join(" ".join([f"word{n}"] * words) for n in range(count))


class TestTheTitle:
    def test_comes_from_the_single_hash_line(self):
        header, _ = _chunk("# Claims \\& Payments\n\n## 1 Overview\n\nText.")

        assert header.title == "Claims & Payments"

    @pytest.mark.parametrize(
        "markdown",
        [
            pytest.param("## 1 Overview\n\nText.", id="none"),
            pytest.param("# One\n\n# Two\n\n## 1 Overview", id="two"),
        ],
    )
    def test_must_appear_exactly_once(self, markdown):
        with pytest.raises(guide_markdown.MalformedGuideError) as raised:
            _chunk(markdown)

        assert str(guides.DOCUMENT_ID) in str(raised.value)
        assert str(guides.VERSION_ID) in str(raised.value)

    def test_builds_the_header_from_the_job(self):
        header, _ = _chunk(NESTED)

        assert header == models.DocumentHeader(
            document_id=guides.DOCUMENT_ID,
            version_id=guides.VERSION_ID,
            status=models.DocumentStatus.PUBLISHED,
            source_type=models.SourceType.GUIDE,
            title="Claims Guidance",
            url=f"/guides/{guides.DOCUMENT_ID}",
            content_url=guides.job().content_url,
            occurred_at=guides.OCCURRED_AT,
            source=source_metadata.GuideMetadata.empty(),
        )


class TestTheIds:
    def test_are_the_cms_ids_from_the_job(self):
        header, _ = _chunk(NESTED)

        assert (header.document_id, header.version_id) == (
            guides.DOCUMENT_ID,
            guides.VERSION_ID,
        )

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            pytest.param("version_id", None, id="no-version-id"),
            pytest.param(
                "version_id",
                uuid.UUID("6f2c1a8e-3b4d-4c5e-9f60-718293a4b5c6"),
                id="version-id-not-uuid7",
            ),
            pytest.param(
                "document_id",
                uuid.UUID("6f2c1a8e-3b4d-5c5e-9f60-718293a4b5c6"),
                id="document-id-not-uuid7",
            ),
        ],
    )
    def test_must_be_the_cms_uuid7s(self, field, value):
        job = dataclasses.replace(guides.job(), **{field: value})
        parser = guide_markdown.GuideMarkdownParser()

        with pytest.raises(guide_markdown.NotACmsIdError):
            parser.parse(job, NESTED)


class TestSections:
    def test_text_before_the_first_section_is_a_level_0_chunk(self):
        _, chunks = _chunk(NESTED)

        assert (
            chunks[0].level,
            chunks[0].heading_path,
            chunks[0].anchor,
            chunks[0].section_number,
            chunks[0].parent_section_id,
            chunks[0].plain_text,
        ) == (0, (), "", None, None, "Read this first.")

    def test_no_preamble_chunk_when_there_is_no_text_before_the_first_section(self):
        _, chunks = _chunk("# Title\n\n## 1 Overview\n\nText.")

        assert [chunk.anchor for chunk in chunks] == ["1-overview"]

    def test_each_heading_starts_a_section_with_its_path_number_and_level(self):
        _, chunks = _chunk(NESTED)

        assert [
            (chunk.heading_path, chunk.section_number, chunk.anchor, chunk.level)
            for chunk in chunks[1:]
        ] == [
            (("Overview",), "1", "1-overview", 1),
            (("Overview", "Eligibility"), "1.1", "11-eligibility", 2),
            (
                ("Overview", "Eligibility", "Concurrent claims"),
                "1.1.1",
                "111-concurrent-claims",
                3,
            ),
            (("Payments",), "2", "2-payments", 1),
            (("Annex A",), None, "annex-a", 1),
            (("Annex A", "Detail"), "A.1", "a1-detail", 2),
        ]

    def test_a_section_knows_its_parent(self):
        _, chunks = _chunk(NESTED)
        found = _by_anchor(chunks)

        assert [
            found[anchor].parent_section_id
            for anchor in ("1-overview", "11-eligibility", "111-concurrent-claims")
        ] == [None, found["1-overview"].section_id, found["11-eligibility"].section_id]
        assert found["2-payments"].parent_section_id is None
        assert found["a1-detail"].parent_section_id == found["annex-a"].section_id

    def test_a_chunk_never_crosses_a_heading(self):
        _, chunks = _chunk(NESTED)

        assert [chunk.plain_text for chunk in chunks] == [
            "Read this first.",
            "Overview text.",
            "Who can claim.",
            "Two at once.",
            "Payment text.",
            "",
            "Annex detail.",
        ]

    def test_a_heading_with_no_body_still_gets_a_chunk(self):
        _, chunks = _chunk("# Title\n\n## Annex A\n\n### A.1 Detail\n\nText.")

        assert (chunks[0].anchor, chunks[0].text, chunks[0].token_count) == (
            "annex-a",
            "",
            0,
        )

    def test_ordinals_run_through_the_guide_in_order(self):
        _, chunks = _chunk(NESTED)

        assert [chunk.ordinal for chunk in chunks] == list(range(len(chunks)))

    def test_heading_paths_have_no_markup(self):
        _, chunks = _chunk("# Title\n\n## 1 Claims **\\&** appeals\n\nText.")

        assert chunks[0].heading_path == ("Claims & appeals",)


class TestPacking:
    def test_a_table_is_a_chunk_of_its_own(self):
        markdown = (
            "# Title\n\n## 1 Overview\n\nBefore.\n\n"
            "| A | B |\n| - | - |\n| 1 | 2 |\n\nAfter."
        )

        _, chunks = _chunk(markdown)

        assert [(chunk.part, chunk.plain_text) for chunk in chunks] == [
            (0, "Before."),
            (1, "A | B\n1 | 2"),
            (2, "After."),
        ]

    def test_a_long_section_becomes_parts_of_one_section(self):
        _, chunks = _chunk(f"# Title\n\n## 1 Overview\n\n{_paragraphs(20)}")

        assert [chunk.part for chunk in chunks] == [0, 1, 2]
        assert len({chunk.section_id for chunk in chunks}) == 1
        assert len({chunk.chunk_id for chunk in chunks}) == 3
        assert all(chunk.token_count <= packed_chunking.MAX_TOKENS for chunk in chunks)

    def test_a_short_section_is_one_chunk(self):
        _, chunks = _chunk(f"# Title\n\n## 1 Overview\n\n{_paragraphs(7)}")

        assert [chunk.token_count for chunk in chunks] == [700]

    @pytest.mark.parametrize(
        "block",
        [
            pytest.param("\n".join(f"- {'item ' * 100}" for _ in range(10)), id="list"),
            pytest.param(
                "\n>\n".join(f"> {'quote ' * 100}" for _ in range(10)), id="quote"
            ),
            pytest.param(" ".join(["word"] * 1000), id="paragraph"),
        ],
    )
    def test_an_oversize_block_is_never_split(self, block):
        _, chunks = _chunk(f"# Title\n\n## 1 Overview\n\nIntro.\n\n{block}\n\nOutro.")

        assert [chunk.token_count for chunk in chunks] == [1, 1000, 1]

    def test_text_is_the_raw_markdown_without_the_heading(self):
        _, chunks = _chunk("# Title\n\n## 1 Overview\n\n**Bold** start.\n\n- item")

        assert chunks[0].text == "**Bold** start.\n\n- item"
        assert chunks[0].plain_text == "Bold start.\nitem"
        assert chunks[0].content_hash == models.content_hash(chunks[0].text)


class TestSectionIds:
    def test_come_from_the_apis_map_by_anchor(self):
        given = {"1-overview": ids.SectionId("0199b8a2-0000-7000-8000-0000000000e1")}

        _, chunks = _chunk(NESTED, given)

        found = _by_anchor(chunks)
        assert found["1-overview"].section_id == given["1-overview"]
        assert found["11-eligibility"].parent_section_id == given["1-overview"]

    def test_fall_back_to_the_hash_without_a_map(self):
        _, chunks = _chunk(NESTED)

        assert _by_anchor(chunks)["11-eligibility"].section_id == ids.section_id(
            models.SourceType.GUIDE,
            guides.DOCUMENT_ID,
            ("Overview", "Eligibility"),
        )

    def test_fall_back_to_the_hash_for_an_anchor_the_map_lacks(self, caplog):
        caplog.set_level(logging.WARNING)

        _, chunks = _chunk(NESTED, {})

        _, unmapped = _chunk(NESTED)
        assert [chunk.section_id for chunk in chunks] == [
            chunk.section_id for chunk in unmapped
        ]
        assert "11-eligibility" in caplog.text

    def test_duplicate_headings_get_different_ids_and_anchors(self):
        _, chunks = _chunk("# Title\n\n## Annex A\n\nOne.\n\n## Annex A\n\nTwo.")

        assert [chunk.anchor for chunk in chunks] == ["annex-a", "annex-a-1"]
        assert chunks[0].section_id != chunks[1].section_id

    def test_renumbering_keeps_section_ids(self):
        _, before = _chunk("# T\n\n## 1 Overview\n\nA.\n\n## 2 Claims\n\nB.")
        _, after = _chunk(
            "# T\n\n## 1 Start\n\nZ.\n\n## 2 Overview\n\nA.\n\n## 3 Claims\n\nB."
        )

        assert [chunk.section_id for chunk in before] == [
            chunk.section_id for chunk in after[1:]
        ]

    def test_chunking_twice_gives_identical_records(self):
        first = _chunk(NESTED)
        second = _chunk(NESTED)

        assert first == second

    def test_chunk_ids_hash_strategy_section_and_part(self):
        _, chunks = _chunk(NESTED)

        assert all(
            chunk.chunk_id == ids.chunk_id("packed-800", chunk.section_id, chunk.part)
            for chunk in chunks
        )


class TestReferences:
    def test_internal_links_resolve_to_a_section_in_this_version(self):
        markdown = (
            "# T\n\n## 1 Overview\n\nSee [claims](#2-claims) and [gone](#9-gone).\n\n"
            "## 2 Claims\n\nText."
        )

        _, sections = _sections(markdown)

        assert sections[0].references == (
            models.Reference(
                kind=models.ReferenceKind.INTERNAL,
                text="claims",
                anchor="2-claims",
                section_id=sections[1].section_id,
            ),
            models.Reference(
                kind=models.ReferenceKind.INTERNAL,
                text="gone",
                anchor="9-gone",
                section_id=None,
            ),
        )

    def test_external_links_keep_a_normalised_url(self):
        _, sections = _sections(
            "# T\n\n## 1 A\n\n[portal](HTTPS://Example.COM:443/a?b=1#c)",
        )

        assert sections[0].references == (
            models.Reference(
                kind=models.ReferenceKind.EXTERNAL,
                text="portal",
                url="https://example.com/a?b=1#c",
            ),
        )

    def test_topics_are_empty(self):
        _, chunks = _chunk(NESTED)

        assert {chunk.topics for chunk in chunks} == {()}


class TestTheSections:
    def test_are_whole_and_hold_every_block(self):
        _, sections = _sections(
            "# T\n\n## 1 Overview\n\nBefore.\n\n| A | B |\n| - | - |\n| 1 | 2 |",
        )

        assert [block.kind for block in sections[0].blocks] == [
            models.BlockKind.PARAGRAPH,
            models.BlockKind.TABLE,
        ]
        assert sections[0].plain_text == "Before.\nA | B\n1 | 2"

    def test_run_through_the_guide_in_order(self):
        _, sections = _sections(NESTED)

        assert [section.ordinal for section in sections] == list(range(7))
