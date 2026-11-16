import asyncio
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from logging import getLogger

import fastapi
import uvicorn

from app import config as app_config
from app.common import mongo, s3, tracing
from app.health import router as health_router
from app.index import active_index, builds
from app.ingestion import router as ingestion_router

logger = getLogger(__name__)


@asynccontextmanager
async def lifespan(_: fastapi.FastAPI) -> AsyncGenerator[None]:
    config = app_config.get_config()
    client = await mongo.get_mongo_client(config)
    logger.info("MongoDB client connected")

    database = await mongo.get_db(client, config)
    await active_index.load(
        config.search_index_directory,
        builds.get_index_build_service(database, config, s3.get_s3_client(config)),
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
    config = app_config.get_config()

    if ingestion_mode is None:
        ingestion_mode = config.ingestion_mode

    app = fastapi.FastAPI(lifespan=lifespan, title="RPA Guidance Hub Search API")
    app.add_middleware(tracing.TraceIdMiddleware, header=config.tracing_header)
    app.include_router(health_router.router)

    if ingestion_mode is app_config.IngestionMode.HTTP:
        # One per app, so every request's ingestion service shares it
        # (`ingestion.service.get_lock`).
        app.state.ingestion_lock = asyncio.Lock()
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
