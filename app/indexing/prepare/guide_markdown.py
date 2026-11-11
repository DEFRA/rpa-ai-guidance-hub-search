"""Parses a guide's `content.md` (the API's Markdown dialect) into a `SourceDocument`.

The guide's title is its single `#` line. Each `##` or deeper heading starts a
section, and its level is the number of hashes minus one. Any text before the
first `##` is a level-0 section with no heading and no anchor. Anchors follow
the API's rules (`anchors`); section numbers such as `4.2.1` are split off the
heading. Section ids come from the API's anchor map when it wrote one.
"""

import dataclasses
import logging
import re
from collections.abc import Mapping, Sequence

import chonkie

from app.indexing import jobs
from app.indexing.prepare import anchors, blocks, plain_text, sources
from app.records import ids, models, source_metadata

logger = logging.getLogger(__name__)

_TITLE = re.compile(r"# (?P<text>.+)")
_HEADING = re.compile(r"(?P<hashes>#{2,}) (?P<text>.+)")


class MalformedGuideError(ValueError):
    """`content.md` isn't in the dialect closely enough to index."""


class NotACmsIdError(ValueError):
    """A guide's document and version ids are the CMS's, and always UUIDv7."""


@dataclasses.dataclass(frozen=True)
class _Section:
    """A heading and the body under it; `rendered` is None for the preamble."""

    rendered: str | None
    level: int
    body: str


def url(document_id: ids.DocumentId) -> str:
    """Where a reader views the guide in the hub."""
    return f"/guides/{document_id}"


class GuideMarkdownParser:
    def __init__(self) -> None:
        self._chef = chonkie.MarkdownChef()

    def parse(
        self,
        job: jobs.IndexJob,
        markdown: str,
        section_ids: Mapping[str, ids.SectionId] | None = None,
    ) -> sources.SourceDocument:
        """Sync and CPU-bound. Raises `MalformedGuideError`, or `NotACmsIdError`.

        `section_ids` is the API's anchor-to-section-ID map, when it has one.
        Sections it doesn't name get hashed ids. A guide never mints ids: the
        job must carry the CMS's UUIDv7 document and version ids.
        """
        version_id = _cms_ids(job)
        title, sections = _split(job, markdown)
        header = models.DocumentHeader(
            document_id=job.document_id,
            version_id=version_id,
            status=job.status,
            source_type=models.SourceType.GUIDE,
            title=title,
            url=url(job.document_id),
            content_url=job.content_url,
            occurred_at=job.occurred_at,
            source=source_metadata.GuideMetadata.empty(),
        )
        section_anchors = _anchors(sections)

        return sources.SourceDocument(
            header=header,
            sections=[
                _source_section(
                    job,
                    section,
                    anchor,
                    tuple(blocks.parse(section.body, self._chef)),
                    section_ids,
                )
                for section, anchor in zip(sections, section_anchors, strict=True)
            ],
        )


def _cms_ids(job: jobs.IndexJob) -> ids.VersionId:
    """The job's version id, once both its ids are shown to be the CMS's."""
    if job.version_id is None:
        msg = f"guide {job.document_id} needs the CMS's version id"
        raise NotACmsIdError(msg)

    for kind, value in (("document", job.document_id), ("version", job.version_id)):
        if not ids.is_uuid7(value):
            msg = f"guide {kind} id {value} is not a CMS id (UUIDv7)"
            raise NotACmsIdError(msg)

    return job.version_id


def _split(job: jobs.IndexJob, markdown: str) -> tuple[str, list[_Section]]:
    lines = markdown.splitlines()
    titles = [match for line in lines if (match := _TITLE.fullmatch(line.rstrip()))]

    if len(titles) != 1:
        msg = (
            f"guide {job.document_id} version {job.version_id} has "
            f"{len(titles)} '# ' title lines; expected exactly one"
        )
        raise MalformedGuideError(msg)

    sections: list[_Section] = []
    rendered: str | None = None
    level = 0
    body: list[str] = []

    for line in lines:
        if _TITLE.fullmatch(line.rstrip()):
            continue

        heading = _HEADING.fullmatch(line.rstrip())

        if heading is None:
            body.append(line)
            continue

        sections.append(_Section(rendered, level, "\n".join(body)))
        rendered, level, body = heading["text"].strip(), len(heading["hashes"]) - 1, []

    sections.append(_Section(rendered, level, "\n".join(body)))

    if sections[0].rendered is None and not sections[0].body.strip():
        sections.pop(0)

    return plain_text.strip_inline(titles[0]["text"]), sections


def _anchors(sections: Sequence[_Section]) -> list[str]:
    headed = anchors.of_headings(
        [section.rendered for section in sections if section.rendered is not None],
    )
    anchor_of = iter(headed)
    return [
        next(anchor_of) if section.rendered is not None else "" for section in sections
    ]


def _source_section(
    job: jobs.IndexJob,
    section: _Section,
    anchor: str,
    section_blocks: tuple[models.Block, ...],
    given_ids: Mapping[str, ids.SectionId] | None,
) -> sources.SourceSection:
    if section.rendered is None:
        number, heading = None, None
    else:
        number, text = ids.split_section_number(section.rendered)
        heading = plain_text.strip_inline(text)

    return sources.SourceSection(
        heading=heading,
        section_number=number,
        level=section.level,
        anchor=anchor,
        blocks=section_blocks,
        section_id=_given_id(job, anchor, given_ids),
    )


def _given_id(
    job: jobs.IndexJob,
    anchor: str,
    given_ids: Mapping[str, ids.SectionId] | None,
) -> ids.SectionId | None:
    if given_ids is None or not anchor:
        return None

    if anchor in given_ids:
        return given_ids[anchor]

    logger.warning(
        "Guide %s version %s: no section ID for anchor %r; using its hash",
        job.document_id,
        job.version_id,
        anchor,
    )
    return None
