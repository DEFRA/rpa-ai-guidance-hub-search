"""The in-process dispatcher publishes each job it is given."""

from app.indexing import dispatch, jobs
from tests.support import chunks as fixtures


class TestDispatch:
    async def test_publishes_the_job(self, mocker):
        publish = mocker.AsyncMock(return_value=None)
        job = jobs.RemoveJob(
            document_id=fixtures.DOCUMENT_ID,
            version_id=fixtures.VERSION_ID,
            occurred_at=fixtures.OCCURRED_AT,
        )

        await dispatch.InProcessDispatcher(publish).dispatch(job)

        publish.assert_awaited_once_with(job)
