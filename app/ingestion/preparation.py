"""Prepares each guide event from the indexing queue for the index writer.

The writer runs in this process (`dispatch.BatchingDispatcher`), one per
replica, as the ingestion routes' did. A dispatch returns once its job is
written, so a message is acknowledged only after the index has it.

Messages are handled several at once and not in arrival order. The applier keeps
the index safe whichever order jobs reach it in, but the files a version stages
are not: events for the same version take turns (`VersionLocks`), so a
publication that is still preparing can't stage files again after a deletion
has cleaned them up, nor two preparations mix their sections and chunks.

Each event also holds the `PreparationGate` until its job is dispatched, so an
index reset (which wipes those files) never lands in the middle of one.
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable, Coroutine, Mapping
from typing import TYPE_CHECKING, Any

from botocore import exceptions as botocore_exceptions

from app.common import cloudevents, s3
from app.index import index_schema
from app.indexing import dispatch, jobs, stages
from app.indexing.prepare import guide_content, sources
from app.ingestion import models
from app.records import chunk_set_store, ids, section_store
from app.records import models as record_models

if TYPE_CHECKING:
    from types_boto3_s3 import S3Client

logger = logging.getLogger(__name__)

Readers = Mapping[record_models.SourceType, sources.SourceReader]


VersionKey = tuple[ids.DocumentId, ids.VersionId | None]


class ContentBucketNotAllowedError(ValueError):
    """The event's content is outside the bucket this service reads from."""


class VersionLocks:
    """One lock per document version, kept only while something holds or wants it.

    A source that mints its version ids while reading has no version id until
    then, so its events share the document's `None` key.
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


class PreparationWorker:
    def __init__(
        self,
        preparer_for: Callable[[Readers], stages.Preparer],
        dispatcher: dispatch.ApplyDispatcher,
        client: S3Client,
        content_bucket: str,
        sections: section_store.SectionStore,
        chunk_sets: chunk_set_store.ChunkSetStore,
        strategy: str = index_schema.CHUNK_STRATEGY,
        versions: VersionLocks | None = None,
        gate: dispatch.PreparationGate | None = None,
    ) -> None:
        self._preparer_for = preparer_for
        self._dispatcher = dispatcher
        self._client = client
        self._content_bucket = content_bucket
        self._sections = sections
        self._chunk_sets = chunk_sets
        self._strategy = strategy
        self._versions = versions or VersionLocks()
        self._gate = gate or dispatch.PreparationGate()
        # asyncio keeps tasks only weakly; the work must outlive its handler.
        self._running: set[asyncio.Task[None]] = set()

    async def handle(self, event: models.IndexingEvent) -> None:
        """Indexes a published version, or removes a deleted one; ignores the rest.

        The work runs as a task of its own, which holds the gate until it has
        finished. A cancelled handler stops waiting but not the work, whose
        storage writes (in threads) can't be cancelled, so a reset never wipes
        the bucket while one of them is still to land.
        """
        match event:
            case models.GuideVersionPublishedV1():
                await self._hold_gate(self._index(event))
            case models.GuideVersionDeletedV1():
                await self._hold_gate(self._remove(event.to_job()))
            case cloudevents.UnknownEvent():
                logger.info(
                    "Ignored %s event %s from %s", event.type, event.id, event.source
                )

    async def _hold_gate(self, work: Coroutine[Any, Any, None]) -> None:
        async def run() -> None:
            async with self._gate.preparing():
                await work

        task = asyncio.create_task(run())
        self._running.add(task)
        task.add_done_callback(self._running.discard)

        await asyncio.shield(task)

    async def _index(self, event: models.GuideVersionPublishedV1) -> None:
        bucket, _ = guide_content.parse_s3_url(event.data.content_url)

        if bucket != self._content_bucket:
            msg = f"{event.data.content_url} is not in {self._content_bucket}"
            raise ContentBucketNotAllowedError(msg)

        job = event.to_job()
        preparer = self._preparer_for({job.source_type: event.to_reader(self._client)})

        async with self._versions.hold(job.document_id, job.version_id):
            try:
                apply_job = await preparer.prepare(job)
            except botocore_exceptions.ClientError as error:
                _log_unreadable(event, error)
                raise

            await self._dispatcher.dispatch(apply_job)

        logger.info(
            "Indexed %s version %s", apply_job.document_id, apply_job.version_id
        )

    async def _remove(self, job: jobs.RemoveJob) -> None:
        """Takes the version out of the index, then deletes its staged files.

        If the removal fails the files are kept, so the retry still finds them.
        """
        async with self._versions.hold(job.document_id, job.version_id):
            await self._dispatcher.dispatch(job)

            await self._sections.delete(job.document_id, job.version_id)
            await self._chunk_sets.delete(
                self._strategy, job.document_id, job.version_id
            )

        logger.info("Removed %s version %s", job.document_id, job.version_id)


def _log_unreadable(
    event: models.GuideVersionPublishedV1,
    error: botocore_exceptions.ClientError,
) -> None:
    if s3.is_missing(error):
        reason = "is missing"
    elif s3.is_access_denied(error):
        reason = "may not be read"
    else:
        return

    logger.warning(
        "Content of %s version %s %s: %s",
        event.document_id,
        event.version_id,
        reason,
        event.data.content_url,
    )
