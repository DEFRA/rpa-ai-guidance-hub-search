"""Every event subclasses one CloudEvents 1.0 base, written in structured mode."""

import datetime as dt
import json
import re
import uuid
from typing import Annotated, Literal

import pydantic
import pytest

from app.common import cloudevents

PING = "uk.gov.defra.rpa.guidancehub.test.ping"
REQUIRED_ATTRIBUTES = {"specversion", "id", "source", "type"}
ATTRIBUTE_NAME = re.compile(r"^[a-z0-9]{1,20}$")


class Ping(cloudevents.CloudEvent):
    """An event of the tests' own, as a sub-domain's `models` would define one."""

    type: Literal["uk.gov.defra.rpa.guidancehub.test.ping"]
    data: dict[str, str]


Event = Annotated[
    Annotated[Ping, pydantic.Tag(PING)]
    | Annotated[cloudevents.UnknownEvent, pydantic.Tag(cloudevents.UNKNOWN)],
    pydantic.Discriminator(cloudevents.type_of({PING})),
]
EVENTS: pydantic.TypeAdapter[Event] = pydantic.TypeAdapter(Event)


def _ping(**attributes):
    return Ping(
        **{
            "specversion": cloudevents.SPEC_VERSION,
            "id": "e-1",
            "source": "/tests",
            "type": PING,
            "time": dt.datetime(2026, 11, 12, 10, tzinfo=dt.UTC),
            "data": {"say": "hello"},
            **attributes,
        },
    )


def _written(event):
    return json.loads(cloudevents.dumps(event))


class TestWriting:
    def test_has_the_required_attributes_and_spec_version(self):
        written = _written(_ping())

        assert written.keys() >= REQUIRED_ATTRIBUTES
        assert written["specversion"] == "1.0"
        assert written["datacontenttype"] == "application/json"

    def test_names_every_attribute_as_the_spec_requires(self):
        written = _written(_ping(cdprequestid="t", subject="s"))

        assert [name for name in written if not ATTRIBUTE_NAME.match(name)] == []

    def test_leaves_out_unset_optional_attributes_rather_than_null(self):
        written = _written(_ping())

        assert "cdprequestid" not in written
        assert None not in written.values()

    def test_writes_time_as_rfc_3339_in_utc(self):
        assert _written(_ping())["time"] == "2026-11-12T10:00:00Z"


class TestReading:
    def test_reads_back_what_it_wrote(self):
        event = _ping(cdprequestid="t")

        assert EVENTS.validate_json(cloudevents.dumps(event)) == event

    @pytest.mark.parametrize("attribute", sorted(REQUIRED_ATTRIBUTES | {"time"}))
    def test_refuses_an_event_missing_a_required_attribute(self, attribute):
        written = _written(_ping())
        del written[attribute]

        with pytest.raises(pydantic.ValidationError):
            EVENTS.validate_json(json.dumps(written))

    def test_refuses_another_spec_version(self):
        written = {**_written(_ping()), "specversion": "0.3"}

        with pytest.raises(pydantic.ValidationError):
            EVENTS.validate_json(json.dumps(written))

    def test_refuses_a_time_without_a_zone(self):
        written = {**_written(_ping()), "time": "2026-11-12T10:00:00"}

        with pytest.raises(pydantic.ValidationError):
            EVENTS.validate_json(json.dumps(written))

    def test_ignores_extensions_it_does_not_know(self):
        written = {**_written(_ping()), "partitionkey": "p"}

        assert isinstance(EVENTS.validate_json(json.dumps(written)), Ping)

    def test_reads_an_unknown_type_as_unknown(self):
        written = {**_written(_ping()), "type": "uk.gov.defra.rpa.other"}

        event = EVENTS.validate_json(json.dumps(written))

        assert isinstance(event, cloudevents.UnknownEvent)
        assert event.type == "uk.gov.defra.rpa.other"


def test_new_ids_are_uuid7s_and_never_repeat():
    first, second = cloudevents.new_id(), cloudevents.new_id()

    assert first != second
    assert uuid.UUID(first).version == 7
