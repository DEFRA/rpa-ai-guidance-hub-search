"""The AsyncAPI document is generated from the event models, and kept current."""

import json
import uuid

import jsonschema
import pydantic
import pytest

from app.ingestion import models as ingestion_models
from scripts import export_asyncapi
from tests.support import events

MESSAGES = ["guideVersionPublishedV1", "guideVersionDeletedV1"]

EVENTS_AND_DATA = [
    (
        ingestion_models.GuideVersionPublishedV1,
        ingestion_models.GuideVersionPublishedDataV1,
    ),
    (
        ingestion_models.GuideVersionDeletedV1,
        ingestion_models.GuideVersionDeletedDataV1,
    ),
]

UUID4 = str(uuid.UUID("0199b8a2-0000-4000-8000-00000000f001"))


@pytest.fixture
def document():
    return export_asyncapi.document()


def _refs(node):
    if isinstance(node, dict):
        if isinstance(node.get("$ref"), str):
            yield node["$ref"]

        for value in node.values():
            yield from _refs(value)

    elif isinstance(node, list):
        for value in node:
            yield from _refs(value)


class TestTheCommittedDocument:
    def test_is_current(self):
        assert export_asyncapi.main(["--check"]) == 0


class TestDataSchemas:
    @pytest.mark.parametrize(("event", "data_model"), EVENTS_AND_DATA)
    def test_each_event_s_dataschema_is_the_id_of_its_data_schema_here(
        self, document, event, data_model
    ):
        """A producer-neutral URN, so another service can emit the same events."""
        schemas = document["components"]["schemas"]
        envelope = schemas[event.__name__]
        dataschema = envelope["properties"]["dataschema"]["const"]

        assert dataschema.startswith("urn:defra:rpa-ai-guidance-hub:events:")
        assert dataschema.endswith(":v1")
        assert schemas[data_model.__name__]["$id"] == dataschema
        assert envelope["properties"]["data"] == {
            "$ref": f"#/components/schemas/{data_model.__name__}"
        }

    @pytest.mark.parametrize("data_model", [data for _, data in EVENTS_AND_DATA])
    def test_each_has_no_references(self, document, data_model):
        """Its `$id` makes it a resource of its own, which tools resolve differently.

        JSON Schema resolves a `$ref` in it against the URN, AsyncAPI's parser
        against the document, so it inlines what it uses instead.
        """
        schema = document["components"]["schemas"][data_model.__name__]

        assert list(_refs(schema)) == []
        assert "$defs" not in schema


class TestTheContractAgreesWithTheReader:
    """What the published data schemas refuse, search refuses, and vice versa.

    Another producer follows the contract, so a check search makes that the
    contract doesn't show would refuse events the contract allows.
    """

    @pytest.mark.parametrize(
        "event",
        [events.published(), events.deleted()],
        ids=["published", "deleted"],
    )
    def test_both_accept_a_valid_event(self, document, event):
        written = json.loads(event.model_dump_json(by_alias=True, exclude_none=True))

        _schema_for(document, event).validate(written["data"])
        ingestion_models.loads(json.dumps(written))

    @pytest.mark.parametrize(
        ("event", "changes"),
        [
            pytest.param(
                events.published(),
                {"contentUrl": "https://example.com/content.md"},
                id="https-content-url",
            ),
            pytest.param(
                events.published(),
                {"contentUrl": "s3://bucket"},
                id="content-url-without-key",
            ),
            pytest.param(
                events.published(),
                {"contentUrl": "s3://bucket/"},
                id="content-url-with-empty-key",
            ),
            pytest.param(
                events.published(), {"documentId": UUID4}, id="published-uuid4-doc"
            ),
            pytest.param(
                events.published(), {"versionId": UUID4}, id="published-uuid4-version"
            ),
            pytest.param(
                events.deleted(), {"documentId": UUID4}, id="deleted-uuid4-doc"
            ),
            pytest.param(
                events.deleted(), {"versionId": UUID4}, id="deleted-uuid4-version"
            ),
            pytest.param(
                events.published(),
                {"title": "   "},
                id="blank-title",
            ),
        ],
    )
    def test_both_refuse_an_invalid_event(self, document, event, changes):
        written = json.loads(event.model_dump_json(by_alias=True, exclude_none=True))
        written["data"] = {**written["data"], **changes}

        assert not _schema_for(document, event).is_valid(written["data"])

        with pytest.raises(pydantic.ValidationError):
            ingestion_models.loads(json.dumps(written))


def _schema_for(document, event):
    data_model = type(event).model_fields["data"].annotation
    schema = document["components"]["schemas"][data_model.__name__]

    return jsonschema.Draft202012Validator(schema)


class TestMessages:
    def test_every_known_event_has_one(self, document):
        names = {
            message["name"] for message in document["components"]["messages"].values()
        }

        assert names == {event.TYPE for event in ingestion_models.KNOWN_EVENTS}

    @pytest.mark.parametrize("name", MESSAGES)
    def test_each_is_a_cloudevent_with_a_valid_example(self, document, name):
        message = document["components"]["messages"][name]

        (example,) = message["examples"]
        event = ingestion_models.loads(json.dumps(example["payload"]))

        assert message["contentType"] == "application/cloudevents+json"
        assert message["name"] == event.type


class TestOperations:
    @pytest.mark.parametrize("action", ["send", "receive"])
    def test_send_and_receive_every_message(self, document, action):
        (operation,) = [
            op for op in document["operations"].values() if op["action"] == action
        ]

        assert len(operation["messages"]) == len(MESSAGES)
