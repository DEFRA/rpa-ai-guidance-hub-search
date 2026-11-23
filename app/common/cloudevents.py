"""CloudEvents 1.0 in structured JSON mode, the format of every queue message."""

import uuid
from collections.abc import Callable, Collection, Mapping
from typing import Any, Final, Literal

import pydantic

SPEC_VERSION: Final = "1.0"
CONTENT_TYPE: Final = "application/cloudevents+json"

DATA_CONTENT_TYPE: Final = "application/json"
TYPE_PREFIX: Final = "uk.gov.defra.rpa.guidancehub"
UNKNOWN: Final = "unknown"


class CloudEvent(pydantic.BaseModel):
    """The context attributes every event has; `cdprequestid` is the trace id."""

    model_config = pydantic.ConfigDict(extra="ignore", frozen=True)

    specversion: Literal["1.0"]
    id: str = pydantic.Field(min_length=1)
    source: str = pydantic.Field(min_length=1)
    type: str
    subject: str | None = None
    time: pydantic.AwareDatetime
    datacontenttype: Literal["application/json"] = DATA_CONTENT_TYPE
    cdprequestid: str | None = None


class UnknownEvent(CloudEvent):
    """A CloudEvent of a type its reader doesn't read."""

    dataschema: str | None = None
    data: Any = None


def dumps(event: CloudEvent) -> str:
    """The event as a message body; optional attributes left out, not null."""
    return event.model_dump_json(by_alias=True, exclude_none=True)


def new_id() -> str:
    """A new UUIDv7 event id."""
    return str(uuid.uuid7())


def type_of(known: Collection[str]) -> Callable[[Any], str]:
    """A discriminator: an event's `type` if it is in `known`, else `UNKNOWN`."""

    def discriminate(value: Any) -> str:
        event_type = (
            value.get("type")
            if isinstance(value, Mapping)
            else getattr(value, "type", None)
        )

        return str(event_type) if event_type in known else UNKNOWN

    return discriminate
