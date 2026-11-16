"""In-memory `ContentSource`, keyed by content URL."""

from collections.abc import Mapping


class InMemoryContent:
    def __init__(self, contents: Mapping[str, str] | None = None) -> None:
        self.contents = dict(contents or {})

    async def read(self, content_url: str) -> str:
        return self.contents[content_url]
