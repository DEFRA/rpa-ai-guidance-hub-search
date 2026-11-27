"""The admin route that resets the search index."""

import logging
from typing import Annotated, Any

import fastapi
import pymongo

from app import config as app_config
from app.admin import index_reset, pending_writes, schemas
from app.common import mongo, s3
from app.index import builds
from app.indexing import dispatch

logger = logging.getLogger(__name__)

router = fastapi.APIRouter(tags=["admin"])

UNAVAILABLE: dict[int | str, dict[str, Any]] = {
    fastapi.status.HTTP_503_SERVICE_UNAVAILABLE: {
        "description": "The index could not be reset; send it again",
    },
}


@router.post("/admin/index/reset", responses=UNAVAILABLE)
async def reset_index(
    config: app_config.Config,
    database: Annotated[
        pymongo.asynchronous.database.AsyncDatabase, fastapi.Depends(mongo.get_db)
    ],
    s3_client: s3.Client,
    index_builds: Annotated[
        builds.IndexBuildService, fastapi.Depends(builds.get_index_build_service)
    ],
    pending: Annotated[
        dispatch.PendingWrites, fastapi.Depends(pending_writes.get_pending_writes)
    ],
) -> schemas.ResetResult:
    """Delete everything in the index and its storage, and serve an empty index.

    Returns once the wipe has finished. Idempotent: if it fails, send it again.
    There is no authentication: it relies on network trust, like the ingestion
    routes.
    """
    try:
        result = await index_reset.reset(
            database,
            s3_client,
            config.search_store_s3_bucket,
            pending,
            config.search_index_directory,
            index_builds,
        )
    except Exception as error:
        logger.exception("Failed to reset the search index")
        raise fastapi.HTTPException(
            fastapi.status.HTTP_503_SERVICE_UNAVAILABLE,
            "the index could not be reset; send it again",
        ) from error

    return schemas.ResetResult.from_result(result)
