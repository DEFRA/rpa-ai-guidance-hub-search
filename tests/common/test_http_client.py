import httpx2

from app.common import http_client, tracing

HEADER = "x-cdp-request-id"


def mock_handler(request):
    request_id = request.headers.get("x-cdp-request-id", "")
    return httpx2.Response(200, text=request_id)


def test_trace_id_missing():
    tracing.ctx_trace_id.set("")
    client = httpx2.Client(
        event_hooks={"request": [http_client.tracing_hook(HEADER)]},
        transport=httpx2.MockTransport(mock_handler),
    )
    resp = client.get("http://localhost:1234/test")
    assert resp.text == ""


def test_trace_id_set():
    tracing.ctx_trace_id.set("trace-id-value")
    client = httpx2.Client(
        event_hooks={"request": [http_client.tracing_hook(HEADER)]},
        transport=httpx2.MockTransport(mock_handler),
    )
    resp = client.get("http://localhost:1234/test")
    assert resp.text == "trace-id-value"


async def test_async_trace_id_missing():
    tracing.ctx_trace_id.set("")
    client = httpx2.AsyncClient(
        event_hooks={"request": [http_client.async_tracing_hook(HEADER)]},
        transport=httpx2.MockTransport(mock_handler),
    )
    resp = await client.get("http://localhost:1234/test")
    assert resp.text == ""


async def test_async_trace_id_set():
    tracing.ctx_trace_id.set("async-trace-id-value")
    client = httpx2.AsyncClient(
        event_hooks={"request": [http_client.async_tracing_hook(HEADER)]},
        transport=httpx2.MockTransport(mock_handler),
    )
    resp = await client.get("http://localhost:1234/test")
    assert resp.text == "async-trace-id-value"


def test_create_client_returns_httpx_client():
    client = http_client.create_client(HEADER)
    assert isinstance(client, httpx2.Client)


def test_create_client_default_timeout():
    client = http_client.create_client(HEADER)
    assert client.timeout.read == 30


def test_create_client_custom_timeout():
    client = http_client.create_client(HEADER, request_timeout=60)
    assert client.timeout.read == 60


def test_create_client_registers_a_tracing_hook():
    client = http_client.create_client(HEADER)
    assert len(client.event_hooks["request"]) == 1


def test_create_async_client_returns_httpx_async_client():
    client = http_client.create_async_client(HEADER)
    assert isinstance(client, httpx2.AsyncClient)


def test_create_async_client_default_timeout():
    client = http_client.create_async_client(HEADER)
    assert client.timeout.read == 30


def test_create_async_client_custom_timeout():
    client = http_client.create_async_client(HEADER, request_timeout=60)
    assert client.timeout.read == 60


def test_create_async_client_registers_a_tracing_hook():
    client = http_client.create_async_client(HEADER)
    assert len(client.event_hooks["request"]) == 1
