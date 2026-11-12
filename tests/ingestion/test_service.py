"""The service prepares, indexes and deletes one request at a time."""

import asyncio
import logging

import pytest

from app.indexing import jobs
from app.ingestion import service
from app.records import chunk_sets, models
from tests.fakes import chunk_set_store as fake_chunk_set_store
from tests.fakes import section_store as fake_section_store
from tests.support import chunks as fixtures
from tests.support import guides

STRATEGY = fixtures.FIXTURE_STRATEGY
REMOVE = jobs.RemoveJob(
    document_id=fixtures.DOCUMENT_ID,
    version_id=fixtures.VERSION_ID,
    occurred_at=fixtures.OCCURRED_AT,
)


def _apply_job(job):
    return jobs.ApplyJob(
        document_id=job.document_id,
        version_id=job.version_id,
        status=job.status,
        occurred_at=job.occurred_at,
        strategy=STRATEGY,
        chunk_set_key="staged/key",
    )


class RecordingPreparer:
    """Stands in for the preparer `preparer_for` builds; records each call."""

    def __init__(self, calls):
        self.calls = calls

    async def prepare(self, job):
        self.calls.append(("prepare", job))
        return _apply_job(job)


class RecordingDispatcher:
    def __init__(self, calls):
        self.calls = calls

    async def dispatch(self, job):
        self.calls.append(("dispatch", job))


@pytest.fixture
def calls():
    return []


@pytest.fixture
def sections():
    return fake_section_store.InMemorySectionStore()


@pytest.fixture
def staged():
    return fake_chunk_set_store.InMemoryChunkSetStore()


@pytest.fixture
def readers_seen():
    return []


@pytest.fixture
def count_failure(mocker):
    return mocker.Mock()


@pytest.fixture
def ingestion(calls, sections, staged, readers_seen, count_failure):
    def preparer_for(readers):
        readers_seen.append(readers)
        return RecordingPreparer(calls)

    return service.IngestionService(
        dispatcher=RecordingDispatcher(calls),
        preparer_for=preparer_for,
        sections=sections,
        chunk_sets=staged,
        strategy=STRATEGY,
        count_failure=count_failure,
    )


class TestIndex:
    async def test_prepares_then_indexes_a_published_version(
        self, ingestion, calls, readers_seen
    ):
        job = guides.job()
        reader = object()

        await ingestion.index(job, reader)

        assert calls == [("prepare", job), ("dispatch", _apply_job(job))]
        assert readers_seen == [{models.SourceType.GUIDE: reader}]


class TestDelete:
    async def test_removes_from_the_index_then_deletes_the_files(
        self, ingestion, calls, sections, staged
    ):
        header, chunks = fixtures.document([fixtures.Section("1 Overview", 1)])
        await sections.put(header, [])
        await staged.put(chunk_sets.ChunkSet(header, STRATEGY, tuple(chunks)))

        await ingestion.delete(REMOVE)

        assert calls == [("dispatch", REMOVE)]
        assert sections.files == {}
        assert staged.sets == {}

    async def test_keeps_the_files_when_removing_from_the_index_fails(
        self, ingestion, sections
    ):
        header, _ = fixtures.document([fixtures.Section("1 Overview", 1)])
        await sections.put(header, [])
        ingestion._dispatcher.dispatch = _raise

        await ingestion.delete(REMOVE)

        assert list(sections.files) == [(header.document_id, header.version_id)]


async def _raise(*_):
    msg = "boom"
    raise RuntimeError(msg)


class TestFailures:
    async def test_are_logged_counted_and_swallowed(
        self, ingestion, count_failure, caplog
    ):
        ingestion._dispatcher.dispatch = _raise
        caplog.set_level(logging.ERROR)

        await ingestion.index(guides.job(), object(), trace_id="trace-1")

        (record,) = caplog.records
        assert "index" in record.getMessage()
        assert str(guides.DOCUMENT_ID) in record.getMessage()
        assert "trace-1" in record.getMessage()
        assert record.exc_info is not None
        count_failure.assert_called_once_with()

    async def test_do_not_block_the_next_request(self, ingestion, calls):
        dispatch = ingestion._dispatcher.dispatch
        ingestion._dispatcher.dispatch = _raise
        await ingestion.index(guides.job(), object())

        ingestion._dispatcher.dispatch = dispatch
        await ingestion.delete(REMOVE)

        assert calls[-1] == ("dispatch", REMOVE)


class TestOrdering:
    async def test_runs_one_request_at_a_time_in_arrival_order(self, ingestion, calls):
        released = asyncio.Event()
        dispatch = ingestion._dispatcher.dispatch

        async def held(job):
            calls.append(("started", job))
            await released.wait()
            await dispatch(job)

        ingestion._dispatcher.dispatch = held
        first = asyncio.create_task(ingestion.index(guides.job(), object()))
        await asyncio.sleep(0)
        second = asyncio.create_task(ingestion.delete(REMOVE))
        await asyncio.sleep(0)

        assert [name for name, _ in calls] == ["prepare", "started"]

        released.set()
        await asyncio.gather(first, second)

        assert [name for name, _ in calls] == [
            "prepare",
            "started",
            "dispatch",
            "started",
            "dispatch",
        ]
        assert calls[-1] == ("dispatch", REMOVE)


class TestFromConfig:
    def test_builds_one_service_per_process(self, mocker):
        mocker.patch.object(service, "_service", None)
        mocker.patch("app.common.s3.get_s3_client")

        assert service.get_service() is service.get_service()

    def test_counts_failures_only_with_metrics_enabled(self, mocker):
        counter = mocker.patch("app.common.metrics.counter")
        mocker.patch("app.common.s3.get_s3_client")
        config = mocker.patch("app.config.get_config").return_value
        config.search_store_s3_bucket = "bucket"

        config.enable_metrics = False
        service.from_config()._count_failure()
        counter.assert_not_called()

        config.enable_metrics = True
        service.from_config()._count_failure()
        counter.assert_called_once_with(service.FAILED_METRIC, 1)
