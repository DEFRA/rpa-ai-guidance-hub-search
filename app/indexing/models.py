"""The events on the index sync queue, which feeds the index's single writer."""

import datetime as dt
import json
import uuid
from typing import Annotated, Final, Literal

import pydantic
from pydantic import alias_generators

from app.common import cloudevents
from app.indexing import jobs
from app.records import ids, models

GROUP_ID = "index-writer"

GUIDE_VERSION_PREPARED: Final = (
    "uk.gov.defra.rpa.guidancehub.search.guide.version.prepared"
)
GUIDE_VERSION_REMOVAL_REQUESTED: Final = (
    "uk.gov.defra.rpa.guidancehub.search.guide.version.removal-requested"
)
GUIDE_VERSION_PREPARED_V1: Final = (
    "urn:defra:rpa-ai-guidance-hub-search:events:guide-version-prepared:v1"
)
GUIDE_VERSION_REMOVAL_REQUESTED_V1: Final = (
    "urn:defra:rpa-ai-guidance-hub-search:events:guide-version-removal-requested:v1"
)
PREPARATION_SOURCE = "/rpa-ai-guidance-hub-search/preparation"

_EVENT_IDS = uuid.UUID("0199c1d4-7a2e-7c3b-9f00-3b5a1e2d4c70")


class ChunkSetBucketNotAllowedError(ValueError):
    """The event names a chunk set outside the bucket this service stages in."""


class _DataModel(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(
        alias_generator=alias_generators.to_camel,
        populate_by_name=True,
        extra="ignore",
        frozen=True,
    )


class ChunkSetRef(_DataModel):
    bucket: str = pydantic.Field(min_length=1)
    key: str = pydantic.Field(min_length=1)


class GuideVersionPreparedDataV1(_DataModel):
    model_config = pydantic.ConfigDict(
        json_schema_extra={"$id": GUIDE_VERSION_PREPARED_V1}
    )

    document_id: ids.DocumentId
    version_id: ids.VersionId
    status: models.DocumentStatus
    strategy: str = pydantic.Field(min_length=1)
    chunk_set: ChunkSetRef


class GuideVersionRemovalRequestedDataV1(_DataModel):
    model_config = pydantic.ConfigDict(
        json_schema_extra={"$id": GUIDE_VERSION_REMOVAL_REQUESTED_V1}
    )

    document_id: ids.DocumentId
    version_id: ids.VersionId


class GuideVersionPreparedV1(cloudevents.KnownEvent):
    """A version's chunks are staged, ready for the writer to apply."""

    type: Literal["uk.gov.defra.rpa.guidancehub.search.guide.version.prepared"]
    dataschema: Literal[
        "urn:defra:rpa-ai-guidance-hub-search:events:guide-version-prepared:v1"
    ]
    data: GuideVersionPreparedDataV1

    def to_job(self, chunk_set_bucket: str) -> jobs.ApplyJob:
        """Raises if the chunk set isn't in `chunk_set_bucket`."""
        chunk_set = self.data.chunk_set

        if chunk_set.bucket != chunk_set_bucket:
            msg = f"chunk set {chunk_set.key} is not in {chunk_set_bucket}"
            raise ChunkSetBucketNotAllowedError(msg)

        return jobs.ApplyJob(
            document_id=self.data.document_id,
            version_id=self.data.version_id,
            status=self.data.status,
            occurred_at=self.time,
            strategy=self.data.strategy,
            chunk_set_key=chunk_set.key,
        )


class GuideVersionRemovalRequestedV1(cloudevents.KnownEvent):
    """A version is to be taken out of the index, and its staged files deleted."""

    type: Literal["uk.gov.defra.rpa.guidancehub.search.guide.version.removal-requested"]
    dataschema: Literal[
        "urn:defra:rpa-ai-guidance-hub-search:events:guide-version-removal-requested:v1"
    ]
    data: GuideVersionRemovalRequestedDataV1

    def to_job(self, chunk_set_bucket: str) -> jobs.RemoveJob:  # noqa: ARG002
        """The bucket is unused; it matches `GuideVersionPrepared.to_job`."""
        return jobs.RemoveJob(
            document_id=self.data.document_id,
            version_id=self.data.version_id,
            occurred_at=self.time,
        )


KNOWN_EVENTS: Final = (GuideVersionPreparedV1, GuideVersionRemovalRequestedV1)
"""Every version of every type the index sync queue's reader reads."""

IndexSyncEvent = Annotated[
    Annotated[GuideVersionPreparedV1, pydantic.Tag(GuideVersionPreparedV1.tag())]
    | Annotated[
        GuideVersionRemovalRequestedV1,
        pydantic.Tag(GuideVersionRemovalRequestedV1.tag()),
    ],
    cloudevents.discriminator(KNOWN_EVENTS),
]

_EVENTS: pydantic.TypeAdapter[IndexSyncEvent] = pydantic.TypeAdapter(IndexSyncEvent)


def loads(body: str) -> IndexSyncEvent:
    """The event in a message body; raises `pydantic.ValidationError` if invalid."""
    return _EVENTS.validate_json(body)


def from_job(
    job: jobs.WriteJob,
    chunk_set_bucket: str,
    trace_id: str | None = None,
) -> IndexSyncEvent:
    """The event for `job`; an apply's chunk set is in `chunk_set_bucket`."""
    match job:
        case jobs.ApplyJob():
            prepared = GuideVersionPreparedDataV1(
                document_id=job.document_id,
                version_id=job.version_id,
                status=job.status,
                strategy=job.strategy,
                chunk_set=ChunkSetRef(bucket=chunk_set_bucket, key=job.chunk_set_key),
            )
            return GuideVersionPreparedV1(
                specversion=cloudevents.SPEC_VERSION,
                id=_event_id(GUIDE_VERSION_PREPARED, prepared, job.occurred_at),
                source=PREPARATION_SOURCE,
                type=GUIDE_VERSION_PREPARED,
                subject=_subject(prepared),
                time=job.occurred_at,
                dataschema=GUIDE_VERSION_PREPARED_V1,
                cdprequestid=trace_id,
                data=prepared,
            )
        case jobs.RemoveJob():
            removal = GuideVersionRemovalRequestedDataV1(
                document_id=job.document_id, version_id=job.version_id
            )
            return GuideVersionRemovalRequestedV1(
                specversion=cloudevents.SPEC_VERSION,
                id=_event_id(GUIDE_VERSION_REMOVAL_REQUESTED, removal, job.occurred_at),
                source=PREPARATION_SOURCE,
                type=GUIDE_VERSION_REMOVAL_REQUESTED,
                subject=_subject(removal),
                time=job.occurred_at,
                dataschema=GUIDE_VERSION_REMOVAL_REQUESTED_V1,
                cdprequestid=trace_id,
                data=removal,
            )


def _event_id(
    event_type: str,
    data: GuideVersionPreparedDataV1 | GuideVersionRemovalRequestedDataV1,
    time: dt.datetime,
) -> str:
    content = json.dumps(
        [event_type, data.model_dump(mode="json", by_alias=True), time.isoformat()],
        sort_keys=True,
    )

    return str(uuid.uuid5(_EVENT_IDS, content))


def _subject(
    data: GuideVersionPreparedDataV1 | GuideVersionRemovalRequestedDataV1,
) -> str:
    return f"documents/{data.document_id}/versions/{data.version_id}"
