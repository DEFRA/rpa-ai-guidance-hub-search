"""Keeps what a person typed out of logged URLs.

`q` and `title` carry search text, which may hold anything, personal details
included. Other parameters (`tag`, `limit`, `offset`, `group`) stay, as they
help debugging.
"""

import urllib.parse

REDACTED_PARAMETERS = frozenset({"q", "title"})
REDACTED = "[redacted]"


def redact_query(url: str) -> str:
    """`url` with the value of each redacted query parameter replaced.

    Works on a full URL or a path with a query string.
    """
    parts = urllib.parse.urlsplit(url)

    if not parts.query:
        return url

    query = urllib.parse.urlencode(
        [
            (name, REDACTED if name in REDACTED_PARAMETERS else value)
            for name, value in urllib.parse.parse_qsl(
                parts.query, keep_blank_values=True
            )
        ],
        safe="[]",
    )
    return urllib.parse.urlunsplit(parts._replace(query=query))
