from collections.abc import Iterator

import boto3
import moto
import pytest
from types_boto3_sqs import client as sqs_client

from tests.unit.common.sqs_consumer.support import events, iam, sqs


@pytest.fixture(autouse=True)
def aws_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fake credentials so no test can reach a real AWS account or profile."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)


@pytest.fixture(autouse=True)
def detach_error_collectors() -> Iterator[None]:
    """Events attaches a logging handler per instance; drop them after each test."""
    yield
    events.detach_collectors()


@pytest.fixture
def client() -> Iterator[sqs_client.SQSClient]:
    with moto.mock_aws():
        yield boto3.client("sqs")


@pytest.fixture
def queue_url(client: sqs_client.SQSClient) -> str:
    return sqs.create_queue(client)


@pytest.fixture
def iam_enforcement() -> Iterator[iam.IamEnforcement]:
    enforcement = iam.IamEnforcement()
    yield enforcement
    enforcement.disable()
