"""Hands write jobs to the index's single writer.

Entry points prepare, then dispatch; they never call the applier themselves.
Today the writer runs in this process. A queue-backed dispatcher can replace
`InProcessDispatcher` without the entry points or the preparer changing.
"""

from collections.abc import Awaitable, Callable
from typing import Protocol

from app.index import builds
from app.indexing import jobs
from app.indexing.apply import document_applier


class ApplyDispatcher(Protocol):
    async def dispatch(self, job: jobs.WriteJob) -> None:
        """Sees the job applied or removed, or raises."""
        ...


class InProcessDispatcher:
    """Publishes each job straight away, in this process.

    Safe only with one writer: nothing retries a lost build swap, so callers
    must not dispatch concurrently.
    """

    def __init__(
        self,
        publish: Callable[
            [jobs.WriteJob], Awaitable[builds.BuildId | None]
        ] = document_applier.publish_from_config,
    ) -> None:
        self._publish = publish

    async def dispatch(self, job: jobs.WriteJob) -> None:
        await self._publish(job)
