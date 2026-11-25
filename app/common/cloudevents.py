"""CloudEvents 1.0 in structured JSON mode, the format of every queue message."""

import typing
import uuid
from collections.abc import Collection, Mapping
from typing import Any, ClassVar, Final, Literal

import pydantic

SPEC_VERSION: Final = "1.0"
CONTENT_TYPE: Final = "application/cloudevents+json"

DATA_CONTENT_TYPE: Final = "application/json"
TYPE_PREFIX: Final = "uk.gov.defra.rpa.guidancehub"
UNKNOWN: Final = "unknown"


class CloudEvent(pydantic.BaseModel):
    """The context attributes of any CloudEvent, as optional as the spec allows.

    `cdprequestid` is the trace id.
    """

    model_config = pydantic.ConfigDict(extra="ignore", frozen=True)

    specversion: Literal["1.0"]
    id: str = pydantic.Field(min_length=1)
    source: str = pydantic.Field(min_length=1)
    type: str = pydantic.Field(min_length=1)
    subject: str | None = None
    time: pydantic.AwareDatetime | None = None
    datacontenttype: str | None = None
    dataschema: str | None = None
    cdprequestid: str | None = None


class UnknownEvent(CloudEvent):
    """A CloudEvent of a type its reader doesn't read."""

    data: Any = None


class KnownEvent(CloudEvent):
    """An event its reader reads: one version of one type, with JSON data.

    A subclass narrows `type` and `dataschema` to one literal each; together
    they are its `tag`, so each version of a type is a model of its own.
    """

    TYPE: ClassVar[str]

    time: pydantic.AwareDatetime = pydantic.Field(
        description=(
            "When this event was published. Sending the same change again is a"
            " new event, with a new time. Search doesn't order by it: a"
            " document's versions are ordered by their ids."
        )
    )
    datacontenttype: Literal["application/json"] = DATA_CONTENT_TYPE
    dataschema: str

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)

        cls.TYPE = _only_literal(cls, "type")
        _only_literal(cls, "dataschema")

    @classmethod
    def tag(cls) -> str:
        """The discriminator tag of this version of this type."""
        return tag(cls.TYPE, _only_literal(cls, "dataschema"))


def _only_literal(model: type[pydantic.BaseModel], field: str) -> str:
    annotation = model.model_fields[field].annotation
    values = typing.get_args(annotation)

    if typing.get_origin(annotation) is not Literal or len(values) != 1:
        msg = f"{model.__name__}.{field} must be a Literal of one string"
        raise TypeError(msg)

    value: str = values[0]

    return value


def tag(event_type: str, dataschema: str) -> str:
    """An event's discriminator tag: its type and the version of its data."""
    return f"{event_type} {dataschema}"


def discriminator(known: Collection[type[KnownEvent]]) -> pydantic.Discriminator:
    """Tags each event by its `type` and `dataschema` for a union of `known`.

    A type none of `known` has is tagged `UNKNOWN`, to be read as an
    `UnknownEvent` and ignored. A known type with a `dataschema` none of them
    has is tagged as it is, so validation refuses it: it is for this reader,
    which can't read that version yet.
    """
    known_types = {event.TYPE for event in known}

    def discriminate(value: Any) -> str:
        event_type = _attribute(value, "type")

        if not isinstance(event_type, str) or event_type not in known_types:
            return UNKNOWN

        dataschema = _attribute(value, "dataschema")

        return tag(event_type, dataschema if isinstance(dataschema, str) else "")

    return pydantic.Discriminator(discriminate)


def _attribute(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)

    return getattr(value, name, None)


def dumps(event: CloudEvent) -> str:
    """The event as a message body; optional attributes left out, not null."""
    return event.model_dump_json(by_alias=True, exclude_none=True)


def new_id() -> str:
    """A new UUIDv7 event id."""
    return str(uuid.uuid7())
