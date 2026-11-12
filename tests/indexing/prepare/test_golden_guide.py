"""The fixture guide becomes exactly the golden sections file and packed chunks.

Run with `UPDATE_GOLDEN=1` to rewrite the golden files after a deliberate
change, then review the diff.
"""

import dataclasses
import json
import os

from app.indexing.prepare import guide_markdown, packed_chunking, sectioning
from app.records import jsonl
from tests.support import guides


def _pipeline():
    document = guide_markdown.GuideMarkdownParser().parse(
        guides.job(),
        guides.GUIDE.read_text(),
    )
    sections = sectioning.build(document)

    return document.header, sections, packed_chunking.PackedChunking().chunk(sections)


def _chunk_lines(chunks):
    return "".join(
        f"{json.dumps(dataclasses.asdict(chunk), ensure_ascii=False, default=str)}\n"
        for chunk in chunks
    )


def _check(path, dumped):
    if os.environ.get("UPDATE_GOLDEN"):
        path.write_text(dumped)

    assert dumped == path.read_text()


class TestTheFixtureGuide:
    def test_becomes_the_golden_sections_file(self):
        header, sections, _ = _pipeline()

        _check(guides.GOLDEN_SECTIONS, jsonl.dumps(header, sections))

    def test_packs_into_the_golden_chunks(self):
        _, _, chunks = _pipeline()

        _check(guides.GOLDEN_CHUNKS, _chunk_lines(chunks))

    def test_golden_sections_pass_the_section_file_checks(self):
        header, sections = jsonl.loads(guides.GOLDEN_SECTIONS.read_text())

        assert header.document_id == guides.DOCUMENT_ID
        assert [section.ordinal for section in sections] == list(range(len(sections)))

    def test_has_a_chunk_for_every_kind_of_content(self):
        _, sections, chunks = _pipeline()
        plain = "\n".join(chunk.plain_text for chunk in chunks)

        assert len(sections) < len(chunks)
        assert {"annex-a", "annex-a-1", "a1-payment-windows"} <= {
            section.anchor for section in sections
        }
        assert all(
            mark not in plain for mark in ("**", "<u>", "<br>", "{.red}", "](", "&amp;")
        )
