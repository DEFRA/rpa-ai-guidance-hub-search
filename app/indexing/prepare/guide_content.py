"""Where a guide's `content.md` is read from: the API's managed-docs bucket,
or the request that carried it inline.

The API writes each version's content at `<documentId>/<versionId>/content.md`.
When the API has section IDs for that version, it writes them beside the
content as `sections.json`, a flat `{anchor: sectionId}` map.
"""

import asyncio
import json
import urllib.parse
from collections.abc import Mapping
from typing import TYPE_CHECKING, Protocol

from botocore import exceptions as botocore_exceptions

from app.common import s3
from app.records import ids

if TYPE_CHECKING:
    from types_boto3_s3 import S3Client

SECTIONS_FILE = "sections.json"


class ContentSource(Protocol):
    async def read(self, content_url: str) -> str: ...


class SectionIdSource(Protocol):
    async def read_section_ids(
        self,
        content_url: str,
    ) -> Mapping[str, ids.SectionId] | None:
        """The API's section IDs by anchor, or `None` when it wrote none."""
        ...


def parse_s3_url(url: str) -> tuple[str, str]:
    """`s3://bucket/a/b` -> `("bucket", "a/b")`."""
    parts = urllib.parse.urlsplit(url)
    key = parts.path.lstrip("/")

    if parts.scheme != "s3" or not parts.netloc or not key:
        msg = f"not an s3://bucket/key URL: {url!r}"
        raise ValueError(msg)

    return parts.netloc, key


class S3ContentSource:
    """Reads content and section IDs with the API's cross-service read grant."""

    def __init__(self, client: S3Client) -> None:
        self._client = client

    async def read(self, content_url: str) -> str:
        bucket, key = parse_s3_url(content_url)

        return await asyncio.to_thread(self._get, bucket, key)

    async def read_section_ids(
        self,
        content_url: str,
    ) -> Mapping[str, ids.SectionId] | None:
        bucket, key = parse_s3_url(content_url)
        sections_key = f"{key.rpartition('/')[0]}/{SECTIONS_FILE}".lstrip("/")

        try:
            text = await asyncio.to_thread(self._get, bucket, sections_key)
        except botocore_exceptions.ClientError as e:
            if s3.is_missing(e):
                return None
            raise

        return _section_ids(text, f"s3://{bucket}/{sections_key}")

    def _get(self, bucket: str, key: str) -> str:
        response = self._client.get_object(Bucket=bucket, Key=key)
        body: bytes = response["Body"].read()

        return body.decode("utf-8")


class InlineContent:
    """One version's content and section IDs, sent with the request.

    Implements `ContentSource` and `SectionIdSource`. It holds a single
    version, so it ignores the `content_url` it is asked for.
    """

    def __init__(
        self,
        markdown: str,
        section_ids: Mapping[str, ids.SectionId] | None = None,
    ) -> None:
        self._markdown = markdown
        self._section_ids = section_ids

    async def read(self, content_url: str) -> str:  # noqa: ARG002
        return self._markdown

    async def read_section_ids(
        self,
        content_url: str,  # noqa: ARG002
    ) -> Mapping[str, ids.SectionId] | None:
        return self._section_ids


def _section_ids(text: str, url: str) -> dict[str, ids.SectionId]:
    parsed = json.loads(text)

    if not isinstance(parsed, dict) or not all(
        isinstance(anchor, str) and isinstance(section_id, str) and section_id
        for anchor, section_id in parsed.items()
    ):
        msg = f"{url} must map each anchor to a section ID"
        raise ValueError(msg)

    return {anchor: ids.SectionId(section_id) for anchor, section_id in parsed.items()}
