"""Keyword search: the role's statuses, the page, excerpts and too many matches."""

import dataclasses
import logging

import pytest

from app.records import models
from app.search import keyword_search, routing, service
from tests.fakes import chunk_index as fake_chunk_index
from tests.support import chunks as fixtures

Section = fixtures.Section
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


def _words(text, role=routing.Role.VIEWER, **page):
    return service.WordSearch(text=text, role=role, **page)


class TestSearchingWords:
    async def test_finds_only_what_a_viewer_may_see(self, search):
        results = await search.search_words(_words("livestock"))

        assert [result.hit.title for result in results.results] == ["Claims Guidance"]

    async def test_finds_ready_for_publish_documents_for_a_designer(self, search):
        results = await search.search_words(_words("livestock", routing.Role.DESIGNER))

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

        with pytest.raises(service.TooManyMatchesError) as raised:
            await search.search_words(_words("shared"))

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


def _documents(text, role=routing.Role.VIEWER, **page):
    return service.DocumentSearch(text=text, role=role, **page)


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

        with pytest.raises(service.TooManyMatchesError) as raised:
            await search.search_documents(_documents("livestock"))

        assert raised.value.total == 4

    async def test_never_logs_what_was_typed(self, search, caplog):
        caplog.set_level(logging.INFO)

        await search.search_documents(_documents("livestock"))

        assert "livestock" not in caplog.text.casefold()
        assert "found 4 sections in 2 documents" in caplog.text


class TestSearchingTitles:
    async def test_finds_close_titles_the_role_may_see(self, search):
        results = await search.search_titles(
            service.TitleSearch(text="Clams Guidanse", role=routing.Role.VIEWER),
        )

        assert [hit.title for hit in results.results] == ["Claims Guidance"]
        assert results.query_id
