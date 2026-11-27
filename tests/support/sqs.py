"""SQS clients against moto and floci, and helpers for driving them."""

import asyncio
import time
from collections.abc import Callable, Iterator
from typing import Any

import boto3
import moto
import pytest

from app.common.sqs_consumer import config as consumer_config

QUICK_POLL = consumer_config.Receive(wait_time_seconds=1)


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


async def wait_for(predicate: Callable[[], object], *, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout

    while not predicate():
        if time.monotonic() > deadline:
            msg = f"condition not met within {timeout}s"
            raise AssertionError(msg)

        await asyncio.sleep(0.1)
