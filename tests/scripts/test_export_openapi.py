"""The OpenAPI export needs none of `AppConfig`'s required environment."""

import pytest

from app import config
from scripts import export_openapi


@pytest.fixture
def no_config(monkeypatch):
    """No cached `AppConfig`, and a required variable missing."""
    monkeypatch.setattr(config, "_config", None)
    monkeypatch.delenv("SEARCH_INDEXING_SQS_QUEUE_URL")


@pytest.mark.usefixtures("no_config")
def test_renders_without_the_required_environment():
    rendered = export_openapi.render()

    assert '"/documents/{documentId}/versions/{versionId}"' in rendered
    assert config._config is None
