"""What the CMS sends to index a version, and what it gets back (camelCase)."""

import datetime as dt
import enum
from typing import Any, Literal

import pydantic
from pydantic import alias_generators

from app.ingestion import models
from app.records import ids
from app.records import models as record_models

INLINE_CONTENT_REFUSED = "inline content is no longer accepted; send contentUrl"


class _CamelModel(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(
        alias_generator=alias_generators.to_camel,
        populate_by_name=True,
        frozen=True,
    )


class IndexRequest(_CamelModel):
    """Index this published version, whose content is at `contentUrl`."""

    source_type: Literal[record_models.SourceType.GUIDE]
    status: Literal[record_models.DocumentStatus.PUBLISHED]
    occurred_at: pydantic.AwareDatetime
    content_url: models.S3Url
    version_number: str | None = None
    metadata: models.GuideMetadata

    @pydantic.model_validator(mode="before")
    @classmethod
    def _no_inline_content(cls, data: Any) -> Any:
        if isinstance(data, dict) and "content" in data:
            raise ValueError(INLINE_CONTENT_REFUSED)

        return data

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
