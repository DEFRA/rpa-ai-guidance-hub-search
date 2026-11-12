"""Where prepared chunk sets wait for the apply step.

Preparing a version puts its chunk set here; applying gets it back by the key
the prepare step handed over. Keys are deterministic, so preparing the same
version at the same status again overwrites the same object.
"""

import asyncio
from typing import TYPE_CHECKING, Protocol

from botocore import exceptions as botocore_exceptions

from app.common import s3
from app.records import chunk_sets

if TYPE_CHECKING:
    from types_boto3_s3 import S3Client

CONTENT_TYPE = "application/x-ndjson"


class ChunkSetStore(Protocol):
    async def put(self, chunk_set: chunk_sets.ChunkSet) -> str:
        """Stores the set and returns its key."""
        ...

    async def get(self, key: str) -> chunk_sets.ChunkSet | None:
        """`None` when nothing was staged under `key`."""
        ...


class S3ChunkSetStore:
    """`staged/{strategy}/{documentId}/{versionId}/{status}.jsonl` in the search-store bucket."""

    def __init__(self, client: S3Client, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    async def put(self, chunk_set: chunk_sets.ChunkSet) -> str:
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._bucket,
            Key=chunk_set.key,
            Body=chunk_sets.dumps(chunk_set).encode(),
            ContentType=CONTENT_TYPE,
        )

        return chunk_set.key

    async def get(self, key: str) -> chunk_sets.ChunkSet | None:
        try:
            text = await asyncio.to_thread(self._read, key)
        except botocore_exceptions.ClientError as e:
            if s3.is_missing(e):
                return None
            raise

        return chunk_sets.loads(text)

    def _read(self, key: str) -> str:
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        body: bytes = response["Body"].read()
        return body.decode("utf-8")
