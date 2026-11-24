"""The service's queue consumers: an entry point, like the HTTP routes.

Each queue belongs to the sub-domain it reaches: the indexing queue's guide
events to `ingestion` (`ingestion.preparation`), which sends each prepared job
to the index sync queue (`indexing.index_sync`), and that queue's jobs to the
index's single writer (`indexing.apply.index_writer`). Only this module knows SQS
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

import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Any, Protocol

from app import config as app_config
from app.common import cloudevents, metrics, tracing
from app.common.sqs_consumer import config as consumer_config
from app.common.sqs_consumer import consumer, errors, message
from app.common.sqs_consumer import hooks as consumer_hooks
from app.index import builds
from app.indexing import index_sync, jobs, stages
from app.indexing import models as indexing_models
from app.indexing.apply import document_applier, index_writer
from app.indexing.prepare import document_preparer
from app.ingestion import models as ingestion_models
from app.ingestion import preparation
from app.records import chunk_set_store, section_store

if TYPE_CHECKING:
    from types_boto3_s3 import S3Client
    from types_boto3_sqs import SQSClient

logger = logging.getLogger(__name__)

FAILED_METRIC = "IngestionFailed"

PREPARE_CONCURRENCY = 4
"""Messages the preparation worker handles at once: the most one poll returns.

Each makes one S3 call at a time, so this bounds the calls they make together.
"""

HEARTBEAT = consumer_config.Heartbeat(interval=20, visibility_timeout=60)
"""Extends a message's visibility every 20s to the queues' own 60s."""

INDEX_SYNC_RECEIVE = consumer_config.Receive(max_messages=10)
"""Jobs the writer publishes together: the most one poll returns.

Publishing costs a download and an upload of the whole index, whatever it
changes, so a batch of N jobs costs one publish rather than N.
"""


def indexing_queue(
    config: app_config.AppConfig,
    sqs_client: SQSClient,
    s3_client: S3Client,
    receive: consumer_config.Receive | None = None,
) -> consumer.Consumer[ingestion_models.IndexingEvent]:
    """Consumes the indexing queue; `receive` is overridden by tests."""
    worker = preparation.PreparationWorker(
        preparer_for=_preparer_for(config, s3_client),
        dispatcher=index_sync.SqsApplyDispatcher(
            sqs_client,
            config.search_index_sync_sqs_queue_url,
            config.search_store_s3_bucket,
        ),
        client=s3_client,
        content_bucket=config.managed_docs_s3_bucket,
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


def index_sync_queue(
    config: app_config.AppConfig,
    sqs_client: SQSClient,
    s3_client: S3Client,
    index_builds: builds.IndexBuildService,
    receive: consumer_config.Receive | None = None,
) -> consumer.BatchConsumer[indexing_models.IndexSyncEvent]:
    """Consumes the index sync queue a batch at a time; `receive` is for tests.

    The queue is FIFO with every event in one message group, so SQS hands its
    messages to one receiver at a time across all replicas: the index has a
    single writer however many replicas prepare.
    """
    bucket = config.search_store_s3_bucket
    writer = index_writer.IndexWriter(
        publish_all=document_applier.publisher(config, s3_client, index_builds),
        sections=section_store.S3SectionStore(s3_client, bucket),
        chunk_sets=chunk_set_store.S3ChunkSetStore(s3_client, bucket),
    )

    return consumer.BatchConsumer(
        config.search_index_sync_sqs_queue_url,
        _written_by(writer, bucket),
        deserializer=indexing_models.loads,
        receive=receive or INDEX_SYNC_RECEIVE,
        heartbeat=HEARTBEAT,
        shutdown_timeout=config.sqs_shutdown_timeout_seconds,
        hooks=hooks(config),
        client=sqs_client,
    )


class HeldBackError(Exception):
    """An earlier message in the batch failed, so this one waits to keep FIFO order."""


IndexSyncMessage = message.Message[indexing_models.IndexSyncEvent]


class Writer(Protocol):
    async def write(self, batch: Sequence[jobs.WriteJob]) -> list[Exception | None]:
        """What each job raised, in order, or None; raises if nothing was written."""
        ...


def _written_by(
    writer: Writer,
    chunk_set_bucket: str,
) -> Callable[[Sequence[IndexSyncMessage]], Awaitable[None]]:
    """A batch handler that writes the batch's jobs in one publish.

    The first message that fails, and every message after it, are left to be
    retried, so a later job is never acknowledged before an earlier one: the
    queue's order holds. Jobs after a failed one may already be written; writing
    them again changes nothing.
    """

    async def handle_batch(items: Sequence[IndexSyncMessage]) -> None:
        written, failed = _to_jobs(items, chunk_set_bucket)

        if written:
            outcomes = await writer.write([job for _, job in written])

            for position, ((item, job), outcome) in enumerate(
                zip(written, outcomes, strict=True)
            ):
                if outcome is not None:
                    later = [held for held, _ in written[position + 1 :]]
                    failed = {item: outcome, **_held_back(later), **failed}
                    break

                _log_written(item.payload, job)

        if failed:
            raise errors.BatchFailureError(failed)

    return handle_batch


def _to_jobs(
    items: Sequence[IndexSyncMessage],
    chunk_set_bucket: str,
) -> tuple[
    list[tuple[IndexSyncMessage, jobs.WriteJob]],
    dict[message.Message[Any], Exception],
]:
    """Each message's job, up to the first that can't be read, which fails."""
    written: list[tuple[IndexSyncMessage, jobs.WriteJob]] = []

    for position, item in enumerate(items):
        try:
            written.append((item, item.payload.to_job(chunk_set_bucket)))
        except Exception as error:  # fails this message, and holds back the rest
            return written, {item: error} | _held_back(items[position + 1 :])

    return written, {}


def _held_back(
    items: Sequence[IndexSyncMessage],
) -> dict[message.Message[Any], Exception]:
    return {item: HeldBackError(item.message_id) for item in items}


def _log_written(event: indexing_models.IndexSyncEvent, job: jobs.WriteJob) -> None:
    logger.info(
        "Wrote %s for %s version %s (trace id %s)",
        type(job).__name__,
        job.document_id,
        job.version_id,
        event.cdprequestid,
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
    """Counts each failed message, when metrics are on.

    The consumer logs a failure itself only when no hook is installed, so the
    hook logs it too.
    """
    if not config.enable_metrics:
        return consumer_hooks.Hooks()

    async def count_failure(
        error: Exception, failed: Sequence[message.Message[Any]]
    ) -> None:
        logger.error(
            "sqs handler failed for %s",
            ", ".join(item.message_id for item in failed),
            exc_info=error,
        )
        metrics.counter(FAILED_METRIC, len(failed))

    return consumer_hooks.Hooks(on_processing_error=count_failure)


def _preparer_for(
    config: app_config.AppConfig,
    client: S3Client,
) -> Callable[[preparation.Readers], stages.Preparer]:
    def preparer_for(readers: preparation.Readers) -> stages.Preparer:
        return document_preparer.from_config(config, client, readers)

    return preparer_for
