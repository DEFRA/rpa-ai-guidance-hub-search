"""The AsyncAPI document is generated from the event models, and kept current."""

import json

import pytest

from app.ingestion import models as ingestion_models
from scripts import export_asyncapi

MESSAGES = ["guideVersionPublished", "guideVersionDeleted"]


@pytest.fixture(scope="module")
def document():
    return export_asyncapi.document()


def test_the_committed_document_is_current():
    assert export_asyncapi.main(["--check"]) == 0


@pytest.mark.parametrize(
    ("event", "data_model"),
    [
        (
            ingestion_models.GuideVersionPublished,
            ingestion_models.GuideVersionPublishedDataV1,
        ),
        (
            ingestion_models.GuideVersionDeleted,
            ingestion_models.GuideVersionDeletedDataV1,
        ),
    ],
)
def test_each_event_s_dataschema_is_the_id_of_its_data_schema_here(
    document, event, data_model
):
    """A producer-neutral URN, so another service can emit the same events."""
    dataschema = event.model_fields["dataschema"].annotation.__args__[0]
    envelope = document["components"]["schemas"][event.__name__]

    assert dataschema.startswith("urn:defra:rpa-ai-guidance-hub:events:")
    assert dataschema.endswith(":v1")
    assert document["components"]["schemas"][data_model.__name__]["$id"] == (dataschema)
    assert envelope["properties"]["data"] == {
        "$ref": f"#/components/schemas/{data_model.__name__}"
    }
    assert envelope["properties"]["dataschema"]["const"] == dataschema


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
