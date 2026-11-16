"""Only entry points read the config; everything else is handed it.

FastAPI routes and dependencies take `app_config.Config`, which injects it.
Anything else takes the config, or the values it needs, as a parameter.
"""

import ast
import pathlib

ROOT = pathlib.Path(__file__).parent.parent
ENTRY_POINTS = ("app/entrypoints", "scripts")


def _reads(path: pathlib.Path) -> list[int]:
    return [
        node.lineno
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == "get_config")
            or (isinstance(node.func, ast.Attribute) and node.func.attr == "get_config")
        )
    ]


def test_only_entry_points_call_get_config():
    offenders = {
        str(relative): lines
        for path in sorted([*ROOT.glob("app/**/*.py"), *ROOT.glob("scripts/**/*.py")])
        if not (relative := path.relative_to(ROOT)).as_posix().startswith(ENTRY_POINTS)
        and (lines := _reads(path))
    }

    assert offenders == {}


def test_finds_a_call(tmp_path):
    module = tmp_path / "module.py"
    module.write_text("from app import config\n\nconfig.get_config()\n")

    assert _reads(module) == [3]
