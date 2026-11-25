"""A shared SQS client, built like the S3 client."""

import logging
from typing import TYPE_CHECKING, Annotated

import boto3
import fastapi
from botocore import config as botocore_config

from app import config as app_config

if TYPE_CHECKING:
    from types_boto3_sqs import SQSClient

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_SECONDS = 2.5
READ_TIMEOUT_SECONDS = 25
ATTEMPTS = 2

client: SQSClient | None = None


def get_sqs_client(config: app_config.Config) -> SQSClient:
    """The process's one client, built from the config of the first call."""
    global client

    if client is None:
        logger.info(
            "Creating SQS client%s",
            f" against {config.floci_endpoint_url}"
            if config.floci_endpoint_url
            else "",
        )

        client = boto3.client(
            "sqs",
            endpoint_url=config.floci_endpoint_url,
            region_name=config.aws_region,
            config=botocore_config.Config(
                connect_timeout=CONNECT_TIMEOUT_SECONDS,
                read_timeout=READ_TIMEOUT_SECONDS,
                retries={"total_max_attempts": ATTEMPTS, "mode": "standard"},
            ),
        )

    return client


Client = Annotated["SQSClient", fastapi.Depends(get_sqs_client)]
"""Injects the shared client into a FastAPI route or dependency."""
