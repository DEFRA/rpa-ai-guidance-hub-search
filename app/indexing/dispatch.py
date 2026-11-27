"""Hands write jobs to the index's single writer.

Entry points prepare, then dispatch; they never call the applier themselves.
Today the writer runs in this process. A queue-backed dispatcher can replace
`BatchingDispatcher` without the entry points or the preparer changing.
"""

import asyncio
import contextlib
import dataclasses
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
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


class PreparationGate:
    """Lets any number of preparations run, or one exclusive holder, never both.

    A preparation writes sections and staged chunk sets into the store, and
    holds the gate until its job has been dispatched. Whoever wipes the store
    takes the gate exclusively: it waits for the preparations under way, and
    preparations that arrive meanwhile wait for it. So no preparation ever
    straddles a wipe, with some of its files gone and the rest dispatched.
    """

    def __init__(self) -> None:
        self._preparing = 0
        self._exclusive = 0
        self._idle = asyncio.Event()
        self._idle.set()
        self._open = asyncio.Event()
        self._open.set()

    @contextlib.asynccontextmanager
    async def preparing(self) -> AsyncIterator[None]:
        await self._open.wait()

        # No await between the wait and the count, so no exclusive holder can
        # slip in between them.
        self._preparing += 1
        self._idle.clear()

        try:
            yield
        finally:
            self._preparing -= 1

            if self._preparing == 0:
                self._idle.set()

    @contextlib.asynccontextmanager
    async def exclusive(self) -> AsyncIterator[None]:
        self._exclusive += 1
        self._open.clear()

        try:
            await self._idle.wait()

            yield
        finally:
            self._exclusive -= 1

            if self._exclusive == 0:
                self._open.set()


@dataclasses.dataclass
class PendingWrites:
    """Jobs waiting for the writer, and the writer's turn; one per process.

    Every `BatchingDispatcher` around the same `PendingWrites` shares one queue.
    `publishing` holds the tasks that publish, which asyncio keeps only weakly.
    `gate` keeps preparations and a wipe of the store apart; take it before
    `turn`, as a preparation under way needs `turn` to finish.
    """

    jobs: list[tuple[jobs.WriteJob, asyncio.Future[None]]] = dataclasses.field(
        default_factory=list,
    )
    turn: asyncio.Lock = dataclasses.field(default_factory=asyncio.Lock)
    publishing: set[asyncio.Task[None]] = dataclasses.field(default_factory=set)
    gate: PreparationGate = dataclasses.field(default_factory=PreparationGate)


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
