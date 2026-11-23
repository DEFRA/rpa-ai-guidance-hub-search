"""The routes' guide events reach the indexing queue as CloudEvents, against moto."""

import pytest

from app.ingestion import event_queue
from app.ingestion import models as ingestion_models
from tests.support import events
from tests.support import sqs as sqs_support


@pytest.fixture
def indexing_url(moto_sqs):
    return moto_sqs.create_queue(QueueName="indexing")["QueueUrl"]


def _attributes(message):
    return {
        name: value["StringValue"]
        for name, value in message.get("MessageAttributes", {}).items()
    }


class TestSqsIndexingQueue:
    @pytest.mark.parametrize("event", [events.published(), events.deleted()])
    async def test_sends_the_event_with_its_filter_attributes(
        self, moto_sqs, indexing_url, event
    ):
        await event_queue.SqsIndexingQueue(moto_sqs, indexing_url).send(event)

        (message,) = sqs_support.receive_now(moto_sqs, indexing_url)
        assert ingestion_models.loads(message["Body"]) == event
        assert _attributes(message) == ingestion_models.message_attributes(event)
