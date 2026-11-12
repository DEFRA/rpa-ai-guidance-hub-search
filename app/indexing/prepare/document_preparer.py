"""The one preparer: turns any source's version into sections and staged chunks.

A job's source reader turns the version into a `SourceDocument`, which becomes
canonical sections. The sections are stored, then chunked by the keyword
index's strategy and staged as a chunk set. The returned `ApplyJob` names that
chunk set; preparing never touches the index. Wiring lives in `from_config`.
"""

import asyncio
import logging
from collections.abc import Mapping

from app import config as app_config
from app.common import s3
from app.indexing import jobs
from app.indexing.prepare import (
    chunking,
    guide_content,
    guide_markdown,
    guide_source,
    packed_chunking,
    sectioning,
    sources,
)
from app.records import chunk_set_store, chunk_sets, models, section_store

logger = logging.getLogger(__name__)


class UnsupportedSourceError(ValueError):
    """No reader is wired for the job's source type."""


class DocumentPreparer:
    """Implements `stages.Preparer`."""

    def __init__(
        self,
        readers: Mapping[models.SourceType, sources.SourceReader],
        sections: section_store.SectionStore,
        strategy: chunking.ChunkingStrategy,
        chunk_sets: chunk_set_store.ChunkSetStore,
    ) -> None:
        self._readers = readers
        self._sections = sections
        self._strategy = strategy
        self._chunk_sets = chunk_sets

    async def prepare(self, job: jobs.IndexJob) -> jobs.ApplyJob:
        """Idempotent: the same job stores the same sections under the same keys.

        The `ApplyJob` takes its ids from the header the reader made, so a
        source that mints its version id while reading hands it on.

        The sections are stored before the chunks are staged, so nothing is
        ever applied that the canonical records can't rebuild.
        """
        reader = self._readers.get(job.source_type)

        if reader is None:
            msg = f"no reader for source type {job.source_type!r}"
            raise UnsupportedSourceError(msg)

        document = await reader.read(job)
        sections = sectioning.build(document)
        chunks = await asyncio.to_thread(self._strategy.chunk, sections)
        staged = chunk_sets.ChunkSet(
            document.header, self._strategy.name, tuple(chunks)
        )

        await self._sections.put(document.header, sections)
        key = await self._chunk_sets.put(staged)

        header = document.header
        logger.info(
            "Prepared %s %s version %s (%s): %d sections as %d %s chunks",
            job.source_type,
            header.document_id,
            header.version_id,
            header.status,
            len(sections),
            len(chunks),
            self._strategy.name,
        )

        return jobs.ApplyJob(
            document_id=header.document_id,
            version_id=header.version_id,
            status=header.status,
            occurred_at=header.occurred_at,
            strategy=self._strategy.name,
            chunk_set_key=key,
        )


def from_config() -> DocumentPreparer:
    config = app_config.get_config()
    client = s3.get_s3_client()
    guide_files = guide_content.S3ContentSource(client)

    return DocumentPreparer(
        readers={
            models.SourceType.GUIDE: guide_source.GuideSource(
                guide_files,
                guide_files,
                guide_markdown.GuideMarkdownParser(),
            ),
        },
        sections=section_store.S3SectionStore(client, config.search_store_s3_bucket),
        strategy=packed_chunking.PackedChunking(),
        chunk_sets=chunk_set_store.S3ChunkSetStore(
            client,
            config.search_store_s3_bucket,
        ),
    )
