"""The AsyncAPI document is generated from the event models, and kept current."""

import json

import pytest

from app.ingestion import models as ingestion_models
from scripts import export_asyncapi

MESSAGES = ["guideVersionPublishedV1", "guideVersionDeletedV1"]


@pytest.fixture(scope="module")
def document():
    return export_asyncapi.document()


def test_the_committed_document_is_current():
    assert export_asyncapi.main(["--check"]) == 0


@pytest.mark.parametrize(
    ("event", "data_model"),
    [
        (
            ingestion_models.GuideVersionPublishedV1,
            ingestion_models.GuideVersionPublishedDataV1,
        ),
        (
            ingestion_models.GuideVersionDeletedV1,
            ingestion_models.GuideVersionDeletedDataV1,
        ),
    ],
)
def test_each_event_s_dataschema_is_the_id_of_its_data_schema_here(
    document, event, data_model
):
    """A producer-neutral URN, so another service can emit the same events."""
    schemas = document["components"]["schemas"]
    envelope = schemas[event.__name__]

    assert event.DATASCHEMA.startswith("urn:defra:rpa-ai-guidance-hub:events:")
    assert event.DATASCHEMA.endswith(":v1")
    assert schemas[data_model.__name__]["$id"] == event.DATASCHEMA
    assert envelope["properties"]["data"] == {
        "$ref": f"#/components/schemas/{data_model.__name__}"
    }
    assert envelope["properties"]["dataschema"]["const"] == event.DATASCHEMA


def _refs(node):
    if isinstance(node, dict):
        if isinstance(node.get("$ref"), str):
            yield node["$ref"]

        for value in node.values():
            yield from _refs(value)

    elif isinstance(node, list):
        for value in node:
            yield from _refs(value)


@pytest.mark.parametrize(
    "data_model",
    [
        ingestion_models.GuideVersionPublishedDataV1,
        ingestion_models.GuideVersionDeletedDataV1,
    ],
)
def test_each_data_schema_has_no_references(document, data_model):
    """Its `$id` makes it a resource of its own, which tools resolve differently.

    JSON Schema resolves a `$ref` in it against the URN, AsyncAPI's parser
    against the document, so it inlines what it uses instead.
    """
    schema = document["components"]["schemas"][data_model.__name__]

    assert list(_refs(schema)) == []
    assert "$defs" not in schema


def test_every_known_event_has_a_message(document):
    names = {message["name"] for message in document["components"]["messages"].values()}

    assert names == {event.TYPE for event in ingestion_models.KNOWN_EVENTS}


@pytest.mark.parametrize("name", MESSAGES)
def test_each_message_is_a_cloudevent_with_a_valid_example(document, name):
    message = document["components"]["messages"][name]

    (example,) = message["examples"]
    event = ingestion_models.loads(json.dumps(example["payload"]))

    assert message["contentType"] == "application/cloudevents+json"
    assert message["name"] == event.type


@pytest.mark.parametrize("action", ["send", "receive"])
def test_sends_and_receives_every_message(document, action):
    (operation,) = [
        op for op in document["operations"].values() if op["action"] == action
    ]

    assert len(operation["messages"]) == len(MESSAGES)
