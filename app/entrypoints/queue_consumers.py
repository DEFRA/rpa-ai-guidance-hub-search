"""The service's queue consumers: an entry point, like the HTTP routes.

Each queue belongs to the sub-domain it reaches: the indexing queue's guide
events to `ingestion` (`ingestion.preparation`), which writes the index in
this process through `dispatch.BatchingDispatcher`. Only this module knows SQS
messages: it hands each handler the message's event, with the event's trace id
set for the handler's logs, and the handler raises to leave a message to be
retried.

Each is an `app.common.sqs_consumer` consumer, entered with `async with` around
the lifespan's `yield`. They share the app's SQS client, so their endpoint comes
from `AppConfig` like every other client's.

A heartbeat keeps a message hidden while its handler runs, however long that
takes, so a slow message is never handed to another consumer meanwhile. On
shutdown each waits up to `SQS_SHUTDOWN_TIMEOUT_SECONDS` for handlers in
flight; uvicorn's graceful shutdown is given longer (`entrypoints.fastapi`).
"""

from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Any

from app import config as app_config
from app.common import cloudevents, metrics, tracing
from app.common.sqs_consumer import config as consumer_config
from app.common.sqs_consumer import consumer, message
from app.common.sqs_consumer import hooks as consumer_hooks
from app.index import builds
from app.indexing import dispatch, stages
from app.indexing.apply import document_applier
from app.indexing.prepare import document_preparer
from app.ingestion import models as ingestion_models
from app.ingestion import preparation
from app.records import chunk_set_store, section_store

if TYPE_CHECKING:
    from types_boto3_s3 import S3Client
    from types_boto3_sqs import SQSClient

FAILED_METRIC = "IngestionFailed"

PREPARE_CONCURRENCY = 4
"""Messages the preparation worker handles at once: the most one poll returns.

Each makes one S3 call at a time, so this bounds the calls they make together.
"""

HEARTBEAT = consumer_config.Heartbeat(interval=20, visibility_timeout=60)
"""Extends a message's visibility every 20s to the queues' own 60s."""


def indexing_queue(
    config: app_config.AppConfig,
    sqs_client: SQSClient,
    s3_client: S3Client,
    index_builds: builds.IndexBuildService,
    receive: consumer_config.Receive | None = None,
) -> consumer.Consumer[ingestion_models.IndexingEvent]:
    """Consumes the indexing queue; `receive` is overridden by tests.

    Every message handled at once shares one `PendingWrites`, so the jobs that
    arrive while the index is being published go together in the next publish.
    """
    store_bucket = config.search_store_s3_bucket
    worker = preparation.PreparationWorker(
        preparer_for=_preparer_for(config, s3_client),
        dispatcher=dispatch.BatchingDispatcher(
            dispatch.PendingWrites(),
            document_applier.publisher(config, s3_client, index_builds),
        ),
        client=s3_client,
        content_bucket=config.managed_docs_s3_bucket,
        sections=section_store.S3SectionStore(s3_client, store_bucket),
        chunk_sets=chunk_set_store.S3ChunkSetStore(s3_client, store_bucket),
    )

    return consumer.Consumer(
        config.search_indexing_sqs_queue_url,
        _traced(worker.handle),
        deserializer=ingestion_models.loads,
        receive=receive or consumer_config.Receive(max_messages=PREPARE_CONCURRENCY),
        heartbeat=HEARTBEAT,
        shutdown_timeout=config.sqs_shutdown_timeout_seconds,
        hooks=hooks(config),
        client=sqs_client,
    )


def _traced[Event: cloudevents.CloudEvent](
    handle: Callable[[Event], Awaitable[None]],
) -> Callable[[message.Message[Event]], Awaitable[None]]:
    """A message handler that runs `handle` under the event's trace id."""

    async def handle_message(item: message.Message[Event]) -> None:
        event = item.payload
        token = tracing.ctx_trace_id.set(event.cdprequestid or item.message_id)

        try:
            await handle(event)
        finally:
            tracing.ctx_trace_id.reset(token)

    return handle_message


def hooks(config: app_config.AppConfig) -> consumer_hooks.Hooks:
    """Counts each failed message, when metrics are on."""
    if not config.enable_metrics:
        return consumer_hooks.Hooks()

    async def count_failure(
        _error: Exception, failed: Sequence[message.Message[Any]]
    ) -> None:
        metrics.counter(FAILED_METRIC, len(failed))

    return consumer_hooks.Hooks(on_processing_error=count_failure)


def _preparer_for(
    config: app_config.AppConfig,
    client: S3Client,
) -> Callable[[preparation.Readers], stages.Preparer]:
    def preparer_for(readers: preparation.Readers) -> stages.Preparer:
        return document_preparer.from_config(config, client, readers)

    return preparer_for
