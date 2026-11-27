"""Hands write jobs to the index's single writer."""

from typing import Protocol

from app.indexing import jobs


class ApplyDispatcher(Protocol):
    async def dispatch(self, job: jobs.WriteJob) -> None:
        """Hands the job to the index's single writer, or raises."""
        ...
