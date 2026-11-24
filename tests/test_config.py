"""Tests for application configuration parsing and the config singleton."""

import logging

import pydantic
import pytest

from app import config


@pytest.fixture
def unset_config_singleton():
    """Clear the cached AppConfig so get_config() rebuilds, then restore it."""
    original = config._config
    config._config = None
    yield
    config._config = original


class TestAppConfig:
    """Field defaults and validation."""

    def test_defaults(self):
        cfg = config.AppConfig()

        assert cfg.host == "127.0.0.1"
        assert cfg.port == 8085
        assert cfg.mongo_database == "rpa-ai-guidance-hub-search"
        assert cfg.mongo_truststore == "TRUSTSTORE_CDP_ROOT_CA"
        assert cfg.aws_region == "eu-west-2"
        assert cfg.tracing_header == "x-cdp-request-id"
        assert cfg.enable_metrics is False
        assert cfg.floci_endpoint_url is None
        assert cfg.http_proxy is None
        assert cfg.ingestion_mode is config.IngestionMode.HTTP
        assert cfg.managed_docs_s3_bucket == "rpa-ai-guidance-hub-managed-docs"

    def test_requires_the_index_sync_queue_to_be_fifo(self, monkeypatch):
        """Only a FIFO queue's single message group keeps to one index writer."""
        monkeypatch.setenv(
            "SEARCH_INDEX_SYNC_SQS_QUEUE_URL", "http://floci:4566/000000000000/q"
        )

        with pytest.raises(pydantic.ValidationError, match="FIFO"):
            config.AppConfig()

    def test_reads_the_ingestion_mode_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("INGESTION_MODE", "s3")

        cfg = config.AppConfig()

        assert cfg.ingestion_mode is config.IngestionMode.S3

    def test_rejects_an_unknown_ingestion_mode(self, monkeypatch):
        monkeypatch.setenv("INGESTION_MODE", "sqs")

        with pytest.raises(pydantic.ValidationError, match="ingestion_mode"):
            config.AppConfig()

    def test_refreshes_the_index_every_minute_by_default(self):
        cfg = config.AppConfig()

        assert cfg.index_refresh_interval_seconds == 60

    def test_reads_the_index_refresh_interval_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("INDEX_REFRESH_INTERVAL_SECONDS", "5")

        cfg = config.AppConfig()

        assert cfg.index_refresh_interval_seconds == 5

    def test_rejects_a_zero_index_refresh_interval(self, monkeypatch):
        monkeypatch.setenv("INDEX_REFRESH_INTERVAL_SECONDS", "0")

        with pytest.raises(
            pydantic.ValidationError, match="index_refresh_interval_seconds"
        ):
            config.AppConfig()


@pytest.mark.usefixtures("unset_config_singleton")
class TestGetConfig:
    """The validate-on-construct singleton."""

    def test_builds_and_caches_a_single_instance(self):
        first = config.get_config()
        second = config.get_config()

        assert isinstance(first, config.AppConfig)
        assert first is second

    def test_returns_the_cached_instance_without_rebuilding(self, mocker):
        cached = config.get_config()
        constructor = mocker.patch.object(config, "AppConfig")

        assert config.get_config() is cached
        constructor.assert_not_called()

    def test_raises_runtime_error_naming_the_offending_field(self, monkeypatch):
        monkeypatch.delenv("SEARCH_INDEXING_SQS_QUEUE_URL", raising=False)

        with pytest.raises(RuntimeError, match="Config validation failed") as exc_info:
            config.get_config()

        assert "search_indexing_sqs_queue_url" in str(exc_info.value)
        assert "Field required" in str(exc_info.value)

    def test_logs_the_validation_failure(self, monkeypatch, caplog):
        monkeypatch.delenv("SEARCH_INDEXING_SQS_QUEUE_URL", raising=False)

        with (
            caplog.at_level(logging.ERROR, logger="app.config"),
            pytest.raises(RuntimeError),
        ):
            config.get_config()

        assert "Config validation failed" in caplog.text

    def test_does_not_cache_a_failed_construction(self, monkeypatch):
        monkeypatch.delenv("SEARCH_INDEXING_SQS_QUEUE_URL", raising=False)

        with pytest.raises(RuntimeError):
            config.get_config()

        assert config._config is None
