import os
import sys

import fastapi.testclient
import pytest

import app.entrypoints.fastapi

client = fastapi.testclient.TestClient(app.entrypoints.fastapi.create_app())


@pytest.fixture
def ingestion_probe(mocker):
    """Swap the ingestion router for one holding a single known route."""
    router = fastapi.APIRouter()
    router.add_api_route("/ingestion-probe", lambda: {"ok": True})
    mocker.patch.object(app.entrypoints.fastapi.ingestion_router, "router", router)
    return "/ingestion-probe"


def get(application, path):
    return fastapi.testclient.TestClient(application).get(path).status_code


def test_http_mode_mounts_the_ingestion_routes(ingestion_probe):
    application = app.entrypoints.fastapi.create_app(app.config.IngestionMode.HTTP)

    assert get(application, "/health") == 200
    assert get(application, ingestion_probe) == 200


def test_s3_mode_leaves_out_the_ingestion_routes(ingestion_probe):
    application = app.entrypoints.fastapi.create_app(app.config.IngestionMode.S3)

    assert get(application, "/health") == 200
    assert get(application, ingestion_probe) == 404


def test_create_app_reads_the_mode_from_config(mocker, ingestion_probe):
    mocker.patch(
        "app.config.get_config",
        return_value=mocker.Mock(ingestion_mode=app.config.IngestionMode.S3),
    )

    application = app.entrypoints.fastapi.create_app()

    assert get(application, ingestion_probe) == 404


def test_lifespan(mocker):
    mock_mongo_client = mocker.AsyncMock()
    mock_get_mongo = mocker.patch(
        "app.common.mongo.get_mongo_client", return_value=mock_mongo_client
    )
    database = mocker.MagicMock()
    mocker.patch("app.common.mongo.get_db", mocker.AsyncMock(return_value=database))
    index_builds = mocker.patch("app.index.builds.get_index_build_service")
    load = mocker.patch("app.index.active_index.load", mocker.AsyncMock())

    with fastapi.testclient.TestClient(app.entrypoints.fastapi.create_app()):
        mock_get_mongo.assert_called_once()
        index_builds.assert_called_once_with(database)
        load.assert_awaited_once_with(
            app.config.get_config().search_index_directory,
            index_builds.return_value,
        )

    mock_mongo_client.close.assert_awaited_once()


def test_root():
    response = client.get("/")
    assert response.status_code == 404


def test_main_sets_proxy_envs(mocker, monkeypatch):
    mocker.patch("app.entrypoints.fastapi.uvicorn.run")

    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)

    mock_config = mocker.Mock()
    mock_config.http_proxy = "http://proxy:8080"
    mock_config.host = "127.0.0.1"
    mock_config.port = 9000
    mock_config.log_config = None
    mock_config.python_env = "production"

    # Force reload of the fastapi entrypoint module to get fresh imports
    if "app.entrypoints.fastapi" in sys.modules:
        del sys.modules["app.entrypoints.fastapi"]

    # Patch before importing
    mocker.patch("app.config.get_config", return_value=mock_config)

    from app.entrypoints.fastapi import main

    main()

    assert os.environ.get("HTTP_PROXY") == "http://proxy:8080"
    assert os.environ.get("HTTPS_PROXY") == "http://proxy:8080"


def test_main_no_proxy_in_config(mocker, monkeypatch):
    mocker.patch("app.entrypoints.fastapi.uvicorn.run")

    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)

    mock_config = mocker.Mock()
    mock_config.http_proxy = None
    mock_config.host = "127.0.0.1"
    mock_config.port = 8085
    mock_config.log_config = None
    mock_config.python_env = "production"

    # Force reload of the fastapi entrypoint module to get fresh imports
    if "app.entrypoints.fastapi" in sys.modules:
        del sys.modules["app.entrypoints.fastapi"]

    # Patch before importing
    mocker.patch("app.config.get_config", return_value=mock_config)

    from app.entrypoints.fastapi import main

    main()

    assert os.environ.get("HTTP_PROXY") is None
    assert os.environ.get("HTTPS_PROXY") is None
