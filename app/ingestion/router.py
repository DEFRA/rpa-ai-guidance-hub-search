"""HTTP routes the CMS uses to index and delete document versions.

Mounted only when `INGESTION_MODE` is `http`. Both answer `202` once the
request is valid; the work runs after the reply (`service`).
"""

from typing import Annotated

import fastapi

from app import config as app_config
from app.common import tracing
from app.indexing.prepare import guide_cms
from app.ingestion import schemas, service
from app.records import ids

router = fastapi.APIRouter(tags=["ingestion"])

PATH = "/documents/{documentId}/versions/{versionId}"

DocumentId = Annotated[guide_cms.CmsId, fastapi.Path(alias="documentId")]
VersionId = Annotated[guide_cms.CmsId, fastapi.Path(alias="versionId")]
Service = Annotated[service.IngestionService, fastapi.Depends(service.get_service)]


@router.put(
    PATH,
    status_code=fastapi.status.HTTP_202_ACCEPTED,
    responses={
        fastapi.status.HTTP_413_CONTENT_TOO_LARGE: {
            "description": "Inline content too large",
        },
    },
)
async def index_version(
    document_id: DocumentId,
    version_id: VersionId,
    body: schemas.IndexRequest,
    background: fastapi.BackgroundTasks,
    ingestion: Service,
) -> schemas.Accepted:
    """Prepare this version, and index it if it is published."""
    limit = app_config.get_config().max_inline_content_bytes

    if body.inline_bytes() > limit:
        raise fastapi.HTTPException(
            fastapi.status.HTTP_413_CONTENT_TOO_LARGE,
            f"inline content is over {limit} bytes; send contentUrl instead",
        )

    job = body.to_job(ids.DocumentId(document_id), ids.VersionId(version_id))
    background.add_task(ingestion.index, job, body.to_reader(), _trace_id())

    return schemas.Accepted(
        document_id=job.document_id,
        version_id=ids.VersionId(version_id),
        status=body.status,
        operation=schemas.Operation.INDEX,
    )


@router.delete(PATH, status_code=fastapi.status.HTTP_202_ACCEPTED)
async def delete_version(
    document_id: DocumentId,
    version_id: VersionId,
    background: fastapi.BackgroundTasks,
    ingestion: Service,
) -> schemas.Accepted:
    """Remove this version, at every status, from the index and staged files."""
    job = schemas.remove_job(ids.DocumentId(document_id), ids.VersionId(version_id))
    background.add_task(ingestion.delete, job, _trace_id())

    return schemas.Accepted(
        document_id=job.document_id,
        version_id=job.version_id,
        status=None,
        operation=schemas.Operation.DELETE,
    )


def _trace_id() -> str | None:
    return tracing.ctx_trace_id.get(None)
