#!/usr/bin/env python3
"""Index one document version into the shared index from the command line.

For local development only: it helps you exercise indexing against local
dependencies (floci, MongoDB). It is not how documents are indexed in CDP.

    uv run task index-document --source-type guide --document-id G --version-id V \
        --status published --content-url s3://<bucket>/<G>/<V>/content.md

The document is prepared: its sections are stored and its chunks staged in S3.
Then the job is applied: the active index build is downloaded, the staged
chunks are written into it, and the result is uploaded as a new build and made
active. Needs S3 and MongoDB.

Only argument parsing lives here. The two steps are `document_preparer`'s and
`document_applier`'s, the same ones every other entry point will run.
"""

import argparse
import asyncio
import datetime as dt
import logging
import sys
import uuid
from collections.abc import Sequence

from app.common import mongo
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
    asyncio.run(_run(job))


async def _run(job: jobs.IndexJob) -> None:
    try:
        apply_job = await document_preparer.from_config().prepare(job)
        await document_applier.publish_from_config(apply_job)
    finally:
        if mongo.client is not None:
            await mongo.client.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Index one document version.")
    parser.add_argument(
        "--source-type",
        type=models.SourceType,
        choices=list(models.SourceType),
        required=True,
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
