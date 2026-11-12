#!/usr/bin/env python3
"""Send one document version to a running service, as the CMS would.

For local development only: it exercises the HTTP ingestion routes end to end.
The service must run in `http` ingestion mode (the default).

    uv run task send-document --document-id G --version-id V --title "Title" \
        --content-url s3://<bucket>/<G>/<V>/content.md

    uv run task send-document --document-id G --version-id V --title "Title" \
        --content-file guide.md [--section-ids sections.json] [--version-number 1.2]

    uv run task send-document --document-id G --version-id V --delete

Every version is sent as `published`, the only status the service indexes, and
`--title` is sent as the CMS's `guideTitle`. The service replies `202` and does
the work afterwards; check its log for the outcome. Only argument parsing and
the request live here.
"""

import argparse
import datetime as dt
import json
import pathlib
import sys
import uuid
from collections.abc import Sequence
from typing import Any

import httpx2

from app.records import models

DEFAULT_URL = "http://localhost:8085"
TIMEOUT_SECONDS = 30


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    _check(parser, args)
    path = f"{args.url}/documents/{args.document_id}/versions/{args.version_id}"

    with httpx2.Client(timeout=TIMEOUT_SECONDS) as client:
        response = (
            client.delete(path) if args.delete else client.put(path, json=_body(args))
        )

    sys.stdout.write(f"{response.status_code} {response.text}\n")
    return 0 if response.is_success else 1


def _body(args: argparse.Namespace) -> dict[str, Any]:
    body: dict[str, Any] = {
        "sourceType": args.source_type,
        "status": models.DocumentStatus.PUBLISHED.value,
        "occurredAt": dt.datetime.now(dt.UTC).isoformat(),
        "versionNumber": args.version_number,
        "metadata": {"guideTitle": args.title},
    }

    if args.content_url:
        body["contentUrl"] = args.content_url
    else:
        body["content"] = {
            "markdown": args.content_file.read_text(),
            "sectionIds": (
                json.loads(args.section_ids.read_text()) if args.section_ids else None
            ),
        }

    return body


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Send one document version.")
    parser.add_argument("--url", default=DEFAULT_URL, help="the service's base URL")
    parser.add_argument("--document-id", type=uuid.UUID, required=True)
    parser.add_argument("--version-id", type=uuid.UUID, required=True)
    parser.add_argument(
        "--source-type",
        choices=[source.value for source in models.SourceType],
        default=models.SourceType.GUIDE.value,
    )
    parser.add_argument("--title", help="the guide's title, as the CMS knows it")
    parser.add_argument("--version-number")
    parser.add_argument("--section-ids", type=pathlib.Path)

    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--content-url")
    action.add_argument("--content-file", type=pathlib.Path)
    action.add_argument("--delete", action="store_true")

    return parser


def _check(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if not args.delete and not args.title:
        parser.error("--title is needed unless deleting")

    if args.section_ids and not args.content_file:
        parser.error("--section-ids goes with --content-file")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
