"""The guide events on the indexing queue."""

import datetime as dt
import uuid
from typing import TYPE_CHECKING, Annotated, Final, Literal

import pydantic
from pydantic import alias_generators

from app.common import cloudevents
from app.indexing import jobs
from app.indexing.prepare import guide_cms, guide_content, guide_markdown, guide_source
from app.records import ids, models

if TYPE_CHECKING:
    from types_boto3_s3 import S3Client

    from app.indexing.prepare import sources

GUIDE_VERSION_PUBLISHED: Final = "uk.gov.defra.rpa.guidancehub.guide.version.published"
GUIDE_VERSION_DELETED: Final = "uk.gov.defra.rpa.guidancehub.guide.version.deleted"
GUIDE_VERSION_PUBLISHED_V1: Final = (
    "urn:defra:rpa-ai-guidance-hub:events:guide-version-published:v1"
)
GUIDE_VERSION_DELETED_V1: Final = (
    "urn:defra:rpa-ai-guidance-hub:events:guide-version-deleted:v1"
)
INGESTION_SOURCE = "/rpa-ai-guidance-hub-search/ingestion"

NOT_BLANK_PATTERN: Final = r"\S"
"""Some text that isn't whitespace, for schemas."""

S3_URL_PATTERN: Final = r"^s3://[^/?#]+/+[^/?#]"
"""An `s3://bucket/key` URL with a key, for schemas: what `parse_s3_url` reads."""


class _DataModel(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(
        alias_generator=alias_generators.to_camel,
        populate_by_name=True,
        extra="ignore",
        frozen=True,
    )


class GuideMetadata(guide_cms.CmsGuideMetadata):
    """The CMS's metadata; `guideTitle` is required, and is the indexed title."""

    guide_title: Annotated[
        str,
        pydantic.StringConstraints(strip_whitespace=True, min_length=1),
        pydantic.Field(json_schema_extra={"pattern": NOT_BLANK_PATTERN}),
    ]


def _s3_url(url: str) -> str:
    guide_content.parse_s3_url(url)

    return url


S3Url = Annotated[
    str,
    pydantic.AfterValidator(_s3_url),
    pydantic.Field(json_schema_extra={"pattern": S3_URL_PATTERN}),
]


class GuideVersionPublishedDataV1(_DataModel):
    model_config = pydantic.ConfigDict(
        json_schema_extra={"$id": GUIDE_VERSION_PUBLISHED_V1}
    )

    document_id: guide_cms.CmsId
    version_id: guide_cms.CmsId
    source_type: Literal[models.SourceType.GUIDE]
    status: Literal[models.DocumentStatus.PUBLISHED]
    content_url: S3Url
    version_number: str | None = None
    metadata: GuideMetadata


class GuideVersionDeletedDataV1(_DataModel):
    model_config = pydantic.ConfigDict(
        json_schema_extra={"$id": GUIDE_VERSION_DELETED_V1}
    )

    document_id: guide_cms.CmsId
    version_id: guide_cms.CmsId


class GuideVersionPublishedV1(cloudevents.KnownEvent):
    """A version of a guide was published, so it should be what search finds."""

    type: Literal["uk.gov.defra.rpa.guidancehub.guide.version.published"]
    dataschema: Literal[
        "urn:defra:rpa-ai-guidance-hub:events:guide-version-published:v1"
    ]
    data: GuideVersionPublishedDataV1

    @property
    def document_id(self) -> ids.DocumentId:
        return ids.DocumentId(self.data.document_id)

    @property
    def version_id(self) -> ids.VersionId:
        return ids.VersionId(self.data.version_id)

    def to_job(self) -> jobs.IndexJob:
        return jobs.IndexJob(
            source_type=self.data.source_type,
            document_id=self.document_id,
            version_id=self.version_id,
            status=self.data.status,
            content_url=self.data.content_url,
            occurred_at=self.time,
        )

    def to_reader(self, client: S3Client) -> sources.SourceReader:
        """Reads the version's content from S3, with its title and metadata."""
        metadata = guide_cms.version_metadata(
            self.data.metadata, self.data.version_number
        )

        return guide_source.GuideSource(
            guide_content.S3ContentSource(client),
            guide_markdown.GuideMarkdownParser(),
            metadata,
            self.data.metadata.guide_title,
        )


class GuideVersionDeletedV1(cloudevents.KnownEvent):
    """A version of a guide was deleted, at every status."""

    type: Literal["uk.gov.defra.rpa.guidancehub.guide.version.deleted"]
    dataschema: Literal["urn:defra:rpa-ai-guidance-hub:events:guide-version-deleted:v1"]
    data: GuideVersionDeletedDataV1

    @property
    def document_id(self) -> ids.DocumentId:
        return ids.DocumentId(self.data.document_id)

    @property
    def version_id(self) -> ids.VersionId:
        return ids.VersionId(self.data.version_id)

    def to_job(self) -> jobs.RemoveJob:
        return jobs.RemoveJob(
            document_id=self.document_id,
            version_id=self.version_id,
            occurred_at=self.time,
        )


GuideEvent = GuideVersionPublishedV1 | GuideVersionDeletedV1
"""An event the ingestion routes send."""

KNOWN_EVENTS: Final = (GuideVersionPublishedV1, GuideVersionDeletedV1)
"""Every version of every type the indexing queue's reader reads."""

IndexingEvent = Annotated[
    Annotated[GuideVersionPublishedV1, pydantic.Tag(GuideVersionPublishedV1.tag())]
    | Annotated[GuideVersionDeletedV1, pydantic.Tag(GuideVersionDeletedV1.tag())]
    | Annotated[cloudevents.UnknownEvent, pydantic.Tag(cloudevents.UNKNOWN)],
    cloudevents.discriminator(KNOWN_EVENTS),
]
"""Any event on the indexing queue.

Each version of a type is a model of its own, tagged by its `type` and
`dataschema`. A new version is a new model here and in `KNOWN_EVENTS`,
converting to the same version-neutral job as the versions before it.
"""

_EVENTS: pydantic.TypeAdapter[IndexingEvent] = pydantic.TypeAdapter(IndexingEvent)


def loads(body: str) -> IndexingEvent:
    """The event in a message body; raises `pydantic.ValidationError` if invalid."""
    return _EVENTS.validate_json(body)


def message_attributes(event: GuideEvent) -> dict[str, str]:
    """Copies of a few attributes, for filtering; the body is authoritative."""
    values = {"type": event.type, "documentId": str(event.document_id)}

    if isinstance(event, GuideVersionPublishedV1):
        values["sourceType"] = event.data.source_type.value

    return values


def _subject(document_id: uuid.UUID, version_id: uuid.UUID) -> str:
    return f"documents/{document_id}/versions/{version_id}"


def guide_version_published(
    data: GuideVersionPublishedDataV1,
    time: dt.datetime,
    trace_id: str | None = None,
) -> GuideVersionPublishedV1:
    """A new event, as the ingestion routes send it."""
    return GuideVersionPublishedV1(
        specversion=cloudevents.SPEC_VERSION,
        type=GUIDE_VERSION_PUBLISHED,
        dataschema=GUIDE_VERSION_PUBLISHED_V1,
        id=cloudevents.new_id(),
        source=INGESTION_SOURCE,
        subject=_subject(data.document_id, data.version_id),
        time=time,
        cdprequestid=trace_id,
        data=data,
    )


def guide_version_deleted(
    data: GuideVersionDeletedDataV1,
    time: dt.datetime,
    trace_id: str | None = None,
) -> GuideVersionDeletedV1:
    """A new event, as the ingestion routes send it."""
    return GuideVersionDeletedV1(
        specversion=cloudevents.SPEC_VERSION,
        type=GUIDE_VERSION_DELETED,
        dataschema=GUIDE_VERSION_DELETED_V1,
        id=cloudevents.new_id(),
        source=INGESTION_SOURCE,
        subject=_subject(data.document_id, data.version_id),
        time=time,
        cdprequestid=trace_id,
        data=data,
    )
