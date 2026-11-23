"""The preparation worker prepares published guide versions and forwards every job."""

import boto3
import moto
import pytest
from botocore import exceptions as botocore_exceptions

from app.common import cloudevents
from app.indexing import jobs
from app.indexing.prepare import document_preparer, packed_chunking
from app.ingestion import preparation
from tests.fakes import chunk_set_store as fake_chunk_set_store
from tests.fakes import section_store as fake_section_store
from tests.support import events, guides

BUCKET = guides.BUCKET


class RecordingDispatcher:
    """Implements `dispatch.ApplyDispatcher`, keeping each job."""

    def __init__(self) -> None:
        self.jobs: list[jobs.WriteJob] = []

    async def dispatch(self, job: jobs.WriteJob) -> None:
        self.jobs.append(job)


@pytest.fixture
def s3_client():
    with moto.mock_aws():
        client = boto3.client("s3", region_name="eu-west-2")
        client.create_bucket(
            Bucket=BUCKET,
            CreateBucketConfiguration={"LocationConstraint": "eu-west-2"},
        )
        yield client


@pytest.fixture
def stores():
    return (
        fake_section_store.InMemorySectionStore(),
        fake_chunk_set_store.InMemoryChunkSetStore(),
    )


@pytest.fixture
def dispatcher():
    return RecordingDispatcher()


@pytest.fixture
def worker(s3_client, stores, dispatcher):
    sections, staged = stores

    def preparer_for(readers):
        return document_preparer.DocumentPreparer(
            readers, sections, packed_chunking.PackedChunking(), staged
        )

    return preparation.PreparationWorker(
        preparer_for=preparer_for,
        dispatcher=dispatcher,
        client=s3_client,
        content_bucket=BUCKET,
    )


def _upload(s3_client):
    s3_client.put_object(
        Bucket=BUCKET, Key=guides.content_key(), Body=guides.GUIDE.read_bytes()
    )


class TestPublished:
    async def test_stages_the_chunks_and_forwards_the_apply_job(
        self, worker, s3_client, stores, dispatcher
    ):
        _upload(s3_client)
        sections, staged = stores

        await worker.handle(events.published())

        (job,) = dispatcher.jobs
        assert isinstance(job, jobs.ApplyJob)
        assert (job.document_id, job.version_id, job.occurred_at) == (
            guides.DOCUMENT_ID,
            guides.VERSION_ID,
            events.TIME,
        )
        chunk_set = await staged.get(job.chunk_set_key)
        assert chunk_set is not None
        assert chunk_set.header.title == events.TITLE
        assert job.chunk_set_key == chunk_set.key
        assert await sections.get(guides.DOCUMENT_ID, guides.VERSION_ID) is not None

    async def test_raises_and_forwards_nothing_when_the_content_is_missing(
        self, worker, dispatcher, caplog
    ):
        with pytest.raises(botocore_exceptions.ClientError):
            await worker.handle(events.published())

        assert dispatcher.jobs == []
        assert "is missing" in caplog.text

    async def test_raises_when_the_content_may_not_be_read(
        self, mocker, worker, s3_client, dispatcher, caplog
    ):
        denied = botocore_exceptions.ClientError(
            {"Error": {"Code": "AccessDenied"}}, "GetObject"
        )
        mocker.patch.object(s3_client, "get_object", side_effect=denied)

        with pytest.raises(botocore_exceptions.ClientError):
            await worker.handle(events.published())

        assert dispatcher.jobs == []
        assert "may not be read" in caplog.text

    async def test_refuses_content_in_another_bucket(self, worker, dispatcher):
        event = events.published(bucket="someone-elses-bucket")

        with pytest.raises(preparation.ContentBucketNotAllowedError):
            await worker.handle(event)

        assert dispatcher.jobs == []

    async def test_preparing_again_stages_the_same_chunk_set(
        self, worker, s3_client, dispatcher
    ):
        _upload(s3_client)

        await worker.handle(events.published())
        await worker.handle(events.published())

        first, second = dispatcher.jobs
        assert first == second


class TestDeleted:
    async def test_forwards_the_remove_job(self, worker, dispatcher):
        await worker.handle(events.deleted())

        assert dispatcher.jobs == [events.deleted().to_job()]


class TestUnknown:
    async def test_acknowledges_an_event_it_does_not_read(self, worker, dispatcher):
        event = cloudevents.UnknownEvent(
            specversion="1.0",
            id="e-1",
            source="/elsewhere",
            type="uk.gov.defra.rpa.guidancehub.guide.created",
            time=events.TIME,
        )

        await worker.handle(event)

        assert dispatcher.jobs == []
