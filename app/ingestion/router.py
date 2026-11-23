"""HTTP routes the CMS uses to index and delete document versions."""

import logging
from typing import Annotated, Any

import fastapi

from app import config as app_config
from app.common import tracing
from app.indexing.prepare import guide_cms, guide_content
from app.ingestion import event_queue, models, schemas
from app.records import ids

logger = logging.getLogger(__name__)

router = fastapi.APIRouter(tags=["ingestion"])

PATH = "/documents/{documentId}/versions/{versionId}"

DocumentId = Annotated[guide_cms.CmsId, fastapi.Path(alias="documentId")]
VersionId = Annotated[guide_cms.CmsId, fastapi.Path(alias="versionId")]

UNAVAILABLE: dict[int | str, dict[str, Any]] = {
    fastapi.status.HTTP_503_SERVICE_UNAVAILABLE: {
        "description": "The request could not be queued; send it again",
    },
}


@router.put(
    PATH,
    status_code=fastapi.status.HTTP_202_ACCEPTED,
    responses=UNAVAILABLE,
)
async def index_version(
    document_id: DocumentId,
    version_id: VersionId,
    body: schemas.IndexRequest,
    queue: event_queue.IndexingQueueDep,
    config: app_config.Config,
) -> schemas.Accepted:
    """Queue this version to be prepared, then indexed."""
    bucket, _ = guide_content.parse_s3_url(body.content_url)

    if bucket != config.managed_docs_s3_bucket:
        raise fastapi.HTTPException(
            fastapi.status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"contentUrl must be in the {config.managed_docs_s3_bucket} bucket",
        )

    event = body.to_event(
        ids.DocumentId(document_id), ids.VersionId(version_id), _trace_id()
    )
    await _send(queue, event)

    return schemas.Accepted(
        document_id=event.document_id,
        version_id=event.version_id,
        status=body.status,
        operation=schemas.Operation.INDEX,
    )


@router.delete(
    PATH,
    status_code=fastapi.status.HTTP_202_ACCEPTED,
    responses=UNAVAILABLE,
)
async def delete_version(
    document_id: DocumentId,
    version_id: VersionId,
    queue: event_queue.IndexingQueueDep,
) -> schemas.Accepted:
    """Queue this version's removal, at every status, from the index and staged files."""
    event = schemas.delete_event(
        ids.DocumentId(document_id), ids.VersionId(version_id), _trace_id()
    )
    await _send(queue, event)

    return schemas.Accepted(
        document_id=event.document_id,
        version_id=event.version_id,
        status=None,
        operation=schemas.Operation.DELETE,
    )


async def _send(queue: event_queue.IndexingQueue, event: models.GuideEvent) -> None:
    try:
        await queue.send(event)
    except Exception as error:
        logger.exception(
            "Failed to queue %s for %s version %s",
            event.type,
            event.document_id,
            event.version_id,
        )
        raise fastapi.HTTPException(
            fastapi.status.HTTP_503_SERVICE_UNAVAILABLE,
            "the request could not be queued; send it again",
        ) from error


def _trace_id() -> str | None:
    return tracing.ctx_trace_id.get(None)
