#!/usr/bin/env python3
"""Write the AsyncAPI document of the service's queues to `docs/asyncapi.yaml`, or check it."""

import argparse
import dataclasses
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
from app.indexing import jobs
from app.indexing import models as indexing_models
from app.ingestion import models as ingestion_models
from app.records import ids, models

ASYNCAPI_PATH = (
    pathlib.Path(__file__).resolve().parent.parent / "docs" / "asyncapi.yaml"
)

ASYNCAPI_VERSION = "3.0.0"
DOCUMENT_VERSION = "1.0.0"

SQS_BINDING_VERSION = "0.2.0"

VISIBILITY_TIMEOUT_SECONDS = 60
MAX_RECEIVE_COUNT = 3

SCHEMAS = "#/components/schemas/{model}"

EXAMPLE_DOCUMENT_ID = ids.DocumentId(uuid.UUID("0199b8a2-0000-7000-8000-00000000f001"))
EXAMPLE_VERSION_ID = ids.VersionId(uuid.UUID("0199b8a2-0000-7000-8000-00000000f002"))
EXAMPLE_TIME = dt.datetime(2026, 11, 12, 10, tzinfo=dt.UTC)
EXAMPLE_EVENT_ID = "0199c1d4-7a2e-7c3b-9f00-3b5a1e2d4c6f"


@dataclasses.dataclass(frozen=True)
class Message:
    name: str
    title: str
    model: type[cloudevents.KnownEvent]
    example: cloudevents.KnownEvent


@dataclasses.dataclass(frozen=True)
class Channel:
    key: str
    queue: str
    title: str
    description: str
    sent_by: str
    received_by: str
    messages: tuple[Message, ...]
    fifo: bool = False

    @property
    def dead_letter_queue(self) -> str:
        suffix = ".fifo" if self.fifo else ""
        return f"{self.queue.removesuffix(suffix)}-deadletter{suffix}"


def channels() -> tuple[Channel, ...]:
    return (
        Channel(
            key="indexing",
            queue="rpa_ai_guidance_hub_search_indexing",
            title="Indexing queue",
            description="Guide events from the hub. Unknown event types are ignored.",
            sent_by="The ingestion routes, until the API publishes the events.",
            received_by="The preparation worker.",
            messages=(
                Message(
                    "guideVersionPublishedV1",
                    "Guide version published",
                    ingestion_models.GuideVersionPublishedV1,
                    _published_example(),
                ),
                Message(
                    "guideVersionDeletedV1",
                    "Guide version deleted",
                    ingestion_models.GuideVersionDeletedV1,
                    _deleted_example(),
                ),
            ),
        ),
        Channel(
            key="indexSync",
            queue="rpa_ai_guidance_hub_search_index_sync.fifo",
            title="Index sync queue",
            description=(
                "Work for the index's single writer, internal to search. One "
                f"message group (`{indexing_models.GROUP_ID}`); deduplicated by "
                "event `id`."
            ),
            sent_by="The preparation worker.",
            received_by="The index writer.",
            messages=(
                Message(
                    "guideVersionPreparedV1",
                    "Guide version prepared",
                    indexing_models.GuideVersionPreparedV1,
                    _index_sync_example(_apply_job()),
                ),
                Message(
                    "guideVersionRemovalRequestedV1",
                    "Guide version removal requested",
                    indexing_models.GuideVersionRemovalRequestedV1,
                    _index_sync_example(_remove_job()),
                ),
            ),
            fifo=True,
        ),
    )


def render() -> str:
    return yaml.safe_dump(document(), sort_keys=True, allow_unicode=True, width=88)


def document() -> dict[str, Any]:
    queues = channels()

    return {
        "asyncapi": ASYNCAPI_VERSION,
        "info": {
            "title": "RPA AI Guidance Hub search: queues",
            "version": DOCUMENT_VERSION,
            "description": (
                "The search service's queues. Messages are CloudEvents 1.0 in "
                "structured JSON mode."
            ),
        },
        "defaultContentType": cloudevents.CONTENT_TYPE,
        "channels": {channel.key: _channel(channel) for channel in queues},
        "operations": {
            name: operation
            for channel in queues
            for name, operation in _operations(channel).items()
        },
        "components": {
            "messages": {
                message.name: _message(message)
                for channel in queues
                for message in channel.messages
            },
            "schemas": _schemas(queues),
        },
    }


def _channel(channel: Channel) -> dict[str, Any]:
    return {
        "address": channel.queue,
        "title": channel.title,
        "description": (
            f"{channel.description} Dead-lettered after {MAX_RECEIVE_COUNT} receives."
        ),
        "messages": {
            message.name: {"$ref": f"#/components/messages/{message.name}"}
            for message in channel.messages
        },
        "bindings": {"sqs": _sqs_binding(channel)},
    }


def _operations(channel: Channel) -> dict[str, Any]:
    suffix = channel.key[0].upper() + channel.key[1:]

    return {
        f"send{suffix}Event": _operation(channel, "send", channel.sent_by),
        f"receive{suffix}Event": _operation(channel, "receive", channel.received_by),
    }


def _operation(channel: Channel, action: str, summary: str) -> dict[str, Any]:
    return {
        "action": action,
        "summary": summary,
        "channel": {"$ref": f"#/channels/{channel.key}"},
        "messages": [
            {"$ref": f"#/channels/{channel.key}/messages/{message.name}"}
            for message in channel.messages
        ],
    }


def _sqs_binding(channel: Channel) -> dict[str, Any]:
    queue: dict[str, Any] = {
        "name": channel.queue,
        "fifoQueue": channel.fifo,
        "visibilityTimeout": VISIBILITY_TIMEOUT_SECONDS,
        "redrivePolicy": {
            "deadLetterQueue": {"name": channel.dead_letter_queue},
            "maxReceiveCount": MAX_RECEIVE_COUNT,
        },
    }

    if channel.fifo:
        queue |= {"deduplicationScope": "queue", "fifoThroughputLimit": "perQueue"}

    return {
        "bindingVersion": SQS_BINDING_VERSION,
        "queue": queue,
        "deadLetterQueue": {
            "name": channel.dead_letter_queue,
            "fifoQueue": channel.fifo,
        },
    }


def _message(message: Message) -> dict[str, Any]:
    return {
        "name": message.model.TYPE,
        "title": message.title,
        "summary": (message.model.__doc__ or "").strip(),
        "contentType": cloudevents.CONTENT_TYPE,
        "payload": {"$ref": SCHEMAS.format(model=message.model.__name__)},
        "examples": [
            {
                "name": message.title,
                "payload": json.loads(cloudevents.dumps(message.example)),
            },
        ],
    }


def _schemas(queues: tuple[Channel, ...]) -> dict[str, Any]:
    """Each event's schema, and the schemas they refer to in `components.schemas`.

    A data schema has its URN as its `$id`, which makes it a resource of its own:
    to JSON Schema a `$ref` inside it resolves against the URN, while AsyncAPI's
    parser resolves it against this document. So each is generated alone, with
    what it refers to inlined, leaving no reference for either to resolve.
    """
    events = [message.model for channel in queues for message in channel.messages]

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
            "versionNumber": "1.0",
            "metadata": {"guideTitle": "Field Visit Guide", "owner": "Schemes team"},
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


def _apply_job() -> jobs.ApplyJob:
    return jobs.ApplyJob(
        document_id=EXAMPLE_DOCUMENT_ID,
        version_id=EXAMPLE_VERSION_ID,
        status=models.DocumentStatus.PUBLISHED,
        occurred_at=EXAMPLE_TIME,
        strategy="packed-800-v2",
        chunk_set_key=(
            f"staged/packed-800-v2/{EXAMPLE_DOCUMENT_ID}/{EXAMPLE_VERSION_ID}/"
            "published.jsonl"
        ),
    )


def _remove_job() -> jobs.RemoveJob:
    return jobs.RemoveJob(
        document_id=EXAMPLE_DOCUMENT_ID,
        version_id=EXAMPLE_VERSION_ID,
        occurred_at=EXAMPLE_TIME,
    )


def _index_sync_example(job: jobs.WriteJob) -> indexing_models.IndexSyncEvent:
    return indexing_models.from_job(job, "rpa-ai-guidance-hub-search-store")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if docs/asyncapi.yaml differs from what the models generate",
    )
    args = parser.parse_args(argv)

    rendered = render()

    if not args.check:
        ASYNCAPI_PATH.write_text(rendered)

        return 0

    if ASYNCAPI_PATH.exists() and ASYNCAPI_PATH.read_text() == rendered:
        return 0

    sys.stderr.write(
        f"docs/{ASYNCAPI_PATH.name} is out of date; run `uv run task asyncapi` "
        "and commit the result.\n"
    )

    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
