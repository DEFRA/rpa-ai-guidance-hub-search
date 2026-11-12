"""The one applier: writes a prepared version's staged chunks to the index.

It reads only the chunk set the `ApplyJob` names, so it never needs a source,
the sectioning or a chunking strategy. It refuses chunks made by a strategy
other than the one the index schema holds (`index_schema.CHUNK_STRATEGY`).
Wiring lives in `from_config`.

`publish` applies a job to the shared index:

1. download the active build into a temporary directory
2. apply the staged chunks there
3. upload the result as a new build and make it active
"""

import logging
import pathlib
import tempfile
from collections.abc import Callable

from app import config as app_config
from app.common import mongo, s3
from app.index import builds, chunk_index, index_schema, tantivy_index
from app.indexing import jobs
from app.records import chunk_set_store, chunk_sets

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

    async def apply(self, job: jobs.ApplyJob) -> None:
        """Idempotent: applying the same job again leaves the same entries."""
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

        await self._writer.replace_document(header, staged.chunks)

        logger.info(
            "Applied %s version %s (%s): %d %s chunks",
            job.document_id,
            job.version_id,
            job.status,
            len(staged.chunks),
            job.strategy,
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
    job: jobs.ApplyJob,
    index_builds: builds.IndexBuildService,
    applier_for: Callable[[pathlib.Path], DocumentApplier],
) -> builds.BuildId:
    """Applies the job to the active build and publishes the result as active.

    Raises `builds.LostSwapError` if another build became active meanwhile.
    """
    # Tantivy can still be touching files as the directory is removed. A failed
    # cleanup of scratch space must not replace the error that got us here.
    with tempfile.TemporaryDirectory(
        prefix="search-index-",
        ignore_cleanup_errors=True,
    ) as work:
        directory = pathlib.Path(work)
        base = await index_builds.load(directory)
        await applier_for(directory).apply(job)
        return await index_builds.publish(directory, base)


async def publish_from_config(job: jobs.ApplyJob) -> builds.BuildId:
    database = await mongo.get_db(await mongo.get_mongo_client())
    return await publish(job, builds.get_index_build_service(database), from_config)


def from_config(index_dir: pathlib.Path) -> DocumentApplier:
    config = app_config.get_config()

    return DocumentApplier(
        chunk_sets=chunk_set_store.S3ChunkSetStore(
            s3.get_s3_client(),
            config.search_store_s3_bucket,
        ),
        writer=tantivy_index.TantivyChunkIndex(index_dir),
    )
