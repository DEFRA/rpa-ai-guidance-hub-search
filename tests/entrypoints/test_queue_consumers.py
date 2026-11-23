"""The queue consumers: wired to the configured queues, as the package expects."""

import pytest

from app import config as app_config
from app.common import cloudevents, tracing
from app.common.sqs_consumer import message
from app.entrypoints import queue_consumers
from app.ingestion import models as ingestion_models
from tests.support import events


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
        await hook(RuntimeError("failed"), [mocker.Mock(), mocker.Mock()])

        counter.assert_called_once_with(queue_consumers.FAILED_METRIC, 2)


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

        with pytest.raises(RuntimeError, match="failed"):
            await queue_consumers._traced(handle)(_message(events.deleted()))
