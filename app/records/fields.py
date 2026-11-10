"""Checks for the shape of stored fields, applied where records are read.

Stored files outlive the code that wrote them, so a field of the wrong type must
fail loudly rather than change meaning (a string read as a tuple of characters)
or surface as an unrelated `AttributeError`. Each check raises `TypeError`,
which the file readers translate into their malformed-file errors.
"""

from collections.abc import Mapping
from typing import Any


def text(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise _wrong(name, "a string", value)

    return value


def optional_text(value: Any, name: str) -> str | None:
    return None if value is None else text(value, name)


def integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _wrong(name, "an integer", value)

    return value


def optional_integer(value: Any, name: str) -> int | None:
    return None if value is None else integer(value, name)


def texts(value: Any, name: str) -> tuple[str, ...]:
    """A JSON array of strings."""
    if not isinstance(value, list):
        raise _wrong(name, "an array", value)

    return tuple(text(item, f"{name} item") for item in value)


def mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise _wrong(name, "an object", value)

    return value


def mappings(value: Any, name: str) -> tuple[Mapping[str, Any], ...]:
    """A JSON array of objects."""
    if not isinstance(value, list):
        raise _wrong(name, "an array", value)

    return tuple(mapping(item, f"{name} item") for item in value)


def _wrong(name: str, expected: str, value: Any) -> TypeError:
    return TypeError(f"{name} must be {expected}, got {type(value).__name__}")
