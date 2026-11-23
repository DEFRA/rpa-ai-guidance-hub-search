"""Guide events and writer jobs for the fixture guide."""

import datetime as dt

from app.indexing import jobs
from app.ingestion import models as ingestion_models
from app.records import ids
from tests.support import guides

TIME = dt.datetime(2026, 11, 12, 10, tzinfo=dt.UTC)
TITLE = "Field Visit Guide"


def published(
    *,
    bucket: str = guides.BUCKET,
    document_id: ids.DocumentId = guides.DOCUMENT_ID,
    version_id: ids.VersionId = guides.VERSION_ID,
    trace_id: str | None = None,
) -> ingestion_models.GuideVersionPublishedV1:
    data = ingestion_models.GuideVersionPublishedDataV1.model_validate(
        {
            "documentId": str(document_id),
            "versionId": str(version_id),
            "sourceType": "guide",
            "status": "published",
            "contentUrl": f"s3://{bucket}/{guides.content_key(document_id, version_id)}",
            "title": TITLE,
            "versionNumber": "1.0",
            "metadata": {"owner": "Schemes team"},
        },
    )

    return ingestion_models.guide_version_published(data, TIME, trace_id)


def deleted(
    *,
    document_id: ids.DocumentId = guides.DOCUMENT_ID,
    version_id: ids.VersionId = guides.VERSION_ID,
    trace_id: str | None = None,
) -> ingestion_models.GuideVersionDeletedV1:
    data = ingestion_models.GuideVersionDeletedDataV1(
        document_id=document_id, version_id=version_id
    )

    return ingestion_models.guide_version_deleted(data, TIME, trace_id)


def apply_job(chunk_set_key: str = "staged/s/d/v/published.jsonl") -> jobs.ApplyJob:
    return jobs.ApplyJob(
        document_id=guides.DOCUMENT_ID,
        version_id=guides.VERSION_ID,
        status=guides.job().status,
        occurred_at=TIME,
        strategy="packed-800-v2",
        chunk_set_key=chunk_set_key,
    )


def remove_job() -> jobs.RemoveJob:
    return jobs.RemoveJob(
        document_id=guides.DOCUMENT_ID,
        version_id=guides.VERSION_ID,
        occurred_at=TIME,
    )
