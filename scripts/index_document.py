#!/usr/bin/env python3
"""Index one document version from the command line.

    uv run task index-document --source-type guide --index-dir DIR --document-id G --version-id V \
        --status published --content-url s3://<bucket>/<G>/<V>/content.md

The guide is prepared (sections stored, chunks staged in S3), then applied to
the local index: the same two steps, through the same `from_config` wiring,
that every other entry point will run. Only argument parsing lives here.
"""

import argparse
import asyncio
import datetime as dt
import logging
import pathlib
import sys
import uuid
from collections.abc import Sequence

from app.indexing import jobs
from app.indexing.apply import document_applier
from app.indexing.prepare import document_preparer
from app.records import ids, models


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    job = jobs.IndexJob(
        source_type=args.source_type,
        document_id=ids.DocumentId(args.document_id),
        version_id=ids.VersionId(args.version_id),
        status=args.status,
        content_url=args.content_url,
        occurred_at=dt.datetime.now(dt.UTC),
    )

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    asyncio.run(_run(job, args.index_dir))


async def _run(job: jobs.IndexJob, index_dir: pathlib.Path) -> None:
    apply_job = await document_preparer.from_config().prepare(job)
    await document_applier.from_config(index_dir).apply(apply_job)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Index one document version.")
    parser.add_argument(
        "--source-type",
        type=models.SourceType,
        choices=list(models.SourceType),
        required=True,
    )
    parser.add_argument(
        "--index-dir",
        type=pathlib.Path,
        required=True,
        help="local directory holding the Tantivy index",
    )
    parser.add_argument("--document-id", type=uuid.UUID, required=True)
    parser.add_argument("--version-id", type=uuid.UUID, required=True)
    parser.add_argument(
        "--status",
        type=models.DocumentStatus,
        choices=list(models.DocumentStatus),
        required=True,
    )
    parser.add_argument("--content-url", required=True)
    return parser


if __name__ == "__main__":
    main(sys.argv[1:])
