"""The ingestion routes validate, queue a guide event, and reply 202."""

import datetime as dt
import uuid

import fastapi.testclient
import pytest

from app import config as app_config
from app.entrypoints import fastapi as entrypoint
from app.ingestion import event_queue
from app.ingestion import models as ingestion_models
from tests.support import guides

PATH = f"/documents/{guides.DOCUMENT_ID}/versions/{guides.VERSION_ID}"
MANAGED_DOCS = app_config.get_config().managed_docs_s3_bucket
CONTENT_URL = guides.job(bucket=MANAGED_DOCS).content_url
TITLE = "Claims Guidance"


def _body(**fields):
    return {
        "sourceType": "guide",
        "status": "published",
        "occurredAt": "2026-11-12T10:00:00Z",
        "contentUrl": CONTENT_URL,
        "metadata": {"guideTitle": TITLE},
        **fields,
    }


class RecordingQueue:
    """Implements `event_queue.IndexingQueue`, keeping what it was sent."""

    def __init__(self, error: Exception | None = None) -> None:
        self.sent: list[ingestion_models.GuideEvent] = []
        self._error = error

    async def send(self, event: ingestion_models.GuideEvent) -> None:
        if self._error is not None:
            raise self._error

        self.sent.append(event)


@pytest.fixture
def queue():
    return RecordingQueue()


def _client(queue, mode=app_config.IngestionMode.HTTP):
    app = entrypoint.create_app(mode)
    app.dependency_overrides[event_queue.get_indexing_queue] = lambda: queue
    return fastapi.testclient.TestClient(app)


@pytest.fixture
def client(queue):
    return _client(queue)


class TestIndex:
    def test_queues_a_published_event_and_replies_202(self, client, queue):
        response = client.put(
            PATH,
            json=_body(versionNumber="1.2", metadata={"guideTitle": f"  {TITLE} "}),
            headers={"x-cdp-request-id": "trace-1"},
        )

        assert response.status_code == 202
        assert response.json() == {
            "documentId": str(guides.DOCUMENT_ID),
            "versionId": str(guides.VERSION_ID),
            "status": "published",
            "operation": "index",
        }
        (event,) = queue.sent
        assert isinstance(event, ingestion_models.GuideVersionPublishedV1)
        assert (event.type, event.source, event.time, event.cdprequestid) == (
            ingestion_models.GUIDE_VERSION_PUBLISHED,
            ingestion_models.INGESTION_SOURCE,
            dt.datetime(2026, 11, 12, 10, tzinfo=dt.UTC),
            "trace-1",
        )
        assert event.subject == (
            f"documents/{guides.DOCUMENT_ID}/versions/{guides.VERSION_ID}"
        )
        assert (event.document_id, event.version_id) == (
            guides.DOCUMENT_ID,
            guides.VERSION_ID,
        )
        assert event.data.content_url == CONTENT_URL
        assert event.data.version_number == "1.2"
        assert event.data.metadata.guide_title == TITLE

    def test_gives_each_event_its_own_id(self, client, queue):
        client.put(PATH, json=_body())
        client.put(PATH, json=_body())

        first, second = queue.sent
        assert first.id != second.id

    def test_refuses_inline_content(self, client, queue):
        response = client.put(PATH, json=_body(content={"markdown": "# Title\n"}))

        assert response.status_code == 422
        assert "inline content is no longer accepted" in response.text
        assert queue.sent == []

    def test_refuses_content_outside_the_managed_docs_bucket(self, client, queue):
        url = guides.job(bucket="someone-elses-bucket").content_url

        response = client.put(PATH, json=_body(contentUrl=url))

        assert response.status_code == 422
        assert MANAGED_DOCS in response.text
        assert queue.sent == []

    @pytest.mark.parametrize(
        "fields",
        [
            pytest.param({"contentUrl": None}, id="no-content-url"),
            pytest.param({"contentUrl": "/a/b"}, id="not-s3"),
            pytest.param({"contentUrl": "s3://bucket-only"}, id="no-key"),
            pytest.param({"occurredAt": "2026-11-12T10:00:00"}, id="naive-time"),
            pytest.param({"status": "draft"}, id="unknown-status"),
            pytest.param({"status": "ready_for_publish"}, id="not-published"),
            pytest.param({"metadata": None}, id="no-metadata"),
            pytest.param({"metadata": {"owner": "Team"}}, id="no-title"),
            pytest.param({"metadata": {"guideTitle": "  "}}, id="blank-title"),
            pytest.param({"sourceType": "govuk"}, id="unknown-source"),
        ],
    )
    def test_rejects_an_invalid_body(self, client, queue, fields):
        response = client.put(PATH, json=_body(**fields))

        assert response.status_code == 422
        assert queue.sent == []

    def test_rejects_ids_that_are_not_the_cms(self, client, queue):
        path = f"/documents/{uuid.uuid4()}/versions/{guides.VERSION_ID}"

        assert client.put(path, json=_body()).status_code == 422
        assert queue.sent == []

    def test_replies_503_when_the_event_cannot_be_queued(self):
        client = _client(RecordingQueue(RuntimeError("SQS is down")))

        response = client.put(PATH, json=_body())

        assert response.status_code == 503


class TestDelete:
    def test_queues_a_deleted_event_and_replies_202(self, client, queue):
        before = dt.datetime.now(dt.UTC)

        response = client.delete(PATH, headers={"x-cdp-request-id": "trace-2"})

        assert response.status_code == 202
        assert response.json() == {
            "documentId": str(guides.DOCUMENT_ID),
            "versionId": str(guides.VERSION_ID),
            "status": None,
            "operation": "delete",
        }
        (event,) = queue.sent
        assert isinstance(event, ingestion_models.GuideVersionDeletedV1)
        assert event.type == ingestion_models.GUIDE_VERSION_DELETED
        assert (event.document_id, event.version_id) == (
            guides.DOCUMENT_ID,
            guides.VERSION_ID,
        )
        assert event.time >= before
        assert event.cdprequestid == "trace-2"

    def test_rejects_ids_that_are_not_the_cms(self, client, queue):
        path = f"/documents/{guides.DOCUMENT_ID}/versions/not-a-uuid"

        assert client.delete(path).status_code == 422
        assert queue.sent == []

    def test_replies_503_when_the_event_cannot_be_queued(self):
        client = _client(RecordingQueue(RuntimeError("SQS is down")))

        assert client.delete(PATH).status_code == 503


class TestS3Mode:
    def test_leaves_out_both_routes(self, queue):
        client = _client(queue, app_config.IngestionMode.S3)

        assert client.put(PATH, json=_body()).status_code == 404
        assert client.delete(PATH).status_code == 404
        assert queue.sent == []
