"""`GET /v1/search` over HTTP: the spike's routes and its result JSON."""

import logging
import uuid

import fastapi.testclient
import pytest

import app.entrypoints.fastapi
from app.common import log_utils
from app.index import active_index
from app.records import models
from app.search import document_matches, service
from app.search.v1 import router
from tests.fakes import chunk_index as fake_chunk_index
from tests.fakes import section_store as fake_section_store
from tests.support import chunks as fixtures

Section = fixtures.Section
CLAIMS = fixtures.document(
    [
        Section("1 Overview", 1, ("Check the land use first.",)),
        Section("1.1 Scope", 2, ("Who it is for.",)),
    ],
)
DRAFT_ID = uuid.UUID("0199b8a2-0000-7000-8000-0000000000aa")
DRAFT = fixtures.document(
    [Section("1 Land", 1, ("Draft land rules.",))],
    fixtures.header(
        document_id=DRAFT_ID,
        status=models.DocumentStatus.READY_FOR_PUBLISH,
        title="Woodland Guidance",
    ),
)


@pytest.fixture
async def index():
    built = fake_chunk_index.InMemoryChunkIndex()
    await built.replace_document(*CLAIMS)
    await built.replace_document(*DRAFT)
    return built


@pytest.fixture
def client(index):
    application = app.entrypoints.fastapi.create_app()
    application.dependency_overrides[active_index.get_chunk_index] = lambda: index
    return fastapi.testclient.TestClient(application)


class TestWordSearch:
    def test_answers_with_the_spikes_result_fields(self, client):
        header, chunks = CLAIMS

        response = client.get("/v1/search", params={"q": "land use"})

        assert response.status_code == 200
        body = response.json()
        assert (body["total"], body["tooManyMatches"], body["message"]) == (
            1,
            False,
            None,
        )
        assert body["queryId"]
        assert body["results"] == [
            {
                "id": chunks[0].section_id,
                "documentId": str(header.document_id),
                "documentTitle": "Claims Guidance",
                "versionId": str(header.version_id),
                "status": "published",
                "sectionNumber": "1",
                "heading": "Overview",
                "headingPath": ["Overview"],
                "anchor": "1-overview",
                "url": f"/guides/{header.document_id}#1-overview",
                "exact": True,
                "matches": 1,
                "snippet": [
                    {"text": "Check the ", "match": False},
                    {"text": "land use", "match": True},
                    {"text": " first.", "match": False},
                ],
            },
        ]

    def test_finds_only_published_documents(self, client):
        response = client.get("/v1/search", params={"q": "land"})

        assert {hit["documentTitle"] for hit in response.json()["results"]} == {
            "Claims Guidance",
        }

    def test_searches_inside_one_document(self, client):
        response = client.get(
            "/v1/search",
            params={"q": "who", "tag": str(fixtures.DOCUMENT_ID)},
        )

        assert [hit["heading"] for hit in response.json()["results"]] == ["Scope"]

    def test_pages_results(self, client):
        response = client.get("/v1/search", params={"q": "o", "limit": 1})

        assert len(response.json()["results"]) <= 1

    def test_says_when_there_are_too_many_matches(self, client, mocker):
        mocker.patch.object(service, "MAX_MATCHES", 0)

        response = client.get("/v1/search", params={"q": "land"})

        assert response.status_code == 200
        assert response.json() | {"queryId": "x"} == {
            "queryId": "x",
            "total": 1,
            "results": [],
            "tooManyMatches": True,
            "message": "Too many matches. Be more specific.",
        }


class TestDocumentSearch:
    def test_answers_with_documents_and_their_sections(self, client):
        header, chunks = CLAIMS

        response = client.get(
            "/v1/search",
            params={"q": "ver", "group": "document", "sections": 1},
        )

        assert response.status_code == 200
        body = response.json()
        assert (body["total"], body["sectionTotal"], body["tooManyMatches"]) == (
            1,
            2,
            False,
        )
        (document,) = body["results"]
        assert {key: value for key, value in document.items() if key != "sections"} == {
            "documentId": str(header.document_id),
            "documentTitle": "Claims Guidance",
            "versionId": str(header.version_id),
            "status": "published",
            "url": f"/guides/{header.document_id}",
            "titleMatch": None,
            "sectionTotal": 2,
        }
        (section,) = document["sections"]
        assert section["id"] in {chunk.section_id for chunk in chunks}
        assert section["snippet"]

    def test_searches_inside_one_document(self, client):
        response = client.get(
            "/v1/search",
            params={"q": "who", "group": "document", "tag": str(fixtures.DOCUMENT_ID)},
        )

        (document,) = response.json()["results"]
        assert [section["heading"] for section in document["sections"]] == ["Scope"]

    def test_gives_the_title_the_words_cover_apart(self, client):
        response = client.get(
            "/v1/search",
            params={"q": "claims guidance", "group": "document"},
        )

        body = response.json()
        assert (body["total"], body["documentTotal"], body["results"]) == (0, 1, [])
        (title,) = body["titles"]
        assert (title["documentTitle"], title["sections"]) == ("Claims Guidance", [])
        assert title["titleMatch"] == {
            "snippet": [{"text": "Claims Guidance", "match": True}],
        }

    def test_gives_no_titles_for_part_of_a_title(self, client):
        response = client.get(
            "/v1/search",
            params={"q": "guidance", "group": "document"},
        )

        assert response.json()["titles"] == []

    def test_says_when_there_are_too_many_matches(self, client, mocker):
        mocker.patch.object(service, "MAX_MATCHES", 0)

        response = client.get("/v1/search", params={"q": "land", "group": "document"})

        assert response.status_code == 200
        assert response.json() | {"queryId": "x"} == {
            "queryId": "x",
            "total": 0,
            "documentTotal": 0,
            "sectionTotal": 1,
            "results": [],
            "titles": [],
            "tooManyMatches": True,
            "message": "Too many matches. Be more specific.",
        }


class TestTitleSearch:
    def test_finds_a_title_despite_typos(self, client):
        response = client.get("/v1/search", params={"title": "Clams Guidanse"})

        assert response.status_code == 200
        (hit,) = response.json()["results"]
        assert (
            hit["documentId"],
            hit["documentTitle"],
            hit["status"],
            hit["url"],
        ) == (
            str(fixtures.DOCUMENT_ID),
            "Claims Guidance",
            "published",
            f"/guides/{fixtures.DOCUMENT_ID}",
        )
        assert 0 < hit["score"] <= 1


class TestBadRequests:
    @pytest.mark.parametrize(
        "params",
        [
            pytest.param({}, id="neither"),
            pytest.param({"q": "land", "title": "Claims"}, id="both"),
            pytest.param({"q": "land", "limit": 0}, id="no-limit"),
            pytest.param({"q": "land", "limit": 1001}, id="over-the-limit"),
            pytest.param({"q": "land", "offset": -1}, id="negative-offset"),
            pytest.param({"q": "land", "tag": "not-a-uuid"}, id="bad-tag"),
            pytest.param({"q": "x" * 501}, id="too-long"),
            pytest.param({"q": "land", "group": "section"}, id="unknown-group"),
            pytest.param({"title": "Claims", "group": "document"}, id="group-titles"),
            pytest.param({"q": "land", "group": "document", "sections": 0}, id="none"),
            pytest.param(
                {"q": "land", "group": "document", "sections": 21},
                id="too-many-sections",
            ),
        ],
    )
    def test_are_refused(self, client, params):
        assert client.get("/v1/search", params=params).status_code == 422


class TestTheRequestLogs:
    """What a person typed never reaches the logs, even in the request's URL."""

    @pytest.mark.parametrize(
        "params",
        [
            pytest.param({"q": "Jo Bloggs land"}, id="words"),
            pytest.param({"q": "Jo Bloggs land", "group": "document"}, id="documents"),
            pytest.param({"title": "Jo Bloggs Claims"}, id="titles"),
        ],
    )
    def test_never_hold_the_search_text(self, client, caplog, params):
        caplog.set_level(logging.INFO)
        caplog.handler.addFilter(log_utils.ExtraFieldsFilter())

        assert client.get("/v1/search", params=params).status_code == 200

        search_records = [r for r in caplog.records if r.name.startswith("app.search")]
        assert search_records
        assert all("[redacted]" in record.url["full"] for record in search_records)
        # The test client's own httpx log line is the caller's, not the service's.
        service_records = [r for r in caplog.records if not r.name.startswith("httpx")]
        assert not any(
            "Bloggs" in record.getMessage() or "Bloggs" in str(vars(record))
            for record in service_records
        )


class TestTheUnversionedPath:
    def test_is_not_served(self, client):
        assert client.get("/search", params={"q": "land"}).status_code == 404


class TestTheServiceDependency:
    def test_searches_the_loaded_index(self, index):
        assert router.get_search_service(index)._index is index


class TestDocumentMatches:
    """`GET /v1/documents/{documentId}/matches`: every place the words are."""

    @pytest.fixture
    async def sections(self):
        store = fake_section_store.InMemorySectionStore()
        header, records = fixtures.sections(
            [Section("1 Overview", 1, ("Check the land use first.",))],
        )
        await store.put(header, records)
        draft_header, draft_records = fixtures.sections(
            [Section("1 Land", 1, ("Draft land rules.",))],
            DRAFT[0],
        )
        await store.put(draft_header, draft_records)
        return store

    @pytest.fixture
    def client(self, index, sections):
        application = app.entrypoints.fastapi.create_app()
        application.dependency_overrides[router.get_match_finder] = lambda: (
            document_matches.DocumentMatchFinder(index, sections)
        )
        return fastapi.testclient.TestClient(application)

    def test_answers_each_match_by_its_place_in_the_sections_text(self, client):
        header, _ = CLAIMS

        response = client.get(
            f"/v1/documents/{header.document_id}/matches",
            params={"q": "land use"},
        )

        assert response.status_code == 200
        assert response.json() == {
            "versionId": str(header.version_id),
            "sections": [
                {
                    "anchor": "1-overview",
                    "matches": [{"start": 21, "end": 29, "text": "land use"}],
                },
            ],
        }

    def test_answers_no_sections_when_nothing_matches(self, client):
        header, _ = CLAIMS

        response = client.get(
            f"/v1/documents/{header.document_id}/matches",
            params={"q": "woodland"},
        )

        assert response.status_code == 200
        assert response.json()["sections"] == []

    @pytest.mark.parametrize(
        "document_id",
        [
            pytest.param(DRAFT_ID, id="not published"),
            pytest.param(
                uuid.UUID("0199b8a2-0000-7000-8000-0000000000ff"), id="unknown"
            ),
        ],
    )
    def test_is_not_found_without_a_published_version(self, client, document_id):
        response = client.get(
            f"/v1/documents/{document_id}/matches",
            params={"q": "land"},
        )

        assert response.status_code == 404

    @pytest.mark.parametrize(
        ("path", "params"),
        [
            pytest.param(
                f"/v1/documents/{CLAIMS[0].document_id}/matches", {}, id="no q"
            ),
            pytest.param(
                f"/v1/documents/{CLAIMS[0].document_id}/matches",
                {"q": "x" * 501},
                id="q too long",
            ),
            pytest.param(
                "/v1/documents/not-a-uuid/matches", {"q": "land"}, id="bad id"
            ),
        ],
    )
    def test_refuses_a_bad_request(self, client, path, params):
        assert client.get(path, params=params).status_code == 422

    def test_never_logs_the_search_text(self, client, caplog):
        caplog.set_level(logging.INFO)
        caplog.handler.addFilter(log_utils.ExtraFieldsFilter())

        response = client.get(
            f"/v1/documents/{CLAIMS[0].document_id}/matches",
            params={"q": "Jo Bloggs land"},
        )

        assert response.status_code == 200
        service_records = [r for r in caplog.records if not r.name.startswith("httpx")]
        assert not any(
            "Bloggs" in record.getMessage() or "Bloggs" in str(vars(record))
            for record in service_records
        )
