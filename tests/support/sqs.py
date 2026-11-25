"""An SQS client against moto, and what was sent to it."""

from collections.abc import Iterator
from typing import Any

import boto3
import moto
import pytest


@pytest.fixture
def moto_sqs() -> Iterator[Any]:
    """An SQS client against moto, for unit tests of what is sent."""
    with moto.mock_aws():
        yield boto3.client("sqs", region_name="eu-west-2")


def receive_now(client: Any, queue_url: str) -> list[dict[str, Any]]:
    """The messages visible now, with every attribute; one receive, no waiting."""
    response = client.receive_message(
        QueueUrl=queue_url,
        MaxNumberOfMessages=10,
        AttributeNames=["All"],
        MessageAttributeNames=["All"],
    )
    return response.get("Messages", [])
