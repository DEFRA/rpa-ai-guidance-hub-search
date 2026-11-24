"""The queue consumers: wired to the configured queues, as the package expects."""

import logging

import pytest

from app import config as app_config
from app.common import cloudevents, tracing
from app.common.sqs_consumer import errors, message
from app.entrypoints import queue_consumers
from app.indexing import models as indexing_models
from app.ingestion import models as ingestion_models
from tests.support import events
from tests.unit.common.sqs_consumer.support import consumers
from tests.unit.common.sqs_consumer.support import sqs as consumer_sqs


@pytest.fixture
def config():
    return app_config.get_config()


class TestHooks:
    def test_count_nothing_without_metrics(self, config):
        off = config.model_copy(update={"enable_metrics": False})

        assert queue_consumers.hooks(off).on_processing_error is None

    async def test_count_each_failed_message_with_metrics(self, mocker, config):
        counter = mocker.patch("app.common.metrics.counter")
        on = config.model_copy(update={"enable_metrics": True})

        hook = queue_consumers.hooks(on).on_processing_error
        await hook(
            RuntimeError("failed"),
            [_message(events.deleted(), "m-1"), _message(events.deleted(), "m-2")],
        )

        counter.assert_called_once_with(queue_consumers.FAILED_METRIC, 2)

    async def test_log_each_failure_with_metrics(
        self, mocker, config, moto_sqs, caplog
    ):
        counter = mocker.patch("app.common.metrics.counter")
        on = config.model_copy(update={"enable_metrics": True})
        queue_url = moto_sqs.create_queue(QueueName="indexing")["QueueUrl"]
        sent = moto_sqs.send_message(QueueUrl=queue_url, MessageBody="a")

        async def fail(_item):
            msg = "index publish failed"
            raise RuntimeError(msg)

        subject = consumers.build(
            queue_url, client=moto_sqs, handler=fail, hooks=queue_consumers.hooks(on)
        )

        with caplog.at_level(logging.ERROR, logger=queue_consumers.__name__):
            async with subject:
                await consumer_sqs.wait_for(lambda: counter.called)

        (record,) = [r for r in caplog.records if r.name == queue_consumers.__name__]
        assert sent["MessageId"] in record.getMessage()
        assert record.exc_info is not None
        assert str(record.exc_info[1]) == "index publish failed"


def _message(event, message_id="m-1"):
    return message.Message.from_sqs(
        {
            "MessageId": message_id,
            "ReceiptHandle": f"r-{message_id}",
            "Body": cloudevents.dumps(event),
        },
        deserializer=ingestion_models.loads,
    )


class TestTraced:
    """Handlers get the message's event, with the event's trace id set."""

    async def test_hands_the_handler_the_event_under_its_trace_id(self):
        seen = []

        async def handle(event):
            seen.append((event, tracing.ctx_trace_id.get(None)))

        event = events.deleted(trace_id="trace-4")

        await queue_consumers._traced(handle)(_message(event))

        assert seen == [(event, "trace-4")]

    async def test_traces_by_message_id_without_one(self):
        seen = []

        async def handle(_event):
            seen.append(tracing.ctx_trace_id.get(None))

        before = tracing.ctx_trace_id.get(None)

        await queue_consumers._traced(handle)(_message(events.deleted(), "m-9"))

        assert seen == ["m-9"]
        assert tracing.ctx_trace_id.get(None) == before

    async def test_raises_what_the_handler_raised_so_the_message_is_retried(self):
        async def handle(_event):
            msg = "failed"
            raise RuntimeError(msg)

        handle_message = queue_consumers._traced(handle)
        item = _message(events.deleted())

        with pytest.raises(RuntimeError, match="failed"):
            await handle_message(item)


BUCKET = "search-store"


class RecordingWriter:
    """Implements `queue_consumers.Writer`: records each batch, answers `outcomes`."""

    def __init__(self, outcomes=None, raises=None):
        self.batches = []
        self._outcomes = outcomes
        self._raises = raises

    async def write(self, batch):
        self.batches.append(list(batch))

        if self._raises is not None:
            raise self._raises

        return self._outcomes or [None] * len(batch)


def _index_sync_message(body, number):
    return message.Message.from_sqs(
        {"MessageId": f"m-{number}", "ReceiptHandle": f"r-{number}", "Body": body},
        deserializer=indexing_models.loads,
    )


def _job_message(job, number=0, bucket=BUCKET, trace_id=None):
    event = indexing_models.from_job(job, bucket, trace_id)
    return _index_sync_message(cloudevents.dumps(event), number)


class TestWrittenBy:
    """The writer's batch handler keeps the FIFO queue's order."""

    async def test_writes_the_batch_s_jobs_together_in_order(self):
        writer = RecordingWriter()
        batch = [
            _job_message(events.apply_job("k/1"), 0),
            _job_message(events.remove_job(), 1),
        ]

        await queue_consumers._written_by(writer, BUCKET)(batch)

        assert writer.batches == [[events.apply_job("k/1"), events.remove_job()]]

    async def test_fails_a_failed_job_and_holds_back_every_later_one(self):
        missing = RuntimeError("nothing staged")
        first = _job_message(events.apply_job("k/1"), 0)
        second = _job_message(events.apply_job("k/2"), 1)
        third = _job_message(events.apply_job("k/3"), 2)
        writer = RecordingWriter(outcomes=[None, missing, None])

        with pytest.raises(errors.BatchFailureError) as raised:
            await queue_consumers._written_by(writer, BUCKET)([first, second, third])

        assert list(raised.value.failed) == [second, third]
        assert raised.value.failed[second] is missing
        assert isinstance(raised.value.failed[third], queue_consumers.HeldBackError)

    async def test_fails_the_whole_batch_when_the_writer_raises(self):
        writer = RecordingWriter(raises=RuntimeError("write failed"))

        with pytest.raises(RuntimeError, match="write failed"):
            await queue_consumers._written_by(writer, BUCKET)(
                [_job_message(events.apply_job())]
            )

    async def test_writes_only_what_comes_before_an_unreadable_message(self):
        writer = RecordingWriter()
        before = _job_message(events.apply_job("k/1"), 0)
        bad = _index_sync_message("not json", 1)
        after = _job_message(events.apply_job("k/2"), 2)

        with pytest.raises(errors.BatchFailureError) as raised:
            await queue_consumers._written_by(writer, BUCKET)([before, bad, after])

        assert writer.batches == [[events.apply_job("k/1")]]
        assert list(raised.value.failed) == [bad, after]
        assert isinstance(raised.value.failed[bad], errors.DeserializationError)
        assert isinstance(raised.value.failed[after], queue_consumers.HeldBackError)

    async def test_refuses_a_chunk_set_in_another_bucket(self):
        writer = RecordingWriter()
        elsewhere = _job_message(events.apply_job(), bucket="someone-elses")

        with pytest.raises(errors.BatchFailureError) as raised:
            await queue_consumers._written_by(writer, BUCKET)([elsewhere])

        assert isinstance(
            raised.value.failed[elsewhere],
            indexing_models.ChunkSetBucketNotAllowedError,
        )
        assert writer.batches == []

    async def test_logs_each_written_job_with_its_trace_id(self, caplog):
        written = _job_message(events.apply_job(), trace_id="t-7")

        with caplog.at_level("INFO"):
            await queue_consumers._written_by(RecordingWriter(), BUCKET)([written])

        assert "trace id t-7" in caplog.text
