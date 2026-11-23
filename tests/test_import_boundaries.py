"""Package boundaries: who may import tantivy, and which domains import which."""

import ast
import pathlib

import pytest

APP = pathlib.Path(__file__).parent.parent / "app"
ALLOWED = {APP / "index" / "tantivy_schema.py", APP / "index" / "tantivy_index.py"}
SQS_CONSUMER = APP / "common" / "sqs_consumer"

# The write side (indexing) and the read side (search) share `index` and
# `matching` but never import each other; the shared packages import neither.
# `records` (the canonical document and section records) sits under them all.
# Within indexing, preparing (sources, sections, chunks) never touches the
# index, and applying (staged chunks into the index) never prepares.
FORBIDDEN = {
    "indexing": {"app.search", "app.ingestion"},
    "ingestion": {"app.search"},
    "common": {
        "app.indexing",
        "app.ingestion",
        "app.search",
        "app.index",
        "app.records",
        "app.matching",
    },
    "indexing/prepare": {"app.index", "app.indexing.apply"},
    "indexing/apply": {"app.indexing.prepare"},
    "search": {"app.indexing"},
    "index": {"app.indexing", "app.search"},
    "matching": {"app.indexing", "app.search", "app.index", "app.records"},
    "records": {"app.indexing", "app.search", "app.index", "app.matching"},
}


def _modules_in(source: str) -> set[str]:
    """Every module `source` imports, and, for a `from` import, each name it
    takes too, as it may be a module: `from app import search` is `app.search`.
    """
    names: set[str] = set()

    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)

    return names


def _modules(path: pathlib.Path) -> set[str]:
    return _modules_in(path.read_text())


def _imports(path: pathlib.Path) -> set[str]:
    return {name.split(".")[0] for name in _modules(path)}


def _names_any(names: set[str], packages: set[str]) -> bool:
    return any(
        name == package or name.startswith(f"{package}.")
        for name in names
        for package in packages
    )


def _imports_any(path: pathlib.Path, packages: set[str]) -> bool:
    return _names_any(_modules(path), packages)


class TestTheTantivyImports:
    def test_are_only_in_the_tantivy_modules(self):
        offenders = [
            str(path.relative_to(APP))
            for path in APP.rglob("*.py")
            if path not in ALLOWED and "tantivy" in _imports(path)
        ]

        assert offenders == []

    def test_check_finds_the_schema_module(self):
        assert "tantivy" in _imports(APP / "index" / "tantivy_schema.py")


class TestTheQueueConsumerImports:
    """Queues are entry points: only `app/entrypoints/` knows SQS messages."""

    def test_are_only_in_the_entry_points(self):
        offenders = [
            str(path.relative_to(APP))
            for path in APP.rglob("*.py")
            if not path.is_relative_to(SQS_CONSUMER)
            and "entrypoints" not in path.relative_to(APP).parts
            and _imports_any(path, {"app.common.sqs_consumer"})
        ]
        assert offenders == []

    def test_check_finds_the_consumers_module(self):
        assert _imports_any(
            APP / "entrypoints" / "queue_consumers.py", {"app.common.sqs_consumer"}
        )


class TestTheDomainImports:
    @pytest.mark.parametrize(("package", "forbidden"), FORBIDDEN.items())
    def test_keep_to_their_side(self, package, forbidden):
        offenders = [
            str(path.relative_to(APP))
            for path in (APP / package).rglob("*.py")
            if _imports_any(path, forbidden)
        ]

        assert offenders == []

    def test_check_finds_a_domain_import(self):
        assert _imports_any(APP / "search" / "service.py", {"app.index"})

    @pytest.mark.parametrize(
        ("source", "package"),
        [
            pytest.param("import app.search.service", "app.search", id="import"),
            pytest.param(
                "from app.search import service", "app.search", id="from-module"
            ),
            pytest.param("from app import search", "app.search", id="from-package"),
            pytest.param(
                "from app.indexing import apply",
                "app.indexing.apply",
                id="from-subpackage",
            ),
            pytest.param(
                "from app.indexing import apply as applying",
                "app.indexing.apply",
                id="aliased",
            ),
        ],
    )
    def test_check_finds_every_form_of_import(self, source, package):
        assert _names_any(_modules_in(source), {package})

    @pytest.mark.parametrize(
        ("source", "package"),
        [
            pytest.param("from app import index", "app.indexing", id="prefix"),
            pytest.param("from app.indexing import prepare", "app.indexing.apply"),
        ],
    )
    def test_check_passes_other_imports(self, source, package):
        assert not _names_any(_modules_in(source), {package})
