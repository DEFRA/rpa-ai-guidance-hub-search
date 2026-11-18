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
    `publishing` holds the tasks that publish, which asyncio keeps only weakly.
    """

    jobs: list[tuple[jobs.WriteJob, asyncio.Future[None]]] = dataclasses.field(
        default_factory=list,
    )
    turn: asyncio.Lock = dataclasses.field(default_factory=asyncio.Lock)
    publishing: set[asyncio.Task[None]] = dataclasses.field(default_factory=set)


class BatchingDispatcher:
    """Publishes, in one batch, every job that arrived while the last one ran.

    Publishing costs a download and an upload of the whole index, whatever it
    changes, so a burst of N jobs costs one more publish rather than N. Jobs
    keep their arrival order within a batch.

    Each dispatch starts a task that waits for the writer's turn and publishes
    whatever is waiting, with its dispatcher's `publish_all`; every dispatcher
    must be wired to the same index. The task belongs to no caller, so a
    cancelled caller stops waiting for its own job without cancelling the
    batch it is in. Each caller sees its own job's outcome: what the job
    raised, or what the batch raised if it couldn't be published.

    Safe only with one writer: nothing retries a lost build swap, so only one
    process may publish to an index.
    """

    def __init__(self, pending: PendingWrites, publish_all: PublishAll) -> None:
        self._pending = pending
        self._publish_all = publish_all

    async def dispatch(self, job: jobs.WriteJob) -> None:
        done: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._pending.jobs.append((job, done))

        task = asyncio.create_task(self._publish_waiting())
        self._pending.publishing.add(task)
        task.add_done_callback(self._pending.publishing.discard)

        await done

    async def _publish_waiting(self) -> None:
        async with self._pending.turn:
            batch, self._pending.jobs = self._pending.jobs, []

            if batch:
                await self._publish(batch)

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
                _settle(done, error)
            return

        for (_, done), raised in zip(batch, result.errors, strict=True):
            _settle(done, raised)


def _settle(done: asyncio.Future[None], raised: Exception | None) -> None:
    """Hands a job's outcome to its caller, unless the caller stopped waiting."""
    if done.done():
        return

    if raised is None:
        done.set_result(None)
    else:
        done.set_exception(raised)
