from logging import getLogger
from typing import Annotated

import fastapi
import pymongo

from app import config as app_config
from app.common import tls

logger = getLogger(__name__)

client: pymongo.AsyncMongoClient | None = None
db: pymongo.asynchronous.database.AsyncDatabase | None = None


async def get_mongo_client(config: app_config.Config) -> pymongo.AsyncMongoClient:
    """The process's one client, built from the config of the first call."""
    global client
    if client is None:
        cert = tls.custom_ca_certs.get(config.mongo_truststore)
        if cert:
            logger.info(
                "Creating MongoDB client with custom TLS cert %s",
                config.mongo_truststore,
            )
            client = pymongo.AsyncMongoClient(
                config.mongo_uri,
                tlsCAFile=cert,
                uuidRepresentation="standard",
            )
        else:
            logger.info("Creating MongoDB client")
            client = pymongo.AsyncMongoClient(
                config.mongo_uri, uuidRepresentation="standard"
            )

        logger.info("Testing MongoDB connection to %s", config.mongo_uri)
        await check_connection(client.get_database(config.mongo_database))
    return client


async def get_db(
    client: Annotated[pymongo.AsyncMongoClient, fastapi.Depends(get_mongo_client)],
    config: app_config.Config,
) -> pymongo.asynchronous.database.AsyncDatabase:
    global db
    if db is None:
        db = client.get_database(config.mongo_database)
    return db


async def check_connection(
    database: pymongo.asynchronous.database.AsyncDatabase,
) -> None:
    response = await database.command("ping")
    logger.info("MongoDB PING %s", response)
