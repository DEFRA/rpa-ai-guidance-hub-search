"""The batching dispatcher publishes what arrives during a publish together, next."""

import asyncio
import dataclasses
import uuid

import pytest

from app.indexing import dispatch, jobs
from tests.support import chunks as fixtures


def _remove(n):
    return jobs.RemoveJob(
        document_id=fixtures.DOCUMENT_ID,
        version_id=uuid.UUID(f"0199b8a2-0000-7000-8000-{n:012d}"),
        occurred_at=fixtures.OCCURRED_AT,
    )


@dataclasses.dataclass(frozen=True)
class Result:
    errors: tuple[Exception | None, ...]


class Publisher:
    """Records each batch; holds a batch open until `release` is set."""

    def __init__(self, fail=frozenset()):
        self.batches = []
        self.release = asyncio.Event()
        self.release.set()
        self._fail = fail

    async def __call__(self, batch):
        self.batches.append(list(batch))
        await self.release.wait()
        return Result(
            tuple(
                RuntimeError(f"failed {job.version_id}") if job in self._fail else None
                for job in batch
            ),
        )


async def _settle():
    for _ in range(5):
        await asyncio.sleep(0)


class TestDispatch:
    async def test_publishes_a_lone_job_by_itself(self):
        publish = Publisher()

        await dispatch.BatchingDispatcher(dispatch.PendingWrites(), publish).dispatch(
            _remove(1)
        )

        assert publish.batches == [[_remove(1)]]

    async def test_batches_the_jobs_that_arrive_during_a_publish_in_order(self):
        publish = Publisher()
        pending = dispatch.PendingWrites()
        publish.release.clear()
        first = asyncio.create_task(
            dispatch.BatchingDispatcher(pending, publish).dispatch(_remove(1))
        )
        await _settle()

        rest = [
            asyncio.create_task(
                dispatch.BatchingDispatcher(pending, publish).dispatch(_remove(n))
            )
            for n in (2, 3, 4)
        ]
        await _settle()
        publish.release.set()
        await asyncio.gather(first, *rest)

        assert publish.batches == [[_remove(1)], [_remove(2), _remove(3), _remove(4)]]

    async def test_raises_a_jobs_error_to_its_caller_only(self):
        publish = Publisher(fail={_remove(2)})
        pending = dispatch.PendingWrites()
        publish.release.clear()
        first = asyncio.create_task(
            dispatch.BatchingDispatcher(pending, publish).dispatch(_remove(1))
        )
        await _settle()
        second, third = (
            asyncio.create_task(
                dispatch.BatchingDispatcher(pending, publish).dispatch(_remove(n))
            )
            for n in (2, 3)
        )
        await _settle()
        publish.release.set()

        results = await asyncio.gather(first, second, third, return_exceptions=True)

        assert results[0] is None
        assert isinstance(results[1], RuntimeError)
        assert results[2] is None

    async def test_raises_a_failed_publish_to_every_job_in_the_batch(self):
        pending = dispatch.PendingWrites()

        async def lost(_):
            msg = "lost the swap"
            raise RuntimeError(msg)

        results = await asyncio.gather(
            *(
                dispatch.BatchingDispatcher(pending, lost).dispatch(_remove(n))
                for n in (1, 2)
            ),
            return_exceptions=True,
        )

        assert [str(result) for result in results] == ["lost the swap"] * 2

    async def test_carries_on_after_a_failed_publish(self):
        pending = dispatch.PendingWrites()

        async def lost(_):
            msg = "lost the swap"
            raise RuntimeError(msg)

        with pytest.raises(RuntimeError):
            await dispatch.BatchingDispatcher(pending, lost).dispatch(_remove(1))

        publish = Publisher()
        await dispatch.BatchingDispatcher(pending, publish).dispatch(_remove(2))

        assert publish.batches == [[_remove(2)]]


class TestCancellation:
    async def test_a_cancelled_caller_leaves_the_rest_of_its_batch(self):
        publish = Publisher(fail={_remove(1)})
        pending = dispatch.PendingWrites()
        publish.release.clear()
        first, second = (
            asyncio.create_task(
                dispatch.BatchingDispatcher(pending, publish).dispatch(_remove(n))
            )
            for n in (1, 2)
        )
        await _settle()

        first.cancel()
        await _settle()
        publish.release.set()

        assert await second is None
        with pytest.raises(asyncio.CancelledError):
            await first
        assert publish.batches == [[_remove(1), _remove(2)]]

    async def test_publishes_a_job_whose_caller_was_cancelled(self):
        publish = Publisher()
        pending = dispatch.PendingWrites()
        publish.release.clear()
        held = asyncio.create_task(
            dispatch.BatchingDispatcher(pending, publish).dispatch(_remove(1))
        )
        await _settle()
        cancelled = asyncio.create_task(
            dispatch.BatchingDispatcher(pending, publish).dispatch(_remove(2))
        )
        await _settle()

        cancelled.cancel()
        publish.release.set()
        await held
        await _settle()

        assert publish.batches == [[_remove(1)], [_remove(2)]]
        assert not pending.publishing
