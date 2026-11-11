"""The invented fixture guide, and a job for it."""

import datetime as dt
import pathlib
import uuid

from app.indexing import jobs
from app.records import ids, models

FIXTURES = pathlib.Path(__file__).parent.parent / "indexing" / "prepare" / "fixtures"
GUIDE = FIXTURES / "guide.md"
GOLDEN_SECTIONS = FIXTURES / "guide.sections.golden.jsonl"
GOLDEN_CHUNKS = FIXTURES / "guide.packed-800.golden.jsonl"

DOCUMENT_ID = ids.DocumentId(uuid.UUID("0199b8a2-0000-7000-8000-00000000f001"))
VERSION_ID = ids.VersionId(uuid.UUID("0199b8a2-0000-7000-8000-00000000f002"))
OCCURRED_AT = dt.datetime(2026, 11, 3, 9, 30, tzinfo=dt.UTC)
BUCKET = "managed-docs"


def content_key(
    document_id: ids.DocumentId = DOCUMENT_ID,
    version_id: ids.VersionId = VERSION_ID,
) -> str:
    return f"{document_id}/{version_id}/content.md"


def job(
    *,
    document_id: ids.DocumentId = DOCUMENT_ID,
    version_id: ids.VersionId = VERSION_ID,
    status: models.DocumentStatus = models.DocumentStatus.PUBLISHED,
    bucket: str = BUCKET,
) -> jobs.IndexJob:
    return jobs.IndexJob(
        source_type=models.SourceType.GUIDE,
        document_id=document_id,
        version_id=version_id,
        status=status,
        content_url=f"s3://{bucket}/{content_key(document_id, version_id)}",
        occurred_at=OCCURRED_AT,
    )
