"""The queue consumers: wired to the configured queues, as the package expects."""

import logging

import pytest

from app import config as app_config
from app.common import cloudevents, tracing
from app.common.sqs_consumer import message
from app.entrypoints import queue_consumers
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
