"""Keep a batch invisible while its handlers run."""

import asyncio
import contextlib
from collections.abc import Callable, Sequence
from typing import Any

import botocore.exceptions

from app.common.sqs_consumer import config, gateway, message


async def run(
    queue: gateway.Gateway,
    messages: Sequence[message.Message[Any]],
    heartbeat: config.Heartbeat,
    *,
    finished: asyncio.Event,
    report: Callable[[Exception], None],
    denied: Callable[[Exception], None],
) -> None:
    """Extend visibility every heartbeat.interval until `finished` is set.

    Setting `finished` lets a request already in flight complete before this
    returns, so it cannot land after the caller's own visibility change. Cancelling
    abandons that request instead, which keeps a forced shutdown bounded.

    AccessDenied is handed to `denied` and ends the heartbeat; any other failure is
    handed to `report` and the next tick tries again.
    """
    while True:
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(heartbeat.interval):
                await finished.wait()

        if finished.is_set():
            return

        try:
            await queue.change_visibility(messages, heartbeat.visibility_timeout)
        except botocore.exceptions.ClientError as exc:
            if gateway.is_access_denied(exc):
                denied(exc)
                return

            report(exc)
        except Exception as exc:
            report(exc)
