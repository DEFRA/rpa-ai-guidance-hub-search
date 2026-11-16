"""httpx clients that forward the request's trace id in `tracing_header`."""

from collections.abc import Awaitable, Callable
from logging import getLogger

import httpx2

from app.common import tracing

logger = getLogger(__name__)


def async_tracing_hook(
    tracing_header: str,
) -> Callable[[httpx2.Request], Awaitable[None]]:
    async def hook(request: httpx2.Request) -> None:
        _add_trace_id(request, tracing_header)

    return hook


def tracing_hook(tracing_header: str) -> Callable[[httpx2.Request], None]:
    def hook(request: httpx2.Request) -> None:
        _add_trace_id(request, tracing_header)

    return hook


def create_async_client(
    tracing_header: str, request_timeout: int = 30
) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        timeout=request_timeout,
        event_hooks={"request": [async_tracing_hook(tracing_header)]},
    )


def create_client(tracing_header: str, request_timeout: int = 30) -> httpx2.Client:
    return httpx2.Client(
        timeout=request_timeout,
        event_hooks={"request": [tracing_hook(tracing_header)]},
    )


def _add_trace_id(request: httpx2.Request, tracing_header: str) -> None:
    trace_id = tracing.ctx_trace_id.get(None)
    if trace_id:
        request.headers[tracing_header] = trace_id
