"""Indexes and deletes document versions as background tasks, after replying.

Up to `PREPARE_CONCURRENCY` versions are read, chunked and staged at once, which
bounds the S3 calls they make together. Writes to the index go to one batching
writer per replica (`dispatch.BatchingDispatcher`) until a queue replaces it.
Failures are logged and dropped; every step is idempotent, so the CMS retries.

Requests no longer run strictly in arrival order. The applier keeps that safe:
it never lets an older version replace a newer one, nor brings back a removed
one, whichever order their jobs reach it in. Requests for the same version still
take turns (`VersionLocks`), so a PUT that is still preparing can't stage files
again after a DELETE has cleaned them up.
"""

import asyncio
import contextlib
import dataclasses
import logging
from collections.abc import AsyncIterator, Callable, Mapping
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

PREPARE_CONCURRENCY = 4

Readers = Mapping[models.SourceType, sources.SourceReader]


VersionKey = tuple[ids.DocumentId, ids.VersionId | None]


class VersionLocks:
    """One lock per document version, kept only while something holds or wants it.

    A source that mints its version ids while reading has no version id until
    then, so its requests share the document's `None` key.
    """

    def __init__(self) -> None:
        self._locks: dict[VersionKey, tuple[asyncio.Lock, int]] = {}

    @contextlib.asynccontextmanager
    async def hold(
        self,
        document_id: ids.DocumentId,
        version_id: ids.VersionId | None,
    ) -> AsyncIterator[None]:
        key = (document_id, version_id)
        lock, users = self._locks.get(key, (asyncio.Lock(), 0))
        self._locks[key] = (lock, users + 1)

        try:
            async with lock:
                yield
        finally:
            lock, users = self._locks[key]

            if users == 1:
                del self._locks[key]
            else:
                self._locks[key] = (lock, users - 1)

    def __len__(self) -> int:
        return len(self._locks)


@dataclasses.dataclass(frozen=True)
class IngestionState:
    """What every request's service shares; `create_app` makes one per app."""

    preparing: asyncio.Semaphore = dataclasses.field(
        default_factory=lambda: asyncio.Semaphore(PREPARE_CONCURRENCY),
    )
    writes: dispatch.PendingWrites = dataclasses.field(
        default_factory=dispatch.PendingWrites,
    )
    versions: VersionLocks = dataclasses.field(default_factory=VersionLocks)


class IngestionService:
    def __init__(
        self,
        preparing: asyncio.Semaphore,
        versions: VersionLocks,
        dispatcher: dispatch.ApplyDispatcher,
        preparer_for: Callable[[Readers], stages.Preparer],
        sections: section_store.SectionStore,
        chunk_sets: chunk_set_store.ChunkSetStore,
        strategy: str = index_schema.CHUNK_STRATEGY,
        count_failure: Callable[[], None] = lambda: None,
    ) -> None:
        self._preparing = preparing
        self._versions = versions
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
        try:
            await self._index(job, reader)
        except Exception:
            self._failed("index", job.document_id, job.version_id, trace_id)

    async def delete(self, job: jobs.RemoveJob, trace_id: str | None = None) -> None:
        """Takes the version out of the index, then deletes its staged files."""
        try:
            async with self._versions.hold(job.document_id, job.version_id):
                await self._dispatcher.dispatch(job)

                async with self._preparing:
                    await self._sections.delete(job.document_id, job.version_id)
                    await self._chunk_sets.delete(
                        self._strategy, job.document_id, job.version_id
                    )
        except Exception:
            self._failed("delete", job.document_id, job.version_id, trace_id)

    async def _index(self, job: jobs.IndexJob, reader: sources.SourceReader) -> None:
        preparer = self._preparer_for({job.source_type: reader})

        # The version's turn first, so no place to prepare is held waiting for it.
        async with self._versions.hold(job.document_id, job.version_id):
            async with self._preparing:
                apply_job = await preparer.prepare(job)

            await self._dispatcher.dispatch(apply_job)

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


def get_state(request: fastapi.Request) -> IngestionState:
    """FastAPI dependency: the state `create_app` made, shared by every request."""
    state: IngestionState = request.app.state.ingestion
    return state


def get_service(
    state: Annotated[IngestionState, fastapi.Depends(get_state)],
    config: app_config.Config,
    client: s3.Client,
    index_builds: Annotated[
        builds.IndexBuildService, fastapi.Depends(builds.get_index_build_service)
    ],
) -> IngestionService:
    """FastAPI dependency: a service per request, around the app's shared state."""

    def preparer_for(readers: Readers) -> stages.Preparer:
        return document_preparer.from_config(config, client, readers)

    return IngestionService(
        preparing=state.preparing,
        versions=state.versions,
        dispatcher=dispatch.BatchingDispatcher(
            state.writes,
            document_applier.publisher(config, client, index_builds),
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
