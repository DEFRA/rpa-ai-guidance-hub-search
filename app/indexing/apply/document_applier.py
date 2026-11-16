"""The one applier: writes a prepared version's staged chunks to the index.

It reads only the chunk set the `ApplyJob` names, so it never needs a source,
the sectioning or a chunking strategy. It refuses chunks made by a strategy
other than the one the index schema holds (`index_schema.CHUNK_STRATEGY`).
Wiring lives in `from_config`.

It never lets an older version replace a newer one (by UUIDv7 order), nor
bring back a version that was removed. The index remembers the latest version
it has applied or removed, even once no content is left, so a delayed or
retried apply can't undo a newer version or a deletion. Every check reads the
same copy of the index it changes.

`publish` applies or removes a job against the shared index:

1. download the active build into a temporary directory
2. apply the staged chunks there, or remove the version
3. if that changed the index, upload the result as a new build and make it active
"""

import logging
import pathlib
import tempfile
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from app import config as app_config
from app.common import mongo
from app.index import builds, chunk_index, index_schema, tantivy_index
from app.indexing import jobs
from app.records import chunk_set_store, chunk_sets, ids

if TYPE_CHECKING:
    from types_boto3_s3 import S3Client

logger = logging.getLogger(__name__)


class ChunkSetNotFoundError(LookupError):
    """Nothing is staged under the job's key."""


class ChunkSetMismatchError(ValueError):
    """The staged chunks aren't what the job, or the index, expects."""


class DocumentApplier:
    """Implements `stages.Applier`."""

    def __init__(
        self,
        chunk_sets: chunk_set_store.ChunkSetStore,
        writer: chunk_index.ChunkWriter,
        strategy: str = index_schema.CHUNK_STRATEGY,
    ) -> None:
        self._chunk_sets = chunk_sets
        self._writer = writer
        self._strategy = strategy

    async def apply(self, job: jobs.ApplyJob) -> bool:
        """Idempotent: applying the same job again leaves the same entries.

        Skips a version older than the one indexed, so a late request can't
        undo a newer one.
        """
        if job.strategy != self._strategy:
            msg = f"this index holds {self._strategy!r} chunks, not {job.strategy!r}"
            raise ChunkSetMismatchError(msg)

        staged = await self._chunk_sets.get(job.chunk_set_key)

        if staged is None:
            msg = f"no chunk set staged at {job.chunk_set_key}"
            raise ChunkSetNotFoundError(msg)

        header = staged.header

        if not _is_jobs(staged, job):
            msg = f"the chunk set at {job.chunk_set_key} is not the job's"
            raise ChunkSetMismatchError(msg)

        latest = await self._writer.latest_version(job.document_id)

        if latest is not None and await self._is_superseded(job, latest):
            logger.info(
                "Skipped %s version %s (%s): version %s has been seen since",
                job.document_id,
                job.version_id,
                job.status,
                latest,
            )
            return False

        await self._writer.replace_document(header, staged.chunks)

        logger.info(
            "Applied %s version %s (%s): %d %s chunks",
            job.document_id,
            job.version_id,
            job.status,
            len(staged.chunks),
            job.strategy,
        )
        return True

    async def remove(self, job: jobs.RemoveJob) -> bool:
        """Idempotent: removing again, or an older version, changes nothing.

        A version not yet indexed is still recorded, so its apply arriving
        later is refused.
        """
        latest = await self._writer.latest_version(job.document_id)

        if latest is not None and await self._is_superseded(job, latest):
            logger.info(
                "Nothing to remove for %s version %s: %s was seen",
                job.document_id,
                job.version_id,
                latest,
            )
            return False

        await self._writer.remove_version(job.document_id, job.version_id)

        logger.info("Removed %s version %s", job.document_id, job.version_id)
        return True

    async def _is_superseded(
        self,
        job: jobs.ApplyJob | jobs.RemoveJob,
        latest: ids.VersionId,
    ) -> bool:
        """Older than the latest, or the latest itself with its content gone."""
        if ids.is_older(job.version_id, latest):
            return True

        return (
            job.version_id == latest
            and await self._writer.indexed_version(job.document_id) != latest
        )


def _is_jobs(staged: chunk_sets.ChunkSet, job: jobs.ApplyJob) -> bool:
    header = staged.header

    return (header.document_id, header.version_id, header.status, staged.strategy) == (
        job.document_id,
        job.version_id,
        job.status,
        job.strategy,
    )


async def publish(
    job: jobs.WriteJob,
    index_builds: builds.IndexBuildService,
    applier_for: Callable[[pathlib.Path], DocumentApplier],
) -> builds.BuildId | None:
    """Applies or removes against the active build and publishes the result.

    Returns the new active build, or `None` when the job left the index
    unchanged, in which case nothing is uploaded. Raises `builds.LostSwapError`
    if another build became active meanwhile.
    """
    # Tantivy can still be touching files as the directory is removed. A failed
    # cleanup of scratch space must not replace the error that got us here.
    with tempfile.TemporaryDirectory(
        prefix="search-index-",
        ignore_cleanup_errors=True,
    ) as work:
        directory = pathlib.Path(work)
        base = await index_builds.load(directory)
        applier = applier_for(directory)

        match job:
            case jobs.ApplyJob():
                changed = await applier.apply(job)
            case jobs.RemoveJob():
                changed = await applier.remove(job)

        if not changed:
            return None

        return await index_builds.publish(directory, base)


Publish = Callable[[jobs.WriteJob], Awaitable[builds.BuildId | None]]


def publisher(
    config: app_config.AppConfig,
    client: S3Client,
    index_builds: builds.IndexBuildService,
) -> Publish:
    """`publish`, wired to the configured bucket and these builds."""

    def applier_for(index_dir: pathlib.Path) -> DocumentApplier:
        return from_config(config, client, index_dir)

    async def publish_job(job: jobs.WriteJob) -> builds.BuildId | None:
        return await publish(job, index_builds, applier_for)

    return publish_job


async def publish_from_config(
    config: app_config.AppConfig,
    client: S3Client,
    job: jobs.WriteJob,
) -> builds.BuildId | None:
    """`publish` for an entry point outside a request; connects to MongoDB."""
    database = await mongo.get_db(await mongo.get_mongo_client(config), config)
    index_builds = builds.get_index_build_service(database, config, client)
    return await publisher(config, client, index_builds)(job)


def from_config(
    config: app_config.AppConfig,
    client: S3Client,
    index_dir: pathlib.Path,
) -> DocumentApplier:
    return DocumentApplier(
        chunk_sets=chunk_set_store.S3ChunkSetStore(
            client,
            config.search_store_s3_bucket,
        ),
        writer=tantivy_index.TantivyChunkIndex(index_dir),
    )
