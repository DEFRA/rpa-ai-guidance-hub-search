"""The service prepares a few requests at once, and hands each on to be written."""

import asyncio
import logging

import pytest

from app import config as app_config
from app.entrypoints import fastapi as entrypoint
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
        preparing=asyncio.Semaphore(2),
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


class TestConcurrency:
    async def test_prepares_no_more_at_once_than_the_service_allows(
        self, ingestion, calls
    ):
        released = asyncio.Event()
        preparing = []
        most = 0

        class HeldPreparer:
            async def prepare(self, job):
                nonlocal most
                preparing.append(job)
                most = max(most, len(preparing))
                await released.wait()
                preparing.remove(job)
                return _apply_job(job)

        ingestion._preparer_for = lambda _: HeldPreparer()
        tasks = [
            asyncio.create_task(ingestion.index(guides.job(), object()))
            for _ in range(5)
        ]
        await asyncio.sleep(0)

        assert len(preparing) == 2

        released.set()
        await asyncio.gather(*tasks)

        assert most == 2
        assert [name for name, _ in calls] == ["dispatch"] * 5

    async def test_dispatches_without_holding_a_place_to_prepare(
        self, ingestion, calls
    ):
        released = asyncio.Event()

        async def held(job):
            calls.append(("started", job))
            await released.wait()

        ingestion._dispatcher.dispatch = held
        tasks = [
            asyncio.create_task(ingestion.index(guides.job(), object()))
            for _ in range(3)
        ]
        for _ in range(5):
            await asyncio.sleep(0)

        # Two places to prepare, yet all three prepared while writes were held.
        assert [name for name, _ in calls].count("prepare") == 3

        released.set()
        await asyncio.gather(*tasks)


class TestGetService:
    def test_every_request_shares_the_apps_state(self, mocker):
        app = entrypoint.create_app(app_config.IngestionMode.HTTP)
        request = mocker.Mock(app=app)
        config = app_config.get_config()
        state = app.state.ingestion

        def built():
            return service.get_service(
                service.get_state(request), config, mocker.Mock(), mocker.Mock()
            )

        first, second = built(), built()

        assert first is not second
        assert first._preparing is second._preparing is state.preparing
        assert first._dispatcher._pending is second._dispatcher._pending
        assert first._dispatcher._pending is state.writes

    def test_counts_failures_only_with_metrics_enabled(self, mocker):
        counter = mocker.patch("app.common.metrics.counter")
        config = app_config.get_config()

        def built(enable_metrics):
            return service.get_service(
                service.IngestionState(),
                config.model_copy(update={"enable_metrics": enable_metrics}),
                mocker.Mock(),
                mocker.Mock(),
            )

        built(enable_metrics=False)._count_failure()
        counter.assert_not_called()

        built(enable_metrics=True)._count_failure()
        counter.assert_called_once_with(service.FAILED_METRIC, 1)
