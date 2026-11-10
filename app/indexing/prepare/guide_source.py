"""Reads one guide version from the API's bucket into a `SourceDocument`."""

import asyncio

from app.indexing import jobs
from app.indexing.prepare import guide_content, guide_markdown, sources


class GuideSource:
    """Implements `sources.SourceReader` for `SourceType.GUIDE`."""

    def __init__(
        self,
        content_source: guide_content.ContentSource,
        section_id_source: guide_content.SectionIdSource,
        parser: guide_markdown.GuideMarkdownParser,
    ) -> None:
        self._content_source = content_source
        self._section_id_source = section_id_source
        self._parser = parser

    async def read(self, job: jobs.IndexJob) -> sources.SourceDocument:
        markdown = await self._content_source.read(job.content_url)
        section_ids = await self._section_id_source.read_section_ids(job.content_url)
        return await asyncio.to_thread(self._parser.parse, job, markdown, section_ids)
