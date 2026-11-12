"""In-memory `BuildStore` and `ActiveBuild`."""

import pathlib
import uuid

from app.index import builds


class InMemoryBuildStore:
    def __init__(self) -> None:
        self.builds: dict[builds.BuildId, dict[str, bytes]] = {}

    async def upload(self, directory: pathlib.Path) -> builds.BuildId:
        build_id = builds.BuildId(str(uuid.uuid7()))
        self.builds[build_id] = {
            path.name: path.read_bytes()
            for path in directory.iterdir()
            if path.is_file() and not path.name.endswith(".lock")
        }
        return build_id

    async def download(self, build_id: builds.BuildId, directory: pathlib.Path) -> None:
        for name, data in self.builds[build_id].items():
            (directory / name).write_bytes(data)

    async def delete(self, build_id: builds.BuildId) -> None:
        self.builds.pop(build_id, None)


class InMemoryActiveBuild:
    def __init__(self, build_id: builds.BuildId | None = None) -> None:
        self.build_id = build_id

    async def get(self) -> builds.BuildId | None:
        return self.build_id

    async def swap(self, expected: builds.BuildId | None, new: builds.BuildId) -> bool:
        if self.build_id != expected:
            return False

        self.build_id = new
        return True
