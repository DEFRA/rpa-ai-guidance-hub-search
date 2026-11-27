"""The process's one `PendingWrites`: the indexing queue's writer turn.

The lifespan hands it to the indexing queue's dispatcher, and the reset route
holds its `turn` while it wipes, so the two can never run at once.
"""

from app.indexing import dispatch

_pending: dispatch.PendingWrites | None = None


def get_pending_writes() -> dispatch.PendingWrites:
    """FastAPI dependency; also called by the lifespan."""
    global _pending

    if _pending is None:
        _pending = dispatch.PendingWrites()

    return _pending
