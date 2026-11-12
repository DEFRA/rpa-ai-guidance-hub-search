"""What the CMS sends to index a version, and what it gets back.

camelCase in and out, as the CMS writes it. A request converts itself into the
`IndexJob` and the reader that prepares it; routers only call the conversions.
"""

import datetime as dt
import enum
from typing import Annotated, Literal, Self

import pydantic
from pydantic import alias_generators

from app.common import s3
from app.indexing import jobs
from app.indexing.prepare import (
    guide_cms,
    guide_content,
    guide_markdown,
    guide_source,
    sources,
)
from app.records import ids, models


class _CamelModel(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(
        alias_generator=alias_generators.to_camel,
        populate_by_name=True,
        frozen=True,
    )


class InlineContent(_CamelModel):
    """The version's `content.md`, and the API's `{anchor: sectionId}` map."""

    markdown: str = pydantic.Field(min_length=1)
    section_ids: (
        dict[str, Annotated[str, pydantic.StringConstraints(min_length=1)]] | None
    ) = None


class IndexMetadata(guide_cms.CmsGuideMetadata):
    """The CMS's metadata; `guideTitle` is required, and is the indexed title."""

    guide_title: Annotated[
        str, pydantic.StringConstraints(strip_whitespace=True, min_length=1)
    ]


class IndexRequest(_CamelModel):
    """Index this published version. Exactly one of `contentUrl` or `content`.

    Only `published` is accepted: the index holds one version per document, so
    a version awaiting publication would replace the one readers can see.
    """

    source_type: models.SourceType
    status: Literal[models.DocumentStatus.PUBLISHED]
    occurred_at: pydantic.AwareDatetime
    content_url: str | None = None
    content: InlineContent | None = None
    version_number: str | None = None
    metadata: IndexMetadata

    @pydantic.model_validator(mode="after")
    def _one_source_of_content(self) -> Self:
        if (self.content_url is None) == (self.content is None):
            msg = "send exactly one of contentUrl or content"
            raise ValueError(msg)

        if self.content_url is not None:
            guide_content.parse_s3_url(self.content_url)

        return self

    def inline_bytes(self) -> int:
        """The size of the inline Markdown; 0 when the content is referenced."""
        return len(self.content.markdown.encode()) if self.content else 0

    def to_job(
        self,
        document_id: ids.DocumentId,
        version_id: ids.VersionId,
    ) -> jobs.IndexJob:
        """Inline content has no address; its `content_url` only records whence."""
        return jobs.IndexJob(
            source_type=self.source_type,
            document_id=document_id,
            version_id=version_id,
            status=self.status,
            content_url=self.content_url
            or f"cms://documents/{document_id}/versions/{version_id}",
            occurred_at=self.occurred_at,
        )

    def to_reader(self) -> sources.SourceReader:
        """Reads the inline content, or the referenced content from S3.

        The title is the CMS's `guideTitle`, never the Markdown's `# ` line.
        """
        parser = guide_markdown.GuideMarkdownParser()
        metadata = guide_cms.version_metadata(self.metadata, self.version_number)
        title = self.metadata.guide_title

        if self.content is None:
            files = guide_content.S3ContentSource(s3.get_s3_client())
            return guide_source.GuideSource(files, files, parser, metadata, title)

        section_ids = self.content.section_ids
        inline = guide_content.InlineContent(
            self.content.markdown,
            None
            if section_ids is None
            else {
                anchor: ids.SectionId(value) for anchor, value in section_ids.items()
            },
        )
        return guide_source.GuideSource(inline, inline, parser, metadata, title)


class Operation(enum.StrEnum):
    INDEX = "index"
    DELETE = "delete"


class Accepted(_CamelModel):
    """The request was accepted; the work happens after this reply."""

    document_id: ids.DocumentId
    version_id: ids.VersionId
    status: models.DocumentStatus | None
    operation: Operation


def remove_job(
    document_id: ids.DocumentId,
    version_id: ids.VersionId,
) -> jobs.RemoveJob:
    return jobs.RemoveJob(
        document_id=document_id,
        version_id=version_id,
        occurred_at=dt.datetime.now(dt.UTC),
    )
