"""Keyword search: the statuses asked for, the page, excerpts and too many matches."""

import dataclasses
import logging

import pytest

from app.records import models
from app.search import keyword_search, service
from tests.fakes import chunk_index as fake_chunk_index
from tests.support import chunks as fixtures

Section = fixtures.Section
PUBLISHED_ONLY = frozenset({models.DocumentStatus.PUBLISHED})
PUBLISHED = fixtures.document(
    [
        Section("1 Overview", 1, ("Claim for livestock.",)),
        Section("2 Eligibility", 1, ("Hold a holding number.",)),
    ],
)
DRAFT = fixtures.document(
    [Section("1 Woodland", 1, ("Felling licences for livestock areas.",))],
    fixtures.header(
        document_id=fixtures.DOCUMENT_ID.__class__(
            "0199b8a2-0000-7000-8000-0000000000aa"
        ),
        status=models.DocumentStatus.READY_FOR_PUBLISH,
        title="Woodland Guidance",
    ),
)


@pytest.fixture
async def index():
    built = fake_chunk_index.InMemoryChunkIndex()
    await built.replace_document(*PUBLISHED)
    await built.replace_document(*DRAFT)
    return built


@pytest.fixture
def search(index):
    return keyword_search.KeywordSearch(index)


def _words(text, statuses=PUBLISHED_ONLY, **page):
    return service.WordSearch(text=text, statuses=statuses, **page)


class TestSearchingWords:
    async def test_finds_only_the_statuses_asked_for(self, search):
        results = await search.search_words(_words("livestock"))

        assert [result.hit.title for result in results.results] == ["Claims Guidance"]

    async def test_finds_ready_for_publish_documents_when_asked(self, search):
        results = await search.search_words(
            _words("livestock", frozenset(models.DocumentStatus))
        )

        assert {result.hit.title for result in results.results} == {
            "Claims Guidance",
            "Woodland Guidance",
        }

    async def test_gives_each_hit_its_excerpt(self, search):
        (result,) = (await search.search_words(_words("holding"))).results

        assert result.excerpt == service.Excerpt(
            text="Hold a holding number.",
            matches=((7, 14),),
        )

    async def test_pages_and_counts_every_match(self, search, index):
        header, chunks = PUBLISHED
        await index.replace_document(
            header,
            [dataclasses.replace(chunk, plain_text="shared word") for chunk in chunks],
        )

        results = await search.search_words(_words("shared", limit=1, offset=1))

        assert (results.total, len(results.results)) == (2, 1)

    async def test_gives_each_search_its_own_id(self, search):
        first = await search.search_words(_words("holding"))
        second = await search.search_words(_words("holding"))

        assert first.query_id != second.query_id

    async def test_refuses_more_than_the_most_matches(self, mocker, search, index):
        mocker.patch.object(service, "MAX_MATCHES", 1)
        header, chunks = PUBLISHED
        await index.replace_document(
            header,
            [dataclasses.replace(chunk, plain_text="shared word") for chunk in chunks],
        )

        request = _words("shared")

        with pytest.raises(service.TooManyMatchesError) as raised:
            await search.search_words(request)

        assert raised.value.total == 2

    async def test_never_logs_what_was_typed(self, search, caplog):
        caplog.set_level(logging.INFO)

        await search.search_words(_words("holding"))

        assert "holding" not in caplog.text
        assert "found 1 sections" in caplog.text


OTHER_ID = fixtures.DOCUMENT_ID.__class__("0199b8a2-0000-7000-8000-0000000000bb")
OTHER = fixtures.document(
    [
        Section("1 Fees", 1, ("Livestock fees are due.",)),
        Section("2 Refunds", 1, ("Livestock refunds take a month.",)),
        Section("3 Appeals", 1, ("Livestock appeals go to a panel.",)),
    ],
    fixtures.header(document_id=OTHER_ID, title="Fees Guidance"),
)


def _documents(text, statuses=PUBLISHED_ONLY, **page):
    return service.DocumentSearch(text=text, statuses=statuses, **page)


class TestSearchingDocuments:
    @pytest.fixture
    async def index(self, index):
        await index.replace_document(*OTHER)
        return index

    async def test_groups_each_documents_sections(self, search):
        results = await search.search_documents(_documents("livestock"))

        assert (results.total, results.section_total) == (2, 4)
        assert {
            result.sections[0].hit.title: result.section_total
            for result in results.results
        } == {"Claims Guidance": 1, "Fees Guidance": 3}

    async def test_ranks_documents_by_their_best_section(self, search, index):
        header, chunks = OTHER
        await index.replace_document(
            header,
            [
                dataclasses.replace(chunk, plain_text="livestock livestock livestock")
                if index_ == 0
                else chunk
                for index_, chunk in enumerate(chunks)
            ],
        )

        results = await search.search_documents(_documents("livestock"))

        assert [r.sections[0].hit.title for r in results.results] == [
            "Fees Guidance",
            "Claims Guidance",
        ]

    async def test_gives_at_most_the_sections_asked_for(self, search):
        results = await search.search_documents(_documents("livestock", sections=2))

        (fees,) = [
            r for r in results.results if r.sections[0].hit.title == "Fees Guidance"
        ]
        assert (len(fees.sections), fees.section_total) == (2, 3)
        assert all(section.excerpt.matches for section in fees.sections)

    async def test_pages_documents_not_sections(self, search):
        first = await search.search_documents(_documents("livestock", limit=1))
        second = await search.search_documents(
            _documents("livestock", limit=1, offset=1),
        )

        assert (first.total, len(first.results), len(second.results)) == (2, 1, 1)
        assert first.results[0].sections[0].hit.document_id != (
            second.results[0].sections[0].hit.document_id
        )

    async def test_searches_inside_one_document(self, search):
        results = await search.search_documents(
            _documents("livestock", tag=OTHER_ID),
        )

        assert (results.total, results.section_total) == (1, 3)

    async def test_refuses_more_than_the_most_matches(self, mocker, search):
        mocker.patch.object(service, "MAX_MATCHES", 3)

        request = _documents("livestock")

        with pytest.raises(service.TooManyMatchesError) as raised:
            await search.search_documents(request)

        assert raised.value.total == 4

    async def test_never_logs_what_was_typed(self, search, caplog):
        caplog.set_level(logging.INFO)

        await search.search_documents(_documents("livestock"))

        assert "livestock" not in caplog.text.casefold()
        assert "found 4 sections in 2 documents and 0 titles" in caplog.text

    async def test_gives_the_one_title_covered_apart_from_the_pages(
        self, search, index
    ):
        header, chunks = PUBLISHED
        await index.replace_document(
            header,
            [dataclasses.replace(chunks[0], plain_text="Read the claims guidance.")],
        )
        other_header, other_chunks = OTHER
        await index.replace_document(
            other_header,
            [dataclasses.replace(other_chunks[0], plain_text="See claims guidance.")],
        )

        results = await search.search_documents(_documents("claims guidance"))

        assert [r.title for r in results.results] == ["Fees Guidance"]
        assert (results.total, results.document_total, results.section_total) == (
            1,
            2,
            2,
        )
        (title,) = results.titles
        assert (title.title, title.section_total) == ("Claims Guidance", 1)
        assert title.title_match is not None
        assert title.title_match.matches == ((0, 15),)

    async def test_gives_a_covered_title_with_no_sections(self, search):
        results = await search.search_documents(_documents("fees guidance"))

        assert (results.total, results.document_total, results.results) == (0, 1, ())
        (title,) = results.titles
        assert (title.title, title.sections, title.section_total) == (
            "Fees Guidance",
            (),
            0,
        )

    async def test_keeps_several_covered_titles_in_the_pages(self, search, index):
        await index.replace_document(
            *fixtures.document(
                [Section("1 Overview", 1, ("Claims guidance annex.",))],
                fixtures.header(
                    document_id=OTHER_ID.__class__(
                        "0199b8a2-0000-7000-8000-0000000000cc"
                    ),
                    title="Claims Guidance Annex",
                ),
            ),
        )

        results = await search.search_documents(_documents("claims guidance"))

        assert [title.title for title in results.titles] == [
            "Claims Guidance",
            "Claims Guidance Annex",
        ]
        assert [(r.title, r.title_match is not None) for r in results.results] == [
            ("Claims Guidance Annex", True),
        ]
        assert (results.total, results.document_total) == (1, 2)

    async def test_ranks_by_sections_alone_when_no_title_is_covered(self, search):
        results = await search.search_documents(_documents("livestock"))

        assert results.titles == ()
        assert all(result.title_match is None for result in results.results)
        assert results.document_total == 2


class TestSearchingTitles:
    async def test_finds_close_titles_of_the_statuses_asked_for(self, search):
        results = await search.search_titles(
            service.TitleSearch(text="Clams Guidanse", statuses=PUBLISHED_ONLY),
        )

        assert [hit.title for hit in results.results] == ["Claims Guidance"]
        assert results.query_id
