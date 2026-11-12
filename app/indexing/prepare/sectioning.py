"""Turns any source's `SourceDocument` into canonical `SectionRecord`s.

Shared by every source: heading paths, section ids (the source's, else a hash
of the heading path), parents, ordinals and references are worked out the same
way whatever the content came from.
"""

import urllib.parse
from collections.abc import Mapping, Sequence

from app.indexing.prepare import plain_text, sources
from app.records import ids, models

_DEFAULT_PORTS = {"http": 80, "https": 443}


def build(document: sources.SourceDocument) -> list[models.SectionRecord]:
    header = document.header
    sections = document.sections
    paths = _heading_paths(sections)
    hashed = ids.section_ids(header.source_type, header.document_id, paths)
    resolved = [
        section.section_id or fallback
        for section, fallback in zip(sections, hashed, strict=True)
    ]
    parents = _parents(sections, resolved)
    by_anchor = {
        section.anchor: section_id
        for section, section_id in zip(sections, resolved, strict=True)
        if section.anchor
    }

    return [
        models.SectionRecord(
            section_id=resolved[index],
            document_id=header.document_id,
            version_id=header.version_id,
            status=header.status,
            source_type=header.source_type,
            parent_section_id=parents[index],
            heading_path=paths[index],
            section_number=section.section_number,
            anchor=section.anchor,
            level=section.level,
            ordinal=index,
            blocks=section.blocks,
            references=_references(
                "\n\n".join(block.markdown for block in section.blocks),
                by_anchor,
            ),
        )
        for index, section in enumerate(sections)
    ]


def _heading_paths(
    sections: Sequence[sources.SourceSection],
) -> list[tuple[str, ...]]:
    stack: list[tuple[int, str]] = []
    paths: list[tuple[str, ...]] = []

    for section in sections:
        if section.heading is None:
            paths.append(())
            continue

        # By level, not depth, so a heading after a skipped level is a sibling
        # of the one before it, as in `_parents`.
        while stack and stack[-1][0] >= section.level:
            stack.pop()

        stack.append((section.level, section.heading))
        paths.append(tuple(heading for _, heading in stack))

    return paths


def _parents(
    sections: Sequence[sources.SourceSection],
    section_ids: Sequence[ids.SectionId],
) -> list[ids.SectionId | None]:
    stack: list[tuple[int, ids.SectionId]] = []
    parents: list[ids.SectionId | None] = []

    for section, section_id in zip(sections, section_ids, strict=True):
        if section.heading is None:
            parents.append(None)
            continue

        while stack and stack[-1][0] >= section.level:
            stack.pop()

        parents.append(stack[-1][1] if stack else None)
        stack.append((section.level, section_id))

    return parents


def _references(
    text: str,
    by_anchor: Mapping[str, ids.SectionId],
) -> tuple[models.Reference, ...]:
    references: list[models.Reference] = []

    for label, target in plain_text.links(text):
        if target.startswith("#"):
            anchor = target[1:]
            references.append(
                models.Reference(
                    kind=models.ReferenceKind.INTERNAL,
                    text=label,
                    anchor=anchor,
                    section_id=by_anchor.get(anchor),
                ),
            )
        else:
            references.append(
                models.Reference(
                    kind=models.ReferenceKind.EXTERNAL,
                    text=label,
                    url=normalise_url(target),
                ),
            )

    return tuple(references)


def normalise_url(url: str) -> str:
    """Lower-cases the scheme and host and drops a default port; the rest is kept."""
    try:
        parts = urllib.parse.urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return url.strip()

    if not parts.scheme or parts.hostname is None:
        return urllib.parse.urlunsplit(parts)

    scheme = parts.scheme.lower()
    host = parts.hostname

    if ":" in host:
        host = f"[{host}]"
    if port is not None and port != _DEFAULT_PORTS.get(scheme):
        host = f"{host}:{port}"

    userinfo, at, _ = parts.netloc.rpartition("@")
    netloc = f"{userinfo}{at}{host}"

    return urllib.parse.urlunsplit(
        (scheme, netloc, parts.path, parts.query, parts.fragment),
    )
