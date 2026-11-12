"""Where a document version's sections are kept; the source for every rebuild.

Shared: indexing writes sections, and enrichment (and later the RAG agent)
reads them back.
"""

import asyncio
from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

from botocore import exceptions as botocore_exceptions

from app.common import s3
from app.records import ids, jsonl, models

if TYPE_CHECKING:
    from types_boto3_s3 import S3Client

CONTENT_TYPE = "application/x-ndjson"


class SectionStore(Protocol):
    async def put(
        self,
        header: models.DocumentHeader,
        sections: Sequence[models.SectionRecord],
    ) -> None: ...

    async def get(
        self,
        document_id: ids.DocumentId,
        version_id: ids.VersionId,
    ) -> tuple[models.DocumentHeader, tuple[models.SectionRecord, ...]] | None:
        """`None` when the version was never stored."""
        ...

    async def delete(
        self,
        document_id: ids.DocumentId,
        version_id: ids.VersionId,
    ) -> None:
        """Removes the version's sections; a no-op if it was never stored."""
        ...


class S3SectionStore:
    """`sections/{documentId}/{versionId}.jsonl` in the search-store bucket."""

    def __init__(self, client: S3Client, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    async def put(
        self,
        header: models.DocumentHeader,
        sections: Sequence[models.SectionRecord],
    ) -> None:
        body = jsonl.dumps(header, sections).encode()

        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._bucket,
            Key=jsonl.key(header.document_id, header.version_id),
            Body=body,
            ContentType=CONTENT_TYPE,
        )

    async def get(
        self,
        document_id: ids.DocumentId,
        version_id: ids.VersionId,
    ) -> tuple[models.DocumentHeader, tuple[models.SectionRecord, ...]] | None:
        try:
            text = await asyncio.to_thread(
                self._read,
                jsonl.key(document_id, version_id),
            )
        except botocore_exceptions.ClientError as e:
            if s3.is_missing(e):
                return None
            raise

        return jsonl.loads(text)

    async def delete(
        self,
        document_id: ids.DocumentId,
        version_id: ids.VersionId,
    ) -> None:
        # S3 answers a delete of a missing key with success.
        await asyncio.to_thread(
            self._client.delete_object,
            Bucket=self._bucket,
            Key=jsonl.key(document_id, version_id),
        )

    def _read(self, key: str) -> str:
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        body: bytes = response["Body"].read()
        return body.decode("utf-8")
