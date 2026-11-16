"""Indexes and deletes document versions as background tasks, after replying.

One lock serialises the work within a replica until a queue replaces it.
Failures are logged and dropped; every step is idempotent, so the CMS retries.
"""

import asyncio
import logging
from collections.abc import Callable, Mapping
from typing import Annotated

import fastapi

from app import config as app_config
from app.common import metrics, s3
from app.index import builds, index_schema
from app.indexing import dispatch, jobs, stages
from app.indexing.apply import document_applier
from app.indexing.prepare import document_preparer, sources
from app.records import chunk_set_store, ids, models, section_store

logger = logging.getLogger(__name__)

FAILED_METRIC = "IngestionFailed"

Readers = Mapping[models.SourceType, sources.SourceReader]


class IngestionService:
    def __init__(
        self,
        lock: asyncio.Lock,
        dispatcher: dispatch.ApplyDispatcher,
        preparer_for: Callable[[Readers], stages.Preparer],
        sections: section_store.SectionStore,
        chunk_sets: chunk_set_store.ChunkSetStore,
        strategy: str = index_schema.CHUNK_STRATEGY,
        count_failure: Callable[[], None] = lambda: None,
    ) -> None:
        self._lock = lock
        self._dispatcher = dispatcher
        self._preparer_for = preparer_for
        self._sections = sections
        self._chunk_sets = chunk_sets
        self._strategy = strategy
        self._count_failure = count_failure

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
        """Takes the version out of the index, then deletes its staged files."""
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


def get_lock(request: fastapi.Request) -> asyncio.Lock:
    """FastAPI dependency: the lock `create_app` made, shared by every request."""
    lock: asyncio.Lock = request.app.state.ingestion_lock
    return lock


def get_service(
    lock: Annotated[asyncio.Lock, fastapi.Depends(get_lock)],
    config: app_config.Config,
    client: s3.Client,
    index_builds: Annotated[
        builds.IndexBuildService, fastapi.Depends(builds.get_index_build_service)
    ],
) -> IngestionService:
    """FastAPI dependency: a service per request, around the app's one lock."""

    def preparer_for(readers: Readers) -> stages.Preparer:
        return document_preparer.from_config(config, client, readers)

    return IngestionService(
        lock=lock,
        dispatcher=dispatch.InProcessDispatcher(
            document_applier.publisher(config, client, index_builds)
        ),
        preparer_for=preparer_for,
        sections=section_store.S3SectionStore(client, config.search_store_s3_bucket),
        chunk_sets=chunk_set_store.S3ChunkSetStore(
            client, config.search_store_s3_bucket
        ),
        count_failure=_count_failure if config.enable_metrics else lambda: None,
    )


def _count_failure() -> None:
    metrics.counter(FAILED_METRIC, 1)
