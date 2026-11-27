"""The index sync queue, where prepared jobs wait for the single writer."""

import asyncio
from typing import TYPE_CHECKING

from app.common import cloudevents, tracing
from app.indexing import jobs, models

if TYPE_CHECKING:
    from types_boto3_sqs import SQSClient


class SqsApplyDispatcher:
    """Implements `dispatch.ApplyDispatcher` with the index sync FIFO queue."""

    def __init__(
        self, client: SQSClient, queue_url: str, chunk_set_bucket: str
    ) -> None:
        self._client = client
        self._queue_url = queue_url
        self._chunk_set_bucket = chunk_set_bucket

    async def dispatch(self, job: jobs.WriteJob) -> None:
        event = models.from_job(
            job, self._chunk_set_bucket, tracing.ctx_trace_id.get(None)
        )

        await asyncio.to_thread(
            self._client.send_message,
            QueueUrl=self._queue_url,
            MessageBody=cloudevents.dumps(event),
            MessageGroupId=models.GROUP_ID,
            MessageDeduplicationId=event.id,
        )
