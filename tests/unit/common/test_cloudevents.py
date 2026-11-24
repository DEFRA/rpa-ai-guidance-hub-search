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
PING_V1 = "urn:defra:rpa-ai-guidance-hub:events:test-ping:v1"
PING_V2 = "urn:defra:rpa-ai-guidance-hub:events:test-ping:v2"
REQUIRED_ATTRIBUTES = {"specversion", "id", "source", "type"}
ATTRIBUTE_NAME = re.compile(r"^[a-z0-9]{1,20}$")


class Ping(cloudevents.KnownEvent):
    """An event of the tests' own, as a sub-domain's `models` would define one."""

    type: Literal["uk.gov.defra.rpa.guidancehub.test.ping"]
    dataschema: Literal["urn:defra:rpa-ai-guidance-hub:events:test-ping:v1"]
    data: dict[str, str]


class PingV2(cloudevents.KnownEvent):
    """The same type's next version: a model of its own."""

    type: Literal["uk.gov.defra.rpa.guidancehub.test.ping"]
    dataschema: Literal["urn:defra:rpa-ai-guidance-hub:events:test-ping:v2"]
    data: dict[str, int]


Event = Annotated[
    Annotated[Ping, pydantic.Tag(Ping.tag())]
    | Annotated[PingV2, pydantic.Tag(PingV2.tag())]
    | Annotated[cloudevents.UnknownEvent, pydantic.Tag(cloudevents.UNKNOWN)],
    cloudevents.discriminator([Ping, PingV2]),
]
EVENTS: pydantic.TypeAdapter[Event] = pydantic.TypeAdapter(Event)


def _ping(**attributes):
    return Ping(
        **{
            "specversion": cloudevents.SPEC_VERSION,
            "id": "e-1",
            "source": "/tests",
            "type": PING,
            "dataschema": PING_V1,
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
        written = _written(_ping())

        assert written["time"] == "2026-11-12T10:00:00Z"


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

        event = EVENTS.validate_json(json.dumps(written))

        assert isinstance(event, Ping)

    def test_reads_an_unknown_type_as_unknown(self):
        written = {**_written(_ping()), "type": "uk.gov.defra.rpa.other"}

        event = EVENTS.validate_json(json.dumps(written))

        assert isinstance(event, cloudevents.UnknownEvent)
        assert event.type == "uk.gov.defra.rpa.other"

    def test_reads_an_unknown_type_with_only_the_required_attributes(self):
        """The spec makes `time` and `datacontenttype` optional; so does `UnknownEvent`.

        Only the events a reader knows require a time and JSON data.
        """
        written = {
            "specversion": "1.0",
            "id": "e-2",
            "source": "/elsewhere",
            "type": "uk.gov.defra.rpa.other",
            "datacontenttype": "text/plain",
            "data": "hello",
        }

        event = EVENTS.validate_json(json.dumps(written))

        assert isinstance(event, cloudevents.UnknownEvent)
        assert event.time is None

    def test_reads_each_version_of_a_type_as_its_own_model(self):
        written = {**_written(_ping()), "dataschema": PING_V2, "data": {"say": 1}}

        event = EVENTS.validate_json(json.dumps(written))

        assert isinstance(event, PingV2)

    @pytest.mark.parametrize(
        "dataschema",
        [
            pytest.param("urn:defra:rpa-ai-guidance-hub:events:test-ping:v3", id="v3"),
            pytest.param(None, id="none"),
        ],
    )
    def test_refuses_a_known_type_in_a_version_it_does_not_read(self, dataschema):
        """The event is for this reader, so it fails, to be retried once it can."""
        written = {**_written(_ping()), "dataschema": dataschema}

        with pytest.raises(pydantic.ValidationError):
            EVENTS.validate_json(json.dumps(written))

    @pytest.mark.parametrize("event_type", [[], {}, 1, None])
    def test_refuses_a_type_that_is_not_a_string(self, event_type):
        written = {**_written(_ping()), "type": event_type}

        with pytest.raises(pydantic.ValidationError):
            EVENTS.validate_json(json.dumps(written))


class TestKnownEvents:
    def test_take_their_type_and_dataschema_from_their_literals(self):
        assert Ping.TYPE == PING
        assert Ping.tag() == cloudevents.tag(PING, PING_V1)
        assert Ping.tag() != PingV2.tag()

    def test_refuse_a_dataschema_that_is_not_one_literal(self):
        with pytest.raises(TypeError, match="dataschema"):

            class Loose(cloudevents.KnownEvent):
                type: Literal["uk.gov.defra.rpa.guidancehub.test.loose"]
                dataschema: str


class TestNewIds:
    def test_are_uuid7s_and_never_repeat(self):
        first, second = cloudevents.new_id(), cloudevents.new_id()

        assert first != second
        assert uuid.UUID(first).version == 7
