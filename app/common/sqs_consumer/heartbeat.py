"""Keep a batch invisible while its handlers run."""

import asyncio
from collections.abc import Callable, Sequence
from typing import Any

import botocore.exceptions

from app.common.sqs_consumer import config, gateway, message


async def run(
    queue: gateway.Gateway,
    messages: Sequence[message.Message[Any]],
    heartbeat: config.Heartbeat,
    *,
    report: Callable[[Exception], None],
    denied: Callable[[Exception], None],
) -> None:
    """Extend visibility every heartbeat.interval until cancelled.

    AccessDenied is handed to `denied` and ends the heartbeat; any other failure is
    handed to `report` and the next tick tries again. On cancel, an in-flight
    request is allowed to finish before this returns.
    """
    while True:
        await asyncio.sleep(heartbeat.interval)

        request = asyncio.ensure_future(
            queue.change_visibility(messages, heartbeat.visibility_timeout)
        )

        try:
            try:
                await asyncio.shield(request)
            except asyncio.CancelledError:
                # Cancelling does not stop a request already running in a thread.
                # Wait for it so it cannot land after the caller's own visibility
                # change. A second cancel ends the wait, so shutdown stays bounded.
                await asyncio.wait({request})
                if not request.cancelled():
                    request.exception()
                raise
        except botocore.exceptions.ClientError as exc:
            if gateway.is_access_denied(exc):
                denied(exc)
                return

            report(exc)
        except Exception as exc:
            report(exc)
