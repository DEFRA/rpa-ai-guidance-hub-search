"""Prepared jobs reach the writer's FIFO queue as CloudEvents, against moto."""

import asyncio
import contextvars

import pytest

from app.common import tracing
from app.indexing import index_sync
from app.indexing import models as indexing_models
from tests.support import events
from tests.support import sqs as sqs_support

BUCKET = "search-store"


@pytest.fixture
def index_sync_url(moto_sqs):
    return moto_sqs.create_queue(
        QueueName="index_sync.fifo",
        Attributes={"FifoQueue": "true", "ContentBasedDeduplication": "false"},
    )["QueueUrl"]


@pytest.fixture
def dispatcher(moto_sqs, index_sync_url):
    return index_sync.SqsApplyDispatcher(moto_sqs, index_sync_url, BUCKET)


class TestSqsApplyDispatcher:
    @pytest.mark.parametrize("job", [events.apply_job(), events.remove_job()])
    async def test_sends_the_job_s_event_in_the_writer_s_group(
        self, moto_sqs, index_sync_url, dispatcher, job
    ):
        await dispatcher.dispatch(job)

        (message,) = sqs_support.receive_now(moto_sqs, index_sync_url)
        event = indexing_models.loads(message["Body"])
        assert event.to_job(BUCKET) == job
        assert message["Attributes"]["MessageGroupId"] == indexing_models.GROUP_ID
        assert message["Attributes"]["MessageDeduplicationId"] == event.id

    async def test_carries_the_trace_id_in_the_event(
        self, moto_sqs, index_sync_url, dispatcher
    ):
        token = tracing.ctx_trace_id.set("trace-3")

        try:
            await dispatcher.dispatch(events.remove_job())
        finally:
            tracing.ctx_trace_id.reset(token)

        (message,) = sqs_support.receive_now(moto_sqs, index_sync_url)
        assert indexing_models.loads(message["Body"]).cdprequestid == "trace-3"

    async def test_carries_no_trace_id_without_one(
        self, moto_sqs, index_sync_url, dispatcher
    ):
        await asyncio.create_task(
            dispatcher.dispatch(events.remove_job()), context=contextvars.Context()
        )

        (message,) = sqs_support.receive_now(moto_sqs, index_sync_url)
        assert indexing_models.loads(message["Body"]).cdprequestid is None
