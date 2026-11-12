"""Indexes and deletes the document versions the CMS sends, after replying.

The routes answer `202` and leave the work to `index` and `delete`, which run
as background tasks in this process. One lock serialises them, in the order
they arrived, so they never race each other for the index. That holds only
within one replica; a queue replaces it later (`dispatch.ApplyDispatcher`).

Failures are logged and counted, then dropped. Every step is idempotent, so
the CMS recovers by sending the same request again.
"""

import asyncio
import logging
from collections.abc import Callable, Mapping

from app import config as app_config
from app.common import metrics, s3
from app.index import index_schema
from app.indexing import dispatch, jobs, stages
from app.indexing.prepare import document_preparer, sources
from app.records import chunk_set_store, ids, models, section_store

logger = logging.getLogger(__name__)

FAILED_METRIC = "IngestionFailed"

Readers = Mapping[models.SourceType, sources.SourceReader]


class IngestionService:
    def __init__(
        self,
        dispatcher: dispatch.ApplyDispatcher,
        preparer_for: Callable[[Readers], stages.Preparer],
        sections: section_store.SectionStore,
        chunk_sets: chunk_set_store.ChunkSetStore,
        strategy: str = index_schema.CHUNK_STRATEGY,
        count_failure: Callable[[], None] = lambda: None,
    ) -> None:
        self._dispatcher = dispatcher
        self._preparer_for = preparer_for
        self._sections = sections
        self._chunk_sets = chunk_sets
        self._strategy = strategy
        self._count_failure = count_failure
        self._lock = asyncio.Lock()

    async def index(
        self,
        job: jobs.IndexJob,
        reader: sources.SourceReader,
        trace_id: str | None = None,
    ) -> None:
        """Prepares the version, then indexes it."""
        async with self._lock:
            try:
                await self._index(job, reader)
            except Exception:
                self._failed("index", job.document_id, job.version_id, trace_id)

    async def delete(self, job: jobs.RemoveJob, trace_id: str | None = None) -> None:
        """Takes the version out of the index, then deletes its staged files.

        The files go second: a leftover file is harmless and the next delete
        removes it, whereas index entries without their files are not.
        """
        async with self._lock:
            try:
                await self._dispatcher.dispatch(job)
                await self._sections.delete(job.document_id, job.version_id)
                await self._chunk_sets.delete(
                    self._strategy, job.document_id, job.version_id
                )
            except Exception:
                self._failed("delete", job.document_id, job.version_id, trace_id)

    async def _index(self, job: jobs.IndexJob, reader: sources.SourceReader) -> None:
        preparer = self._preparer_for({job.source_type: reader})
        await self._dispatcher.dispatch(await preparer.prepare(job))

    def _failed(
        self,
        operation: str,
        document_id: ids.DocumentId,
        version_id: ids.VersionId | None,
        trace_id: str | None,
    ) -> None:
        logger.exception(
            "Failed to %s %s version %s (trace id %s)",
            operation,
            document_id,
            version_id,
            trace_id,
        )
        self._count_failure()


_service: IngestionService | None = None


def get_service() -> IngestionService:
    """The one service per process, so its lock covers every request."""
    global _service

    if _service is None:
        _service = from_config()

    return _service


def from_config() -> IngestionService:
    config = app_config.get_config()
    client = s3.get_s3_client()

    return IngestionService(
        dispatcher=dispatch.InProcessDispatcher(),
        preparer_for=document_preparer.from_config,
        sections=section_store.S3SectionStore(client, config.search_store_s3_bucket),
        chunk_sets=chunk_set_store.S3ChunkSetStore(
            client, config.search_store_s3_bucket
        ),
        count_failure=_count_failure if config.enable_metrics else lambda: None,
    )


def _count_failure() -> None:
    metrics.counter(FAILED_METRIC, 1)
