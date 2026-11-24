"""Prepares each guide event from the indexing queue for the index writer.

Each job goes to the index writer on the index sync queue (`index_sync`). A
dispatch returns once its job is queued, so a message is acknowledged once the
writer will get its job, not once the index has it.

Messages are handled several at once and not in arrival order. Events for the
same version take turns (`VersionLocks`), so two preparations can't mix their
sections and chunks, and a version's jobs reach the index sync queue in the
order they were handled. The writer deletes a removed version's staged files,
after any job queued before the removal has read them.
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable, Mapping
from typing import TYPE_CHECKING

from botocore import exceptions as botocore_exceptions

from app.common import cloudevents, s3
from app.indexing import dispatch, stages
from app.indexing.prepare import guide_content, sources
from app.ingestion import models
from app.records import ids
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
        versions: VersionLocks | None = None,
    ) -> None:
        self._preparer_for = preparer_for
        self._dispatcher = dispatcher
        self._client = client
        self._content_bucket = content_bucket
        self._versions = versions or VersionLocks()

    async def handle(self, event: models.IndexingEvent) -> None:
        """Prepares a published version, or forwards a deletion; ignores the rest."""
        match event:
            case models.GuideVersionPublishedV1():
                await self._index(event)
            case models.GuideVersionDeletedV1():
                await self._remove(event)
            case cloudevents.UnknownEvent():
                logger.info(
                    "Ignored %s event %s from %s", event.type, event.id, event.source
                )

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
            "Queued %s version %s for the index",
            apply_job.document_id,
            apply_job.version_id,
        )

    async def _remove(self, event: models.GuideVersionDeletedV1) -> None:
        job = event.to_job()

        async with self._versions.hold(job.document_id, job.version_id):
            await self._dispatcher.dispatch(job)

        logger.info("Queued removal of %s version %s", job.document_id, job.version_id)


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
