"""In-memory `ContentSource` and `SectionIdSource`, keyed by content URL."""

from collections.abc import Mapping

from app.records import ids


class InMemoryContent:
    def __init__(
        self,
        contents: Mapping[str, str] | None = None,
        section_ids: Mapping[str, Mapping[str, ids.SectionId]] | None = None,
    ) -> None:
        self.contents = dict(contents or {})
        self.section_ids = dict(section_ids or {})

    async def read(self, content_url: str) -> str:
        return self.contents[content_url]

    async def read_section_ids(
        self,
        content_url: str,
    ) -> Mapping[str, ids.SectionId] | None:
        return self.section_ids.get(content_url)
