"""Hands write jobs to the index's single writer.

Entry points prepare, then dispatch; they never call the applier themselves.
Today the writer runs in this process. A queue-backed dispatcher can replace
`BatchingDispatcher` without the entry points or the preparer changing.
"""

import asyncio
import dataclasses
from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol

from app.indexing import jobs


class ApplyDispatcher(Protocol):
    async def dispatch(self, job: jobs.WriteJob) -> None:
        """Sees the job applied or removed, or raises."""
        ...


class BatchResult(Protocol):
    @property
    def errors(self) -> Sequence[Exception | None]:
        """What each job in the batch raised, in order, or None."""
        ...


PublishAll = Callable[[Sequence[jobs.WriteJob]], Awaitable[BatchResult]]


@dataclasses.dataclass
class PendingWrites:
    """Jobs waiting for the writer, and the writer's turn; one per process.

    Every `BatchingDispatcher` around the same `PendingWrites` shares one queue.
    """

    jobs: list[tuple[jobs.WriteJob, asyncio.Future[None]]] = dataclasses.field(
        default_factory=list,
    )
    turn: asyncio.Lock = dataclasses.field(default_factory=asyncio.Lock)


class BatchingDispatcher:
    """Publishes, in one batch, every job that arrived while the last one ran.

    Publishing costs a download and an upload of the whole index, whatever it
    changes, so a burst of N jobs costs one more publish rather than N. Jobs
    keep their arrival order within a batch.

    Whichever caller gets the writer's turn publishes the batch, with its own
    `publish_all`; every dispatcher must be wired to the same index. Each
    caller still sees its own job's outcome: what the job raised, or what the
    batch raised if it couldn't be published.

    Safe only with one writer: nothing retries a lost build swap, so only one
    process may publish to an index.
    """

    def __init__(self, pending: PendingWrites, publish_all: PublishAll) -> None:
        self._pending = pending
        self._publish_all = publish_all

    async def dispatch(self, job: jobs.WriteJob) -> None:
        done: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._pending.jobs.append((job, done))

        async with self._pending.turn:
            if not done.done():
                batch, self._pending.jobs = self._pending.jobs, []
                await self._publish(batch)

        await done

    async def _publish(
        self,
        batch: list[tuple[jobs.WriteJob, asyncio.Future[None]]],
    ) -> None:
        try:
            result = await self._publish_all([job for job, _ in batch])
        except asyncio.CancelledError:
            for _, done in batch:
                done.cancel()
            raise
        except Exception as error:  # raised to every job's caller
            for _, done in batch:
                done.set_exception(error)
            return

        for (_, done), raised in zip(batch, result.errors, strict=True):
            if raised is None:
                done.set_result(None)
            else:
                done.set_exception(raised)
