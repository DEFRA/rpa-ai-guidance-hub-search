"""The indexing queue, as the ingestion routes send guide events to it."""

import asyncio
from typing import TYPE_CHECKING, Annotated, Protocol

import fastapi

from app import config as app_config
from app.common import cloudevents, sqs
from app.ingestion import models

if TYPE_CHECKING:
    from types_boto3_sqs import SQSClient
    from types_boto3_sqs.type_defs import MessageAttributeValueTypeDef


class IndexingQueue(Protocol):
    async def send(self, event: models.GuideEvent) -> None:
        """Puts the event on the queue, or raises."""
        ...


class SqsIndexingQueue:
    """Implements `IndexingQueue`."""

    def __init__(self, client: SQSClient, queue_url: str) -> None:
        self._client = client
        self._queue_url = queue_url

    async def send(self, event: models.GuideEvent) -> None:
        await asyncio.to_thread(
            self._client.send_message,
            QueueUrl=self._queue_url,
            MessageBody=cloudevents.dumps(event),
            MessageAttributes=_strings(models.message_attributes(event)),
        )


def _strings(values: dict[str, str]) -> dict[str, MessageAttributeValueTypeDef]:
    return {
        name: {"DataType": "String", "StringValue": value}
        for name, value in values.items()
    }


def get_indexing_queue(
    config: app_config.Config,
    client: sqs.Client,
) -> IndexingQueue:
    """FastAPI dependency: the indexing queue the ingestion routes send to."""
    return SqsIndexingQueue(client, config.search_indexing_sqs_queue_url)


IndexingQueueDep = Annotated[IndexingQueue, fastapi.Depends(get_indexing_queue)]
"""Injects the indexing queue into a FastAPI route."""
