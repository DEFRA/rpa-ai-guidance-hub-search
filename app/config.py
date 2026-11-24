import enum
import logging
import pathlib
import tempfile
from typing import Annotated

import fastapi
import pydantic
import pydantic_settings

logger = logging.getLogger(__name__)


class IngestionMode(enum.StrEnum):
    """How documents reach the service.

    `http` mounts the ingestion routes, for the CMS to push to. `s3` leaves them
    out, and documents arrive only through the indexing queue and the
    `index-document` CLI. The queue consumers run in both modes.
    """

    HTTP = "http"
    S3 = "s3"


class AppConfig(pydantic_settings.BaseSettings):
    model_config = pydantic_settings.SettingsConfigDict()
    python_env: str | None = None
    host: str = "127.0.0.1"
    port: int = 8085
    log_config: str | None = None
    mongo_uri: str | None = None
    mongo_database: str = "rpa-ai-guidance-hub-search"
    mongo_truststore: str = "TRUSTSTORE_CDP_ROOT_CA"
    floci_endpoint_url: str | None = None
    search_store_s3_bucket: str = "rpa-ai-guidance-hub-search-store"
    managed_docs_s3_bucket: str = "rpa-ai-guidance-hub-managed-docs"
    search_indexing_sqs_queue_url: str = pydantic.Field(min_length=1)
    search_index_sync_sqs_queue_url: str = pydantic.Field(min_length=1)
    sqs_shutdown_timeout_seconds: int = pydantic.Field(default=20, gt=0)
    search_index_directory: pathlib.Path = (
        pathlib.Path(tempfile.gettempdir()) / "rpa-ai-guidance-hub-search"
    )
    index_refresh_interval_seconds: float = pydantic.Field(default=60, gt=0)
    aws_region: str = pydantic.Field(
        default="eu-west-2", description="AWS region for S3, SQS and other services"
    )
    http_proxy: pydantic.HttpUrl | None = None
    enable_metrics: bool = False
    ingestion_mode: IngestionMode = IngestionMode.HTTP
    tracing_header: str = "x-cdp-request-id"

    @pydantic.field_validator("search_index_sync_sqs_queue_url")
    @classmethod
    def validate_index_sync_queue_is_fifo(cls: type[AppConfig], v: str) -> str:
        """Only a FIFO queue keeps the index to one writer."""
        if not v.endswith(".fifo"):
            msg = "the index sync queue must be a FIFO queue (its URL ends in .fifo)"
            raise ValueError(msg)

        return v


_config: AppConfig | None = None


def get_config() -> AppConfig:
    global _config

    if _config is not None:
        return _config

    try:
        _config = AppConfig()  # type: ignore[call-arg]
        return _config
    except pydantic.ValidationError as e:
        error_details = [
            {
                "field": ".".join(str(loc) for loc in error["loc"]),
                "type": error["type"],
                "message": error["msg"],
            }
            for error in e.errors()
        ]

        error_strings = [
            f"Field '{error['field']}' {error['message']}" for error in error_details
        ]

        msg = f"Config validation failed with errors: {', '.join(error_strings)}"
        logger.error(msg)
        raise RuntimeError(msg) from None


Config = Annotated[AppConfig, fastapi.Depends(get_config)]
"""Injects the config into a FastAPI route or dependency."""
