"""Where a guide's `content.md` is read from: the API's managed-docs bucket,
or the request that carried it inline.

The API writes each version's content at `<documentId>/<versionId>/content.md`.
"""

import asyncio
import urllib.parse
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from types_boto3_s3 import S3Client


class ContentSource(Protocol):
    async def read(self, content_url: str) -> str: ...


def parse_s3_url(url: str) -> tuple[str, str]:
    """`s3://bucket/a/b` -> `("bucket", "a/b")`."""
    parts = urllib.parse.urlsplit(url)
    key = parts.path.lstrip("/")

    if parts.scheme != "s3" or not parts.netloc or not key:
        msg = f"not an s3://bucket/key URL: {url!r}"
        raise ValueError(msg)

    return parts.netloc, key


class S3ContentSource:
    """Reads content with the API's cross-service read grant."""

    def __init__(self, client: S3Client) -> None:
        self._client = client

    async def read(self, content_url: str) -> str:
        bucket, key = parse_s3_url(content_url)

        return await asyncio.to_thread(self._get, bucket, key)

    def _get(self, bucket: str, key: str) -> str:
        response = self._client.get_object(Bucket=bucket, Key=key)
        body: bytes = response["Body"].read()

        return body.decode("utf-8")


class InlineContent:
    """One version's content, sent with the request.

    Holds a single version, so it ignores `content_url`.
    """

    def __init__(self, markdown: str) -> None:
        self._markdown = markdown

    async def read(self, content_url: str) -> str:  # noqa: ARG002
        return self._markdown
