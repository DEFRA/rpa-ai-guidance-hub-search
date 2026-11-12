"""The two steps of indexing; every entry point calls prepare, then apply.

Prepare never touches the index; apply never reads a source or chunks. Both
are idempotent, and raising leaves the job to be retried.
"""

from typing import Protocol

from app.indexing import jobs


class Preparer(Protocol):
    async def prepare(self, job: jobs.IndexJob) -> jobs.ApplyJob:
        """Stores the version's sections, stages its chunks, says what to apply."""
        ...


class Applier(Protocol):
    async def apply(self, job: jobs.ApplyJob) -> bool:
        """Makes the staged chunks the document's entries in the index.

        False when it left the index unchanged because a newer version of the
        document is already indexed.
        """
        ...

    async def remove(self, job: jobs.RemoveJob) -> bool:
        """Takes the version out of the index if it is the version indexed.

        False when it left the index unchanged.
        """
        ...
