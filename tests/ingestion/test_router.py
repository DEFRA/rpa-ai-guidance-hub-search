"""The ingestion routes validate, reply 202, and leave the work to the service."""

import datetime as dt
import uuid

import fastapi.testclient
import pytest

from app import config as app_config
from app.entrypoints import fastapi as entrypoint
from app.indexing import jobs
from app.indexing.prepare import guide_content, guide_source
from app.ingestion import service
from app.records import models, source_metadata
from tests.support import guides

PATH = f"/documents/{guides.DOCUMENT_ID}/versions/{guides.VERSION_ID}"
MARKDOWN = "# Markdown Title\n\n## 1 Overview\n\nHold a holding number.\n"
CONTENT_URL = guides.job().content_url
TITLE = "Claims Guidance"


def _body(**fields):
    return {
        "sourceType": "guide",
        "status": "published",
        "occurredAt": "2026-11-12T10:00:00Z",
        "content": {"markdown": MARKDOWN},
        "metadata": {"guideTitle": TITLE},
        **fields,
    }


@pytest.fixture
def ingestion(mocker):
    return mocker.create_autospec(service.IngestionService, instance=True)


@pytest.fixture
def client(ingestion):
    app = entrypoint.create_app(app_config.IngestionMode.HTTP)
    app.dependency_overrides[service.get_service] = lambda: ingestion
    return fastapi.testclient.TestClient(app)


def _indexed(ingestion):
    ingestion.index.assert_awaited_once()
    job, reader, trace_id = ingestion.index.await_args.args
    return job, reader, trace_id


class TestIndex:
    def test_accepts_inline_content(self, client, ingestion):
        response = client.put(PATH, json=_body())

        assert response.status_code == 202
        assert response.json() == {
            "documentId": str(guides.DOCUMENT_ID),
            "versionId": str(guides.VERSION_ID),
            "status": "published",
            "operation": "index",
        }
        job, reader, _ = _indexed(ingestion)
        assert job == jobs.IndexJob(
            source_type=models.SourceType.GUIDE,
            document_id=guides.DOCUMENT_ID,
            version_id=guides.VERSION_ID,
            status=models.DocumentStatus.PUBLISHED,
            content_url=(
                f"cms://documents/{guides.DOCUMENT_ID}/versions/{guides.VERSION_ID}"
            ),
            occurred_at=dt.datetime(2026, 11, 12, 10, tzinfo=dt.UTC),
        )
        assert isinstance(reader, guide_source.GuideSource)

    def test_accepts_a_content_url(self, client, ingestion, mocker):
        mocker.patch("app.common.s3.get_s3_client")

        response = client.put(PATH, json=_body(content=None, contentUrl=CONTENT_URL))

        assert response.status_code == 202
        job, reader, _ = _indexed(ingestion)
        assert job.content_url == CONTENT_URL
        assert isinstance(reader._content_source, guide_content.S3ContentSource)

    async def test_hands_the_reader_the_content_title_and_metadata(
        self, client, ingestion
    ):
        client.put(
            PATH,
            json=_body(
                content={
                    "markdown": MARKDOWN,
                    "sectionIds": {"1-overview": "section-1"},
                },
                versionNumber="1.2",
                metadata={
                    "guideTitle": f"  {TITLE} ",
                    "schemes": ["SFI"],
                    "guidanceType": "Process guide",
                },
            ),
        )
        job, reader, _ = _indexed(ingestion)

        document = await reader.read(job)

        assert document.header.title == TITLE
        assert document.header.source == source_metadata.GuideMetadata(
            version_number="1.2",
            schemes=("SFI",),
            guidance_types=("Process guide",),
        )
        assert document.sections[-1].section_id == "section-1"

    def test_passes_on_the_trace_id(self, client, ingestion):
        client.put(PATH, json=_body(), headers={"x-cdp-request-id": "trace-1"})

        _, _, trace_id = _indexed(ingestion)
        assert trace_id == "trace-1"

    @pytest.mark.parametrize(
        "fields",
        [
            pytest.param({"contentUrl": CONTENT_URL}, id="both"),
            pytest.param({"content": None}, id="neither"),
            pytest.param({"content": None, "contentUrl": "/a/b"}, id="not-s3"),
            pytest.param({"content": {"markdown": ""}}, id="empty-markdown"),
            pytest.param({"occurredAt": "2026-11-12T10:00:00"}, id="naive-time"),
            pytest.param({"status": "draft"}, id="unknown-status"),
            pytest.param({"status": "ready_for_publish"}, id="not-published"),
            pytest.param({"metadata": None}, id="no-metadata"),
            pytest.param({"metadata": {"owner": "Team"}}, id="no-title"),
            pytest.param({"metadata": {"guideTitle": "  "}}, id="blank-title"),
            pytest.param({"sourceType": "govuk"}, id="unknown-source"),
            pytest.param(
                {"content": {"markdown": MARKDOWN, "sectionIds": {"a": ""}}},
                id="empty-section-id",
            ),
        ],
    )
    def test_rejects_an_invalid_body(self, client, ingestion, fields):
        response = client.put(PATH, json=_body(**fields))

        assert response.status_code == 422
        ingestion.index.assert_not_awaited()

    def test_rejects_ids_that_are_not_the_cms(self, client, ingestion):
        path = f"/documents/{uuid.uuid4()}/versions/{guides.VERSION_ID}"

        assert client.put(path, json=_body()).status_code == 422
        ingestion.index.assert_not_awaited()

    def test_rejects_inline_content_over_the_limit(self, client, ingestion, mocker):
        small = app_config.get_config().model_copy(
            update={"max_inline_content_bytes": len(MARKDOWN) - 1}
        )
        mocker.patch("app.config.get_config", return_value=small)

        response = client.put(PATH, json=_body())

        assert response.status_code == 413
        ingestion.index.assert_not_awaited()

    def test_accepts_inline_content_at_the_limit(self, client, mocker):
        exact = app_config.get_config().model_copy(
            update={"max_inline_content_bytes": len(MARKDOWN.encode())}
        )
        mocker.patch("app.config.get_config", return_value=exact)

        assert client.put(PATH, json=_body()).status_code == 202


class TestDelete:
    def test_accepts_and_removes_every_status(self, client, ingestion):
        response = client.delete(PATH, headers={"x-cdp-request-id": "trace-2"})

        assert response.status_code == 202
        assert response.json() == {
            "documentId": str(guides.DOCUMENT_ID),
            "versionId": str(guides.VERSION_ID),
            "status": None,
            "operation": "delete",
        }
        ingestion.delete.assert_awaited_once()
        job, trace_id = ingestion.delete.await_args.args
        assert (job.document_id, job.version_id) == (
            guides.DOCUMENT_ID,
            guides.VERSION_ID,
        )
        assert job.occurred_at.utcoffset() is not None
        assert trace_id == "trace-2"

    def test_rejects_ids_that_are_not_the_cms(self, client, ingestion):
        path = f"/documents/{guides.DOCUMENT_ID}/versions/not-a-uuid"

        assert client.delete(path).status_code == 422
        ingestion.delete.assert_not_awaited()


class TestS3Mode:
    def test_leaves_out_both_routes(self, ingestion):
        app = entrypoint.create_app(app_config.IngestionMode.S3)
        app.dependency_overrides[service.get_service] = lambda: ingestion
        client = fastapi.testclient.TestClient(app)

        assert client.put(PATH, json=_body()).status_code == 404
        assert client.delete(PATH).status_code == 404
