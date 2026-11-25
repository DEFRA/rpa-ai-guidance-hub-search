#!/usr/bin/env python3
"""Write the AsyncAPI document of the service's queues to `docs/asyncapi.json` and `.yaml`, or check them."""

import argparse
import datetime as dt
import json
import pathlib
import sys
import uuid
from collections.abc import Iterator
from typing import Any

import pydantic
import yaml
from pydantic import json_schema

from app.common import cloudevents
from app.ingestion import models as ingestion_models

ASYNCAPI_PATH = (
    pathlib.Path(__file__).resolve().parent.parent / "docs" / "asyncapi.json"
)

ASYNCAPI_VERSION = "3.0.0"
DOCUMENT_VERSION = "1.0.0"
"""The contract's version: the major version matches the schemas' `V1`."""

SQS_BINDING_VERSION = "0.2.0"
"""The newest SQS binding the AsyncAPI 3.0.0 schema accepts."""

QUEUE = "rpa_ai_guidance_hub_search_indexing"
DEAD_LETTER_QUEUE = f"{QUEUE}-deadletter"
VISIBILITY_TIMEOUT_SECONDS = 60
MAX_RECEIVE_COUNT = 3

SCHEMAS = "#/components/schemas/{model}"

EXAMPLE_DOCUMENT_ID = uuid.UUID("0199b8a2-0000-7000-8000-00000000f001")
EXAMPLE_VERSION_ID = uuid.UUID("0199b8a2-0000-7000-8000-00000000f002")
EXAMPLE_TIME = dt.datetime(2026, 11, 12, 10, tzinfo=dt.UTC)
EXAMPLE_EVENT_ID = "0199c1d4-7a2e-7c3b-9f00-3b5a1e2d4c6f"
"""Fixed, unlike a real event's, so the document is the same every time."""


def render() -> str:
    return json.dumps(document(), indent=2, sort_keys=True) + "\n"


def render_yaml() -> str:
    return yaml.safe_dump(document(), sort_keys=True, allow_unicode=True, width=88)


def document() -> dict[str, Any]:
    return {
        "asyncapi": ASYNCAPI_VERSION,
        "info": {
            "title": "RPA AI Guidance Hub search: indexing events",
            "version": DOCUMENT_VERSION,
            "description": (
                "Guide events the search service indexes. They are the hub's "
                "events, not search's: rpa-ai-guidance-hub-api will publish them "
                "to its guide-events topic, which delivers them to the indexing "
                "queue, and until it does, search's own ingestion routes send the "
                "same events. Each message is a CloudEvents 1.0 event in "
                "structured JSON mode; `source` names whichever service sent it. "
                "Within v1, changes are additive and readers ignore what they "
                "don't know; a breaking change to `data` is a new schema, whose "
                "URN `dataschema` names (`…:v2`)."
            ),
        },
        "defaultContentType": cloudevents.CONTENT_TYPE,
        "channels": {
            "indexing": {
                "address": QUEUE,
                "title": "Indexing queue",
                "description": (
                    "A standard SQS queue. A message that fails is received again "
                    f"after {VISIBILITY_TIMEOUT_SECONDS} seconds, and dead-lettered "
                    f"after {MAX_RECEIVE_COUNT} receives. An event of a type search "
                    "doesn't read is acknowledged and ignored."
                ),
                "messages": {
                    name: {"$ref": f"#/components/messages/{name}"}
                    for name in _messages()
                },
                "bindings": {"sqs": _sqs_binding()},
            },
        },
        "operations": {
            "sendIndexingEvent": _operation(
                "send",
                "Search's ingestion routes send an event for each request, "
                "until the API publishes them.",
            ),
            "receiveIndexingEvent": _operation(
                "receive", "The preparation worker prepares or removes each version."
            ),
        },
        "components": {
            "messages": _messages(),
            "schemas": _schemas(),
        },
    }


def _operation(action: str, summary: str) -> dict[str, Any]:
    return {
        "action": action,
        "summary": summary,
        "channel": {"$ref": "#/channels/indexing"},
        "messages": [
            {"$ref": f"#/channels/indexing/messages/{name}"} for name in _messages()
        ],
    }


def _sqs_binding() -> dict[str, Any]:
    return {
        "bindingVersion": SQS_BINDING_VERSION,
        "queue": {
            "name": QUEUE,
            "fifoQueue": False,
            "visibilityTimeout": VISIBILITY_TIMEOUT_SECONDS,
            "redrivePolicy": {
                "deadLetterQueue": {"name": DEAD_LETTER_QUEUE},
                "maxReceiveCount": MAX_RECEIVE_COUNT,
            },
        },
        "deadLetterQueue": {"name": DEAD_LETTER_QUEUE, "fifoQueue": False},
    }


def _messages() -> dict[str, Any]:
    return {
        "guideVersionPublishedV1": _message(
            "Guide version published (v1)",
            ingestion_models.GuideVersionPublishedV1,
            _published_example(),
        ),
        "guideVersionDeletedV1": _message(
            "Guide version deleted (v1)",
            ingestion_models.GuideVersionDeletedV1,
            _deleted_example(),
        ),
    }


def _message(
    title: str,
    model: type[ingestion_models.GuideEvent],
    example: ingestion_models.GuideEvent,
) -> dict[str, Any]:
    return {
        "name": model.TYPE,
        "title": title,
        "summary": (model.__doc__ or "").strip(),
        "contentType": cloudevents.CONTENT_TYPE,
        "payload": {"$ref": SCHEMAS.format(model=model.__name__)},
        "examples": [
            {"name": title, "payload": json.loads(cloudevents.dumps(example))},
        ],
    }


def _schemas() -> dict[str, Any]:
    """Each event's schema, and the schemas they refer to in `components.schemas`.

    A data schema has its URN as its `$id`, which makes it a resource of its own:
    to JSON Schema a `$ref` inside it resolves against the URN, while AsyncAPI's
    parser resolves it against this document. So each is generated alone, with
    what it refers to inlined, leaving no reference for either to resolve.
    """
    events = ingestion_models.KNOWN_EVENTS

    _, definitions = json_schema.models_json_schema(
        [(event, "validation") for event in events],
        by_alias=True,
        ref_template=SCHEMAS,
    )
    schemas: dict[str, Any] = definitions["$defs"]

    for event in events:
        data_model = _data_model(event)
        schemas[data_model.__name__] = _inlined(
            data_model.model_json_schema(by_alias=True)
        )

    used = _reachable(schemas, [event.__name__ for event in events])

    return {name: schema for name, schema in schemas.items() if name in used}


def _data_model(event: type[cloudevents.KnownEvent]) -> type[pydantic.BaseModel]:
    annotation = event.model_fields["data"].annotation

    if not isinstance(annotation, type) or not issubclass(
        annotation, pydantic.BaseModel
    ):
        msg = f"{event.__name__}.data must be a pydantic model"
        raise TypeError(msg)

    return annotation


def _inlined(schema: dict[str, Any]) -> dict[str, Any]:
    """The schema with each `#/$defs/…` reference replaced by its definition."""
    definitions = schema.pop("$defs", {})

    def inline(node: Any) -> Any:
        if isinstance(node, list):
            return [inline(value) for value in node]

        if not isinstance(node, dict):
            return node

        ref = node.get("$ref")

        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            return inline(definitions[ref.removeprefix("#/$defs/")])

        return {key: inline(value) for key, value in node.items()}

    inlined: dict[str, Any] = inline(schema)

    return inlined


def _reachable(schemas: dict[str, Any], roots: list[str]) -> set[str]:
    """The schemas `roots` refer to through `components.schemas`, and themselves."""
    seen: set[str] = set()
    pending = list(roots)

    while pending:
        name = pending.pop()

        if name not in seen:
            seen.add(name)
            pending.extend(_component_refs(schemas[name]))

    return seen


def _component_refs(node: Any) -> Iterator[str]:
    prefix = SCHEMAS.format(model="")

    if isinstance(node, dict):
        ref = node.get("$ref")

        if isinstance(ref, str) and ref.startswith(prefix):
            yield ref.removeprefix(prefix)

        for value in node.values():
            yield from _component_refs(value)

    elif isinstance(node, list):
        for value in node:
            yield from _component_refs(value)


def _published_example() -> ingestion_models.GuideVersionPublishedV1:
    data = ingestion_models.GuideVersionPublishedDataV1.model_validate(
        {
            "documentId": str(EXAMPLE_DOCUMENT_ID),
            "versionId": str(EXAMPLE_VERSION_ID),
            "sourceType": "guide",
            "status": "published",
            "contentUrl": (
                "s3://rpa-ai-guidance-hub-managed-docs/"
                f"{EXAMPLE_DOCUMENT_ID}/{EXAMPLE_VERSION_ID}/content.md"
            ),
            "title": "Field Visit Guide",
            "versionNumber": "1.0",
            "metadata": {"owner": "Schemes team"},
        },
    )
    event = ingestion_models.guide_version_published(data, EXAMPLE_TIME)

    return event.model_copy(update={"id": EXAMPLE_EVENT_ID})


def _deleted_example() -> ingestion_models.GuideVersionDeletedV1:
    data = ingestion_models.GuideVersionDeletedDataV1(
        document_id=EXAMPLE_DOCUMENT_ID, version_id=EXAMPLE_VERSION_ID
    )
    event = ingestion_models.guide_version_deleted(data, EXAMPLE_TIME)

    return event.model_copy(update={"id": EXAMPLE_EVENT_ID})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if docs/asyncapi.json differs from what the models generate",
    )
    args = parser.parse_args(argv)

    outputs = {
        ASYNCAPI_PATH: render(),
        ASYNCAPI_PATH.with_suffix(".yaml"): render_yaml(),
    }

    if not args.check:
        for path, rendered in outputs.items():
            path.write_text(rendered)

        return 0

    stale = [
        path
        for path, rendered in outputs.items()
        if not path.exists() or path.read_text() != rendered
    ]

    for path in stale:
        sys.stderr.write(
            f"docs/{path.name} is out of date; run `uv run task asyncapi` "
            "and commit the result.\n"
        )

    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
