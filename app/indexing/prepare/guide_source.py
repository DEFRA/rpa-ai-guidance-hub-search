"""Reads one guide version into a `SourceDocument`.

The content comes from the API's bucket, or inline from a request; the title
and metadata, when the caller has them, come from the CMS.
"""

import asyncio

from app.indexing import jobs
from app.indexing.prepare import guide_content, guide_markdown, sources
from app.records import source_metadata


class GuideSource:
    """Implements `sources.SourceReader` for `SourceType.GUIDE`."""

    def __init__(
        self,
        content_source: guide_content.ContentSource,
        section_id_source: guide_content.SectionIdSource,
        parser: guide_markdown.GuideMarkdownParser,
        metadata: source_metadata.GuideMetadata | None = None,
        title: str | None = None,
    ) -> None:
        self._content_source = content_source
        self._section_id_source = section_id_source
        self._parser = parser
        self._metadata = metadata
        self._title = title

    async def read(self, job: jobs.IndexJob) -> sources.SourceDocument:
        markdown = await self._content_source.read(job.content_url)
        section_ids = await self._section_id_source.read_section_ids(job.content_url)

        return await asyncio.to_thread(
            self._parser.parse, job, markdown, section_ids, self._metadata, self._title
        )
