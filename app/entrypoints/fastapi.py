import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from logging import getLogger

import fastapi
import uvicorn

from app import config as app_config
from app.common import mongo, tracing
from app.health import router as health_router
from app.index import active_index, builds
from app.ingestion import router as ingestion_router
from app.search.v1 import router as search_v1_router

logger = getLogger(__name__)


@asynccontextmanager
async def lifespan(_: fastapi.FastAPI) -> AsyncGenerator[None]:
    client = await mongo.get_mongo_client()
    logger.info("MongoDB client connected")

    database = await mongo.get_db(client)
    await active_index.load(
        app_config.get_config().search_index_directory,
        builds.get_index_build_service(database),
    )

    yield
    if client:
        await client.close()
        logger.info("MongoDB client closed")


def create_app(
    ingestion_mode: app_config.IngestionMode | None = None,
) -> fastapi.FastAPI:
    """Build the app, mounting the ingestion routes only in `http` mode.

    A factory rather than a module-level app, so importing this module never
    builds `AppConfig`. Without an explicit mode it reads `INGESTION_MODE`.
    """
    if ingestion_mode is None:
        ingestion_mode = app_config.get_config().ingestion_mode

    app = fastapi.FastAPI(lifespan=lifespan, title="RPA Guidance Hub Search API")
    app.add_middleware(tracing.TraceIdMiddleware)
    app.include_router(health_router.router)
    app.include_router(search_v1_router.router)

    if ingestion_mode is app_config.IngestionMode.HTTP:
        app.include_router(ingestion_router.router)

    return app


def main() -> None:  # pragma: no cover
    config = app_config.get_config()

    if config.http_proxy:
        os.environ["HTTP_PROXY"] = str(config.http_proxy)
        os.environ["HTTPS_PROXY"] = str(config.http_proxy)
    else:
        os.environ.pop("HTTP_PROXY", None)
        os.environ.pop("HTTPS_PROXY", None)

    uvicorn.run(
        "app.entrypoints.fastapi:create_app",
        factory=True,
        host=config.host,
        port=config.port,
        log_config=config.log_config,
        reload=config.python_env == "development",
    )


if __name__ == "__main__":  # pragma: no cover
    main()
