"""A shared S3 client, lazily built the same way `common.mongo` builds its client.

`floci_endpoint_url` is passed explicitly rather than relying on boto3's own
`AWS_ENDPOINT_URL` env-var detection: every other client in this app takes its
endpoint from `AppConfig` rather than an ambient env var, and doing the same here
keeps `get_s3_client` overridable in a test the way `get_mongo_client` already is.
`None` locally falls through to boto3's normal region/credential resolution, which
is what talking to real AWS in CDP needs.

Typed with `types-boto3`'s `S3Client` (a dev-only stub package, so imported only
for type checking).

**Each S3 call is capped, and tried once more.** A call is given 2.5 seconds to
connect and 2.5 to answer - half the 5 seconds the front end waits for a whole call
to this service (the UI's `guidanceApi.timeout`) - so that one stuck call cannot
hang a request. A failure that can be retried (standard mode: throttling, timeouts,
5xx) is tried once more; anything else, and a second failure, is raised to the
caller. The cap is per call, not per request: a request that makes many calls, as
storing a document does, can take longer than the front end waits.

One client serves every request and thread: boto3 clients are thread-safe, and its
connection pool (10 by default) is larger than the most writes a store makes at
once.
"""

from logging import getLogger
from typing import TYPE_CHECKING

import boto3
from botocore import exceptions as botocore_exceptions
from botocore.config import Config

from app import config as app_config

if TYPE_CHECKING:
    from types_boto3_s3 import S3Client

logger = getLogger(__name__)

TIMEOUT_SECONDS = 2.5
ATTEMPTS = 2
_MISSING = frozenset({"NoSuchKey", "404"})

client: S3Client | None = None


def get_s3_client() -> S3Client:
    global client

    if client is None:
        config = app_config.get_config()

        logger.info(
            "Creating S3 client%s",
            f" against {config.floci_endpoint_url}"
            if config.floci_endpoint_url
            else "",
        )

        client = boto3.client(
            "s3",
            endpoint_url=config.floci_endpoint_url,
            region_name=config.aws_region,
            config=Config(
                connect_timeout=TIMEOUT_SECONDS,
                read_timeout=TIMEOUT_SECONDS,
                retries={"total_max_attempts": ATTEMPTS, "mode": "standard"},
            ),
        )

    return client


def is_missing(error: botocore_exceptions.ClientError) -> bool:
    """The object (or bucket) isn't there, as opposed to any other failure."""
    return error.response.get("Error", {}).get("Code") in _MISSING
