import json
import logging
import pathlib

import pytest

from app.common import log_utils

ROOT = pathlib.Path(__file__).parent.parent.parent


def test_extra_fields_filter_with_all_context(mocker):
    mock_trace_id = mocker.patch("app.common.tracing.ctx_trace_id")
    mock_request = mocker.patch("app.common.tracing.ctx_request")
    mock_response = mocker.patch("app.common.tracing.ctx_response")

    # Set context values
    mock_trace_id.get.return_value = "test-trace-id"
    mock_request.get.return_value = {"url": "http://test.com", "method": "GET"}
    mock_response.get.return_value = {"status_code": 200}

    # Create a log record
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="test message",
        args=(),
        exc_info=None,
    )

    log_filter = log_utils.ExtraFieldsFilter()
    result = log_filter.filter(record)

    # Assertions
    assert result is True
    assert record.trace == {"id": "test-trace-id"}
    assert record.url == {"full": "http://test.com"}
    assert record.http == {
        "request": {"method": "GET"},
        "response": {"status_code": 200},
    }


def test_extra_fields_filter_with_no_context(mocker):
    mock_trace_id = mocker.patch("app.common.tracing.ctx_trace_id")
    mock_request = mocker.patch("app.common.tracing.ctx_request")
    mock_response = mocker.patch("app.common.tracing.ctx_response")

    mock_trace_id.get.return_value = None
    mock_request.get.return_value = None
    mock_response.get.return_value = None

    # Create a log record
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="test message",
        args=(),
        exc_info=None,
    )

    log_filter = log_utils.ExtraFieldsFilter()
    result = log_filter.filter(record)

    assert result is True
    assert not hasattr(record, "trace")
    assert not hasattr(record, "url")
    assert not hasattr(record, "http")


def test_endpoint_filter_blocks_matching_path():
    filter_path = "/health"
    log_filter = log_utils.EndpointFilter(path=filter_path)

    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg=f"GET {filter_path} HTTP/1.1",
        args=(),
        exc_info=None,
    )

    assert log_filter.filter(record) is False


def test_endpoint_filter_allows_non_matching_path():
    filter_path = "/health"
    log_filter = log_utils.EndpointFilter(path=filter_path)

    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="GET /api/users HTTP/1.1",
        args=(),
        exc_info=None,
    )

    assert log_filter.filter(record) is True


def _access_record(path):
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:5000", "GET", path, "1.1", 200),
        exc_info=None,
    )


def test_access_log_redaction_filter_redacts_the_search_text():
    record = _access_record("/v1/search?q=Jo+Bloggs&limit=5")

    assert log_utils.AccessLogRedactionFilter().filter(record) is True
    assert record.getMessage() == (
        '127.0.0.1:5000 - "GET /v1/search?q=[redacted]&limit=5 HTTP/1.1" 200'
    )


def test_access_log_redaction_filter_leaves_other_records_alone():
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="%s",
        args=("q=Jo",),
        exc_info=None,
    )

    assert log_utils.AccessLogRedactionFilter().filter(record) is True
    assert record.getMessage() == "q=Jo"


@pytest.mark.parametrize("name", ["logging.json", "logging-dev.json"])
def test_log_configs_redact_the_access_log(name):
    config = json.loads((ROOT / name).read_text())

    filter_names = config["loggers"]["uvicorn.access"]["filters"]

    assert [config["filters"][filter_name]["()"] for filter_name in filter_names] == [
        "app.common.log_utils.AccessLogRedactionFilter"
    ]
