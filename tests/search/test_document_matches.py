"""Where words are in one document, by their place in a section's text.

The cases are the guidance API's own (`tests/search/contract/test_guide_search.py`
on its RAI-209 branch): the hub places each match by these offsets, so the text
they count in must be the hub's reading of the guide. Guides are read here as
indexing reads them, so a change there that moves an offset fails here.
"""

import dataclasses
import uuid

import pytest

from app.indexing.prepare import guide_markdown, sectioning
from app.records import ids, models
from app.search import document_matches, service
from tests.fakes import chunk_index as fake_chunk_index
from tests.fakes import section_store as fake_section_store
from tests.support import chunks as fixtures
from tests.support import guides

Match = service.Match
SectionMatches = service.SectionMatches
PUBLISHED = frozenset({models.DocumentStatus.PUBLISHED})


@pytest.fixture
def index():
    return fake_chunk_index.InMemoryChunkIndex()


@pytest.fixture
def sections():
    return fake_section_store.InMemorySectionStore()


@pytest.fixture
def finder(index, sections):
    return document_matches.DocumentMatchFinder(index, sections)


@pytest.fixture
def stored(index, sections):
    async def store(
        markdown: str,
        status: models.DocumentStatus = models.DocumentStatus.PUBLISHED,
        version_id: ids.VersionId = guides.VERSION_ID,
    ) -> ids.DocumentId:
        source = guide_markdown.GuideMarkdownParser().parse(
            guides.job(status=status, version_id=version_id),
            markdown,
            title="Claims",
        )
        records = sectioning.build(source)

        await index.replace_document(source.header, fixtures.chunks_of(records))
        await sections.put(source.header, records)

        return source.header.document_id

    return store


async def test_finds_each_match_in_a_section_its_heading_included(stored, finder):
    document_id = await stored("## 1 Paying\n\nRepayment, then payment.")

    found = await finder.matches_in(document_id, "pay", PUBLISHED)

    assert found is not None
    assert found.sections == (
        SectionMatches(
            "1-paying",
            (Match(2, 5, "Pay"), Match(11, 14, "pay"), Match(25, 28, "pay")),
        ),
    )


async def test_answers_for_the_indexed_version(stored, finder):
    document_id = await stored("## 1 Paying\n\nRepayment.")

    found = await finder.matches_in(document_id, "pay", PUBLISHED)

    assert found is not None
    assert found.version_id == guides.VERSION_ID


async def test_answers_from_the_latest_version_only(stored, finder):
    newer = ids.VersionId(uuid.UUID("0199b8a2-0000-7000-8000-00000000f003"))
    await stored("## 1 Paying\n\nRepayment.")
    document_id = await stored("## 1 Paying\n\nNo payment now.", version_id=newer)

    found = await finder.matches_in(document_id, "pay", PUBLISHED)

    assert found is not None
    assert found.version_id == newer
    assert found.sections == (
        SectionMatches("1-paying", (Match(2, 5, "Pay"), Match(12, 15, "pay"))),
    )


async def test_reads_the_words_not_the_marks_and_never_a_links_address(
    stored,
    finder,
):
    document_id = await stored(
        "## Forms\n\nUse the **claim** [form](https://example.com/pay).",
    )

    phrase = await finder.matches_in(document_id, "claim form", PUBLISHED)
    address = await finder.matches_in(document_id, "pay", PUBLISHED)

    assert phrase is not None
    assert phrase.sections == (SectionMatches("forms", (Match(14, 24, "claim form"),)),)
    assert address is not None
    assert address.sections == ()


async def test_a_phrase_runs_over_a_wrapped_line_but_never_into_another_block(
    stored,
    finder,
):
    document_id = await stored(
        "## Notes\n\nPay the claim\nin full.\n\nA claim\n\nin time.",
    )

    found = await finder.matches_in(document_id, "claim in", PUBLISHED)

    assert found is not None
    assert found.sections == (SectionMatches("notes", (Match(14, 22, "claim in"),)),)


async def test_gives_sections_in_order_the_introduction_without_an_anchor(
    stored,
    finder,
):
    document_id = await stored(
        "# Claims\n\nPay first.\n\n## 1 Notes\n\nNone here.\n\n## 2 Late\n\nPay late.",
    )

    found = await finder.matches_in(document_id, "pay", PUBLISHED)

    assert found is not None
    assert found.sections == (
        SectionMatches(None, (Match(0, 3, "Pay"),)),
        SectionMatches("2-late", (Match(7, 10, "Pay"),)),
    )


async def test_reads_each_table_cell_as_a_line_of_its_own(stored, finder):
    document_id = await stored(
        "## Rates\n\n| Item | Pay |\n|------|-----|\n| Late | 5 |",
    )

    cell = await finder.matches_in(document_id, "pay", PUBLISHED)
    across = await finder.matches_in(document_id, "item pay", PUBLISHED)

    assert cell is not None
    assert cell.sections == (SectionMatches("rates", (Match(11, 14, "Pay"),)),)
    assert across is not None
    assert across.sections == ()


async def test_places_a_match_after_a_list_in_a_table_cell_as_the_page_reads_it(
    stored, finder
):
    document_id = await stored(
        "## Rates\n\n| Item | Pay |\n|------|-----|\n| Late | - Apply<br>- Record |"
        "\n\nPay now.",
    )

    found = await finder.matches_in(document_id, "now", PUBLISHED)

    page = "Rates\nItem\nPay\nLate\nApply\nRecord\nPay now."
    start = page.index("now")
    assert found is not None
    assert found.sections == (
        SectionMatches("rates", (Match(start, start + 3, "now"),)),
    )


async def test_keeps_an_escaped_pipe_within_its_table_cell(stored, finder):
    document_id = await stored(
        "## Rates\n\n| Item | Pay |\n|------|-----|\n| A \\| B | Pay |",
    )

    phrase = await finder.matches_in(document_id, "a | b", PUBLISHED)
    cell = await finder.matches_in(document_id, "pay", PUBLISHED)

    # Read as the page reads it: "Rates\nItem\nPay\nA | B\nPay".
    assert phrase is not None
    assert phrase.sections == (SectionMatches("rates", (Match(15, 20, "A | B"),)),)
    assert cell is not None
    assert cell.sections == (
        SectionMatches("rates", (Match(11, 14, "Pay"), Match(21, 24, "Pay"))),
    )


async def test_a_phrase_runs_over_a_hard_break_as_the_page_shows_it(
    stored,
    finder,
):
    document_id = await stored("## Notes\n\nLine one\\\nline two\n\n- First\\\n  item")

    paragraph = await finder.matches_in(document_id, "one line", PUBLISHED)
    item = await finder.matches_in(document_id, "first item", PUBLISHED)

    # Read as "Notes\nLine one line two\nFirst item".
    assert paragraph is not None
    assert paragraph.sections == (
        SectionMatches("notes", (Match(11, 19, "one line"),)),
    )
    assert item is not None
    assert item.sections == (SectionMatches("notes", (Match(24, 34, "First item"),)),)


async def test_finds_nothing_for_no_words(stored, finder):
    document_id = await stored("## 1 Paying\n\nRepayment.")

    found = await finder.matches_in(document_id, "  ", PUBLISHED)

    assert found is not None
    assert found.sections == ()


async def test_answers_none_for_a_document_not_indexed(finder):
    unknown = ids.DocumentId(uuid.UUID("0199b8a2-0000-7000-8000-0000000000ff"))

    assert await finder.matches_in(unknown, "pay", PUBLISHED) is None


async def test_answers_none_when_the_indexed_versions_sections_are_missing(
    stored,
    sections,
    finder,
):
    document_id = await stored("## 1 Paying\n\nRepayment.")
    await sections.delete(document_id, guides.VERSION_ID)

    assert await finder.matches_in(document_id, "pay", PUBLISHED) is None


async def test_takes_the_status_from_the_index_not_the_stored_sections(
    index,
    sections,
    finder,
):
    # The same version prepared as published: its sections are stored before
    # the index changes, so for a while they are ahead of it.
    source = guide_markdown.GuideMarkdownParser().parse(
        guides.job(status=models.DocumentStatus.READY_FOR_PUBLISH),
        "## 1 Paying\n\nRepayment.",
        title="Claims",
    )
    records = sectioning.build(source)
    published = dataclasses.replace(
        source.header, status=models.DocumentStatus.PUBLISHED
    )
    await index.replace_document(source.header, fixtures.chunks_of(records))
    await sections.put(published, records)

    assert await finder.matches_in(published.document_id, "pay", PUBLISHED) is None


async def test_answers_none_for_a_document_whose_indexed_version_is_not_published(
    stored,
    finder,
):
    document_id = await stored(
        "## 1 Paying\n\nRepayment.",
        models.DocumentStatus.READY_FOR_PUBLISH,
    )

    assert await finder.matches_in(document_id, "pay", PUBLISHED) is None
