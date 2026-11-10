"""Package boundaries: who may import tantivy, and which domains import which."""

import ast
import pathlib

import pytest

APP = pathlib.Path(__file__).parent.parent / "app"
ALLOWED = {APP / "index" / "tantivy_schema.py", APP / "index" / "tantivy_index.py"}

# The write side (indexing) and the read side (search) share `index` and
# `matching` but never import each other; the shared packages import neither.
# `records` (the canonical document and section records) sits under them all.
# Within indexing, preparing (sources, sections, chunks) never touches the
# index, and applying (staged chunks into the index) never prepares.
FORBIDDEN = {
    "indexing": {"app.search"},
    "indexing/prepare": {"app.index", "app.indexing.apply"},
    "indexing/apply": {"app.indexing.prepare"},
    "search": {"app.indexing"},
    "index": {"app.indexing", "app.search"},
    "matching": {"app.indexing", "app.search", "app.index", "app.records"},
    "records": {"app.indexing", "app.search", "app.index", "app.matching"},
}


def _modules(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    names: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)

    return names


def _imports(path: pathlib.Path) -> set[str]:
    return {name.split(".")[0] for name in _modules(path)}


def _imports_any(path: pathlib.Path, packages: set[str]) -> bool:
    return any(
        name == package or name.startswith(f"{package}.")
        for name in _modules(path)
        for package in packages
    )


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
