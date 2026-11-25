"""What the CMS sends to index a version, and what it gets back (camelCase)."""

import datetime as dt
import enum
from typing import Any, Literal, Self

import pydantic
from pydantic import alias_generators

from app.indexing.prepare import guide_cms
from app.ingestion import models
from app.records import ids
from app.records import models as record_models

INLINE_CONTENT_REFUSED = "inline content is no longer accepted; send contentUrl"
TITLE_MISSING = "send title, or metadata.guideTitle"


class _CamelModel(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(
        alias_generator=alias_generators.to_camel,
        populate_by_name=True,
        frozen=True,
    )


class IndexMetadata(guide_cms.CmsGuideMetadata):
    """The CMS's metadata. `guideTitle` is the title when `title` isn't sent."""

    guide_title: models.Title | None = None


class IndexRequest(_CamelModel):
    """Index this published version, whose content is at `contentUrl`."""

    source_type: Literal[record_models.SourceType.GUIDE]
    status: Literal[record_models.DocumentStatus.PUBLISHED]
    occurred_at: pydantic.AwareDatetime
    content_url: models.S3Url
    title: models.Title | None = None
    version_number: str | None = None
    metadata: IndexMetadata

    @pydantic.model_validator(mode="before")
    @classmethod
    def _no_inline_content(cls, data: Any) -> Any:
        if isinstance(data, dict) and "content" in data:
            raise ValueError(INLINE_CONTENT_REFUSED)

        return data

    @pydantic.model_validator(mode="after")
    def _a_title(self) -> Self:
        if self.title is None and self.metadata.guide_title is None:
            raise ValueError(TITLE_MISSING)

        return self

    @property
    def indexed_title(self) -> str:
        """`title`, else `metadata.guideTitle`, which the hub API sends until it
        sends `title`."""
        title = self.title or self.metadata.guide_title

        if title is None:
            raise ValueError(TITLE_MISSING)

        return title

    def to_event(
        self,
        document_id: ids.DocumentId,
        version_id: ids.VersionId,
        trace_id: str | None,
    ) -> models.GuideVersionPublishedV1:
        data = models.GuideVersionPublishedDataV1(
            document_id=document_id,
            version_id=version_id,
            source_type=self.source_type,
            status=self.status,
            content_url=self.content_url,
            title=self.indexed_title,
            version_number=self.version_number,
            metadata=self.metadata,
        )
        return models.guide_version_published(data, self.occurred_at, trace_id)


class Operation(enum.StrEnum):
    INDEX = "index"
    DELETE = "delete"


class Accepted(_CamelModel):
    """The request was accepted and queued; the work happens after this reply."""

    document_id: ids.DocumentId
    version_id: ids.VersionId
    status: record_models.DocumentStatus | None
    operation: Operation


def delete_event(
    document_id: ids.DocumentId,
    version_id: ids.VersionId,
    trace_id: str | None,
) -> models.GuideVersionDeletedV1:
    data = models.GuideVersionDeletedDataV1(
        document_id=document_id, version_id=version_id
    )
    return models.guide_version_deleted(data, dt.datetime.now(dt.UTC), trace_id)
